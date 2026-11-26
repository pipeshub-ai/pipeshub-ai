"""LEG-10a, LEG-10b, CPD10-24, CPD10-26, CPD10-27, CPD10-83: a legacy chat turn assembled from ``X-PipesHub-Resolved``.

The agent and assistant routes are the production ones on in-memory stores (``rig.py``); the loop and the model
factory are replaced at the routes' edge. A read the header answers is counted on the stores and must be zero.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.middlewares.auth import _normalize_regular_payload
from app.harness.credentials.tool_credentials import (
    REF_KEY,
    mcp_reference,
    toolset_reference,
)
from app.harness.legacy.errors import RESOLVED_TURN_REFUSAL_CODES
from app.harness.legacy.resolved_request import (
    POLICY_STORE_ENV,
    InvalidPolicyStoreMode,
    resolved_turn_for,
)
from app.harness.legacy.resolved_turn import RESOLVED_TURN_HEADER
from app.services.featureflag.platform_settings import is_actions_enabled
from harness_core.effects.flow import FlowRule
from tests.support import edition_defaults
from tests.support.agent_routes import FakeContainer, _TestAuth
from tests.unit.harness.legacy.rig import (
    APPROVALS_NODE,
    CONVERSATION,
    MCP_INSTANCE,
    Rig,
    World,
    mcp_credentials_path,
    real_now,
    settings_reads,
    toolset_path,
)
from tests.unit.harness.legacy.support import KEYS_NODE, NOW, VECTOR, claims, sign

if TYPE_CHECKING:
    from httpx import Response

BOUND = [
    {"installation": "inst-1", "owner": "user:u-bob", "ref": "bnd-1"},
    {"installation": "mcp-1", "owner": "user:u-bob", "ref": "bnd-2"},
]
ASSISTANT = "/api/v1/chat/stream"
ASSISTANT_JSON = "/api/v1/chat"


@pytest.fixture
def world() -> World:
    return World()


@pytest.fixture
def rig(world: World, monkeypatch: pytest.MonkeyPatch) -> Rig:
    monkeypatch.delenv(POLICY_STORE_ENV, raising=False)
    return Rig(world, monkeypatch)


def _refusal(response: Response) -> str:
    assert response.status_code == 401, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "RESOLVED_TURN_REFUSED"
    assert detail["reason"] in RESOLVED_TURN_REFUSAL_CODES
    return detail["reason"]


def _served(response: Response) -> None:
    assert response.status_code == 200, response.text
    assert "RUN_FINISHED" in response.text, response.text


# -- LEG-10a ------------------------------------------------------------------------------------------------------


def test_leg10a_access_on_the_header_replaces_the_permission_check(rig: Rig) -> None:
    _served(rig.agent_stream(header=rig.header(access={"run": True})))
    assert rig.world.graph.calls_to("check_agent_permission") == []
    assert len(rig.world.graph.calls_to("get_agent")) == 1, "the agent document holds the prompt and the tools"
    assert rig.loop.query_info["systemPrompt"] == "be brief"


def test_leg10a_without_access_on_the_header_the_permission_check_runs_as_today(rig: Rig) -> None:
    _served(rig.agent_stream(header=rig.header(access=None)))
    assert len(rig.world.graph.calls_to("check_agent_permission")) == 1
    _served(rig.agent_stream(header=rig.header(access={"run": False})))
    assert len(rig.world.graph.calls_to("check_agent_permission")) == 2


def test_leg10a_a_header_without_access_does_not_open_an_agent_the_caller_cannot_run(rig: Rig) -> None:
    response = rig.agent_stream("private", header=rig.header(agent="private", access=None))
    assert response.status_code == 404
    assert not rig.loop.calls


def test_leg10a_a_bound_installation_s_credential_is_not_read(rig: Rig) -> None:
    _served(rig.agent_stream(header=rig.header(bindings=BOUND)))
    reads = rig.world.config.reads
    assert toolset_path("inst-1", "u-bob") not in reads
    assert mcp_credentials_path("u-bob") not in reads
    assert reads.count(toolset_path("inst-2", "u-bob")) == 1, "an installation the header does not name is read as today"
    assert reads.count(MCP_INSTANCE) == 1, "the instance definition holds the transport"
    info = rig.loop.query_info
    assert info["toolsetConfigs"] == {
        "inst-1": toolset_reference("inst-1", "u-bob"),
        "inst-2": toolset_reference("inst-2", "u-bob"),
    }
    assert info["mcpServerConfigs"]["mcp-1"][REF_KEY] == mcp_reference("mcp-1", "u-bob")
    assert info["mcpServerConfigs"]["mcp-1"]["ownerId"] == "u-bob"
    assert [t["instanceId"] for t in info["toolsets"]] == ["inst-1", "inst-2"]


def test_leg10a_without_bindings_every_credential_is_read_as_today(rig: Rig) -> None:
    _served(rig.agent_stream(header=rig.header()))
    reads = rig.world.config.reads
    assert reads.count(toolset_path("inst-1", "u-bob")) == reads.count(toolset_path("inst-2", "u-bob")) == 1
    assert reads.count(mcp_credentials_path("u-bob")) == 1


def test_leg10a_a_binding_of_another_owner_is_not_this_turn_s(rig: Rig) -> None:
    """A user's binding never stands in for a service-account agent's own credential, nor another user's for the
    caller's."""
    others = [
        {"installation": "inst-1", "owner": "user:u-alice", "ref": "bnd-1"},
        {"installation": "inst-1", "owner": "agent:u-bob", "ref": "bnd-3"},
    ]
    _served(rig.agent_stream(header=rig.header(bindings=others)))
    assert rig.world.reads_of(toolset_path("inst-1", "u-bob")) == 1

    sa = [{"installation": "inst-1", "owner": "user:u-bob", "ref": "bnd-1"}]
    _served(rig.agent_stream("sa", header=rig.header(agent="sa", bindings=sa)))
    assert rig.world.reads_of(toolset_path("inst-1", "sa")) == 1


def test_leg10a_a_service_account_agent_s_binding_is_the_agent_s(rig: Rig) -> None:
    bound = [{"installation": "inst-1", "owner": "agent:sa", "ref": "bnd-9"}]
    _served(rig.agent_stream("sa", header=rig.header(agent="sa", bindings=bound)))
    assert rig.world.reads_of(toolset_path("inst-1", "sa")) == 0
    assert rig.loop.calls[-1]["query_info"]["toolsetConfigs"] == {"inst-1": toolset_reference("inst-1", "sa")}


def test_leg10a_the_flow_rules_reach_the_loop(rig: Rig) -> None:
    flow = [{"level": "ask", "above": "public"}, {"level": "deny", "above": "private"}]
    _served(rig.agent_stream(header=rig.header(flow=flow)))
    assert rig.loop.query_info["resolvedFlowRules"] == (FlowRule("ask", "public"), FlowRule("deny", "private"))
    assert rig.world.reads_of(APPROVALS_NODE) == 0


def test_leg10a_the_flow_rules_reach_the_assistant_s_loop(rig: Rig) -> None:
    _served(rig.post(ASSISTANT, header=rig.header(agent=None, flow=[{"level": "deny", "above": "public"}])))
    assert rig.loop.query_info["resolvedFlowRules"] == (FlowRule("deny", "public"),)


def test_leg10a_without_a_header_the_loop_gets_no_resolved_rules(rig: Rig) -> None:
    _served(rig.agent_stream())
    assert rig.loop.query_info["resolvedFlowRules"] is None
    _served(rig.post(ASSISTANT))
    assert rig.loop.calls[-1]["query_info"]["resolvedFlowRules"] is None


def test_leg10a_the_switches_the_header_carries_are_not_read_from_the_settings(rig: Rig) -> None:
    """Actions, skills and user context answer from the header (also where the loop's factory reads them); the
    MCP switch reads the settings document until its reader goes through the same path (slice A)."""
    _served(rig.agent_stream(header=rig.header()))
    assert rig.world.settings_reads <= 1
    assert rig.loop.flags == rig.loop.task_flags == [
        {"ENABLE_ACTIONS": True, "ENABLE_SKILLS": True, "ENABLE_USER_CONTEXT": True}
    ]
    assert rig.world.settings_reads <= 1, "the loop's own reads answered from the header too"


def test_leg10a_no_switch_is_read_from_the_settings_document(rig: Rig) -> None:
    _served(rig.agent_stream(header=rig.header()))
    assert rig.world.settings_reads == 0


def test_leg10a_the_process_builds_one_verifier_and_reads_the_keys_once(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(POLICY_STORE_ENV, raising=False)
    rig = Rig(world, monkeypatch, now=real_now())
    del rig.app.state.resolved_turn_verifier
    for _ in range(3):
        _served(rig.agent_stream(header=rig.header()))
    assert world.reads_of(KEYS_NODE) == 1
    assert rig.app.state.resolved_turn_verifier is not None


# -- LEG-10b ------------------------------------------------------------------------------------------------------


def _refused_headers(rig: Rig) -> dict[str, tuple[str, str, dict[str, Any] | None]]:
    """reason -> (header, route agent, body override)."""
    valid = rig.header()
    head, body, signature = valid.split(".")
    return {
        "resolved_signature": (f"{head}.{body}.{'B' if signature[0] == 'A' else 'A'}{signature[1:]}", "shared", None),
        "resolved_expired": (rig.header(iat=NOW - 120, exp=NOW - 60), "shared", None),
        "resolved_malformed": ("not-a-token", "shared", None),
        "resolved_audience": (rig.header(aud="pipeshub-agent-harness/gateway"), "shared", None),
        "resolved_subject_mismatch": (rig.header("alice"), "shared", None),
        "resolved_agent_mismatch": (rig.header(agent="private"), "shared", None),
        "resolved_conversation_mismatch": (valid, "shared", {"conversationId": "conv-2"}),
        "resolved_routing": (
            rig.header(pin={**VECTOR["valid"]["payload"]["pin"], "routing": {"path": "durable", "reason": None}}),
            "shared",
            None,
        ),
        "resolved_version": (rig.header(ver=2), "shared", None),
    }


@pytest.mark.parametrize(
    "reason",
    [
        "resolved_signature", "resolved_expired", "resolved_malformed", "resolved_audience", "resolved_subject_mismatch",
        "resolved_agent_mismatch", "resolved_conversation_mismatch", "resolved_routing", "resolved_version",
    ],
)
def test_leg10b_a_refused_header_is_401_before_any_read_or_model_call(
    rig: Rig, reason: str, caplog: pytest.LogCaptureFixture
) -> None:
    header, agent, body = _refused_headers(rig)[reason]
    with caplog.at_level(logging.INFO):
        response = rig.agent_stream(agent, header=header, body=body)
    assert _refusal(response) == reason
    assert rig.world.graph.calls == []
    assert rig.reads_before_the_keys() == []
    assert rig.loop.calls == [] and rig.model_calls == 0
    refusals = [r for r in caplog.records if getattr(r, "harness_event", None) == "legacy.resolved_refused"]
    assert len(refusals) == 1 and refusals[0].levelno == logging.WARNING
    assert refusals[0].harness["reasonCode"] == reason  # type: ignore[attr-defined]
    assert refusals[0].harness["orgId"] == "org-1" and refusals[0].harness["userId"] == "u-bob"  # type: ignore[attr-defined]
    assert header not in caplog.text and header not in response.text
    if "." in header:
        assert all(part not in caplog.text and part not in response.text for part in header.split("."))


def test_leg10b_a_body_without_a_conversation_is_never_the_header_s(rig: Rig) -> None:
    response = rig.client.post(
        "/api/v1/agent/shared/chat/stream",
        headers={"x-test-user": "bob", RESOLVED_TURN_HEADER: rig.header()},
        json={"query": "hi"},
    )
    assert _refusal(response) == "resolved_conversation_mismatch"


def test_leg10b_an_empty_header_is_a_header(rig: Rig) -> None:
    assert _refusal(rig.agent_stream(header="")) == "resolved_malformed"
    assert rig.loop.calls == []


def test_leg10b_unreadable_keys_refuse_and_never_fall_back_to_the_reads(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(POLICY_STORE_ENV, raising=False)
    world.config.put(KEYS_NODE, None)
    rig = Rig(world, monkeypatch)
    assert _refusal(rig.agent_stream(header=rig.header())) == "resolved_unknown_key"
    assert world.graph.calls == [] and rig.loop.calls == []


@pytest.mark.parametrize("path", ["/api/v1/agent/shared/chat", ASSISTANT, ASSISTANT_JSON, "/api/v1/agent/agentIdPlaceholder/chat/stream"])
def test_leg10b_every_legacy_chat_route_refuses_a_header_that_is_not_its_turn_s(rig: Rig, path: str) -> None:
    """The non-streaming twins and the assistant's routes verify the same way."""
    agent = "shared" if "/agent/shared/" in path else None
    other = "private" if agent is None else None
    assert _refusal(rig.post(path, header=rig.header(agent=other))) == "resolved_agent_mismatch"
    assert _refusal(rig.post(path, header=rig.header("alice", agent=agent))) == "resolved_subject_mismatch"
    assert rig.loop.calls == [] and rig.model_calls == 0
    assert rig.world.graph.calls == [] and rig.reads_before_the_keys() == []


@pytest.mark.parametrize("path", ["/api/v1/agent/shared/chat", ASSISTANT, ASSISTANT_JSON])
def test_leg10b_every_legacy_chat_route_serves_its_own_turn(rig: Rig, path: str) -> None:
    agent = "shared" if "/agent/" in path else None
    response = rig.post(path, header=rig.header(agent=agent, access={"run": True}))
    assert response.status_code == 200, response.text
    assert len(rig.loop.calls) == 1


# -- security: whose identity -------------------------------------------------------------------------------------


def test_the_identity_compared_is_the_request_s_own_never_the_body_s(rig: Rig) -> None:
    alice = rig.header("alice")
    body = {"userId": "u-alice", "orgId": "org-1", "callerEmail": "alice@acme.test"}
    assert _refusal(rig.agent_stream(header=alice, body=body)) == "resolved_subject_mismatch"
    foreign = rig.header("mallory", access={"run": True})
    assert _refusal(rig.agent_stream(header=foreign)) == "resolved_subject_mismatch"


def test_a_client_credentials_caller_is_the_identity_it_acts_as(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """``auth.py`` replaces a client-credentials token's ``userId`` (the client id) by ``createdBy``: the identity
    the routes act as. Node signs ``sub`` for that identity; the client id is nobody's turn."""
    monkeypatch.delenv(POLICY_STORE_ENV, raising=False)
    token_claims = {
        "userId": "client-7", "client_id": "client-7", "createdBy": "u-bob", "orgId": "org-1",
        "tokenType": "oauth", "scope": "agent:execute",
    }
    rig = Rig(world, monkeypatch, extra_users={"app": _normalize_regular_payload(dict(token_claims), "bearer")})
    as_identity = sign(claims(NOW, org="org-1", sub="u-bob", agent="shared", cid=CONVERSATION, access={"run": True}))
    as_client = sign(claims(NOW, org="org-1", sub="client-7", agent="shared", cid=CONVERSATION, access={"run": True}))
    assert _refusal(rig.agent_stream(caller="app", header=as_client)) == "resolved_subject_mismatch"
    _served(rig.agent_stream(caller="app", header=as_identity))
    assert rig.loop.user_info["userId"] == "u-bob"


# -- CPD10-24 -----------------------------------------------------------------------------------------------------


def test_cpd10_24_actions_off_in_the_header_loads_no_toolset_whatever_the_settings_say(rig: Rig) -> None:
    assert rig.world.flag_values["ENABLE_ACTIONS"] is True
    _served(rig.agent_stream(header=rig.header(stages={**rig.world.stages(), "ENABLE_ACTIONS": False})))
    assert rig.loop.query_info["toolsets"] == [] and rig.loop.query_info["toolsetConfigs"] == {}
    assert rig.loop.flags[0]["ENABLE_ACTIONS"] is False, "the switch holds inside the stream"
    assert rig.loop.task_flags[0]["ENABLE_ACTIONS"] is False, "and in a task the stream starts"


def test_cpd10_24_actions_on_in_the_header_holds_after_the_settings_switch_it_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(POLICY_STORE_ENV, raising=False)
    world = World(flags={"ENABLE_ACTIONS": False})
    rig = Rig(world, monkeypatch)
    _served(rig.agent_stream(header=rig.header(stages={**world.stages(), "ENABLE_ACTIONS": True})))
    assert [t["instanceId"] for t in rig.loop.query_info["toolsets"]] == ["inst-1", "inst-2"]


def test_cpd10_24_mcp_off_in_the_header_loads_no_mcp_server_whatever_the_settings_say(rig: Rig) -> None:
    assert rig.world.flag_values["ENABLE_MCP"] is True
    _served(rig.agent_stream(header=rig.header(stages={**rig.world.stages(), "ENABLE_MCP": False})))
    assert rig.loop.query_info["mcpServers"] == [] and rig.loop.query_info["mcpServerConfigs"] == {}


@pytest.mark.parametrize("path", ["/api/v1/agent/agentIdPlaceholder/chat/stream", ASSISTANT, ASSISTANT_JSON])
def test_cpd10_24_user_context_off_in_the_header_sends_no_user_info(rig: Rig, path: str) -> None:
    assert rig.world.flag_values["ENABLE_USER_CONTEXT"] is True
    off = {**rig.world.stages(), "ENABLE_USER_CONTEXT": False, "ENABLE_ACTIONS": False, "ENABLE_MCP": False}
    response = rig.post(path, header=rig.header(agent=None, stages=off))
    assert response.status_code == 200, response.text
    assert rig.loop.user_info["sendUserInfo"] is False
    assert rig.loop.flags == rig.loop.task_flags == [
        {"ENABLE_ACTIONS": False, "ENABLE_SKILLS": True, "ENABLE_USER_CONTEXT": False}
    ]


def test_cpd10_24_a_turn_s_stages_never_reach_the_next_request(rig: Rig) -> None:
    off = {**rig.world.stages(), "ENABLE_ACTIONS": False}
    _served(rig.agent_stream(header=rig.header(stages=off)))
    _served(rig.agent_stream())
    assert rig.loop.flags == [
        {"ENABLE_ACTIONS": False, "ENABLE_SKILLS": True, "ENABLE_USER_CONTEXT": True},
        {"ENABLE_ACTIONS": True, "ENABLE_SKILLS": True, "ENABLE_USER_CONTEXT": True},
    ]
    assert [t["instanceId"] for t in rig.loop.calls[1]["query_info"]["toolsets"]] == ["inst-1", "inst-2"]


@pytest.mark.parametrize("path", ["/api/v1/agent/shared/chat/stream", "/api/v1/agent/shared/chat", ASSISTANT, ASSISTANT_JSON])
async def test_cpd10_24_a_turn_s_stages_are_gone_from_the_task_that_ran_its_stream(rig: Rig, path: str) -> None:
    """A server that runs two requests in one task (or a durable run started from it) reads the settings again."""
    import httpx

    agent = "shared" if "/agent/" in path else None
    off = {**rig.world.stages(), "ENABLE_ACTIONS": False}
    transport = httpx.ASGITransport(app=rig.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://query") as client:
        response = await client.post(
            path,
            headers={"x-test-user": "bob", RESOLVED_TURN_HEADER: rig.header(agent=agent, stages=off)},
            json={"query": "hi", "conversationId": CONVERSATION},
        )
    assert response.status_code == 200, response.text
    assert rig.loop.flags[0]["ENABLE_ACTIONS"] is False
    assert await is_actions_enabled(rig.world.config) is True


# -- CPD10-26, CPD10-27 -------------------------------------------------------------------------------------------

ROUTES = ["/api/v1/agent/shared/chat/stream", "/api/v1/agent/shared/chat", ASSISTANT, ASSISTANT_JSON]


@pytest.mark.parametrize("path", ROUTES)
@pytest.mark.parametrize("mode", [None, "", "dual", "off", " DUAL "], ids=repr)
def test_cpd10_26_without_a_header_the_request_reads_as_today(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, path: str, mode: str | None
) -> None:
    if mode is not None:
        monkeypatch.setenv(POLICY_STORE_ENV, mode)
    response = rig.post(path)
    assert response.status_code == 200, response.text
    assert len(rig.loop.calls) == 1 and rig.model_calls == 1
    assert rig.world.reads_of(KEYS_NODE) == 0, "no header, no key read"
    if "/agent/" in path:
        assert len(rig.world.graph.calls_to("check_agent_permission")) == 1
        # And whether the harness is on for the org (ADR-OPS-08), from the config cache: which version of the agent
        # runs. A release without the harness read the first two only.
        assert settings_reads(rig.loop.reads_before[0]) == 3 + edition_defaults.agent_turn_live_settings_reads_extra(), (
            "the actions and MCP switches, read live, and the harness"
        )
        assert rig.world.reads_of(toolset_path("inst-1", "u-bob")) == 1
    else:
        assert settings_reads(rig.loop.reads_before[0]) == 1, "the user-context switch, read live"


@pytest.mark.parametrize("path", ROUTES)
@pytest.mark.parametrize("mode", ["on", " On "])
def test_cpd10_26_without_a_header_under_store_on_the_request_is_refused_before_any_read(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, path: str, mode: str, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(POLICY_STORE_ENV, mode)
    with caplog.at_level(logging.WARNING):
        response = rig.post(path)
    assert _refusal(response) == "resolved_required"
    assert rig.world.config.reads == [] and rig.world.graph.calls == []
    assert rig.loop.calls == [] and rig.model_calls == 0
    assert [r.harness["reasonCode"] for r in caplog.records if getattr(r, "harness_event", "") == "legacy.resolved_refused"] == [  # type: ignore[attr-defined]
        "resolved_required"
    ]


@pytest.mark.parametrize("path", ROUTES)
def test_cpd10_26_with_a_header_under_store_on_the_turn_is_served(rig: Rig, monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    monkeypatch.setenv(POLICY_STORE_ENV, "on")
    agent = "shared" if "/agent/" in path else None
    response = rig.post(path, header=rig.header(agent=agent, access={"run": True}))
    assert response.status_code == 200, response.text
    assert len(rig.loop.calls) == 1
    assert rig.world.reads_of(APPROVALS_NODE) == 0


@pytest.mark.parametrize("path", ROUTES)
def test_cpd10_26_a_store_mode_nobody_knows_serves_nothing(rig: Rig, monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    monkeypatch.setenv(POLICY_STORE_ENV, "maybe")
    assert rig.post(path).status_code >= 400
    assert rig.loop.calls == [] and rig.model_calls == 0


async def test_cpd10_26_a_store_mode_nobody_knows_is_refused_at_first_use(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(POLICY_STORE_ENV, "maybe")
    request = SimpleNamespace(headers={}, state=SimpleNamespace(user={"orgId": "org-1", "userId": "u-bob"}))
    with pytest.raises(InvalidPolicyStoreMode):
        _ = await resolved_turn_for(request, agent_id="shared", conversation_id=CONVERSATION)  # type: ignore[arg-type]


# What Node's chat proxy of the release before sends for a legacy agent turn: the body it builds, its own
# headers, and no X-PipesHub-Resolved.
OLD_NODE_BODY: dict[str, Any] = {
    "query": "what changed in Q3?",
    "previousConversations": [],
    "conversationId": CONVERSATION,
    "chatMode": "planExecute",
    "modelKey": None,
    "modelName": None,
    "timezone": "UTC",
    "currentTime": "2026-10-12T10:00:00.000Z",
    "tools": None,
    "filters": {"apps": [], "kb": []},
}


@pytest.mark.parametrize("path", ["/api/v1/agent/shared/chat/stream", ASSISTANT])
def test_cpd10_27_an_older_node_s_request_under_store_on_is_refused_never_served_from_the_kv_copies(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    monkeypatch.setenv(POLICY_STORE_ENV, "on")
    rig.world.config.put(APPROVALS_NODE, {"default": {"flow": {"level": "off"}}})
    body = {k: v for k, v in OLD_NODE_BODY.items() if "/agent/" in path or k != "tools"}
    response = rig.client.post(path, headers={"x-test-user": "bob", "client-name": "pipeshub-web"}, json=body)
    assert _refusal(response) == "resolved_required"
    assert rig.world.config.reads == [] and rig.loop.calls == []


# -- CPD10-83 -----------------------------------------------------------------------------------------------------

BASE_COMMIT = "f8b0e34a5"
REPO = Path(__file__).resolve().parents[6]


def _base_route_module() -> types.ModuleType:
    """``app/api/routes/agent.py`` as the release before this slice had it, loaded beside today's."""
    try:
        source = subprocess.run(
            ["git", "-C", str(REPO), "show", f"{BASE_COMMIT}:backend/python/app/api/routes/agent.py"],
            capture_output=True, text=True, check=True, timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        pytest.skip(f"the base tree {BASE_COMMIT} is not readable here: {type(exc).__name__}")
    module = types.ModuleType("app.api.routes._agent_before_cpd10")
    module.__file__ = str(REPO / "backend/python/app/api/routes/agent.py")
    sys.modules[module.__name__] = module
    try:
        exec(compile(source, module.__file__, "exec"), module.__dict__)
    finally:
        del sys.modules[module.__name__]
    return module


def test_cpd10_83_the_route_of_the_release_before_ignores_the_header(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """Skew: a Node that sends the header with a query service that does not know it. The old handler never reads
    the header (FastAPI hands a handler only what it asks for), so the turn is served from its own reads."""
    base = _base_route_module()
    assert RESOLVED_TURN_HEADER not in (base.__loader__ or "") and "resolved" not in vars(base)
    rig = Rig(world, monkeypatch)
    monkeypatch.setattr(base, "run_agent_loop_stream", rig.loop)
    monkeypatch.setattr(base, "get_llm_for_chat", vars(sys.modules["app.api.routes.agent"])["get_llm_for_chat"])
    monkeypatch.setattr(base, "load_entity_vector_store", vars(sys.modules["app.api.routes.agent"])["load_entity_vector_store"])
    app = FastAPI()
    app.include_router(base.router, prefix="/api/v1/agent")
    app.container = FakeContainer(world.graph, world.config)  # type: ignore[attr-defined]
    app.add_middleware(_TestAuth, users=rig.users)
    client = TestClient(app, raise_server_exceptions=False)
    for header in (rig.header(access={"run": True}), "not-a-token"):
        response = client.post(
            "/api/v1/agent/shared/chat/stream",
            headers={"x-test-user": "bob", RESOLVED_TURN_HEADER: header},
            json={"query": "hi", "conversationId": CONVERSATION},
        )
        _served(response)
    assert len(world.graph.calls_to("check_agent_permission")) == 2, "its own permission check, as before"
    assert world.reads_of(KEYS_NODE) == 0
    assert all("resolvedFlowRules" not in call["query_info"] for call in rig.loop.calls)


# -- LEG-10a: the flow guard ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("flow", [(), (FlowRule("ask", "public"),)], ids=["no rule", "the org's rule"])
async def test_leg10a_the_flow_guard_takes_the_header_s_rules_and_reads_no_approvals_document(
    flow: tuple[FlowRule, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    """From the route's ``query_info`` to the loop's context to the guard: ``build_initial_state`` and
    ``AgentContext.from_chat_state`` are the production ones."""
    from app.agents.agent_loop.context import AgentContext
    from app.agents.agent_loop.hooks.flow_guard import register_legacy_flow_guard
    from app.modules.agents.qna.chat_state import build_initial_state
    from harness_core.kernel.hooks.registry import HookRegistry

    monkeypatch.setenv("HARNESS_FLOW_GUARD", "false")
    world = World(approvals={"default": {"flow": {"level": "deny"}}})
    query_info = {"query": "hi", "agentKey": "shared", "resolvedFlowRules": flow}
    user_info = {"userId": "u-bob", "orgId": "org-1"}
    state = build_initial_state(
        query_info, user_info, None, logging.getLogger("test"), None, None, None, world.config,  # type: ignore[arg-type]
        "model", "key", {}, has_sql_connector=False,
    )
    context = AgentContext.from_chat_state(state)
    assert context.resolved_flow_rules == flow
    assert await register_legacy_flow_guard(HookRegistry(), context) is bool(flow)
    assert world.reads_of(APPROVALS_NODE) == 0

    read = AgentContext.from_chat_state(
        build_initial_state(
            {"query": "hi", "agentKey": "shared"}, user_info, None, logging.getLogger("test"), None, None, None,  # type: ignore[arg-type]
            world.config, "model", "key", {}, has_sql_connector=False,
        )
    )
    assert read.resolved_flow_rules is None
    assert await register_legacy_flow_guard(HookRegistry(), read) is True
    assert world.reads_of(APPROVALS_NODE) == 1, "without a header the guard reads the document, as before"
