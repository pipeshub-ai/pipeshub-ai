"""AP-1..AP-6: the approval policy narrows by layer and fails closed on bad rules."""

from __future__ import annotations

import itertools
from typing import TYPE_CHECKING, get_args

import pytest

from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects import (
    ALLOW_ALL,
    ENGINE_APPROVALS,
    WRITES_ASK,
    ApprovalConfigInvalid,
    ApprovalRules,
    LayeredApprovalPolicy,
    ToolEffect,
    parse_rules,
    stricter,
    tool_id_for,
)
from harness_core.effects.types import ApprovalDecision, EffectKind
from harness_core.kernel.core.messages import ToolCall

if TYPE_CHECKING:
    from pydantic import JsonValue

CALL = ToolCall(id="c1", name="t", arguments={})
KINDS: tuple[EffectKind, ...] = get_args(EffectKind)
LEVELS: tuple[ApprovalDecision, ...] = ("allow", "ask", "deny")


def effect(kind: EffectKind, name: str = "t") -> ToolEffect:
    return ToolEffect(
        tool_id=tool_id_for(name),
        effect=kind,
        idempotent=kind in ("read", "workspace"),
        sink=SinkDescriptor(egress="external", args_leave_boundary=True),
    )


def decide(policy: LayeredApprovalPolicy, kind: EffectKind, name: str = "t") -> ApprovalDecision:
    return policy(CALL, effect(kind, name))


@pytest.mark.parametrize("kind", KINDS)
def test_ap1_no_configuration_allows_every_class(kind: EffectKind) -> None:
    assert decide(LayeredApprovalPolicy(), kind) == "allow"
    assert decide(LayeredApprovalPolicy((parse_rules(None),)), kind) == "allow"


@pytest.mark.parametrize(
    ("kind", "expected"),
    [("read", "allow"), ("workspace", "allow"), ("write", "ask"), ("execute", "ask")],
)
def test_ap2_the_one_switch_asks_for_writes_and_execution(
    kind: EffectKind, expected: ApprovalDecision
) -> None:
    assert decide(LayeredApprovalPolicy((parse_rules({"writes": "ask"}),)), kind) == expected
    assert decide(LayeredApprovalPolicy((WRITES_ASK,)), kind) == expected


@pytest.mark.parametrize(("org", "agent"), list(itertools.product(LEVELS, LEVELS)))
def test_ap3_a_later_layer_only_narrows(org: ApprovalDecision, agent: ApprovalDecision) -> None:
    policy = LayeredApprovalPolicy(
        (ApprovalRules(classes={"write": org}), ApprovalRules(classes={"write": agent}))
    )
    got = decide(policy, "write")
    assert got == stricter(org, agent)
    assert LEVELS.index(got) >= LEVELS.index(org)


def test_ap4_a_tool_rule_narrows_its_tool_only() -> None:
    rules = parse_rules({"tools": {tool_id_for("send"): "deny"}})
    policy = LayeredApprovalPolicy((WRITES_ASK, rules))
    assert decide(policy, "write", "send") == "deny"
    assert decide(policy, "write", "other") == "ask"
    widening = LayeredApprovalPolicy((WRITES_ASK, parse_rules({"tools": {tool_id_for("send"): "allow"}})))
    assert decide(widening, "write", "send") == "ask"


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("ask", "approval_rules_not_object"),
        ({"writes": "maybe"}, "approval_level_invalid"),
        ({"writes": "ask", "extra": 1}, "approval_rules_unknown_key"),
        ({"classes": {"network": "ask"}}, "approval_class_unknown"),
        ({"classes": ["write"]}, "approval_rules_not_object"),
        ({"tools": {"x": 3}}, "approval_level_invalid"),
    ],
)
def test_ap5_invalid_rules_are_refused_with_a_reason(raw: JsonValue, reason: str) -> None:
    with pytest.raises(ApprovalConfigInvalid) as refused:
        _ = parse_rules(raw)
    assert refused.value.reason_code == reason


def test_ap6_engine_runs_ask_for_writes() -> None:
    assert decide(ENGINE_APPROVALS, "write") == "ask"
    assert decide(ENGINE_APPROVALS, "execute") == "ask"
    assert decide(ENGINE_APPROVALS, "read") == "allow"
    assert ALLOW_ALL.decide(effect("execute")) == "allow"


def test_classes_combine_with_the_switch_strictest_first() -> None:
    rules = parse_rules({"writes": "ask", "classes": {"write": "deny", "read": "ask"}})
    assert rules.classes == {"write": "deny", "execute": "ask", "read": "ask"}
