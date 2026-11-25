"""``pin ⊓ live`` at the moment a tool call is decided (NFR-23, NFR-24, NFR-33).

``LiveNarrowedApprovals`` wraps a run's pinned approval policy. Before a decision the effect runner refreshes it,
which reads the org's live layer from the cache (in process while the layer is fresh); the decision is then the
stricter of the pin's and the live layer's, so a rule an admin tightened reaches a running attempt and one they
loosened changes nothing. While the live layer is stale, a call that writes or executes asks at least, and a read
is decided by the pin alone.

Flow rules are not applied here: they need the run's source ledger, so the assembler meets the live layer's with
the pin's when it builds an attempt.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, cast

from harness_core.effects.policy import ApprovalRules, stricter
from harness_core.effects.redaction import redaction_of
from harness_core.effects.types import POLICY_DENIAL, ExplainsAsk, ExplainsDenial

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from pydantic import JsonValue

    from app.control_plane.runtime.live_layer import LiveLayer
    from harness_core.effects.redaction import Redaction
    from harness_core.effects.types import (
        ApprovalDecision,
        ApprovalPolicy,
        EffectKind,
        ToolEffect,
    )
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.logs import HarnessLog

APPROVALS: str = "approvals"
_NO_RULES = ApprovalRules()


def live_approval_rules(layer: LiveLayer, agent_key: str | None) -> ApprovalRules:
    """The live layer's approval ceiling for an agent's runs (the lifted form of ``approval-rules``) as rules."""
    entry = layer.for_agent(agent_key).entries.get(APPROVALS)
    if entry is None:
        return _NO_RULES
    ceiling = cast("Mapping[str, Mapping[str, ApprovalDecision]]", entry.value)
    return ApprovalRules(
        classes=cast("Mapping[EffectKind, ApprovalDecision]", dict(ceiling["classes"])), tools=dict(ceiling["tools"])
    )


def closed_while_stale(effect: ToolEffect) -> bool:
    """What a stale live layer may not let through unasked: a call that writes or executes, whatever its sink (the
    rule that went missing may be about any of them). A read inside the pin stays open (NFR-33)."""
    return effect.needs_intent_commit


class LiveNarrowedApprovals:
    """``pinned`` narrowed by the live layer of the run's org; never looser than ``pinned``."""

    def __init__(
        self,
        pinned: ApprovalPolicy,
        live: Callable[[], Awaitable[LiveLayer]],
        *,
        agent_key: str | None,
        log: HarnessLog,
    ) -> None:
        self._pinned = pinned
        self._live = live
        self._agent_key = agent_key
        self._log = log
        # Until the first refresh nothing is known of the live layer: decided as a stale one.
        self._layer: LiveLayer | None = None
        self._rules = _NO_RULES

    async def refresh(self) -> None:
        try:
            layer = await self._live()
        except Exception as exc:  # a decision must never fail on the live layer's plumbing
            self._log.warning(
                "approvals.live_refresh_failed", reasonCode="live_layer_unavailable", errorType=type(exc).__name__
            )
            if self._layer is not None:
                self._layer = replace(self._layer, stale=True)
            return
        if layer is not self._layer:
            self._layer = layer
            self._rules = live_approval_rules(layer, self._agent_key)

    def _narrowing(self, effect: ToolEffect) -> ApprovalDecision:
        decision = self._rules.decide(effect)
        stale = self._layer is None or self._layer.stale
        return stricter(decision, "ask") if stale and closed_while_stale(effect) else decision

    def __call__(self, call: ToolCall, effect: ToolEffect) -> ApprovalDecision:
        pinned = self._pinned(call, effect)
        decision = stricter(pinned, self._narrowing(effect))
        if decision != pinned:
            self._log.info(
                "approvals.live_narrowed",
                toolId=effect.tool_id,
                decision=decision,
                pinned=pinned,
                liveRevision=self._layer.revision if self._layer is not None else None,
                stale=self._layer is None or self._layer.stale,
            )
        return decision

    def redaction(self, call: ToolCall, effect: ToolEffect) -> Redaction | None:
        return redaction_of(self._pinned, call, effect)

    def denial(self, call: ToolCall, effect: ToolEffect) -> tuple[str, str]:
        if self._pinned(call, effect) == "deny" and isinstance(self._pinned, ExplainsDenial):
            return self._pinned.denial(call, effect)
        return POLICY_DENIAL

    def ask_reason(self, call: ToolCall, effect: ToolEffect) -> Mapping[str, JsonValue] | None:
        """Why the run asks (``ExplainsAsk``): the pin's own reason when the pin asks, else the org's live rule that
        tightened it, else the org's rules that could not be read (a stale layer holds a write). WP-97:
        without this, the pin's reason stopped here and the card had none. The org's ask is never granted."""
        if self(call, effect) != "ask":
            return None
        if self._pinned(call, effect) == "ask" and isinstance(self._pinned, ExplainsAsk):
            reason = self._pinned.ask_reason(call, effect)
            if reason:
                return reason
        rule = self._rules.asking(effect)
        if rule is None and closed_while_stale(effect) and (self._layer is None or self._layer.stale):
            rule = "writes" if effect.effect in ("write", "execute") else "effect_class"
        if rule is None:
            return None
        return {"rule": rule, "layer": "org", "toolId": effect.tool_id, "conversationGrant": False}


__all__ = ["LiveNarrowedApprovals", "closed_while_stale", "live_approval_rules"]
