"""Who is woken when the ledger frees something, how fast, and that a stop request is honoured before an attempt."""

from __future__ import annotations

import asyncio
import os
import threading
import time
from typing import TYPE_CHECKING

import pytest

from app.capacity import member_budget as mb
from app.capacity.budget_policy import Demand, Family
from app.capacity.member_budget import (
    AdmissionTimeout,
    Cancelled,
    CancelToken,
    MemberBudget,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def ledger(tmp_path: Path) -> Path:
    d = tmp_path / "ledger"
    d.mkdir(mode=0o700)
    return d


def _llm() -> Demand:
    return Demand(Family.LLM, 0)


def _small(n: int = 1) -> Demand:
    return Demand(Family.PARSE, n, 0, "small")


def _p95(values: list[float]) -> float:
    return sorted(values)[max(0, int(len(values) * 0.95) - 1)]


async def test_a_release_wakes_a_waiter_within_a_few_milliseconds(ledger: Path) -> None:
    b = MemberBudget(ledger, cpu_tokens=16, mem_bytes=1 << 36, llm_cap=1)
    latencies: list[float] = []
    for _ in range(30):
        held = await b.reserve(_llm(), deadline_s=1)
        task = asyncio.create_task(b.reserve(_llm(), deadline_s=5))
        await asyncio.sleep(0.02)
        started = time.monotonic()
        held.release()
        permit = await asyncio.wait_for(task, 2)
        latencies.append(time.monotonic() - started)
        permit.release()
    assert _p95(latencies) < 0.02, sorted(latencies)[-3:]


async def test_a_release_that_lands_before_the_watchers_first_read_is_not_missed(ledger: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The watcher thread starts late (as a descheduled thread would): the waiter's own pre-attempt read still sees the change."""
    b = MemberBudget(ledger, cpu_tokens=16, mem_bytes=1 << 36, llm_cap=1)
    monkeypatch.setattr(mb, "FALLBACK_S", 5.0)  # only a wake, not the fallback tick, can serve the waiter in time
    real_run = mb._Watcher._run

    def late(self: mb._Watcher) -> None:
        time.sleep(0.2)
        real_run(self)

    monkeypatch.setattr(mb._Watcher, "_run", late)
    held = await b.reserve(_llm(), deadline_s=1)
    task = asyncio.create_task(b.reserve(_llm(), deadline_s=10))
    await asyncio.sleep(0.05)  # the waiter has made its first attempt; the watcher thread is still asleep
    started = time.monotonic()
    held.release()
    await asyncio.wait_for(task, 3)
    assert time.monotonic() - started < 1.0


async def test_freeing_k_llm_slots_at_once_wakes_k_waiters_in_one_round(ledger: Path) -> None:
    b = MemberBudget(ledger, cpu_tokens=16, mem_bytes=1 << 36, llm_cap=20)
    holder = MemberBudget(ledger, cpu_tokens=16, mem_bytes=1 << 36, llm_cap=20)
    held = [holder.try_reserve(_llm()) for _ in range(20)]
    waiters = [asyncio.create_task(b.reserve(_llm(), deadline_s=10)) for _ in range(20)]
    await asyncio.sleep(0.3)
    assert not any(w.done() for w in waiters)
    started = time.monotonic()
    holder.close()  # one call frees 20 slots
    await asyncio.wait_for(asyncio.gather(*waiters), 5)
    assert time.monotonic() - started < 0.5  # one wake round, not one waiter per fallback tick (about 1.4 s)
    assert len(held) == 20


async def test_cpu_waiters_do_not_herd(ledger: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """100 CPU waiters, 8 tokens freed one at a time: about a few attempts per grant, not one per waiter."""
    b = MemberBudget(ledger, cpu_tokens=8, mem_bytes=1 << 36)
    held = [b.try_reserve(_small()) for _ in range(8)]
    counts = {"take": 0}
    real_take = b._take

    def take(*a: object, **k: object) -> object:
        counts["take"] += 1
        return real_take(*a, **k)

    waiters = [asyncio.create_task(b.reserve(_small(), deadline_s=30)) for _ in range(100)]
    await asyncio.sleep(0.6)
    monkeypatch.setattr(b, "_take", take)
    for h in held:
        h.release()
        await asyncio.sleep(0.15)
    served = [w for w in waiters if w.done() and not w.cancelled() and w.exception() is None]
    assert len(served) == 8
    assert counts["take"] <= 8 * 6 + 30, counts  # polling or waking everyone made 50 per grant (400 or more)
    for w in waiters:
        w.cancel()
    await asyncio.gather(*waiters, return_exceptions=True)
    for w in served:
        w.result().release()


async def test_a_cancel_or_deadline_is_honoured_before_the_attempt_that_follows_a_wake(ledger: Path) -> None:
    b = MemberBudget(ledger, cpu_tokens=1, mem_bytes=1 << 36)
    held = b.try_reserve(_small())
    token = CancelToken()
    task = asyncio.create_task(b.reserve(_small(), cancel=token))
    await asyncio.sleep(0.1)
    token.cancel()
    held.release()  # capacity is free now, but the caller asked to stop
    with pytest.raises(Cancelled):
        await asyncio.wait_for(task, 2)
    assert b.snapshot()["used"] == 0  # no permit was taken and dropped on the way out


def test_a_blocking_wait_whose_deadline_passed_does_not_take_one_more_permit(ledger: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The wait is woken by a release exactly when its deadline has passed: it raises, it does not take the permit.

    Deterministic: ``time.monotonic`` is the wait's clock, advanced by the test, so no wall-clock margin matters."""
    b = MemberBudget(ledger, cpu_tokens=1, mem_bytes=1 << 36)
    held = b.try_reserve(_small())
    now = [1000.0]
    monkeypatch.setattr(mb.time, "monotonic", lambda: now[0])
    waiting = threading.Event()
    result: list[BaseException | object] = []
    real_wait = threading.Event.wait

    def wait_then_pass_the_deadline(self: threading.Event, timeout: float | None = None) -> bool:
        if timeout is None or waiting.is_set():
            return real_wait(self, timeout)  # thread start-up and everything else that waits on an Event
        waiting.set()  # the waiter has made its first attempt and is about to sleep
        now[0] += 10.0  # the deadline passes while it sleeps ...
        held.release()  # ... and capacity frees at the same moment
        return real_wait(self, 5)

    monkeypatch.setattr(threading.Event, "wait", wait_then_pass_the_deadline)
    try:
        b.reserve_blocking(_small(), deadline_s=5)
        result.append("took the permit")
    except AdmissionTimeout as exc:
        result.append(exc)
    assert isinstance(result[0], AdmissionTimeout), result  # the free token was not taken after the deadline
    monkeypatch.setattr(threading.Event, "wait", real_wait)
    assert waiting.is_set()
    assert b.snapshot()["used"] == 0


def test_demand_expiry_follows_the_publication_time_not_the_slot_beat(ledger: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [1_000_000]
    monkeypatch.setattr(mb, "_now_ms", lambda: clock[0])
    b = MemberBudget(ledger, cpu_tokens=16, mem_bytes=1 << 36)
    b.publish_demand({"heavy_parse": (5.0, 1, 1, 1)})
    assert b.container_demand(max_age_ms=3000, now_ms=clock[0])["heavy_parse"][0] == 5.0
    clock[0] += 10_000
    b.try_reserve(_small()).release()  # permit traffic refreshes the slot's beat; the demand is still 10 s old
    assert b.container_demand(max_age_ms=3000, now_ms=clock[0])["heavy_parse"][0] == 0.0


def test_concurrent_releases_and_memory_refreshes_never_raise(ledger: Path) -> None:
    b = MemberBudget(ledger, cpu_tokens=64, mem_bytes=1 << 36)
    errors: list[BaseException] = []
    stop = threading.Event()

    def refresher() -> None:
        try:
            while not stop.is_set():
                b.refresh_memory()
        except BaseException as exc:
            errors.append(exc)

    t = threading.Thread(target=refresher)
    t.start()
    try:
        for _ in range(300):
            permits = [b.try_reserve(Demand(Family.PARSE, 1, 1 << 20, "small")) for _ in range(8)]
            threads = [threading.Thread(target=p.release) for p in permits if p]
            for th in threads:
                th.start()
            for th in threads:
                th.join()
    finally:
        stop.set()
        t.join()
    assert errors == []
    assert b.snapshot()["used"] == 0


def test_a_forked_child_that_only_closes_does_not_keep_the_parents_lock_alive(ledger: Path) -> None:
    b = MemberBudget(ledger, cpu_tokens=8, mem_bytes=1 << 34)
    held = b.try_reserve(_small(2))
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:
        try:
            os.close(r)
            b.close()  # the child's copy of the parent's handle, closed before any ledger transaction
            os.write(w, b"x")
            time.sleep(30)
        finally:
            os._exit(0)
    os.close(w)  # the parent's write end: a failed child then ends the read with EOF instead of hanging it
    assert _read_or_kill(r, pid) == b"x"
    os.close(r)
    try:
        # The parent's slot lock must still be held by the parent alone: closing a copy does not release it,
        # and the child must not subtract the parent's holdings.
        assert b.snapshot()["used"] == 2
        assert held is not None
    finally:
        os.kill(pid, 9)
        os.waitpid(pid, 0)


def test_a_forked_child_can_wait_for_a_slot_the_parent_frees(ledger: Path) -> None:
    """The child's first call is a wait: it must reattach before it reads the change counters."""
    b = MemberBudget(ledger, cpu_tokens=8, mem_bytes=1 << 34, llm_cap=1)
    held = b.try_reserve(_llm())
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:
        code = 1
        try:
            permit = b.reserve_blocking(_llm(), deadline_s=3)
            permit.release()
            code = 0
        except BaseException:
            code = 2
        finally:
            os.write(w, bytes([code]))
            os._exit(code)
    time.sleep(0.1)
    held.release()  # the parent frees the only slot while the child waits
    outcome = _read_or_kill(r, pid)
    os.waitpid(pid, 0)
    assert outcome == b"\x00"


def _read_or_kill(r: int, pid: int, timeout: float = 15.0) -> bytes:
    """One byte from the child, or a failure: a child that hangs (a regression) is killed so pytest does not hang with it."""
    import select
    import signal

    ready, _, _ = select.select([r], [], [], timeout)
    if not ready:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
        pytest.fail(f"the forked child did not answer within {timeout} s (it was killed)")
    return os.read(r, 1)


def _in_a_forked_child(work: object) -> bytes:
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:
        code = 2
        try:
            work()  # type: ignore[operator]
            code = 0
        except BaseException:
            code = 1
        finally:
            os.write(w, bytes([code]))
            os._exit(code)
    outcome = _read_or_kill(r, pid)
    os.waitpid(pid, 0)
    os.close(r)
    return outcome


def test_every_entry_point_works_in_a_forked_child(ledger: Path) -> None:
    """Wait, try_reserve, async wait, snapshot: each is the child's first ledger call in its own fork."""
    b = MemberBudget(ledger, cpu_tokens=8, mem_bytes=1 << 34, llm_cap=4)

    def first_try() -> None:
        b.try_reserve(_small()).release()  # type: ignore[union-attr]

    def first_blocking_wait() -> None:
        b.reserve_blocking(_small(), deadline_s=1).release()

    def first_async_wait() -> None:
        asyncio.run(_async_wait(b))

    def first_snapshot() -> None:
        assert b.snapshot()["cpu_tokens"] == 8

    for work in (first_try, first_blocking_wait, first_async_wait, first_snapshot):
        assert _in_a_forked_child(work) == b"\x00", work.__name__


async def _async_wait(b: MemberBudget) -> None:
    (await b.reserve(_small(), deadline_s=1)).release()


async def test_a_freed_token_wakes_the_critical_waiter_before_an_older_big_one(ledger: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mb, "FALLBACK_S", 5.0)  # only a wake, not the fallback tick, can serve the waiter in time
    b = MemberBudget(ledger, cpu_tokens=1, mem_bytes=1 << 36)
    held = b.try_reserve(_small())
    big = asyncio.create_task(b.reserve(Demand(Family.PARSE, 1, 0, "large"), deadline_s=10))
    await asyncio.sleep(0.1)
    critical = asyncio.create_task(b.reserve(_small(), deadline_s=10))
    await asyncio.sleep(0.1)
    started = time.monotonic()
    held.release()
    permit = await asyncio.wait_for(critical, 2)
    assert time.monotonic() - started < 0.5  # woken first, not after the fallback tick (5 s)
    assert not big.done()
    permit.release()
    (await asyncio.wait_for(big, 2)).release()


async def test_a_wake_that_is_lost_is_served_by_the_fallback_tick(ledger: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mb, "FALLBACK_S", 0.05)
    real_pick = mb._Watcher._pick

    def lose_change_wakes(self: mb._Watcher, entries: list, n: int, *, llm: bool, fallback: bool) -> list:
        return real_pick(self, entries, n, llm=llm, fallback=fallback) if fallback else []

    monkeypatch.setattr(mb._Watcher, "_pick", lose_change_wakes)
    b = MemberBudget(ledger, cpu_tokens=1, mem_bytes=1 << 36, llm_cap=1)
    held = b.try_reserve(_llm())
    task = asyncio.create_task(b.reserve(_llm(), deadline_s=10))
    await asyncio.sleep(0.1)
    held.release()  # the change wake is lost on purpose
    permit = await asyncio.wait_for(task, 2)  # the fallback tick serves it
    permit.release()


async def test_a_wake_callback_whose_loop_is_gone_does_not_end_the_watcher(ledger: Path) -> None:
    """A finished waiter's copied callback raises (its loop closed); the other waiters must still be woken."""
    b = MemberBudget(ledger, cpu_tokens=16, mem_bytes=1 << 36, llm_cap=1)
    held = await b.reserve(_llm(), deadline_s=1)
    dead_loop = asyncio.new_event_loop()
    dead_loop.close()

    def stale() -> None:
        dead_loop.call_soon_threadsafe(lambda: None)  # RuntimeError: Event loop is closed

    token = b._watcher.add(stale, llm=True, critical=False, since=b._changes())  # registered first: woken first
    waiter = asyncio.create_task(b.reserve(_llm(), deadline_s=10))
    await asyncio.sleep(0.1)
    held.release()  # the watcher wakes the stale entry first, and must carry on to the real waiter
    permit = await asyncio.wait_for(waiter, 2)
    permit.release()
    b._watcher.remove(token)


def test_a_permit_inherited_by_a_fork_with_its_lock_held_releases_nothing_and_does_not_block(ledger: Path) -> None:
    b = MemberBudget(ledger, cpu_tokens=8, mem_bytes=1 << 34)
    permit = b.try_reserve(_small(2))
    holder_has_lock = threading.Event()
    stop = threading.Event()

    def hold_the_permit_lock() -> None:
        with permit._lock:
            holder_has_lock.set()
            stop.wait(10)

    t = threading.Thread(target=hold_the_permit_lock)
    t.start()
    holder_has_lock.wait(5)
    try:
        outcome = _in_a_forked_child(permit.release)  # the lock is copied locked into the child
    finally:
        stop.set()
        t.join()
    assert outcome == b"\x00"
    assert b.snapshot()["used"] == 2  # the child released nothing of its parent's
