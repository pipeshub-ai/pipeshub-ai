"""What the grant auditor reads and reports (P9): executed calls, a run's pinned grant, violations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.budget.types import Amount, LedgerUsage

CallSource = Literal["tool_gateway", "model_gateway", "effect_log", "usage_ledger"]
ViolationCode = Literal["tool_not_granted", "route_not_granted", "run_unknown", "usage_unreconciled"]

TOOL_NOT_GRANTED: ViolationCode = "tool_not_granted"
ROUTE_NOT_GRANTED: ViolationCode = "route_not_granted"
RUN_UNKNOWN: ViolationCode = "run_unknown"
# C9.01: an ended run's journal usage differs from what its ledger account holds (OWD-8's authority).
USAGE_UNRECONCILED: ViolationCode = "usage_unreconciled"


@dataclass(frozen=True, slots=True)
class ExecutedCall:
    """One call a gateway served (or the effect log journaled) for a run: ids only, never arguments."""

    source: CallSource
    org_id: str
    run_id: str
    # A tool id (``<source>@<instance>/<tool>``) for tools, a route id (``provider:model[@kind:account]``) for models.
    subject: str
    attempt_id: str | None = None
    invocation_id: str | None = None

    @property
    def key(self) -> tuple[str, str, str, str, str]:
        return (self.source, self.org_id, self.run_id, self.subject, self.invocation_id or self.attempt_id or "")


@dataclass(frozen=True, slots=True)
class PinnedGrant:
    tool_ids: frozenset[str]
    route_ids: frozenset[str]


@dataclass(frozen=True, slots=True)
class JournalUsageRecord:
    """One usage-bearing journal event (``model.response``, ``usage.recorded``), counts as journaled."""

    type: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    # ``None``: counted as its reporter counts, which ``provider`` and ``transport`` tell (USAGE1's legacy rule).
    input_includes_cached: bool | None = None
    provider: str = ""
    transport: str | None = None


@dataclass(frozen=True, slots=True)
class LedgerAccountView:
    # ``None``: no settler ever told the account a usage (opened before the ledger kept it); nothing to reconcile.
    used: LedgerUsage | None
    # What the account still holds: a settlement may still land while it is not zero.
    reserved: Amount


@dataclass(frozen=True, slots=True)
class RunEvidence:
    """A run's pinned grant and the tool intents its journal holds (the effect log); for the usage reconciliation
    (C9.01), whether it ended, its journal's usage records and its ledger account."""

    grant: PinnedGrant
    intents: tuple[ExecutedCall, ...] = ()
    ended: bool = False
    usage: tuple[JournalUsageRecord, ...] = ()
    ledger: LedgerAccountView | None = None


@dataclass(frozen=True, slots=True)
class Violation:
    code: ViolationCode
    call: ExecutedCall
    # Counts that say what differed (a usage mismatch's journal and ledger totals); never content.
    detail: Mapping[str, int] = field(default_factory=lambda: dict[str, int]())


__all__ = [
    "ROUTE_NOT_GRANTED",
    "RUN_UNKNOWN",
    "TOOL_NOT_GRANTED",
    "USAGE_UNRECONCILED",
    "CallSource",
    "ExecutedCall",
    "LedgerAccountView",
    "PinnedGrant",
    "RunEvidence",
    "JournalUsageRecord",
    "Violation",
    "ViolationCode",
]
