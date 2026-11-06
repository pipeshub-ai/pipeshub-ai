"""Agent chat routes (`POST /{agent_id}/chat/stream` and `POST /{agent_id}/chat`)
driven through the real FastAPI app.

The graph database and config store are in-memory stand-ins
(`tests/support/agent_routes.py`). Two things are replaced at their edge: the
LLM factory (`get_llm_for_chat`, which would build a network client) and the
agent loop (`run_agent_loop_stream`, which would call that LLM). The fake loop
records what the route handed it and answers with frames built by the real
AG-UI formatter, serialised exactly as `stream_bridge.py` writes them.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, NoReturn

import pytest

from app.agents.agent_loop.protocol.formatter import AGUI_FORMATTER
from tests.support.agent_routes import (
    AGENTS,
    FakeConfigService,
    InMemoryGraph,
    as_user,
    make_client,
    user_key,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi.testclient import TestClient
    from httpx import Response


def _sse(frames: list[dict[str, Any]]) -> list[str]:
    return [f"event: {f['event']}\ndata: {json.dumps(f['data'])}\n\n" for f in frames]


_CTX = SimpleNamespace(run_id="run-1", conversation_id="conv-1")


class FakeLoop:
    def __init__(self, frames: list[str] | None = None) -> None:
        self.frames = frames if frames is not None else _sse(
            AGUI_FORMATTER.answer_final(_CTX, completion_data={"answer": "42", "citations": []})
        )
        self.calls: list[dict[str, Any]] = []

    def __call__(self, query_info: dict, user_info: dict, *args: object, **kwargs: object) -> AsyncIterator[str]:
        self.calls.append({"query_info": query_info, "user_info": user_info, **kwargs})

        async def gen() -> AsyncIterator[str]:
            for frame in self.frames:
                yield frame
        return gen()


@pytest.fixture
def graph() -> InMemoryGraph:
    g = InMemoryGraph()
    g.add_agent("private", "alice", systemPrompt="be brief")
    g.add_agent("shared", "alice", share_with_org=True)
    g.add_agent("sa", "alice", share_with_org=True, isServiceAccount=True)
    return g


@pytest.fixture
def loop(monkeypatch) -> FakeLoop:
    fake = FakeLoop()
    monkeypatch.setattr("app.api.routes.agent.run_agent_loop_stream", fake)

    async def llm(*_a: object, **_k: object) -> tuple[object, dict, dict]:
        return object(), {"isMultimodal": False, "provider": "openai"}, {}
    monkeypatch.setattr("app.api.routes.agent.get_llm_for_chat", llm)
    return fake


def _frames(response: Response) -> list[tuple[str, dict]]:
    frames = []
    for block in response.text.split("\n\n"):
        if block.startswith("event: ") and "\ndata: " in block:
            event, data = block.split("\ndata: ", 1)
            frames.append((event[len("event: "):], json.loads(data)))
    return frames


def _custom(response: Response, name: str) -> dict:
    for event, data in _frames(response):
        if event == "CUSTOM" and data.get("name") == name:
            return data["value"]
    raise AssertionError(f"no CUSTOM {name!r} in {response.text!r}")


def _run_error(response: Response) -> dict:
    for event, data in _frames(response):
        if event == "RUN_ERROR":
            return data
    raise AssertionError(f"no RUN_ERROR in {response.text!r}")


def _stream(client: TestClient, agent: str, caller: str, body: dict | None = None) -> Response:
    return client.post(
        f"/api/v1/agent/{agent}/chat/stream", headers=as_user(caller), json=body or {"query": "hi"},
    )


class TestChatStreamAccess:
    @pytest.mark.parametrize("caller,agent", [("bob", "private"), ("mallory", "shared")])
    def test_agent_outside_reach_reads_as_not_found(self, graph, loop, caller, agent) -> None:
        c, _ = make_client(graph)
        response = _stream(c, agent, caller)
        assert response.status_code == 404
        assert not loop.calls

    def test_colleague_can_chat_with_an_org_shared_agent(self, graph, loop) -> None:
        c, _ = make_client(graph)
        response = _stream(c, "shared", "bob")
        assert response.status_code == 200
        assert "RUN_FINISHED" in response.text
        (call,) = loop.calls
        assert call["user_info"]["userId"] == "u-bob"
        assert call["user_info"]["orgId"] == "org-1"

    def test_service_account_agent_of_another_org_reads_as_not_found(self, graph, loop) -> None:
        c, _ = make_client(graph)
        other = _stream(c, "sa", "mallory")
        missing = _stream(c, "missing", "mallory")
        assert other.status_code == missing.status_code == 404
        assert other.json() == missing.json()
        assert not loop.calls

    def test_service_account_agent_retrieves_as_its_creator(self, graph, loop) -> None:
        c, _ = make_client(graph)
        response = _stream(c, "sa", "bob", {"query": "hi", "callerDisplayName": "Bob B."})
        assert response.status_code == 200
        (call,) = loop.calls
        # Retrieval ACL is the creator's; only the name the LLM sees is the caller's.
        assert call["user_info"]["userId"] == "u-alice"
        assert call["user_info"]["fullName"] == "Bob B."
        assert call["query_info"]["is_service_account"] is True
        assert call["cancellation_owner"].user_id == "u-bob"

    def test_service_account_artifacts_belong_to_the_person_chatting(self, graph, loop) -> None:
        c, _ = make_client(graph)
        assert _stream(c, "sa", "bob", {"query": "hi", "callerDisplayName": "Bob B."}).status_code == 200
        (call,) = loop.calls
        # Only what the run makes changes hands; the run itself is still the creator's.
        assert call["query_info"]["artifact_owner_user_id"] == "u-bob"
        assert call["user_info"]["userId"] == "u-alice"

    def test_service_account_artifacts_of_a_service_caller_stay_the_creators(self, graph, loop) -> None:
        """The Slack bot's JWT is a service identity with no user to grant files to."""
        bot = {"slack-bot": {"userId": "u-slack-bot", "orgId": "org-1", "isServiceAccount": True}}
        c, _ = make_client(graph, extra_users=bot)
        assert _stream(c, "sa", "slack-bot", {"query": "hi", "callerDisplayName": "Bob"}).status_code == 200
        (call,) = loop.calls
        assert call["query_info"]["artifact_owner_user_id"] is None
        assert call["user_info"]["userId"] == "u-alice"

    @pytest.mark.parametrize("change", ["missing", "other-org"])
    def test_service_account_artifacts_of_an_unknown_caller_stay_the_creators(self, graph, loop, change) -> None:
        if change == "missing":
            del graph.nodes["users"][user_key("bob")]
        else:
            graph.nodes["users"][user_key("bob")]["orgId"] = "org-2"
        c, _ = make_client(graph)
        assert _stream(c, "sa", "bob", {"query": "hi", "callerDisplayName": "Bob B."}).status_code == 200
        (call,) = loop.calls
        assert call["query_info"]["artifact_owner_user_id"] is None

    def test_service_account_chat_fails_rather_than_guess_the_owner(self, graph, loop) -> None:
        """A caller who cannot be read is not handed to the creator: their files would go to the wrong person."""
        graph.fail("get_user_by_user_id")
        c, _ = make_client(graph)
        response = _stream(c, "sa", "bob", {"query": "hi", "callerDisplayName": "Bob B."})
        assert response.status_code == 503
        assert not loop.calls

    @pytest.mark.parametrize("agent", ["private", "shared"])
    def test_other_agents_set_no_artifact_owner(self, graph, loop, agent) -> None:
        c, _ = make_client(graph)
        caller = "alice" if agent == "private" else "bob"
        assert _stream(c, agent, caller).status_code == 200
        (call,) = loop.calls
        assert call["query_info"]["artifact_owner_user_id"] is None

    def test_service_account_creator_in_another_org_is_refused(self, graph, loop) -> None:
        graph.nodes["users"][user_key("alice")]["orgId"] = "org-2"
        c, _ = make_client(graph)
        assert _stream(c, "sa", "bob").status_code == 404
        assert not loop.calls

    def test_request_ids_are_ignored(self, graph, loop) -> None:
        c, _ = make_client(graph)
        response = _stream(c, "private", "bob", {"query": "hi", "userId": "u-alice", "orgId": "org-1"})
        assert response.status_code == 404


class TestChatStreamInput:
    def test_too_many_tools_is_rejected(self, graph, loop) -> None:
        c, _ = make_client(graph)
        response = _stream(c, "private", "alice", {"query": "hi", "tools": [f"t{i}" for i in range(1025)]})
        assert response.status_code == 400
        assert "maximum 1024 actions" in response.json()["detail"]

    def test_duplicate_run_id_is_a_conflict(self, graph, loop) -> None:
        import asyncio

        from app.agents.agent_loop.cancellation.registry import RunOwner

        c, container = make_client(graph)
        run_id = "9b2f1c3e-5d6a-4b7c-8d9e-0f1a2b3c4d5e"
        asyncio.run(container.registry.register(
            run_id, object(), RunOwner(user_id="u-alice", org_id="org-1", conversation_id=None),
        ))
        response = _stream(c, "private", "alice", {"query": "hi", "runId": run_id})
        assert response.status_code == 409

    def test_malformed_run_id_is_rejected_in_plain_words(self, graph, loop) -> None:
        c, _ = make_client(graph)
        response = _stream(c, "private", "alice", {"query": "hi", "runId": "not-a-uuid"})
        assert response.status_code == 400
        assert "Traceback" not in response.text

    def test_unknown_filter_key_is_rejected_with_the_allowed_keys(self, graph, loop) -> None:
        c, _ = make_client(graph)
        response = _stream(c, "private", "alice", {"query": "hi", "filters": {"connectors": ["x"]}})
        assert response.status_code == 400
        detail = response.json()["detail"]
        assert "Unknown filter key(s): connectors" in detail
        assert "Allowed keys: apps" in detail

    def test_missing_query_is_rejected(self, graph, loop) -> None:
        c, _ = make_client(graph)
        response = _stream(c, "private", "alice", {"limit": 3})
        assert response.status_code == 400


class TestChatStreamToolsets:
    def _with_toolset(self, graph: InMemoryGraph, agent: str) -> None:
        graph.add_node("agentToolsets", {"_key": "ts", "name": "jira", "displayName": "Jira", "instanceId": "inst-1"})
        graph.add_node("agentTools", {"_key": "t1", "name": "search", "fullName": "jira.search"})
        graph.add_node("agentTools", {"_key": "t2", "name": "create", "fullName": "jira.create"})
        graph.add_edge("agentHasToolset", {"_from": f"{AGENTS}/{agent}", "_to": "agentToolsets/ts"})
        graph.add_edge("toolsetHasTool", {"_from": "agentToolsets/ts", "_to": "agentTools/t1"})
        graph.add_edge("toolsetHasTool", {"_from": "agentToolsets/ts", "_to": "agentTools/t2"})

    def test_credentials_are_the_executing_users(self, graph, loop) -> None:
        self._with_toolset(graph, "shared")
        config = FakeConfigService({
            "/services/toolsets/inst-1/u-alice": {"isAuthenticated": True, "who": "alice"},
            "/services/toolsets/inst-1/u-bob": {"isAuthenticated": True, "who": "bob"},
        })
        c, _ = make_client(graph, config)
        response = _stream(c, "shared", "bob", {"query": "hi", "tools": ["jira.search"]})
        assert response.status_code == 200
        (call,) = loop.calls
        assert call["query_info"]["toolsetConfigs"] == {"inst-1": {"isAuthenticated": True, "who": "bob"}}
        (toolset,) = call["query_info"]["toolsets"]
        assert [t["fullName"] for t in toolset["tools"]] == ["jira.search"]

    def test_service_account_uses_the_agents_own_credentials(self, graph, loop) -> None:
        self._with_toolset(graph, "sa")
        config = FakeConfigService({
            "/services/toolsets/inst-1/sa": {"isAuthenticated": True, "who": "agent"},
            "/services/toolsets/inst-1/u-bob": {"isAuthenticated": True, "who": "bob"},
        })
        c, _ = make_client(graph, config)
        _stream(c, "sa", "bob")
        (call,) = loop.calls
        assert call["query_info"]["toolsetConfigs"]["inst-1"]["who"] == "agent"

    @pytest.mark.parametrize("stored,problem", [
        (None, "not_configured"),
        ({"isAuthenticated": False}, "not_authenticated"),
    ])
    def test_a_toolset_the_user_has_not_connected_is_left_out_and_the_run_goes_on(
        self, graph, loop, stored, problem,
    ) -> None:
        self._with_toolset(graph, "private")
        config = FakeConfigService({"/services/toolsets/inst-1/u-alice": stored} if stored else {})
        c, _ = make_client(graph, config)

        response = _stream(c, "private", "alice", {"query": "do you like coffee?"})

        assert response.status_code == 200
        assert "RUN_ERROR" not in response.text
        (call,) = loop.calls
        assert call["query_info"]["toolsets"] == []
        assert call["query_info"]["toolsetConfigs"] == {}
        item = {
            "kind": "toolset", "instanceId": "inst-1", "displayName": "Jira", "name": "jira",
            "problem": problem, "authMode": None, "sharedCredential": False, "userCanConnect": True,
        }
        assert call["query_info"]["unavailableTools"] == [item]
        assert _custom(response, "tools_unavailable") == {
            "agentId": "private", "serviceAccount": False, "items": [item],
        }
        assert "42" in response.text

    def test_the_connected_toolsets_still_run_beside_a_missing_one(self, graph, loop) -> None:
        self._with_toolset(graph, "shared")
        graph.add_node("agentToolsets", {"_key": "ts2", "name": "gmail", "displayName": "Gmail", "instanceId": "inst-2"})
        graph.add_edge("agentHasToolset", {"_from": f"{AGENTS}/shared", "_to": "agentToolsets/ts2"})
        config = FakeConfigService({
            "/services/toolsets/inst-1/u-bob": {"isAuthenticated": True, "who": "bob"},
            # Alice (the agent's owner) connected Gmail; Bob never borrows her credential.
            "/services/toolsets/inst-2/u-alice": {"isAuthenticated": True, "who": "alice"},
        })
        c, _ = make_client(graph, config)

        _stream(c, "shared", "bob")

        (call,) = loop.calls
        assert [t["instanceId"] for t in call["query_info"]["toolsets"]] == ["inst-1"]
        assert call["query_info"]["toolsetConfigs"] == {"inst-1": {"isAuthenticated": True, "who": "bob"}}
        assert [i["instanceId"] for i in call["query_info"]["unavailableTools"]] == ["inst-2"]

    def test_everything_connected_sends_no_notice(self, graph, loop) -> None:
        self._with_toolset(graph, "private")
        config = FakeConfigService({"/services/toolsets/inst-1/u-alice": {"isAuthenticated": True}})
        c, _ = make_client(graph, config)
        response = _stream(c, "private", "alice")
        assert "tools_unavailable" not in response.text
        assert loop.calls[0]["query_info"]["unavailableTools"] == []

    def test_a_service_account_agent_missing_its_credential_still_stops_and_names_it(self, graph, loop) -> None:
        self._with_toolset(graph, "sa")
        # The caller's own credential never stands in for the agent's.
        config = FakeConfigService({"/services/toolsets/inst-1/u-bob": {"isAuthenticated": True}})
        c, _ = make_client(graph, config)

        response = _stream(c, "sa", "bob")

        error = _run_error(response)
        assert error["code"] == "toolset_config_missing"
        assert "Ask an admin to connect them" in error["message"]
        assert "'Jira' (not configured)" in error["message"]
        assert error["details"] == {
            "agentId": "sa",
            "serviceAccount": True,
            "items": [{
                "kind": "toolset", "instanceId": "inst-1", "displayName": "Jira", "name": "jira",
                "problem": "not_configured", "authMode": None, "sharedCredential": False, "userCanConnect": False,
            }],
            "servers": [],
        }
        assert not loop.calls

    def test_actions_flag_off_drops_every_toolset(self, graph, loop) -> None:
        from app.services.featureflag.platform_settings import PLATFORM_SETTINGS_KEY

        self._with_toolset(graph, "private")
        config = FakeConfigService({PLATFORM_SETTINGS_KEY: {"featureFlags": {"ENABLE_ACTIONS": False}}})
        c, _ = make_client(graph, config)
        _stream(c, "private", "alice")
        (call,) = loop.calls
        assert call["query_info"]["toolsets"] == []


class TestChatStreamMcpServers:
    """An MCP server the user can't use is left out of the run and named; a service-account
    agent missing one still stops, with a frame the chat can act on."""

    def _attach(self, graph: InMemoryGraph, agent: str, instance_id: str = "inst-m", name: str = "GitHub") -> None:
        graph.add_node("agentMcpServers", {"_key": "ms", "instanceId": instance_id, "name": "github", "displayName": name})
        graph.add_edge("agentHasMcpServer", {"_from": f"{AGENTS}/{agent}", "_to": "agentMcpServers/ms"})

    def _config(self, **records: dict) -> FakeConfigService:
        from app.services.featureflag.platform_settings import PLATFORM_SETTINGS_KEY

        return FakeConfigService({PLATFORM_SETTINGS_KEY: {"featureFlags": {"ENABLE_MCP": True}}, **records})

    @staticmethod
    def _instance(**overrides: object) -> dict:
        return {
            "_id": "inst-m", "orgId": "org-1", "createdBy": "u-alice", "name": "GitHub", "transport": "streamable_http",
            "url": "https://mcp.example.com/mcp", "authMode": "oauth", "useAdminAuth": False, "scope": "org",
            **overrides,
        }

    def test_a_server_the_user_has_not_connected_is_left_out_and_named(self, graph, loop) -> None:
        self._attach(graph, "private")
        c, _ = make_client(graph, self._config(**{"/services/mcp/instances/inst-m": self._instance()}))

        response = _stream(c, "private", "alice")

        assert "RUN_ERROR" not in response.text
        (call,) = loop.calls
        assert call["query_info"]["mcpServers"] == []
        assert call["query_info"]["mcpServerConfigs"] == {}
        assert _custom(response, "tools_unavailable")["items"] == [{
            "kind": "mcp", "instanceId": "inst-m", "displayName": "GitHub", "name": "github",
            "problem": "not_authenticated", "authMode": "oauth", "sharedCredential": False, "userCanConnect": True,
        }]

    def test_a_connected_server_runs_with_the_users_own_credential(self, graph, loop) -> None:
        self._attach(graph, "shared")
        c, _ = make_client(graph, self._config(**{
            "/services/mcp/instances/inst-m": self._instance(),
            "/services/mcp/credentials/inst-m/u-bob": {"isAuthenticated": True, "who": "bob"},
            "/services/mcp/credentials/inst-m/u-alice": {"isAuthenticated": True, "who": "alice"},
        }))

        _stream(c, "shared", "bob")

        (call,) = loop.calls
        config = call["query_info"]["mcpServerConfigs"]["inst-m"]
        assert config["auth"]["who"] == "bob" and config["ownerId"] == "u-bob"
        assert call["query_info"]["unavailableTools"] == []

    def test_a_removed_server_is_marked_not_found(self, graph, loop) -> None:
        self._attach(graph, "private")
        c, _ = make_client(graph, self._config())

        (item,) = _custom(_stream(c, "private", "alice"), "tools_unavailable")["items"]

        assert item["problem"] == "not_found"
        assert item["userCanConnect"] is False

    def test_a_missing_shared_credential_is_for_an_admin(self, graph, loop) -> None:
        self._attach(graph, "private")
        shared = self._instance(authMode="api_token", useAdminAuth=True)
        c, _ = make_client(graph, self._config(**{"/services/mcp/instances/inst-m": shared}))

        (item,) = _custom(_stream(c, "private", "alice"), "tools_unavailable")["items"]

        assert item["sharedCredential"] is True
        assert item["authMode"] == "api_token"
        assert item["userCanConnect"] is False

    def test_a_service_account_agent_stops_with_the_servers_named(self, graph, loop) -> None:
        self._attach(graph, "sa")
        c, _ = make_client(graph, self._config(**{
            "/services/mcp/instances/inst-m": self._instance(),
            # The caller's credential never backs a service-account agent's server.
            "/services/mcp/credentials/inst-m/u-bob": {"isAuthenticated": True},
        }))

        error = _run_error(_stream(c, "sa", "bob"))

        assert error["code"] == "mcp_server_config_missing"
        assert error["details"]["serviceAccount"] is True
        assert error["details"]["agentId"] == "sa"
        assert error["details"]["servers"] == [{
            "instanceId": "inst-m", "name": "GitHub", "problem": "not_connected",
            "authMode": "oauth", "sharedCredential": False,
        }]
        (item,) = error["details"]["items"]
        assert item["kind"] == "mcp" and item["userCanConnect"] is False
        assert not loop.calls

    def test_a_service_account_agent_uses_its_own_server_credential(self, graph, loop) -> None:
        self._attach(graph, "sa")
        c, _ = make_client(graph, self._config(**{
            "/services/mcp/instances/inst-m": self._instance(),
            "/services/mcp/credentials/inst-m/sa": {"isAuthenticated": True, "who": "agent"},
        }))

        _stream(c, "sa", "bob")

        (call,) = loop.calls
        assert call["query_info"]["mcpServerConfigs"]["inst-m"]["ownerId"] == "sa"


class TestChatStreamKnowledge:
    def test_agent_knowledge_becomes_the_retrieval_scope(self, graph, loop) -> None:
        graph.add_node("agentKnowledge", {"_key": "k1", "connectorId": "conn-1", "type": "APP"})
        graph.add_edge("agentHasKnowledge", {"_from": f"{AGENTS}/private", "_to": "agentKnowledge/k1"})
        c, _ = make_client(graph)
        _stream(c, "private", "alice")
        (call,) = loop.calls
        # The agent's sources also bound what a tool may read by id.
        assert call["query_info"]["filters"] == {
            "apps": ["conn-1"], "kb": ["NO_KB_SELECTED"],
            "allowedApps": ["conn-1"], "allowedRecordGroups": [], "allowedRecords": [],
        }
        assert call["query_info"]["systemPrompt"] == "be brief"

    def test_internal_search_switched_off_clears_knowledge(self, graph, loop) -> None:
        graph.add_node("agentKnowledge", {"_key": "k1", "connectorId": "conn-1"})
        graph.add_edge("agentHasKnowledge", {"_from": f"{AGENTS}/private", "_to": "agentKnowledge/k1"})
        c, _ = make_client(graph)
        _stream(c, "private", "alice", {"query": "hi", "agentCapabilities": {"internalSearch": False}})
        (call,) = loop.calls
        assert call["query_info"]["knowledge"] == []
        assert call["query_info"]["filters"] == {"apps": [], "kb": ["NO_KB_SELECTED"]}

    def test_explicit_filters_keep_the_agents_other_sources(self, graph, loop) -> None:
        graph.add_node("agentKnowledge", {"_key": "k1", "connectorId": "kb-1", "type": "KB"})
        graph.add_node("agentKnowledge", {"_key": "k2", "connectorId": "conn-1"})
        for k in ("k1", "k2"):
            graph.add_edge("agentHasKnowledge", {"_from": f"{AGENTS}/private", "_to": f"agentKnowledge/{k}"})
        c, _ = make_client(graph)
        _stream(c, "private", "alice", {"query": "hi", "filters": {"apps": ["conn-1"]}, "strictScope": True})
        (call,) = loop.calls
        assert call["query_info"]["filters"]["apps"] == ["conn-1"]
        assert call["query_info"]["filters"]["strictScope"] is True
        assert {k["connectorId"] for k in call["query_info"]["knowledge"]} == {"conn-1", "kb-1"}

    def test_loop_failure_becomes_a_plain_error_frame(self, graph, monkeypatch) -> None:
        def boom(*_a: object, **_k: object) -> NoReturn:
            raise RuntimeError("socket closed by 10.0.0.7")
        monkeypatch.setattr("app.api.routes.agent.run_agent_loop_stream", boom)

        async def llm(*_a: object, **_k: object) -> tuple[object, dict, dict]:
            return object(), {}, {}
        monkeypatch.setattr("app.api.routes.agent.get_llm_for_chat", llm)
        c, _ = make_client(graph)
        response = _stream(c, "private", "alice")
        assert "RUN_ERROR" in response.text
        assert "10.0.0.7" not in response.text

    def test_no_model_configured_is_reported_as_such(self, graph, monkeypatch) -> None:
        async def none(*_a: object, **_k: object) -> None:
            return None
        monkeypatch.setattr("app.api.routes.agent.get_llm_for_chat", none)
        c, _ = make_client(graph)
        response = _stream(c, "private", "alice")
        assert "RUN_ERROR" in response.text


class TestNonStreamingChat:
    """`POST /{agent_id}/chat` drains `chat_stream`, which always speaks AG-UI."""

    def test_answer_is_returned(self, graph, loop) -> None:
        c, _ = make_client(graph)
        response = c.post("/api/v1/agent/private/chat", headers=as_user("alice"), json={"query": "hi"})
        assert response.status_code == 200
        assert response.json() == {"answer": "42", "citations": []}

    def test_nested_run_finishing_first_does_not_hide_the_answer(self, graph, loop) -> None:
        from app.agents.agent_loop.protocol.agui import AGUIEventType, frame

        child_finished = frame(
            AGUIEventType.RUN_FINISHED, runId="child", parentRunId="run-1",
            result={"answer": "child answer"},
        )
        loop.frames = loop.frames + _sse([child_finished])
        c, _ = make_client(graph)
        response = c.post("/api/v1/agent/private/chat", headers=as_user("alice"), json={"query": "hi"})
        assert response.status_code == 200
        assert response.json() == {"answer": "42", "citations": []}

    def test_recovered_sub_agent_failure_keeps_the_answer(self, graph, loop) -> None:
        # AGUIEventEmitter sends RUN_ERROR only for a child run; AgentTool hands the
        # failure back to the parent as a tool result and the parent still answers.
        from app.agents.agent_loop.protocol.agui import AGUIEventType, frame

        child_error = frame(
            AGUIEventType.RUN_ERROR, runId="child", parentRunId="run-1",
            message="sub-agent failed", code="agent_error",
        )
        loop.frames = _sse([child_error]) + loop.frames
        c, _ = make_client(graph)
        response = c.post("/api/v1/agent/private/chat", headers=as_user("alice"), json={"query": "hi"})
        assert response.status_code == 200
        assert response.json() == {"answer": "42", "citations": []}

    def test_root_run_error_after_finishing_is_still_an_error(self, graph, loop) -> None:
        loop.frames = loop.frames + _sse(
            AGUI_FORMATTER.error(_CTX, message="The answer could not be saved. Try again.", code="stream_error")
        )
        c, _ = make_client(graph)
        response = c.post("/api/v1/agent/private/chat", headers=as_user("alice"), json={"query": "hi"})
        assert response.status_code == 500
        assert response.json()["message"] == "The answer could not be saved. Try again."

    def test_run_error_is_returned_as_an_error(self, graph, loop) -> None:
        loop.frames = _sse(AGUI_FORMATTER.error(_CTX, message="The model is busy. Try again shortly.", code="rate_limit"))
        c, _ = make_client(graph)
        response = c.post("/api/v1/agent/private/chat", headers=as_user("alice"), json={"query": "hi"})
        assert response.status_code == 429
        assert response.json()["message"] == "The model is busy. Try again shortly."

    def test_a_missing_toolset_comes_back_with_the_answer(self, graph, loop) -> None:
        graph.add_node("agentToolsets", {"_key": "ts", "name": "jira", "displayName": "Jira", "instanceId": "inst-1"})
        graph.add_edge("agentHasToolset", {"_from": f"{AGENTS}/private", "_to": "agentToolsets/ts"})
        c, _ = make_client(graph)
        response = c.post("/api/v1/agent/private/chat", headers=as_user("alice"), json={"query": "hi"})
        assert response.status_code == 200
        body = response.json()
        assert body["answer"] == "42"
        assert [i["instanceId"] for i in body["unavailableTools"]] == ["inst-1"]
        assert "events" not in body

    def test_a_service_account_agent_missing_credentials_reaches_the_caller_with_the_tools(self, graph, loop) -> None:
        graph.add_node("agentToolsets", {"_key": "ts", "name": "jira", "displayName": "Jira", "instanceId": "inst-1"})
        graph.add_edge("agentHasToolset", {"_from": f"{AGENTS}/sa", "_to": "agentToolsets/ts"})
        c, _ = make_client(graph)
        response = c.post("/api/v1/agent/sa/chat", headers=as_user("bob"), json={"query": "hi"})
        assert response.status_code == 424
        body = response.json()
        assert "Ask an admin" in body["message"]
        assert [i["displayName"] for i in body["details"]["items"]] == ["Jira"]

    def test_agent_outside_reach_is_not_found(self, graph, loop) -> None:
        c, _ = make_client(graph)
        response = c.post("/api/v1/agent/private/chat", headers=as_user("bob"), json={"query": "hi"})
        assert response.status_code == 404

    def test_a_disconnected_caller_closes_the_agent_loop(self, graph, loop, monkeypatch) -> None:
        """The collector stops on disconnect; `aclosing` in `chat_stream` must then close
        the loop's generator at once, so its producer task is cancelled rather than left
        running until garbage collection."""
        closed: list[bool] = []
        # Held like a traceback or reference cycle would hold it: without an explicit
        # close, garbage collection never gets the chance to finalize the generator.
        live: list[object] = []

        def loop_that_records_close(*_a: object, **_k: object) -> AsyncIterator[str]:
            async def gen() -> AsyncIterator[str]:
                try:
                    for _ in range(1000):
                        yield ": keep-alive\n\n"
                finally:
                    closed.append(True)
            stream = gen()
            live.append(stream)
            return stream

        async def disconnected(self) -> bool:  # noqa: ANN001 - patched Request method
            return True

        from app.api.routes import agent as agent_routes

        collect = agent_routes.collect_stream_outcome
        closed_when_collected: list[bool] = []

        async def collect_and_check(*args: object, **kwargs: object):  # noqa: ANN202
            outcome = await collect(*args, **kwargs)
            # Checked inside the request: TestClient's loop shutdown would close
            # every live async generator afterwards and hide a missing close.
            closed_when_collected.append(bool(closed))
            return outcome

        monkeypatch.setattr("app.api.routes.agent.run_agent_loop_stream", loop_that_records_close)
        monkeypatch.setattr("app.api.routes.agent.collect_stream_outcome", collect_and_check)
        monkeypatch.setattr("starlette.requests.Request.is_disconnected", disconnected)
        c, _ = make_client(graph)

        response = c.post("/api/v1/agent/private/chat", headers=as_user("alice"), json={"query": "hi"})

        assert response.status_code == 499
        assert closed_when_collected == [True]
