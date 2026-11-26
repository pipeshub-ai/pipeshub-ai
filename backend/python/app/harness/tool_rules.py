"""Main's per-tool approval rules (MCP #4003, toolsets #4256) on a durable run (SYNC3).

Today's loop asks with its own card and parks the turn; a durable run asks through its kernel's
approval interaction instead (needs_input, resumed by the person's answer). Which calls ask, run or
are refused is main's decision (`tool_approvals.decide`: the instance's or the person's rule, else
the tool's kind); this layer only narrows the run's own policy with it, so the stricter of the two
wins. The gate itself still runs in the tool executor and, told by ``KERNEL_ASKS``, refuses only
what is blocked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.agents.agent_loop.tool_approvals import (
    _RULES_CACHE,
    KERNEL_ASKS,
    _blocked_message,
    _rules_for,
    _target,
    decide,
    unresolved_mcp_call,
    unresolved_message,
)
from harness_core.effects.flow import destination_wire, grant_answerable
from harness_core.effects.policy import stricter
from harness_core.effects.redaction import redaction_of
from harness_core.effects.types import (
    POLICY_DENIAL,
    ExplainsAsk,
    ExplainsDenial,
    refresh_policy,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from pydantic import JsonValue

    from app.agents.agent_loop.context import AgentContext
    from harness_core.effects.flow import AskDestination, ConversationGrants
    from harness_core.effects.redaction import Redaction
    from harness_core.effects.types import ApprovalDecision, ApprovalPolicy, ToolEffect
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.tools.registry import ToolRegistry

TOOL_RULES_DENIAL = "tool_rules"


@dataclass
class ToolRulesPolicy:
    base: ApprovalPolicy
    context: AgentContext
    registry: ToolRegistry
    # Per path: main's rule for the call, or a refusal text. Filled by ``refresh``; deciding reads nothing.
    _rules: dict[str, tuple[str, str]] = field(default_factory=dict)
    # The session's "allow for this conversation" answers, and where a call goes: they answer main's default ask
    # (a write asks first) for that tool and destination, never an admin's own Ask or Block.
    grants: ConversationGrants | None = None
    destination: Callable[[ToolCall, ToolEffect], AskDestination | None] | None = None
    # Paths whose rule is a person's or an instance's own setting, not the tool kind's default.
    _explicit: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.context.tool_state[KERNEL_ASKS] = True

    async def refresh(self) -> None:
        await refresh_policy(self.base)
        # An Ask is read again at every decision: the card's "Don't ask again" saves the person's rule mid-run.
        for path in [p for p, (rule, _) in self._rules.items() if rule == "ask"]:
            target = _target(self.context, path)
            if target is not None:
                self.context.tool_state.get(_RULES_CACHE, {}).pop((target.source, target.instance_id), None)
            del self._rules[path]
            self._explicit.discard(path)
        for name in self.registry.names():
            path = self.registry.path_for_name(name)
            target = _target(self.context, path) if path else None
            if target is None or path in self._rules:
                continue
            rules = await _rules_for(self.context, target.source, target.instance_id, personal=target.personal)
            if rules.unavailable:
                self._rules[path] = ("block", (
                    f"The approval rules for {target.tool_name} on {target.owner} couldn't be checked, "
                    "so it wasn't run. Tell the user to try again in a moment."
                ))
                continue
            # A durable run always has someone to ask, a service-account agent's too (ADR-OWD-09 R34): the kernel's
            # interaction reaches the person who started the run (the chat, or a schedule's actor in the inbox) and the
            # approvals queue whoever else may decide. Only today's loop, with no card for such a run, reads Ask as Block.
            own = rules.rules.tools.get(target.tool_name)
            verdict = decide(own=own, kind=target.kind, source=target.source, watched=True)
            self._rules[path] = (verdict.rule, _blocked_message(verdict.reason, target) if verdict.rule == "block" else "")
            if own is not None:
                self._explicit.add(path)

    def _rule(self, call: ToolCall) -> tuple[str, str]:
        path = self.registry.path_for_name(call.name)
        if not path:
            return ("allow", "")
        # Read at each decision, never cached: the server's adapter may be registered later in the run.
        if path not in self._rules and unresolved_mcp_call(self.context, path):
            return ("block", unresolved_message(path))
        return self._rules.get(path, ("allow", ""))

    def _default_ask(self, call: ToolCall) -> bool:
        path = self.registry.path_for_name(call.name)
        return self._rule(call)[0] == "ask" and bool(path) and path not in self._explicit

    def _where(self, call: ToolCall, effect: ToolEffect) -> AskDestination | None:
        return self.destination(call, effect) if self.destination is not None else None

    def _own(self, call: ToolCall, effect: ToolEffect) -> ApprovalDecision:
        rule, _ = self._rule(call)
        if self._default_ask(call) and self.grants is not None:
            where = self._where(call, effect)
            if where is not None and self.grants.allows(effect.tool_id, where.key):
                return "allow"
        return "deny" if rule == "block" else rule  # type: ignore[return-value]

    def __call__(self, call: ToolCall, effect: ToolEffect) -> ApprovalDecision:
        return stricter(self.base(call, effect), self._own(call, effect))

    def lifts_default(self, call: ToolCall, effect: ToolEffect) -> bool:
        """The person's or the instance's own Allow of this tool (D-ALLOW-1), not the tool kind's default."""
        path = self.registry.path_for_name(call.name)
        return bool(path) and path in self._explicit and self._rule(call)[0] == "allow"

    def grant_answers(self, call: ToolCall, effect: ToolEffect) -> bool:
        own = self._own(call, effect)
        return (own == "allow" or self._default_ask(call)) and grant_answerable(self.base, call, effect)

    def redaction(self, call: ToolCall, effect: ToolEffect) -> Redaction | None:
        return redaction_of(self.base, call, effect)

    def denial(self, call: ToolCall, effect: ToolEffect) -> tuple[str, str]:
        rule, text = self._rule(call)
        if rule == "block":
            return TOOL_RULES_DENIAL, text
        return self.base.denial(call, effect) if isinstance(self.base, ExplainsDenial) else POLICY_DENIAL

    def ask_reason(self, call: ToolCall, effect: ToolEffect) -> Mapping[str, JsonValue] | None:
        """Why the card asks (SYNC3 open item): the run's own policy when it asks, else main's rule, named as a
        tool rule a person or an instance set, or the default that a write asks first. Only the default may be
        allowed for the conversation, and only where the call's destination can be named."""
        if isinstance(self.base, ExplainsAsk) and self.base(call, effect) == "ask":
            return self.base.ask_reason(call, effect)
        path = self.registry.path_for_name(call.name)
        if self._rule(call)[0] != "ask" or not path:
            return self.base.ask_reason(call, effect) if isinstance(self.base, ExplainsAsk) else None
        # An instance's own rule is the organization's; the kind's starting rule is the product's default.
        explicit = path in self._explicit
        where = None if explicit or self.grants is None or not self.grants.enabled else self._where(call, effect)
        reason: dict[str, JsonValue] = {
            "rule": "tool" if explicit else "writes", "layer": "org" if explicit else "deployment",
            "toolId": effect.tool_id, "conversationGrant": where is not None and self.grant_answers(call, effect),
        }
        if where is not None:
            reason["destination"] = destination_wire(where)
        target = _target(self.context, path)
        if not explicit and target is not None and target.source == "mcp":
            # Main's "Don't ask again" saves the person's own rule, so only for their MCP tool: a toolset's are the admin's.
            reason["ruleGrant"] = {"instanceId": target.instance_id, "toolName": target.tool_name}
        return reason


__all__ = ["TOOL_RULES_DENIAL", "ToolRulesPolicy"]
