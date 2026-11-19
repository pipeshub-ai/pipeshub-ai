"""Events for a record already being indexed wait for it, on a real Redis.

The customer case: during one long reindex the same 8 pending entries, other
events for the record being reindexed, were offered again about once a
second for two hours. Here one long run holds record R while more events
for R and for other records of the same connector arrive.

Requires:
  docker compose -f deployment/docker-compose/docker-compose.integration.messaging.yml up -d
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import TYPE_CHECKING

import pytest
from redis.asyncio import Redis

from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
    RedisStreamsConfig,
)
from app.services.messaging.redis_streams.indexing_consumer import (
    IndexingRedisStreamsConsumer,
)
from app.services.messaging.scheduling.interface import FairSchedulerConfig
from app.services.resource_governor.models import ParseTier
from tests.integration.messaging.conftest import DRAIN_TIMEOUT_SECONDS

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from app.services.messaging.config import StreamMessage

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

LOGGER_NAME = "it-record-wait"
# Past the old loop's once-a-second re-offers several times over, and past
# an ownership refresh (claim_min_idle_ms / 3).
HOLD_SECONDS = 3.0


@pytest.fixture
async def redis_client(redis_available: tuple[str, int]) -> AsyncIterator[Redis]:
    host, port = redis_available
    client = Redis(host=host, port=port, decode_responses=True)
    yield client
    await client.aclose()


@pytest.fixture
async def stream(redis_client: Redis, unique_suffix: str) -> AsyncIterator[str]:
    name = f"record-events-wait-{unique_suffix}"
    yield name
    await redis_client.delete(name)


async def _add(client: Redis, stream: str, record_id: str, seq: int) -> str:
    payload = {
        "recordId": record_id,
        "orgId": "org-1",
        "connectorId": "conn-a",
        "extension": "txt",
        "mimeType": "text/plain",
        "seq": seq,
    }
    return await client.xadd(
        stream, {"value": json.dumps({"eventType": "newRecord", "payload": payload})}
    )


async def _until(predicate: Callable[[], bool], what: str, timeout: float = DRAIN_TIMEOUT_SECONDS) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.05)


class TestALongRunHoldsItsRecord:
    async def test_its_other_events_wait_once_and_run_after_it_in_order(
        self,
        redis_available: tuple[str, int],
        redis_client: Redis,
        stream: str,
        unique_suffix: str,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.INFO, logger=LOGGER_NAME)
        host, port = redis_available
        group = f"it-record-wait-{unique_suffix}"
        done: list[tuple[str, int]] = []
        long_run_started = asyncio.Event()
        long_run_ended = asyncio.Event()
        # The test, not a timer, ends the long run: it ends only after the
        # checks made while it holds the record.
        release_long_run = threading.Event()
        main_loop = asyncio.get_running_loop()

        async def handle(message: StreamMessage) -> AsyncIterator[PipelineEvent]:
            yield PipelineEvent(event=IndexingEvent.START_PARSING, data=PipelineEventData(tier=ParseTier.LIGHT))
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE)
            record_id, seq = message.payload["recordId"], int(message.payload["seq"])
            if seq == 0:
                main_loop.call_soon_threadsafe(long_run_started.set)
                # The handler runs on the consumer's worker loop, not this
                # test's, so it waits on a thread event, off that loop. No
                # timeout: one could end the run before the checks it must
                # outlast; the test's finally releases it on any failure.
                await asyncio.to_thread(release_long_run.wait)
                main_loop.call_soon_threadsafe(long_run_ended.set)
            done.append((record_id, seq))
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE)

        consumer = IndexingRedisStreamsConsumer(
            logging.getLogger(LOGGER_NAME),
            RedisStreamsConfig(
                host=host, port=port, client_id=f"{group}-client", group_id=group,
                topics=[stream], batch_size=10, block_ms=200, claim_min_idle_ms=3_000,
            ),
            fair_scheduler_config=FairSchedulerConfig(
                enabled=True, key_fields=("orgId", "connectorId"),
                max_buffered_messages=20, max_per_entity_messages=20,
            ),
        )
        await _add(redis_client, stream, "R", 0)
        await consumer.start(handle)
        try:
            await asyncio.wait_for(long_run_started.wait(), timeout=DRAIN_TIMEOUT_SECONDS)
            waiting = [await _add(redis_client, stream, "R", seq) for seq in (1, 2, 3, 4)]
            for seq in range(5, 10):
                await _add(redis_client, stream, f"other-{seq}", seq)

            # The connector's other records do not wait for R.
            await _until(lambda: sum(1 for r, _ in done if r != "R") == 5, "the other records")
            assert not long_run_ended.is_set()
            await asyncio.sleep(HOLD_SECONDS)
            pending = await redis_client.xpending_range(stream, group, min="-", max="+", count=100)
            assert sorted(p["message_id"] for p in pending if p["message_id"] in waiting) == sorted(waiting)
            # Refreshed, never claimed again: one delivery each, so waiting
            # never moves them toward the dead-letter backstop.
            assert all(p["times_delivered"] == 1 for p in pending if p["message_id"] in waiting)

            release_long_run.set()
            await _until(lambda: sum(1 for r, _ in done if r == "R") == 5, "R's queued events")
        finally:
            release_long_run.set()
            await consumer.stop()

        assert [seq for r, seq in done if r == "R"] == [0, 1, 2, 3, 4]
        recovering = [
            r.getMessage() for r in caplog.records
            if r.getMessage().startswith("Recovering own pending message")
        ]
        # One re-offer per waiter, when its turn came. Before: about one per
        # waiter per second for the whole run.
        assert len(recovering) == len(waiting)
        assert await redis_client.xpending(stream, group) == {
            "pending": 0, "min": None, "max": None, "consumers": []
        }


class TestATrimmedWaiterIsNotKept:
    async def test_a_waiting_entry_whose_body_is_gone_is_not_confirmed_by_the_refresh(
        self, redis_available: tuple[str, int], redis_client: Redis, stream: str, unique_suffix: str
    ) -> None:
        """On Redis before 7, XCLAIM JUSTID still returns an id whose entry
        was trimmed or deleted (Redis 7 drops it from the pending list): the
        refresh must not keep such a wait."""
        host, port = redis_available
        group = f"it-trimmed-{unique_suffix}"
        consumer = IndexingRedisStreamsConsumer(
            logging.getLogger(LOGGER_NAME),
            RedisStreamsConfig(
                host=host, port=port, client_id=f"{group}-client", group_id=group,
                topics=[stream], batch_size=10, block_ms=200, claim_min_idle_ms=3_000,
            ),
        )
        consumer.redis = Redis(host=host, port=port, decode_responses=True)
        try:
            await redis_client.xgroup_create(stream, group, id="0", mkstream=True)
            gone = await _add(redis_client, stream, "R", 1)
            kept = await _add(redis_client, stream, "R", 2)
            await redis_client.xreadgroup(group, consumer.consumer_name, {stream: ">"}, count=10)
            assert consumer._claim_record("R")
            assert not consumer._claim_record_or_wait("R", stream, gone)
            assert not consumer._claim_record_or_wait("R", stream, kept)
            await redis_client.xdel(stream, gone)

            consumer._last_ownership_refresh = 0.0
            await consumer._IndexingRedisStreamsConsumer__refresh_held_ownership()

            assert list(consumer._record_waits) == [(stream, kept)]
        finally:
            await consumer.redis.aclose()
