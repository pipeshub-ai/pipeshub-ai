"""``RunEvidenceSource`` in memory: what a test or an embedder registers per run."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from harness_core.audit.types import (
    ExecutedCall,
    LedgerAccountView,
    PinnedGrant,
    RunEvidence,
)

if TYPE_CHECKING:
    from harness_core.audit.types import JournalUsageRecord
    from harness_core.budget.types import Amount, LedgerUsage


class InMemoryRunEvidenceSource:
    def __init__(self) -> None:
        self._runs: dict[tuple[str, str], RunEvidence] = {}

    def pin(self, org_id: str, run_id: str, *, tools: tuple[str, ...] = (), routes: tuple[str, ...] = ()) -> None:
        current = self._runs.get((org_id, run_id))
        grant = PinnedGrant(frozenset(tools), frozenset(routes))
        self._runs[(org_id, run_id)] = replace(current, grant=grant) if current else RunEvidence(grant)

    def intent(self, org_id: str, run_id: str, tool_id: str, *, invocation_id: str, attempt_id: str | None = None) -> None:
        current = self._runs[(org_id, run_id)]
        call = ExecutedCall("effect_log", org_id, run_id, tool_id, attempt_id, invocation_id)
        self._runs[(org_id, run_id)] = replace(current, intents=(*current.intents, call))

    def usage(self, org_id: str, run_id: str, *records: JournalUsageRecord) -> None:
        current = self._runs[(org_id, run_id)]
        self._runs[(org_id, run_id)] = replace(current, usage=(*current.usage, *records))

    def account(self, org_id: str, run_id: str, *, used: LedgerUsage | None, reserved: Amount) -> None:
        current = self._runs[(org_id, run_id)]
        self._runs[(org_id, run_id)] = replace(current, ledger=LedgerAccountView(used, reserved))

    def end(self, org_id: str, run_id: str) -> None:
        self._runs[(org_id, run_id)] = replace(self._runs[(org_id, run_id)], ended=True)

    async def evidence(self, org_id: str, run_id: str) -> RunEvidence | None:
        return self._runs.get((org_id, run_id))


__all__ = ["InMemoryRunEvidenceSource"]
