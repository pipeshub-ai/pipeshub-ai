"""A gate given its own capacity still grants big work only what the ledger's held floors leave, and a unit the ledger has not yet granted does not starve small work."""
from __future__ import annotations

import asyncio
import time

import pytest

from app.modules.parse_engine.admission import AdmissionNumbers
from app.modules.parse_engine.budget_port import AdmissionTimeout, Family
from app.modules.parse_engine.class_gate import ClassGate, GateRequest, PoolCapacity
from app.modules.parse_engine.cost import CostClass
from tests.support.fake_member_budget import FakeMemberBudget

S, L = CostClass.SMALL, CostClass.LARGE
N = AdmissionNumbers()


async def settle(n: int = 12) -> None:
    for _ in range(n):
        await asyncio.sleep(0)


def big(**kw: object) -> GateRequest:
    # A large CPU parse: two governor permits, one member token.
    return GateRequest(cls=L, cpu=2, tokens=1, est_seconds=120.0, **kw)  # type: ignore[arg-type]


async def test_the_gate_takes_the_ledgers_held_floor_into_account_when_it_is_given_its_own_capacity() -> None:
    budget = FakeMemberBudget(18, floors={Family.PARSE: 14})
    gate = ClassGate("heavy_parse", numbers=lambda: N, budget=budget, capacity=lambda: PoolCapacity(18))
    tasks = [asyncio.create_task(gate.acquire(big(), deadline_s=0.3)) for _ in range(8)]
    await settle()
    # Big work may take 18 - 14 = 4 tokens; the gate must not start more than the ledger will grant.
    assert budget.snapshot()["used"] <= 4
    assert gate.running(L) <= 4  # permits: the pool minus the held floor
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def test_a_unit_refused_by_the_ledger_does_not_count_as_running_and_starve_small_work() -> None:
    budget = FakeMemberBudget(18, floors={Family.PARSE: 14})
    gate = ClassGate("heavy_parse", numbers=lambda: N, budget=budget, capacity=lambda: PoolCapacity(36))
    held = [await gate.acquire(big(), deadline_s=1) for _ in range(4)]
    blocked = asyncio.create_task(gate.acquire(big(), deadline_s=5))
    await settle()
    assert gate.running(L) <= 10  # the fifth is queued in the ledger, not starving the pool
    small = await asyncio.wait_for(
        gate.acquire(GateRequest(cls=S, est_seconds=10.0), deadline_s=1), 1
    )
    assert small.cls is S
    small.release()
    blocked.cancel()
    await asyncio.gather(blocked, return_exceptions=True)
    for p in held:
        p.release()


async def test_a_policy_timer_admits_a_waiter_even_when_nothing_else_changes() -> None:
    """Small demand stops being recent: the big share rises from the recent cap to the borrow cap with no event."""
    from app.modules.parse_engine.admission import AdmissionNumbers

    numbers = AdmissionNumbers(small_recent_s=0.4)
    gate = ClassGate("heavy_parse", numbers=lambda: numbers, capacity=lambda: PoolCapacity(10))
    small = await gate.acquire(GateRequest(cls=S, est_seconds=10.0), deadline_s=1)
    big = [await gate.acquire(GateRequest(cls=L, est_seconds=120.0), deadline_s=1) for _ in range(5)]  # up to the recent cap
    small.release()
    blocked = asyncio.create_task(gate.acquire(GateRequest(cls=L, est_seconds=120.0), deadline_s=5))
    await asyncio.sleep(0.15)
    assert not blocked.done()  # the sixth large is over the recent cap while small demand is recent
    started = time.monotonic()
    permit = await asyncio.wait_for(blocked, 4)  # no release, no capacity change: only the clock moved
    assert time.monotonic() - started < 2.0
    permit.release()
    for p in big:
        p.release()


async def test_the_class_cap_applies_to_the_whole_weight_of_a_request_in_an_ordering_gate() -> None:
    """Capacity 10, small demand recent: large work stops at 5. A request of weight 2 over four running is refused."""
    gate = ClassGate("heavy_parse", numbers=lambda: N, capacity=lambda: PoolCapacity(10))
    (await gate.acquire(GateRequest(cls=S, est_seconds=10.0), deadline_s=1)).release()  # small demand is now recent
    running = [await gate.acquire(GateRequest(cls=L, est_seconds=120.0), deadline_s=1) for _ in range(4)]
    heavy = asyncio.create_task(gate.acquire(GateRequest(cls=L, cpu=2, est_seconds=120.0), deadline_s=0.4))
    await settle()
    await asyncio.sleep(0.2)
    assert not heavy.done()  # 4 + 2 would be 6 large tokens against a cap of 5
    assert gate.running(L) == 4
    one = await asyncio.wait_for(gate.acquire(GateRequest(cls=L, est_seconds=120.0), deadline_s=1), 1)  # 4 + 1 fits
    assert gate.running(L) == 5
    with pytest.raises(AdmissionTimeout):  # the weight-2 request times out, it was never granted
        await heavy
    one.release()
    for p in running:
        p.release()


async def test_a_class_with_nothing_running_takes_a_unit_heavier_than_its_ceiling() -> None:
    gate = ClassGate("heavy_parse", numbers=lambda: N, capacity=lambda: PoolCapacity(4))
    permit = await asyncio.wait_for(gate.acquire(GateRequest(cls=L, cpu=3, est_seconds=120.0), deadline_s=1), 1)
    assert gate.running(L) == 3
    permit.release()


async def test_a_waiter_over_its_class_ceiling_does_not_block_the_ones_behind_it() -> None:
    """One large permit runs; the head of the large queue weighs 2 and would exceed the class ceiling. A weight-1 large
    behind it that fits, and a small record, are granted: the pump skips the head, it does not stop."""
    gate = ClassGate("heavy_parse", numbers=lambda: N, capacity=lambda: PoolCapacity(10))
    running = await gate.acquire(GateRequest(cls=L, est_seconds=120.0), deadline_s=1)
    heavy = asyncio.create_task(gate.acquire(GateRequest(cls=L, cpu=2, est_seconds=120.0), deadline_s=1.5))
    await settle()
    light_large = asyncio.create_task(gate.acquire(GateRequest(cls=L, est_seconds=120.0), deadline_s=1.5))
    small = asyncio.create_task(gate.acquire(GateRequest(cls=S, est_seconds=10.0), deadline_s=1.5))
    started = time.monotonic()
    small_permit = await asyncio.wait_for(small, 1.0)  # not stuck behind a request this gate will not admit
    assert time.monotonic() - started < 0.5
    large_permit = await asyncio.wait_for(light_large, 1.0)  # the weight-1 behind the weight-2 head fits and is granted
    assert not heavy.done()
    running.release()
    small_permit.release()
    large_permit.release()
    (await asyncio.wait_for(heavy, 2.0)).release()  # once its class has room it runs
