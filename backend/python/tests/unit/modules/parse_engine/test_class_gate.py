"""ClassGate: who goes next, and what is handed to the member budget."""
from __future__ import annotations

import asyncio
import random

import pytest

from app.modules.parse_engine.admission import AdmissionNumbers
from app.modules.parse_engine.budget_port import AdmissionTimeout, Cancelled, Family
from app.modules.parse_engine.class_gate import (
    ClassGate,
    GateRequest,
    PoolCapacity,
    request_from_estimate,
    route_gate,
)
from app.modules.parse_engine.cost import (
    CostClass,
    CostConfig,
    CostInputs,
    FormatKind,
    estimate,
)
from tests.support.eventually import done_without_waiting
from tests.support.fake_member_budget import FakeMemberBudget
from tests.support.settle import SETTLE_S

S, L, H = CostClass.SMALL, CostClass.LARGE, CostClass.HUGE
N = AdmissionNumbers()


async def settle(n: int = 6) -> None:
    for _ in range(n):
        await asyncio.sleep(0)


def make_gate(
    cap: int, *, held: int = 0, floors: dict | None = None, numbers: AdmissionNumbers = N, **kw
) -> tuple[ClassGate, FakeMemberBudget]:
    budget = FakeMemberBudget(cap, floors=floors if floors is not None else ({Family.PARSE: held} if held else {}))
    gate = ClassGate("heavy_parse", numbers=lambda: numbers, budget=budget, **kw)
    return gate, budget


def req(cls: CostClass, **kw) -> GateRequest:
    return GateRequest(cls=cls, est_seconds=10.0 if cls is S else 120.0, **kw)


async def test_small_docling_bypasses_cpu() -> None:
    cfg = CostConfig()
    cpu_gate, budget = make_gate(8)
    docling_gate = ClassGate("docling", numbers=lambda: N, capacity=lambda: PoolCapacity(4))
    # four large layouts hold the CPU pool
    big = [await cpu_gate.acquire(req(L), deadline_s=1) for _ in range(4)]
    assert cpu_gate.running(L) == 4
    est = estimate(CostInputs(FormatKind.PDF, 80_000, pages=1), cfg)
    assert est.needs_docling_permit and not est.needs_cpu_slot
    gate = route_gate(est, cpu_gate, docling_gate)
    assert gate is docling_gate
    before = (budget.reserve_calls, budget.snapshot()["used"])
    permit = done_without_waiting(gate.acquire(request_from_estimate(est), deadline_s=1))
    assert (budget.reserve_calls, budget.snapshot()["used"]) == before  # no CPU token, no reserve
    assert cpu_gate.running(S) == 0 and docling_gate.running(S) == 1
    assert permit.inner is None
    permit.release()
    for p in big:
        p.release()
    # a CPU-routed small PDF goes to the CPU gate
    cpu_est = estimate(CostInputs(FormatKind.PDF, 80_000, pages=35), cfg)
    assert route_gate(cpu_est, cpu_gate, docling_gate) is cpu_gate


async def test_small_goes_ahead_of_queued_large() -> None:
    gate, budget = make_gate(4)
    running = [await gate.acquire(req(L), deadline_s=1) for _ in range(1)]
    running += [await gate.acquire(req(S), deadline_s=1) for _ in range(3)]
    order: list[str] = []

    async def go(name: str, r: GateRequest) -> None:
        p = await gate.acquire(r, deadline_s=5)
        order.append(name)
        p.release()

    big = [asyncio.create_task(go(f"L{i}", req(L))) for i in range(3)]
    await settle()
    sm = asyncio.create_task(go("S", req(S)))
    await settle()
    running[1].release()  # one token frees: the small waiter takes it, not a queued large
    await asyncio.wait_for(sm, SETTLE_S)
    assert order[0] == "S"
    for p in running:
        p.release()
    await asyncio.wait_for(asyncio.gather(*big), 2)
    assert budget.max_used <= 4


async def test_org_share_in_big_class() -> None:
    gate, budget = make_gate(10, org_share=0.5, numbers=AdmissionNumbers(large_cap=0.9, borrow_cap=0.9))
    held: dict[str, list] = {"a": [], "b": []}
    waiters = []

    async def go(org: str) -> None:
        held[org].append(await gate.acquire(req(L, org=org), deadline_s=10))

    # org A floods big work first
    waiters += [asyncio.create_task(go("a")) for _ in range(20)]
    await settle()
    share = max(1, int(10 * 0.5))
    flood = len(held["a"])
    assert flood == 9  # alone, A may take what the borrow cap allows
    b = asyncio.create_task(go("b"))
    await settle()
    assert not held["b"]  # B waits: the pool is full
    for p in held["a"][:2]:
        p.release()  # two tokens free
    await settle()
    assert len(held["b"]) == 1, "org B's big job takes the next big grant"
    assert len(held["a"]) == flood, "A is over its share while B is present: it gets nothing more"
    # A comes back under its share only by finishing work; then it is let in again, up to the share
    for p in held["a"][2:7]:
        p.release()
    await settle()
    live_a = [p for p in held["a"] if not p.released]
    assert len(live_a) == share, "A is let back in, but only up to its share"
    for t in [*waiters, b]:
        t.cancel()
    await asyncio.gather(*waiters, b, return_exceptions=True)
    for ps in held.values():
        for p in ps:
            p.release()
    assert gate.running(L) == 0


async def test_priority_head_of_class() -> None:
    gate, _ = make_gate(2)
    holders = [await gate.acquire(req(S), deadline_s=1) for _ in range(2)]
    order: list[str] = []

    async def go(name: str, **kw) -> None:
        p = await gate.acquire(req(S, **kw), deadline_s=5)
        order.append(name)
        p.release()

    ts = [asyncio.create_task(go("n1")), asyncio.create_task(go("n2"))]
    await settle()
    ts.append(asyncio.create_task(go("now", priority=True)))
    await settle()
    for h in holders:
        h.release()
    await asyncio.wait_for(asyncio.gather(*ts), 2)
    assert order[0] == "now"


async def test_hand_back_keeps_first_seen_so_it_ages() -> None:
    # capacity 1 with small always waiting: an aged big head (first seen long ago) gets A2, a fresh one does not
    now = [10_000.0]
    gate, _ = make_gate(1, clock=lambda: now[0])
    first = await gate.acquire(req(S), deadline_s=1)
    s_task = asyncio.create_task(gate.acquire(req(S), deadline_s=50))
    old_big = asyncio.create_task(gate.acquire(req(L, first_seen_s=now[0] - 2000), deadline_s=50))
    await settle()
    first.release()
    p = await asyncio.wait_for(old_big, SETTLE_S)
    assert gate.grants[-1].rule == "A2" and p.cls is L
    p.release()
    (await asyncio.wait_for(s_task, SETTLE_S)).release()


async def test_fresh_big_does_not_beat_small_at_capacity_one() -> None:
    gate, _ = make_gate(1)
    first = await gate.acquire(req(S), deadline_s=1)
    s_task = asyncio.create_task(gate.acquire(req(S), deadline_s=50))
    big = asyncio.create_task(gate.acquire(req(L), deadline_s=50))
    await settle()
    first.release()
    (await asyncio.wait_for(s_task, SETTLE_S)).release()
    (await asyncio.wait_for(big, SETTLE_S)).release()
    assert [g.cls for g in gate.grants][-2:] == [S, L]


async def test_brake_p2() -> None:
    # capacity falls from 8 to 4 under 4 running big: no further big grant until big is back under P - H
    gate, budget = make_gate(8, held=2, numbers=AdmissionNumbers(large_cap=0.9, borrow_cap=0.9))
    big = [await gate.acquire(req(L), deadline_s=1) for _ in range(4)]
    budget.set_capacity(4)
    waiting = asyncio.create_task(gate.acquire(req(L), deadline_s=20))
    small = await gate.acquire(req(S), deadline_s=1) if budget.snapshot()["used"] < 4 else None
    gate.poke()
    await settle()
    assert not waiting.done()  # big is at 4 of P - H = 2
    assert budget.snapshot()["used"] <= 4 + 0  # nothing admitted past the new C
    big.pop().release()
    big.pop().release()
    gate.poke()
    await settle()
    assert not waiting.done()  # still 2 running, above the 2 allowed
    big.pop().release()
    gate.poke()
    await settle()
    assert waiting.done()
    for p in [*big, await waiting]:
        p.release()
    if small:
        small.release()


async def test_ceiling_and_bound_reach_the_budget() -> None:
    gate, budget = make_gate(10)
    await gate.acquire(req(L), deadline_s=1)
    await gate.acquire(req(L, bounded=False), deadline_s=1)
    big, unbounded = budget.grant_log[-2:]
    assert big.cls is L and big.ceiling_tokens == 2 and big.est_seconds == 120.0 and big.family is Family.PARSE
    assert unbounded.ceiling_tokens == 2  # never borrows: only its guarantee
    small = await gate.acquire(req(S), deadline_s=1)
    assert budget.grant_log[-1].ceiling_tokens is None and small.cls is S


async def test_timeout_and_cancel_leave_nothing_behind() -> None:
    gate, budget = make_gate(1)
    holder = await gate.acquire(req(S), deadline_s=1)
    with pytest.raises(AdmissionTimeout):
        await gate.acquire(req(S), deadline_s=0.05)
    assert gate.waiting(S) == 0 and gate.running(S) == 1

    class Tok:
        def __init__(self) -> None:
            self.c = False

        def cancelled(self) -> bool:
            return self.c

    tok = Tok()
    t = asyncio.create_task(gate.acquire(req(S), cancel=tok))
    await settle()
    tok.c = True
    with pytest.raises(Cancelled):
        await asyncio.wait_for(t, 2)
    assert gate.waiting(S) == 0
    t2 = asyncio.create_task(gate.acquire(req(S), deadline_s=10))
    await settle()
    t2.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t2
    assert gate.waiting(S) == 0 and gate.running(S) == 1
    holder.release()
    assert gate.running(S) == 0 and budget.snapshot()["used"] == 0
    with pytest.raises(ValueError, match="cancel token"):
        await gate.acquire(req(S))


async def test_reserve_failure_returns_the_claim() -> None:
    # the gate claims a slot, then the ledger times the reserve out: the claim must come back
    gate, budget = make_gate(2)
    hog = budget.try_reserve(__import__("app.modules.parse_engine.budget_port", fromlist=["Demand"]).Demand(Family.EMBED, 2, 0))
    assert hog is not None
    with pytest.raises(AdmissionTimeout):
        await gate.acquire(req(S), deadline_s=0.1)
    assert gate.running(S) == 0 and gate.waiting(S) == 0
    hog.release()
    (await gate.acquire(req(S), deadline_s=1)).release()


async def test_release_is_idempotent_and_adopt_keeps_one_permit() -> None:
    gate, budget = make_gate(2)
    p = await gate.acquire(req(S), deadline_s=1)
    assert p.adopt() is p and p.inner is not None and p.inner.adopted
    p.release()
    p.release()
    assert gate.running(S) == 0 and budget.snapshot()["used"] == 0


@pytest.mark.parametrize("seed", [11, 12, 13])
async def test_small_never_behind_large_at_the_gate(seed: int) -> None:
    """Whenever the gate grants big work through a borrowing rule, no small waiter existed."""
    rng = random.Random(seed)
    cap = rng.choice([2, 4, 8])
    gate, budget = make_gate(cap, held=rng.randint(0, cap // 2))
    done = 0

    async def job(i: int) -> None:
        nonlocal done
        cls = rng.choices([S, L, H], [6, 3, 1])[0]
        await asyncio.sleep(rng.random() * 0.05)
        p = await gate.acquire(req(cls, org=rng.choice("abc"), bounded=rng.random() < 0.8), deadline_s=30)
        await asyncio.sleep(rng.random() * 0.01)
        p.release()
        done += 1

    await asyncio.wait_for(asyncio.gather(*(job(i) for i in range(120))), 30)
    assert done == 120
    assert budget.max_used <= cap
    for g in gate.grants:
        if g.cls in (L, H) and g.rule in ("A2", "A4"):
            assert g.rule == "A2" or g.waiting[S] == 0, f"seed={seed}: {g}"
    assert gate.running(S) == gate.running(L) == gate.running(H) == 0
