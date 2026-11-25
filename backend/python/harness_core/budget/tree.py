"""The ledger's rules over one path of accounts (pure; Node's `domain/budget-ledger.ts` is the same).

An account holds ``committed`` and ``reserved`` of its whole subtree, so what a run
may reserve is the minimum, over the run and its ancestors, of
``cap - committed - reserved``. The shared table
``contracts/harness/vectors/budget-ledger.json`` pins both implementations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from harness_core.budget.errors import (
    BudgetExhausted,
    BudgetInsufficient,
    BudgetShareExceedsRemaining,
)
from harness_core.budget.types import (
    NO_USAGE,
    Amount,
    Available,
    Cap,
    LedgerUsage,
    Remaining,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from harness_core.budget.types import RunAccountRef


@dataclass(frozen=True, slots=True)
class AccountState:
    ref: RunAccountRef
    parent: RunAccountRef | None
    cap: Cap
    committed: Amount
    reserved: Amount
    closed: bool = False
    used: LedgerUsage = NO_USAGE
    # Calls of the subtree charged an estimate not yet replaced by the provider's usage (WP-66).
    estimated: int = 0


def _least(values: list[int]) -> int | None:
    return min(values) if values else None


def available(path: Sequence[AccountState]) -> Available:
    """``path`` is the run's account first, then each ancestor up to the root."""
    return Available(
        usd_micros=_least(
            [a.cap.usd_micros - a.committed.usd_micros - a.reserved.usd_micros for a in path if a.cap.usd_micros is not None]
        ),
        tokens=_least([a.cap.tokens - a.committed.tokens - a.reserved.tokens for a in path if a.cap.tokens is not None]),
    )


def _unspent(path: Sequence[AccountState]) -> Available:
    """Like ``available`` but ignoring reservations: what will never come back once ≤ 0."""
    return Available(
        usd_micros=_least([a.cap.usd_micros - a.committed.usd_micros for a in path if a.cap.usd_micros is not None]),
        tokens=_least([a.cap.tokens - a.committed.tokens for a in path if a.cap.tokens is not None]),
    )


def remaining(path: Sequence[AccountState]) -> Remaining:
    left = available(path)
    unspent = _unspent(path)
    exhausted = any(v is not None and v <= 0 for v in (unspent.usd_micros, unspent.tokens))
    return Remaining(
        available=Available(
            None if left.usd_micros is None else max(0, left.usd_micros),
            None if left.tokens is None else max(0, left.tokens),
        ),
        committed=path[0].committed,
        reserved=path[0].reserved,
        exhausted=exhausted,
        used=path[0].used,
        estimated=path[0].estimated,
    )


def _grant_one(requested: int, minimum: int, left: int | None, unspent: int | None, what: str) -> int:
    if left is None or (requested == 0 and minimum == 0):
        return requested
    if unspent is not None and unspent <= 0:
        raise BudgetExhausted(f"the run's tree has spent its {what} budget")
    if max(left, 0) < minimum:
        raise BudgetInsufficient(f"less {what} budget is left than the call needs")
    return min(requested, max(left, 0))


def grant(path: Sequence[AccountState], requested: Amount, minimum: Amount) -> Amount:
    """What a reservation gets: ``min(requested, available)`` per dimension once the
    available amount covers ``minimum``. A dimension asked for nothing is not checked
    (a route the org does not pay for reserves no USD)."""
    left, unspent = available(path), _unspent(path)
    return Amount(
        usd_micros=_grant_one(requested.usd_micros, minimum.usd_micros, left.usd_micros, unspent.usd_micros, "cost"),
        tokens=_grant_one(requested.tokens, minimum.tokens, left.tokens, unspent.tokens, "token"),
    )


def crossed(before: Sequence[AccountState], after: Sequence[AccountState]) -> bool:
    """A settlement crossed when an account on the path reached a cap it was below."""

    def reached(a: AccountState) -> tuple[bool, bool]:
        return (
            a.cap.usd_micros is not None and a.committed.usd_micros >= a.cap.usd_micros,
            a.cap.tokens is not None and a.committed.tokens >= a.cap.tokens,
        )

    return any(
        (now_usd and not was_usd) or (now_tokens and not was_tokens)
        for (was_usd, was_tokens), (now_usd, now_tokens) in zip(
            (reached(a) for a in before), (reached(a) for a in after), strict=True
        )
    )


def reserve_for_child(parent: Available, share: Cap) -> Cap:
    """The cap a child's account opens with, or a refusal (P6.5 seam for K-L's shares).

    An explicit share must fit in what the parent may still reserve; a dimension
    without a share draws from the parent, which must have something left."""
    for asked, left in ((share.usd_micros, parent.usd_micros), (share.tokens, parent.tokens)):
        if left is None:
            continue
        if asked is not None and asked > left:
            raise BudgetShareExceedsRemaining("the child's share is more than its parent has left")
        if asked is None and left <= 0:
            raise BudgetShareExceedsRemaining("the parent has nothing left for a child to draw on")
    return share


def child_cap(parent_pinned: Cap, child_pinned: Cap) -> Cap:
    """A child pinned with its parent's own budget draws from the parent (an equal cap
    can never bind: the parent's account holds the child's spend too); a smaller one
    is the child's share."""

    def own(parent: int | None, child: int | None) -> int | None:
        if child is None or (parent is not None and child >= parent):
            return None
        return child

    return Cap(own(parent_pinned.usd_micros, child_pinned.usd_micros), own(parent_pinned.tokens, child_pinned.tokens))


def usd_micros(usd: float) -> int:
    """A pinned USD budget as micro-USD (Node's `usdMicros`: `Math.round`)."""
    return int(usd * 1_000_000 + 0.5)


__all__ = [
    "AccountState",
    "available",
    "child_cap",
    "crossed",
    "grant",
    "remaining",
    "reserve_for_child",
    "usd_micros",
]
