"""The wrap-up reserve of a run's budget (BUDGETEND): what its answer-only final turn may spend.

A run that reaches its spending limit still answers from what it gathered, on one answer-only turn. That
turn is paid for in advance: before the working phase spends, the run holds a reservation on its ledger
account sized for the wrap-up request (a capped prompt plus the final answer's output, at the model's
price). Every working call then sees that much less available, so the working phase stops, refused by the
ledger, while the reserve is still there; the final turn draws on the held reservation instead of asking
for a new one, so it is never refused and never spends more than the reserve.

The reservation lives as long as the attempt that held it: a later attempt holds its own, and one that
expires (a run longer than the ledger's longest hold) is held again before the next working call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from harness_core.access.spend import estimate_spend
from harness_core.budget.answer_size import WRAP_UP_INPUT_TOKENS, WRAP_UP_OUTPUT_TOKENS
from harness_core.budget.errors import BudgetAccountMissing, BudgetRefused
from harness_core.budget.types import ZERO
from harness_core.clock import Clock, SystemClock
from harness_core.logs import HarnessLog

if TYPE_CHECKING:
    from harness_core.budget.ports import BudgetAccounts
    from harness_core.budget.types import Amount, Remaining, Reservation, RunAccountRef
    from harness_core.ids import IdSource

# At most this share of the window goes to the wrap-up prompt, whatever the model's window.
WRAP_UP_WINDOW_SHARE = 0.5
# The ledger holds a reservation at most an hour (Node's ``ttlS`` bound).
WRAP_UP_TTL_S = 3_600
# A held reserve this close to expiry is held again before the next working call.
WRAP_UP_RENEW_MARGIN_S = 300
# The reserve takes at most this share of what the account has left when it is first held, so a small budget (a
# deep run's sub-agent share) keeps most of it for work; the wrap-up request is then cut smaller, never below these.
WRAP_UP_MAX_SHARE = 0.1
WRAP_UP_MIN_INPUT_TOKENS = 2_000
WRAP_UP_MIN_OUTPUT_TOKENS = 1_000


@dataclass(frozen=True, slots=True)
class WrapUpSize:
    """How large the wrap-up request may be: its prompt and its answer, in tokens."""

    input_tokens: int
    output_tokens: int

    @staticmethod
    def for_window(window_tokens: int | None) -> WrapUpSize:
        cap = WRAP_UP_INPUT_TOKENS
        if window_tokens:
            cap = min(cap, int(window_tokens * WRAP_UP_WINDOW_SHARE))
        return WrapUpSize(input_tokens=cap, output_tokens=WRAP_UP_OUTPUT_TOKENS)

    def amount(self, pricing_model: str, *, charges_usd: bool) -> Amount:
        return estimate_spend(self.input_tokens, self.output_tokens, pricing_model, charges_usd=charges_usd)

    def scaled(self, factor: float) -> WrapUpSize:
        """This size times ``factor`` (at most 1), never below the floors."""
        if factor >= 1:
            return self
        return WrapUpSize(
            input_tokens=max(int(self.input_tokens * factor), WRAP_UP_MIN_INPUT_TOKENS),
            output_tokens=max(int(self.output_tokens * factor), WRAP_UP_MIN_OUTPUT_TOKENS),
        )


class WrapUpReserve:
    """The reserve on the run's ledger account. ``hold`` reserves it (all or nothing), ``arm`` hands it to the
    next metered call of the run, ``release`` returns it unused. Shared by every metered transport of the run
    (its routes too): the reserve is the account's, not a route's."""

    def __init__(
        self,
        *,
        accounts: BudgetAccounts,
        account: RunAccountRef,
        pricing_model: str,
        ids: IdSource,
        size: WrapUpSize,
        charges_usd: bool = True,
        ttl_s: int = WRAP_UP_TTL_S,
        clock: Clock | None = None,
        log: HarnessLog | None = None,
    ) -> None:
        self._accounts = accounts
        self._account = account
        self._ids = ids
        self._pricing_model = pricing_model
        self._charges_usd = charges_usd
        self.size = size
        self.amount = size.amount(pricing_model, charges_usd=charges_usd)
        self._sized = False
        self._ttl_s = ttl_s
        self._clock = clock or SystemClock()
        self._log = log or HarnessLog.named("harness_core.budget.wrap_up")
        self._held: Reservation | None = None
        # When the hold is due for renewal, on this process's monotonic clock: the ledger's own expiry is another
        # clock (Node's database), and a skew between them must not renew the hold on every call.
        self._renew_at = 0.0
        self._armed = False

    @property
    def held(self) -> bool:
        return self._held is not None

    @property
    def pricing_model(self) -> str:
        """The model the reserve is priced for, whose counter sizes the wrap-up request (R15-WRAP)."""
        return self._pricing_model

    async def hold(self) -> bool:
        """Holds the reserve unless it is held and not about to expire; ``False`` when the ledger refuses it (the
        run then wraps up with no model call if its limit runs out)."""
        if self._held is not None:
            if self._clock.monotonic() < self._renew_at:
                return True
            await self.release()
        if not self._sized:
            await self._size_to_account()
        try:
            self._held = await self._accounts.reserve(
                self._account,
                self._ids.new_id("wrapup"),
                self.amount,
                minimum=self.amount,
                ttl_s=self._ttl_s,
            )
        except BudgetRefused as refused:
            self._log.warning("budget.wrap_up_unreserved", reason=refused.reason_code, runId=self._account.run_id)
            return False
        except Exception as exc:
            self._log.warning("budget.wrap_up_unreserved", reason="ledger_error", errorType=type(exc).__name__)
            return False
        self._renew_at = self._clock.monotonic() + self._ttl_s - WRAP_UP_RENEW_MARGIN_S
        self._log.info(
            "budget.wrap_up_held",
            runId=self._account.run_id,
            reserveMicroUsd=self.amount.usd_micros,
            reserveTokens=self.amount.tokens,
        )
        return True

    async def _size_to_account(self) -> None:
        """At most ``WRAP_UP_MAX_SHARE`` of what the account has left, once, when the reserve is first held."""
        try:
            left = (await self._left()).available
        except Exception as exc:
            # The reservation itself says whether the account can pay; the size stays the model's.
            self._log.warning("budget.wrap_up_unsized", errorType=type(exc).__name__)
            return
        self._sized = True
        factors = [
            WRAP_UP_MAX_SHARE * available / wanted
            for available, wanted in ((left.tokens, self.amount.tokens), (left.usd_micros, self.amount.usd_micros))
            if available is not None and wanted > 0
        ]
        factor = min(factors, default=1.0)
        if factor < 1:
            self.size = self.size.scaled(factor)
            self.amount = self.size.amount(self._pricing_model, charges_usd=self._charges_usd)

    async def _left(self) -> Remaining:
        """The account's remaining budget. A run's account opens on its first reservation, and the reserve is held
        before the run's first call: reading a missing account opens it with an empty reservation (returned at
        once) and reads again, so the first hold is sized like every other (otherwise
        ``wrap_up_unsized BudgetAccountMissing``)."""
        try:
            return await self._accounts.remaining(self._account)
        except BudgetAccountMissing:
            opened = await self._accounts.reserve(
                self._account, self._ids.new_id("wrapup-open"), ZERO, minimum=ZERO, ttl_s=60
            )
            settled = await self._accounts.release(opened)
            return settled.remaining

    def arm(self) -> bool:
        """The next metered call draws on the held reserve; ``False`` when none is held."""
        self._armed = self._held is not None
        return self._armed

    def take(self) -> Reservation | None:
        """The held reservation for the call ``arm`` named, once; ``None`` when not armed."""
        if not self._armed or self._held is None:
            return None
        held, self._held, self._armed = self._held, None, False
        return held

    async def release(self) -> None:
        held, self._held, self._armed = self._held, None, False
        if held is None:
            return
        try:
            _ = await self._accounts.release(held)
        except Exception as exc:
            # It expires on its own: nothing is charged for it.
            self._log.warning("budget.wrap_up_release_failed", errorType=type(exc).__name__)


__all__ = [
    "WRAP_UP_INPUT_TOKENS",
    "WRAP_UP_MAX_SHARE",
    "WRAP_UP_MIN_INPUT_TOKENS",
    "WRAP_UP_MIN_OUTPUT_TOKENS",
    "WRAP_UP_OUTPUT_TOKENS",
    "WRAP_UP_RENEW_MARGIN_S",
    "WRAP_UP_TTL_S",
    "WRAP_UP_WINDOW_SHARE",
    "WrapUpReserve",
    "WrapUpSize",
]
