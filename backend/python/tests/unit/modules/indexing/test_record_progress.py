"""The stall timer and the rule that decides whether a cut attempt is counted.

The consumer's real ``asyncio.timeout`` is used throughout, with limits of a fraction of a second, so
what is tested is the deadline that actually fires. Each test names what it would let through if the
code under test were wrong.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.modules.indexing import record_progress as rp
from app.modules.indexing.embed_resume import (
    EMBED_DELIVERY_ID,
    EMBED_DONE,
    EMBED_FIELDS,
    EMBED_SECONDS,
    EMBED_UNCOUNTED,
    cleared_fields,
)
from app.modules.indexing.record_progress import (
    RecordProgress,
    StallHistory,
    carried_counters,
)

FLOOR = 0.3


def _progress(*, ceiling: float = 3600.0, history: StallHistory | None = None, **kw: Any) -> RecordProgress:  # noqa: ANN401
    return RecordProgress(
        floor_seconds=FLOOR, ceiling_seconds=1.0, record_ceiling_seconds=ceiling,
        history=history or StallHistory(), **kw,
    )


class Writes:
    """The hand-back write the event layer would make, recorded."""

    def __init__(self, *, applies: bool = True, fails: bool = False) -> None:
        self.calls: list[tuple[float, int]] = []
        self.applies, self.fails = applies, fails

    async def __call__(self, seconds: float, uncounted: int) -> bool:
        if self.fails:
            raise RuntimeError("graph down")
        self.calls.append((seconds, uncounted))
        return self.applies


def _bind(p: RecordProgress, writes: Writes, *, prior_seconds: float | None = 0.0, prior_uncounted: int = 0) -> None:
    p.bind(record_id="r1", delivery_id="d1", prior_seconds=prior_seconds, prior_uncounted=prior_uncounted, commit=writes)


async def _run(
    p: RecordProgress,
    work: Any,  # noqa: ANN401
    *,
    cap: float = 5.0,
    plan: bool = True,
    restarted: bool = False,
) -> bool:
    """Run ``work(p)`` under a budget of ``cap`` seconds; True when the budget ended it."""
    try:
        async with asyncio.timeout(cap) as budget:
            p.attach(budget)
            if plan:
                p.plan(100, 0, restarted=restarted)
            await work(p)
    except TimeoutError:
        return True
    return False


async def _beat_once_then_hang(p: RecordProgress) -> None:
    await asyncio.sleep(0.02)
    p.beat(20)
    await asyncio.sleep(30)


class TestTheStallLimit:
    def test_an_outlier_among_fast_batches_does_not_move_it(self) -> None:
        # Kills: a limit taken from the slowest recent batch.
        h = StallHistory()
        for _ in range(49):
            h.observe(8.1)
        h.observe(97.7)
        assert h.stall_seconds(360, 1200) == 360

    def test_too_few_samples_use_the_floor(self) -> None:
        # Kills: believing three batches over the floor.
        h = StallHistory()
        for _ in range(9):
            h.observe(116.0)
        assert h.stall_seconds(360, 1200) == 360

    def test_a_contended_host_raises_it_to_four_times_the_ninetieth_percentile(self) -> None:
        h = StallHistory()
        for value in [45.0] * 40 + [114.0] * 10:
            h.observe(value)
        assert h.stall_seconds(360, 1200) == pytest.approx(456.0)

    def test_it_follows_a_step_from_idle_to_contended_and_back(self) -> None:
        # Kills: a limit frozen at the first value; a window that never forgets.
        h = StallHistory()
        for _ in range(50):
            h.observe(8.0)
        assert h.stall_seconds(360, 1200) == 360
        for _ in range(50):
            h.observe(150.0)
        assert h.stall_seconds(360, 1200) == 600
        for _ in range(50):
            h.observe(8.0)
        assert h.stall_seconds(360, 1200) == 360

    def test_it_never_exceeds_the_ceiling_or_goes_below_the_floor(self) -> None:
        h = StallHistory()
        for _ in range(50):
            h.observe(400.0)
        assert h.stall_seconds(360, 1200) == 1200
        assert h.stall_seconds(2000, 3000) == 2000

    def test_nonsense_intervals_are_ignored(self) -> None:
        h = StallHistory()
        for value in (float("nan"), float("inf"), -1.0):
            h.observe(value)
        assert h.stall_seconds(360, 1200) == 360


@pytest.mark.asyncio
class TestTheDeadline:
    async def test_a_record_that_stops_finishing_batches_is_cut_at_the_stall_limit_not_the_cap(self) -> None:
        # Kills: no stall timer (the cap alone ends it).
        p = _progress()
        started = asyncio.get_running_loop().time()
        assert await _run(p, _beat_once_then_hang, cap=10.0)
        assert asyncio.get_running_loop().time() - started < 1.5

    async def test_a_record_that_keeps_finishing_batches_is_not_cut_by_the_stall_limit(self) -> None:
        # Kills: a stall timer that is not re-armed by a batch.
        p = _progress()

        async def steady(pp: RecordProgress) -> None:
            for _ in range(8):
                await asyncio.sleep(FLOOR * 0.6)
                pp.beat(20)

        assert not await _run(p, steady, cap=10.0)
        assert p.new_units == 8

    async def test_the_cap_still_ends_a_record_that_keeps_making_progress(self) -> None:
        # Kills: a stall deadline that replaces the cap instead of being bounded by it.
        p = _progress()

        async def forever(pp: RecordProgress) -> None:
            while True:
                await asyncio.sleep(0.05)
                pp.beat(1)

        started = asyncio.get_running_loop().time()
        assert await _run(p, forever, cap=0.6)
        assert asyncio.get_running_loop().time() - started < 1.0

    async def test_the_cap_is_the_live_budget_which_already_carries_an_admission_wait(self) -> None:
        # Kills: a deadline computed from the wall start plus the flat limit. A record that waited for
        # a slot has a budget later than that, and its first batch must not be cut by the old one.
        p = _progress()
        started = asyncio.get_running_loop().time()

        async def work(pp: RecordProgress) -> None:
            await asyncio.sleep(0.05)
            pp.beat(20)
            await asyncio.sleep(0.05)

        async with asyncio.timeout(0.2) as budget:
            p.attach(budget)
            budget.reschedule(started + 3.0)  # what parse_admission_wait does after a long wait
            p.plan(100, 0)
            await work(p)
        assert p._cap == pytest.approx(started + 3.0)

    async def test_a_provider_outage_does_not_trip_the_stall_timer_but_the_cap_still_ends_it(self) -> None:
        # Kills: a pause that does nothing; a pause that lifts the cap.
        p = _progress()

        async def outage(pp: RecordProgress) -> None:
            await asyncio.sleep(0.02)
            pp.beat(20)
            pp.pause()
            await asyncio.sleep(FLOOR * 2)  # twice the stall limit, waiting out a retry
            pp.resume()
            pp.beat(20)

        assert not await _run(p, outage, cap=5.0)

        q = _progress()

        async def never_recovers(pp: RecordProgress) -> None:
            pp.pause()
            await asyncio.sleep(30)

        started = asyncio.get_running_loop().time()
        assert await _run(q, never_recovers, cap=0.8)
        assert 0.7 < asyncio.get_running_loop().time() - started < 1.3

    async def test_a_call_in_flight_is_not_a_stall_but_the_cap_still_ends_it(self) -> None:
        # Kills: a stall timer that runs during an embedding call, and a call that lifts the cap.
        p = _progress()

        async def slow_call(pp: RecordProgress) -> None:
            pp.call_start()
            await asyncio.sleep(FLOOR * 3)
            pp.call_end()
            pp.beat(1)

        assert not await _run(p, slow_call, cap=5.0)

        q = _progress()

        async def never_returns(pp: RecordProgress) -> None:
            pp.call_start()
            await asyncio.sleep(30)

        started = asyncio.get_running_loop().time()
        assert await _run(q, never_returns, cap=0.8)
        assert 0.7 < asyncio.get_running_loop().time() - started < 1.3

    async def test_the_stall_timer_counts_from_the_end_of_the_call(self) -> None:
        # Kills: a call that ends with the clock of the last batch, which would cut at once.
        p = _progress()

        async def work(pp: RecordProgress) -> None:
            pp.call_start()
            await asyncio.sleep(FLOOR * 3)
            pp.call_end()
            await asyncio.sleep(FLOOR * 0.5)
            pp.beat(1)

        assert not await _run(p, work, cap=5.0)

        q = _progress()

        async def hangs_after_call(pp: RecordProgress) -> None:
            pp.call_start()
            pp.call_end()
            await asyncio.sleep(30)

        assert await _run(q, hangs_after_call, cap=5.0)
        assert q.new_units == 0

    async def test_a_retry_starting_starts_the_stall_timer_over(self) -> None:
        # Kills: resume() that keeps the clock of the batch before the outage.
        p = _progress()

        async def work(pp: RecordProgress) -> None:
            await asyncio.sleep(FLOOR * 0.8)
            pp.pause()
            await asyncio.sleep(FLOOR * 0.8)
            pp.resume()
            await asyncio.sleep(FLOOR * 0.8)  # 2.4 x the limit since the last batch, 0.8 since the retry
            pp.beat(1)

        assert not await _run(p, work, cap=10.0)

    async def test_the_pause_is_bounded_per_attempt(self) -> None:
        # Kills: an unbounded pause that hides a hung downstream for ever.
        p = _progress(pause_limit_seconds=0.3)

        async def work(pp: RecordProgress) -> None:
            pp.pause()
            await asyncio.sleep(30)

        started = asyncio.get_running_loop().time()
        assert await _run(p, work, cap=10.0)
        assert asyncio.get_running_loop().time() - started < 1.4  # 0.3 allowance + 0.3 limit, not the cap

    async def test_the_pause_allowance_is_shared_by_every_pause_of_the_attempt(self) -> None:
        # Kills: an allowance that starts over at each pause, so repeated outages are never bounded.
        p = _progress(pause_limit_seconds=0.6)
        second: list[float] = []

        async def work(pp: RecordProgress) -> None:
            pp.pause()
            await asyncio.sleep(0.5)
            pp.resume()
            pp.pause()
            second.append(asyncio.get_running_loop().time())
            await asyncio.sleep(30)

        assert await _run(p, work, cap=10.0)
        # 0.1 s of allowance left plus the 0.3 s stall limit, not a fresh 0.6 + 0.3.
        assert asyncio.get_running_loop().time() - second[0] < 0.7

    async def test_the_stall_timer_is_off_once_the_embedding_is_done(self) -> None:
        # Kills: a stall limit still armed through the verification and the writes after the
        # embedding, which cuts a slow verify at the stall limit and counts it.
        p = _progress()

        async def work(pp: RecordProgress) -> None:
            await asyncio.sleep(0.02)
            pp.beat(20)
            pp.finish()
            await asyncio.sleep(0.8)  # well past the 0.3 s stall limit, inside the cap

        assert not await _run(p, work, cap=2.0)

    async def test_a_beat_after_the_embedding_is_done_does_not_arm_the_stall_timer_again(self) -> None:
        p = _progress()

        async def work(pp: RecordProgress) -> None:
            pp.finish()
            pp.beat(1)
            await asyncio.sleep(0.8)

        assert not await _run(p, work, cap=2.0)

    async def test_paused_time_counts_in_the_seconds_of_the_attempt(self) -> None:
        # Kills: embed seconds that leave the pause out, which would let the 6 h ceiling be dodged.
        p = _progress()

        async def work(pp: RecordProgress) -> None:
            pp.pause()
            await asyncio.sleep(0.25)
            pp.resume()
            pp.beat(1)

        await _run(p, work)
        assert p.attempt_seconds() >= 0.25

    async def test_a_beat_before_the_embedding_is_planned_does_nothing(self) -> None:
        # Kills: counting batches of a run that has no marker (code, SQL, images) as resumable progress.
        p = _progress()

        async def work(pp: RecordProgress) -> None:
            pp.beat(20)

        await _run(p, work, plan=False)
        assert p.new_units == 0 and not p.planned


async def _cut(p: RecordProgress, **kw: Any) -> None:  # noqa: ANN401
    assert await _run(p, _beat_once_then_hang, **kw)


@pytest.mark.asyncio
class TestWhenACutIsNotCounted:
    async def test_a_stall_after_progress_is_handed_back_and_the_node_is_told(self) -> None:
        writes = Writes()
        p = _progress()
        _bind(p, writes, prior_seconds=100.0, prior_uncounted=2)
        await _cut(p)
        assert await p.settle_cut(lease_intact=True, running=True) is None
        (seconds, uncounted), = writes.calls
        assert uncounted == 3
        assert seconds >= 100.0 + 0.02

    async def test_a_cut_with_no_batch_is_counted(self) -> None:
        # Kills: every cut uncounted (a stuck record would never dead-letter).
        writes = Writes()
        p = _progress()
        _bind(p, writes)

        async def hang(pp: RecordProgress) -> None:
            await asyncio.sleep(30)

        assert await _run(p, hang)
        assert await p.settle_cut(lease_intact=True, running=True) == rp.COUNTED_NO_PROGRESS
        assert writes.calls == []

    async def test_a_cut_before_the_embedding_was_planned_is_counted(self) -> None:
        writes = Writes()
        p = _progress()
        _bind(p, writes)

        async def hang(pp: RecordProgress) -> None:
            await asyncio.sleep(30)

        assert await _run(p, hang, cap=0.2, plan=False)
        assert await p.settle_cut(lease_intact=True, running=True) == rp.COUNTED_NO_PROGRESS

    async def test_a_lost_lease_is_never_a_requeue(self) -> None:
        # Kills: a decision that does not look at the lease.
        writes = Writes()
        p = _progress()
        _bind(p, writes)
        await _cut(p)
        assert await p.settle_cut(lease_intact=False, running=True) == rp.COUNTED_LEASE
        assert writes.calls == []

    async def test_a_stopping_consumer_never_requeues(self) -> None:
        # Kills: a decision that does not look at the consumer.
        writes = Writes()
        p = _progress()
        _bind(p, writes)
        await _cut(p)
        assert await p.settle_cut(lease_intact=True, running=False) == rp.COUNTED_STOPPING
        assert writes.calls == []

    async def test_a_timeout_that_was_not_the_attempts_own_is_counted(self) -> None:
        # Kills: converting any TimeoutError (a client's, the status write's) into a requeue.
        writes = Writes()
        p = _progress()
        _bind(p, writes)

        async with asyncio.timeout(30) as budget:
            p.attach(budget)
            p.plan(100, 0)
            p.beat(20)
            await asyncio.sleep(0)
        assert await p.settle_cut(lease_intact=True, running=True) == rp.COUNTED_NOT_BUDGET
        assert writes.calls == []

    async def test_a_restart_that_made_progress_is_still_counted(self) -> None:
        # Kills: a digest that differs on every attempt looking like endless progress.
        writes = Writes()
        p = _progress()
        _bind(p, writes)
        await _cut(p, restarted=True)
        assert await p.settle_cut(lease_intact=True, running=True) == rp.COUNTED_RESTART
        assert writes.calls == []

    async def test_the_ceiling_uses_the_value_on_the_node_plus_this_attempt(self) -> None:
        # Kills: a ceiling checked on this attempt alone, which a restart or a recovery would reset.
        writes = Writes()
        p = _progress(ceiling=1000.0)
        _bind(p, writes, prior_seconds=999.9)
        await _cut(p)
        assert await p.settle_cut(lease_intact=True, running=True) == rp.COUNTED_CEILING
        assert writes.calls == []

    async def test_the_ceiling_is_the_same_for_a_new_process_that_only_has_the_node(self) -> None:
        # The state lives on the node: a fresh object built from the node's value decides the same.
        node = {EMBED_DELIVERY_ID: "d1", EMBED_SECONDS: 21_599.9, EMBED_UNCOUNTED: 40}
        seconds, uncounted = carried_counters(node, "d1")
        writes = Writes()
        p = _progress(ceiling=21_600.0)
        _bind(p, writes, prior_seconds=seconds, prior_uncounted=uncounted or 0)
        await _cut(p)
        assert await p.settle_cut(lease_intact=True, running=True) == rp.COUNTED_CEILING

    async def test_an_unknown_clock_is_counted(self) -> None:
        # Kills: a missing value read as zero, which would hand out a new 6 h.
        writes = Writes()
        p = _progress()
        _bind(p, writes, prior_seconds=None)
        await _cut(p)
        assert await p.settle_cut(lease_intact=True, running=True) == rp.COUNTED_UNKNOWN_CLOCK

    async def test_a_hand_back_that_could_not_be_written_is_counted(self) -> None:
        # Kills: deciding before the write. A crash between the cut and the write is the same case.
        p = _progress()
        _bind(p, Writes(fails=True))
        await _cut(p)
        assert await p.settle_cut(lease_intact=True, running=True) == rp.COUNTED_NOT_WRITTEN

    async def test_a_record_that_no_longer_belongs_to_this_delivery_is_counted(self) -> None:
        # Kills: an unconditional status write over another owner's record.
        p = _progress()
        _bind(p, Writes(applies=False))
        await _cut(p)
        assert await p.settle_cut(lease_intact=True, running=True) == rp.COUNTED_NOT_OWNER

    async def test_a_progress_that_was_never_bound_to_a_record_is_counted(self) -> None:
        p = _progress()
        await _cut(p)
        assert await p.settle_cut(lease_intact=True, running=True) == rp.COUNTED_UNKNOWN_CLOCK


class TestTheCountersOfAChain:
    def test_a_new_event_starts_a_new_clock(self) -> None:
        node = {EMBED_DELIVERY_ID: "old", EMBED_SECONDS: 20_000, EMBED_UNCOUNTED: 9}
        assert carried_counters(node, "new") == (0.0, 0)
        assert carried_counters({}, "new") == (0.0, 0)

    def test_the_same_event_keeps_its_clock(self) -> None:
        node = {EMBED_DELIVERY_ID: "d1", EMBED_SECONDS: 1234.5, EMBED_UNCOUNTED: 4}
        assert carried_counters(node, "d1") == (1234.5, 4)

    @pytest.mark.parametrize("bad", [None, "x", -1, float("nan"), float("inf"), True])
    def test_a_value_that_is_missing_or_not_a_sane_number_is_unknown(self, bad: object) -> None:
        # Kills: unknown read as zero.
        node = {EMBED_DELIVERY_ID: "d1", EMBED_SECONDS: bad, EMBED_UNCOUNTED: bad}
        assert carried_counters(node, "d1") == (None, None)

    def test_the_end_of_a_chain_clears_every_counter(self) -> None:
        # Kills: a stale clock surviving into the next event.
        cleared = cleared_fields()
        assert set(cleared) == set(EMBED_FIELDS)
        assert {EMBED_SECONDS, EMBED_UNCOUNTED, EMBED_DONE} <= set(cleared)
        assert all(v is None for v in cleared.values())


def test_the_shipped_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    # Kills: a default changed by accident; these are the figures the docs and the design state.
    from app.services.messaging.config import messaging_env

    for name in (
        "EMBED_STALL_FLOOR_SECONDS", "EMBED_STALL_CEILING_SECONDS",
        "EMBED_RECORD_CEILING_SECONDS", "EMBED_CUT_REQUEUE_DELAY_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    assert messaging_env.embed_stall_floor_seconds == 360.0
    assert messaging_env.embed_stall_ceiling_seconds == 1200.0
    assert messaging_env.embed_record_ceiling_seconds == 21600.0
    assert messaging_env.embed_cut_requeue_delay_seconds == 5.0
    assert rp.PAUSE_LIMIT_SECONDS == 1800.0
