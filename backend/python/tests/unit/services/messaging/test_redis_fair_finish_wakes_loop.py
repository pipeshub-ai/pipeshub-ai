"""The Redis Streams fair loop wakes when a delivery finishes, instead of waiting a fixed time.

Buffered work that waits for capacity (a connector's slot share, the dispatch budget, buffer room) can only move once
a dispatched task ends. The read phase used to wait for that in a fixed XREADGROUP block (``_BUSY_BLOCK_MS``, about
100 ms on a real Redis) or a fixed ``asyncio.sleep(0.5)`` under backpressure, and a finish ended neither.

The first tests run the real consume loop and worker thread on fakeredis (reads go out without BLOCK, and an empty
read with a block waits it out, as in ``test_connector_off_at_read.py``). "Only a finish can make progress" means
``_BUSY_BLOCK_MS`` raised to a minute; the ceilings are seconds against the ~0.1-1 s the loop needs. The rest drive
the read and dispatch phases directly against a mocked Redis.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from concurrent.futures import Future
from logging import getLogger
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("fakeredis.aioredis")

from app.services.messaging.config import RedisStreamsConfig
from app.services.messaging.consumer_concurrency import DispatchBudget
from app.services.messaging.redis_streams import indexing_consumer as redis_module
from app.services.messaging.redis_streams.indexing_consumer import (
    IndexingRedisStreamsConsumer,
)
from tests.unit.services.messaging.test_connector_off_at_read import (
    Handler,
    _envelope,
    _fair,
    _NonBlockingReadProvider,
    _until,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from tests.support.fake_cluster_redis import FakeClusterRedis

TOPIC = "record-events"
GROUP = "busy-block-group"
MINUTE_MS = 60_000


class _RecordingProvider(_NonBlockingReadProvider):
    """Records the BLOCK argument of every XREADGROUP the consumer sends (None = no BLOCK)."""

    def __init__(self) -> None:
        super().__init__(is_cluster=False)
        self.reads: list[int | None] = []

    def create_client(self, options=None) -> FakeClusterRedis:
        client = super().create_client(options)
        read = client.xreadgroup

        async def xreadgroup(*args, block=None, **kwargs):  # noqa: ANN202
            self.reads.append(block)
            return await read(*args, block=block, **kwargs)

        client.xreadgroup = xreadgroup
        return client


class Harness:
    def __init__(self) -> None:
        self.provider = _RecordingProvider()
        self.consumer: IndexingRedisStreamsConsumer | None = None

    @property
    def reads(self) -> list[int | None]:
        return self.provider.reads

    async def produce(self, *envelopes: dict) -> None:
        pipe = self.provider.get_client().pipeline(transaction=False)
        for envelope in envelopes:
            pipe.xadd(TOPIC, {"value": json.dumps(envelope)})
        await pipe.execute()

    def build(self, *, block_ms: int = 20) -> IndexingRedisStreamsConsumer:
        self.consumer = IndexingRedisStreamsConsumer(
            getLogger("test.busy_block"),
            RedisStreamsConfig(topics=[TOPIC], group_id=GROUP, client_id="indexing-1",
                               batch_size=100, block_ms=block_ms, claim_min_idle_ms=60_000),
            fair_scheduler_config=_fair(),
            provider=self.provider,
        )
        return self.consumer

    async def stop(self) -> None:
        if self.consumer is not None:
            await self.consumer.stop()


@pytest.fixture
async def harness() -> AsyncGenerator[Harness, None]:
    h = Harness()
    yield h
    await h.stop()


def _records(connector: str, n: int) -> list[dict]:
    return [_envelope(f"{connector}-{i}", connector) for i in range(n)]


class _SetterRecordingEvent(asyncio.Event):
    """An Event that records the thread every set() runs on."""

    def __init__(self) -> None:
        super().__init__()
        self.setter_threads: set[int] = set()

    def set(self) -> None:
        self.setter_threads.add(threading.get_ident())
        super().set()


class TestAFinishWakesTheLoop:
    async def _two_connectors_on_their_slot_share(self, harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
        """Two connectors share two index slots, so each has one record in flight. Its next record waits in the
        buffer for that finish; with the busy block a minute long, only the finish can start it."""
        monkeypatch.setenv("MAX_CONCURRENT_INDEXING", "2")
        monkeypatch.setattr(redis_module, "_BUSY_BLOCK_MS", MINUTE_MS)
        await harness.produce(*_records("conn-a", 10), *_records("conn-b", 10))
        handler = Handler()

        consumer = harness.build()
        with monkeypatch.context() as m:
            m.setattr(asyncio, "Event", _SetterRecordingEvent)  # the event start() makes records its setters
            await consumer.start(handler)

        # 25 s, not less: the file's first test also absorbs the worker thread's cold start on a loaded machine.
        await _until(lambda: len(handler.seen) == 20, timeout=25)
        assert sorted(handler.seen) == sorted(f"conn-{c}-{i}" for c in "ab" for i in range(10))
        assert consumer._finished.setter_threads == {threading.get_ident()}, "the finish must be set on the main loop"

    async def test_connectors_capped_by_their_slot_share_drain_at_their_handlers_pace(
        self, harness: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await self._two_connectors_on_their_slot_share(harness, monkeypatch)

    async def test_the_finish_reaches_the_main_loop_thread_safely(
        self, harness: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """In debug mode asyncio refuses a call_soon from another thread: a finish that set the event straight
        from the worker thread would raise there and never wake the loop."""
        asyncio.get_running_loop().set_debug(True)
        await self._two_connectors_on_their_slot_share(harness, monkeypatch)

    async def test_a_finish_releases_a_read_held_by_the_dispatch_budget(
        self, harness: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One index slot and room for one queued task: each record waits for the one before it. The read phase
        held by the budget used to sleep 0.5 s whatever finished (20 records: ~10 s)."""
        monkeypatch.setenv("MAX_CONCURRENT_INDEXING", "1")
        monkeypatch.setenv("MAX_PENDING_INDEXING_TASKS", "1")
        await harness.produce(*_records("conn-a", 20))
        handler = Handler()

        await harness.build().start(handler)
        await _until(lambda: handler.seen, timeout=15)
        started = time.monotonic()  # from the first record, so the worker's cold start is not counted
        await _until(lambda: len(handler.seen) == 20, timeout=15)

        # wall-clock-ok: 20 records on a woken loop take well under a second; the regression polls each one
        assert time.monotonic() - started < 5.0

    async def test_finishes_do_not_count_as_idle_polls(
        self, harness: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Three empty polls in a row mean idle and run the pending drain (XAUTOCLAIM and XPENDING per stream). A
        read that came back empty because the loop was waiting for a finish is not idle when a finish woke it."""
        monkeypatch.setenv("MAX_CONCURRENT_INDEXING", "2")
        monkeypatch.setattr(redis_module, "_BUSY_BLOCK_MS", MINUTE_MS)
        await harness.produce(*_records("conn-a", 20), *_records("conn-b", 20))
        consumer = harness.build()
        drain = AsyncMock(return_value=False)
        consumer._drain_pending = drain  # type: ignore[method-assign]
        handler = Handler()

        await consumer.start(handler)
        await _until(lambda: len(handler.seen) == 40, timeout=10)

        # One at startup; the rest only if the loop sat idle for three polls, which it never did while working.
        assert drain.await_count <= 2, f"{drain.await_count} pending drains while 40 records were worked"


class TestTheWaitStaysBounded:
    async def test_no_read_ever_asks_redis_to_block_forever(
        self, harness: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """XREADGROUP BLOCK 0 blocks until data arrives: a read that must not block goes out without BLOCK."""
        monkeypatch.setenv("MAX_CONCURRENT_INDEXING", "2")
        await harness.produce(*_records("conn-a", 10), *_records("conn-b", 10))
        handler = Handler()
        await harness.build().start(handler)
        await _until(lambda: len(handler.seen) == 20, timeout=10)

        assert 0 not in harness.reads

    async def test_a_slow_delivery_does_not_turn_the_wait_into_a_spin(
        self, harness: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Both connectors' first records are held: buffered work waits behind them and nothing finishes. The
        loop looks again at most once per busy block (50 ms), not in a tight loop; and those waits that time
        out are idle polls, so the pending drain (the only XAUTOCLAIM of a dead replica's entries) still runs."""
        monkeypatch.setenv("MAX_CONCURRENT_INDEXING", "2")
        await harness.produce(*_records("conn-a", 5), *_records("conn-b", 5))
        consumer = harness.build()
        drain = AsyncMock(return_value=False)
        consumer._drain_pending = drain  # type: ignore[method-assign]
        handler = Handler(hold={"conn-a-0", "conn-b-0"})
        await consumer.start(handler)
        await _until(lambda: len(handler.seen) == 2, timeout=10)

        started, before, drains_before = time.monotonic(), len(harness.reads), drain.await_count
        # At least a second, so a spin would show, and until a drain ran: a loaded machine only slows the loop.
        await _until(lambda: time.monotonic() - started >= 1.0 and drain.await_count > drains_before, timeout=15)
        elapsed, reads = time.monotonic() - started, len(harness.reads) - before
        handler.release.set()
        await _until(lambda: len(handler.seen) == 10, timeout=10)

        # One look per 50 ms busy block is 20 a second; a spin is hundreds.
        assert reads <= 30 * elapsed, f"{reads} reads in {elapsed:.1f} s with nothing finishing"

    async def test_an_idle_consumer_still_waits_in_one_long_block_and_wakes_on_a_record(
        self, harness: Harness
    ) -> None:
        handler = Handler()
        await harness.build(block_ms=500).start(handler)
        await asyncio.sleep(0.3)

        before = len(harness.reads)
        await asyncio.sleep(1.0)
        idle_reads = len(harness.reads) - before
        await harness.produce(_envelope("late-0", "conn-a"))
        await _until(lambda: handler.seen == ["late-0"], timeout=2)

        assert idle_reads <= 3
        assert None not in harness.reads

    async def test_buffered_work_with_nothing_in_flight_keeps_the_short_block(self, harness: Harness) -> None:
        """A record waiting out its retry time, nothing in flight: no finish can come, so the read keeps
        blocking for the busy block (which also ends on new data) instead of waiting for a finish."""
        await harness.produce(_envelope("later-0", "conn-a", _retry_not_before=time.time() + 3600))
        consumer = harness.build()
        await consumer.start(Handler())
        await _until(lambda: consumer._scheduler.pending_count == 1, timeout=10)

        await _until(lambda: harness.reads.count(redis_module._BUSY_BLOCK_MS) >= 5, timeout=10)
        assert None not in harness.reads


# ------------------------------------------------------------------ read and dispatch phases, mocked Redis

LANES = [TOPIC, *(f"{TOPIC}.{i}" for i in range(8))]
UNIT_LOGGER = "test.busy_block.unit"


def _unit_consumer(max_buffered: int = 20, group: str = "g") -> IndexingRedisStreamsConsumer:
    consumer = IndexingRedisStreamsConsumer(
        logging.getLogger(UNIT_LOGGER),
        RedisStreamsConfig(host="localhost", port=6379, batch_size=10, block_ms=100, client_id="unit",
                           group_id=group, topics=LANES),
        fair_scheduler_config=_fair(max_buffered_messages=max_buffered, max_per_entity_messages=max_buffered),
    )
    consumer._pending_message_is_owned = AsyncMock(return_value=True)
    redis = AsyncMock()
    redis.xreadgroup = AsyncMock(return_value=[])
    redis.xpending = AsyncMock(return_value={"pending": 0})
    redis.xautoclaim = AsyncMock(return_value=["0-0", [], []])
    consumer.redis = redis
    consumer.running = True
    return consumer


async def _buffer(consumer: IndexingRedisStreamsConsumer, n: int, **payload: object) -> None:
    parsed = await consumer._parse_message(
        "0-0", {"value": json.dumps(_envelope("r", "conn-a", **payload))}, LANES[1]
    )
    for i in range(n):
        assert consumer._IndexingRedisStreamsConsumer__try_enqueue(LANES[1], f"{i}-0", {}, parsed)


async def _read_phase(consumer: IndexingRedisStreamsConsumer) -> None:
    await consumer._IndexingRedisStreamsConsumer__read_phase()


def _held_by_budget(consumer: IndexingRedisStreamsConsumer, blocked: dict[str, bool]) -> None:
    real_budget = consumer._dispatch_budget

    def budget() -> DispatchBudget:
        b = real_budget()
        return DispatchBudget(total_ceiling=1, total_waiters=1, tiers=b.tiers) if blocked["now"] else b

    consumer._dispatch_budget = budget  # type: ignore[method-assign]


class TestReadsAcrossSlotGroups:
    async def test_a_read_without_block_goes_to_every_slot_group_without_block(self) -> None:
        """On Redis Cluster the lane streams can span hash slots, one XREADGROUP per group. A read that must not
        block goes to each of them without BLOCK, not with a share of a block."""
        consumer = _unit_consumer()
        consumer._planner.group = lambda names: [names[:4], names[4:]]  # type: ignore[method-assign]

        await consumer._xreadgroup_grouped(dict.fromkeys(LANES, ">"), 1, None)

        calls = consumer.redis.xreadgroup.await_args_list
        assert len(calls) == 2
        assert [c.kwargs["block"] for c in calls] == [None, None]


class TestBackpressureWaitsForAFinishAtMostHalfASecond:
    async def test_with_nothing_finishing_the_wait_lasts_its_bound(self) -> None:
        """Under backpressure with nothing in flight no finish can come: the read phase waits its full 0.5 s bound
        (main's sleep) before looking again, rather than iterating at the busy-block pace."""
        consumer = _unit_consumer(group="g-bound")
        consumer.main_loop = asyncio.get_running_loop()
        _held_by_budget(consumer, {"now": True})

        started = time.monotonic()
        await _read_phase(consumer)

        assert time.monotonic() - started >= 0.4
        consumer.redis.xreadgroup.assert_not_awaited()


class TestNoFinishIsLost:
    async def test_a_finish_after_the_dispatch_phase_looked_ends_the_next_wait_at_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The dispatch phase clears the event before it looks at capacity; a finish after that must end the read
        phase's next wait, however long its bound."""
        monkeypatch.setattr(redis_module, "_BUSY_BLOCK_MS", MINUTE_MS)
        consumer = _unit_consumer()
        consumer.main_loop = asyncio.get_running_loop()
        await _buffer(consumer, 1, _retry_not_before=time.time() + 3600)  # buffered, not dispatchable
        consumer._active_futures.add(Future())  # a task in flight, whose finish is the only way on

        await consumer._IndexingRedisStreamsConsumer__dispatch_phase()
        consumer._finished.set()  # the finish, as on_future_done hands it over
        await asyncio.wait_for(_read_phase(consumer), timeout=5)

        consumer.redis.xreadgroup.assert_awaited_once()
        assert consumer.redis.xreadgroup.await_args.kwargs.get("block") is None


    async def test_a_finish_that_ended_the_last_task_before_the_read_is_not_missed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The last task in flight can end during the read phase's own awaits, and its hand-off to the main loop
        may not have run yet when the read is chosen: nothing is in flight and the event is still clear. The read
        must not block for the busy block with work buffered."""
        monkeypatch.setattr(redis_module, "_BUSY_BLOCK_MS", MINUTE_MS)
        consumer = _unit_consumer()
        consumer.main_loop = asyncio.get_running_loop()
        await _buffer(consumer, 1, _retry_not_before=time.time() + 3600)

        async def read(**_kwargs: object) -> list:
            consumer._finished.set()  # the queued hand-off runs only after the read went out
            return []

        consumer.redis.xreadgroup.side_effect = read
        await consumer._IndexingRedisStreamsConsumer__dispatch_phase()
        with consumer._futures_lock:  # the worker side of on_future_done: the future has left, the finish counted
            consumer._finishes += 1
        await asyncio.wait_for(_read_phase(consumer), timeout=5)

        assert consumer.redis.xreadgroup.await_args.kwargs.get("block") is None


    async def test_a_finish_the_dispatch_phase_already_saw_does_not_keep_reads_unblocked(self) -> None:
        """Once the dispatch phase has looked at the capacity a finish freed, that finish is spent: with nothing in
        flight and only a retry not yet due buffered, the read goes back to the busy block."""
        consumer = _unit_consumer()
        consumer.main_loop = asyncio.get_running_loop()
        await _buffer(consumer, 1, _retry_not_before=time.time() + 3600)
        with consumer._futures_lock:  # an earlier delivery ended
            consumer._finishes += 1

        await consumer._IndexingRedisStreamsConsumer__dispatch_phase()
        await _read_phase(consumer)

        assert consumer.redis.xreadgroup.await_args.kwargs["block"] == redis_module._BUSY_BLOCK_MS


def _streams_read(consumer: IndexingRedisStreamsConsumer) -> list[list[str]]:
    return [list(c.kwargs["streams"]) for c in consumer.redis.xreadgroup.await_args_list]


class TestAReadNeverClaimsMoreThanTheBufferHolds:
    async def test_with_less_room_than_lanes_a_read_takes_as_many_lanes_as_there_are_places(self) -> None:
        """COUNT is per stream: one entry from each of 9 lanes would not fit 8 places, and what did not fit would
        sit in this consumer's pending list unbuffered. Retries not yet due hold the other 12 places, so this is
        the only way a ready record on another lane gets read before they come due."""
        consumer = _unit_consumer(max_buffered=20)
        consumer.main_loop = asyncio.get_running_loop()
        await _buffer(consumer, 12, _retry_not_before=time.time() + 300)

        await _read_phase(consumer)

        [streams] = _streams_read(consumer)
        assert len(streams) == 8
        assert consumer.redis.xreadgroup.await_args.kwargs["count"] == 1

    async def test_a_read_after_reclaim_takes_only_the_room_reclaim_left(self) -> None:
        """Reclaim runs on the read pass, after the full-buffer check: the read sizes itself to what it left."""
        consumer = _unit_consumer(max_buffered=20)
        consumer.main_loop = asyncio.get_running_loop()

        async def reclaim() -> None:
            await _buffer(consumer, 16)

        consumer._IndexingRedisStreamsConsumer__reclaim_while_reading = reclaim  # type: ignore[attr-defined]

        await _read_phase(consumer)

        [streams] = _streams_read(consumer)
        assert len(streams) == 4

    async def test_short_reads_take_their_turn_on_every_lane(self) -> None:
        consumer = _unit_consumer(max_buffered=20)
        consumer.main_loop = asyncio.get_running_loop()
        consumer._drain_pending = AsyncMock(return_value=False)  # type: ignore[method-assign]
        await _buffer(consumer, 17)

        for _ in range(3):
            await _read_phase(consumer)

        reads = _streams_read(consumer)
        assert [len(r) for r in reads] == [3, 3, 3]
        assert sorted(sum(reads, [])) == sorted(LANES), "every lane read once in three reads of three"

    async def test_short_reads_skip_a_parked_lane_and_still_reach_every_lane_when_it_comes_and_goes(self) -> None:
        """A lane holding parked entries is skipped while another lane produces, so the set of readable lanes
        changes between reads. Short reads must keep skipping the parked lane, and the rotation must not settle
        into a cycle that never reaches a lane (with a cursor kept in the readable list's own positions, every
        lane but the fifth was read in a loop)."""
        consumer = _unit_consumer(max_buffered=20)
        consumer.main_loop = asyncio.get_running_loop()
        consumer._drain_pending = AsyncMock(return_value=False)  # type: ignore[method-assign]
        consumer._IndexingRedisStreamsConsumer__buffer_room = lambda: 4  # type: ignore[attr-defined]
        consumer._IndexingRedisStreamsConsumer__drain_deferred = AsyncMock()  # type: ignore[attr-defined]
        parked = LANES[0]
        parsed = await consumer._parse_message("0-0", {"value": json.dumps(_envelope("p", "conn-p"))}, parked)
        parked_entry = (parked, "9-0", {}, parsed, consumer.key_extractor.extract(parsed), time.monotonic())

        for i in range(8):
            consumer._deferred_entries.clear()
            if i % 2 == 1:
                consumer._deferred_entries.append(parked_entry)
            consumer._lanes_with_data = {LANES[5]}  # another lane is producing
            await _read_phase(consumer)

        consumer._read_offset = 0  # the parked lane is next in turn
        consumer._deferred_entries.append(parked_entry)
        consumer._lanes_with_data = {LANES[5]}
        await _read_phase(consumer)

        reads = _streams_read(consumer)
        assert all(len(r) == 4 for r in reads)
        assert all(parked not in r for r in [*reads[1:8:2], reads[8]]), "a parked lane is skipped while another lane produces"
        assert set(sum(reads[:8], [])) == set(LANES), "every lane read within 8 short reads of 4"

    async def test_an_idle_short_read_blocks_only_for_the_busy_block(self) -> None:
        """A buffer smaller than the lanes reads some lanes at a time; the others may hold the work. Blocking the
        full idle time on each empty one would hold a backlog on the last lane behind every lane before it."""
        consumer = _unit_consumer(max_buffered=1)
        consumer.main_loop = asyncio.get_running_loop()

        await _read_phase(consumer)

        assert consumer.redis.xreadgroup.await_args.kwargs["block"] == redis_module._BUSY_BLOCK_MS

    async def test_an_idle_read_of_every_lane_keeps_the_full_block(self) -> None:
        consumer = _unit_consumer(max_buffered=20)
        consumer.main_loop = asyncio.get_running_loop()

        await _read_phase(consumer)

        assert consumer.redis.xreadgroup.await_args.kwargs["block"] == consumer.config.block_ms

    async def test_a_read_takes_every_lane_once_every_lane_has_room(self) -> None:
        consumer = _unit_consumer(max_buffered=20)
        consumer.main_loop = asyncio.get_running_loop()
        await _buffer(consumer, 11)

        await _read_phase(consumer)

        assert _streams_read(consumer) == [LANES]
        assert consumer.redis.xreadgroup.await_args.kwargs["count"] == 1

    async def test_a_buffer_smaller_than_the_lane_count_reads_as_many_lanes_as_it_holds(self) -> None:
        consumer = _unit_consumer(max_buffered=5)
        consumer.main_loop = asyncio.get_running_loop()

        await _read_phase(consumer)

        [streams] = _streams_read(consumer)
        assert len(streams) == 5
        assert consumer.redis.xreadgroup.await_args.kwargs["count"] == 1
