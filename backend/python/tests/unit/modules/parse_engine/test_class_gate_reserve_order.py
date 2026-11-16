"""A gate given its own capacity still grants big work only what the ledger's held floors leave, and a unit the ledger has not yet granted does not starve small work."""
from __future__ import annotations

import asyncio

from app.modules.parse_engine.admission import AdmissionNumbers
from app.modules.parse_engine.budget_port import Family
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
