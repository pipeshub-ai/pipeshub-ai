"""What the durable kernel journals when it reconciles an unknown effect (P2.9).

The decision itself is the ``Reconciler``'s (``harness_core.effects.reconcile``);
this module turns an invocation into the effect it asks about and a verdict into
events. ``tool.reconciled`` is written non-critical: a fold that does not know
it parks the effect as before, which never re-calls.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from harness_core.effects.reconcile import ReconcileVerdict, UnknownEffect
from harness_core.effects.types import ToolExecution
from harness_core.journal.types import NewEvent

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_core.effects.reconcile import ReconcileEvidence
    from harness_core.effects.types import ToolEffect
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.kernel.fold import Invocation

ReconcileSource = Literal["reconciler", "operator"]
# An unknown effect's interaction resolved by the operator console (P2.9): the decision's meaning.
OPERATOR_APPLIED = "answered"
OPERATOR_ABANDONED = "cancelled"
ABANDONED_ERROR = "effect_abandoned"
OPERATOR_EVIDENCE = "an operator confirmed the effect was applied"
# A re-call journals its own intent: a second intent means the one re-call happened.
RECALL_SPENT_AT = 2


def unknown_effect_for(
    inv: Invocation,
    call: ToolCall,
    effect: ToolEffect,
    *,
    effect_key: str,
    run_id: str,
    lease_s: float,
    now: datetime,
) -> UnknownEffect:
    return UnknownEffect(
        run_id=run_id,
        invocation_id=inv.invocation_id,
        tool_id=effect.tool_id,
        tool_name=call.name,
        arguments=dict(call.arguments),
        effect_key=effect_key,
        dispatched_at=_journal_time(inv.intent_ts, now),
        lease_s=lease_s,
    )


def with_recall_rule(verdict: ReconcileVerdict, inv: Invocation) -> ReconcileVerdict:
    """``not_applied`` permits one re-call; after it, a person decides."""
    if verdict.answer == "not_applied" and inv.intents >= RECALL_SPENT_AT:
        return replace(verdict, answer="still_unknown", reason="recall_spent")
    return verdict


def reconciled_event(
    invocation_id: str, verdict: ReconcileVerdict, source: ReconcileSource = "reconciler"
) -> NewEvent:
    return NewEvent(
        "tool.reconciled",
        {
            "invocationId": invocation_id,
            "verdict": verdict.answer,
            "reason": verdict.reason,
            "lookups": verdict.lookups,
            "source": source,
        },
        critical=False,
    )


def applied_execution(evidence: ReconcileEvidence, source: ReconcileSource) -> ToolExecution:
    receipt: dict[str, JsonValue] = dict(evidence.receipt)
    receipt["reconciled"] = {"verdict": "applied", "source": source}
    return ToolExecution(status="ok", content=evidence.content, receipt=receipt)


def operator_verdict() -> ReconcileVerdict:
    return ReconcileVerdict("applied", "found")


def _journal_time(ts: str | None, now: datetime) -> datetime:
    """An intent's DB time; without one the window starts now (the conservative end)."""
    if ts is None:
        return now
    parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def is_unknown_interaction(inv: Invocation) -> bool:
    return (inv.interaction_id or "").startswith("ix.unknown.")


__all__ = [
    "ABANDONED_ERROR",
    "OPERATOR_ABANDONED",
    "OPERATOR_APPLIED",
    "OPERATOR_EVIDENCE",
    "RECALL_SPENT_AT",
    "ReconcileSource",
    "applied_execution",
    "is_unknown_interaction",
    "operator_verdict",
    "reconciled_event",
    "unknown_effect_for",
    "with_recall_rule",
]
