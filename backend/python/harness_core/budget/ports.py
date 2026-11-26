"""Ports of the budget ledger (P6.5).

``BudgetAccounts`` is what a charger needs (a gateway around a paid call);
``BudgetLedger`` adds what the admission authority does (open accounts, close).
``credential`` is the caller's proof for the run, opaque to the core, forwarded only
to the authority (PipesHub: the run's gateway token); in-memory adapters ignore it.

Contract suites: ``harness_core.testing.contracts.budget``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from harness_core.budget.types import (
        Amount,
        Cap,
        Hold,
        LedgerUsage,
        Remaining,
        Reservation,
        RunAccountRef,
        Settlement,
    )


class BudgetAccounts(Protocol):
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
        """Holds up to ``requested`` (at least ``minimum``) on the account and every
        ancestor until committed, released or expired. The same ``key`` again returns
        the same reservation. ``opening`` is the spend a lazily opened account starts
        with (a run admitted before the ledger); ignored once the account exists.
        Raises a ``BudgetRefused``."""
        ...

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
        """Charges ``actual`` (once per reservation) and returns the unused part; after
        expiry it still charges ``actual``. ``usage`` (the provider's counts for the call)
        is added to the run's own ``Remaining.used`` with the first commit only.

        ``hold``: ``actual`` is the gateway's estimate (WP-66). The call counts in ``Remaining.estimated`` and
        ``hold.amount`` stays held on the path, past any cap, until ``reconcile`` or ``hold.ttl_s``.

        ``model_role``: what the call was for (GA-15: primary, fast, subagent, compaction), so
        cost is reported per role; ``None`` reads as primary."""
        ...

    async def reconcile(
        self,
        reservation: Reservation,
        actual: Amount,
        *,
        usage: LedgerUsage | None = None,
        credential: str | None = None,
    ) -> Settlement:
        """Replaces an estimated commit's charge with the provider's ``actual`` (more or less than the estimate),
        its usage with ``usage``, and gives its hold back. Once: a commit that was not estimated, or was
        reconciled, is left as it is. The difference counts under the role its commit named. Raises a
        ``BudgetRefused`` for a reservation never committed."""
        ...

    async def release(self, reservation: Reservation, *, credential: str | None = None) -> Settlement:
        """Returns the reservation unused; a settled one is left as it is."""
        ...

    async def remaining(self, account: RunAccountRef, *, credential: str | None = None) -> Remaining: ...


class BudgetLedger(BudgetAccounts, Protocol):
    async def open_root(self, account: RunAccountRef, cap: Cap, *, opening: Amount | None = None) -> Remaining:
        """Idempotent on the run."""
        ...

    async def open_child(self, parent: RunAccountRef, child: RunAccountRef, cap: Cap) -> Remaining:
        """The child's account under its parent's (``tree.reserve_for_child`` decides);
        idempotent on the child."""
        ...

    async def close(self, account: RunAccountRef) -> None:
        """No more reservations on the run or its descendants; open ones may still settle."""
        ...


__all__ = ["BudgetAccounts", "BudgetLedger"]
