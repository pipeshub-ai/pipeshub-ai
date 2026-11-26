"""The grant auditor (P9): every call the gateways served, and every intent the effect log journaled, checked
against the run's pinned grant; a violation is reported once, however often it is read again.

It also reconciles each run it reads (C9.01): once the run ended, its journal usage must equal what its ledger
account holds (``harness_core.audit.usage``). A run not yet decidable is read again on later passes."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from harness_core.audit.grants import check_call, never
from harness_core.audit.usage import reconcile_usage
from harness_core.logs import HarnessLog

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from harness_core.audit.ports import RunEvidenceSource
    from harness_core.audit.types import ExecutedCall, RunEvidence, Violation


@dataclass(frozen=True, slots=True)
class AuditReport:
    calls: int
    runs: int
    malformed: int
    # Violations first seen in this pass.
    violations: tuple[Violation, ...]
    # Ended runs whose journal usage matched their ledger in this pass (C9.01).
    reconciled: int = 0


class GrantAuditor:
    # Runs read before they could be reconciled, kept to read again; the oldest are dropped past this.
    MAX_PENDING_RUNS = 10_000

    def __init__(
        self,
        source: RunEvidenceSource,
        *,
        outside_grant: Callable[[str], bool] = never,
        log: HarnessLog | None = None,
    ) -> None:
        self._source = source
        self._outside = outside_grant
        self._log = log or HarnessLog.named("harness_core.audit")
        self._seen: set[tuple[str, str, str, str, str]] = set()
        self._pending: OrderedDict[tuple[str, str], None] = OrderedDict()

    async def audit(self, calls: Iterable[ExecutedCall], *, malformed: int = 0) -> AuditReport:
        """Checks ``calls`` and the effect log of every run they name (read again each pass: it grows)."""
        served = list(calls)
        evidence: dict[tuple[str, str], RunEvidence | None] = {}
        for run in (*((c.org_id, c.run_id) for c in served), *list(self._pending)):
            if run not in evidence:
                evidence[run] = await self._source.evidence(*run)
        journaled = [intent for found in evidence.values() if found is not None for intent in found.intents]
        new: list[Violation] = []
        for call in (*served, *journaled):
            violation = check_call(call, evidence.get((call.org_id, call.run_id)), outside_grant=self._outside)
            if violation is not None:
                self._report(violation, new)
        reconciled = 0
        for (org_id, run_id), found in evidence.items():
            verdict, violation = ("unmetered", None) if found is None else reconcile_usage(org_id, run_id, found)
            if verdict == "pending":
                self._remember((org_id, run_id))
                continue
            _ = self._pending.pop((org_id, run_id), None)
            if verdict == "reconciled":
                reconciled += 1
            if violation is not None:
                self._report(violation, new)
        report = AuditReport(len(served) + len(journaled), len(evidence), malformed, tuple(new), reconciled)
        self._log.info(
            "audit.checked",
            calls=report.calls,
            runs=report.runs,
            malformed=malformed,
            violations=len(new),
            reconciled=reconciled,
            pending=len(self._pending),
        )
        return report

    def _report(self, violation: Violation, new: list[Violation]) -> None:
        call = violation.call
        if call.key in self._seen:
            return
        self._seen.add(call.key)
        new.append(violation)
        self._log.error(
            "audit.violation",
            orgId=call.org_id,
            runId=call.run_id,
            attemptId=call.attempt_id,
            invocationId=call.invocation_id,
            reason=violation.code,
            source=call.source,
            subject=call.subject,
            **violation.detail,
        )

    def _remember(self, run: tuple[str, str]) -> None:
        self._pending[run] = None
        self._pending.move_to_end(run)
        while len(self._pending) > self.MAX_PENDING_RUNS:
            _ = self._pending.popitem(last=False)


__all__ = ["AuditReport", "GrantAuditor"]
