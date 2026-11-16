"""Reserve exactly once: the gate's permit is the step's only reservation, and nothing holds while it waits."""
from __future__ import annotations

import asyncio

import pytest

from app.modules.parse_engine.admission import AdmissionNumbers
from app.modules.parse_engine.class_gate import ClassGate, GateRequest, PoolCapacity
from app.modules.parse_engine.cost import CostClass
from tests.support.fake_member_budget import FakeMemberBudget


@pytest.mark.parametrize("n", [1, 3, 8])
async def test_n_concurrent_steps_with_capacity_n_all_complete(n: int) -> None:
    budget = FakeMemberBudget(n)
    gate = ClassGate("heavy_parse", numbers=lambda: AdmissionNumbers(), budget=budget)
    started = asyncio.Event()
    running = 0
    peak_tokens = 0

    async def step(i: int) -> None:
        nonlocal running, peak_tokens
        cls = CostClass.SMALL if i % 2 == 0 else CostClass.LARGE
        permit = await gate.acquire(GateRequest(cls, est_seconds=10.0), deadline_s=10)
        permit.adopt()  # the lane takes the permit over; it reserves nothing itself
        running += 1
        peak_tokens = max(peak_tokens, budget.snapshot()["used"])
        if running == n:
            started.set()
        await asyncio.wait_for(started.wait(), 5)  # all N hold their permit at once: no hold-and-wait deadlock
        permit.release()

    await asyncio.wait_for(asyncio.gather(*(step(i) for i in range(n))), 15)
    assert peak_tokens == n  # the ledger shows exactly N tokens
    assert budget.reserve_calls == n  # one reserve per step, from the gate only
    assert budget.snapshot()["used"] == 0


async def test_gate_never_reserves_twice_for_one_step() -> None:
    budget = FakeMemberBudget(2)
    gate = ClassGate("heavy_parse", numbers=lambda: AdmissionNumbers(), budget=budget)
    p = await gate.acquire(GateRequest(CostClass.SMALL), deadline_s=1)
    assert budget.reserve_calls == 1 and budget.snapshot()["used"] == 1
    p.adopt()
    p.release()
    assert budget.reserve_calls == 1 and budget.snapshot()["used"] == 0


async def test_ordering_ticket_holds_no_tokens() -> None:
    budget = FakeMemberBudget(2)
    docling = ClassGate("docling", numbers=lambda: AdmissionNumbers(), capacity=lambda: PoolCapacity(2))
    tickets = [await docling.acquire(GateRequest(CostClass.SMALL), deadline_s=1) for _ in range(2)]
    assert budget.snapshot()["used"] == 0 and budget.reserve_calls == 0
    for t in tickets:
        t.release()
