"""Entries a stopped consumer left pending are reclaimed while new events keep
arriving, and do not hold back the stranded-record sweep, on a real Redis.

Requires:
  docker compose -f deployment/docker-compose/docker-compose.integration.messaging.yml up -d
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
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
from app.services.resource_governor.models import ParseTier, ResourceSnapshot
from tests.integration.messaging.conftest import DRAIN_TIMEOUT_SECONDS

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from app.services.messaging.config import StreamMessage

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.timeout(180)]

_LANES = 4
_CLAIM_MS = 500
_STOPPED = ("replaced-1", "replaced-2", "replaced-3")


def _connector_on_lane(lane: int, prefix: str) -> str:
    for n in range(10_000):
        candidate = f"{prefix}-{n}"
        if stable_lane(candidate, _LANES) == lane:
            return candidate
    raise AssertionError(f"no connector id found for lane {lane}")


def _event(connector_id: str, record_id: str) -> dict[str, str]:
    payload = {
        "recordId": record_id,
        "orgId": "org-1",
        "connectorId": connector_id,
        "extension": "txt",
        "mimeType": "text/plain",
    }
    return {"value": json.dumps({"eventType": "newRecord", "payload": payload})}


def _consumer(host, port, base, group, *, claim_min_idle_ms=_CLAIM_MS) -> IndexingRedisStreamsConsumer:
    config = RedisStreamsConfig(
        host=host,
        port=port,
        client_id=f"{group}-client",
        group_id=group,
        topics=RedisLaneRouter(_LANES).lane_topics(base),
        batch_size=10,
        block_ms=500,
        claim_min_idle_ms=claim_min_idle_ms,
    )
    # A small buffer, so it is full most of the time while the trickle runs.
    fair = FairSchedulerConfig(
        enabled=True,
        key_fields=("orgId", "connectorId"),
        max_buffered_messages=20,
        max_per_entity_messages=10,
    )
    return IndexingRedisStreamsConsumer(
        logging.getLogger("it-stopped-consumers"), config, fair_scheduler_config=fair
    )


def _handler(done: list[str], seconds: float) -> Callable:
    async def handle(parsed_message: StreamMessage) -> AsyncIterator[PipelineEvent]:
        yield PipelineEvent(
            event=IndexingEvent.START_PARSING, data=PipelineEventData(tier=ParseTier.LIGHT)
        )
        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE)
        await asyncio.sleep(seconds)
        done.append(parsed_message.payload["recordId"])
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
    name = f"record-events-stopped-{unique_suffix}"
    yield name
    for stream in [*RedisLaneRouter(_LANES).lane_topics(name), f"{name}.9"]:
        await redis_client.delete(stream)


async def _leave_pending(
    client: Redis, stream: str, group: str, connector: str, per_consumer: int, *, first_ms: int | None = None
) -> list[str]:
    """Entries read by consumers that then stopped, never acknowledged."""
    total = per_consumer * len(_STOPPED)
    for i in range(total):
        entry_id = f"{first_ms}-{i}" if first_ms is not None else "*"
        await client.xadd(stream, _event(connector, f"old-{stream}-{i}"), id=entry_id)
    for name in _STOPPED:
        await client.xreadgroup(group, name, {stream: ">"}, count=per_consumer)
    return [f"old-{stream}-{i}" for i in range(total)]


async def _create_groups(client: Redis, streams: list[str], group: str) -> None:
    for stream in streams:
        await client.xgroup_create(stream, group, id="0", mkstream=True)


async def _until(predicate, what: str, timeout: float = DRAIN_TIMEOUT_SECONDS) -> None:
    deadline = time.monotonic() + timeout
    while not await predicate():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.2)


async def _consumer_names(client: Redis, stream: str, group: str) -> set[str]:
    return {c["name"] for c in await client.xinfo_consumers(stream, group)}


class TestReclaimWhileEventsKeepArriving:
    async def test_every_entry_a_stopped_consumer_left_is_processed_and_the_consumer_removed(
        self, redis_available, redis_client, base_stream, unique_suffix
    ) -> None:
        """Three replaced processes left 300 entries pending on lane 0. New events
        arrive on lane 1 faster than the idle drain's quiet window, so before,
        the startup drain took one buffer of them and the rest stayed pending."""
        host, port = redis_available
        group = f"it-stopped-{unique_suffix}"
        topics = RedisLaneRouter(_LANES).lane_topics(base_stream)
        await _create_groups(redis_client, topics, group)
        old_lane, new_lane = f"{base_stream}.0", f"{base_stream}.1"
        stranded = await _leave_pending(
            redis_client, old_lane, group, _connector_on_lane(0, "old"), per_consumer=100
        )
        await asyncio.sleep(_CLAIM_MS / 1000 + 0.2)

        done: list[str] = []
        consumer = _consumer(host, port, base_stream, group)
        await consumer.start(_handler(done, seconds=0.005))
        stop = asyncio.Event()

        async def keep_arriving() -> None:
            n = 0
            fresh = _connector_on_lane(1, "fresh")
            while not stop.is_set():
                await redis_client.xadd(new_lane, _event(fresh, f"new-{n}"))
                n += 1
                await asyncio.sleep(0.1)

        trickle = asyncio.create_task(keep_arriving())
        try:
            await _until(
                lambda: _done(done, stranded), "every stranded entry to be processed"
            )
            await _until(
                lambda: _no_stopped_consumer(redis_client, old_lane, group),
                "the stopped consumers to be removed from the group",
            )
        finally:
            stop.set()
            await trickle
            await consumer.stop()

        assert (await redis_client.xpending(old_lane, group))["pending"] == 0
        processed = [record for record in done if record in set(stranded)]
        assert len(processed) == len(set(processed))

    async def test_a_lane_left_over_from_a_larger_lane_count_is_reclaimed_too(
        self, redis_available, redis_client, base_stream, unique_suffix
    ) -> None:
        host, port = redis_available
        group = f"it-leftover-{unique_suffix}"
        topics = RedisLaneRouter(_LANES).lane_topics(base_stream)
        leftover = f"{base_stream}.9"
        await _create_groups(redis_client, [*topics, leftover], group)
        stranded = await _leave_pending(redis_client, leftover, group, "old-connector", per_consumer=20)
        await asyncio.sleep(_CLAIM_MS / 1000 + 0.2)

        done: list[str] = []
        consumer = _consumer(host, port, base_stream, group)
        await consumer.start(_handler(done, seconds=0.0))
        stop = asyncio.Event()

        async def keep_arriving() -> None:
            n = 0
            while not stop.is_set():
                await redis_client.xadd(f"{base_stream}.1", _event("fresh", f"new-{n}"))
                n += 1
                await asyncio.sleep(0.1)

        trickle = asyncio.create_task(keep_arriving())
        try:
            await _until(lambda: _done(done, stranded), "the leftover lane's entries to be processed")
        finally:
            stop.set()
            await trickle
            await consumer.stop()

        assert leftover in consumer.config.topics


async def _done(done: list[str], wanted: list[str]) -> bool:
    return set(wanted) <= set(done)


async def _no_stopped_consumer(client: Redis, stream: str, group: str) -> bool:
    return not (await _consumer_names(client, stream, group)) & set(_STOPPED)


class TestTheSweepAndStoppedConsumers:
    async def test_a_stopped_consumers_old_entries_do_not_hold_a_queued_record(
        self, redis_available, redis_client, base_stream, unique_suffix
    ) -> None:
        """Entries published 17 days ago, held by consumers that stopped, sit at
        the head of the lane's pending list. A record queued two hours ago on
        that lane was left alone on every sweep pass before."""
        host, port = redis_available
        group = f"it-sweep-{unique_suffix}"
        topics = RedisLaneRouter(_LANES).lane_topics(base_stream)
        await _create_groups(redis_client, topics, group)
        connector = _connector_on_lane(2, "queued")
        now_ms = int(time.time() * 1000)
        await _leave_pending(
            redis_client, f"{base_stream}.2", group, connector, per_consumer=5,
            first_ms=now_ms - 17 * 24 * 3_600_000,
        )
        consumer = _consumer(host, port, base_stream, group, claim_min_idle_ms=50)
        await consumer.initialize()
        try:
            await asyncio.sleep(consumer.stopped_consumer_after_ms() / 1000 + 0.3)
            backlog = await consumer._read_lane_backlog(base_stream)
        finally:
            await consumer.stop()

        queued_at = now_ms - 2 * 3_600_000
        assert backlog.oldest_waiting_ms[f"{base_stream}.2"] < queued_at
        assert backlog.oldest_waiting_for({"connectorId": connector}) is None
        assert set(backlog.stale[f"{base_stream}.2"].owners) == set(_STOPPED)

    async def test_a_live_consumers_pending_entries_still_hold_it(
        self, redis_available, redis_client, base_stream, unique_suffix
    ) -> None:
        host, port = redis_available
        group = f"it-sweep-live-{unique_suffix}"
        topics = RedisLaneRouter(_LANES).lane_topics(base_stream)
        await _create_groups(redis_client, topics, group)
        connector = _connector_on_lane(2, "queued")
        lane = f"{base_stream}.2"
        published_ms = int(time.time() * 1000) - 5 * 3_600_000
        await redis_client.xadd(lane, _event(connector, "busy-1"), id=f"{published_ms}-0")
        await redis_client.xreadgroup(group, "live-replica", {lane: ">"}, count=1)
        consumer = _consumer(host, port, base_stream, group)
        await consumer.initialize()
        try:
            backlog = await consumer._read_lane_backlog(base_stream)
        finally:
            await consumer.stop()

        assert backlog.oldest_waiting_for({"connectorId": connector}) == float(published_ms)
        assert backlog.stale == {}


class _MemoryPressureProbe:
    """Memory at 88-90 % of the limit: the governor halves heavy indexing and
    holds heavy parsing at one slot, rate limited."""

    def snapshot(self) -> ResourceSnapshot:

        limit = 16 * 1024**3
        return ResourceSnapshot(
            cpu_quota=4.0, cpu_utilisation=0.05, cpu_throttled_ratio=0.0, cpu_pressure=0.0,
            mem_limit_bytes=limit, mem_working_set_bytes=int(limit * 0.89), source="it-probe",
        )


def _tiered_handler(done: list[str], seconds: float) -> Callable:
    async def handle(parsed_message: StreamMessage) -> AsyncIterator[PipelineEvent]:
        heavy = parsed_message.payload.get("extension") == "pdf"
        yield PipelineEvent(
            event=IndexingEvent.START_PARSING,
            data=PipelineEventData(tier=ParseTier.HEAVY if heavy else ParseTier.LIGHT),
        )
        await asyncio.sleep(seconds)
        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE)
        done.append(parsed_message.payload["recordId"])
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE)

    return handle


@pytest.fixture
async def sixteen_lanes(redis_client: Redis, unique_suffix: str) -> AsyncIterator[str]:
    name = f"record-events-pressure-{unique_suffix}"
    yield name
    for stream in RedisLaneRouter(16).lane_topics(name):
        await redis_client.delete(stream)


class TestUnderMemoryPressure:
    async def test_a_stopped_backlog_drains_while_held_heavy_records_wait_at_its_head(
        self, redis_available, redis_client, sixteen_lanes, unique_suffix, tmp_path
    ) -> None:
        """Heavy records wait at the head of the pending list for the one heavy
        parse slot while the rest of a stopped backlog keeps being taken over
        and processed (the drain's scan past them is pinned down exactly in
        ``TestIdleDrainPastHeldEntries``)."""
        from app.services.resource_governor import ResourceGovernor

        host, port = redis_available
        group = f"it-pressure-{unique_suffix}"
        topics = RedisLaneRouter(16).lane_topics(sixteen_lanes)
        await _create_groups(redis_client, topics, group)
        lane = f"{sixteen_lanes}.7"
        pipe = redis_client.pipeline()
        for i in range(1500):
            payload = json.loads(_event("old-connector", f"old-{i}")["value"])
            payload["payload"]["extension"] = "pdf" if i % 7 == 0 else "txt"
            pipe.xadd(lane, {"value": json.dumps(payload)})
        await pipe.execute()
        for name in _STOPPED:
            await redis_client.xreadgroup(group, name, {lane: ">"}, count=500)
        await asyncio.sleep(_CLAIM_MS / 1000 + 0.2)

        governor = ResourceGovernor(
            logger=logging.getLogger("it-governor"), worker_count=1,
            heavy_parse_slots_dir=tmp_path, probe=_MemoryPressureProbe(), sample_interval=0.5,
        )
        governing = asyncio.create_task(governor.run())
        config = RedisStreamsConfig(
            host=host, port=port, client_id=f"{group}-client", group_id=group, topics=topics,
            batch_size=1, block_ms=500, claim_min_idle_ms=_CLAIM_MS,
        )
        fair = FairSchedulerConfig(
            enabled=True, key_fields=("orgId", "connectorId"),
            max_buffered_messages=200, max_per_entity_messages=200,
        )
        done: list[str] = []
        consumer = IndexingRedisStreamsConsumer(
            logging.getLogger("it-pressure"), config, governor=governor, fair_scheduler_config=fair
        )
        await consumer.start(_tiered_handler(done, seconds=0.02))
        try:
            await asyncio.sleep(10)
            after_start = len(done)
            await asyncio.sleep(20)
            moved = len(done) - after_start
        finally:
            await consumer.stop()
            governor.stop()
            governing.cancel()

        assert moved >= 150, f"only {moved} stranded records processed in 20s"


class TestIdleDrainPastHeldEntries:
    async def test_the_drain_scans_past_entries_it_holds_to_a_stopped_consumers(
        self, redis_available, redis_client, sixteen_lanes, unique_suffix
    ) -> None:
        """With 17 streams each drain page is one entry, and XAUTOCLAIM looks at
        ten. Entries this consumer holds and keeps fresh at the head of the
        pending list made the page empty, and the lane was dropped as done."""
        host, port = redis_available
        group = f"it-drain-{unique_suffix}"
        topics = RedisLaneRouter(16).lane_topics(sixteen_lanes)
        await _create_groups(redis_client, topics, group)
        lane = f"{sixteen_lanes}.7"
        config = RedisStreamsConfig(
            host=host, port=port, client_id=f"{group}-client", group_id=group, topics=topics,
            batch_size=1, block_ms=500, claim_min_idle_ms=_CLAIM_MS,
        )
        fair = FairSchedulerConfig(
            enabled=True, key_fields=("orgId", "connectorId"),
            max_buffered_messages=400, max_per_entity_messages=400,
        )
        consumer = IndexingRedisStreamsConsumer(logging.getLogger("it-drain"), config, fair_scheduler_config=fair)
        await consumer.initialize()
        consumer.running = True
        try:
            for i in range(30):
                await redis_client.xadd(lane, _event(f"held-{i}", f"held-{i}"))
            read = await redis_client.xreadgroup(group, consumer.consumer_name, {lane: ">"})
            for _stream, entries in read:
                for message_id, fields in entries:
                    await consumer._IndexingRedisStreamsConsumer__enqueue_message(lane, message_id, fields)
            await _leave_pending(redis_client, lane, group, "old-connector", per_consumer=2)
            await asyncio.sleep(_CLAIM_MS / 1000 + 0.2)
            consumer._last_ownership_refresh = 0.0

            await consumer._drain_pending()

            left = await redis_client.xpending_range(lane, group, min="-", max="+", count=100)
            assert {e["consumer"] for e in left} == {consumer.consumer_name}
        finally:
            consumer.running = False
            await consumer.stop()


class TestNewEventsBesideAStoppedBacklog:
    async def test_new_events_of_other_connectors_are_not_held_behind_the_backlog(
        self, redis_available, redis_client, base_stream, unique_suffix
    ) -> None:
        """While 3,000 stopped entries drain, other connectors' new events must
        still be read and scheduled at once. Reclaiming to the last free place
        left every read pass with a full buffer and nothing read."""
        host, port = redis_available
        group = f"it-fresh-{unique_suffix}"
        topics = RedisLaneRouter(_LANES).lane_topics(base_stream)
        await _create_groups(redis_client, topics, group)
        await _leave_pending(
            redis_client, f"{base_stream}.0", group, _connector_on_lane(0, "old"), per_consumer=1000
        )
        await asyncio.sleep(_CLAIM_MS * 10 / 1000 + 0.5)

        done: list[str] = []
        published: dict[str, float] = {}
        finished: dict[str, float] = {}

        async def handle(parsed_message: StreamMessage) -> AsyncIterator[PipelineEvent]:
            yield PipelineEvent(event=IndexingEvent.START_PARSING, data=PipelineEventData(tier=ParseTier.LIGHT))
            await asyncio.sleep(0.02)
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE)
            record = parsed_message.payload["recordId"]
            done.append(record)
            if record in published:
                finished[record] = time.monotonic()
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE)

        config = RedisStreamsConfig(
            host=host, port=port, client_id=f"{group}-client", group_id=group, topics=topics,
            batch_size=10, block_ms=500, claim_min_idle_ms=_CLAIM_MS,
        )
        fair = FairSchedulerConfig(
            enabled=True, key_fields=("orgId", "connectorId"),
            max_buffered_messages=400, max_per_entity_messages=400,
        )
        consumer = IndexingRedisStreamsConsumer(logging.getLogger("it-fresh"), config, fair_scheduler_config=fair)
        await consumer.start(handle)
        try:
            # The startup drain's one batch is the old behaviour; what matters
            # is what follows, while the rest of the backlog is reclaimed.
            await _until(lambda: _done(done, [f"old-{base_stream}.0-{i}" for i in range(200)]), "the startup batch")
            before = len(done)
            for n in range(60):
                record = f"fresh-{n}"
                connector = _connector_on_lane(1 + n % 3, f"fresh{n % 3}")
                published[record] = time.monotonic()
                await redis_client.xadd(f"{base_stream}.{1 + n % 3}", _event(connector, record))
                await asyncio.sleep(0.25)
            await _until(lambda: _done(list(finished), list(published)), "every new event", timeout=30)
        finally:
            await consumer.stop()

        waits = sorted(finished[r] - published[r] for r in published)
        assert waits[int(len(waits) * 0.95)] < 2.0, f"p95 wait {waits[int(len(waits) * 0.95)]:.1f}s"
        stranded_meanwhile = [r for r in done[before:] if r.startswith("old-")]
        assert len(stranded_meanwhile) > 300


class TestALiveConsumersLongRecord:
    async def test_a_record_being_processed_is_not_claimed_again(
        self, redis_available, redis_client, base_stream, unique_suffix
    ) -> None:
        """A record whose processing outlasts the claim window was claimed again
        on every look, by its own consumer's recovery or a peer's, each claim
        counting a delivery: enough of them dead-letter it."""
        host, port = redis_available
        group = f"it-long-{unique_suffix}"
        topics = RedisLaneRouter(_LANES).lane_topics(base_stream)
        await _create_groups(redis_client, topics, group)
        starts: list[str] = []

        def handler(who: str, seconds: float) -> Callable:
            async def handle(parsed_message: StreamMessage) -> AsyncIterator[PipelineEvent]:
                record = parsed_message.payload["recordId"]
                if record == "long":
                    starts.append(who)
                yield PipelineEvent(event=IndexingEvent.START_PARSING, data=PipelineEventData(tier=ParseTier.LIGHT))
                yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE)
                await asyncio.sleep(seconds if record == "long" else 0.01)
                yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE)

            return handle

        # A window the ownership refresh (at most once a second) fits inside, as
        # the default 30s does.
        first = _consumer(host, port, base_stream, group, claim_min_idle_ms=3000)
        await first.start(handler("first", 9.0))
        await redis_client.xadd(f"{base_stream}.0", _event(_connector_on_lane(0, "long"), "long"))
        await _until(lambda: _done(starts, ["first"]), "the long record to start", timeout=10)
        second = _consumer(host, port, base_stream, group, claim_min_idle_ms=3000)
        await second.start(handler("second", 0.0))
        delivered: list[int] = []
        try:
            for n in range(14):
                await redis_client.xadd(f"{base_stream}.1", _event("trickle", f"t-{n}"))
                await asyncio.sleep(0.5)
                entry = await redis_client.xpending_range(f"{base_stream}.0", group, min="-", max="+", count=1)
                if entry:
                    delivered.append(entry[0]["times_delivered"])
        finally:
            await second.stop()
            await first.stop()

        assert starts == ["first"]
        assert delivered and max(delivered) == 1, delivered
