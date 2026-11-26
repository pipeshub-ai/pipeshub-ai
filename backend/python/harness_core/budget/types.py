"""Values of the budget ledger: money in integer micro-USD (no float drift), tokens.

A ``Cap`` dimension of ``None`` means the account has no limit of its own there: it
draws from its parent. An ``Available`` dimension of ``None`` means nothing on the
path bounds it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

MICRO_USD = 1_000_000


@dataclass(frozen=True, slots=True)
class Amount:
    usd_micros: int = 0
    tokens: int = 0

    def __add__(self, other: Amount) -> Amount:
        return Amount(self.usd_micros + other.usd_micros, self.tokens + other.tokens)

    def __sub__(self, other: Amount) -> Amount:
        return Amount(self.usd_micros - other.usd_micros, self.tokens - other.tokens)

    @property
    def usd(self) -> float:
        return self.usd_micros / MICRO_USD

    def validate(self) -> Amount:
        if self.usd_micros < 0 or self.tokens < 0:
            raise ValueError("A budget amount is never negative")
        return self


ZERO = Amount()


@dataclass(frozen=True, slots=True)
class LedgerUsage:
    """A call's provider usage as its settler reported it; summed per run, it is the run's usage (OWD-8: the
    gateway ledger is the authority, an engine's own report is not). ``input_tokens`` counts cached tokens too."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __add__(self, other: LedgerUsage) -> LedgerUsage:
        return LedgerUsage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
            self.cache_write_tokens + other.cache_write_tokens,
        )

    def __sub__(self, other: LedgerUsage) -> LedgerUsage:
        return LedgerUsage(
            self.input_tokens - other.input_tokens,
            self.output_tokens - other.output_tokens,
            self.cache_read_tokens - other.cache_read_tokens,
            self.cache_write_tokens - other.cache_write_tokens,
        )

    def validate(self) -> LedgerUsage:
        if min(self.input_tokens, self.output_tokens, self.cache_read_tokens, self.cache_write_tokens) < 0:
            raise ValueError("A call's usage is never negative")
        return self


NO_USAGE = LedgerUsage()


@dataclass(frozen=True, slots=True)
class Cap:
    usd_micros: int | None = None
    tokens: int | None = None


UNCAPPED = Cap()


@dataclass(frozen=True, slots=True)
class Available:
    usd_micros: int | None
    tokens: int | None


@dataclass(frozen=True, slots=True)
class RunAccountRef:
    """One run's account; ids are opaque to the core."""

    tenant: str
    run_id: str


class ReservationState(StrEnum):
    OPEN = "open"
    COMMITTED = "committed"
    RELEASED = "released"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class Reservation:
    account: RunAccountRef
    key: str
    requested: Amount
    granted: Amount
    expires_at: datetime
    state: ReservationState = ReservationState.OPEN

    @property
    def partial(self) -> bool:
        return self.granted != self.requested


@dataclass(frozen=True, slots=True)
class Remaining:
    """What the run may still reserve (the minimum along its path), and its subtree's totals."""

    available: Available
    committed: Amount
    reserved: Amount
    exhausted: bool
    # The run's own calls' usage (never its children's): what the ledger was told at each first commit.
    used: LedgerUsage = NO_USAGE
    # A shared budget set to stop that leaves the run less than its own account (C9.06): its scope, opaque here,
    # binds the run's next call. None: the run's own limit binds (WP56-STOP-1).
    budget_scope: str | None = None
    # Calls of the subtree whose charge is still the gateway's estimate (WP-66): shown as estimated until reconciled.
    estimated: int = 0


@dataclass(frozen=True, slots=True)
class Hold:
    """What a commit whose charge is only an estimate keeps held beyond it (WP-66): the rest of the upper bound the
    provider could bill, on every account of the path and whatever the caps say, so budgets enforce the bound
    while people are charged the estimate. It goes when the provider's usage replaces the estimate (``reconcile``)
    or after ``ttl_s``, when the estimate stands."""

    amount: Amount
    ttl_s: int


def hold_key(key: str) -> str:
    """The key of the reservation that holds an estimated commit's bound: no caller's key has a ``#`` (Node refuses
    one), so no caller can reserve, commit or release it."""
    return f"{key}#hold"


@dataclass(frozen=True, slots=True)
class Settlement:
    reservation: Reservation
    charged: Amount
    # This settlement made an account on the path reach its cap: the caller logs it once.
    crossed: bool
    remaining: Remaining


@dataclass(frozen=True, slots=True)
class PinnedBudget:
    """What a host pinned for a run that has no account yet (lazy opening, P6.5 migration)."""

    cap: Cap
    parent: RunAccountRef | None = None


__all__ = [
    "MICRO_USD",
    "NO_USAGE",
    "UNCAPPED",
    "ZERO",
    "RunAccountRef",
    "Amount",
    "Available",
    "Cap",
    "Hold",
    "LedgerUsage",
    "PinnedBudget",
    "Remaining",
    "Reservation",
    "ReservationState",
    "Settlement",
    "hold_key",
]
