"""UX4 ES-1..ES-9: the engine default (``ENGINE_APPROVALS`` with its profile's) asks only before a write whose sink
leaves the user's own space; denials, org rules and the flow rule are unchanged."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects.flow import (
    ENGINE_WRITES_RULE,
    FLOW_RULE,
    DefaultAskPolicy,
    FlowGuardedPolicy,
    FlowRule,
    leaves_own_space,
)
from harness_core.effects.policy import (
    ENGINE_APPROVALS,
    WRITES_ASK,
    ApprovalRules,
    LayeredApprovalPolicy,
)
from harness_core.effects.types import POLICY_DENIAL, ToolEffect, allow_all, tool_id_for
from harness_core.engines.types import ProfileApprovals
from harness_core.kernel.durable.effects import classify_tool_effect
from harness_core.tools.base import Tag
from tests.unit.harness_core.effects.test_ux1_flow_ask import Ledger, call, where
from tests.unit.harness_core.effects.test_ux2_default_ask import granted

if TYPE_CHECKING:
    from harness_core.effects.types import EffectKind

EGRESSES = ("none", "self", "internal_audience", "external")
ORG_ASKS = LayeredApprovalPolicy((WRITES_ASK,))
ORG_DENIES = LayeredApprovalPolicy((ApprovalRules(classes={"write": "deny"}),))


def effect(egress: str, kind: EffectKind = "write") -> ToolEffect:
    sink = SinkDescriptor(egress=egress, args_leave_boundary=egress == "external")  # type: ignore[arg-type]
    return ToolEffect(tool_id_for("post", source="slack"), kind, False, sink)


def profile(approvals: ProfileApprovals) -> LayeredApprovalPolicy:
    """What ``approval_policy_for`` layers under ``ENGINE_APPROVALS`` in the assembler (UX2 FB-5)."""
    return LayeredApprovalPolicy((*ENGINE_APPROVALS.layers, ApprovalRules(classes=approvals.as_mapping())))


def policy(base: object = allow_all, *, default: object = ENGINE_APPROVALS, records: frozenset[str] | None = frozenset(), grants: object = None) -> DefaultAskPolicy:
    return DefaultAskPolicy(base, default, ENGINE_WRITES_RULE, Ledger(records), "r", destination=where, grants=grants)  # type: ignore[arg-type]


# ES-1
@pytest.mark.parametrize("kind", ["write", "execute"])
@pytest.mark.parametrize(
    ("egress", "decision"),
    [("none", "allow"), ("self", "allow"), ("internal_audience", "ask"), ("external", "ask")],
)
def test_es1_the_default_asks_only_when_the_sink_leaves_the_users_space(kind: EffectKind, egress: str, decision: str) -> None:
    p, e = policy(), effect(egress, kind)
    assert leaves_own_space(e) is (decision == "ask")
    assert p(call(), e) == decision
    reason = p.ask_reason(call(), e)
    if decision == "allow":
        assert reason is None
    else:
        assert reason is not None and reason["rule"] == ENGINE_WRITES_RULE


@pytest.mark.parametrize("egress", EGRESSES)
def test_es1_reads_and_workspace_calls_never_leave(egress: str) -> None:
    for kind in ("read", "workspace"):
        assert not leaves_own_space(effect(egress, kind))  # type: ignore[arg-type]
        assert policy()(call(), effect(egress, kind)) == "allow"  # type: ignore[arg-type]


# ES-2
@pytest.mark.parametrize(
    "tags",
    [
        [Tag("effect", "write")],
        [Tag("effect", "write"), Tag("sink", "shared_drive")],
        [Tag("effect", "write"), Tag("sink", "self"), Tag("sink", "none")],
    ],
    ids=["untagged", "unknown", "several"],
)
def test_es2_an_untagged_or_unreadable_sink_asks(tags: list[Tag]) -> None:
    e = classify_tool_effect("post", tags)
    assert e.sink.egress == "external"
    assert policy()(call(), e) == "ask"


def test_es2_a_tagged_own_space_tool_does_not_ask() -> None:
    e = classify_tool_effect("draft_email", [Tag("effect", "write"), Tag("sink", "self")])
    assert policy()(call(), e) == "allow"


# ES-3
@pytest.mark.parametrize("egress", EGRESSES)
def test_es3_a_default_that_denies_still_denies_everywhere(egress: str) -> None:
    p = policy(default=profile(ProfileApprovals(write="deny")))
    assert p(call(), effect(egress)) == "deny"
    assert p.denial(call(), effect(egress)) == POLICY_DENIAL


# ES-4, ES-5
@pytest.mark.parametrize("egress", ["none", "self"])
def test_es4_an_org_rule_that_asks_still_asks_and_is_not_the_engines(egress: str) -> None:
    p = policy(ORG_ASKS, grants=granted())
    assert p(call(), effect(egress)) == "ask"
    reason = p.ask_reason(call(), effect(egress))
    assert reason is not None and (reason["rule"], reason["conversationGrant"]) == ("writes", False)


@pytest.mark.parametrize("egress", ["none", "self"])
def test_es5_an_org_rule_that_denies_still_denies(egress: str) -> None:
    assert policy(ORG_DENIES)(call(), effect(egress)) == "deny"


# ES-6
def test_es6_the_flow_rule_is_unchanged() -> None:
    read = frozenset({"rec-1"})
    flow = FlowGuardedPolicy(allow_all, FlowRule("ask"), Ledger(read), "r", destination=where)  # type: ignore[arg-type]
    p = policy(flow, records=read)
    assert p(call(), effect("self")) == "allow"
    assert p(call(), effect("external")) == "ask"
    reason = p.ask_reason(call(), effect("external"))
    assert reason is not None and (reason["rule"], reason["sensitivity"]) == (FLOW_RULE, "private")
    deny = FlowGuardedPolicy(allow_all, FlowRule("deny"), Ledger(read), "r")  # type: ignore[arg-type]
    assert policy(deny, records=read)(call(), effect("external")) == "deny"
    assert policy(deny, records=read)(call(), effect("self")) == "allow"


# ES-7
@pytest.mark.parametrize(("egress", "decision"), [("none", "allow"), ("self", "allow"), ("external", "ask")])
def test_es7_the_profiles_asks_are_part_of_the_default(egress: str, decision: str) -> None:
    assert policy(default=profile(ProfileApprovals()))(call(), effect(egress)) == decision


# ES-8
def test_es8_a_grant_still_answers_the_default_for_an_external_write() -> None:
    p = policy(grants=granted("sales"))
    assert p(call("sales"), effect("external")) == "allow"
    assert p(call("general"), effect("external")) == "ask"


# ES-9
def test_es9_an_own_space_write_is_logged_once_per_call(caplog: pytest.LogCaptureFixture) -> None:
    p = policy()
    with caplog.at_level(logging.INFO, logger="harness_core.effects"):
        assert p(call(), effect("self")) == "allow"
        assert p.ask_reason(call(), effect("self")) is None
        p(call(), effect("external"))
        p(call(), effect("self", "read"))
    logged = [r.__dict__["harness"] for r in caplog.records if r.__dict__.get("harness_event") == "effects.default_own_space"]
    assert logged == [{"toolId": effect("self").tool_id, "egress": "self", "rule": ENGINE_WRITES_RULE}]
