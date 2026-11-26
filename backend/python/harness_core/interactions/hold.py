"""``BridgeApprovalHold``: the ``EffectRunner``'s approval hold over an ``InteractionBridge``.

The interaction is named by the call's effect key (run, tool, arguments), not
its invocation: an engine that re-calls a tool after a deferral gets a new
invocation id, and must find the approval it is waiting on (E2 FB-3). Nothing
is recorded in the effect log before the approval: a held call that is never
approved left no intent behind, so it can never become an unknown effect.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from harness_core.contracts import canonical_sha256
from harness_core.effects.edits import EditsApproved
from harness_core.interactions.ports import InteractionRequest, SaysWhy

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from harness_core.effects.edits import ApprovedEdit
    from harness_core.effects.runner import HoldDecision
    from harness_core.effects.types import EffectCall, EffectKey
    from harness_core.interactions.ports import InteractionBridge, RunBinding

# Engines' MCP tool timeout is at least 150 s, so a hold of 120 s answers in time.
APPROVAL_HOLD_S = 120


def approval_interaction_id(key: EffectKey) -> str:
    digest = canonical_sha256(
        {"runId": key.run_id, "toolId": key.tool_id, "argsDigest": key.args_digest}
    )
    return f"ix.approve.k{digest[:40]}"


@dataclass(frozen=True)
class BridgeApprovalHold:
    bridge: InteractionBridge
    binding: RunBinding
    hold_s: float = APPROVAL_HOLD_S
    # Why each call asks, from the runner's ``explain``: the request carries it into the journal (WP-97).
    _reasons: dict[str, Mapping[str, JsonValue]] = field(default_factory=dict[str, "Mapping[str, JsonValue]"])

    def interaction_for(self, call: EffectCall) -> str:
        return approval_interaction_id(call.key)

    def explain(self, call: EffectCall, reason: Mapping[str, JsonValue]) -> None:
        self._reasons[self.interaction_for(call)] = reason

    async def hold(self, call: EffectCall) -> HoldDecision | None:
        ix = self.interaction_for(call)
        ticket = await self.bridge.request(
            self.binding,
            InteractionRequest(ix, "approval", call.effect.tool_id, call.args_digest, reason=self._reasons.get(ix)),
        )
        decision = await self.bridge.wait(ticket, within_s=self.hold_s)
        if decision is None:
            return None
        # Expired or cancelled is not an approval: the call is refused, never run.
        return "approved" if decision == "approved" else "denied"

    def denial_reason(self, call: EffectCall) -> str | None:
        return self.bridge.said(self.interaction_for(call)) if isinstance(self.bridge, SaysWhy) else None

    def approved_edit(self, call: EffectCall) -> ApprovedEdit | None:
        """The arguments the person approved the call with in place of its own (AP-06); ``None``: as asked."""
        return self.bridge.edit(self.interaction_for(call)) if isinstance(self.bridge, EditsApproved) else None


__all__ = ["APPROVAL_HOLD_S", "BridgeApprovalHold", "approval_interaction_id"]
