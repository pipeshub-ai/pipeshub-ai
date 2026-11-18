"""A holder that dies mid-update never lets the ledger exceed its budget; waiters do not poll; forks and LLM waiters stay out of the way."""

from __future__ import annotations

import asyncio
import multiprocessing as mp
import os
import time
from typing import TYPE_CHECKING

import pytest

from app.capacity.budget_policy import Demand, Family
from app.capacity.member_budget import MemberBudget
from tests.support import ledger_procs as lp

if TYPE_CHECKING:
    from pathlib import Path

C = 8


@pytest.fixture
def ledger(tmp_path: Path) -> Path:
    d = tmp_path / "ledger"
    d.mkdir(mode=0o700)
    return d


def _small(n: int = 1) -> Demand:
    return Demand(Family.PARSE, n, 0, "small")


@pytest.mark.parametrize("mode", ["acquire", "release"])
def test_a_holder_killed_between_its_two_writes_never_lets_the_ledger_exceed_c(ledger: Path, mode: str) -> None:
    parent = MemberBudget(ledger, cpu_tokens=C, mem_bytes=1 << 34)
    held = [parent.try_reserve(_small()) for _ in range(4)]
    assert all(held)
    child = mp.get_context("spawn").Process(target=lp.die_at_hot_write, args=(str(ledger), C, mode))
    child.start()
    child.join(60)
    assert child.exitcode == 9
    parent.elect_controller(1.0)  # forces the reap of the dead slot
    assert parent.snapshot()["used"] == 4  # exactly what the live process holds: no leak, no double count
    more = 0
    while parent.try_reserve(_small()) is not None:
        more += 1
        assert more <= C
    assert 4 + more <= C


def test_a_forked_child_releasing_an_inherited_permit_changes_nothing(ledger: Path) -> None:
    budget = MemberBudget(ledger, cpu_tokens=C, mem_bytes=1 << 34)
    permits = [budget.try_reserve(_small()) for _ in range(4)]
    pid = os.fork()
    if pid == 0:
        try:
            permits[0].release()  # type: ignore[union-attr]
        finally:
            os._exit(0)
    os.waitpid(pid, 0)
    assert budget.snapshot()["used"] == 4
    permits[0].release()  # type: ignore[union-attr]
    assert budget.snapshot()["used"] == 3


async def test_llm_waiters_take_no_place_in_the_order(ledger: Path) -> None:
    b = MemberBudget(ledger, cpu_tokens=16, mem_bytes=1 << 36, llm_cap=24)
    held = [await b.reserve(Demand(Family.LLM, 0), deadline_s=1) for _ in range(24)]
    waiters = [asyncio.create_task(b.reserve(Demand(Family.LLM, 0), deadline_s=5)) for _ in range(60)]
    await asyncio.sleep(0.3)
    assert b.snapshot()["families"]["llm"]["waiting"] == 0  # no head in the table
    assert b.register_head(Demand(Family.DOCLING, 2, 0, "small")) is not None  # a critical waiter keeps its place
    for h in held:
        h.release()
    results = await asyncio.gather(*waiters, return_exceptions=True)
    assert sum(not isinstance(r, Exception) for r in results) == 24  # the cap still holds; the rest time out later
    for r in results:
        if not isinstance(r, Exception):
            r.release()


def _pairs_per_s(b: MemberBudget, seconds: float) -> float:
    n = 0
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        b.try_reserve(_small()).release()  # type: ignore[union-attr]
        n += 1
    return n / seconds


async def test_idle_llm_waiters_cost_a_bounded_number_of_ledger_attempts(ledger: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Counts, not wall-clock throughput, so it holds on any runner: 200 waiters on full LLM slots make no
    reservation attempts while nothing is freed (beyond the watcher's fallback tick), and about one per slot freed.
    Polling waiters made thousands per second (each a locked ledger transaction)."""
    b = MemberBudget(ledger, cpu_tokens=16, mem_bytes=1 << 36, llm_cap=3)
    held = [await b.reserve(Demand(Family.LLM, 0), deadline_s=1) for _ in range(3)]
    attempts = {"take": 0, "lock": 0}
    real_take, real_lock = b._take, b._lock

    def take(*a: object, **k: object) -> object:
        attempts["take"] += 1
        return real_take(*a, **k)

    def lock() -> None:
        attempts["lock"] += 1
        real_lock()

    waiters = [asyncio.create_task(b.reserve(Demand(Family.LLM, 0), deadline_s=30)) for _ in range(200)]
    await asyncio.sleep(0.5)  # every waiter has made its first attempt
    monkeypatch.setattr(b, "_take", take)
    monkeypatch.setattr(b, "_lock", lock)
    await asyncio.sleep(2.0)  # idle: nothing is freed
    idle = dict(attempts)
    assert idle["take"] <= 60 and idle["lock"] <= 120, idle  # the fallback tick (one waiter per 50-100 ms), not 200 pollers
    for h in held:
        h.release()
        await asyncio.sleep(0.3)
    served = [w for w in waiters if w.done() and not w.cancelled() and w.exception() is None]
    assert len(served) == 3
    freed = {k: attempts[k] - idle[k] for k in attempts}
    assert freed["take"] <= 3 * 4 + 20 and freed["lock"] <= 3 * 8 + 40, freed  # about one wake per freed slot, plus the tick
    for w in waiters:
        w.cancel()
    await asyncio.gather(*waiters, return_exceptions=True)
    for w in served:
        w.result().release()


def test_two_hundred_llm_waiters_do_not_burn_cpu_or_collapse_the_ledger(ledger: Path) -> None:
    """Informational throughput (logged) with a loose catastrophe floor; the exact gate is the counting test above."""
    ctx = mp.get_context("spawn")
    b = lp.open_budget(str(ledger), "main", cpu_tokens=16, mem=1 << 36)
    idle = max(_pairs_per_s(b, 0.5) for _ in range(3))
    held = [b.try_reserve(Demand(Family.LLM, 0)) for _ in range(24)]
    pipes = [ctx.Pipe(duplex=False) for _ in range(4)]
    procs = [ctx.Process(target=lp.llm_waiters, args=(str(ledger), 50, 6.0, w)) for _, w in pipes]
    for p in procs:
        p.start()
    time.sleep(2.0)
    loaded = max(_pairs_per_s(b, 0.5) for _ in range(3))
    for p in procs:
        p.join(60)
    cpu = sum(r.recv() for r, _ in pipes)
    for h in held:
        h.release()  # type: ignore[union-attr]
    print(f"ledger pairs/s: idle {idle:.0f}, with 200 LLM waiters {loaded:.0f}; waiters used {cpu:.1f} CPU-s")
    assert loaded >= 0.3 * idle, (idle, loaded)  # polling took it to 5 %
    assert cpu <= 0.1 * 6.0 * len(procs) + 2.0, cpu


async def test_a_cancel_token_is_a_method_and_stops_a_wait(ledger: Path) -> None:
    from app.capacity.member_budget import Cancelled, CancelToken

    b = MemberBudget(ledger, cpu_tokens=2, mem_bytes=1 << 34)
    held = [b.try_reserve(_small()) for _ in range(2)]
    token = CancelToken()
    assert token.cancelled() is False
    task = asyncio.create_task(b.reserve(_small(), cancel=token))
    await asyncio.sleep(0.1)
    token.cancel()
    with pytest.raises(Cancelled):
        await asyncio.wait_for(task, 2)
    for h in held:
        h.release()  # type: ignore[union-attr]


async def test_a_waiter_gets_the_slots_of_a_holder_that_is_killed_with_no_further_call(ledger: Path) -> None:
    """The waiter is already blocked in ``reserve``; nothing else enters the ledger and no controller runs."""
    ctx = mp.get_context("spawn")
    reader, writer = ctx.Pipe(duplex=False)
    holder = ctx.Process(target=lp.hold_all_llm_slots_then_wait, args=(str(ledger), 3, writer))
    holder.start()
    assert reader.poll(60) and reader.recv() is True
    waiter = MemberBudget(ledger, cpu_tokens=16, mem_bytes=1 << 36, llm_cap=3)
    task = asyncio.create_task(waiter.reserve(Demand(Family.LLM, 0), deadline_s=20))
    await asyncio.sleep(0.3)
    assert not task.done()
    holder.kill()
    holder.join(30)
    permit = await asyncio.wait_for(task, 10)
    assert permit is not None
    permit.release()


def test_an_idle_forked_child_does_not_keep_a_dead_parents_slot_alive(ledger: Path) -> None:
    """The parent dies while its child (which never used the budget) lives: the parent's tokens must come back."""
    import signal

    ctx = mp.get_context("spawn")
    reader, writer = ctx.Pipe(duplex=False)
    parent = ctx.Process(target=lp.hold_then_leave_an_idle_child, args=(str(ledger), writer))
    parent.start()
    assert reader.poll(60)
    child_pid, took = reader.recv()
    parent.join(3)  # the idle child holds the parent's sentinel, so this waits out the timeout
    try:
        assert took and parent.exitcode == 9
        observer = MemberBudget(ledger, cpu_tokens=16, mem_bytes=1 << 36)
        observer.elect_controller(1.0)  # reaps whatever is dead
        assert observer.snapshot()["used"] == 0
    finally:
        os.kill(child_pid, signal.SIGKILL)
