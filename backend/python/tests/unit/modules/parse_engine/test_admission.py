"""The reserve/cap/aging policy: tables, then generated worlds with printed seeds."""
from __future__ import annotations

import math
import os
import random

import pytest

from app.modules.parse_engine.admission import (
    AdmissionNumbers,
    AdmissionNumbersError,
    ClassDemand,
    next_grant,
    numbers_from_env,
    numbers_from_mapping,
    slots,
)
from app.modules.parse_engine.cost import CostClass
from tests.support.parse_admission_sim import World, simulate

S, L, H = CostClass.SMALL, CostClass.LARGE, CostClass.HUGE
N = AdmissionNumbers()


def d(running: int = 0, waiting: int = 0, oldest: float = 0.0, bounded: bool = True) -> ClassDemand:
    return ClassDemand(running, waiting, oldest, bounded)


def grant(cap: int, *, s=None, l=None, h=None, since=math.inf, held=0, elsewhere=False, numbers=N) -> tuple[CostClass, str] | None:  # noqa: E741  # noqa: ANN201
    demand = {S: s or d(), L: l or d(), H: h or d()}
    g = next_grant(numbers, cap, demand, since_small_s=since, held_floors=held, small_waiting_elsewhere=elsewhere)
    return None if g is None else (g.cls, g.rule)


def test_slots() -> None:
    assert [slots(0.2, p) for p in (1, 4, 5, 10, 16)] == [1, 1, 1, 2, 3]
    assert slots(0.85, 10) == 8


def test_all_large() -> None:
    # bounded big work, small demand recent: it may hold large_cap of P, no more
    for running in range(0, 5):
        assert grant(10, l=d(running, 3), since=10.0) == ((L, "A1" if running < 2 else "A4"))
    assert grant(10, l=d(5, 3), since=10.0) is None
    # after small has been absent for small_recent_s it may borrow up to borrow_cap
    assert grant(10, l=d(5, 3), since=N.small_recent_s + 1) == (L, "A4")
    assert grant(10, l=d(8, 3), since=N.small_recent_s + 1) is None
    # an unbounded head only ever gets its guarantee
    assert grant(10, l=d(0, 3, bounded=False), since=1e9) == (L, "A1")
    assert grant(10, l=d(1, 3, bounded=False), since=1e9) == (L, "A1")
    assert grant(10, l=d(2, 3, bounded=False), since=1e9) is None
    # huge: its own cap applies while LARGE waits, and never idles a token when LARGE does not
    assert grant(10, l=d(2, 1), h=d(6, 1), since=1e9) is None  # big at borrow_cap (8 of 10)
    assert grant(10, h=d(3, 1), since=1e9) == (H, "A4")
    assert grant(10, l=d(0, 1), h=d(5, 1)) == (L, "A4")  # LARGE ahead of HUGE


def test_huge_cap_only_while_large_waits() -> None:
    n = AdmissionNumbers(huge_cap=0.3)
    # LARGE waits with an unbounded head: the borrowing rule cannot serve it, and huge is at its cap
    assert grant(10, l=d(3, 1, bounded=False), h=d(3, 2), since=1e9, numbers=n) is None
    # LARGE absent: huge borrows past huge_cap, up to the borrow cap
    assert grant(10, h=d(3, 2), since=1e9, numbers=n) == (H, "A4")


def test_all_small() -> None:
    for running in range(10):
        assert grant(10, s=d(running, 2)) == (S, "A3")
    assert grant(10, s=d(10, 2)) is None  # never above capacity


def test_small_first_and_capacity_one() -> None:
    assert grant(10, s=d(0, 1), l=d(3, 1)) == (S, "A3")  # big already has its guarantee
    assert grant(10, s=d(0, 1), l=d(0, 1)) == (L, "A1")  # big below its guarantee goes first
    # at capacity 1 small is not starved by the guarantee
    assert grant(1, s=d(0, 1), l=d(0, 1)) == (S, "A3")
    assert grant(1, l=d(0, 1)) == (L, "A1")


def test_a4_never_while_small_waits_anywhere() -> None:
    assert grant(10, l=d(3, 1), since=1e9, elsewhere=True) is None
    assert grant(10, l=d(3, 1), since=1e9, elsewhere=False) == (L, "A4")


def test_held_floors_are_not_lent_to_big() -> None:
    assert grant(10, l=d(5, 1), since=1e9, held=5) is None  # P - H = 5, all in use
    assert grant(10, l=d(4, 1), since=1e9, held=5) == (L, "A4")


def test_aging() -> None:
    old = N.aging_s + 1
    # big at its guarantee, small waiting: the aged head lifts big to aged_min
    assert grant(10, s=d(0, 5), l=d(2, 1, oldest=old)) == (L, "A2")
    assert grant(10, s=d(0, 5), l=d(2, 1, oldest=old - 5)) == (S, "A3")
    assert grant(10, s=d(0, 5), l=d(3, 1, oldest=old)) == (S, "A3")  # aged_min = 3 slots of 10
    # the aged head must still be bounded, and inside the tokens above the floors
    assert grant(10, s=d(0, 5), l=d(2, 1, oldest=old, bounded=False)) == (S, "A3")
    assert grant(10, s=d(0, 5), l=d(2, 1, oldest=old), held=9) == (S, "A3")
    # capacity 1: aging is what gets big in at all while small keeps waiting
    assert grant(1, s=d(0, 5), l=d(0, 1, oldest=old)) == (L, "A2")
    assert grant(1, s=d(0, 5), l=d(0, 1, oldest=old - 5)) == (S, "A3")


def test_brake_p2() -> None:
    # capacity halved under a big load: no borrowing grant until big is back under P - H
    assert grant(4, l=d(3, 2), since=1e9, held=2) is None
    assert grant(4, l=d(1, 2), since=1e9, held=2) == (L, "A4")
    assert grant(4, s=d(2, 0), l=d(2, 2)) is None  # P reached: nothing past C


def _random_state(rng: random.Random) -> tuple[AdmissionNumbers, int, dict[CostClass, ClassDemand], dict]:
    cap = rng.choice([1, 2, 3, 4, 8, 16, 32, 64])
    held = rng.randint(0, max(0, cap - 1))
    demand = {}
    for c in CostClass:
        waiting = rng.choice([0, 0, 1, 3])
        demand[c] = ClassDemand(rng.randint(0, cap), waiting, rng.choice([0.0, 100.0, 1000.0, 5000.0]), rng.random() < 0.7)
    extra = {
        "since_small_s": rng.choice([0.0, 10.0, 700.0, math.inf]),
        "held_floors": held,
        "small_waiting_elsewhere": rng.random() < 0.3,
    }
    return N, cap, demand, extra


def _check_state(n: AdmissionNumbers, cap: int, demand, extra) -> list[str]:
    bad = []
    g = next_grant(n, cap, demand, **extra)
    total = sum(x.running for x in demand.values())
    big_used = demand[L].running + demand[H].running
    big_waits = demand[L].waiting + demand[H].waiting > 0
    held = extra["held_floors"]
    above = max(0, cap - held)
    if g is not None:
        if total >= cap:
            bad.append("granted past capacity")
        if demand[g.cls].waiting == 0:
            bad.append("granted a class with nobody waiting")
        if g.cls in (L, H) and g.rule in ("A2", "A4"):
            if big_used + 1 > above:
                bad.append("P1/P2: borrowed a held floor")
            if not any(demand[c].head_bounded for c in (L, H) if demand[c].waiting):
                bad.append("borrowed with only unbounded heads")
        if g.rule == "A4" and (demand[S].waiting or extra["small_waiting_elsewhere"]):
            bad.append("A4 with small waiting")
        if g.rule == "A3" and g.cls is not S:
            bad.append("A3 not small")
        if g.cls is H and demand[L].waiting and g.rule == "A4" and demand[H].running >= slots(n.huge_cap, cap):
            bad.append("huge past huge_cap while large waits")
    # P3: a big guarantee is served as soon as a token is free (capacity >= 2)
    if cap >= 2 and total < cap and big_waits and big_used < slots(n.large_min, cap) and g is None:
        bad.append("P3: guarantee not served")
    if cap >= 2 and total < cap and big_waits and big_used < slots(n.large_min, cap) and g is not None and g.cls is S:
        bad.append("P3: small served ahead of the big guarantee")
    # P5: no token above the floors idles while bounded eligible big work waits and nothing small waits
    if (
        total < cap
        and not demand[S].waiting
        and not extra["small_waiting_elsewhere"]
        and big_used + 1 <= above
        and g is None
    ):
        recent = demand[S].running > 0 or extra["since_small_s"] < n.small_recent_s
        capn = slots(n.large_cap if recent else n.borrow_cap, cap)
        for c in (L, H):
            if demand[c].waiting and demand[c].head_bounded and big_used + 1 <= capn:
                if c is H and demand[L].waiting and demand[H].running >= slots(n.huge_cap, cap):
                    continue
                if c is H and demand[L].waiting and demand[L].head_bounded:
                    continue
                bad.append(f"P5: idle token above the floors with {c} waiting")
    # P6: more capacity never turns a grant into none
    if g is not None:
        g2 = next_grant(n, cap + 1, demand, **extra)
        if g2 is None:
            bad.append("P6: raising P removed a grant")
    return bad


def _worlds() -> int:
    return int(os.environ.get("PARSE_ADMISSION_WORLDS", "200"))


@pytest.mark.parametrize("seed", [20260515, 7])
def test_policy_properties_generated(seed: int) -> None:
    rng = random.Random(seed)
    failures = []
    for i in range(_worlds()):
        n, cap, demand, extra = _random_state(rng)
        bad = _check_state(n, cap, demand, extra)
        if bad:
            failures.append((seed, i, cap, demand, extra, bad))
    assert not failures, f"seed={seed}: {failures[:3]}"


@pytest.mark.parametrize("seed", range(3))
def test_policy_in_simulated_worlds(seed: int) -> None:
    rng = random.Random(1000 + seed)
    for i in range(12):
        cap = rng.choice([2, 3, 4, 8, 16])
        w = World(
            capacity=cap,
            held=rng.randint(0, cap // 2),
            small_rate=rng.uniform(0.01, 0.1) * cap / 8,
            large_rate=rng.uniform(0.005, 0.05) * cap / 8,
            huge_rate=rng.choice([0, 0.003]) * cap / 8,
            unbounded_share=rng.choice([0, 0.3]),
            drops=((8000.0, max(1, cap // 2)),) if rng.random() < 0.5 else (),
            handback_after_s=rng.choice([None, 600.0]),
            seed=rng.randrange(10**6),
        )
        r = simulate(w)
        assert not r.violations, f"seed={1000 + seed} world={i} {w}: {r.violations[:3]}"


def _starved_world(*, keeps: bool, seed: int, horizon: float = 60_000.0) -> World:
    # one token, small demand beyond what it serves, big hands back every 400 s while it waits
    return World(
        capacity=1,
        small_rate=0.15,  # overload: small is always waiting

        small_service=10.0,
        large_rate=0.004,
        large_service=60.0,
        handback_after_s=400.0,
        handback_keeps_first_seen=keeps,
        horizon=horizon,
        seed=seed,
    )


@pytest.mark.parametrize("seed", range(4))
def test_aging_bounds_the_big_head(seed: int) -> None:
    w = _starved_world(keeps=True, seed=seed)
    r = simulate(w)
    assert not r.violations
    assert r.big_granted > 0
    assert r.max_big_head_wait <= w.numbers.aging_s + 2 * r.max_service, f"seed={seed}"


def test_aging_negative_control_hand_backs_reset_first_seen() -> None:
    # A hand-back that restarts the clock never lets the head age: the bound is blown. The simulation runs on its own
    # clock, so this is deterministic; one world over a horizon long enough for four hand-backs shows it, which keeps
    # the test cheap (the full-horizon runs are the bounded-head cases above).
    horizon = 10_000.0
    worst = simulate(_starved_world(keeps=False, seed=0, horizon=horizon)).max_big_head_wait
    w = _starved_world(keeps=True, seed=0, horizon=horizon)
    assert worst > w.numbers.aging_s + 2 * simulate(w).max_service


def test_big_waiting_progresses_when_small_saturates() -> None:
    # P >= 2: big always gets its guarantee, whatever small does
    w = World(capacity=4, small_rate=0.6, small_service=10.0, large_rate=0.01, large_service=100.0, seed=3)
    r = simulate(w)
    assert not r.violations
    assert r.big_granted > 0 and r.rule_counts.get("A1", 0) > 0


# -- config validation -----------------------------------------------------


def test_numbers_validate() -> None:
    n = numbers_from_mapping({"largeMin": 0.1, "agedMin": 0.3, "largeCap": 0.6, "borrowCap": 0.9})
    assert (n.large_min, n.aged_min, n.large_cap, n.borrow_cap) == (0.1, 0.3, 0.6, 0.9)
    for bad in (
        {"largeMin": 0.6},  # large_min > aged_min
        {"borrowCap": 1.5},
        {"largeCap": 0.9, "borrowCap": 0.8},
        {"agingS": 0},
        {"hugeCap": 0},
        {"largeMin": "x"},
        {"agingS": float("nan")},
        {"smallReserve": 0.9, "largeMin": 0.2},
        {"nope": 1},
    ):
        with pytest.raises(AdmissionNumbersError):
            numbers_from_mapping(bad)
    with pytest.raises(AdmissionNumbersError):
        numbers_from_mapping([1, 2])


def test_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    assert numbers_from_env() == AdmissionNumbers()
    monkeypatch.setenv("PARSE_ADMISSION_LARGE_MIN", "0.1")
    monkeypatch.setenv("PARSE_ADMISSION_AGING_S", "600")
    n = numbers_from_env()
    assert n.large_min == 0.1 and n.aging_s == 600.0
    monkeypatch.setenv("PARSE_ADMISSION_LARGE_CAP", "0.15")  # below aged_min: the whole set is ignored
    assert numbers_from_env() == AdmissionNumbers()
