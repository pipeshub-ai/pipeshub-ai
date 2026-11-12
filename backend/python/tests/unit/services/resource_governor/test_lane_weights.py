"""Weighted leases and the waiter reserve (T60 to T64, and the round-5 fairness items).

Budgets on one directory stand in for processes; each takes its own ``pid`` so the
waiter flag can tell them apart. Timing tests use the real clock.
"""
from __future__ import annotations

import contextlib
import subprocess
import sys
import textwrap
import threading
import time
from typing import TYPE_CHECKING

import pytest

from app.services.resource_governor import lane_ledger
from app.services.resource_governor.lane_ledger import (
    WAITER_COOLDOWN_SECONDS,
    WAITER_FRESH_SECONDS,
    LedgerRecord,
    active_waiter,
)
from app.services.resource_governor.lanes import LaneBudget, LaneLease, LaneWaitTimeout

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from tests.unit.lane_gauge import FakeClock



def real(make_budget: Callable[..., LaneBudget], lanes: int, pid: int) -> LaneBudget:
    return make_budget(lanes, clock=time.monotonic, pid=pid, view_ttl=0.05)


def take_soon(budget: LaneBudget, seconds: float = 5.0) -> LaneLease | None:
    """``try_acquire`` until it succeeds: one call may find the slot mutex busy."""
    end = time.monotonic() + seconds
    while True:
        lease = budget.try_acquire()
        if lease is not None or time.monotonic() >= end:
            return lease
        time.sleep(0.005)


def flag(budget: LaneBudget) -> LedgerRecord:
    budget._ledger.invalidate()
    return budget._ledger.read()


def wait_until(predicate: Callable[[], bool], seconds: float = 5.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_weighted_take_is_all_or_nothing(make_budget: Callable[..., LaneBudget]) -> None:
    """T60: a weight-3 request denied at limit 3 holds nothing, not even a unit 'so far'."""
    holder = real(make_budget, 3, 101)
    waiter = real(make_budget, 3, 102)
    lease = holder.try_acquire()
    assert lease is not None
    seen: set[int] = set()
    stop = threading.Event()

    def sample() -> None:
        while not stop.is_set():
            seen.add(waiter.held_count()[0])
            time.sleep(0.0005)  # a hot loop starves the acquirer of the GIL and the test of its time

    sampler = threading.Thread(target=sample)
    sampler.start()
    try:
        for _ in range(150):
            assert waiter.try_acquire(weight=3) is None
    finally:
        stop.set()
        sampler.join()
    assert seen == {1}, seen
    lease.release()
    big = waiter.try_acquire(weight=3)
    assert big is not None
    assert waiter.held_count()[0] == 3
    big.release()
    assert waiter.held_count()[0] == 0


def test_weighted_holder_is_never_seen_partially(make_budget: Callable[..., LaneBudget]) -> None:
    """A weight-3 holder shows up as 3 held units or none, never 1 or 2, while others race."""
    heavy = real(make_budget, 4, 111)
    light = real(make_budget, 4, 112)
    seen: set[int] = set()
    stop = threading.Event()

    def churn() -> None:
        while not stop.is_set():
            lease = light.try_acquire()
            if lease is not None:
                lease.release()
            time.sleep(0.0005)

    def sample() -> None:
        while not stop.is_set():
            seen.add(heavy.held_count()[0])
            time.sleep(0.001)

    threads = [threading.Thread(target=churn), threading.Thread(target=sample)]
    for t in threads:
        t.start()
    try:
        admitted = 0
        end = time.monotonic() + 1.5
        while time.monotonic() < end:
            lease = heavy.try_acquire(weight=3)
            if lease is not None:
                admitted += 1
                time.sleep(0.005)
                lease.release()
    finally:
        stop.set()
        for t in threads:
            t.join()
    assert admitted > 0
    assert 3 in seen
    assert seen <= {0, 1, 3, 4}, seen  # 3 alone, or with the one light unit


def test_weight3_is_not_starved_by_a_weight1_stream(make_budget: Callable[..., LaneBudget]) -> None:
    """T61: weight-1 jobs of 0.2 s from three processes do not hold a weight-3 waiter off."""
    others = [real(make_budget, 3, 200 + i) for i in range(3)]
    waiter = real(make_budget, 3, 299)
    stop = threading.Event()

    def stream(budget: LaneBudget) -> None:
        while not stop.is_set():
            try:
                with budget.acquire(timeout=5):
                    time.sleep(0.2)
            except LaneWaitTimeout:
                return

    threads = [threading.Thread(target=stream, args=(b,)) for b in others for _ in range(2)]
    for t in threads:
        t.start()
    try:
        # The contention case needs the streams to fill the limit before the waiter asks.
        assert wait_until(lambda: others[0].held_count()[0] >= 3)
        started = time.monotonic()
        lease = waiter.acquire(weight=3, timeout=8)
        waited = time.monotonic() - started
        lease.release()
    finally:
        stop.set()
        for t in threads:
            t.join()
    assert waited < 0.2 + 1.0 + 0.8, waited


@pytest.mark.parametrize("shape", ["second_process", "two_waiters", "same_process_as_holder"])
def test_weight3_and_weight1_holders_all_finish_at_limit_3(make_budget: Callable[..., LaneBudget], shape: str) -> None:
    """T62: three weight-1 holders and weighted waiters; everything finishes."""
    holders = real(make_budget, 3, 301)
    other = real(make_budget, 3, 302)
    third = real(make_budget, 3, 303)
    done: list[str] = []
    errors: list[BaseException] = []

    def hold(budget: LaneBudget, name: str) -> None:
        try:
            with budget.acquire(timeout=20):
                time.sleep(0.3)
            done.append(name)
        except BaseException as exc:
            errors.append(exc)

    def heavy(budget: LaneBudget, name: str) -> None:
        try:
            with budget.acquire(weight=3, timeout=20):
                time.sleep(0.1)
            done.append(name)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=hold, args=(holders, f"h{i}")) for i in range(3)]
    for t in threads:
        t.start()
    assert wait_until(lambda: holders.held_count()[0] == 3)
    waiters = {
        "second_process": [(other, "w1")],
        "two_waiters": [(other, "w1"), (third, "w2")],
        "same_process_as_holder": [(holders, "w1")],
    }[shape]
    threads += [threading.Thread(target=heavy, args=w) for w in waiters]
    for t in threads[3:]:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors, errors
    assert sorted(done) == sorted([f"h{i}" for i in range(3)] + [n for _, n in waiters])


def test_weight_above_limit_runs_alone(make_budget: Callable[..., LaneBudget]) -> None:
    """T63: weight 3 at limit 1 is admitted when nothing is held, and blocks the rest while it runs."""
    a = real(make_budget, 1, 401)
    b = real(make_budget, 1, 402)
    big = a.acquire(weight=3, timeout=2)
    assert a.held_count()[0] == 3
    assert b.try_acquire() is None
    assert b.try_acquire(weight=3) is None
    big.release()
    assert b.try_acquire() is not None


def test_weight_above_limit_waits_for_a_holder(make_budget: Callable[..., LaneBudget]) -> None:
    a = real(make_budget, 1, 411)
    b = real(make_budget, 1, 412)
    small = a.try_acquire()
    assert small is not None
    assert b.try_acquire(weight=3) is None  # not admitted while anything is held
    small.release()
    assert b.try_acquire(weight=3) is not None


def test_weight1_defers_to_a_waiter_larger_than_the_limit(make_budget: Callable[..., LaneBudget]) -> None:
    """T63: while a waiter that can only run alone is fresh, weight-1 acquirers do not take the idle lane."""
    holder = real(make_budget, 1, 421)
    big = real(make_budget, 1, 422)
    light = real(make_budget, 1, 423)
    lease = holder.try_acquire()
    assert lease is not None
    result: list[object] = []
    t = threading.Thread(target=lambda: result.append(big.acquire(weight=3, timeout=10)))
    t.start()
    try:
        assert wait_until(lambda: active_waiter(flag(light), time.monotonic()) is not None)
        lease.release()
        # The lane is idle for a moment, but the waiter is ahead of this caller.
        denied = 0
        while not result and denied < 40:
            if light.try_acquire() is None:
                denied += 1
            time.sleep(0.005)
        t.join(timeout=5)
    finally:
        t.join(timeout=5)
    assert result, "the waiter was never admitted"
    assert denied > 0
    result[0].release()  # type: ignore[attr-defined]


def test_weight1_leaves_room_for_the_waiter(make_budget: Callable[..., LaneBudget]) -> None:
    holder = real(make_budget, 8, 431)
    waiter = real(make_budget, 8, 432)
    light = real(make_budget, 8, 433)
    held = [holder.try_acquire() for _ in range(4)]
    t = threading.Thread(target=lambda: _swallow(lambda: waiter.acquire(weight=6, timeout=2.5)))
    t.start()
    try:
        assert wait_until(lambda: active_waiter(flag(light), time.monotonic()) is not None)
        # Limit 8 and a waiter of 6 leave room for 2 units in total.
        assert light.try_acquire() is None  # 4 held
        held.pop().release()
        held.pop().release()
        assert light.try_acquire() is None  # 2 held: 2 + 1 > 2
        held.pop().release()
        # 1 held: 1 + 1 <= 2. A call also returns None while the waiter's polling holds
        # the slot mutex for an instant ("the caller polls"), so poll for the answer.
        mine = take_soon(light)
        assert mine is not None
        mine.release()
    finally:
        t.join()
        for lease in held:
            if lease is not None:
                lease.release()


def test_waiter_flag_expires_when_the_waiter_dies(make_budget: Callable[..., LaneBudget], lane_dir: Path) -> None:
    """T64: a SIGKILLed waiter stops rewriting; weight-1 acquisition resumes within 2.5 s."""
    holder = real(make_budget, 3, 501)
    light = real(make_budget, 3, 502)
    lease = holder.try_acquire()
    assert lease is not None
    child = _spawn_waiter(lane_dir, 3)
    try:
        assert wait_until(lambda: active_waiter(flag(light), time.monotonic()) is not None, 20)
        assert light.try_acquire() is None  # deferring to the waiter
        child.kill()
        child.wait(timeout=10)
        started = time.monotonic()
        got = None
        while got is None and time.monotonic() - started < 6:
            got = light.try_acquire()
            time.sleep(0.02)
        assert got is not None
        assert time.monotonic() - started <= WAITER_FRESH_SECONDS + 0.6
    finally:
        child.kill()
        child.wait(timeout=10)


def test_waiter_flag_expires_when_its_owner_is_sigstopped(make_budget: Callable[..., LaneBudget], lane_dir: Path) -> None:
    import os
    import signal

    holder = real(make_budget, 3, 511)
    light = real(make_budget, 3, 512)
    lease = holder.try_acquire()
    assert lease is not None
    child = _spawn_waiter(lane_dir, 3)
    try:
        assert wait_until(lambda: active_waiter(flag(light), time.monotonic()) is not None, 20)
        os.kill(child.pid, signal.SIGSTOP)
        started = time.monotonic()
        got = None
        while got is None and time.monotonic() - started < 6:
            got = light.try_acquire()
            time.sleep(0.02)
        assert got is not None
        assert time.monotonic() - started <= WAITER_FRESH_SECONDS + 0.6
    finally:
        os.kill(child.pid, signal.SIGCONT)
        child.kill()
        child.wait(timeout=10)


def _spawn_waiter(lane_dir: Path, lanes: int) -> subprocess.Popen:
    code = textwrap.dedent(
        f"""
        import sys, time
        from pathlib import Path
        from app.services.resource_governor.lanes import LaneBudget, plan_lanes
        plan = plan_lanes(cpus={lanes * 2}, heavy={lanes * 2})
        budget = LaneBudget.open(plan, Path(sys.argv[1]), memory_reader=lambda: (None, None))
        print("up", flush=True)
        budget.acquire(weight=3, timeout=60)
        time.sleep(60)
        """
    )
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(lane_dir)], stdout=subprocess.PIPE, text=True
    )
    assert child.stdout is not None
    assert child.stdout.readline().strip() == "up"
    return child


def test_waiter_flag_write_rate_is_bounded(make_budget: Callable[..., LaneBudget], monkeypatch: pytest.MonkeyPatch) -> None:
    """T64: a live waiter rewrites its stamp at most twice a second."""
    holder = real(make_budget, 3, 521)
    waiter = real(make_budget, 3, 522)
    lease = holder.try_acquire()
    assert lease is not None
    writes: list[float] = []
    original = lane_ledger.LedgerTxn.write

    def counting(self: lane_ledger.LedgerTxn, record: LedgerRecord) -> None:
        if record.waiter_pid == 522:
            writes.append(time.monotonic())
        original(self, record)

    monkeypatch.setattr(lane_ledger.LedgerTxn, "write", counting)
    with pytest.raises(LaneWaitTimeout):
        waiter.acquire(weight=3, timeout=3.0)
    # Registered once, rewritten every 0.5 s: about 6 in 3 s, never one per poll (about 40).
    assert 3 <= len(writes) <= 8, len(writes)


def test_flag_not_lost_by_other_ledger_writers(make_budget: Callable[..., LaneBudget]) -> None:
    """A brake step and an incident written between registration and admission keep the flag."""
    holder = real(make_budget, 3, 531)
    waiter = real(make_budget, 3, 532)
    other = real(make_budget, 3, 533)
    lease = holder.try_acquire()
    assert lease is not None
    t = threading.Thread(target=lambda: _swallow(lambda: waiter.acquire(weight=3, timeout=3)))
    t.start()
    try:
        assert wait_until(lambda: active_waiter(flag(other), time.monotonic()) is not None)
        other.report_incident()
        other._ledger.invalidate()
        other.request_reap()
        record = flag(other)
        assert (record.waiter_weight, record.waiter_pid) == (3, 532)
        assert record.cap < 3  # the incident did land
    finally:
        t.join()


def _swallow(call: Callable[[], object]) -> None:
    with contextlib.suppress(LaneWaitTimeout):
        call()


def test_waiter_cooldown_cleared_on_abandon(make_budget: Callable[..., LaneBudget], clock: FakeClock) -> None:
    """After a waiter gives up, every acquirer ignores the flag and nobody may register for 2.5 s."""
    holder = make_budget(3, pid=541)
    waiter = make_budget(3, pid=542)
    second = make_budget(3, pid=543)
    lease = holder.try_acquire()
    assert lease is not None
    request = lanes_request()
    assert waiter.try_acquire(weight=3, _request=request) is None
    waiter._hold_place(request, 3)
    assert active_waiter(waiter._ledger.read(), clock()) == (3, 542)
    waiter._release_place(request)
    record = waiter._ledger.read()
    assert record.waiter_at == 0 and record.waiter_clear_at == pytest.approx(clock() + WAITER_COOLDOWN_SECONDS)
    # Cooldown: a second weighted request cannot register, and weight 1 sees no flag.
    other = lanes_request()
    second._hold_place(other, 3)
    assert not other.registered
    assert second._ledger.read().waiter_at == 0
    assert holder.try_acquire() is not None
    clock.advance(WAITER_COOLDOWN_SECONDS + 0.1)
    second._hold_place(other, 3)
    assert other.registered


def lanes_request() -> object:
    from app.services.resource_governor.lanes import _WaiterRequest

    return _WaiterRequest()


def test_flag_in_cooldown_is_ignored_even_if_still_set() -> None:
    now = 100.0
    record = LedgerRecord(waiter_at=now - 0.1, waiter_weight=3, waiter_pid=9, waiter_clear_at=now + 1.0)
    assert active_waiter(record, now) is None
    assert active_waiter(LedgerRecord(waiter_at=now - 0.1, waiter_weight=3, waiter_pid=9), now) == (3, 9)
    assert active_waiter(LedgerRecord(waiter_at=now - 3.0, waiter_weight=3, waiter_pid=9), now) is None


def test_a_request_in_the_waiters_process_still_defers(make_budget: Callable[..., LaneBudget], clock: FakeClock) -> None:
    """Only the registered request is exempt, not every caller with the waiter's pid."""
    budget = make_budget(3, pid=551)
    holder = make_budget(3, pid=552)
    lease = holder.try_acquire()
    assert lease is not None
    request = lanes_request()
    budget._hold_place(request, 3)
    assert budget.try_acquire(weight=1) is None  # a different request in the same process
    assert budget.try_acquire(weight=3, _request=request) is None  # the waiter itself is only held by capacity
    lease.release()
    assert budget.try_acquire(weight=3, _request=request) is not None


def test_weight_one_progresses_under_continuous_weighted_stream(make_budget: Callable[..., LaneBudget]) -> None:
    """Round 5 R5-2: at limit 3 a continuous weight-3 stream does not exclude weight-1 work."""
    heavy = [real(make_budget, 3, 600 + i) for i in range(3)]
    light = real(make_budget, 3, 610)
    stop = threading.Event()

    def stream(budget: LaneBudget) -> None:
        while not stop.is_set():
            try:
                with budget.acquire(weight=3, timeout=10):
                    time.sleep(0.25)
            except LaneWaitTimeout:
                return
            time.sleep(0.03)  # the next request arrives over HTTP, not instantly

    threads = [threading.Thread(target=stream, args=(b,)) for b in heavy]
    for t in threads:
        t.start()
    starts: list[float] = []
    try:
        begin = time.monotonic()
        while time.monotonic() - begin < 10:
            lease = light.try_acquire()
            if lease is not None:
                starts.append(time.monotonic() - begin)
                time.sleep(0.02)
                lease.release()
            time.sleep(0.005)
    finally:
        stop.set()
        for t in threads:
            t.join()
    # Without the cooldown the flag is never absent and this stays at zero.
    assert len(starts) >= 1, starts


def test_health_reports_a_fresh_waiter_and_clears_it_once_admitted(make_budget: Callable[..., LaneBudget]) -> None:
    holder = real(make_budget, 1, 111)
    big = real(make_budget, 1, 112)
    health = real(make_budget, 1, 113)
    assert health.stats()["waiter_active"] is False
    lease = holder.try_acquire()
    assert lease is not None
    result: list[object] = []
    t = threading.Thread(target=lambda: result.append(big.acquire(weight=3, timeout=10)))
    t.start()
    try:
        assert wait_until(lambda: health.stats()["waiter_active"] is True)
        lease.release()
        t.join(timeout=5)
    finally:
        t.join(timeout=5)
    assert result, "the waiter was never admitted"
    result[0].release()  # type: ignore[attr-defined]
    assert wait_until(lambda: health.stats()["waiter_active"] is False)
