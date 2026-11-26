"""C9.01, EV-10: an ended run's journal usage reconciled with its ledger account (pure).

The gateway ledger is the authority on a run's usage (OWD-8): every model call's settlement tells it the provider's
counts. The journal must say the same: each turn's ``model.response`` (an engine turn's is read from the ledger) and
each ``usage.recorded`` (a call no turn counts, such as an engine run's wrap-up answer). A run is decided once it
ended and its account holds nothing more (a late settlement could still land); until then it stays pending.
"""

from __future__ import annotations

import functools
import operator
from typing import TYPE_CHECKING, Literal

from harness_core.audit.types import USAGE_UNRECONCILED, ExecutedCall, Violation
from harness_core.budget.types import ZERO, LedgerUsage
from harness_core.kernel.fold import journaled_input_tokens

if TYPE_CHECKING:
    from collections.abc import Iterable

    from harness_core.audit.types import JournalUsageRecord, RunEvidence

UsageVerdict = Literal["pending", "unmetered", "reconciled", "unreconciled"]


def journal_usage(records: Iterable[JournalUsageRecord]) -> LedgerUsage:
    """The journal's total, every input counting cached tokens as the fold counts it."""
    return functools.reduce(operator.add, map(_counted, records), LedgerUsage())


def _counted(record: JournalUsageRecord) -> LedgerUsage:
    input_tokens = (
        journaled_input_tokens(
            record.input_tokens,
            record.cache_read_tokens,
            record.cache_write_tokens,
            includes_cached=record.input_includes_cached,
            provider=record.provider,
            transport=record.transport,
        )
        if record.type == "model.response"
        else record.input_tokens
    )
    return LedgerUsage(input_tokens, record.output_tokens, record.cache_read_tokens, record.cache_write_tokens)


def reconcile_usage(org_id: str, run_id: str, evidence: RunEvidence) -> tuple[UsageVerdict, Violation | None]:
    ledger = evidence.ledger
    if ledger is None or ledger.used is None:
        return "unmetered", None
    if not evidence.ended or ledger.reserved != ZERO:
        return "pending", None
    journal = journal_usage(evidence.usage)
    if journal == ledger.used:
        return "reconciled", None
    detail = {
        "journalInputTokens": journal.input_tokens,
        "ledgerInputTokens": ledger.used.input_tokens,
        "journalOutputTokens": journal.output_tokens,
        "ledgerOutputTokens": ledger.used.output_tokens,
        "journalCacheReadTokens": journal.cache_read_tokens,
        "ledgerCacheReadTokens": ledger.used.cache_read_tokens,
    }
    return "unreconciled", Violation(USAGE_UNRECONCILED, ExecutedCall("usage_ledger", org_id, run_id, "usage"), detail)


__all__ = ["UsageVerdict", "journal_usage", "reconcile_usage"]
