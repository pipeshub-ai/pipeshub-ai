"""Fair scheduling and lanes against a real Redis Streams broker.

Exercises the seams a fake cannot: real consumer groups, a real pending
entries list, real XACK, and real per-lane stream routing.

Requires:
  docker compose -f deployment/docker-compose/docker-compose.integration.messaging.yml up -d
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
    RedisStreamsConfig,
)
from app.services.messaging.lanes.hash_router import RedisLaneRouter
from app.services.messaging.lanes.interface import LaneConfig
from app.services.messaging.lanes.producer import LaneAwareProducer
from app.services.messaging.redis_streams.indexing_consumer import (
    IndexingRedisStreamsConsumer,
)
from app.services.messaging.redis_streams.producer import RedisStreamsProducer
from app.services.messaging.scheduling.interface import FairSchedulerConfig
from app.services.resource_governor.models import ParseTier
from tests.integration.messaging.conftest import DRAIN_TIMEOUT_SECONDS

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_LANES = 4
_BIG = 120
_SMALL = 8


def _stream_config(host, port, base, lanes, group):
    return RedisStreamsConfig(
        host=host,
        port=port,
        client_id=f"{group}-client",
        group_id=group,
        topics=RedisLaneRouter(lanes).lane_topics(base),
        batch_size=10,
        block_ms=2000,
        # The recovery test's second consumer has a new name, so it can only
        # reach the first one's pending entries through XAUTOCLAIM. The
        # 30s production default would eat most of the drain budget before
        # recovery could even begin.
        claim_min_idle_ms=500,
    )


def _fair(**overrides) -> FairSchedulerConfig:
    base = dict(
        enabled=True,
        key_fields=("orgId", "connectorId"),
        default_quantum=1,
        max_buffered_messages=200,
        max_per_entity_messages=50,
        max_dwell_seconds=900.0,
    )
    base.update(overrides)
    return FairSchedulerConfig(**base)


async def _publish(host, port, base, records):
    inner = RedisStreamsProducer(
        logging.getLogger("it-producer"),
        RedisStreamsConfig(host=host, port=port, client_id="it-producer"),
    )
    producer = LaneAwareProducer(
        logging.getLogger("it-producer"),
        inner,
        RedisLaneRouter(_LANES),
        LaneConfig(lane_count=_LANES, laned_topics=(base,)),
    )
    await producer.initialize()
    try:
        for connector_id, record_id in records:
            await producer.send_event(
                topic=base,
                event_type="newRecord",
                payload={
                    "recordId": record_id,
                    "orgId": "org-1",
                    "connectorId": connector_id,
                    "extension": "txt",
                    "mimeType": "text/plain",
                },
            )
    finally:
        await producer.cleanup()


def _handler(completions: list[str], record_ids: list[str] | None = None):
    async def handle(parsed_message):
        yield PipelineEvent(
            event=IndexingEvent.START_PARSING,
            data=PipelineEventData(tier=ParseTier.LIGHT),
        )
        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE)
        completions.append(parsed_message.payload["connectorId"])
        if record_ids is not None:
            record_ids.append(parsed_message.payload["recordId"])
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE)

    return handle


async def _drain(completions: list, expected: int) -> None:
    deadline = asyncio.get_running_loop().time() + DRAIN_TIMEOUT_SECONDS
    while len(completions) < expected:
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(
                f"drained {len(completions)} of {expected} before timeout"
            )
        await asyncio.sleep(0.2)


async def _pending_total(host, port, group, streams) -> int:
    from redis.asyncio import Redis

    client = Redis(host=host, port=port, decode_responses=True)
    try:
        total = 0
        for stream in streams:
            try:
                info = await client.xpending(stream, group)
            except Exception:
                continue
            total += (info or {}).get("pending", 0) if isinstance(info, dict) else 0
        return total
    finally:
        await client.aclose()


@pytest.fixture
async def base_stream(redis_available, unique_suffix):
    from redis.asyncio import Redis

    host, port = redis_available
    name = f"record-events-{unique_suffix}"
    yield name
    client = Redis(host=host, port=port, decode_responses=True)
    try:
        for stream in RedisLaneRouter(_LANES).lane_topics(name):
            await client.delete(stream)
    finally:
        await client.aclose()


class TestFairnessOnARealBroker:
    async def test_small_user_is_not_starved_by_a_segregated_backlog(
        self, redis_available, base_stream, unique_suffix
    ):
        host, port = redis_available
        records = [("user-a", f"big-{i}") for i in range(_BIG)]
        records += [("user-b", f"small-{i}") for i in range(_SMALL)]
        await _publish(host, port, base_stream, records)

        group = f"it-fair-{unique_suffix}"
        consumer = IndexingRedisStreamsConsumer(
            logging.getLogger("it-consumer"),
            _stream_config(host, port, base_stream, _LANES, group),
            fair_scheduler_config=_fair(),
        )
        completions: list[str] = []
        await consumer.start(_handler(completions))
        try:
            await _drain(completions, _BIG + _SMALL)
        finally:
            await consumer.stop()

        assert completions.count("user-b") == _SMALL
        assert completions.count("user-a") == _BIG
        last_small = max(
            i for i, conn in enumerate(completions) if conn == "user-b"
        )
        assert last_small < (_BIG + _SMALL) // 2, (
            f"small user finished at {last_small} of {len(completions)}"
        )

    async def test_one_connector_lands_on_one_lane_stream(
        self, redis_available, base_stream
    ):
        from redis.asyncio import Redis

        host, port = redis_available
        await _publish(
            host, port, base_stream, [("user-a", f"r-{i}") for i in range(25)]
        )

        client = Redis(host=host, port=port, decode_responses=True)
        try:
            lengths = {}
            for stream in RedisLaneRouter(_LANES).lane_topics(base_stream):
                length = await client.xlen(stream)
                if length:
                    lengths[stream] = length
        finally:
            await client.aclose()

        assert lengths, "nothing was published"
        assert len(lengths) == 1, f"one connector should use one lane: {lengths}"
        assert sum(lengths.values()) == 25


class TestPendingListOnARealBroker:
    async def test_everything_is_acked_so_the_pending_list_empties(
        self, redis_available, base_stream, unique_suffix
    ):
        """An entry left in the PEL is work the consumer thinks is still in
        flight; after a clean drain there must be none."""
        host, port = redis_available
        total = 40
        await _publish(
            host,
            port,
            base_stream,
            [(f"user-{i % 3}", f"r-{i}") for i in range(total)],
        )

        group = f"it-pel-{unique_suffix}"
        config = _stream_config(host, port, base_stream, _LANES, group)
        consumer = IndexingRedisStreamsConsumer(
            logging.getLogger("it-consumer"), config, fair_scheduler_config=_fair()
        )
        completions: list[str] = []
        await consumer.start(_handler(completions))
        try:
            await _drain(completions, total)
            await asyncio.sleep(1.0)
        finally:
            await consumer.stop()

        assert len(completions) == total
        pending = await _pending_total(host, port, group, config.topics)
        assert pending == 0, f"{pending} entries left un-ACKed in the PEL"

    async def test_a_restart_reprocesses_nothing_already_acked(
        self, redis_available, base_stream, unique_suffix
    ):
        host, port = redis_available
        total = 30
        await _publish(
            host,
            port,
            base_stream,
            [(f"user-{i % 2}", f"r-{i}") for i in range(total)],
        )
        group = f"it-restart-{unique_suffix}"
        config = _stream_config(host, port, base_stream, _LANES, group)

        first: list[str] = []
        consumer = IndexingRedisStreamsConsumer(
            logging.getLogger("it-consumer"), config, fair_scheduler_config=_fair()
        )
        await consumer.start(_handler(first))
        try:
            await _drain(first, total)
            await asyncio.sleep(1.0)
        finally:
            await consumer.stop()
        assert len(first) == total

        second: list[str] = []
        consumer = IndexingRedisStreamsConsumer(
            logging.getLogger("it-consumer"),
            _stream_config(host, port, base_stream, _LANES, group),
            fair_scheduler_config=_fair(),
        )
        await consumer.start(_handler(second))
        try:
            await asyncio.sleep(8.0)
        finally:
            await consumer.stop()

        assert second == [], f"restart reprocessed {len(second)} acked entries"


class TestLaneAdoptionOnARealBroker:
    async def test_a_lane_outside_the_configured_range_still_drains(
        self, redis_available, base_stream, unique_suffix
    ):
        """Lowering the lane count must not orphan the lanes that drop out.
        Publish across 4 lanes, then consume configured for 2."""
        host, port = redis_available
        await _publish(
            host,
            port,
            base_stream,
            [(f"user-{i}", f"r-{i}") for i in range(24)],
        )

        group = f"it-adopt-{unique_suffix}"
        narrowed = _stream_config(host, port, base_stream, 2, group)
        consumer = IndexingRedisStreamsConsumer(
            logging.getLogger("it-consumer"), narrowed, fair_scheduler_config=_fair()
        )
        completions: list[str] = []
        await consumer.start(_handler(completions))
        try:
            await _drain(completions, 24)
        finally:
            await consumer.stop()

        assert len(completions) == 24, (
            "entries on lanes outside the configured range were stranded"
        )


class TestCrashRecoveryOnARealBroker:
    async def test_a_mid_flight_restart_loses_nothing(
        self, redis_available, base_stream, unique_suffix
    ):
        """Stop mid-drain and come back. Entries still un-ACKed sit in the
        pending list; the recovery path has to pick them up, or they are
        lost. Duplicates are fine -- at-least-once is the contract."""
        host, port = redis_available
        total = 80
        expected = {f"r-{i}" for i in range(total)}
        await _publish(
            host,
            port,
            base_stream,
            [(f"user-{i % 4}", f"r-{i}") for i in range(total)],
        )

        group = f"it-crash-{unique_suffix}"
        seen: list[str] = []

        consumer = IndexingRedisStreamsConsumer(
            logging.getLogger("it-consumer"),
            _stream_config(host, port, base_stream, _LANES, group),
            fair_scheduler_config=_fair(),
        )
        await consumer.start(_handler([], seen))
        try:
            deadline = asyncio.get_running_loop().time() + DRAIN_TIMEOUT_SECONDS
            while len(seen) < total // 3:
                if asyncio.get_running_loop().time() > deadline:
                    raise AssertionError("consumer never made progress")
                await asyncio.sleep(0.05)
            first_run = len(seen)
        finally:
            await consumer.stop()

        assert first_run > 0, "the first run never made progress"
        if len(seen) >= total:
            pytest.skip(
                "the broker drained the whole backlog before shutdown, so "
                "there is no partial state left to recover from"
            )

        consumer = IndexingRedisStreamsConsumer(
            logging.getLogger("it-consumer"),
            _stream_config(host, port, base_stream, _LANES, group),
            fair_scheduler_config=_fair(),
        )
        await consumer.start(_handler([], seen))
        try:
            deadline = asyncio.get_running_loop().time() + DRAIN_TIMEOUT_SECONDS
            while set(seen) != expected:
                if asyncio.get_running_loop().time() > deadline:
                    missing = expected - set(seen)
                    raise AssertionError(
                        f"{len(missing)} record(s) never indexed: "
                        f"{sorted(missing)[:10]}"
                    )
                await asyncio.sleep(0.2)
        finally:
            await consumer.stop()

        assert set(seen) == expected
