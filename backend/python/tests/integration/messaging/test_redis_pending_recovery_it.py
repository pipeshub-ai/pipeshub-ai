"""Pending entries come back, and every lane gets its turn, on a real
Redis Streams broker.

Requires:
  docker compose -f deployment/docker-compose/docker-compose.integration.messaging.yml up -d
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import TYPE_CHECKING

import pytest
from redis.asyncio import Redis

from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
    RedisStreamsConfig,
)
from app.services.messaging.lanes.hash_router import RedisLaneRouter, stable_lane
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

_LANES = 4


def _connector_on_lane(lane: int, prefix: str) -> str:
    """A connector id the hash router puts on ``lane``."""
    for n in range(10_000):
        candidate = f"{prefix}-{n}"
        if stable_lane(candidate, _LANES) == lane:
            return candidate
    raise AssertionError(f"no connector id found for lane {lane}")


def _config(host, port, base, group, *, claim_min_idle_ms) -> RedisStreamsConfig:
    return RedisStreamsConfig(
        host=host,
        port=port,
        client_id=f"{group}-client",
        group_id=group,
        topics=RedisLaneRouter(_LANES).lane_topics(base),
        batch_size=10,
        block_ms=500,
        claim_min_idle_ms=claim_min_idle_ms,
    )


def _fair(**overrides) -> FairSchedulerConfig:
    base = {
        "enabled": True,
        "key_fields": ("orgId", "connectorId"),
        "max_buffered_messages": 20,
        "max_per_entity_messages": 10,
    }
    base.update(overrides)
    return FairSchedulerConfig(**base)


async def _add(client, base: str, connector_id: str, record_id: str) -> None:
    lane = stable_lane(connector_id, _LANES)
    payload = {
        "recordId": record_id,
        "orgId": "org-1",
        "connectorId": connector_id,
        "extension": "txt",
        "mimeType": "text/plain",
    }
    await client.xadd(
        f"{base}.{lane}",
        {"value": json.dumps({"eventType": "newRecord", "payload": payload})},
    )


def _handler(done: list[tuple[str, str]], seconds: float = 0.0) -> Callable:
    async def handle(parsed_message: StreamMessage) -> AsyncIterator[PipelineEvent]:
        yield PipelineEvent(
            event=IndexingEvent.START_PARSING,
            data=PipelineEventData(tier=ParseTier.LIGHT),
        )
        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE)
        if seconds:
            await asyncio.sleep(seconds)
        done.append(
            (parsed_message.payload["connectorId"], parsed_message.payload["recordId"])
        )
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE)

    return handle


@pytest.fixture
async def redis_client(redis_available: tuple[str, int]) -> AsyncIterator[Redis]:
    host, port = redis_available
    client = Redis(host=host, port=port, decode_responses=True)
    yield client
    await client.aclose()


@pytest.fixture
async def base_stream(redis_client: Redis, unique_suffix: str) -> AsyncIterator[str]:
    name = f"record-events-recovery-{unique_suffix}"
    yield name
    for stream in RedisLaneRouter(_LANES).lane_topics(name):
        await redis_client.delete(stream)


async def _until(predicate, what: str, timeout: float = DRAIN_TIMEOUT_SECONDS) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.1)


class TestRestartBehindOneBigLane:
    async def test_every_connector_is_dispatched_soon_after_a_restart(
        self, redis_available, redis_client, base_stream, unique_suffix
    ) -> None:
        """A previous process read everything and died: 200 entries of one
        connector pending on lane 0, five of each of three others on lanes
        1-3. The new process serves all four from the start, not lane 0
        until it is empty."""
        host, port = redis_available
        group = f"it-restart-{unique_suffix}"
        big = _connector_on_lane(0, "big")
        smalls = [_connector_on_lane(lane, f"small{lane}") for lane in (1, 2, 3)]
        for i in range(200):
            await _add(redis_client, base_stream, big, f"big-{i}")
        for connector in smalls:
            for i in range(5):
                await _add(redis_client, base_stream, connector, f"{connector}-{i}")

        topics = RedisLaneRouter(_LANES).lane_topics(base_stream)
        for topic in topics:
            await redis_client.xgroup_create(topic, group, id="0", mkstream=True)
            # The dead process: read without acknowledging.
            await redis_client.xreadgroup(group, "dead-process", {topic: ">"}, count=1000)
        await asyncio.sleep(0.6)  # past claim_min_idle_ms

        done: list[tuple[str, str]] = []
        consumer = IndexingRedisStreamsConsumer(
            logging.getLogger("it-recovery"),
            _config(host, port, base_stream, group, claim_min_idle_ms=500),
            fair_scheduler_config=_fair(),
        )
        await consumer.start(_handler(done, seconds=0.02))
        try:
            await _until(
                lambda: {c for c, _ in done} >= set(smalls),
                "every small connector to be indexed",
            )
        finally:
            await consumer.stop()

        last_small = max(i for i, (c, _) in enumerate(done) if c in smalls)
        big_before_last_small = sum(1 for c, _ in done[:last_small] if c == big)
        # Served in turn: the small connectors finish while most of the big
        # backlog is still waiting. Lane by lane, all 200 went first.
        assert big_before_last_small < 100


class TestAgedEntriesWhileAnotherLaneIsBusy:
    async def test_a_connector_waiting_past_the_dwell_budget_still_finishes(
        self, redis_available, redis_client, base_stream, unique_suffix, monkeypatch
    ) -> None:
        """Connector A's entries wait in the buffer for longer than the
        dwell budget while connector B's lane produces faster than the
        pipeline indexes. All of A's entries are indexed."""
        monkeypatch.setenv("MAX_CONCURRENT_INDEXING", "4")
        host, port = redis_available
        group = f"it-dwell-{unique_suffix}"
        quiet = _connector_on_lane(1, "quiet")
        busy = _connector_on_lane(0, "busy")
        for i in range(30):
            await _add(redis_client, base_stream, quiet, f"quiet-{i}")

        stop = asyncio.Event()

        async def keep_busy() -> None:
            n = 0
            while not stop.is_set():
                await _add(redis_client, base_stream, busy, f"busy-{n}")
                n += 1
                await asyncio.sleep(0.01)

        done: list[tuple[str, str]] = []
        consumer = IndexingRedisStreamsConsumer(
            logging.getLogger("it-recovery"),
            _config(host, port, base_stream, group, claim_min_idle_ms=30_000),
            fair_scheduler_config=_fair(max_dwell_seconds=1.0),
        )
        producer = asyncio.create_task(keep_busy())
        await consumer.start(_handler(done, seconds=0.3))
        try:
            await _until(
                lambda: sum(1 for c, _ in done if c == quiet) >= 30,
                "the quiet connector's 30 entries",
                timeout=45.0,
            )
        finally:
            stop.set()
            with contextlib.suppress(Exception):
                await producer
            await consumer.stop()


class TestTwoLiveReplicas:
    async def test_live_replicas_do_not_take_each_others_entries(
        self, redis_available, redis_client, base_stream, unique_suffix
    ) -> None:
        """Two replicas share the group. Each one's held entries stay its
        own while it refreshes them, so read-phase recovery in the other
        takes none of them and nothing is indexed twice."""
        host, port = redis_available
        group = f"it-replicas-{unique_suffix}"
        connectors = [_connector_on_lane(lane, f"c{lane}") for lane in range(_LANES)]
        expected = set()
        for connector in connectors:
            for i in range(15):
                await _add(redis_client, base_stream, connector, f"{connector}-{i}")
                expected.add(f"{connector}-{i}")

        done: list[tuple[str, str]] = []
        replicas = [
            IndexingRedisStreamsConsumer(
                logging.getLogger(f"it-replica-{n}"),
                _config(host, port, base_stream, group, claim_min_idle_ms=3_000),
                fair_scheduler_config=_fair(max_buffered_messages=8, max_per_entity_messages=4),
            )
            for n in range(2)
        ]
        for replica in replicas:
            await replica.start(_handler(done, seconds=0.05))
        try:
            await _until(lambda: {r for _, r in done} >= expected, "every record")
        finally:
            for replica in replicas:
                await replica.stop()

        records = [r for _, r in done]
        assert len(records) == len(set(records)), "a record was indexed twice"
