"""The flow guard on today's loop (UX3, R1 F2/F12; FG2).

The durable loop holds a call that would send data outside the organization after the run read private data,
for the user to approve (UX1, ``FlowGuardedPolicy``). Today's loop still serves the modes the durable one does not
(planExecute, Universal Agent Mode, project chats, a regenerate through the API, the kill switch), and it cannot hold
a call for a person mid-run. So the same rules refuse there instead of letting the call through: the model reads one
typed refusal and tells the user what was not done and where they can approve it.

The rules are the durable loop's: the deployment's and the org's and agent's own (``refusing_flow_rules``), so an org
that turned the rule on keeps it with the deployment's switch off (QA10-F1). What the request has seen is the
conversation's history, by the label Node joins over its messages (FG2; none, or one it cannot read, is unknown: the
history comes from the client), and every call whose result may carry private content, sends included (QA10-F2): a
knowledge search's records are private, anything nobody can name unknown. The loop's machinery, the question to the
user and public web results carry none. The product's web search always runs, and its page fetch too, for an address
the request was given (``app.web.addresses``); once a rule holds, a fetch of any other address is refused as the
durable loop refuses it. The same notes make the answer's own label (``request_label``), which Node
records so the next turn can decide from it.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Final

from app.agents.agent_loop.hooks._tool_naming import resolve_tool_name
from app.harness.citations import CitationJournal
from app.harness.labels import (
    citation_sources,
    label_from_wire,
    label_wire,
    tool_kind,
    unknown_label,
)
from app.harness.run_policy.select import legacy_flow_rules
from app.web.addresses import RunAddresses, user_turns
from harness_core.effects.flow import (
    WEB_ADDRESS_DENIAL,
    WEB_ADDRESS_RULE,
    external_write,
    flow_holds,
    unvouched_address,
)
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable.effects import classify_tool_effect
from harness_core.kernel.hooks.events import HookEvent
from harness_core.logs import HarnessLog
from harness_core.tools.tags import TAG_OUTPUT_DERIVED, TAG_PROVIDER_PLATFORM

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic import JsonValue

    from app.agent_loop_lib.hooks.middleware.context import ToolCallContext
    from app.agent_loop_lib.hooks.middleware.pipeline import Middleware, Next
    from harness_core.effects.flow import FlowRule, Sensitivity
    from harness_core.effects.types import ToolEffect
    from harness_core.kernel.hooks.registry import HookRegistry
    from harness_core.tools.base import Tag

logger = logging.getLogger(__name__)

_NOT_HERE = (
    "this chat cannot ask the user to approve it, so it was not done. Tell the user, in their language, that it was "
    "not done and that they can do it from an agent chat in auto, quick or deep mode, where they approve it first. "
    "Do not retry it here."
)
LEGACY_FLOW_REFUSAL: Final = (
    "approval_unavailable: this action would send content outside the organization after this conversation read "
    "company content, and " + _NOT_HERE
)
LEGACY_SEND_REFUSAL: Final = (
    "approval_unavailable: an earlier action in this request already sent content outside the organization and its "
    "result is now part of this conversation; this action would send more, and " + _NOT_HERE
)
LEGACY_WEB_REFUSAL: Final = (
    "approval_unavailable: this web request would carry text chosen in this conversation (in its address or query) "
    "to a site outside the organization after this conversation read company content, and " + _NOT_HERE
)
# A page fetch to an address the request was not given, after it read company content: refused as the durable loop
# refuses it, with nothing to approve.
LEGACY_ADDRESS_REFUSAL: Final = WEB_ADDRESS_DENIAL[1]
LEGACY_REFUSALS: Final = (LEGACY_FLOW_REFUSAL, LEGACY_SEND_REFUSAL, LEGACY_WEB_REFUSAL, LEGACY_ADDRESS_REFUSAL)

_SEEN = "flow_guard_seen"
_SENT_ONLY = "flow_guard_sent_only"
_RANK: Final[dict[Sensitivity, int]] = {"public": 0, "private": 1, "unknown": 2}
_ARTIFACT_RECEIPTS = frozenset(
    f"artifacts{sep}{tool}" for tool in ("save_artifact", "update_artifact", "promote_artifact") for sep in ("__", "_")
)

# The loop's own machinery (spawning a sub-agent, planning, critique, ending the run, tool disclosure) sends
# nothing anywhere; the calls it leads to are guarded themselves. Untagged, these would classify as writes (fail
# closed), as MCP tools rightly do.
_LOOP_MACHINERY_PATH = "/toolsets/builtin/"
_LOOP_MACHINERY_TAG_KEYS = frozenset({"spawn", "planning", "lifecycle"})


def is_loop_machinery(tool_path: str, tags: Iterable[Tag]) -> bool:
    return tool_path.startswith(_LOOP_MACHINERY_PATH) or any(tag.key in _LOOP_MACHINERY_TAG_KEYS for tag in tags)


def _unresolved(ctx: ToolCallContext) -> bool:
    """A call to a tool the run lacks: it never runs, and the loop answers it with its own error text."""
    registry = ctx.scope.turn.run.runtime.tool_registry if ctx.scope is not None else None
    return registry is not None and not registry.has_path(ctx.tool_path)


def _history(context: Any) -> Sensitivity:  # noqa: ANN401 - the request's AgentContext
    """What the conversation's history read, by the label Node joined over its messages; none sent, or one that
    cannot be read, is unknown (today's loop gets its history from the client and cannot vouch for it)."""
    if not getattr(context, "previous_conversations", None):
        return "public"
    label = label_from_wire(getattr(context, "history_label", None))
    if label is None or not label.complete:
        return "unknown"
    return "private" if label.sources else "public"


def _seen(context: Any) -> Sensitivity:  # noqa: ANN401
    found: Sensitivity = context.tool_state.get(_SEEN, "public")
    history = _history(context)
    return history if _RANK[history] > _RANK[found] else found


def _note_result(context: Any, name: str, ctx: ToolCallContext, effect: ToolEffect) -> None:  # noqa: ANN401
    """What the call about to run will put in front of the model."""
    kind = tool_kind(name)
    if (
        is_loop_machinery(ctx.tool_path, ctx.tags)
        or kind in ("none", "public")
        or name in _ARTIFACT_RECEIPTS
        or {TAG_OUTPUT_DERIVED, TAG_PROVIDER_PLATFORM} <= set(ctx.tags)
    ):
        return
    sensitivity: Sensitivity = "private" if kind == "records" else "unknown"
    state = context.tool_state
    state[_SENT_ONLY] = external_write(effect) and state.get(_SENT_ONLY, _seen(context) == "public")
    if _RANK[sensitivity] > _RANK[state.get(_SEEN, "public")]:
        state[_SEEN] = sensitivity
    state.setdefault(_KINDS, set()).add(kind)


_KINDS = "flow_guard_result_kinds"
_WATCHED = "flow_guard_watched"


def request_label(context: Any) -> dict[str, JsonValue] | None:  # noqa: ANN401
    """The request's own work as a turn label (P2.5a's wire form), what Node records on the answer so a later turn
    knows what this one read: records its knowledge tools cited, or unknown once a result nobody can name reached the
    model. The history it was given is not in it: Node joins the conversation's messages. None where the guard did
    not watch the request (the durable loop, which records its own label)."""
    if not context.tool_state.get(_WATCHED):
        return None
    kinds: set[str] = context.tool_state.get(_KINDS, set())
    if "opaque" in kinds:
        return label_wire(unknown_label())
    if "records" not in kinds:
        return {"complete": True, "sources": []}
    return label_wire(citation_sources(CitationJournal(context).delta() or {}))


def _refusal(effect: ToolEffect, context: Any) -> str:  # noqa: ANN401
    if not external_write(effect):
        return LEGACY_WEB_REFUSAL
    return LEGACY_SEND_REFUSAL if context.tool_state.get(_SENT_ONLY) and _history(context) == "public" else LEGACY_FLOW_REFUSAL


def legacy_flow_guard(context: Any, rules: tuple[FlowRule, ...]) -> Middleware[ToolCallContext]:  # noqa: ANN401
    """PRE_TOOL_USE: refuse a call that sends data out once the request has seen what the rules hold; note what each
    call that runs will return (also with no rule, for the answer's label)."""

    addresses = RunAddresses(
        context.tool_state,
        user_text=[getattr(context, "query", None), *user_turns(getattr(context, "previous_conversations", None) or [])],
    )

    async def _middleware(ctx: ToolCallContext, next_fn: Next) -> None:
        if _unresolved(ctx):
            await next_fn()
            return
        name = resolve_tool_name(ctx)
        effect = classify_tool_effect(name, ctx.tags)
        sensitivity = _seen(context)
        if (
            unvouched_address(ToolCall(id=str(ctx.tool_use_id), name=name, arguments=ctx.tool_input), effect, addresses)
            and any(rule.holds(sensitivity) for rule in rules)
        ):
            logger.info("effects.web_address_refused_legacy toolId=%s sensitivity=%s", effect.tool_id, sensitivity)
            ctx.deny(LEGACY_ADDRESS_REFUSAL, code=WEB_ADDRESS_RULE)
            return
        if flow_holds(effect) and not is_loop_machinery(ctx.tool_path, ctx.tags):
            refusing = next((rule for rule in rules if rule.decide(sensitivity, effect) != "allow"), None)
            if refusing is not None:
                logger.info(
                    "effects.flow_refused_legacy toolId=%s sensitivity=%s level=%s",
                    effect.tool_id,
                    sensitivity,
                    refusing.level,
                )
                ctx.deny(_refusal(effect, context))
                return
        _note_result(context, name, ctx, effect)
        await next_fn()

    return _middleware


async def register_legacy_flow_guard(hooks: HookRegistry, context: Any) -> bool:  # noqa: ANN401
    """Adds the guard to today's loop; says whether any flow rule applies to the request (without one it only notes
    what the request read, for the answer's label). The rules are the turn's resolved ones when the request carries
    them (``context.resolved_flow_rules``), else the approvals document's while it is the record; with neither it
    raises ``ResolvedPolicyRequired`` and registers nothing."""
    rules = await legacy_flow_rules(
        context.config_service,
        org_id=context.org_id,
        agent_key=getattr(context, "agent_key", None),
        resolved=getattr(context, "resolved_flow_rules", None),
        log=HarnessLog.named("app.agents.agent_loop.flow_guard"),
    )
    context.tool_state[_WATCHED] = True
    # The hook library's Pipeline is not generic over its context, hence the ignore.
    hooks.on(HookEvent.PRE_TOOL_USE).use(legacy_flow_guard(context, rules))  # pyright: ignore[reportUnknownMemberType, reportUnusedCallResult]
    return bool(rules)


__all__ = [
    "LEGACY_ADDRESS_REFUSAL",
    "LEGACY_FLOW_REFUSAL",
    "LEGACY_REFUSALS",
    "LEGACY_SEND_REFUSAL",
    "LEGACY_WEB_REFUSAL",
    "is_loop_machinery",
    "legacy_flow_guard",
    "register_legacy_flow_guard",
    "request_label",
]
