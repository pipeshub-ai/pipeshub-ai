"""A turn's usage, as the gateway ledger holds it (OWD-8).

Every model call an engine makes goes through the model gateway, which tells the ledger the provider's own counts
when it settles the call, before the call's last chunk reaches the engine. So the run's ``used`` read after the
turn's stream ended, less the one read before it, is exactly what the turn's calls cost. An engine's own report is
not: opencode over ACP answers a turn with its last call's usage only (CF-13). The engine's reports still drive the
in-turn limits (``max_tokens``), which cannot wait for the turn to end; a report above the ledger means a call the
gateway never saw, and is logged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from harness_core.budget.ports import BudgetAccounts
    from harness_core.budget.types import LedgerUsage, Remaining, RunAccountRef

UsageSource = Literal["ledger", "engine"]


class RunUsageLedger(Protocol):
    async def read(self) -> Remaining:
        """The run's ledger account now: ``used``, its own calls' usage (never its children's); ``committed``,
        what it was charged."""
        ...


@dataclass(frozen=True, slots=True)
class AccountUsageLedger:
    """``RunUsageLedger`` over the run's ledger account, read with the caller's own credential."""

    accounts: BudgetAccounts
    account: RunAccountRef

    async def read(self) -> Remaining:
        return await self.accounts.remaining(self.account)


@dataclass(frozen=True, slots=True)
class TurnUsage:
    usage: LedgerUsage
    source: UsageSource
    # The engine reported more than the ledger holds in some dimension: a call that bypassed the gateway.
    over_ledger: bool = False


def turn_usage(before: LedgerUsage | None, after: LedgerUsage | None, reported: LedgerUsage) -> TurnUsage:
    """The ledger's difference over the turn; the engine's own report only when the ledger could not be read."""
    if before is None or after is None:
        return TurnUsage(reported, "engine")
    usage = after - before
    over = reported.input_tokens > usage.input_tokens or reported.output_tokens > usage.output_tokens
    return TurnUsage(usage, "ledger", over_ledger=over)


__all__ = ["AccountUsageLedger", "RunUsageLedger", "TurnUsage", "UsageSource", "turn_usage"]
