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

S, L, H = CostClass.SMALL, CostClass.LARGE, CostClass.HUGE
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


async def test_the_huge_cap_holds_only_while_large_work_waits() -> None:
    """Capacity 10 and only huge work: the borrow limit applies (8), not the huge cap (5)."""
    H = CostClass.HUGE
    gate = ClassGate("heavy_parse", numbers=lambda: N, capacity=lambda: PoolCapacity(10))
    permits = [await asyncio.wait_for(gate.acquire(GateRequest(cls=H, est_seconds=400.0), deadline_s=1), 1) for _ in range(6)]
    assert gate.running(H) == 6  # past the huge cap of 5: nothing large is waiting
    for p in permits:
        p.release()


async def test_an_oversized_head_is_served_at_a_capacity_of_one_while_small_work_keeps_arriving() -> None:
    """P=1, aging 0.5 s, a steady small stream: an unbounded huge head (an Office record over the unit budget) waited
    for ever (A1 needs room for two when small work waits, A2 and A4 take bounded heads only)."""
    from app.modules.parse_engine.admission import AdmissionNumbers

    numbers = AdmissionNumbers(aging_s=0.5)
    gate = ClassGate("remote_parse", numbers=lambda: numbers, capacity=lambda: PoolCapacity(1))
    stop = asyncio.Event()
    served_small = 0

    async def small_stream() -> None:
        nonlocal served_small
        while not stop.is_set():
            permit = await gate.acquire(GateRequest(cls=S, est_seconds=1.0), deadline_s=5)
            served_small += 1
            await asyncio.sleep(0.02)
            permit.release()

    streams = [asyncio.create_task(small_stream()) for _ in range(3)]
    await asyncio.sleep(0.1)
    started = time.monotonic()
    head = await gate.acquire(GateRequest(cls=H, est_seconds=900.0, bounded=False), deadline_s=4)
    waited = time.monotonic() - started
    stop.set()
    head.release()
    await asyncio.gather(*streams)
    assert waited < 2.5, waited  # aged (0.5 s), then served next; it was not served in 4 s before
    assert served_small > 0


async def test_a_fresh_unbounded_head_still_waits_for_the_running_work_and_then_runs() -> None:
    """The age bound covers an unbounded head only once it has aged (default 900 s): a fresh one waits as before."""
    gate = ClassGate("remote_parse", numbers=lambda: N, capacity=lambda: PoolCapacity(1))
    small = await gate.acquire(GateRequest(cls=S, est_seconds=1.0), deadline_s=1)
    head = asyncio.create_task(gate.acquire(GateRequest(cls=H, est_seconds=900.0, bounded=False), deadline_s=2))
    await settle()
    await asyncio.sleep(0.1)
    assert not head.done()  # one place, held by small work
    small.release()
    (await asyncio.wait_for(head, 2)).release()  # served by the ordinary rules once the place is free


async def test_the_age_bound_is_used_only_where_the_ordinary_rules_cannot_grant() -> None:
    """At a capacity of 4 a backlog of aged unsplittable heads is not served by the age bound ahead of small work."""
    from app.modules.parse_engine.admission import AdmissionNumbers

    gate = ClassGate("remote_parse", numbers=lambda: AdmissionNumbers(aging_s=0.05), capacity=lambda: PoolCapacity(4))
    running = [await gate.acquire(GateRequest(cls=S, est_seconds=1.0), deadline_s=1) for _ in range(4)]
    heads = [asyncio.create_task(gate.acquire(GateRequest(cls=H, est_seconds=900.0, bounded=False), deadline_s=3)) for _ in range(2)]
    smalls = [asyncio.create_task(gate.acquire(GateRequest(cls=S, est_seconds=1.0), deadline_s=3)) for _ in range(3)]
    await asyncio.sleep(0.2)  # the heads have aged
    for p in running:
        p.release()
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.2)
    assert all(g.rule != "A5" for g in gate.grants), [g.rule for g in gate.grants]
    for t in (*heads, *smalls):
        t.cancel()
    await asyncio.gather(*heads, *smalls, return_exceptions=True)


async def test_an_aged_unsplittable_head_is_served_next_even_while_a_priority_small_waiter_waits() -> None:
    """It is overdue: a priority-lane consumer's own stream is priority too, so a rule that waited for priority work to
    drain would never run for that consumer."""
    from app.modules.parse_engine.admission import AdmissionNumbers

    gate = ClassGate("remote_parse", numbers=lambda: AdmissionNumbers(aging_s=0.05), capacity=lambda: PoolCapacity(1))
    running = await gate.acquire(GateRequest(cls=S, est_seconds=1.0), deadline_s=1)
    head = asyncio.create_task(gate.acquire(GateRequest(cls=H, est_seconds=900.0, bounded=False), deadline_s=3))
    urgent = asyncio.create_task(gate.acquire(GateRequest(cls=S, est_seconds=1.0, priority=True), deadline_s=3))
    await asyncio.sleep(0.2)  # the head has aged; a priority small record waits
    running.release()
    done, _ = await asyncio.wait({urgent, head}, return_when=asyncio.FIRST_COMPLETED, timeout=2)
    assert head in done and urgent not in done  # the overdue head runs next, then the priority record
    (await head).release()
    (await asyncio.wait_for(urgent, 2)).release()


async def test_a_priority_unsplittable_head_is_served_at_capacity_one_and_goes_before_an_older_one() -> None:
    """A parse-now Office document over the unit budget is an aged, unsplittable, priority head: the ordinary rules never
    grant it at a capacity of 1 while small work waits, so the age bound must, and ahead of an older non-priority head."""
    from app.modules.parse_engine.admission import AdmissionNumbers

    gate = ClassGate("remote_parse", numbers=lambda: AdmissionNumbers(aging_s=0.05), capacity=lambda: PoolCapacity(1))
    running = await gate.acquire(GateRequest(cls=S, est_seconds=1.0), deadline_s=1)
    older = asyncio.create_task(gate.acquire(GateRequest(cls=H, est_seconds=900.0, bounded=False), deadline_s=3))
    await settle()
    urgent = asyncio.create_task(gate.acquire(GateRequest(cls=H, est_seconds=900.0, bounded=False, priority=True), deadline_s=3))
    small = asyncio.create_task(gate.acquire(GateRequest(cls=S, est_seconds=1.0), deadline_s=3))
    await asyncio.sleep(0.2)  # both heads have aged; small work waits
    running.release()
    done, _ = await asyncio.wait({older, urgent, small}, return_when=asyncio.FIRST_COMPLETED, timeout=2)
    assert urgent in done and older not in done, [t is urgent for t in done]  # the priority head, not the older one
    (await urgent).release()
    (await asyncio.wait_for(older, 2)).release()
    (await asyncio.wait_for(small, 2)).release()


@pytest.mark.parametrize("head_priority", [False, True])
async def test_an_unsplittable_head_is_served_at_capacity_one_under_a_priority_small_stream(head_priority: bool) -> None:
    """A priority-lane consumer: every record is priority, the head included."""
    from app.modules.parse_engine.admission import AdmissionNumbers

    gate = ClassGate("remote_parse", numbers=lambda: AdmissionNumbers(aging_s=0.4), capacity=lambda: PoolCapacity(1))
    stop = asyncio.Event()

    async def stream() -> None:
        async def one() -> None:
            try:
                p = await gate.acquire(GateRequest(cls=S, priority=True, est_seconds=1.0), deadline_s=10)
            except AdmissionTimeout:
                return
            await asyncio.sleep(0.02)
            p.release()

        tasks = []
        while not stop.is_set():
            tasks.append(asyncio.create_task(one()))
            await asyncio.sleep(0.01)
        await asyncio.gather(*tasks, return_exceptions=True)

    runner = asyncio.create_task(stream())
    await asyncio.sleep(0.2)
    started = time.monotonic()
    head = await gate.acquire(GateRequest(cls=H, bounded=False, priority=head_priority, est_seconds=900.0), deadline_s=4)
    waited = time.monotonic() - started
    stop.set()
    head.release()
    await runner
    assert waited < 3.0, waited  # aged (0.4 s), then served next; it was not served in 4 s while any priority waiter existed


async def test_the_age_bound_waits_for_the_aging_time() -> None:
    """Not before: a head that has waited less than ``aging_s`` is left to the ordinary rules."""
    from app.modules.parse_engine.admission import AdmissionNumbers

    gate = ClassGate("remote_parse", numbers=lambda: AdmissionNumbers(aging_s=0.8), capacity=lambda: PoolCapacity(1))
    stop = asyncio.Event()

    async def one() -> None:
        while not stop.is_set():
            p = await gate.acquire(GateRequest(cls=S, est_seconds=1.0), deadline_s=10)
            await asyncio.sleep(0.01)
            p.release()

    streams = [asyncio.create_task(one()) for _ in range(3)]
    await asyncio.sleep(0.1)
    started = time.monotonic()
    head = await gate.acquire(GateRequest(cls=H, bounded=False, est_seconds=900.0), deadline_s=5)
    waited = time.monotonic() - started
    stop.set()
    head.release()
    await asyncio.gather(*streams, return_exceptions=True)
    assert 0.6 <= waited < 3.0, waited  # held back until it aged (0.8 s), then served
