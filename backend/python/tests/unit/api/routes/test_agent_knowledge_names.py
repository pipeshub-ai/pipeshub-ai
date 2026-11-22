"""A member who cannot open a limited connector (``accessLimit``) does not get its name from an agent's knowledge: not from
``GET /agent/{id}``, not from ``GET /agent/``, not in the sources a chat turn names to the model. The source keeps its id, as one
that cannot be resolved does; an unlimited connector keeps its name. An org admin sees every name, as on the connectors page. Driven through the real routes over the in-memory graph
(``tests/support/agent_routes.py``), whose connector list applies the limit as the graph queries do."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.modules.agents.knowledge_scope import hide_names_the_viewer_cannot_open
from tests.support.agent_routes import (
    AGENTS,
    USERS,
    InMemoryGraph,
    as_user,
    make_client,
)

# key: (accessLimit, the app's org, its creator)
SOURCES: dict[str, tuple[str | None, str, str]] = {
    "byAlice": ("CREATOR", "org-1", "u-alice"),
    "orgWide": ("ORG", "org-1", "u-alice"),
    "junk": ("SOMEONE", "org-1", "u-alice"),           # any other value admits nobody, its creator included
    "elsewhere": ("ORG", "org-2", "u-alice"),          # another org's: admitted under neither
    "open": (None, "org-1", "u-alice"),
}


def _entry(key: str) -> dict[str, Any]:
    limit = SOURCES[key][0]
    return {"_key": f"k-{key}", "connectorId": key, "name": f"Name {key}", "displayName": f"Name {key}", "type": "SLACK",
            "accessLimit": limit}


@pytest.fixture
def graph() -> InMemoryGraph:
    g = InMemoryGraph()
    for key, (limit, org, creator) in SOURCES.items():
        g.add_node("apps", {"_key": key, "type": "SLACK", "scope": "team", "orgId": org, "createdBy": creator,
                            "accessLimit": limit, "isConfigured": True})
        g.add_node("agentKnowledge", _entry(key))
    g.add_agent("shared", "alice", share_with_org=True, knowledge=[_entry(k) for k in SOURCES])
    for key in SOURCES:
        g.add_edge("agentHasKnowledge", {"_from": f"{AGENTS}/shared", "_to": f"agentKnowledge/k-{key}"})
    return g


def _names(knowledge: list[dict[str, Any]]) -> dict[str, str]:
    return {k["connectorId"]: k["name"] for k in knowledge}


def _expected(admitted: set[str]) -> dict[str, str]:
    return {k: (f"Name {k}" if SOURCES[k][0] is None or k in admitted else k) for k in SOURCES}


VIEWERS = [
    ("bob", {"orgWide"}),                    # a member: not alice's creator-limited connector
    ("alice", {"byAlice", "orgWide"}),       # its creator
    ("bob-admin", set(SOURCES)),             # an admin of the org: every name, junk and another org's stamp included
]


def _client(graph: InMemoryGraph, viewer: str) -> tuple[Any, str]:
    """``bob-admin`` is bob with the admin role in his token, as the connector routes read it (``is_request_admin``)."""
    if viewer == "bob-admin":
        client, _ = make_client(graph, extra_users={"bob": {**USERS["bob"], "role": "admin"}})
        return client, "bob"
    client, _ = make_client(graph, extra_users={"bob": {**USERS["bob"], "role": "member"}})
    return client, viewer


@pytest.mark.parametrize(("viewer", "admitted"), VIEWERS)
def test_get_agent_names_only_the_sources_the_viewer_may_open(graph: InMemoryGraph, viewer: str, admitted: set[str]) -> None:
    client, viewer = _client(graph, viewer)
    response = client.get("/api/v1/agent/shared", headers=as_user(viewer))
    assert response.status_code == 200
    knowledge = response.json()["agent"]["knowledge"]
    assert _names(knowledge) == _expected(admitted)
    assert {k["connectorId"]: k["displayName"] for k in knowledge} == _expected(admitted)


@pytest.mark.parametrize(("viewer", "admitted"), VIEWERS)
def test_the_agent_list_names_only_the_sources_the_viewer_may_open(graph: InMemoryGraph, viewer: str, admitted: set[str]) -> None:
    client, viewer = _client(graph, viewer)
    response = client.get("/api/v1/agent/", headers=as_user(viewer))
    assert response.status_code == 200
    (agent,) = response.json()["agents"]
    assert _names(agent["knowledge"]) == _expected(admitted)


@pytest.mark.parametrize(("viewer", "admitted"), VIEWERS)
def test_a_chat_turn_names_to_the_model_only_the_sources_the_caller_may_open(
    graph: InMemoryGraph, monkeypatch: pytest.MonkeyPatch, viewer: str, admitted: set[str],
) -> None:
    calls: list[dict[str, Any]] = []

    def loop(query_info: dict, *_a: object, **_k: object) -> Any:  # noqa: ANN401 - an async iterator
        calls.append(query_info)

        async def gen() -> Any:  # noqa: ANN401
            if False:
                yield ""
        return gen()

    async def llm(*_a: object, **_k: object) -> tuple[object, dict, dict]:
        return object(), {"isMultimodal": False, "provider": "openai"}, {}
    monkeypatch.setattr("app.api.routes.agent.run_agent_loop_stream", loop)
    monkeypatch.setattr("app.api.routes.agent.get_llm_for_chat", llm)
    client, viewer = _client(graph, viewer)
    client.post("/api/v1/agent/shared/chat/stream", headers=as_user(viewer), json={"query": "hi"})
    (query_info,) = calls
    assert _names(query_info["knowledge"]) == _expected(admitted)


async def test_no_lookup_is_made_when_no_source_is_limited() -> None:
    graph = AsyncMock()
    knowledge = [{"connectorId": "a", "name": "A", "displayName": "A", "accessLimit": None}, {"connectorId": "kb", "name": "KB"}]
    await hide_names_the_viewer_cannot_open(graph, [knowledge, []], "u-1", "org-1", is_admin=False)
    graph.get_user_connector_instances.assert_not_awaited()
    assert [k["name"] for k in knowledge] == ["A", "KB"]


async def test_one_lookup_covers_every_agent_and_asks_under_the_limit() -> None:
    graph = AsyncMock()
    graph.get_user_connector_instances.return_value = [{"_key": "lim-ok"}, {"id": "open"}]
    first = [{"connectorId": "lim-ok", "name": "Ok", "accessLimit": "ORG"}, {"connectorId": "lim-no", "name": "No", "accessLimit": "CREATOR"}]
    second = [{"connectorId": "lim-no", "name": "No", "displayName": "No", "accessLimit": "CREATOR"}]
    await hide_names_the_viewer_cannot_open(graph, [first, second], "u-1", "org-1", is_admin=False)
    graph.get_user_connector_instances.assert_awaited_once()
    assert graph.get_user_connector_instances.await_args.kwargs["apply_access_limit"] is True
    assert graph.get_user_connector_instances.await_args.kwargs["org_id"] == "org-1"
    assert [k["name"] for k in first] == ["Ok", "lim-no"]
    assert second[0]["name"] == second[0]["displayName"] == "lim-no"


@pytest.mark.parametrize("failure", [RuntimeError("graph down"), None])
async def test_a_failed_lookup_hides_every_limited_name(failure: Exception | None) -> None:
    graph = AsyncMock()
    if failure:
        graph.get_user_connector_instances.side_effect = failure
    else:
        graph.get_user_connector_instances.return_value = None
    knowledge = [{"connectorId": "lim", "name": "Secret", "accessLimit": "ORG"}, {"connectorId": "open", "name": "Open", "accessLimit": None}]
    await hide_names_the_viewer_cannot_open(graph, [knowledge], "u-1", "org-1", is_admin=False)
    assert [k["name"] for k in knowledge] == ["lim", "Open"]


async def test_an_admin_sees_every_name_without_a_lookup_and_a_member_the_id() -> None:
    graph = AsyncMock()
    graph.get_user_connector_instances.return_value = []          # the limit admits this user to nothing
    entries = [{"connectorId": key, "name": f"Name {key}", "displayName": f"Name {key}", "accessLimit": limit}
               for key, limit in (("creator", "CREATOR"), ("org", "ORG"), ("junk", "SOMEONE"))]
    as_admin = [dict(e) for e in entries]
    await hide_names_the_viewer_cannot_open(graph, [as_admin], "u-1", "org-1", is_admin=True)
    assert [k["name"] for k in as_admin] == ["Name creator", "Name org", "Name junk"]
    graph.get_user_connector_instances.assert_not_awaited()
    as_member = [dict(e) for e in entries]
    await hide_names_the_viewer_cannot_open(graph, [as_member], "u-1", "org-1", is_admin=False)
    assert [k["name"] for k in as_member] == ["creator", "org", "junk"]
