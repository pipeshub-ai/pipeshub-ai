"""CODEAUTO CA-1..CA-12: code that runs in the run's own sandbox with no network runs without asking a person (owner,
2026-10-18). A layer's class rule (the `writes` switch, `classes`, an engine profile's, the fail-closed default) no
longer asks about it; a tool rule, a deny, anything with egress beyond `none`, writes, and the flow rule still ask.
The call's intent names the rule that let it run (`allowedBy: own_sandbox`) when the journal accepts it."""

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
)
from harness_core.effects.policy import (
    ENGINE_APPROVALS,
    OWN_SANDBOX_RULE,
    WRITES_ASK,
    ApprovalRules,
    LayeredApprovalPolicy,
    in_own_sandbox,
    parse_rules,
)
from harness_core.effects.types import ToolEffect, ToolExecution, tool_id_for
from harness_core.journal.types import INTENT_ALLOWED_BY
from harness_core.kernel.durable.effects import classify_tool_effect
from harness_core.testing.contracts.tool_catalog import OfferedTool
from harness_core.testing.engines.durable import EngineWorld
from harness_core.tools.base import Tag
from tests.unit.harness_core.durable.support import RUN, World, reply, scripted
from tests.unit.harness_core.effects.test_ux1_flow_ask import Ledger, call, where

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from harness_core.effects.types import ApprovalPolicy, EffectKind, ToolContext
    from harness_core.security.labels import SourceLedger

RUN_CODE_ID = "pipeshub@sandbox/run_code"
ORG_ASKS = LayeredApprovalPolicy((parse_rules({"writes": "ask"}),), ("org",))


def effect(egress: str, kind: EffectKind = "execute", tool_id: str = RUN_CODE_ID) -> ToolEffect:
    sink = SinkDescriptor(egress=egress, args_leave_boundary=egress == "external")  # type: ignore[arg-type]
    return ToolEffect(tool_id, kind, kind == "execute", sink)


RUN_CODE = effect("none")


# CA-1
@pytest.mark.parametrize(
    "rules",
    [{"writes": "ask"}, {"classes": {"execute": "ask"}}, {"writes": "allow", "classes": {"write": "ask", "execute": "ask"}}],
    ids=["writes-switch", "execute-class", "both-classes"],
)
def test_ca1_a_class_rule_does_not_ask_about_code_in_the_runs_own_sandbox(rules: dict[str, JsonValue]) -> None:
    policy = LayeredApprovalPolicy((parse_rules(rules),), ("org",))
    assert in_own_sandbox(RUN_CODE)
    assert policy(call(), RUN_CODE) == "allow"
    assert policy.ask_reason(call(), RUN_CODE) is None


def test_ca1_the_fail_closed_layers_and_the_engine_default_do_not_ask_either() -> None:
    for policy in (LayeredApprovalPolicy((WRITES_ASK,)), ENGINE_APPROVALS):
        assert policy(call(), RUN_CODE) == "allow"


# CA-2: what still asks under the org's writes switch.
@pytest.mark.parametrize(
    ("egress", "kind"),
    [
        ("self", "execute"),
        ("internal_audience", "execute"),
        ("external", "execute"),
        ("none", "write"),
        ("self", "write"),
        ("internal_audience", "write"),
        ("external", "write"),
    ],
)
def test_ca2_anything_that_can_reach_beyond_the_sandbox_still_asks(egress: str, kind: EffectKind) -> None:
    e = effect(egress, kind, tool_id_for("post", source="slack"))
    assert not in_own_sandbox(e)
    assert ORG_ASKS(call(), e) == "ask"
    reason = ORG_ASKS.ask_reason(call(), e)
    assert reason is not None and reason["rule"] == "writes"


def test_ca2_run_code_with_network_and_install_packages_reach_the_network_and_ask() -> None:
    # The tools' own tags (coding_sandbox.py): run_code is sink none only without network; install_packages
    # declares no sink, so it is external (fail closed): it reaches the package registry.
    with_network = classify_tool_effect("run_code", [Tag("category", "execute"), Tag("effect", "execute")])
    install = classify_tool_effect("install_packages", [Tag("category", "execute"), Tag("effect", "execute")])
    offline = classify_tool_effect(
        "run_code", [Tag("category", "execute"), Tag("effect", "execute"), Tag("sink", "none")]
    )
    for asks in (with_network, install):
        assert asks.sink.egress == "external" and ORG_ASKS(call(), asks) == "ask"
    assert ORG_ASKS(call(), offline) == "allow"


# CA-3: the admin's override.
def test_ca3_a_tool_rule_for_run_code_asks_again_and_names_its_own_rule() -> None:
    policy = LayeredApprovalPolicy((parse_rules({"writes": "ask", "tools": {RUN_CODE_ID: "ask"}}),), ("org",))
    assert policy(call(), RUN_CODE) == "ask"
    reason = policy.ask_reason(call(), RUN_CODE)
    assert reason is not None and (reason["rule"], reason["layer"], reason["conversationGrant"]) == ("tool", "org", False)


@pytest.mark.parametrize("rules", [{"writes": "deny"}, {"classes": {"execute": "deny"}}, {"tools": {RUN_CODE_ID: "deny"}}])
def test_ca3_a_deny_still_denies(rules: dict[str, JsonValue]) -> None:
    assert LayeredApprovalPolicy((parse_rules(rules),))(call(), RUN_CODE) == "deny"


# CA-4: the flow rule (external sends keep asking).
def test_ca4_after_a_private_read_an_external_send_still_asks_and_own_sandbox_code_does_not() -> None:
    guarded = FlowGuardedPolicy(ORG_ASKS, FlowRule("ask"), Ledger(frozenset({"rec-1"})), "r", destination=where)
    send = effect("external", "write", tool_id_for("post", source="slack"))
    assert guarded(call(), send) == "ask"
    reason = guarded.ask_reason(call(), send)
    assert reason is not None and reason["rule"] == FLOW_RULE
    assert guarded(call(), RUN_CODE) == "allow"


def test_ca4_an_unknown_context_does_not_hold_own_sandbox_code() -> None:
    guarded = FlowGuardedPolicy(ORG_ASKS, FlowRule("deny"), Ledger(None), "r", destination=where)
    assert guarded(call(), RUN_CODE) == "allow"


# CA-5: engine runs (UX2/UX4 default over the org's rules, as ``engine_guarded`` composes them).
def engine(base: ApprovalPolicy) -> DefaultAskPolicy:
    return DefaultAskPolicy(base, ENGINE_APPROVALS, ENGINE_WRITES_RULE, Ledger(frozenset()), "r", destination=where)  # type: ignore[arg-type]


def test_ca5_an_engines_gateway_call_to_run_code_follows_the_same_default() -> None:
    assert engine(ORG_ASKS)(call(), RUN_CODE) == "allow"
    profile = LayeredApprovalPolicy((ApprovalRules(classes={"execute": "ask"}),), ("engine",))
    assert engine(profile)(call(), RUN_CODE) == "allow"
    override = LayeredApprovalPolicy((parse_rules({"tools": {RUN_CODE_ID: "ask"}}),), ("org",))
    assert engine(override)(call(), RUN_CODE) == "ask"
    assert engine(ORG_ASKS)(call(), effect("external", "write", tool_id_for("post", source="mcp"))) == "ask"


# CA-6..CA-10: the durable loop.
async def _run_code(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
    return ToolExecution(status="ok", content={"stdout": "2\n", "exitCode": 0})


def _world(**kwargs: object) -> World:
    world = World(**kwargs)  # type: ignore[arg-type]
    world.tools.add("run_code", _run_code, effect=RUN_CODE)
    world.admit()
    return world


def _code_script() -> object:
    return scripted(reply("", ("run_code", {"code": "print(1 + 1)"})), reply("it printed 2"))[1]


def _payloads(world: World) -> list[tuple[str, Mapping[str, JsonValue]]]:
    return [(e.type, e.data) for e in world.events(run_id=RUN)]


async def test_ca6_run_code_under_the_orgs_writes_switch_runs_with_no_card_and_the_intent_names_the_rule(
    caplog: pytest.LogCaptureFixture,
) -> None:
    world = _world()
    with caplog.at_level(logging.INFO, logger="harness_core.kernel.durable"):
        disposition = await world.run(world.kernel(_code_script(), approval=ORG_ASKS))
    assert disposition.status == "completed"
    assert "interaction.requested" not in world.types()
    (intent,) = world.events("tool.intent")
    assert (intent.data["toolId"], intent.data["allowedBy"]) == (RUN_CODE_ID, OWN_SANDBOX_RULE)
    assert [e.data["status"] for e in world.events("tool.outcome")] == ["ok"]
    allowed = [r for r in caplog.records if r.getMessage().startswith("tool.allowed")]
    assert len(allowed) == 1 and allowed[0].harness["rule"] == OWN_SANDBOX_RULE  # type: ignore[attr-defined]


async def test_ca7_a_node_that_does_not_accept_the_field_gets_the_intent_as_before() -> None:
    world = _world(journal_accepts=frozenset())
    assert (await world.run(world.kernel(_code_script(), approval=ORG_ASKS))).status == "completed"
    assert "interaction.requested" not in world.types()
    (intent,) = world.events("tool.intent")
    assert "allowedBy" not in intent.data


async def test_ca7_a_node_that_names_the_feature_gets_the_rule() -> None:
    world = _world(journal_accepts=frozenset({INTENT_ALLOWED_BY}))
    assert (await world.run(world.kernel(_code_script(), approval=ORG_ASKS))).status == "completed"
    (intent,) = world.events("tool.intent")
    assert intent.data["allowedBy"] == OWN_SANDBOX_RULE


async def test_ca8_a_write_still_asks_and_the_run_waits_for_the_person() -> None:
    world = _world()
    port = scripted(reply("", ("post_message", {"text": "hi"})), reply("posted"))[1]
    disposition = await world.run(world.kernel(port, approval=ORG_ASKS))
    assert disposition.status == "suspended"
    assert len(world.events("interaction.requested")) == 1
    assert world.events("tool.intent") == []


async def test_ca9_a_person_approved_run_code_names_no_rule_in_its_intent() -> None:
    world = _world()
    override = LayeredApprovalPolicy((parse_rules({"writes": "ask", "tools": {RUN_CODE_ID: "ask"}}),), ("org",))
    port = _code_script()
    suspended = await world.run(world.kernel(port, approval=override))
    assert suspended.status == "suspended" and suspended.reason is not None
    assert world.events("tool.intent") == []
    world.journal.resolve_interaction(RUN, suspended.reason, "approved")
    assert (await world.run(world.kernel(port, approval=override))).status == "completed"
    (intent,) = world.events("tool.intent")
    assert "allowedBy" not in intent.data


async def test_ca10_replay_two_runs_of_the_same_script_journal_the_same_decision() -> None:
    first, second = _world(), _world()
    for world in (first, second):
        assert (await world.run(world.kernel(_code_script(), approval=ORG_ASKS))).status == "completed"
    assert _payloads(first) == _payloads(second)
    assert [d.get("allowedBy") for t, d in _payloads(first) if t == "tool.intent"] == [OWN_SANDBOX_RULE]


# CA-11, CA-12: engine runs.
ENGINE_RUN = "run-codeauto"
ENGINE_RUN_CODE = OfferedTool("run_code", RUN_CODE)


def _org_rules(rules: dict[str, JsonValue]) -> object:
    def guard(base: ApprovalPolicy, ledger: SourceLedger, run_id: str) -> ApprovalPolicy:
        del base, ledger, run_id
        return engine(LayeredApprovalPolicy((parse_rules(rules),), ("org",)))

    return guard


def _engine_world(rules: dict[str, JsonValue]) -> EngineWorld:
    return EngineWorld(tools=(ENGINE_RUN_CODE,), ledger=lambda: Ledger(frozenset()), guard=_org_rules(rules))  # type: ignore[arg-type]


async def test_ca11_an_engines_shell_and_its_run_code_call_ask_nobody() -> None:
    world = _engine_world({"writes": "ask"})
    world.admit(ENGINE_RUN, 'shell echo hi\ntool run_code {"code": "print(1)"}\nsay done')
    assert (await world.attempt(ENGINE_RUN)).status == "completed"
    types = world.types(ENGINE_RUN)
    assert "interaction.requested" not in types
    assert [c for c in world.presenter.calls if c[0] == "approval"] == []
    (intent,) = [e for e in world.events(ENGINE_RUN) if e.type == "tool.intent"]
    assert intent.data["allowedBy"] == OWN_SANDBOX_RULE
    assert sum(world.effects.values()) == 1


async def test_ca12_an_admins_tool_rule_holds_an_engines_run_code_for_a_person() -> None:
    world = _engine_world({"writes": "ask", "tools": {RUN_CODE_ID: "ask"}})
    world.admit(ENGINE_RUN, 'tool run_code {"code": "print(1)"}\nsay waiting')
    disposition = await world.attempt(ENGINE_RUN)
    assert disposition.status == "suspended"
    assert [c for c in world.presenter.calls if c[0] == "approval"] == [("approval", "run_code")]
    assert sum(world.effects.values()) == 0
