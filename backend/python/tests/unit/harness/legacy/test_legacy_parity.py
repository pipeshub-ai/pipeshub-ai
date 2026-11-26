"""LEG-P: for the same world, a legacy turn assembled from its header and one assembled from the per-request reads
hand the loop the same request and apply the same flow rules.

The header's claims are built from the documents the reads would see, by the functions Node's values are defined
by: the Labs stages from the settings document, the flow rules from the approvals vectors' effective policy
(``contracts/harness/vectors/approvals.json``), access from the agent's permission, a binding for each credential
the store holds ready.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from app.agents.agent_loop.hooks import flow_guard
from app.harness.approvals import UNREADABLE_LAYER, approval_policy
from app.harness.legacy.resolved_request import POLICY_STORE_ENV
from harness_core.effects.flow import FlowRule, Sensitivity
from harness_core.kernel.durable.effects import classify_tool_effect
from harness_core.kernel.hooks.registry import HookRegistry
from harness_core.logs import HarnessLog
from harness_core.tools.base import Tag
from tests.support.config_service import FakeConfigurationService
from tests.unit.harness.legacy.rig import (
    APPROVALS_NODE,
    Rig,
    World,
    mcp_credentials_path,
    toolset_path,
)

if TYPE_CHECKING:
    from httpx import Response

APPROVALS: list[dict[str, Any]] = json.loads(
    (Path(__file__).resolve().parents[6] / "contracts" / "harness" / "vectors" / "approvals.json").read_text(encoding="utf-8")
)["documents"]
NEW_KEY = "resolvedFlowRules"
SEND = classify_tool_effect("slack.send_message", [Tag("category", "write")])
SENSITIVITIES: tuple[Sensitivity, ...] = ("public", "private", "unknown")
LOG = HarnessLog.named("tests.legacy_parity")


def _bindings(world: World, owner_id: str, owner: str) -> list[dict[str, str]]:
    """A binding for each installation whose credential the store holds authenticated for ``owner_id``."""
    held = [
        ("inst-1", toolset_path("inst-1", owner_id)),
        ("mcp-1", mcp_credentials_path(owner_id)),
    ]
    return [
        {"installation": installation, "owner": owner, "ref": f"bnd-{installation}"}
        for installation, path in held
        if isinstance(world.config._nodes.get(path), dict) and world.config._nodes[path].get("isAuthenticated")
    ]


def _pair(
    monkeypatch: pytest.MonkeyPatch, make: Any, path: str, *, agent: str | None, owner: tuple[str, str], body: dict[str, Any] | None = None  # noqa: ANN401
) -> tuple[Rig, Response, Rig, Response]:
    monkeypatch.delenv(POLICY_STORE_ENV, raising=False)
    read = Rig(make(), monkeypatch)
    read_response = read.post(path, body=body)
    fed = Rig(make(), monkeypatch)
    header = fed.header(agent=agent, access={"run": True}, bindings=_bindings(fed.world, *owner))
    fed_response = fed.post(path, header=header, body=body)
    return read, read_response, fed, fed_response


def _handed(rig: Rig) -> dict[str, Any]:
    (call,) = rig.loop.calls
    info = {key: value for key, value in call["query_info"].items() if key != NEW_KEY}
    passed = {
        key: value
        for key, value in call.items()
        if key in ("model_name", "model_key", "is_multimodal_llm", "client_name", "protocol", "llm_provider", "context_length")
    }
    return {"query_info": info, "user_info": call["user_info"], "passed": passed}


AGENT = "/api/v1/agent/shared/chat/stream"
WORLDS: dict[str, dict[str, Any]] = {
    "everything on": {},
    "actions off": {"flags": {"ENABLE_ACTIONS": False}},
    "mcp off": {"flags": {"ENABLE_MCP": False}},
    "actions and mcp off": {"flags": {"ENABLE_ACTIONS": False, "ENABLE_MCP": False}},
    "skills off": {"flags": {"ENABLE_SKILLS": False}},
    "a deployment flow rule": {"approvals": {"default": {"flow": {"level": "ask"}}}},
    "an agent flow rule": {"approvals": {"agents": {"shared": {"flow": {"level": "deny", "above": "private"}}}}},
}


@pytest.mark.parametrize("world", WORLDS.values(), ids=WORLDS.keys())
@pytest.mark.parametrize("body", [None, {"tools": ["inst-1.search"]}, {"tools": []}], ids=["all tools", "one tool", "no tool"])
def test_legp_an_agent_turn_is_the_same_request_from_the_header_and_from_the_reads(
    monkeypatch: pytest.MonkeyPatch, world: dict[str, Any], body: dict[str, Any] | None
) -> None:
    read, read_response, fed, fed_response = _pair(
        monkeypatch, lambda: World(**world), AGENT, agent="shared", owner=("u-bob", "user:u-bob"), body=body
    )
    assert read_response.status_code == fed_response.status_code == 200
    assert _handed(fed) == _handed(read)
    flags = World(**world).flag_values
    assert bool(_handed(read)["query_info"]["toolsets"]) is (flags["ENABLE_ACTIONS"] and body != {"tools": []})
    assert bool(_handed(read)["query_info"]["mcpServers"]) is (flags["ENABLE_MCP"] and body is None)
    # What the header saved: the bound credentials and the permission check; nothing else moved.
    assert fed.world.graph.calls_to("check_agent_permission") == []
    assert len(read.world.graph.calls_to("check_agent_permission")) == 1
    skipped = set(read.loop.reads_before[0]) - set(fed.loop.reads_before[0])
    assert skipped <= {toolset_path("inst-1", "u-bob"), mcp_credentials_path("u-bob"), "/services/platform/settings"}


def test_legp_a_bound_and_an_unbound_toolset_and_an_mcp_server(monkeypatch: pytest.MonkeyPatch) -> None:
    read, _, fed, _ = _pair(monkeypatch, World, AGENT, agent="shared", owner=("u-bob", "user:u-bob"))
    info = _handed(fed)["query_info"]
    assert info == _handed(read)["query_info"]
    assert set(info["toolsetConfigs"]) == {"inst-1", "inst-2"} and set(info["mcpServerConfigs"]) == {"mcp-1"}
    assert toolset_path("inst-1", "u-bob") not in fed.world.config.reads
    assert toolset_path("inst-2", "u-bob") in fed.world.config.reads


def test_legp_a_service_account_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    read, read_response, fed, fed_response = _pair(
        monkeypatch, World, "/api/v1/agent/sa/chat/stream", agent="sa", owner=("sa", "agent:sa")
    )
    assert read_response.status_code == fed_response.status_code == 200
    assert _handed(fed) == _handed(read)
    assert _handed(fed)["query_info"]["credentialOwner"] == "sa"
    assert toolset_path("inst-1", "sa") in read.world.config.reads
    assert toolset_path("inst-1", "sa") not in fed.world.config.reads


def test_legp_a_credential_the_store_does_not_hold_stops_both_the_same_way(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing is bound for a credential nobody connected: the header-fed turn reads it and answers as today."""
    read, read_response, fed, fed_response = _pair(
        monkeypatch, lambda: World(toolset_owners=("u-alice",)), AGENT, agent="shared", owner=("u-bob", "user:u-bob")
    )
    assert read.loop.calls == fed.loop.calls == []
    assert "toolset_config_missing" in read_response.text
    assert fed_response.text == read_response.text


@pytest.mark.parametrize("path", ["/api/v1/agent/agentIdPlaceholder/chat/stream", "/api/v1/chat/stream", "/api/v1/chat"])
@pytest.mark.parametrize("user_context", [True, False])
def test_legp_the_assistant_s_turn(monkeypatch: pytest.MonkeyPatch, path: str, user_context: bool) -> None:
    flags = {"ENABLE_USER_CONTEXT": user_context, "ENABLE_ACTIONS": False, "ENABLE_MCP": False}
    read, read_response, fed, fed_response = _pair(
        monkeypatch, lambda: World(flags=flags), path, agent=None, owner=("u-bob", "user:u-bob")
    )
    assert read_response.status_code == fed_response.status_code == 200
    assert _handed(fed) == _handed(read)
    assert _handed(fed)["user_info"]["sendUserInfo"] is (True if user_context else False)


# -- the flow rules ------------------------------------------------------------------------------------------------


def _unreadable(case: dict[str, Any]) -> bool:
    policy = approval_policy(case["document"], org_id=case["orgId"], agent_key=case["agentKey"], log=LOG)
    return any(layer is UNREADABLE_LAYER for layer in policy.layers)


def _header_flow(case: dict[str, Any]) -> tuple[FlowRule, ...]:
    """The claim Node signs: the effective policy's flow rules, as the vector states them."""
    return tuple(FlowRule(rule["level"], rule["above"]) for rule in case["effective"].get("flow", []))


async def _guard_rules(
    monkeypatch: pytest.MonkeyPatch, case: dict[str, Any], resolved: tuple[FlowRule, ...] | None
) -> tuple[tuple[FlowRule, ...], bool, list[str]]:
    """The rules ``register_legacy_flow_guard`` arms today's loop with, whether it says any applies, and the reads."""
    armed: list[tuple[FlowRule, ...]] = []
    real = flow_guard.legacy_flow_guard
    monkeypatch.setattr(flow_guard, "legacy_flow_guard", lambda context, rules: armed.append(rules) or real(context, rules))
    config = FakeConfigurationService({APPROVALS_NODE: case["document"]} if case["document"] is not None else None)
    context = SimpleNamespace(
        previous_conversations=[], tool_state={}, config_service=config, org_id=case["orgId"],
        agent_key=case["agentKey"], resolved_flow_rules=resolved,
    )
    applies = await flow_guard.register_legacy_flow_guard(HookRegistry(), context)
    (rules,) = armed
    return rules, applies, config.reads


def _refuses(rules: tuple[FlowRule, ...]) -> dict[str, bool]:
    return {seen: any(rule.decide(seen, SEND) != "allow" for rule in rules) for seen in SENSITIVITIES}


DEPLOYMENTS: dict[str, dict[str, str]] = {
    "guard off": {"HARNESS_FLOW_GUARD": "false"},
    "guard on": {"HARNESS_FLOW_GUARD": "true"},
    "deny above private": {"HARNESS_FLOW_GUARD": "true", "HARNESS_FLOW_GUARD_LEVEL": "deny", "HARNESS_FLOW_GUARD_ABOVE": "private"},
    "ask above private": {"HARNESS_FLOW_GUARD": "true", "HARNESS_FLOW_GUARD_ABOVE": "private"},
}
READABLE = [case for case in APPROVALS if not _unreadable(case)]
UNREADABLE = [case for case in APPROVALS if _unreadable(case)]


@pytest.mark.parametrize("deployment", DEPLOYMENTS.values(), ids=DEPLOYMENTS.keys())
@pytest.mark.parametrize("case", READABLE, ids=lambda case: case["id"])
async def test_legp_the_flow_rules_from_the_header_are_the_reads(
    monkeypatch: pytest.MonkeyPatch, case: dict[str, Any], deployment: dict[str, str]
) -> None:
    for name, value in deployment.items():
        monkeypatch.setenv(name, value)
    read_rules, read_applies, read_reads = await _guard_rules(monkeypatch, case, None)
    fed_rules, fed_applies, fed_reads = await _guard_rules(monkeypatch, case, _header_flow(case))
    assert APPROVALS_NODE not in fed_reads, "the header answers: the approvals document is not read"
    assert _refuses(fed_rules) == _refuses(read_rules)
    assert fed_applies is read_applies
    if deployment == DEPLOYMENTS["guard on"]:
        # Today's reads stop at the deployment's rule when it already refuses from the lowest threshold.
        assert read_rules == (FlowRule("ask", "public"),) and APPROVALS_NODE not in read_reads
    else:
        assert fed_rules == read_rules
        assert APPROVALS_NODE in read_reads


def test_legp_the_vectors_cover_an_org_rule_an_agent_rule_and_two_rules_at_once() -> None:
    flows = {case["id"]: _header_flow(case) for case in READABLE}
    assert flows["D-F1"] == (FlowRule("ask", "public"),)
    assert flows["D-F4"] == (FlowRule("ask", "public"), FlowRule("deny", "private"))
    assert flows["D-F5"] == (FlowRule("deny", "public"),)
    assert {case["id"] for case in UNREADABLE} >= {"D-7", "D-F6"}


@pytest.mark.parametrize("case", UNREADABLE, ids=lambda case: case["id"])
async def test_legp_a_layer_the_reads_cannot_parse_refuses_and_the_header_must_say_so(
    monkeypatch: pytest.MonkeyPatch, case: dict[str, Any]
) -> None:
    """QA10-F1: today's loop counts a layer it cannot read as one that asks. The vectors' effective policy carries
    that as writes-ask, not as a flow rule, so a header built from ``effective.flow`` alone would be weaker than
    the reads: Node's signer adds ``{ask, public}`` for a policy with an unreadable layer."""
    monkeypatch.setenv("HARNESS_FLOW_GUARD", "false")
    read_rules, _, _ = await _guard_rules(monkeypatch, case, None)
    assert _refuses(read_rules) == {"public": False, "private": True, "unknown": True}
    weaker, _, _ = await _guard_rules(monkeypatch, case, _header_flow(case))
    assert _refuses(weaker) != _refuses(read_rules)
    fed_rules, _, _ = await _guard_rules(monkeypatch, case, (*_header_flow(case), FlowRule("ask", "public")))
    assert fed_rules == read_rules


async def test_legp_a_stricter_deployment_meets_the_header_s_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    """A worker the operator made stricter than Node stays stricter; a header never widens the deployment's rule."""
    case = next(case for case in APPROVALS if case["id"] == "D-2")
    monkeypatch.setenv("HARNESS_FLOW_GUARD", "true")
    monkeypatch.setenv("HARNESS_FLOW_GUARD_LEVEL", "deny")
    rules, applies, _ = await _guard_rules(monkeypatch, case, ())
    assert rules == (FlowRule("deny", "public"),) and applies is True
    rules, _, _ = await _guard_rules(monkeypatch, case, (FlowRule("ask", "private"),))
    assert rules == (FlowRule("deny", "public"),)
    monkeypatch.setenv("HARNESS_FLOW_GUARD", "false")
    rules, applies, _ = await _guard_rules(monkeypatch, case, ())
    assert rules == () and applies is False
