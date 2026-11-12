"""The strike ledger: exact windows, scopes and bounds, on an injected clock (nothing sleeps)."""

from __future__ import annotations

import time

import pytest

from app.connectors.sources.localKB.push import ledger as L
from app.connectors.sources.localKB.push.contract import PushRefusal

U = ("org1", "user1")
V = ("org1", "user2")


class Clock:
    def __init__(self) -> None:
        self.t = 1_000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def ledger(clock) -> L.PushLedger:
    return L.PushLedger(clock=clock)


def refusal(fn, *args) -> PushRefusal | None:
    try:
        fn(*args)
    except PushRefusal as r:
        return r
    return None


def test_two_strikes_refuse_the_pair_for_ten_minutes(ledger, clock) -> None:
    ledger.strike(U, "r:k1", L.IMAGE)
    assert refusal(ledger.check_pair, U, "r:k1") is None  # one strike refuses nothing
    clock.t += 599
    ledger.strike(U, "r:k1", L.IMAGE)
    r = refusal(ledger.check_pair, U, "r:k1")
    assert (r.status, r.reason) == (503, "PUSH_TIMEOUT") and 0 < r.extra[
        "retryAfter"
    ] <= 600
    clock.t += 599
    assert (
        refusal(ledger.check_pair, U, "r:k1") is not None
    )  # 10 min run from the second strike
    clock.t += 2
    assert refusal(ledger.check_pair, U, "r:k1") is None
    assert refusal(ledger.check_pair, V, "r:k1") is None  # another caller


def test_a_second_strike_outside_the_window_does_not_refuse(ledger, clock) -> None:
    ledger.strike(U, "r:k1", L.IMAGE)
    clock.t += 601
    ledger.strike(U, "r:k1", L.IMAGE)
    assert refusal(ledger.check_pair, U, "r:k1") is None


def test_a_body_pair_is_answered_as_a_complex_body(ledger) -> None:
    ledger.strike(U, "b:abc", L.TEXT)
    ledger.strike(U, "b:abc", L.TEXT)
    r = refusal(ledger.check_pair, U, "b:abc")
    assert (r.status, r.extra["limit"]) == (413, "complexity")


def test_four_distinct_keys_suspend_images_only(ledger, clock) -> None:
    for i in range(3):
        ledger.strike(U, f"r:k{i}", L.IMAGE)
        clock.t += 10
    assert refusal(ledger.check_images, U) is None
    ledger.strike(U, "r:k3", L.IMAGE)
    r = refusal(ledger.check_images, U)
    assert (r.status, r.reason) == (429, "PUSH_SUSPENDED") and "mage" in r.message
    assert 0 < r.extra["retryAfter"] <= 600
    assert refusal(ledger.check_all, U) is None  # text-only pushes stay
    assert refusal(ledger.check_images, V) is None
    clock.t += (
        570  # the first strike (30 s ago) leaves the window: three distinct keys remain
    )
    assert refusal(ledger.check_images, U) is None


def test_the_same_key_four_times_is_not_four_keys(ledger, clock) -> None:
    for _ in range(4):
        ledger.strike(U, "r:same", L.IMAGE)
        clock.t += 1  # four distinct moments: only the key can make them one
    assert refusal(ledger.check_images, U) is None


def test_three_distinct_bodies_suspend_every_push(ledger) -> None:
    for i in range(2):
        ledger.strike(U, f"b:{i}", L.TEXT)
    assert refusal(ledger.check_all, U) is None
    ledger.strike(U, "b:2", L.TEXT)
    r = refusal(ledger.check_all, U)
    assert (r.status, r.reason) == (429, "PUSH_SUSPENDED")
    assert refusal(ledger.check_all, V) is None


def test_a_trailing_byte_evades_only_the_pair_refusal(ledger) -> None:
    """Every variant is a new key, so the distinct-key rule is what bounds an evader."""
    for i in range(3):
        assert refusal(ledger.check_pair, U, f"b:variant{i}") is None
        ledger.strike(U, f"b:variant{i}", L.TEXT)
    assert refusal(ledger.check_all, U) is not None


def test_a_120_second_refusal_is_not_a_strike(ledger, clock) -> None:
    for i in range(8):
        ledger.refuse_pair(U, f"r:slow{i}")
    assert (
        refusal(ledger.check_images, U) is None and refusal(ledger.check_all, U) is None
    )
    r = refusal(ledger.check_pair, U, "r:slow0")
    assert (r.status, r.reason) == (503, "PUSH_TIMEOUT") and r.extra[
        "retryAfter"
    ] <= 120
    clock.t += 121
    assert refusal(ledger.check_pair, U, "r:slow0") is None


def test_lease_seconds_are_a_budget_that_a_long_run_under_the_kill_still_spends(
    ledger, clock
) -> None:
    for _ in range(2):
        ledger.lease(U, 49.0)  # never killed: 49 s of the shared worker each
        clock.t += 1
    assert refusal(ledger.check_all, U) is None
    ledger.lease(U, 49.0)  # 147 s
    assert refusal(ledger.check_all, U) is None
    ledger.lease(U, 4.0)  # 151 s
    r = refusal(ledger.check_all, U)
    assert (r.status, r.reason) == (503, "PUSH_TIMEOUT") and 0 < r.extra[
        "retryAfter"
    ] <= 600
    assert refusal(ledger.check_all, V) is None  # another caller is served meanwhile
    clock.t += 600  # the window frees
    assert refusal(ledger.check_all, U) is None


def test_fast_pushes_never_reach_the_lease_budget(ledger, clock) -> None:
    for _ in range(60):
        ledger.lease(U, 0.4)
        clock.t += 5
    assert refusal(ledger.check_all, U) is None


def test_suspect_events_refuse_after_four_by_scope(ledger) -> None:
    for _ in range(3):
        ledger.suspect(U, L.IMAGE)
    assert refusal(ledger.check_images, U) is None
    ledger.suspect(U, L.IMAGE)
    assert refusal(ledger.check_images, U).reason == "PUSH_TIMEOUT"
    assert (
        refusal(ledger.check_all, U) is None
    )  # an image problem does not stop text pushes
    for _ in range(4):
        ledger.suspect(V, L.TEXT)
    assert refusal(ledger.check_all, V).reason == "PUSH_TIMEOUT"
    assert refusal(ledger.check_images, V) is None


def test_the_accountant_maps_each_event(ledger) -> None:
    img = ledger.accountant(U, "r:x", L.IMAGE)
    img("strike")
    img("strike")
    assert refusal(ledger.check_pair, U, "r:x") is not None
    txt = ledger.accountant(V, "b:y", L.TEXT)
    txt("suspect")
    assert (
        refusal(ledger.check_pair, V, "b:y") is None
    )  # a replayed text body that survived is not held against its key
    txt("overrun")
    assert refusal(ledger.check_pair, V, "b:y") is not None
    txt("lease", 3.0)


def test_bounds_lru_ttl_and_row_cap(clock) -> None:
    ledger = L.PushLedger(clock=clock, max_users=4)
    for i in range(6):
        ledger.strike(("o", f"u{i}"), "k", L.IMAGE)
    assert ledger.users() == 4
    one = ("o", "u5")
    for i in range(200):
        ledger.strike(one, f"k{i}", L.IMAGE)
    assert len(ledger._rows[one].strikes) <= L.MAX_ROWS
    clock.t += 601
    ledger.strike(one, "fresh", L.IMAGE)
    assert len(ledger._rows[one].strikes) == 1  # the old ones aged out


def test_checks_do_not_touch_the_lru(clock) -> None:
    """A flood of checks by unknown callers must not evict a suspended one (and must not create entries)."""
    ledger = L.PushLedger(clock=clock, max_users=3)
    for i in range(4):
        ledger.strike(("o", "bad"), f"b:{i}", L.TEXT)
    for i in range(5_000):
        ledger.check_all(("o", f"stranger{i}"))
        ledger.check_images(("o", f"stranger{i}"))
        ledger.check_pair(("o", f"stranger{i}"), "k")
    assert ledger.users() == 1
    assert refusal(ledger.check_all, ("o", "bad")) is not None


def test_a_check_is_cheap_at_4096_callers() -> None:
    ledger = L.PushLedger()
    for i in range(4096):
        ledger.strike(("o", f"u{i}"), "k", L.IMAGE)
    samples = []
    for i in range(2_000):
        t = time.perf_counter()
        ledger.check_all(("o", f"u{i % 4096}"))
        ledger.check_images(("o", f"u{i % 4096}"))
        samples.append(time.perf_counter() - t)
    samples.sort()
    p95 = samples[int(len(samples) * 0.95)]
    print(f"ledger check p95 {p95 * 1e6:.1f} us")
    assert p95 < 250e-6  # measured well under 50 us; the margin is for a shared host


def test_many_small_charges_cannot_push_a_large_one_out_of_the_budget(
    ledger, clock
) -> None:
    """The oldest charges are merged, never dropped: 49 s followed by more small charges than there are rows is still 49 s."""
    ledger.lease(U, 49.0)
    for _ in range(L.MAX_ROWS + 20):
        clock.t += 0.01
        ledger.lease(U, 0.01)
    assert len(ledger._rows[U].lease) <= L.MAX_ROWS
    assert sum(s for _, s in ledger._rows[U].lease) >= 49.0
    for _ in range(
        3
    ):  # three more big runs: 4 x 49 s > 150 s must be refused whatever came between
        clock.t += 1
        ledger.lease(U, 49.0)
    assert refusal(ledger.check_all, U).reason == "PUSH_TIMEOUT"


def test_merged_charges_stop_counting_once_they_leave_the_window(ledger, clock) -> None:
    """Three 49 s runs, then a small charge every 2 s: once the big runs are out of the window, the caller is served."""
    start = clock.t
    for _ in range(3):
        ledger.lease(U, 49.0)
        clock.t += 1
    while clock.t < start + L.WINDOW_S + L.LEASE_SLOT_S + 5:
        clock.t += 2.0
        ledger.lease(U, 0.1)  # 30 s per window: far under the budget
    assert len(ledger._rows[U].lease) <= L.MAX_ROWS
    assert refusal(ledger.check_all, U) is None, "the 147 s left the window; merging must not carry them forward"


def test_merging_never_counts_a_charge_less_and_at_most_one_slot_more(ledger, clock) -> None:
    """Against an exact record of every charge: never fewer live seconds (the budget is a security bound), never
    more than the charges of the window widened by one slot."""
    import random

    rnd, charges = random.Random(5720), []
    for _ in range(2000):
        clock.t += rnd.choice((0.01, 0.2, 1.0, 3.0, 12.0))
        seconds = rnd.choice((0.01, 0.3, 2.0, 49.0))
        ledger.lease(U, seconds)
        charges = [c for c in charges if c[0] > clock.t - 2 * L.WINDOW_S] + [(clock.t, seconds)]
        for at in (clock.t, clock.t + rnd.uniform(0, L.WINDOW_S + 2 * L.LEASE_SLOT_S)):
            counted = sum(s for t, s in ledger._rows[U].lease if t > at - L.WINDOW_S)
            exact = sum(s for t, s in charges if t > at - L.WINDOW_S)
            widened = sum(s for t, s in charges if t > at - L.WINDOW_S - L.LEASE_SLOT_S)
            assert exact - 1e-6 <= counted <= widened + 1e-6
        assert len(ledger._rows[U].lease) <= L.MAX_ROWS


def test_the_flush_pattern_of_big_charges_between_floods_of_small_ones_is_refused(
    ledger, clock
) -> None:
    """Twelve rounds of one 49 s charge and 65 tiny ones: the budget is hit within the window, as with four plain 49 s runs."""
    hit = None
    for i in range(12):
        ledger.lease(U, 49.0)
        clock.t += 49.5
        for _ in range(65):
            ledger.lease(U, 0.01)
            clock.t += 0.01
        if refusal(ledger.check_all, U) is not None:
            hit = i
            break
    assert hit is not None and hit <= 3


def test_checks_by_an_existing_caller_do_not_promote_its_row(clock) -> None:
    """Row order is write order: a caller who only gets checked is still the first to go when the table is full."""
    ledger = L.PushLedger(clock=clock, max_users=3)
    for name in ("a", "b", "c"):
        ledger.strike(("o", name), "k", L.IMAGE)
    for _ in range(10):
        ledger.check_all(("o", "a"))
        ledger.check_images(("o", "a"))
        ledger.check_pair(("o", "a"), "k")
    ledger.strike(("o", "d"), "k", L.IMAGE)
    assert ("o", "a") not in ledger._rows and ("o", "b") in ledger._rows
