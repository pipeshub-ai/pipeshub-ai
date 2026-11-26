"""P6.5 pure rules: availability along a path, grants, crossing, children's caps."""

from __future__ import annotations

import pytest

from harness_core.budget.errors import (
    BudgetExhausted,
    BudgetInsufficient,
    BudgetShareExceedsRemaining,
)
from harness_core.budget.tree import (
    AccountState,
    available,
    child_cap,
    crossed,
    grant,
    reserve_for_child,
    usd_micros,
)
from harness_core.budget.types import ZERO, Amount, Available, Cap, RunAccountRef

ROOT = RunAccountRef("o", "r")
CHILD = RunAccountRef("o", "c")


def state(ref: RunAccountRef, cap: Cap, committed: Amount = ZERO, reserved: Amount = ZERO) -> AccountState:
    return AccountState(ref, None, cap, committed, reserved)


def test_available_is_the_least_over_the_path() -> None:
    path = [state(CHILD, Cap(300, None), Amount(50, 0)), state(ROOT, Cap(1_000, 90), Amount(200, 10), Amount(600, 5))]
    assert available(path) == Available(200, 75)
    assert available([state(ROOT, Cap())]) == Available(None, None)


@pytest.mark.parametrize(
    ("left", "committed", "requested", "minimum", "expected"),
    [
        (1_000, 0, 300, 100, 300),
        (1_000, 0, 1_300, 100, 1_000),
        (1_000, 950, 300, 10, 50),
        (1_000, 1_000, 300, 0, BudgetExhausted),
        (1_000, 950, 300, 100, BudgetInsufficient),
        (1_000, 1_000, 0, 0, 0),
    ],
)
def test_grant_table(left: int, committed: int, requested: int, minimum: int, expected: int | type[Exception]) -> None:
    path = [state(ROOT, Cap(left, None), Amount(committed, 0))]
    if isinstance(expected, int):
        assert grant(path, Amount(requested, 0), Amount(minimum, 0)) == Amount(expected, 0)
    else:
        with pytest.raises(expected):
            grant(path, Amount(requested, 0), Amount(minimum, 0))


def test_reserved_but_unspent_is_insufficient_not_exhausted() -> None:
    path = [state(ROOT, Cap(1_000, None), ZERO, Amount(1_000, 0))]
    with pytest.raises(BudgetInsufficient):
        grant(path, Amount(10, 0), Amount(1, 0))


def test_crossed_only_when_a_cap_is_reached_now() -> None:
    before = [state(ROOT, Cap(100, 10), Amount(90, 0))]
    assert crossed(before, [state(ROOT, Cap(100, 10), Amount(100, 0))])
    assert not crossed(before, [state(ROOT, Cap(100, 10), Amount(99, 9))])
    assert crossed(before, [state(ROOT, Cap(100, 10), Amount(90, 10))])
    over = [state(ROOT, Cap(100, None), Amount(150, 0))]
    assert not crossed(over, [state(ROOT, Cap(100, None), Amount(160, 0))])


@pytest.mark.parametrize(
    ("parent", "share", "ok"),
    [
        (Available(500, None), Cap(400, None), True),
        (Available(500, None), Cap(600, None), False),
        (Available(500, 10), Cap(None, None), True),
        (Available(0, 10), Cap(None, None), False),
        (Available(0, 10), Cap(None, 5), False),
        (Available(None, None), Cap(10**9, None), True),
    ],
)
def test_reserve_for_child(parent: Available, share: Cap, ok: bool) -> None:
    if ok:
        assert reserve_for_child(parent, share) == share
    else:
        with pytest.raises(BudgetShareExceedsRemaining):
            reserve_for_child(parent, share)


def test_child_cap_is_a_share_only_when_smaller_than_the_parents_pin() -> None:
    assert child_cap(Cap(5_000_000, 1_000_000), Cap(5_000_000, 1_000_000)) == Cap(None, None)
    assert child_cap(Cap(5_000_000, 1_000_000), Cap(1_000_000, 250_000)) == Cap(1_000_000, 250_000)
    assert child_cap(Cap(None, 10), Cap(7, 10)) == Cap(7, None)


def test_usd_micros_rounds_like_node() -> None:
    assert [usd_micros(v) for v in (5, 2.0, 0.001, 0.1 + 0.2, 1e-7)] == [5_000_000, 2_000_000, 1_000, 300_000, 0]
