"""In-memory ``BudgetLedger`` (tests, Loom, a single-process host; the reference for Node's).

No operation awaits between reading and writing, so concurrent tasks of one event
loop see each operation whole. Expired reservations are returned by the next
operation, as Node's store does inside its transactions.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from typing import TYPE_CHECKING

from harness_core.budget.errors import (
    BudgetAccountClosed,
    BudgetAccountMissing,
    BudgetReservationMissing,
    BudgetReservationSettled,
)
from harness_core.budget.tree import (
    AccountState,
    crossed,
    grant,
    remaining,
    reserve_for_child,
)
from harness_core.budget.types import (
    NO_USAGE,
    ZERO,
    Reservation,
    ReservationState,
    Settlement,
    hold_key,
)

if TYPE_CHECKING:
    from harness_core.budget.types import (
        Amount,
        Cap,
        Hold,
        LedgerUsage,
        PinnedBudget,
        Remaining,
        RunAccountRef,
    )
    from harness_core.clock import Clock


@dataclass(slots=True)
class _Held:
    reservation: Reservation
    path: tuple[RunAccountRef, ...]
    settlement: Settlement | None = None
    # Committed at the gateway's estimate and not yet reconciled (WP-66).
    estimated: bool = False
    usage: LedgerUsage | None = None
    # A hold (``Hold``): the ledger's own; nobody commits, releases or reconciles it.
    hold: bool = False
    # The role its commit was charged under (GA-15), so a reconcile counts its difference there too.
    role: str = "primary"


class InMemoryBudgetLedger:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._accounts: dict[RunAccountRef, AccountState] = {}
        self._held: dict[tuple[RunAccountRef, str], _Held] = {}
        self._pins: dict[RunAccountRef, PinnedBudget] = {}
        # GA-15: each run's own charges by the role its calls named.
        self._role_spend: dict[RunAccountRef, dict[str, Amount]] = {}

    def role_spend(self, account: RunAccountRef) -> dict[str, Amount]:
        """The run's own charges by model role (its descendants' are theirs)."""
        return dict(self._role_spend.get(account, {}))

    def pin(self, account: RunAccountRef, pinned: PinnedBudget) -> None:
        """What the host pinned for a run, so a reservation can open its account lazily."""
        self._pins[account] = pinned

    async def open_root(self, account: RunAccountRef, cap: Cap, *, opening: Amount | None = None) -> Remaining:
        self._open(account, None, cap, opening)
        return remaining(self._path(account))

    async def open_child(self, parent: RunAccountRef, child: RunAccountRef, cap: Cap) -> Remaining:
        self._expire()
        if child not in self._accounts:
            path = self._path(self._ensure(parent, None))
            self._check_open(path)
            self._open(child, parent, reserve_for_child(remaining(path).available, cap), None)
        return remaining(self._path(child))

    async def close(self, account: RunAccountRef) -> None:
        found = self._accounts.get(account)
        if found is not None:
            self._accounts[account] = replace(found, closed=True)

    async def reserve(
        self,
        account: RunAccountRef,
        key: str,
        requested: Amount,
        *,
        minimum: Amount,
        ttl_s: int,
        opening: Amount | None = None,
        credential: str | None = None,
    ) -> Reservation:
        del credential
        requested.validate()
        minimum.validate()
        if minimum.usd_micros > requested.usd_micros or minimum.tokens > requested.tokens:
            raise ValueError("A reservation's minimum is at most what it requests")
        self._expire()
        known = self._held.get((account, key))
        if known is not None:
            return known.reservation
        path = self._path(self._ensure(account, opening))
        self._check_open(path)
        granted = grant(path, requested, minimum)
        held = _Held(
            reservation=Reservation(account, key, requested, granted, self._clock.now() + timedelta(seconds=ttl_s)),
            path=tuple(a.ref for a in path),
        )
        self._held[account, key] = held
        self._add(held.path, reserved=granted)
        return held.reservation

    async def commit(
        self,
        reservation: Reservation,
        actual: Amount,
        *,
        usage: LedgerUsage | None = None,
        credential: str | None = None,
        hold: Hold | None = None,
        model_role: str | None = None,
    ) -> Settlement:
        del credential
        actual.validate()
        if usage is not None:
            usage.validate()
        if hold is not None:
            hold.amount.validate()
        self._expire()
        held = self._find(reservation)
        state = held.reservation.state
        if state is ReservationState.COMMITTED and held.settlement is not None:
            return held.settlement
        if state is ReservationState.RELEASED:
            raise BudgetReservationSettled("the reservation was released; nothing was charged")
        before = [self._accounts[ref] for ref in held.path]
        returned = held.reservation.granted if state is ReservationState.OPEN else ZERO
        self._add(held.path, reserved=ZERO - returned, committed=actual)
        held.role = model_role or "primary"
        roles = self._role_spend.setdefault(held.path[0], {})
        roles[held.role] = roles.get(held.role, ZERO) + actual
        if usage is not None:
            own = self._accounts[held.path[0]]
            self._accounts[held.path[0]] = replace(own, used=own.used + usage)
        if hold is not None:
            held.estimated, held.usage = True, usage
            self._add(held.path, estimated=1)
            self._hold(held, hold)
        return self._settle(held, ReservationState.COMMITTED, actual, did_cross=crossed(before, self._path(held.path[0])))

    async def reconcile(
        self,
        reservation: Reservation,
        actual: Amount,
        *,
        usage: LedgerUsage | None = None,
        credential: str | None = None,
    ) -> Settlement:
        del credential
        actual.validate()
        if usage is not None:
            usage.validate()
        self._expire()
        held = self._find(reservation)
        if held.reservation.state is not ReservationState.COMMITTED or held.settlement is None:
            raise BudgetReservationSettled("only a committed reservation is reconciled")
        if not held.estimated:
            return held.settlement
        before = [self._accounts[ref] for ref in held.path]
        self._add(held.path, committed=actual - held.settlement.charged, estimated=-1)
        roles = self._role_spend.setdefault(held.path[0], {})
        roles[held.role] = roles.get(held.role, ZERO) + (actual - held.settlement.charged)
        if usage is not None:
            own = self._accounts[held.path[0]]
            self._accounts[held.path[0]] = replace(own, used=own.used - (held.usage or NO_USAGE) + usage)
        hold = self._held.get((held.reservation.account, hold_key(held.reservation.key)))
        if hold is not None and hold.reservation.state is ReservationState.OPEN:
            self._add(hold.path, reserved=ZERO - hold.reservation.granted)
            hold.reservation = replace(hold.reservation, state=ReservationState.RELEASED)
        held.estimated, held.usage = False, usage
        return self._settle(held, ReservationState.COMMITTED, actual, did_cross=crossed(before, self._path(held.path[0])))

    def _hold(self, held: _Held, hold: Hold) -> None:
        """The bound's rest, held on the path past any cap: it is spend that may already have happened."""
        key = hold_key(held.reservation.key)
        if hold.amount == ZERO or (held.reservation.account, key) in self._held:
            return
        account = held.reservation.account
        reservation = Reservation(account, key, hold.amount, hold.amount, self._clock.now() + timedelta(seconds=hold.ttl_s))
        self._held[account, key] = _Held(reservation=reservation, path=held.path, hold=True)
        self._add(held.path, reserved=hold.amount)

    async def release(self, reservation: Reservation, *, credential: str | None = None) -> Settlement:
        del credential
        self._expire()
        held = self._find(reservation)
        if held.reservation.state is not ReservationState.OPEN:
            return held.settlement or Settlement(held.reservation, ZERO, False, remaining(self._path(held.path[0])))
        self._add(held.path, reserved=ZERO - held.reservation.granted)
        return self._settle(held, ReservationState.RELEASED, ZERO, did_cross=False)

    async def remaining(self, account: RunAccountRef, *, credential: str | None = None) -> Remaining:
        del credential
        self._expire()
        return remaining(self._path(self._ensure(account, None)))

    def _settle(self, held: _Held, state: ReservationState, charged: Amount, *, did_cross: bool) -> Settlement:
        held.reservation = replace(held.reservation, state=state)
        held.settlement = Settlement(held.reservation, charged, did_cross, remaining(self._path(held.path[0])))
        return held.settlement

    def _find(self, reservation: Reservation) -> _Held:
        held = self._held.get((reservation.account, reservation.key))
        if held is None or held.hold:
            raise BudgetReservationMissing("no such reservation on this run")
        return held

    def _open(self, account: RunAccountRef, parent: RunAccountRef | None, cap: Cap, opening: Amount | None) -> None:
        if account in self._accounts:
            return
        self._accounts[account] = AccountState(account, parent, cap, ZERO, ZERO)
        if opening is not None and opening != ZERO:
            self._add(tuple(a.ref for a in self._path(account)), committed=opening.validate())

    def _ensure(self, account: RunAccountRef, opening: Amount | None) -> RunAccountRef:
        if account in self._accounts:
            return account
        pinned = self._pins.get(account)
        if pinned is None:
            raise BudgetAccountMissing("the run has no budget account")
        if pinned.parent is not None:
            self._ensure(pinned.parent, None)
        self._open(account, pinned.parent, pinned.cap, opening)
        return account

    def _path(self, account: RunAccountRef) -> list[AccountState]:
        path: list[AccountState] = []
        ref: RunAccountRef | None = account
        while ref is not None:
            state = self._accounts[ref]
            path.append(state)
            ref = state.parent
        return path

    @staticmethod
    def _check_open(path: list[AccountState]) -> None:
        if any(a.closed for a in path):
            raise BudgetAccountClosed("the run's budget account is closed")

    def _add(
        self, path: tuple[RunAccountRef, ...], *, reserved: Amount = ZERO, committed: Amount = ZERO, estimated: int = 0
    ) -> None:
        for ref in path:
            state = self._accounts[ref]
            self._accounts[ref] = replace(
                state,
                reserved=state.reserved + reserved,
                committed=state.committed + committed,
                estimated=state.estimated + estimated,
            )

    def _expire(self) -> None:
        now = self._clock.now()
        for held in self._held.values():
            if held.reservation.state is ReservationState.OPEN and held.reservation.expires_at <= now:
                self._add(held.path, reserved=ZERO - held.reservation.granted)
                held.reservation = replace(held.reservation, state=ReservationState.EXPIRED)


__all__ = ["InMemoryBudgetLedger"]
