"""Entries a stopped consumer left pending are reclaimed while new events keep
arriving, and do not hold back the stranded-record sweep, on a real Redis.

Requires:
  docker compose -f deployment/docker-compose/docker-compose.integration.messaging.yml up -d
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
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
        stopped_consumer_after_ms=claim_min_idle_ms * 10,
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
            batch_size=1, block_ms=500, claim_min_idle_ms=_CLAIM_MS, stopped_consumer_after_ms=_CLAIM_MS * 10,
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

        # Relative, not a rate: a slow runner managed about 125 here, while a
        # stalled drain moves about one record every two seconds (10 in 20s).
        assert moved >= 5 * 10, f"only {moved} stranded records processed in 20s, as if stalled"


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
            batch_size=1, block_ms=500, claim_min_idle_ms=_CLAIM_MS, stopped_consumer_after_ms=_CLAIM_MS * 10,
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
            batch_size=10, block_ms=500, claim_min_idle_ms=_CLAIM_MS, stopped_consumer_after_ms=_CLAIM_MS * 10,
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


class TestALiveConsumerWhoseLoopPauses:
    async def test_its_held_entries_still_hold_the_lane_after_a_pause(
        self, redis_available, redis_client, base_stream, unique_suffix
    ) -> None:
        """Its event loop blocks for several claim windows (a slow first import
        of a large module did, on a CI runner). Nothing it holds is refreshed
        meanwhile, yet it is alive: its entries must still count."""
        host, port = redis_available
        group = f"it-pause-{unique_suffix}"
        topics = RedisLaneRouter(_LANES).lane_topics(base_stream)
        await _create_groups(redis_client, topics, group)
        connector = _connector_on_lane(1, "held")
        lane = f"{base_stream}.1"
        for i in range(4):
            await redis_client.xadd(lane, _event(connector, f"held-{i}"))
        config = RedisStreamsConfig(
            host=host, port=port, client_id=f"{group}-client", group_id=group, topics=topics,
            batch_size=10, block_ms=500, claim_min_idle_ms=_CLAIM_MS,
        )
        consumer = IndexingRedisStreamsConsumer(
            logging.getLogger("it-pause"), config, fair_scheduler_config=FairSchedulerConfig(
                enabled=True, key_fields=("orgId", "connectorId"), max_buffered_messages=20,
                max_per_entity_messages=20,
            ),
        )
        # A threading.Event: handlers run on the consumer's worker loop.
        gate = threading.Event()

        async def hold(parsed_message: StreamMessage) -> AsyncIterator[PipelineEvent]:
            yield PipelineEvent(event=IndexingEvent.START_PARSING, data=PipelineEventData(tier=ParseTier.LIGHT))
            await asyncio.to_thread(gate.wait, DRAIN_TIMEOUT_SECONDS)
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE)

        await consumer.start(hold)
        try:
            await _until(
                lambda: _pending_at_least(redis_client, lane, group, 4), "the entries to be read"
            )
            time.sleep(_CLAIM_MS * 10 / 1000 + 1)  # the whole main loop, the consumer's included
            backlog = await consumer._read_lane_backlog(base_stream)
        finally:
            gate.set()
            await consumer.stop()

        assert backlog.stale == {}
        assert backlog.oldest_waiting_for({"connectorId": connector}) is not None


async def _pending_at_least(client: Redis, stream: str, group: str, count: int) -> bool:
    return int((await client.xpending(stream, group))["pending"]) >= count


class TestATrimmedEntryOfAStoppedConsumer:
    async def test_it_does_not_stay_pending_under_the_live_consumer(
        self, redis_available, redis_client, base_stream, unique_suffix
    ) -> None:
        """Taken over while reading, a stopped consumer's trimmed entry must not
        stay pending under this live consumer, and the entries beside it in the
        same claim are processed. Before Redis 7.0 the claim answers a trimmed
        entry with an empty slot and moves its id to the claimer. The claim
        window is long here so that only the read-time reclaim takes them."""
        host, port = redis_available
        group = f"it-trimmed-{unique_suffix}"
        topics = RedisLaneRouter(_LANES).lane_topics(base_stream)
        await _create_groups(redis_client, topics, group)
        lane = f"{base_stream}.0"
        await _leave_pending(redis_client, lane, group, _connector_on_lane(0, "old"), per_consumer=2)
        first = (await redis_client.xpending_range(lane, group, min="-", max="+", count=1))[0]["message_id"]
        await redis_client.xdel(lane, first)
        await asyncio.sleep(2.5)

        config = RedisStreamsConfig(
            host=host, port=port, client_id=f"{group}-client", group_id=group, topics=topics,
            batch_size=10, block_ms=500, claim_min_idle_ms=60_000, stopped_consumer_after_ms=2_000,
        )
        done: list[str] = []
        consumer = IndexingRedisStreamsConsumer(
            logging.getLogger("it-trimmed"), config, fair_scheduler_config=FairSchedulerConfig(
                enabled=True, key_fields=("orgId", "connectorId"), max_buffered_messages=40,
                max_per_entity_messages=40,
            ),
        )
        await consumer.start(_handler(done, seconds=0.0))
        try:
            await _until(lambda: _done(done, [f"old-{lane}-{i}" for i in range(1, 6)]), "the rest")
            await _until(lambda: _nothing_pending(redis_client, lane, group), "an empty pending list")
        finally:
            await consumer.stop()


async def _nothing_pending(client: Redis, stream: str, group: str) -> bool:
    return int((await client.xpending(stream, group))["pending"]) == 0


class TestAnEntryRecoveryCouldNotHandOn:
    async def test_it_is_processed_beside_another_live_consumer_under_traffic(
        self, redis_available, redis_client, base_stream, unique_suffix
    ) -> None:
        """Two live consumers, new events arriving all the time, and the check
        before a reclaimed entry is buffered fails once for each. The entries
        are left pending under the consumer that claimed them, which nothing
        else takes back while the other consumer is alive and traffic flows."""
        host, port = redis_available
        group = f"it-unhanded-{unique_suffix}"
        topics = RedisLaneRouter(_LANES).lane_topics(base_stream)
        await _create_groups(redis_client, topics, group)
        lane = f"{base_stream}.0"
        stranded = await _leave_pending(redis_client, lane, group, _connector_on_lane(0, "old"), per_consumer=3)
        await asyncio.sleep(2.5)

        # A long claim window, as in production, so the idle drain's XAUTOCLAIM
        # cannot be what brings them back; and the production read block.
        config = RedisStreamsConfig(
            host=host, port=port, client_id=f"{group}-client", group_id=group, topics=topics,
            batch_size=10, block_ms=2000, claim_min_idle_ms=60_000, stopped_consumer_after_ms=2_000,
        )
        done: list[str] = []
        failed: set[str] = set()
        consumers = [
            IndexingRedisStreamsConsumer(
                logging.getLogger(f"it-unhanded-{n}"), config.model_copy(),
                fair_scheduler_config=FairSchedulerConfig(
                    enabled=True, key_fields=("orgId", "connectorId"), max_buffered_messages=40,
                    max_per_entity_messages=40,
                ),
            )
            for n in range(2)
        ]
        for consumer in consumers:
            real_check = consumer._should_dead_letter

            async def fails_once(topic, message_id, *args, _real=real_check, **kwargs):  # noqa: ANN202
                if topic == lane and message_id not in failed:
                    failed.add(message_id)
                    raise ConnectionError("retry store unreachable")
                return await _real(topic, message_id, *args, **kwargs)

            consumer._should_dead_letter = fails_once
            await consumer.start(_handler(done, seconds=0.0))
        stop = asyncio.Event()

        async def keep_arriving() -> None:
            n = 0
            while not stop.is_set():
                await redis_client.xadd(f"{base_stream}.1", _event("fresh", f"new-{n}"))
                n += 1
                await asyncio.sleep(0.3)

        trickle = asyncio.create_task(keep_arriving())
        try:
            await _until(lambda: _done(done, stranded), "every stranded entry", timeout=30)
        finally:
            stop.set()
            await trickle
            for consumer in consumers:
                await consumer.stop()
        assert failed


class TestTheFifoPathWithFullGates:
    async def test_a_consumer_held_back_by_full_gates_keeps_its_running_entry_fresh(
        self, redis_available, redis_client, base_stream, unique_suffix, monkeypatch
    ) -> None:
        """Without fair scheduling, full index gates sent the loop round again
        before anything touched Redis, so a long record's entry went idle and a
        peer could take it while its handler still ran."""
        from app.services.messaging import consumer_concurrency

        host, port = redis_available
        group = f"it-fifo-{unique_suffix}"
        topics = RedisLaneRouter(_LANES).lane_topics(base_stream)
        await _create_groups(redis_client, topics, group)
        lane = f"{base_stream}.0"
        await redis_client.xadd(lane, _event(_connector_on_lane(0, "long"), "long"))
        config = RedisStreamsConfig(
            host=host, port=port, client_id=f"{group}-client", group_id=group, topics=topics,
            batch_size=1, block_ms=500, claim_min_idle_ms=1500,
        )
        consumer = IndexingRedisStreamsConsumer(logging.getLogger("it-fifo"), config)
        started = threading.Event()
        gate = threading.Event()

        async def hold(parsed_message: StreamMessage) -> AsyncIterator[PipelineEvent]:
            yield PipelineEvent(event=IndexingEvent.START_PARSING, data=PipelineEventData(tier=ParseTier.LIGHT))
            started.set()
            await asyncio.to_thread(gate.wait, DRAIN_TIMEOUT_SECONDS)
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE)

        await consumer.start(hold)
        try:
            await asyncio.to_thread(started.wait, 10)
            monkeypatch.setattr(consumer_concurrency, "index_gates_saturated", lambda _host: True)
            await asyncio.sleep(4)
            entry = (await redis_client.xpending_range(lane, group, min="-", max="+", count=1))[0]
        finally:
            gate.set()
            await consumer.stop()

        assert entry["time_since_delivered"] < 1500
        assert entry["times_delivered"] == 1


class TestTheIdleDrainOnEveryRedisVersion:
    async def test_it_reclaims_a_stopped_consumers_entries_including_a_deleted_one(
        self, redis_available, redis_client, base_stream, unique_suffix
    ) -> None:
        """XAUTOCLAIM replies with three parts on Redis 7 and two on 6.2, where
        a deleted entry is an empty slot whose id moves to the claimer. The
        drain took only the first shape, so on 6.2 it reclaimed nothing."""
        host, port = redis_available
        group = f"it-drain-shape-{unique_suffix}"
        topics = RedisLaneRouter(_LANES).lane_topics(base_stream)
        await _create_groups(redis_client, topics, group)
        lane = f"{base_stream}.0"
        stranded = await _leave_pending(redis_client, lane, group, _connector_on_lane(0, "old"), per_consumer=2)
        first = (await redis_client.xpending_range(lane, group, min="-", max="+", count=1))[0]["message_id"]
        await redis_client.xdel(lane, first)
        await asyncio.sleep(_CLAIM_MS / 1000 + 0.3)

        # A long stopped-consumer bound: only the drain takes them here.
        config = RedisStreamsConfig(
            host=host, port=port, client_id=f"{group}-client", group_id=group, topics=topics,
            batch_size=10, block_ms=300, claim_min_idle_ms=_CLAIM_MS, stopped_consumer_after_ms=600_000,
        )
        done: list[str] = []
        consumer = IndexingRedisStreamsConsumer(
            logging.getLogger("it-drain-shape"), config, fair_scheduler_config=FairSchedulerConfig(
                enabled=True, key_fields=("orgId", "connectorId"), max_buffered_messages=40,
                max_per_entity_messages=40,
            ),
        )
        await consumer.start(_handler(done, seconds=0.0))
        try:
            await _until(lambda: _done(done, stranded[1:]), "the entries left", timeout=30)
            await _until(lambda: _nothing_pending(redis_client, lane, group), "an empty pending list", timeout=30)
        finally:
            await consumer.stop()


class TestOwnEntriesBesideALivePeer:
    @pytest.mark.parametrize("fair_scheduling", [True, False], ids=["fair", "fifo"])
    async def test_one_left_untracked_with_the_retry_list_full_comes_back_once_the_error_clears(
        self, redis_available, redis_client, base_stream, unique_suffix, monkeypatch, fair_scheduling
    ) -> None:
        """The retry list is full, another consumer is live, events keep
        arriving, and the check before buffering this consumer's own unheld
        entries fails for a while. Nothing remembers them, yet they must come
        back: read-time own-pending recovery runs beside the peer, from the
        pending list itself."""
        from app.services.messaging.redis_streams import indexing_consumer as module

        monkeypatch.setattr(module, "_UNHANDED_LIMIT", 1)
        host, port = redis_available
        group = f"it-own-{unique_suffix}"
        topics = RedisLaneRouter(_LANES).lane_topics(base_stream)
        await _create_groups(redis_client, topics, group)
        lane = f"{base_stream}.0"
        connector = _connector_on_lane(0, "own")
        records = [f"own-{i}" for i in range(4)]
        for record in records:
            await redis_client.xadd(lane, _event(connector, record))

        # A production-like claim window, so no drain claim brings them back.
        config = RedisStreamsConfig(
            host=host, port=port, client_id=f"{group}-client", group_id=group, topics=topics,
            batch_size=10, block_ms=2000, claim_min_idle_ms=60_000,
        )
        fair = FairSchedulerConfig(
            enabled=True, key_fields=("orgId", "connectorId"), max_buffered_messages=40,
            max_per_entity_messages=40,
        )
        done: list[str] = []
        if not fair_scheduling:
            fair = None
        owner = IndexingRedisStreamsConsumer(logging.getLogger("it-own"), config.model_copy(), fair_scheduler_config=fair)
        peer = IndexingRedisStreamsConsumer(logging.getLogger("it-own-peer"), config.model_copy(), fair_scheduler_config=fair)
        # Read by the owner, and never handed on: pending under it, unheld.
        await redis_client.xreadgroup(group, owner.consumer_name, {lane: ">"})
        owner._unhanded = {(lane, "0-1"): None}
        broken = {"on": True}
        real_check = owner._should_dead_letter

        async def check(topic, message_id, *args, **kwargs):  # noqa: ANN202
            if broken["on"] and topic == lane:
                raise ConnectionError("retry store unreachable")
            return await real_check(topic, message_id, *args, **kwargs)

        owner._should_dead_letter = check
        await peer.start(_handler(done, seconds=0.0))
        await owner.start(_handler(done, seconds=0.0))
        stop = asyncio.Event()

        async def keep_arriving() -> None:
            n = 0
            while not stop.is_set():
                await redis_client.xadd(f"{base_stream}.1", _event("fresh", f"new-{n}"))
                n += 1
                await asyncio.sleep(0.3)

        trickle = asyncio.create_task(keep_arriving())
        try:
            await asyncio.sleep(3)
            assert not set(records) & set(done)
            broken["on"] = False
            await _until(lambda: _done(done, records), "the owner's own entries", timeout=20)
        finally:
            stop.set()
            await trickle
            await owner.stop()
            await peer.stop()


class TestOwnEntriesBehindOnesThatKeepFailing:
    @pytest.mark.parametrize("fair_scheduling", [True, False], ids=["fair", "fifo"])
    async def test_an_entry_behind_ten_that_always_fail_is_still_recovered(
        self, redis_available, redis_client, base_stream, unique_suffix, monkeypatch, fair_scheduling
    ) -> None:
        """Ten entries at the head of this consumer's pending list fail their
        handoff every time, the retry list is full, a peer is live and events
        keep arriving. A scan from the head of the list took those ten every
        time, so the entry behind them was never reached. Driven through the
        running consumers' own loops."""
        from app.services.messaging.redis_streams import indexing_consumer as module

        monkeypatch.setattr(module, "_UNHANDED_LIMIT", 1)
        host, port = redis_available
        group = f"it-behind-{unique_suffix}"
        topics = RedisLaneRouter(_LANES).lane_topics(base_stream)
        await _create_groups(redis_client, topics, group)
        lane = f"{base_stream}.0"
        connector = _connector_on_lane(0, "own")
        for i in range(10):
            await redis_client.xadd(lane, _event(connector, f"poison-{i}"))
        await redis_client.xadd(lane, _event(connector, "behind"))

        config = RedisStreamsConfig(
            host=host, port=port, client_id=f"{group}-client", group_id=group, topics=topics,
            batch_size=10, block_ms=2000, claim_min_idle_ms=60_000,
        )
        fair = FairSchedulerConfig(
            enabled=True, key_fields=("orgId", "connectorId"), max_buffered_messages=40,
            max_per_entity_messages=40,
        ) if fair_scheduling else None
        done: list[str] = []
        owner = IndexingRedisStreamsConsumer(logging.getLogger("it-behind"), config.model_copy(), fair_scheduler_config=fair)
        peer = IndexingRedisStreamsConsumer(logging.getLogger("it-behind-peer"), config.model_copy(), fair_scheduler_config=fair)
        read = await redis_client.xreadgroup(group, owner.consumer_name, {lane: ">"})
        poison = {message_id for _s, entries in read for message_id, _f in entries[:10]}
        behind = read[0][1][10][0]
        owner._unhanded = {(lane, "0-1"): None}
        real_check = owner._should_dead_letter
        # The entry behind fails too until the startup drain is over, so only
        # read-time recovery, under traffic, can bring it back.
        behind_fails = {"on": True}

        async def check(topic, message_id, *args, **kwargs):  # noqa: ANN202
            if message_id in poison or (message_id == behind and behind_fails["on"]):
                raise ConnectionError("retry store unreachable for this record")
            return await real_check(topic, message_id, *args, **kwargs)

        owner._should_dead_letter = check
        await peer.start(_handler(done, seconds=0.0))
        await owner.start(_handler(done, seconds=0.0))
        stop = asyncio.Event()

        async def keep_arriving() -> None:
            n = 0
            while not stop.is_set():
                await redis_client.xadd(f"{base_stream}.1", _event("fresh", f"new-{n}"))
                n += 1
                await asyncio.sleep(0.3)

        trickle = asyncio.create_task(keep_arriving())
        try:
            await asyncio.sleep(3)
            assert "behind" not in done
            behind_fails["on"] = False
            await _until(lambda: _done(done, ["behind"]), "the entry behind the failing ones", timeout=20)
        finally:
            stop.set()
            await trickle
            await owner.stop()
            await peer.stop()
        assert not {f"poison-{i}" for i in range(10)} & set(done)


class TestRetriesOnTheFifoPathRespectAdmission:
    async def test_no_task_starts_while_the_gates_are_full(
        self, redis_available, redis_client, base_stream, unique_suffix, monkeypatch
    ) -> None:
        """Without fair scheduling, handing a remembered entry on starts its
        task: under backpressure (full index gates) none may start until the
        gates open."""
        from app.services.messaging import consumer_concurrency

        host, port = redis_available
        group = f"it-fifo-admit-{unique_suffix}"
        topics = RedisLaneRouter(_LANES).lane_topics(base_stream)
        await _create_groups(redis_client, topics, group)
        lane = f"{base_stream}.0"
        await redis_client.xadd(lane, _event(_connector_on_lane(0, "own"), "remembered"))
        config = RedisStreamsConfig(
            host=host, port=port, client_id=f"{group}-client", group_id=group, topics=topics,
            batch_size=10, block_ms=500, claim_min_idle_ms=60_000,
        )
        consumer = IndexingRedisStreamsConsumer(logging.getLogger("it-fifo-admit"), config)
        read = await redis_client.xreadgroup(group, consumer.consumer_name, {lane: ">"})
        consumer._unhanded = {(lane, read[0][1][0][0]): None}
        gates = {"full": True}
        monkeypatch.setattr(consumer_concurrency, "index_gates_saturated", lambda _host: gates["full"])
        # Keeps the startup drain and own-pending recovery from taking it, so
        # only the retry could start it.
        consumer._IndexingRedisStreamsConsumer__recover_while_reading = _no_op
        real_drain = consumer._drain_pending

        async def drain_only_after_start() -> bool:
            return False if gates["full"] else await real_drain()

        consumer._drain_pending = drain_only_after_start
        done: list[str] = []
        await consumer.start(_handler(done, seconds=0.0))
        try:
            await asyncio.sleep(3)
            assert done == []
            gates["full"] = False
            await _until(lambda: _done(done, ["remembered"]), "the remembered entry", timeout=15)
        finally:
            await consumer.stop()


async def _no_op(*_args, **_kwargs) -> None:
    return None
