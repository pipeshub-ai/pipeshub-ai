"""What the wiring added to ClassGate: a ceiling per class, an upgrade in place, a token weight, a capacity that moves."""
from __future__ import annotations

import asyncio

from app.modules.parse_engine.admission import AdmissionNumbers
from app.modules.parse_engine.budget_port import Family
from app.modules.parse_engine.class_gate import ClassGate, GateRequest, PoolCapacity
from app.modules.parse_engine.cost import CostClass
from tests.support.eventually import done_without_waiting
from tests.support.fake_member_budget import FakeMemberBudget
from tests.support.settle import SETTLE_S

S, L, H = CostClass.SMALL, CostClass.LARGE, CostClass.HUGE
N = AdmissionNumbers()


def req(cls: CostClass, **kw) -> GateRequest:
    return GateRequest(cls=cls, est_seconds=10.0 if cls is S else 120.0, **kw)


async def test_the_ceiling_passed_to_the_ledger_is_per_class_and_huge_is_held_to_huge_cap() -> None:
    """one ceiling per class. A huge unit's ceiling never exceeds `slots(huge_cap)`; a large unit's is large's own."""
    numbers = AdmissionNumbers(huge_cap=0.1)
    budget = FakeMemberBudget(16)
    gate = ClassGate("heavy_parse", numbers=lambda: numbers, budget=budget)
    large = await gate.acquire(req(L), deadline_s=1)
    huge = await gate.acquire(req(H), deadline_s=1)
    ceilings = {d.cls: d.ceiling_tokens for d in budget.grant_log}
    assert ceilings[H] == 1  # floor(0.1 x 16), below the guarantee large gets
    assert ceilings[L] == 3  # large_min x 16: its own, not shared with huge
    large.release()
    huge.release()


async def test_the_token_weight_can_differ_from_the_gate_unit() -> None:
    """A large parse costs two permits of the pool but one token of the member budget."""
    budget = FakeMemberBudget(8)
    gate = ClassGate("heavy_parse", numbers=lambda: N, budget=budget, capacity=lambda: PoolCapacity(8))
    permit = await gate.acquire(GateRequest(cls=L, family=Family.PARSE, cpu=2, tokens=1, est_seconds=100.0), deadline_s=1)
    assert budget.snapshot()["used"] == 1
    assert gate.running(L) == 2
    permit.release()
    assert budget.snapshot()["used"] == 0 and gate.running(L) == 0


async def test_an_upgrade_moves_the_running_count_and_the_org_share_without_a_requeue() -> None:
    gate = ClassGate("index_heavy", numbers=lambda: N, capacity=lambda: PoolCapacity(8))
    permit = await gate.acquire(req(S, org="a"), deadline_s=1)
    assert (gate.running(S), gate.running(L)) == (1, 0)
    permit.upgrade(L)
    assert (gate.running(S), gate.running(L)) == (0, 1)
    assert permit.cls is L and gate.upgrades == 1
    permit.upgrade(L)  # a second call to the same class changes nothing
    assert gate.upgrades == 1
    permit.release()
    assert (gate.running(S), gate.running(L)) == (0, 0)


async def test_an_upgraded_large_counts_against_larges_share_for_the_next_grant() -> None:
    """After the upgrade the pool is treated as holding a large unit: with large's cap reached, another large waits."""
    numbers = AdmissionNumbers(large_cap=0.25, borrow_cap=0.25, large_min=0.1, aged_min=0.1)
    gate = ClassGate("index_heavy", numbers=lambda: numbers, capacity=lambda: PoolCapacity(8))
    a = await gate.acquire(req(S), deadline_s=1)
    b = await gate.acquire(req(S), deadline_s=1)
    a.upgrade(L)
    b.upgrade(L)  # two large units now: the cap of 25 % of 8 is reached
    waiting = asyncio.create_task(gate.acquire(req(L), deadline_s=0.3))
    await asyncio.sleep(0.1)
    assert not waiting.done()
    small = done_without_waiting(gate.acquire(req(S), deadline_s=1))  # a small one is not held back
    small.release()
    a.release()
    got = await asyncio.wait_for(waiting, SETTLE_S)
    got.release()
    b.release()


async def test_a_capacity_that_rises_under_waiters_wakes_them() -> None:
    cap = {"n": 1}
    gate = ClassGate("docling", numbers=lambda: N, capacity=lambda: PoolCapacity(cap["n"]))
    first = await gate.acquire(req(S), deadline_s=1)
    second = asyncio.create_task(gate.acquire(req(S), deadline_s=3))
    await asyncio.sleep(0.2)
    assert not second.done()
    cap["n"] = 2  # a brake lifted: nobody released, nobody arrived
    permit = await asyncio.wait_for(second, 2.0)
    permit.release()
    first.release()
