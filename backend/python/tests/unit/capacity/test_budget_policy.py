"""The member budget's rules (pure): I1', held floors, lending, owner-first order, memory counted once."""

from __future__ import annotations

import os
import random
from typing import TYPE_CHECKING

import pytest

from app.capacity import budget_policy as bp
from app.capacity.budget_policy import (
    AdmissionPolicy,
    Demand,
    Family,
    LedgerState,
    UnitMem,
    WaiterHead,
    admit,
    may_take,
    mem_available,
    next_grant,
    plan_floors,
    slots,
)
from tests.support.budget_sim import GIB, Sim, Stream, p95

if TYPE_CHECKING:
    from collections.abc import Iterator

POLICY = AdmissionPolicy()
SEED = int(os.environ.get("CAPACITY_FUZZ_SEED", "20241115"))


def _floors(cpus, c, s_d=1.0) -> bp.Floors:
    return plan_floors(cpus, c, None, s_d, POLICY)


# ---- I1': admitted weight never above C ---------------------------------------------------------

_KINDS = [
    (Family.DOCLING, 2, "small"), (Family.DOCLING, 2, "large"), (Family.DOCLING, 2, "huge"), (Family.PARSE, 1, "small"),
    (Family.PARSE, 1, "large"), (Family.INSPECT, 1, None), (Family.RASTER, 1, None), (Family.EMBED, 2, "small"),
    (Family.EMBED, 2, "large"), (Family.QUERY_EMBED, 2, None), (Family.QUERY_PARSE, 1, None),
]


def _drive(c: int, seed: int, steps: int = 1500) -> Iterator[tuple[int, int]]:
    """Random reserve and release on a ledger state, using ``admit`` only. Yields the true total after each step."""
    rng = random.Random(seed)
    floors = _floors(max(1, c / 1.25), c)
    state = LedgerState()
    held: list[Demand] = []
    now = 1000.0
    for _ in range(steps):
        now += rng.random()
        if held and rng.random() < 0.45:
            d = held.pop(rng.randrange(len(held)))
            key = (d.family, d.cls)
            state.used[key] -= d.cpu
            if not state.used[key]:
                del state.used[key]
        else:
            fam, w, cls = rng.choice(_KINDS)
            d = Demand(fam, w, 0, cls, est_seconds=rng.choice([10, 100, 400]))
            if admit(state, d, floors, POLICY, c, 1 << 60, now).ok:
                key = (fam, cls)
                state.used[key] = state.used.get(key, 0) + w
                held.append(d)
        true_total = sum(x.cpu for x in held)
        yield true_total, len(held)


@pytest.mark.parametrize("c", [4, 5, 18, 37, 151, 303])
@pytest.mark.parametrize("seed", [SEED, SEED + 1, SEED + 2])
def test_i1_admitted(c, seed) -> None:
    print(f"seed={seed}")
    for total, n in _drive(c, seed):
        assert total <= c or n == 1, f"admitted weight {total} > C={c} with {n} units (seed {seed})"


def test_i1_needs_every_family(monkeypatch: pytest.MonkeyPatch) -> None:
    """Negative control: a ledger that leaves the raster family out of the total admits more than C."""
    real = LedgerState.total
    monkeypatch.setattr(LedgerState, "total", lambda self: real(self) - self.family_used(Family.RASTER))
    over = [t for c in (5, 18) for t, n in _drive(c, SEED) if t > c and n > 1]
    assert over, "leaving raster out of the ledger did not exceed C"


# ---- held floors, lending and ceilings ----------------------------------------------------------


def test_floors_rows() -> None:
    f = _floors(16, 18)
    assert (f.total, f.docling_warm_workers, f.docling_max_workers) == (14, 4, 7)
    assert _floors(128, 151).total == 82


def test_large_work_never_uses_the_reserve() -> None:
    f = _floors(16, 18)  # reserve 14, large work gets at most 4
    state = LedgerState()
    big = Demand(Family.DOCLING, 2, 0, "large", est_seconds=100)
    n = 0
    while admit(state, big, f, POLICY, 18, 1 << 60, 0).ok:
        state.used[(Family.DOCLING, "large")] = state.used.get((Family.DOCLING, "large"), 0) + 2
        n += 1
        assert n < 20
    assert state.big_used() == 4 == 18 - f.total  # work conservation above the floors, nothing in the reserve


def test_big_work_ceilings_and_borrow_budget() -> None:
    f = _floors(128, 151)
    nofloor = bp.Floors(dict.fromkeys(f.by_family, 0), f.desired, 1, 60)
    big = Demand(Family.PARSE, 1, 0, "large", est_seconds=100)
    recent = LedgerState(last_critical_ms=0)
    idle = LedgerState(last_critical_ms=None)
    now_recent, now_idle = 100.0, 100_000.0

    def fill(state, now, d) -> int:
        k = 0
        while admit(state, d, f, POLICY, 151, 1 << 60, now).ok:
            state.used[(d.family, d.cls)] = state.used.get((d.family, d.cls), 0) + d.cpu
            k += 1
        return state.big_used()

    assert fill(recent, now_recent, big) == 151 - f.total == 69  # C - floors binds below largeCap (76)
    f, saved = nofloor, f
    assert fill(LedgerState(last_critical_ms=0), now_recent, big) == slots(POLICY.large_cap, 151)  # 76 while small work is recent
    assert fill(idle, now_idle, big) == slots(POLICY.borrow_cap, 151)  # 129 when small work has been idle
    f = saved
    long = Demand(Family.PARSE, 1, 0, "large", est_seconds=900)
    s = LedgerState()
    taken = 0
    while admit(s, long, f, POLICY, 151, 1 << 60, now_idle).ok:
        s.used[(Family.PARSE, "large")] = s.used.get((Family.PARSE, "large"), 0) + 1
        taken += 1
    assert taken == slots(POLICY.large_min, 151)  # a long unit runs only inside the guarantee, never on borrowed tokens
    huge = Demand(Family.PARSE, 1, 0, "huge", est_seconds=100)
    s = LedgerState()
    taken = 0
    while admit(s, huge, f, POLICY, 151, 1 << 60, now_idle).ok:
        s.used[(Family.PARSE, "huge")] = s.used.get((Family.PARSE, "huge"), 0) + 1
        taken += 1
    assert taken == min(slots(POLICY.huge_cap, 151), 151 - f.total)


def _alone(stream: Stream, c: int, cpus: float) -> int:
    peak = []
    Sim(c, _floors(cpus, c), [stream], seed=3).run(60, on_step=lambda st, now: peak.append(st.total()))
    return max(peak)


def test_critical_families_share_reserve() -> None:
    """One critical family alone uses the whole reserve up to its width (16 CPU: embed 16 tokens, parse 14 jobs)."""
    embed = Stream("e", Demand(Family.EMBED, 2, 0, "small", ceiling_tokens=16), 0, (20, 30), always_waiting=20)
    parse = Stream("p", Demand(Family.PARSE, 1, 0, "small", ceiling_tokens=14), 0, (20, 30), always_waiting=30)
    assert _alone(embed, 18, 16) >= 16
    assert _alone(parse, 18, 16) >= 14


def _mix(rate: float, seed: int, c: int = 18, cpus: float = 16, seconds: float = 1800) -> list[float]:
    streams = [
        Stream("sd", Demand(Family.DOCLING, 2, 0, "small", est_seconds=30), 0.2 * cpus / 16, (5, 16)),
        Stream("big", Demand(Family.DOCLING, 2, 0, "large", est_seconds=200), 0, (60, 300), always_waiting=8),
    ]
    if rate:
        streams.append(Stream("emb", Demand(Family.EMBED, 2, 0, "small", est_seconds=3), rate, (0.5, 1.5)))
    return Sim(c, _floors(cpus, c), streams, seed=seed).run(seconds)["sd"]


@pytest.mark.parametrize("seed", [7, 31])
def test_small_token_wait_p95(seed) -> None:
    waits = _mix(0, seed)
    assert len(waits) > 200, "an empty sample would read as no wait at all (p95 of nothing is 0)"
    assert p95(waits) <= 10.0


@pytest.mark.parametrize("rate", [0, 1, 3, 4.5])
def test_small_token_wait_with_mixed_critical_families(rate) -> None:
    waits = _mix(rate, 11)
    assert len(waits) > 200
    assert p95(waits) <= 45.0


def test_oldest_first_sharing_starves_the_docling_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    """Negative control: pure oldest-first sharing (no owner-first reclaim) breaks the small-Docling wait."""
    real = bp.next_grant

    def oldest_first(state, floors, policy, c, now_s) -> bp.GrantPlan:
        plan = real(state, floors, policy, c, now_s)
        order = sorted((h for h in plan.order if h.critical), key=lambda h: (h.enqueue_ms, h.ticket))
        order += [h for h in plan.order if not h.critical]
        return bp.GrantPlan(tuple(order), plan.blocked_critical)

    import tests.support.budget_sim as sim_mod

    owner_first = p95(_mix(4.5, 11))
    monkeypatch.setattr(sim_mod, "next_grant", oldest_first)
    assert p95(_mix(4.5, 11)) >= 1.5 * owner_first


def test_without_a_held_reserve_small_work_waits() -> None:
    """Negative control: floors of zero (big work may take the reserve) push the small-Docling wait past the bound."""
    f = _floors(16, 18)
    empty = bp.Floors(dict.fromkeys(f.by_family, 0), f.desired, 1, 7)
    streams = [
        Stream("sd", Demand(Family.DOCLING, 2, 0, "small", est_seconds=30), 0.2, (5, 16)),
        Stream("big", Demand(Family.DOCLING, 2, 0, "large", est_seconds=200), 0, (60, 300), always_waiting=8),
    ]
    assert p95(Sim(18, f, streams, seed=7).run(1800)["sd"]) <= 10.0
    assert p95(Sim(18, empty, streams, seed=7).run(1800)["sd"]) > 10.0


# ---- order --------------------------------------------------------------------------------------


def _head(ticket, fam, cls, enq, cpu=2, slot=0) -> WaiterHead:
    return WaiterHead(ticket, cls, fam, cpu, 0, 100.0, enq, slot)


def test_small_in_one_process_beats_large_in_another() -> None:
    f = _floors(16, 18)
    state = LedgerState(heads=[_head(1, Family.PARSE, "large", 10, slot=1), _head(2, Family.DOCLING, "small", 50, slot=0)])
    plan = next_grant(state, f, POLICY, 18, 1.0)
    assert [h.ticket for h in plan.order] == [2, 1]
    state.heads.append(_head(3, Family.QUERY_EMBED, None, 99))
    plan = next_grant(state, f, POLICY, 18, 1.0)
    # The docling head is its family's owner below its floor, so it goes first (contracts 13.5 step 1);
    # then query_embed ahead of the remaining critical work, then big work.
    assert [h.ticket for h in plan.order] == [2, 3, 1]


def test_owner_first_then_oldest_latency_waiter() -> None:
    f = _floors(16, 18)
    # embed holds its floor already and waits oldest; docling is below its floor and waits younger
    state = LedgerState(
        used={(Family.EMBED, "small"): 2},
        heads=[_head(1, Family.EMBED, "small", 10), _head(2, Family.DOCLING, "small", 50)],
    )
    assert next_grant(state, f, POLICY, 18, 1.0).order[0].ticket == 2
    state.used = {(Family.DOCLING, "small"): 8, (Family.EMBED, "small"): 2}
    assert next_grant(state, f, POLICY, 18, 1.0).order[0].ticket == 1  # no owner below its floor: oldest first


def test_head_of_line_reserves_tokens_for_the_oldest_critical_head() -> None:
    f = _floors(16, 18)
    state = LedgerState(used={(Family.DOCLING, "small"): 8, (Family.PARSE, "large"): 4, (Family.EMBED, "small"): 4},
                        heads=[_head(1, Family.DOCLING, "small", 5, cpu=4), _head(2, Family.PARSE, "large", 6, cpu=1),
                               _head(3, Family.PARSE, "small", 7, cpu=1)])
    plan = next_grant(state, f, POLICY, 18, 1.0)  # free = 2, the Docling head needs 4
    assert plan.blocked_critical.ticket == 1
    later = {h.ticket: may_take(state, h, plan, 18) for h in state.heads}
    # Big work (2) cannot overtake: 2 free - 1 = 1 < 4 needed. The PARSE/SMALL head (3) is the owner of an unmet
    # parse floor (4 PARSE/LARGE tokens do not count towards it), so it is served first, as the owner rule says.
    assert later == {1: True, 2: False, 3: True}
    state.used[(Family.EMBED, "small")] = 2  # free = 4: the head fits, nothing is blocked
    assert next_grant(state, f, POLICY, 18, 1.0).blocked_critical is None


def test_aging_raises_the_guarantee_and_never_the_order() -> None:
    f = _floors(16, 18)
    old = _head(1, Family.PARSE, "large", 0, cpu=1)
    fresh_small = _head(2, Family.DOCLING, "small", 800_000)
    state = LedgerState(heads=[old, fresh_small], used={(Family.PARSE, "large"): 4})
    assert next_grant(state, f, POLICY, 18, 1000.0).order[0].ticket == 2  # still behind small
    big = Demand(Family.PARSE, 1, 0, "large", est_seconds=100)
    young = LedgerState(heads=[_head(1, Family.PARSE, "large", 999_000)], used={(Family.PARSE, "large"): 4})
    assert not admit(young, big, f, POLICY, 18, 1 << 60, 1000.0).ok
    assert admit(state, big, f, POLICY, 18, 1 << 60, 1000.0).ok  # aged head: guarantee 34 % = 7 tokens


# ---- memory counted once ------------------------------------------------------------------------


@pytest.mark.parametrize("young,admits,peak", [(10, 23, 61.5), (16, 17, 61.5)])
def test_mem_available_rows(young, admits, peak) -> None:
    """Young units (RSS far below the estimate) are charged their estimate. Grown units are covered by
    ``test_mem_counted_once_and_not_free_plus_reserved``."""
    est = int(1.5 * GIB)
    units = [UnitMem(est=est, actual=int(est * 0.07))] * young
    avail = mem_available(64 * GIB, 2 * GIB, 12 * GIB, units)
    n = avail // est
    assert n == admits
    assert 12 + (young + n) * 1.5 <= peak + 1e-9 <= 64


def test_mem_counted_once_and_not_free_plus_reserved() -> None:
    est = int(1.5 * GIB)
    fresh = [UnitMem(est, 0)] * 10
    grown = [UnitMem(est, int(2.0 * est))] * 10  # a unit past its estimate is charged what it uses
    a = mem_available(64 * GIB, 2 * GIB, 12 * GIB, fresh)
    b = mem_available(64 * GIB, 2 * GIB, 12 * GIB, grown)
    assert a - b == 10 * est
    assert mem_available(GIB, 2 * GIB, 0, []) == 0  # never negative


def test_memory_waits_instead_of_refusing_and_an_empty_ledger_admits_one() -> None:
    f = _floors(16, 18)
    d = Demand(Family.DOCLING, 2, int(1.5 * GIB), "small")
    busy = LedgerState(used={(Family.PARSE, "small"): 1})
    verdict = admit(busy, d, f, POLICY, 18, GIB, 0.0)
    assert (verdict.ok, verdict.reason) == (False, "memory")
    assert admit(LedgerState(), d, f, POLICY, 18, 0, 0.0).ok  # an unfittable unit still runs alone


def test_llm_dimension_is_its_own_count() -> None:
    f = _floors(16, 18)
    llm = Demand(Family.LLM, 0)
    s = LedgerState(llm_used=23, llm_cap=24, used={(Family.DOCLING, "large"): 18})
    assert admit(s, llm, f, POLICY, 18, 0, 0.0).ok  # no CPU tokens needed
    s.llm_used = 24
    assert admit(s, llm, f, POLICY, 18, 0, 0.0).reason == "llm"


def test_huge_work_is_held_to_its_own_cap_inside_the_borrow_cap() -> None:
    """With no floors held and small work idle, large work may borrow to the borrow cap; huge work stops at ``huge_cap``."""
    f = _floors(128, 151)
    nofloor = bp.Floors(dict.fromkeys(f.by_family, 0), f.desired, 1, 60)
    huge = Demand(Family.PARSE, 1, 0, "huge", est_seconds=100)
    state = LedgerState(last_critical_ms=None)
    taken = 0
    while admit(state, huge, nofloor, POLICY, 151, 1 << 60, 100_000.0).ok:
        state.used[(Family.PARSE, "huge")] = state.used.get((Family.PARSE, "huge"), 0) + 1
        taken += 1
    assert slots(POLICY.huge_cap, 151) < slots(POLICY.borrow_cap, 151)
    assert taken == slots(POLICY.huge_cap, 151)
    assert admit(state, huge, nofloor, POLICY, 151, 1 << 60, 100_000.0).reason == "class_cap"


# ---- review round 2 -----------------------------------------------------------------------------


def test_a_critical_demand_is_held_to_its_ceiling() -> None:
    """Four PARSE/SMALL tokens in use and a ceiling of four: two more must wait (the class helper leaves critical work out)."""
    f = _floors(16, 18)
    state = LedgerState(used={(Family.PARSE, "small"): 4})
    d = Demand(Family.PARSE, 2, 0, "small", ceiling_tokens=4)
    assert admit(state, d, f, POLICY, 18, 1 << 60, 1000.0).reason == "ceiling"
    assert admit(state, Demand(Family.PARSE, 2, 0, "small", ceiling_tokens=6), f, POLICY, 18, 1 << 60, 1000.0).ok
    other = Demand(Family.EMBED, 2, 0, "small", ceiling_tokens=4)
    assert admit(state, other, f, POLICY, 18, 1 << 60, 1000.0).ok, "another family's tokens are not this family's ceiling"


def test_large_work_does_not_satisfy_a_critical_floor() -> None:
    """EMBED floor 4 with four EMBED/LARGE tokens in use: the floor is still unmet, so the older EMBED/SMALL
    waiter keeps owner priority over a newer QUERY_EMBED waiter whose own floor is zero."""
    f = bp.Floors({**dict.fromkeys(bp.PRECEDENCE, 0), Family.EMBED: 4}, {}, 1, 1)
    state = LedgerState(used={(Family.EMBED, "large"): 4}, heads=[
        _head(1, Family.EMBED, "small", 10), _head(2, Family.QUERY_EMBED, None, 20)])
    assert state.critical_family_used(Family.EMBED) == 0
    assert [h.ticket for h in next_grant(state, f, POLICY, 18, 1.0).order][:2] == [1, 2]
    state.used[(Family.EMBED, "small")] = 4  # the floor is now met by critical work: no owner, query_embed leads
    assert [h.ticket for h in next_grant(state, f, POLICY, 18, 1.0).order][:2] == [2, 1]
