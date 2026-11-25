"""QA6-F6: a held call's reason names the rule and whose rule it is, for every source of a rule.

A ``writes: ask`` rule gave no reason at all, so neither the card nor the journal said why it was raised. Every
policy that asks now answers ``ask_reason`` with the same two names (``rule``, ``layer``), validated here against the
event's generated model; a claim whose journal predates the names gets the shape that journal accepts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.harness.approvals import UNREADABLE_LAYER, approval_policy, engine_guarded
from app.harness.labels import SessionSourceLedger
from harness_core.contracts import validate_contract
from harness_core.contracts.generated.events.interaction_explained import (
    InteractionExplainedData,
)
from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects.flow import (
    ENGINE_WRITES_RULE,
    FLOW_RULE,
    FlowGuardedPolicy,
    FlowRule,
    GrantingLedger,
    destination_key,
    in_context,
    reason_for_journal,
)
from harness_core.effects.policy import (
    ENGINE_APPROVALS,
    WRITES_ASK,
    ApprovalRules,
    LayeredApprovalPolicy,
    parse_rules,
)
from harness_core.effects.types import ExplainsAsk, ToolEffect, tool_id_for
from harness_core.engines.policy import approval_policy_for, engine_default_approvals
from harness_core.kernel.core.messages import ToolCall
from harness_core.logs import HarnessLog
from harness_core.security.labels import AskSource
from harness_core.testing.engines.durable import engine_profile
from tests.unit.harness_core.effects.test_ux1_flow_ask import Ledger, where

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from harness_core.effects.types import ApprovalPolicy

LOG = HarnessLog.named("tests.qa6fix")
ORG, AGENT = "org-1", "agent-1"
POST = ToolEffect(tool_id_for("post", source="slack"), "write", False, SinkDescriptor(egress="external", args_leave_boundary=True))
# With network: code in its own offline sandbox does not ask (CODEAUTO), so the switch is shown on one that may reach out.
RUN_CODE = ToolEffect(tool_id_for("run_code"), "execute", False, SinkDescriptor(egress="external", args_leave_boundary=True))
SEARCH = ToolEffect(tool_id_for("search"), "read", True, SinkDescriptor(egress="self", args_leave_boundary=False))
CALL = ToolCall(id="c", name="post", arguments={"channel": "sales"})


def reason_of(policy: ApprovalPolicy, effect: ToolEffect = POST) -> Mapping[str, JsonValue] | None:
    assert isinstance(policy, ExplainsAsk)
    reason = policy.ask_reason(CALL, effect)
    if reason is not None:
        assert policy(CALL, effect) == "ask", "a reason is given only for a call the policy holds"
        _ = validate_contract(InteractionExplainedData, {"interactionId": "ix.approve.t1.0", **reason})
    return reason


def document_policy(**sections: object) -> LayeredApprovalPolicy:
    return approval_policy(sections, org_id=ORG, agent_key=AGENT, log=LOG)  # type: ignore[arg-type]


# F6-1


def test_f6_1_a_writes_ask_names_the_rule_and_its_layer() -> None:
    policy = document_policy(default={"writes": "ask"})
    assert reason_of(policy) == {"rule": "writes", "layer": "deployment", "toolId": POST.tool_id, "conversationGrant": False}


# F6-2


@pytest.mark.parametrize(
    ("policy", "effect", "rule", "layer"),
    [
        (document_policy(default={"writes": "ask"}), POST, "writes", "deployment"),
        (document_policy(default={"writes": "ask"}), RUN_CODE, "writes", "deployment"),
        (document_policy(default={"classes": {"write": "ask"}}), POST, "writes", "deployment"),
        (document_policy(agents={AGENT: {"writes": "ask"}}), POST, "writes", "agent"),
        (document_policy(default={"tools": {POST.tool_id: "ask"}}), POST, "tool", "deployment"),
        (document_policy(agents={AGENT: {"classes": {"read": "ask"}}}), SEARCH, "effect_class", "agent"),
        (LayeredApprovalPolicy().narrowed(WRITES_ASK, "engine"), POST, "writes", "engine"),
        (approval_policy_for(engine_profile()), POST, "writes", "engine"),
        (ENGINE_APPROVALS, POST, "writes", "engine"),
        (LayeredApprovalPolicy((WRITES_ASK,)), POST, "writes", "deployment"),
    ],
)
def test_f6_2_every_layer_names_its_rule_the_same_way(policy: LayeredApprovalPolicy, effect: ToolEffect, rule: str, layer: str) -> None:
    reason = reason_of(policy, effect)
    assert reason is not None
    assert (reason["rule"], reason["layer"], reason["toolId"], reason["conversationGrant"]) == (rule, layer, effect.tool_id, False)
    assert set(reason) == {"rule", "layer", "toolId", "conversationGrant"}, "a layer's rule claims nothing about what the run read"


def test_f6_2_the_flow_rule_and_an_engines_default_name_their_layer_too() -> None:
    flow = FlowGuardedPolicy(LayeredApprovalPolicy(), FlowRule("ask"), Ledger(frozenset({"rec-1"})), "r", destination=where)  # type: ignore[arg-type]
    reason = reason_of(flow)
    assert reason is not None and (reason["rule"], reason["layer"]) == (FLOW_RULE, "flow")
    ledger = GrantingLedger(SessionSourceLedger())
    engine = engine_guarded(engine_default_approvals(), LayeredApprovalPolicy(), rules=(), ledger=ledger, run_id="r", log=LOG)
    reason = reason_of(engine)
    assert reason is not None and (reason["rule"], reason["layer"]) == (ENGINE_WRITES_RULE, "engine")


def test_f6_2_a_tools_own_rule_is_named_before_its_class() -> None:
    both = document_policy(default={"writes": "ask", "tools": {POST.tool_id: "ask"}})
    assert reason_of(both) is not None and reason_of(both)["rule"] == "tool"  # type: ignore[index]


# F6-3


def test_f6_3_the_first_layer_that_asks_in_precedence_order() -> None:
    policy = document_policy(
        default={"classes": {"read": "ask"}},
        agents={AGENT: {"writes": "ask", "tools": {POST.tool_id: "ask"}}},
    )
    reason = reason_of(policy)
    assert reason is not None and (reason["rule"], reason["layer"]) == ("tool", "agent")
    assert reason_of(policy, SEARCH) is not None and reason_of(policy, SEARCH)["layer"] == "deployment"  # type: ignore[index]


def test_f6_3_a_layer_that_allows_is_not_named() -> None:
    policy = document_policy(default={}, agents={AGENT: {"writes": "ask"}})
    assert reason_of(policy) is not None and reason_of(policy)["layer"] == "agent"  # type: ignore[index]


def test_f6_3_no_reason_for_a_call_nothing_holds_or_a_layer_denies() -> None:
    assert reason_of(document_policy(default={"writes": "ask"}), SEARCH) is None
    assert reason_of(LayeredApprovalPolicy()) is None
    denied = document_policy(default={"writes": "ask"}, agents={AGENT: {"writes": "deny"}})
    assert denied(CALL, POST) == "deny"
    assert isinstance(denied, ExplainsAsk) and denied.ask_reason(CALL, POST) is None


# F6-4


def test_f6_4_the_flow_rule_and_a_layer_rule_both_ask_the_flow_reason_never_granted() -> None:
    org = document_policy(default={"writes": "ask"})
    tainted = FlowGuardedPolicy(org, FlowRule("ask"), Ledger(frozenset({"rec-1"})), "r", destination=where)  # type: ignore[arg-type]
    reason = reason_of(tainted)
    assert reason is not None
    assert (reason["rule"], reason["layer"], reason["sensitivity"], reason["conversationGrant"]) == (FLOW_RULE, "flow", "private", False)


def test_f6_4_only_the_layer_rule_asks_under_a_flow_rule_that_does_not() -> None:
    org = document_policy(default={"writes": "ask"})
    clean = FlowGuardedPolicy(org, FlowRule("ask"), Ledger(frozenset()), "r", destination=where)  # type: ignore[arg-type]
    assert reason_of(clean) == {
        "rule": "writes",
        "layer": "deployment",
        "toolId": POST.tool_id,
        "conversationGrant": False,
        # What a policy that follows the run adds for the person deciding: what it read, where the call goes.
        "sensitivity": "public",
        "sources": [],
        "moreSources": 0,
        "destination": {"label": "#sales", "key": destination_key(POST.tool_id, "sales")},
    }


def test_f6_4_the_run_s_context_is_added_once_and_never_changes_whose_rule_it_is() -> None:
    org = document_policy(default={"writes": "ask"})
    read = Ledger(frozenset({"rec-1"}), (AskSource("Q3 plan", "rec-1"),))
    inner = FlowGuardedPolicy(org, FlowRule("deny", "unknown"), read, "r", destination=where)  # type: ignore[arg-type]
    outer = FlowGuardedPolicy(inner, FlowRule("ask", "unknown"), Ledger(None), "r")  # type: ignore[arg-type]
    reason = reason_of(outer)
    assert reason is not None
    assert (reason["rule"], reason["layer"], reason["conversationGrant"]) == ("writes", "deployment", False)
    assert (reason["sensitivity"], reason["sources"]) == ("private", [{"title": "Q3 plan", "recordId": "rec-1"}])
    said = {"rule": FLOW_RULE, "layer": "flow", "toolId": POST.tool_id, "sensitivity": "unknown", "sources": [], "moreSources": 0, "conversationGrant": True}
    assert in_context(said, POST, read, "r", None) is said


def test_f6_4_an_engine_run_held_by_a_layer_rule_names_that_layer_not_its_own_default() -> None:
    ledger = GrantingLedger(SessionSourceLedger())
    org = document_policy(default={"writes": "ask"})
    engine = engine_guarded(engine_default_approvals(), org, rules=(), ledger=ledger, run_id="r", log=LOG)
    reason = reason_of(engine)
    assert reason is not None and (reason["rule"], reason["layer"], reason["conversationGrant"]) == ("writes", "deployment", False)
    assert (reason["sensitivity"], reason["sources"], reason["moreSources"]) == ("public", [], 0)


# F6-5


def test_f6_5_an_unreadable_layer_asks_under_its_own_name() -> None:
    policy = document_policy(agents={AGENT: {"writes": "sometimes"}})
    assert policy.layers[policy.scopes.index("agent")] is UNREADABLE_LAYER
    reason = reason_of(policy)
    assert reason is not None and (reason["rule"], reason["layer"]) == ("writes", "agent")


def test_f6_5_an_unreadable_document_is_the_deployments() -> None:
    policy = approval_policy({"surprise": {}}, org_id=ORG, agent_key=AGENT, log=LOG)
    reason = reason_of(policy)
    assert reason is not None and (reason["rule"], reason["layer"]) == ("writes", "deployment")
    assert reason_of(approval_policy({"agents": "all"}, org_id=ORG, agent_key=AGENT, log=LOG))["layer"] == "deployment"  # type: ignore[index]


def test_f6_5_layers_added_without_a_name_are_the_deployments() -> None:
    policy = LayeredApprovalPolicy((ApprovalRules(), WRITES_ASK), ("org",))
    assert reason_of(policy) is not None and reason_of(policy)["layer"] == "deployment"  # type: ignore[index]
    assert parse_rules({"writes": "ask"}).asking(POST) == "writes" and parse_rules(None).asking(POST) is None


# F6-6


FLOW_REASON: dict[str, JsonValue] = {
    "rule": FLOW_RULE, "layer": "flow", "toolId": POST.tool_id, "sensitivity": "private", "sources": [], "moreSources": 0,
    "conversationGrant": True,
}


@pytest.mark.parametrize(
    ("reason", "layers", "written"),
    [
        (FLOW_REASON, True, FLOW_REASON),
        (FLOW_REASON, False, {k: v for k, v in FLOW_REASON.items() if k != "layer"}),
        ({**FLOW_REASON, "rule": ENGINE_WRITES_RULE, "layer": "engine"}, False, {**{k: v for k, v in FLOW_REASON.items() if k != "layer"}, "rule": ENGINE_WRITES_RULE}),
        ({"rule": "writes", "layer": "org", "toolId": POST.tool_id, "conversationGrant": False}, True, {"rule": "writes", "layer": "org", "toolId": POST.tool_id, "conversationGrant": False}),
        ({"rule": "writes", "layer": "org", "toolId": POST.tool_id, "conversationGrant": False}, False, None),
        ({"rule": "tool", "layer": "agent", "toolId": POST.tool_id, "conversationGrant": False}, False, None),
        ({"rule": "effect_class", "layer": "agent", "toolId": POST.tool_id, "conversationGrant": False}, False, None),
    ],
)
def test_f6_6_a_journal_that_predates_layers_gets_the_shape_it_accepts(reason: dict[str, JsonValue], layers: bool, written: dict[str, JsonValue] | None) -> None:
    assert reason_for_journal(reason, layers=layers) == written
