"""ADR-OWD-09 D-ALLOW-1: a call's own specific Allow lifts a host's class default (``DefaultAskPolicy``), nothing else.

Precedence, strongest first: a block (any layer) and the flow guard; an org rule that asks; a specific per-tool Allow
(``LiftsDefault``), which lifts the engine's class default for that tool only; with no specific rule, the class
default (an engine run's writes ask) and the class defaults under it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from harness_core.effects.flow import (
    ENGINE_WRITES_RULE,
    FLOW_RULE,
    DefaultAskPolicy,
    FlowGuardedPolicy,
    FlowRule,
)
from harness_core.effects.policy import (
    ENGINE_APPROVALS,
    WRITES_ASK,
    ApprovalRules,
    LayeredApprovalPolicy,
    stricter,
)
from harness_core.effects.types import (
    ApprovalDecision,
    ApprovalPolicy,
    ExplainsAsk,
    LiftsDefault,
    ToolEffect,
    allow_all,
    default_lifted,
)
from tests.unit.harness_core.effects.test_ux1_flow_ask import (
    POST,
    READ,
    SAVE,
    Ledger,
    call,
    where,
)

if TYPE_CHECKING:
    from harness_core.kernel.core.messages import ToolCall

ORG_ASKS = LayeredApprovalPolicy((WRITES_ASK,))
ORG_DENIES = LayeredApprovalPolicy((ApprovalRules(classes={"write": "deny"}),))
DENY_DEFAULT = LayeredApprovalPolicy((ApprovalRules(classes={"write": "deny"}),))


@dataclass
class _ToolRules:
    """A per-tool rule store over the org's rules, as ``ToolRulesPolicy`` is: the stricter of the two decides."""

    base: ApprovalPolicy
    rules: dict[str, ApprovalDecision]

    def __call__(self, call: ToolCall, effect: ToolEffect) -> ApprovalDecision:
        return stricter(self.base(call, effect), self.rules.get(effect.tool_id, "allow"))

    def lifts_default(self, call: ToolCall, effect: ToolEffect) -> bool:
        return self.rules.get(effect.tool_id) == "allow"

    def ask_reason(self, call: ToolCall, effect: ToolEffect) -> dict[str, object] | None:
        if isinstance(self.base, ExplainsAsk) and self.base(call, effect) == "ask":
            return dict(self.base.ask_reason(call, effect) or {})
        return {"rule": "tool"} if self.rules.get(effect.tool_id) == "ask" else None


def _engine(base: ApprovalPolicy, *, default: ApprovalPolicy = ENGINE_APPROVALS, read: bool = False,
            flow: FlowRule | None = None) -> DefaultAskPolicy:
    ledger = Ledger(frozenset({"rec-1"}) if read else frozenset())
    if flow is not None:
        base = FlowGuardedPolicy(base, flow, ledger, "r", destination=where)  # type: ignore[arg-type]
    return DefaultAskPolicy(base, default, ENGINE_WRITES_RULE, ledger, "r", destination=where)  # type: ignore[arg-type]


POST_ID = POST.tool_id


# (the org's rules, the tool's own rule, the engine default, the flow rule, the run read private data, decided, asked by)
PRECEDENCE = [
    # A specific Allow lifts the engine's class default for its tool.
    (allow_all, "allow", ENGINE_APPROVALS, None, False, "allow", None),
    # An org rule that asks wins over it; an org's deny too.
    (ORG_ASKS, "allow", ENGINE_APPROVALS, None, False, "ask", "writes"),
    (ORG_DENIES, "allow", ENGINE_APPROVALS, None, False, "deny", None),
    # The flow guard wins over it, ask or deny.
    (allow_all, "allow", ENGINE_APPROVALS, FlowRule("ask"), True, "ask", FLOW_RULE),
    (allow_all, "allow", ENGINE_APPROVALS, FlowRule("deny"), True, "deny", None),
    # Through the flow guard when it has nothing to hold, the Allow still lifts the default.
    (allow_all, "allow", ENGINE_APPROVALS, FlowRule("ask"), False, "allow", None),
    # A deny in the host's default is never lifted: only its ask is.
    (allow_all, "allow", DENY_DEFAULT, None, False, "deny", None),
    # The tool's own Ask and Block.
    (allow_all, "ask", ENGINE_APPROVALS, None, False, "ask", "tool"),
    (allow_all, "deny", ENGINE_APPROVALS, None, False, "deny", None),
    # No specific rule: the release default stays for the unconfigured tool.
    (allow_all, None, ENGINE_APPROVALS, None, False, "ask", ENGINE_WRITES_RULE),
    (ORG_ASKS, None, ENGINE_APPROVALS, None, False, "ask", "writes"),
]


@pytest.mark.parametrize(("org", "own", "default", "flow", "read", "decided", "asked_by"), PRECEDENCE)
def test_allow1_precedence(
    org: ApprovalPolicy, own: ApprovalDecision | None, default: ApprovalPolicy, flow: FlowRule | None, read: bool,
    decided: str, asked_by: str | None,
) -> None:
    policy = _engine(_ToolRules(org, {} if own is None else {POST_ID: own}), default=default, read=read, flow=flow)
    assert policy(call(), POST) == decided
    reason = policy.ask_reason(call(), POST)
    if decided == "ask":
        assert reason is not None and reason["rule"] == asked_by
    elif decided == "allow":
        assert reason is None


def test_allow1_an_allow_of_another_tool_lifts_nothing_for_this_one() -> None:
    policy = _engine(_ToolRules(allow_all, {"other@x/tool": "allow"}))
    assert policy(call(), POST) == "ask"
    assert (policy.ask_reason(call(), POST) or {})["rule"] == ENGINE_WRITES_RULE


def test_allow1_reads_and_own_space_writes_are_unchanged() -> None:
    policy = _engine(_ToolRules(allow_all, {}))
    assert policy(call(), READ) == "allow"
    assert policy(call(), SAVE) == "allow"


def test_allow1_a_policy_that_does_not_say_lifts_nothing() -> None:
    assert isinstance(_ToolRules(allow_all, {}), LiftsDefault)
    assert not default_lifted(allow_all, call(), POST)
    assert not default_lifted(LayeredApprovalPolicy(), call(), POST)
    assert _engine(allow_all)(call(), POST) == "ask"
