"""Typed refusals of the budget ledger, each with a stable ``reason_code`` (C-6: unique).

``http_status`` is what a gateway answers: 402 for budgets, because OpenAI-compatible
clients retry 429 and 5xx and an exhausted budget must not be retried.
"""

from __future__ import annotations

from harness_core.refusals import HarnessRefusal


class BudgetRefused(HarnessRefusal):
    reason_code: str = "budget_refused"
    http_status: int = 402
    # Which shared budget refused it (WP-51), as the extension that keeps it names it (opaque here); None: the run's
    # own spending limit, or a ledger that does not say.
    budget_scope: str | None = None


class BudgetExhausted(BudgetRefused):
    """An account on the run's path has committed its cap: nothing will come back."""

    reason_code = "budget_exhausted"


class BudgetInsufficient(BudgetRefused):
    """Something is left, less than the call's minimum (other reservations may return it)."""

    reason_code = "budget_insufficient"


class BudgetAccountMissing(BudgetRefused):
    """The run has no account and the authority has no pinned budget to open one from."""

    reason_code = "budget_account_missing"
    http_status = 403


class BudgetAccountClosed(BudgetRefused):
    """The run (or an ancestor) was closed: it may settle what it reserved, not reserve more."""

    reason_code = "budget_account_closed"


class BudgetReservationMissing(BudgetRefused):
    """No reservation with this key on this run."""

    reason_code = "budget_reservation_missing"
    http_status = 404


class BudgetReservationSettled(BudgetRefused):
    """A commit of a reservation that was released: nothing was charged."""

    reason_code = "budget_reservation_settled"
    http_status = 409


class BudgetShareExceedsRemaining(BudgetRefused):
    """A child's explicit share is more than its parent may still reserve, or an
    inheriting child's parent has nothing left."""

    reason_code = "budget_share_exceeds_remaining"


class BudgetUnavailable(BudgetRefused):
    """The ledger cannot be reached or refused the caller; the call is not made (fail closed)."""

    reason_code = "budget_unavailable"

    def __init__(self, message: str, *, detail: str | None = None, http_status: int = 503) -> None:
        super().__init__(message)
        self.detail = detail
        self.http_status = http_status


REFUSALS_BY_CODE: dict[str, type[BudgetRefused]] = {
    cls.reason_code: cls
    for cls in (
        BudgetExhausted,
        BudgetInsufficient,
        BudgetAccountMissing,
        BudgetAccountClosed,
        BudgetReservationMissing,
        BudgetReservationSettled,
        BudgetShareExceedsRemaining,
    )
}

__all__ = [
    "REFUSALS_BY_CODE",
    "BudgetAccountClosed",
    "BudgetAccountMissing",
    "BudgetExhausted",
    "BudgetInsufficient",
    "BudgetRefused",
    "BudgetReservationMissing",
    "BudgetReservationSettled",
    "BudgetShareExceedsRemaining",
    "BudgetUnavailable",
]
