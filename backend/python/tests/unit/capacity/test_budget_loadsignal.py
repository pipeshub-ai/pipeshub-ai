"""The ledger's overload signal: ``saturated`` with hysteresis, ``families`` equal to the ledger, published by the controller only."""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

import pytest

from app.capacity.budget_policy import Demand, Family
from app.capacity.controller import ElectedController, SaturationTracker, budget_signal
from tests.support import ledger_procs as lp

if TYPE_CHECKING:
    from pathlib import Path

    from app.capacity.member_budget import MemberBudget


@pytest.fixture
def ledger(tmp_path: Path) -> Path:
    d = tmp_path / "ledger"
    d.mkdir(mode=0o700)
    return d


def small(ledger: Path, service: str, c: int = 18) -> MemberBudget:
    return lp.open_budget(str(ledger), service, cpu_tokens=c, mem=64 << 30)


def test_saturated_turns_true_after_two_ticks_of_a_full_reserve_with_critical_waiters_and_clears_with_hysteresis(ledger: Path) -> None:
    b = small(ledger, "a")
    floors = b.floors()
    hold = []
    while (p := b.try_reserve(Demand(Family.EMBED, 2, 0, "small"))) is not None:
        hold.append(p)
    assert b.snapshot()["critical_used"] >= floors.total
    head = b.register_head(Demand(Family.DOCLING, 2, 1 << 30, "small"))
    tracker = SaturationTracker()
    snap = b.snapshot()
    assert tracker.update(snap) is False  # one tick is not enough
    assert tracker.update(snap) is True
    b.withdraw_head(head)
    clean = b.snapshot()
    assert tracker.update(clean) is True  # hysteresis: one clean tick keeps it
    assert tracker.update(clean) is False
    assert budget_signal(snap, SaturationTracker(1))["saturated"] is True


def test_a_full_budget_with_any_waiter_is_saturated_too(ledger: Path) -> None:
    b = small(ledger, "a", c=4)
    assert b.try_reserve(Demand(Family.PARSE, 4, 0, "large")) is not None
    head = b.register_head(Demand(Family.PARSE, 1, 0, "large"))
    t = SaturationTracker()
    assert [t.update(b.snapshot()) for _ in range(2)] == [False, True]
    b.withdraw_head(head)


def test_only_the_controller_publishes_the_signal(ledger: Path) -> None:
    handles = [small(ledger, f"c{i}") for i in range(3)]
    controllers = [ElectedController(h, 15) for h in handles]
    for c in controllers:
        c.tick(lambda s, d: {"n": 1})
    signals = [c.load_signal() for c in controllers]
    assert [s is not None for s in signals] == [True, False, False]  # the container is counted once


def test_families_match_ledger(ledger: Path) -> None:
    """Random weighted reservations from four processes: the published families equal the ledger's own counts."""
    rng = random.Random(5)
    handles = [small(ledger, f"p{i}", c=60) for i in range(4)]
    controller = ElectedController(handles[0], 15)
    controller.tick(lambda s, d: {})
    held = []
    expected: dict[str, int] = {f.value: 0 for f in Family}
    kinds = [(Family.DOCLING, 2, "small"), (Family.PARSE, 1, "small"), (Family.EMBED, 2, "small"),
             (Family.INSPECT, 1, None), (Family.QUERY_EMBED, 2, None), (Family.RASTER, 1, "large"),
             (Family.PARSE, 1, "large")]
    heads = []
    for _ in range(300):
        if held and rng.random() < 0.4:
            fam, w, permit = held.pop(rng.randrange(len(held)))
            permit.release()
            expected[fam.value] -= w
        else:
            fam, w, cls = rng.choice(kinds)
            permit = rng.choice(handles).try_reserve(Demand(fam, w, 0, cls))
            if permit is not None:
                held.append((fam, w, permit))
                expected[fam.value] += w
    # leave some waiters in the ledger
    for fam, w, cls in rng.sample(kinds, 3):
        heads.append(handles[1].register_head(Demand(fam, w + 40, 0, cls)))
    snap = handles[2].snapshot()
    sig = controller.load_signal()
    assert sig is not None
    assert set(sig["families"]) == {f.value for f in Family}  # exactly the enum: no per-class or per-org key
    for fam in Family:
        assert sig["families"][fam.value]["used"] == snap["families"][fam.value]["used"] == expected[fam.value]
        assert sig["families"][fam.value]["waiting"] == snap["families"][fam.value]["waiting"]
    assert sum(sig["families"][f.value]["used"] for f in Family if f is not Family.LLM) == sig["used"] == snap["used"]
    assert sum(v["waiting"] for v in sig["families"].values()) == len(heads)


def test_llm_calls_are_counted_in_the_llm_family_not_in_cpu_tokens(ledger: Path) -> None:
    a, b = small(ledger, "a"), small(ledger, "b")
    permits = [a.try_reserve(Demand(Family.LLM, 0)), b.try_reserve(Demand(Family.LLM, 0))]
    controller = ElectedController(a, 15)
    controller.tick(lambda s, d: {})
    sig = controller.load_signal()
    assert sig["families"]["llm"]["used"] == 2 and sig["used"] == 0
    assert all(permits)


def test_a_controller_that_lost_the_role_stops_publishing_the_signal(ledger: Path) -> None:
    """A missed two ticks and B took over: A still holds an epoch of its own, but the ledger names B."""
    a = small(ledger, "a")
    first = ElectedController(a, 15)
    first.tick(lambda s, d: {"n": 1}, now_ms=1_000_000)
    assert first.load_signal() is not None
    taker = small(ledger, "b")
    assert taker.elect_controller(15, now_ms=1_000_000 + 60_000) is not None  # A's beat is stale: B takes over
    assert first.is_controller  # A has not noticed yet
    assert first.load_signal() is None  # and must not publish for B's epoch
