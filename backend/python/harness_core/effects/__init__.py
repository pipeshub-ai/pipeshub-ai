"""Tool-call effects: how a call is classified, recorded, deduplicated and executed (F1).

Product-neutral: no PipesHub concepts, no clocks, ids or randomness of its own.
Ports: ``EffectLog`` (where intents and outcomes are recorded), ``Reconciler`` and
``ReconcileLookup`` (deciding an unknown effect by a read, P2.9), ``ApprovalPolicy``
(``LayeredApprovalPolicy`` builds one from rules) and ``ApprovalHold`` (who may run a call). Adapters: ``RequestEffectLog`` (in
memory, one request) and ``JournalEffectLog`` (a durable run's journal).
Contract suite: ``harness_core.testing.contracts.effect_log``.
"""

from typing import TYPE_CHECKING

from harness_core.lazy_exports import lazy_exports

if TYPE_CHECKING:
    from harness_core.effects.ambiguity import (
        EffectUnknownFromSend,
        SendTracker,
        note_ambiguous_send,
        track_sends,
    )
    from harness_core.effects.journal import JournalEffectLog
    from harness_core.effects.log import EffectLog, RequestEffectLog
    from harness_core.effects.memory import InMemoryReconcileLookup
    from harness_core.effects.policy import (
        ALLOW_ALL,
        ENGINE_APPROVALS,
        WRITES_ASK,
        ApprovalConfigInvalid,
        ApprovalRules,
        LayeredApprovalPolicy,
        parse_rules,
        stricter,
    )
    from harness_core.effects.reconcile import (
        ReconcileEvidence,
        ReconcileLookup,
        Reconciler,
        ReconcileTiming,
        ReconcileVerdict,
        TimedReconciler,
        UnknownEffect,
        current_effect_tag,
        effect_tag,
        stamped_effect,
    )
    from harness_core.effects.runner import (
        MAX_EFFECT_RETRIES,
        ApprovalHold,
        EffectRunner,
        HoldDecision,
    )
    from harness_core.effects.types import (
        UNKNOWN_TOOL,
        ApprovalDecision,
        ApprovalPolicy,
        EffectCall,
        EffectDisposition,
        EffectKey,
        EffectKind,
        EffectResult,
        EffectUnknown,
        RecordedEffect,
        RecordedState,
        ToolContext,
        ToolEffect,
        ToolExecution,
        allow_all,
        args_digest,
        tool_id_for,
    )

# Each name's submodule, imported when the name is first read (R25OPS-FU-1).
_EXPORTS = {
    "EffectUnknownFromSend": "ambiguity",
    "SendTracker": "ambiguity",
    "note_ambiguous_send": "ambiguity",
    "track_sends": "ambiguity",
    "JournalEffectLog": "journal",
    "EffectLog": "log",
    "RequestEffectLog": "log",
    "InMemoryReconcileLookup": "memory",
    "ALLOW_ALL": "policy",
    "ENGINE_APPROVALS": "policy",
    "WRITES_ASK": "policy",
    "ApprovalConfigInvalid": "policy",
    "ApprovalRules": "policy",
    "LayeredApprovalPolicy": "policy",
    "parse_rules": "policy",
    "stricter": "policy",
    "ReconcileEvidence": "reconcile",
    "ReconcileLookup": "reconcile",
    "Reconciler": "reconcile",
    "ReconcileTiming": "reconcile",
    "ReconcileVerdict": "reconcile",
    "TimedReconciler": "reconcile",
    "UnknownEffect": "reconcile",
    "current_effect_tag": "reconcile",
    "effect_tag": "reconcile",
    "stamped_effect": "reconcile",
    "MAX_EFFECT_RETRIES": "runner",
    "ApprovalHold": "runner",
    "EffectRunner": "runner",
    "HoldDecision": "runner",
    "UNKNOWN_TOOL": "types",
    "ApprovalDecision": "types",
    "ApprovalPolicy": "types",
    "EffectCall": "types",
    "EffectDisposition": "types",
    "EffectKey": "types",
    "EffectKind": "types",
    "EffectResult": "types",
    "EffectUnknown": "types",
    "RecordedEffect": "types",
    "RecordedState": "types",
    "ToolContext": "types",
    "ToolEffect": "types",
    "ToolExecution": "types",
    "allow_all": "types",
    "args_digest": "types",
    "tool_id_for": "types",
}
__getattr__, __dir__ = lazy_exports(__name__, _EXPORTS)

__all__ = [
    "InMemoryReconcileLookup",
    "ReconcileEvidence",
    "ReconcileLookup",
    "ReconcileTiming",
    "ReconcileVerdict",
    "Reconciler",
    "TimedReconciler",
    "UnknownEffect",
    "current_effect_tag",
    "effect_tag",
    "stamped_effect",
    "ALLOW_ALL",
    "ENGINE_APPROVALS",
    "WRITES_ASK",
    "ApprovalConfigInvalid",
    "ApprovalRules",
    "LayeredApprovalPolicy",
    "parse_rules",
    "stricter",
    "MAX_EFFECT_RETRIES",
    "UNKNOWN_TOOL",
    "ApprovalDecision",
    "ApprovalHold",
    "ApprovalPolicy",
    "EffectCall",
    "EffectDisposition",
    "EffectKey",
    "EffectKind",
    "EffectLog",
    "EffectResult",
    "EffectRunner",
    "EffectUnknown",
    "EffectUnknownFromSend",
    "HoldDecision",
    "JournalEffectLog",
    "RecordedEffect",
    "RecordedState",
    "RequestEffectLog",
    "SendTracker",
    "ToolContext",
    "ToolEffect",
    "ToolExecution",
    "allow_all",
    "args_digest",
    "note_ambiguous_send",
    "tool_id_for",
    "track_sends",
]
