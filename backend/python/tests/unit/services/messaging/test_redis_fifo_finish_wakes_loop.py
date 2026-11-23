"""The Redis Streams FIFO loop (fair scheduling off) wakes when a delivery finishes, and never reads more entries than
its dispatch budget can start.

It used to wait a fixed ``asyncio.sleep(0.5)`` under backpressure, which a finish could not end (about 125 records/s
on a real Redis). Its read asked every stream for ``min(batch, remaining)`` entries; COUNT is per stream, so on the
record-events lanes a read took up to nine times what fit. The start loop then stopped at the budget, the rest stayed
pending, and recovery started them later, behind newer entries of their stream.

These tests drive the real FIFO branch of ``_consume_loop``. Reads are faked (every stream always has more entries,
unless a test asks for empty ones); the dispatch budget is a counter that started tasks fill; a task finishes through a
main-loop hop that frees its place and sets ``_finished``, as ``on_future_done`` does. Sleeps take no time and waits
are spied, so nothing depends on wall time; a loop that turns without yielding is stopped and reported.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from unittest.mock import AsyncMock, patch

from app.services.messaging import consumer_concurrency as concurrency
from app.services.messaging.config import RedisStreamsConfig
from app.services.messaging.redis_streams import indexing_consumer as redis_module
from app.services.messaging.redis_streams.indexing_consumer import (
    IndexingRedisStreamsConsumer,
)
from app.services.resource_governor.models import ParseTier

BASE = "record-events"
LANES = [BASE, *(f"{BASE}.{i}" for i in range(8))]
BLOCK_MS = 2000
WAIT_FOR_FINISH = "_IndexingRedisStreamsConsumer__wait_for_finish"
# Far more turns than any test needs: past this the loop is spinning (a set event waited on returns without yielding).
MAX_TURNS = 1000
_real_sleep = asyncio.sleep


async def _yield(*_a: object, **_k: object) -> None:
    """A patched sleep that takes no time but still lets callbacks (a finish) run."""
    await _real_sleep(0)


class _Fifo:
    def __init__(
        self, topics: list[str], ceiling: int, *, reads: int, finish_soon: bool = True, empty: bool = False
    ) -> None:
        self.consumer = IndexingRedisStreamsConsumer(
            logging.getLogger("test_redis_fifo_wakes"),
            RedisStreamsConfig(host="localhost", port=6379, block_ms=BLOCK_MS, batch_size=10,
                               client_id="fifo", group_id="g", topics=topics),
            retry_manager=None,
            producer=None,
        )
        assert self.consumer._scheduler is None
        self.ceiling = ceiling
        self.waiting = 0
        self.max_reads = reads
        self.finish_soon = finish_soon
        self.empty = empty
        self.turns = 0
        self.reads: list[tuple[dict[str, str], int, int | None]] = []
        self.read_ids: list[tuple[str, str]] = []
        self.started: list[tuple[str, str]] = []
        self.next_seq = dict.fromkeys(topics, 0)
        self.sleep = AsyncMock(side_effect=_yield)
        self.drain = AsyncMock()
        c = self.consumer
        c.redis = AsyncMock()
        c.redis.xpending = AsyncMock(return_value={"pending": 0})
        c.redis.xautoclaim = AsyncMock(return_value=["0-0", [], []])
        c.running = True

    def budget(self) -> concurrency.DispatchBudget:
        return concurrency.DispatchBudget(
            total_ceiling=self.ceiling, total_waiters=self.waiting,
            tiers={ParseTier.HEAVY: concurrency.TierBudget(waiters=self.waiting, ceiling=self.ceiling)},
        )

    async def read(self, streams: dict[str, str], count: int, block_ms: int | None = None) -> list:
        self.reads.append((dict(streams), count, block_ms))
        if self.empty:
            return []
        out = []
        for stream in streams:
            ids = [f"1-{self.next_seq[stream] + i}" for i in range(count)]
            self.next_seq[stream] += count
            self.read_ids += [(stream, i) for i in ids]
            out.append((stream, [(i, {"value": "{}"}) for i in ids]))
        return out

    def finish(self) -> None:
        self.waiting -= 1
        self.consumer._finished.set()

    async def start_task(self, stream: str, message_id: str, _fields: dict) -> None:
        self.started.append((stream, message_id))
        self.waiting += 1
        if self.finish_soon:
            asyncio.get_running_loop().call_soon(self.finish)

    async def top_of_turn(self) -> None:
        """Every FIFO turn starts here: once the reads are done, or the loop spins, end it as a cancelled task ends."""
        self.turns += 1
        if len(self.reads) >= self.max_reads or self.turns > MAX_TURNS:
            raise asyncio.CancelledError

    async def run(self, *extra: object) -> None:
        c = self.consumer

        async def read(*a: object, **k: object) -> list:  # late-bound: a test may replace self.read
            return await self.read(*a, **k)

        with contextlib.ExitStack() as stack:
            for p in (
                patch.object(c, "_dispatch_budget", side_effect=lambda: self.budget()),
                patch.object(c, "_xreadgroup_grouped", side_effect=read),
                patch.object(c, "_start_processing_task", side_effect=self.start_task),
                patch.object(c, "_IndexingRedisStreamsConsumer__hand_back_fifo_read", AsyncMock(return_value=False)),
                patch.object(c, "_drain_pending", self.drain),
                patch.object(c, "_wait_out_backpressure", side_effect=self.top_of_turn),
                patch.object(redis_module.asyncio, "sleep", self.sleep),
                *extra,
            ):
                stack.enter_context(p)  # type: ignore[arg-type]
            await asyncio.wait_for(c._consume_loop(), timeout=10)
        assert self.turns <= MAX_TURNS, "the loop turned without waiting"


class TestReadRoom:
    async def test_a_read_across_lanes_never_asks_for_more_than_the_budget_can_start(self) -> None:
        fifo = _Fifo(LANES, ceiling=3, reads=1, finish_soon=False)
        await fifo.run()
        streams, count, _block = fifo.reads[0]
        assert len(streams) * count <= 3

    async def test_every_entry_read_is_started_in_stream_order(self) -> None:
        """Room for at most 5 on 9 lanes: every entry read is started, each lane's in order, none left pending."""
        fifo = _Fifo(LANES, ceiling=5, reads=20)
        await fifo.run()
        assert fifo.started == fifo.read_ids
        for lane in LANES:
            seqs = [int(i.split("-")[1]) for s, i in fifo.started if s == lane]
            assert seqs == list(range(len(seqs)))

    async def test_a_budget_that_shrinks_after_the_read_still_starts_every_entry(self) -> None:
        """The ceiling follows the governor's current limits and can drop while a read blocks. What the read took
        is started anyway: an entry left pending would be recovered later, behind newer entries of its lane."""
        fifo = _Fifo(LANES, ceiling=64, reads=1, finish_soon=False)

        async def read_then_shrink(streams: dict[str, str], count: int, block_ms: int | None = None) -> list:
            out = await _Fifo.read(fifo, streams, count, block_ms)
            fifo.ceiling = 1
            return out

        fifo.read = read_then_shrink  # type: ignore[method-assign]
        await fifo.run()
        assert fifo.started == fifo.read_ids
        assert len(fifo.read_ids) == 63

    async def test_short_reads_take_the_lanes_in_turn(self) -> None:
        fifo = _Fifo(LANES, ceiling=1, reads=len(LANES) + 1)
        await fifo.run()
        assert [list(s) for s, _c, _b in fifo.reads] == [[lane] for lane in [*LANES, LANES[0]]]

    async def test_a_short_read_blocks_briefly_and_a_full_one_for_block_ms(self) -> None:
        short = _Fifo(LANES, ceiling=3, reads=1, finish_soon=False)
        await short.run()
        assert short.reads[0][2] == redis_module._BUSY_BLOCK_MS
        full = _Fifo(LANES, ceiling=64, reads=1, finish_soon=False)
        await full.run()
        assert full.reads[0] == (dict.fromkeys(LANES, ">"), 7, BLOCK_MS)  # 9 x 7 <= 64

    async def test_room_for_exactly_one_entry_per_lane_reads_every_lane(self) -> None:
        fifo = _Fifo(LANES, ceiling=len(LANES), reads=1, finish_soon=False)
        await fifo.run()
        assert fifo.reads == [(dict.fromkeys(LANES, ">"), 1, BLOCK_MS)]

    async def test_a_full_read_is_not_a_turn_of_the_short_read_cursor(self) -> None:
        """Room 3, then exactly one place per lane, then 3 again: the full read asks the lanes in topic order and
        leaves the cursor, so the next short read goes on after the first one."""
        fifo = _Fifo(LANES, ceiling=3, reads=3, finish_soon=False)
        room_after = iter([len(LANES), 3])

        async def read_then_set_room(streams: dict[str, str], count: int, block_ms: int | None = None) -> list:
            out = await _Fifo.read(fifo, streams, count, block_ms)
            started_by_this_read = sum(len(entries) for _s, entries in out)
            fifo.ceiling = fifo.waiting + started_by_this_read + next(room_after, 0)
            return out

        fifo.read = read_then_set_room  # type: ignore[method-assign]
        await fifo.run()
        assert [list(s) for s, _c, _b in fifo.reads] == [LANES[:3], LANES, LANES[3:6]]
        assert [b for _s, _c, b in fifo.reads] == [redis_module._BUSY_BLOCK_MS, BLOCK_MS, redis_module._BUSY_BLOCK_MS]

    async def test_one_stream_reads_as_before(self) -> None:
        fifo = _Fifo([BASE], ceiling=64, reads=1, finish_soon=False)
        await fifo.run()
        assert fifo.reads == [({BASE: ">"}, 10, BLOCK_MS)]

    async def test_no_topics_reads_nothing_and_does_not_fail(self) -> None:
        fifo = _Fifo([], ceiling=64, reads=1, finish_soon=False)
        await fifo.run()
        assert fifo.reads == [({}, 10, BLOCK_MS)]


class TestIdleDrain:
    async def test_empty_short_reads_do_not_start_the_idle_drain(self) -> None:
        """A short read looked at only some lanes, so finding nothing there is not idleness. With the budget nearly
        full and the streams empty, counting them ran the full pending drain about every 0.3 s."""
        fifo = _Fifo(LANES, ceiling=3, reads=6, finish_soon=False, empty=True)
        await fifo.run(patch.object(fifo.consumer, WAIT_FOR_FINISH, AsyncMock(return_value=False)))
        assert fifo.drain.await_count == 1  # the startup drain only
        assert all(len(s) < len(LANES) for s, _c, _b in fifo.reads)

    async def test_an_empty_short_read_waits_for_a_finish_before_reading_again(self) -> None:
        """Room comes back only as tasks finish, so polling the lanes again at once finds nothing new. The wait's
        timeout is raised to a minute: the second read can only come from the finish that lands during the wait."""
        fifo = _Fifo(LANES, ceiling=4, reads=2, finish_soon=False, empty=True)
        fifo.waiting = 1
        real_wait = getattr(fifo.consumer, WAIT_FOR_FINISH)
        waits: list[tuple[int, bool]] = []

        async def wait(timeout: float) -> bool:
            if fifo.reads:
                asyncio.get_running_loop().call_soon(fifo.finish)  # a task ends while the loop waits
            woken = await real_wait(timeout)
            waits.append((len(fifo.reads), woken))
            return woken

        await fifo.run(
            patch.object(redis_module, "_BACKPRESSURE_WAIT_SECONDS", 60.0),
            patch.object(fifo.consumer, WAIT_FOR_FINISH, side_effect=wait),
        )
        assert waits[:1] == [(1, True)]  # after the first (empty, short) read, ended by the finish
        assert len(fifo.reads) == 2
        assert all(len(s) < len(LANES) for s, _c, _b in fifo.reads)

    async def test_empty_full_reads_still_start_the_idle_drain(self) -> None:
        fifo = _Fifo(LANES, ceiling=64, reads=7, finish_soon=False, empty=True)
        await fifo.run()
        assert fifo.drain.await_count == 1 + 2  # startup, then after the third and sixth empty reads


class TestWaitForFinish:
    async def test_backpressure_waits_for_a_finish_not_a_fixed_sleep(self) -> None:
        """Blocked on the first look; a finish lands right after it. The wait (timeout raised to a minute) must
        end on that finish, and no fixed 0.5 s sleep is taken."""
        fifo = _Fifo([BASE], ceiling=1, reads=1, finish_soon=False)
        fifo.waiting = 1
        looks = 0

        def budget() -> concurrency.DispatchBudget:
            nonlocal looks
            looks += 1
            b = _Fifo.budget(fifo)
            if looks == 1:
                asyncio.get_running_loop().call_soon(fifo.finish)
            return b

        fifo.budget = budget  # type: ignore[method-assign]
        await fifo.run(patch.object(redis_module, "_BACKPRESSURE_WAIT_SECONDS", 60.0))
        assert [c.args for c in fifo.sleep.await_args_list if c.args == (0.5,)] == []
        assert fifo.started == [(BASE, "1-0")]

    async def test_a_stale_finish_does_not_spin_a_blocked_loop(self) -> None:
        """A finish from before the turn's capacity look must not end that turn's wait: blocked with no finishes,
        every turn waits on a cleared event, and nothing is read."""
        fifo = _Fifo([BASE], ceiling=1, reads=1, finish_soon=False)
        fifo.waiting = 1
        fifo.consumer._finished.set()  # stale: set before the loop started
        event_was_set: list[bool] = []

        async def wait(_timeout: float) -> bool:
            event_was_set.append(fifo.consumer._finished.is_set())
            if len(event_was_set) == 5:
                fifo.consumer.running = False
            return False

        async def sleep(*_a: object, **_k: object) -> None:  # a fixed sleep, recorded as a wait
            await wait(0.5)

        fifo.sleep.side_effect = sleep
        await fifo.run(patch.object(fifo.consumer, WAIT_FOR_FINISH, side_effect=wait))
        assert fifo.sleep.await_count == 0  # waits on the finish event, not a timer
        assert event_was_set == [False] * 5  # cleared before each look
        assert fifo.reads == []
