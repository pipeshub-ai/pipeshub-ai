"""An agent's MCP server the person chatting may not use: the run leaves it out, the agent read marks it.

Which servers those are is the edition's `unusable_agent_mcp_server_ids`; here it is replaced so the
routes' handling is pinned on its own.
"""
from __future__ import annotations

import json
import logging
from contextlib import ExitStack
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Imported before any patch: loading it binds the edition's route overrides.
from app import edition_config
from tests.support.agent_routes import (
    FakeConfigService,
    InMemoryGraph,
    as_user,
    make_client,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi.testclient import TestClient

ORG = "o1"


def _server(instance_id: str) -> dict:
    return {
        "instanceId": instance_id,
        "name": instance_id,
        "typeId": instance_id,
        "tools": [{"name": "search", "fullName": f"mcp_{instance_id}_search"}],
    }


ATTACHED = [_server("exa"), _server("github"), _server("rovo")]


def _agent(*, service_account: bool = False) -> dict:
    return {
        "_key": "agent-1",
        "name": "agent",
        "knowledge": [],
        "toolsets": [],
        "mcpServers": [dict(server) for server in ATTACHED],
        "models": [],
        "createdBy": "creator-key",
        "isServiceAccount": service_account,
    }


def _services(agent: dict) -> dict:
    graph = AsyncMock()
    graph.get_agent = AsyncMock(return_value=agent)
    graph.check_agent_permission = AsyncMock(return_value={"can_edit": False, "role": "viewer"})
    creator = {"_key": "creator-key", "userId": "creator", "orgId": ORG, "email": "c@x.com"}
    users = {"caller": {"_key": "caller-key", "email": "u@x.com"}, "creator": creator}
    graph.get_document = AsyncMock(side_effect=lambda key, collection: creator if collection == "users" else None)
    graph.get_nodes_by_field_in = AsyncMock(return_value=[])
    graph.get_user_by_user_id = AsyncMock(side_effect=lambda user_id, **_: users.get(user_id))
    return {
        "graph_provider": graph,
        "retrieval_service": MagicMock(),
        "reranker_service": MagicMock(),
        "config_service": AsyncMock(),
        "logger": logging.getLogger("agent-unusable-mcp-test"),
    }


async def _run(agent: dict, body: dict, unusable: AsyncMock, *, agent_id: str = "agent-1") -> dict:
    """Run chat_stream with every MCP server set up; return the query_info the loop got."""
    from app.api.routes.agent import chat_stream

    captured: dict = {}

    async def _recording_loop(query_info: dict, *_args: object, **_kwargs: object) -> AsyncIterator[str]:
        captured.update(query_info)
        return
        yield  # pragma: no cover - marks this an async generator

    request = MagicMock()
    request.body = AsyncMock(return_value=json.dumps(body).encode())
    request.headers = {}
    request.app.state.toolset_registry = MagicMock()

    with ExitStack() as stack:
        for target, kwargs in [
            ("get_services", {"new_callable": AsyncMock, "return_value": _services(agent)}),
            ("_get_user_context", {"return_value": {"userId": "caller", "orgId": ORG}}),
            ("_get_user_document", {"new_callable": AsyncMock, "return_value": {"email": "u@x.com", "_key": "caller-key"}}),
            ("_get_org_info", {"new_callable": AsyncMock, "return_value": {"orgId": ORG, "accountType": "enterprise"}}),
            ("get_llm_for_chat", {"new_callable": AsyncMock, "return_value": (MagicMock(), {}, {})}),
            ("load_entity_vector_store", {"new_callable": AsyncMock, "return_value": None, "create": True}),
            ("get_assistant_agent", {"new_callable": AsyncMock, "return_value": (agent, {})}),
            ("run_agent_loop_stream", {"new": _recording_loop}),
        ]:
            stack.enter_context(patch(f"app.api.routes.agent.{target}", **kwargs))
        stack.enter_context(patch.object(edition_config, "unusable_agent_mcp_server_ids", unusable))
        stack.enter_context(patch.object(
            edition_config, "get_mcp_instance_resolved",
            AsyncMock(side_effect=lambda instance_id, *_a, **_k: {"_id": instance_id, "name": instance_id}),
        ))
        stack.enter_context(patch("app.agents.mcp.service.is_mcp_enabled", AsyncMock(return_value=True)))
        stack.enter_context(patch("app.agents.mcp.service.resolve_effective_user_auth", AsyncMock(return_value={"ok": True})))
        stack.enter_context(patch("app.agents.mcp.service.is_effective_auth_authenticated", MagicMock(return_value=True)))
        response = await chat_stream(request, agent_id)
        "".join([chunk async for chunk in response.body_iterator])
    return captured


def _loaded(query_info: dict) -> tuple[list[str], list[str]]:
    assert query_info, "agent loop was not reached"
    return [s["instanceId"] for s in query_info["mcpServers"]], sorted(query_info["mcpServerConfigs"])


@pytest.mark.parametrize("service_account", [False, True])
class TestTheRun:
    async def test_a_server_the_person_may_not_use_never_reaches_the_loop(self, *, service_account: bool) -> None:
        unusable = AsyncMock(return_value=frozenset({"github"}))
        query_info = await _run(_agent(service_account=service_account), {"query": "q"}, unusable)
        assert _loaded(query_info) == (["exa", "rovo"], ["exa", "rovo"])

    async def test_it_is_asked_about_the_person_chatting(self, *, service_account: bool) -> None:
        unusable = AsyncMock(return_value=frozenset())
        await _run(_agent(service_account=service_account), {"query": "q"}, unusable)
        attached, _config, org_id, user_id = unusable.await_args.args
        assert [s["instanceId"] for s in attached] == ["exa", "github", "rovo"]
        assert (org_id, user_id) == (ORG, "caller")

    async def test_naming_its_tools_loads_nothing_from_it(self, *, service_account: bool) -> None:
        unusable = AsyncMock(return_value=frozenset({"github"}))
        body = {"query": "q", "tools": ["mcp_github_search", "mcp_exa_search"]}
        query_info = await _run(_agent(service_account=service_account), body, unusable)
        assert _loaded(query_info) == (["exa"], ["exa"])

    async def test_with_every_server_left_out_the_agent_still_runs(self, *, service_account: bool) -> None:
        unusable = AsyncMock(return_value=frozenset({"exa", "github", "rovo"}))
        query_info = await _run(_agent(service_account=service_account), {"query": "q"}, unusable)
        assert _loaded(query_info) == ([], [])


async def test_with_none_left_out_the_run_is_unchanged() -> None:
    query_info = await _run(_agent(), {"query": "q"}, AsyncMock(return_value=frozenset()))
    assert _loaded(query_info) == (["exa", "github", "rovo"], ["exa", "github", "rovo"])


async def test_the_assistant_is_not_asked_again() -> None:
    """Its servers are already only the ones this person may use."""
    unusable = AsyncMock(return_value=frozenset({"github"}))
    query_info = await _run(_agent(), {"query": "q"}, unusable, agent_id="agentIdPlaceholder")
    unusable.assert_not_awaited()
    assert _loaded(query_info)[0] == ["exa", "github", "rovo"]


class TestTheAgentRead:
    @pytest.fixture
    def client(self) -> TestClient:
        graph = InMemoryGraph()
        graph.add_agent("private", "alice")
        client, _ = make_client(graph, FakeConfigService({
            "/services/mcp/instances/inst-1": {"_id": "inst-1", "orgId": "org-1", "name": "github"},
        }))
        saved = client.put("/api/v1/agent/private", headers=as_user("alice"), json={
            "mcpServers": [{"instanceId": "inst-1", "name": "github", "typeId": "github", "allTools": True, "tools": []}],
        })
        assert saved.status_code == 200
        return client

    def test_marks_a_server_the_viewer_may_not_use(self, client: TestClient) -> None:
        unusable = AsyncMock(return_value=frozenset({"inst-1"}))
        with patch.object(edition_config, "unusable_agent_mcp_server_ids", unusable):
            (server,) = client.get("/api/v1/agent/private", headers=as_user("alice")).json()["agent"]["mcpServers"]
        assert server["usableByViewer"] is False
        assert unusable.await_args.args[2:] == ("org-1", "u-alice")

    def test_says_nothing_about_a_server_the_viewer_may_use(self, client: TestClient) -> None:
        (server,) = client.get("/api/v1/agent/private", headers=as_user("alice")).json()["agent"]["mcpServers"]
        assert "usableByViewer" not in server

