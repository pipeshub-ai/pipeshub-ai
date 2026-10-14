"""Fair scheduling and lanes against a real Redis Streams broker.

Exercises the seams a fake cannot: real consumer groups, a real pending
entries list, real XACK, and real per-lane stream routing.

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

from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
    RedisStreamsConfig,
)
from app.services.messaging.lanes.hash_router import RedisLaneRouter, stable_lane
from app.services.messaging.lanes.interface import LaneConfig
from app.services.messaging.lanes.producer import LaneAwareProducer
from app.services.messaging.redis_streams.indexing_consumer import (
    IndexingRedisStreamsConsumer,
)
from app.services.messaging.redis_streams.producer import RedisStreamsProducer
from app.services.messaging.scheduling.interface import FairSchedulerConfig
from app.services.resource_governor.models import ParseTier
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.integration.messaging.conftest import (
    DRAIN_TIMEOUT_SECONDS,
    OneTopicProducer,
    held_handler,
    run_stranded_sweep,
    stop_mid_flight,
    wait_until_held,
)
from tests.support.fake_record_graph import FakeRecordGraph

if TYPE_CHECKING:
    from app.services.messaging.lanes.backlog import LaneBacklog

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


def _lane_producer(host, port, base) -> LaneAwareProducer:
    inner = RedisStreamsProducer(
        logging.getLogger("it-producer"),
        RedisStreamsConfig(host=host, port=port, client_id="it-producer"),
    )
    return LaneAwareProducer(
        logging.getLogger("it-producer"),
        inner,
        RedisLaneRouter(_LANES),
        LaneConfig(lane_count=_LANES, laned_topics=(base,)),
    )


async def _publish(host, port, base, records):
    producer = _lane_producer(host, port, base)
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

        gate = threading.Event()
        parked: list[str] = []
        consumer = IndexingRedisStreamsConsumer(
            logging.getLogger("it-consumer"),
            _stream_config(host, port, base_stream, _LANES, group),
            fair_scheduler_config=_fair(),
        )
        await consumer.start(held_handler(seen, total // 3, gate, parked))
        await stop_mid_flight(consumer, gate, parked)

        assert len(set(seen)) < total, "the first run was meant to stop partway"

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


async def _lane_entries(host, port, base) -> dict[str, list[tuple[float, str]]]:
    """Per lane stream, (published ms, recordId) of every entry, in stream order."""
    from redis.asyncio import Redis

    client = Redis(host=host, port=port, decode_responses=True)
    try:
        lanes: dict[str, list[tuple[float, str]]] = {}
        for stream in RedisLaneRouter(_LANES).lane_topics(base):
            entries = await client.xrange(stream) if await client.exists(stream) else []
            lanes[stream] = [
                (float(entry_id.split("-")[0]), json.loads(fields["value"])["payload"]["recordId"])
                for entry_id, fields in entries
            ]
        return lanes
    finally:
        await client.aclose()


async def _oldest_unfinished(host, port, base, finished: list[str]) -> dict[str, float]:
    """Worked out from the streams and what the handler finished, not from the group."""
    done = set(finished)
    return {
        stream: next(ms for ms, record_id in entries if record_id not in done)
        for stream, entries in (await _lane_entries(host, port, base)).items()
        if any(record_id not in done for _ms, record_id in entries)
    }


async def _backlog_reaches(consumer, base, expected: dict[str, float]) -> LaneBacklog:
    """Acknowledgements land a moment after the handler returns."""
    deadline = asyncio.get_running_loop().time() + DRAIN_TIMEOUT_SECONDS
    while True:
        backlog = await consumer.lane_backlog(base)
        if dict(backlog.oldest_waiting_ms) == expected:
            return backlog
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(
                f"backlog {dict(backlog.oldest_waiting_ms)} never became {expected}"
            )
        await asyncio.sleep(0.2)


class TestLaneBacklogOnARealBroker:
    """What the stranded-record sweep asks the broker before re-sending a record."""

    @pytest.fixture(autouse=True)
    def _laned_as_the_producer_is(self, monkeypatch, base_stream) -> None:
        monkeypatch.setenv("FAIR_SCHEDULING_LANE_COUNT", str(_LANES))
        monkeypatch.setenv("FAIR_SCHEDULING_LANED_TOPICS", base_stream)
        monkeypatch.setenv("STRANDED_RECORD_REPUBLISH_AFTER_SECONDS", "3600")

    async def test_each_lane_reports_its_oldest_unfinished_event_until_it_is_drained(
        self, redis_available, base_stream, unique_suffix
    ) -> None:
        """With records in flight, buffered, parked behind a full key and not
        yet delivered, the lane is as old as the oldest of them."""
        host, port = redis_available
        records = [("user-a", f"a-{i}") for i in range(12)]
        records += [("user-b", f"b-{i}") for i in range(4)]
        await _publish(host, port, base_stream, records)

        consumer = IndexingRedisStreamsConsumer(
            logging.getLogger("it-consumer"),
            _stream_config(host, port, base_stream, _LANES, f"it-backlog-{unique_suffix}"),
            # A low per-key cap, so user-a's lane is paused with entries undelivered.
            fair_scheduler_config=_fair(max_per_entity_messages=3),
        )
        seen: list[str] = []
        parked: list[str] = []
        gate = threading.Event()
        # Groups created, nothing delivered: each lane is as old as its first entry.
        await consumer.initialize()
        try:
            untouched = await _oldest_unfinished(host, port, base_stream, [])
            assert len(untouched) >= 1
            assert dict((await consumer.lane_backlog(base_stream)).oldest_waiting_ms) == untouched

            await consumer.start(held_handler(seen, 5, gate, parked))
            await wait_until_held(seen, parked)
            expected = await _oldest_unfinished(host, port, base_stream, seen)
            assert expected, "the hold was meant to leave work on the lanes"

            backlog = await _backlog_reaches(consumer, base_stream, expected)

            lane_a = f"{base_stream}.{stable_lane('user-a', _LANES)}"
            assert backlog.oldest_waiting_for({"connectorId": "user-a"}) == expected[lane_a]

            gate.set()
            await _drain(seen, len(records))
            await _backlog_reaches(consumer, base_stream, {})
        finally:
            gate.set()
            await consumer.stop()

    async def test_the_sweep_resends_only_the_record_the_lane_has_moved_past(
        self, redis_available, base_stream, unique_suffix
    ) -> None:
        host, port = redis_available
        records = [("user-a", f"a-{i}") for i in range(8)]
        graph = FakeRecordGraph()
        queued_at = get_epoch_timestamp_in_ms()
        for connector_id, record_id in records:
            graph.add_queued(record_id, connector_id, queued_at)
        # Queued with the rest, but its event never reached the broker.
        graph.add_queued("lost", "user-a", queued_at)
        await _publish(host, port, base_stream, records)

        consumer = IndexingRedisStreamsConsumer(
            logging.getLogger("it-consumer"),
            _stream_config(host, port, base_stream, _LANES, f"it-sweep-{unique_suffix}"),
            fair_scheduler_config=_fair(),
        )
        seen: list[str] = []
        parked: list[str] = []
        gate = threading.Event()
        lane_producer = _lane_producer(host, port, base_stream)
        await lane_producer.initialize()
        producer = OneTopicProducer(lane_producer, base_stream)
        await consumer.start(held_handler(seen, 2, gate, parked))
        try:
            await wait_until_held(seen, parked)
            for record_id in seen:
                graph.mark_indexed(record_id)

            # Two hours on, six records and the lost one are all still QUEUED,
            # and the lane still holds events as old as they are.
            assert await run_stranded_sweep(graph, producer, consumer, base_stream, hours_later=2) == 0
            published = await _lane_entries(host, port, base_stream)
            assert sum(len(entries) for entries in published.values()) == len(records)

            gate.set()
            await _drain(seen, len(records))
            for record_id in seen:
                graph.mark_indexed(record_id)
            await _backlog_reaches(consumer, base_stream, {})

            assert graph.still_queued() == ["lost"]
            assert await run_stranded_sweep(graph, producer, consumer, base_stream, hours_later=2) == 1
            await _drain(seen, len(records) + 1)
            assert seen.count("lost") == 1
            assert graph.records["lost"]["republishCount"] == 1
        finally:
            gate.set()
            await consumer.stop()
            await lane_producer.cleanup()
