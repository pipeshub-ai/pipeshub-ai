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

    @pytest.mark.parametrize("stored,fragment", [
        (None, "not configured: 'Jira'"),
        ({"isAuthenticated": False}, "not authenticated: 'Jira'"),
    ])
    def test_unusable_credentials_stop_the_run_with_a_next_step(self, graph, loop, stored, fragment) -> None:
        self._with_toolset(graph, "private")
        config = FakeConfigService({"/services/toolsets/inst-1/u-alice": stored} if stored else {})
        c, _ = make_client(graph, config)
        response = _stream(c, "private", "alice")
        assert response.status_code == 200
        assert "RUN_ERROR" in response.text
        assert fragment in response.text
        assert "Workspace" in response.text
        assert not loop.calls

    def test_actions_flag_off_drops_every_toolset(self, graph, loop) -> None:
        from app.services.featureflag.platform_settings import PLATFORM_SETTINGS_KEY

        self._with_toolset(graph, "private")
        config = FakeConfigService({PLATFORM_SETTINGS_KEY: {"featureFlags": {"ENABLE_ACTIONS": False}}})
        c, _ = make_client(graph, config)
        _stream(c, "private", "alice")
        (call,) = loop.calls
        assert call["query_info"]["toolsets"] == []


class TestChatStreamKnowledge:
    def test_agent_knowledge_becomes_the_retrieval_scope(self, graph, loop) -> None:
        graph.add_node("agentKnowledge", {"_key": "k1", "connectorId": "conn-1", "type": "APP"})
        graph.add_edge("agentHasKnowledge", {"_from": f"{AGENTS}/private", "_to": "agentKnowledge/k1"})
        c, _ = make_client(graph)
        _stream(c, "private", "alice")
        (call,) = loop.calls
        assert call["query_info"]["filters"] == {"apps": ["conn-1"], "kb": ["NO_KB_SELECTED"]}
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
        assert response.status_code == 400
        assert response.json()["message"] == "The answer could not be saved. Try again."

    def test_run_error_is_returned_as_an_error(self, graph, loop) -> None:
        loop.frames = _sse(AGUI_FORMATTER.error(_CTX, message="The model is busy. Try again shortly.", code="rate_limit"))
        c, _ = make_client(graph)
        response = c.post("/api/v1/agent/private/chat", headers=as_user("alice"), json={"query": "hi"})
        assert response.status_code == 400
        assert response.json()["message"] == "The model is busy. Try again shortly."

    def test_missing_credentials_reach_the_caller_with_the_next_step(self, graph, loop) -> None:
        graph.add_node("agentToolsets", {"_key": "ts", "name": "jira", "displayName": "Jira", "instanceId": "inst-1"})
        graph.add_edge("agentHasToolset", {"_from": f"{AGENTS}/private", "_to": "agentToolsets/ts"})
        c, _ = make_client(graph)
        response = c.post("/api/v1/agent/private/chat", headers=as_user("alice"), json={"query": "hi"})
        assert response.status_code == 400
        assert "Workspace → Actions" in response.json()["message"]

    def test_agent_outside_reach_is_not_found(self, graph, loop) -> None:
        c, _ = make_client(graph)
        response = c.post("/api/v1/agent/private/chat", headers=as_user("bob"), json={"query": "hi"})
        assert response.status_code == 404
