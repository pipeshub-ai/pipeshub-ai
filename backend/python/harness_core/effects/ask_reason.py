"""The reason an approval card shows, never missing (WP-97).

Every policy that asks says why (``ExplainsAsk``); ``card_reason`` is what the effect runner and the durable loop
hand the hold. A policy that asks without naming its rule (a wrapper that did not pass its pin's reason
on) gets ``plain_reason``, logged at warning so the gap is found and fixed where it is.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from harness_core.effects.types import ExplainsAsk, args_digest

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from harness_core.effects.types import ApprovalPolicy, ToolEffect
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.logs import HarnessLog

# Logged when a policy asked and named no rule: the card shows the plain reason.
REASON_MISSING_LOG: Final = "tool.approval_reason_missing"


def plain_reason(effect: ToolEffect) -> dict[str, JsonValue]:
    """The reason for an ask no rule named: the approval rules hold the call, layer unnamed, never granted. The card
    words it as "An approval rule asks for a decision before this action", which is true whichever rule asked."""
    return {
        "rule": "writes" if effect.effect in ("write", "execute") else "effect_class",
        "toolId": effect.tool_id,
        "conversationGrant": False,
    }


def card_reason(policy: ApprovalPolicy, call: ToolCall, effect: ToolEffect, log: HarnessLog) -> Mapping[str, JsonValue]:
    """Why ``policy`` asks about the call, for the card; the plain reason when it does not say. Either carries the
    call's ``argsDigest`` (R38-AUTHZ): a person's decline covers the same tool with the same arguments in any run of
    the conversation, so the parent of a child whose call was declined is not asked the same thing again."""
    reason = policy.ask_reason(call, effect) if isinstance(policy, ExplainsAsk) else None
    if not reason:
        log.warning(REASON_MISSING_LOG, toolId=effect.tool_id, policy=type(policy).__name__)
        reason = plain_reason(effect)
    return {**reason, "argsDigest": args_digest(call.arguments)}


__all__ = ["REASON_MISSING_LOG", "card_reason", "plain_reason"]
