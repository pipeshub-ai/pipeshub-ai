"""``chat_stream`` hands the agent loop a scope no wider than the agent's knowledge.

Drives the real route with the agent loop replaced by a recorder, so these
fail if the route stops applying ``resolve_agent_filters`` — the pure
function's own tests cannot catch that.
"""
from __future__ import annotations

import json
import logging
from contextlib import ExitStack
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

ORG = "o1"
AGENT_KNOWLEDGE = [
    {"connectorId": "jira-1", "type": "JIRA"},
    {"connectorId": "kb-1", "type": "KB"},
]


def _services(agent: dict, *, apps: dict | None = None, roles: dict | None = None) -> dict:
    graph = AsyncMock()
    graph.get_agent = AsyncMock(return_value=agent)
    graph.check_agent_permission = AsyncMock(return_value={"can_edit": False, "role": "viewer"})

    async def _get_document(key: str, collection: str) -> dict | None:
        if collection == "users":
            return {"_key": "creator-key", "userId": "creator", "orgId": ORG, "email": "c@x.com"}
        return None

    async def _nodes(collection: str, field: str, values: list, return_fields=None) -> list:
        return [{"id": v, **(apps or {})[v]} for v in values if v in (apps or {})]

    users = {"caller": {"_key": "caller-key", "email": "u@x.com"},
             "creator": {"_key": "creator-key", "email": "c@x.com"}}
    graph.get_document = AsyncMock(side_effect=_get_document)
    graph.get_nodes_by_field_in = AsyncMock(side_effect=_nodes)
    graph.get_user_by_user_id = AsyncMock(side_effect=lambda user_id: users.get(user_id))

    async def _role(kb_id: str, user: str) -> str | None:
        return (roles or {}).get((kb_id, user))

    graph.get_user_kb_permission = AsyncMock(side_effect=_role)
    return {
        "graph_provider": graph,
        "retrieval_service": MagicMock(),
        "reranker_service": MagicMock(),
        "config_service": AsyncMock(),
        "logger": logging.getLogger("agent-route-test"),
    }


async def _run(agent: dict, body: dict, *, graph_out: list | None = None, **graph_kwargs) -> dict:
    """Run chat_stream for a saved agent; return the query_info the loop got."""
    from app.api.routes.agent import chat_stream

    captured: dict = {}

    async def _recording_loop(
        query_info: dict, *_args: object, **_kwargs: object,
    ) -> AsyncIterator[str]:
        captured.update(query_info)
        return
        yield  # pragma: no cover - marks this an async generator

    services = _services(agent, **graph_kwargs)
    if graph_out is not None:
        graph_out.append(services["graph_provider"])
    request = MagicMock()
    request.body = AsyncMock(return_value=json.dumps(body).encode())
    request.headers = {}
    request.app.state.toolset_registry = MagicMock()

    with ExitStack() as stack:
        for target, kwargs in [
            ("get_services", {"new_callable": AsyncMock, "return_value": services}),
            ("_get_user_context", {"return_value": {"userId": "caller", "orgId": ORG}}),
            ("_get_user_document", {"new_callable": AsyncMock,
                                    "return_value": {"email": "u@x.com", "_key": "caller-key"}}),
            ("_get_org_info", {"new_callable": AsyncMock,
                               "return_value": {"orgId": ORG, "accountType": "enterprise"}}),
            ("get_llm_for_chat", {"new_callable": AsyncMock, "return_value": (MagicMock(), {}, {})}),
            ("load_entity_vector_store", {"new_callable": AsyncMock, "return_value": None}),
            ("run_agent_loop_stream", {"new": _recording_loop}),
        ]:
            stack.enter_context(patch(f"app.api.routes.agent.{target}", **kwargs))
        response = await chat_stream(request, "agent-1")
        "".join([chunk async for chunk in response.body_iterator])
    return captured


def _agent(*, service_account: bool) -> dict:
    return {
        "_key": "agent-1",
        "name": "agent",
        "knowledge": AGENT_KNOWLEDGE,
        "toolsets": [],
        "models": [],
        "createdBy": "creator-key",
        "isServiceAccount": service_account,
    }


@pytest.mark.parametrize("service_account", [False, True])
class TestSavedAgentScope:
    async def test_foreign_connector_never_reaches_the_loop(self, service_account) -> None:
        query_info = await _run(
            _agent(service_account=service_account),
            {"query": "q", "filters": {"apps": ["creator-private-gmail"], "kb": []}},
        )
        assert query_info, "agent loop was not reached"
        assert "creator-private-gmail" not in query_info["filters"]["apps"]
        assert query_info["filters"]["apps"] == []
        assert all(k.get("connectorId") != "creator-private-gmail" for k in query_info["knowledge"])

    async def test_foreign_kb_is_dropped_alongside_own_sources(self, service_account) -> None:
        query_info = await _run(
            _agent(service_account=service_account),
            {"query": "q", "filters": {"apps": ["jira-1"], "kb": ["kb-1", "someone-elses-kb"]}},
        )
        assert query_info["filters"]["apps"] == ["jira-1"]
        assert query_info["filters"]["kb"] == ["kb-1"]

    async def test_no_filters_uses_the_agents_knowledge(self, service_account) -> None:
        query_info = await _run(_agent(service_account=service_account), {"query": "q"})
        assert query_info["filters"]["apps"] == ["jira-1"]
        assert query_info["filters"]["kb"] == ["kb-1"]

    async def test_callers_project_collection_is_kept(self, service_account) -> None:
        """A project chat adds the project's own hidden collection to ``kb``;
        the caller may search their own project's files."""
        hidden = {"type": "KB", "isHidden": True, "orgId": ORG}
        graphs: list = []
        query_info = await _run(
            _agent(service_account=service_account),
            {"query": "q", "filters": {"apps": ["jira-1"], "kb": ["proj-kb"]}, "strictScope": True},
            graph_out=graphs,
            apps={"proj-kb": hidden},
            roles={("proj-kb", "caller-key"): "READER"},
        )
        assert query_info["filters"]["kb"] == ["proj-kb"]
        assert query_info["filters"]["strictScope"] is True
        # Admission is checked for the caller, never the run-as creator.
        roles_checked = [c.args for c in graphs[0].get_user_kb_permission.await_args_list]
        assert roles_checked == [("proj-kb", "caller-key")]

    async def test_project_collection_after_the_projects_other_collections(self, service_account) -> None:
        """Node appends the hidden project collection after the project's
        other collections, none of which the agent has."""
        visible = {f"proj-visible-{i}": {"type": "KB", "isHidden": False, "orgId": ORG} for i in range(6)}
        hidden = {"proj-kb": {"type": "KB", "isHidden": True, "orgId": ORG}}
        query_info = await _run(
            _agent(service_account=service_account),
            {"query": "q", "filters": {"apps": ["jira-1"], "kb": [*visible, "proj-kb"]}, "strictScope": True},
            apps=visible | hidden,
            roles={("proj-kb", "caller-key"): "READER"},
        )
        assert query_info["filters"]["kb"] == ["proj-kb"]

    async def test_hidden_collection_the_caller_cannot_read_is_dropped(self, service_account) -> None:
        hidden = {"type": "KB", "isHidden": True, "orgId": ORG}
        query_info = await _run(
            _agent(service_account=service_account),
            {"query": "q", "filters": {"apps": ["jira-1"], "kb": ["proj-kb"]}},
            apps={"proj-kb": hidden},
            roles={},
        )
        assert "proj-kb" not in query_info["filters"]["kb"]


async def test_dropped_ids_are_logged_with_the_agent(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="agent-route-test"):
        await _run(
            _agent(service_account=True),
            {"query": "q", "filters": {"apps": ["creator-private-gmail"], "kb": []}},
        )
    messages = [r.getMessage() for r in caplog.records]
    assert any("agent-1" in m and "creator-private-gmail" in m for m in messages)


async def test_nothing_logged_when_nothing_is_dropped(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="agent-route-test"):
        await _run(_agent(service_account=False), {"query": "q", "filters": {"apps": ["jira-1"], "kb": []}})
    assert not any("outside the agent's knowledge" in r.getMessage() for r in caplog.records)
