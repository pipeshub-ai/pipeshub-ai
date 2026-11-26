"""A member who cannot open a limited connector (``accessLimit``) does not get its name from an agent's knowledge: not from
``GET /agent/{id}``, not from ``GET /agent/``, not in the sources a chat turn names to the model. The source keeps its id, as one
that cannot be resolved does; an unlimited connector keeps its name. An org admin sees every name, as on the connectors page. Driven through the real routes over the in-memory graph
(``tests/support/agent_routes.py``), whose connector list applies the limit as the graph queries do."""

from __future__ import annotations

import copy
import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.modules.agents.knowledge_scope import (
    _without_titles,
    hide_names_the_viewer_cannot_open,
    keep_hidden_titles,
)
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


TITLE = "Board minutes 2026"      # a folder title the agent builder saved on alice's creator-limited source
_SAVED = {"recordGroups": ["rg-board"], "nodes": [{"id": "rg-board", "name": TITLE, "nodeType": "recordGroup"}]}


def _entry(key: str) -> dict[str, Any]:
    limit = SOURCES[key][0]
    saved = {"filters": json.dumps(_SAVED), "filtersParsed": copy.deepcopy(_SAVED)} if key == "byAlice" else {}
    return {"_key": f"k-{key}", "connectorId": key, "name": f"Name {key}", "displayName": f"Name {key}", "type": "SLACK",
            "accessLimit": limit, **saved}


@pytest.fixture
def graph() -> InMemoryGraph:
    g = InMemoryGraph()
    for key, (limit, org, creator) in SOURCES.items():
        # The providers' get_agent names each source after its connector.
        g.add_node("apps", {"_key": key, "name": f"Name {key}", "type": "SLACK", "scope": "team", "orgId": org,
                            "createdBy": creator, "accessLimit": limit, "isConfigured": True})
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

    async def turn_filters(*, agent_knowledge: list[dict[str, Any]], **_k: object) -> dict[str, Any]:
        # Every source on: the narrowed source's selection is checked by the graph's access check, which the stand-in has not.
        return {"apps": [k["connectorId"] for k in agent_knowledge]}
    monkeypatch.setattr("app.api.routes.agent.run_agent_loop_stream", loop)
    monkeypatch.setattr("app.api.routes.agent.get_llm_for_chat", llm)
    monkeypatch.setattr("app.api.routes.agent._resolve_turn_filters", turn_filters)
    client, viewer = _client(graph, viewer)
    client.post("/api/v1/agent/shared/chat/stream", headers=as_user(viewer), json={"query": "hi"})
    (query_info,) = calls
    assert _names(query_info["knowledge"]) == _expected(admitted)


@pytest.mark.parametrize(("viewer", "admitted"), VIEWERS)
def test_a_chat_turn_without_streaming_names_to_the_model_only_the_sources_the_caller_may_open(
    graph: InMemoryGraph, monkeypatch: pytest.MonkeyPatch, viewer: str, admitted: set[str],
) -> None:
    """``POST /chat`` runs ``chat_stream`` on its own request, so the caller's admin role decides there too."""
    calls: list[dict[str, Any]] = []

    def loop(query_info: dict, *_a: object, **_k: object) -> Any:  # noqa: ANN401 - an async iterator
        calls.append(query_info)

        async def gen() -> Any:  # noqa: ANN401
            if False:
                yield ""
        return gen()

    async def llm(*_a: object, **_k: object) -> tuple[object, dict, dict]:
        return object(), {"isMultimodal": False, "provider": "openai"}, {}

    async def turn_filters(*, agent_knowledge: list[dict[str, Any]], **_k: object) -> dict[str, Any]:
        return {"apps": [k["connectorId"] for k in agent_knowledge]}
    monkeypatch.setattr("app.api.routes.agent.run_agent_loop_stream", loop)
    monkeypatch.setattr("app.api.routes.agent.get_llm_for_chat", llm)
    monkeypatch.setattr("app.api.routes.agent._resolve_turn_filters", turn_filters)
    client, viewer = _client(graph, viewer)
    client.post("/api/v1/agent/shared/chat", headers=as_user(viewer), json={"query": "hi"})
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


def _leaks(payload: object) -> set[str]:
    text = json.dumps(payload, default=str)
    return {secret for secret in ("Name byAlice", TITLE) if secret in text}


@pytest.mark.parametrize(("viewer", "hidden"), [("bob", True), ("bob-admin", False)])
def test_a_refused_member_gets_neither_the_name_nor_the_saved_titles_anywhere(
    graph: InMemoryGraph, monkeypatch: pytest.MonkeyPatch, viewer: str, hidden: bool,
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

    async def turn_filters(*, agent_knowledge: list[dict[str, Any]], **_k: object) -> dict[str, Any]:
        # Every source on: the narrowed source's selection is checked by the graph's access check, which the stand-in has not.
        return {"apps": [k["connectorId"] for k in agent_knowledge]}
    monkeypatch.setattr("app.api.routes.agent.run_agent_loop_stream", loop)
    monkeypatch.setattr("app.api.routes.agent.get_llm_for_chat", llm)
    monkeypatch.setattr("app.api.routes.agent._resolve_turn_filters", turn_filters)
    client, user = _client(graph, viewer)
    one = client.get("/api/v1/agent/shared", headers=as_user(user)).json()
    many = client.get("/api/v1/agent/", headers=as_user(user)).json()
    client.post("/api/v1/agent/shared/chat/stream", headers=as_user(user), json={"query": "hi"})
    (chat,) = calls
    for payload in (one, many, chat["knowledge"]):
        assert _leaks(payload) == (set() if hidden else {"Name byAlice", TITLE})
    # The ids that limit the source stay, so the source is searched exactly as before.
    (source,) = [k for k in one["agent"]["knowledge"] if k["connectorId"] == "byAlice"]
    assert source["filtersParsed"]["recordGroups"] == ["rg-board"] and json.loads(source["filters"])["recordGroups"] == ["rg-board"]


def test_titles_are_left_out_of_either_form_and_an_unreadable_limit_stays_as_it_is() -> None:
    as_text = json.loads(_without_titles(json.dumps(_SAVED)))
    assert as_text == {"recordGroups": ["rg-board"], "nodes": [{"id": "rg-board", "nodeType": "recordGroup"}]}
    assert _without_titles(copy.deepcopy(_SAVED)) == as_text
    for value in ("not json", "", None, {"recordGroups": ["x"]}, ["x"]):
        assert _without_titles(value) == value


def _stored_filters(graph: InMemoryGraph, connector_id: str) -> dict[str, Any]:
    (node,) = [n for n in graph.nodes["agentKnowledge"].values() if n["connectorId"] == connector_id]
    return json.loads(node["filters"]) if isinstance(node["filters"], str) else node["filters"]


def _titles(filters: dict[str, Any]) -> dict[str, str | None]:
    return {n["id"]: n.get("name") for n in filters.get("nodes", [])}


def _save(graph: InMemoryGraph, viewer: str, edit: Any) -> None:  # noqa: ANN401 - a callable over the knowledge list
    """``viewer`` opens the shared agent, changes its knowledge with ``edit`` and saves it, as the agent builder does."""
    graph.add_edge("permission", {"_from": "users/k-bob", "_to": f"{AGENTS}/shared", "type": "USER", "role": "WRITER"})
    graph.get_selection_nodes = AsyncMock(return_value={"groups": [], "records": []})  # the size check, not tested here
    client, user = _client(graph, viewer)
    knowledge = client.get("/api/v1/agent/shared", headers=as_user(user)).json()["agent"]["knowledge"]
    body = [{"connectorId": k["connectorId"], "filters": k.get("filters") or {}} for k in knowledge]
    edit(body)
    assert client.put("/api/v1/agent/shared", headers=as_user(user), json={"knowledge": body}).status_code == 200


def _by_alice(body: list[dict[str, Any]]) -> dict[str, Any]:
    (entry,) = [k for k in body if k["connectorId"] == "byAlice"]
    entry["filters"] = json.loads(entry["filters"]) if isinstance(entry["filters"], str) else entry["filters"]
    return entry["filters"]


def test_a_refused_editor_saving_what_they_were_shown_keeps_the_titles(graph: InMemoryGraph) -> None:
    _save(graph, "bob", lambda body: None)
    saved = _stored_filters(graph, "byAlice")
    assert _titles(saved) == {"rg-board": TITLE} and saved["recordGroups"] == ["rg-board"]
    for viewer in ("alice", "bob-admin"):      # the creator and an admin read the original title back
        client, user = _client(graph, viewer)
        assert TITLE in json.dumps(client.get("/api/v1/agent/shared", headers=as_user(user)).json())


def test_a_refused_editor_who_adds_a_node_keeps_the_stored_titles(graph: InMemoryGraph) -> None:
    def add(body: list[dict[str, Any]]) -> None:
        filters = _by_alice(body)
        filters["recordGroups"].append("rg-new")
        filters["nodes"].append({"id": "rg-new", "name": "New folder", "nodeType": "recordGroup"})
    _save(graph, "bob", add)
    saved = _stored_filters(graph, "byAlice")
    assert _titles(saved) == {"rg-board": TITLE, "rg-new": "New folder"}
    assert saved["recordGroups"] == ["rg-board", "rg-new"]


def test_a_refused_editor_who_drops_the_nodes_list_still_keeps_the_titles_of_what_stays_chosen(graph: InMemoryGraph) -> None:
    _save(graph, "bob", lambda body: _by_alice(body).pop("nodes"))
    assert _titles(_stored_filters(graph, "byAlice")) == {"rg-board": TITLE}


@pytest.mark.parametrize("viewer", ["alice", "bob-admin"])
def test_an_editor_the_limit_admits_saves_a_renamed_title_as_sent(graph: InMemoryGraph, viewer: str) -> None:
    def rename(body: list[dict[str, Any]]) -> None:
        _by_alice(body)["nodes"][0]["name"] = "Renamed folder"
    if viewer == "alice":
        graph.add_edge("permission", {"_from": "users/k-alice", "_to": f"{AGENTS}/shared", "type": "USER", "role": "OWNER"})
    _save(graph, viewer, rename)
    assert _titles(_stored_filters(graph, "byAlice")) == {"rg-board": "Renamed folder"}


async def test_a_stored_limit_that_cannot_be_read_stays_as_it_is() -> None:
    graph = AsyncMock()
    graph.get_user_connector_instances.return_value = []
    incoming = {"lim": {"connectorId": "lim", "filters": {"recordGroups": ["x"]}},
                "open": {"connectorId": "open", "filters": {"recordGroups": ["y"]}}}
    stored = [{"connectorId": "lim", "accessLimit": "ORG", "filters": "not json"},
              {"connectorId": "open", "accessLimit": None, "filters": "not json"}]
    await keep_hidden_titles(graph, incoming, stored, "u-1", "org-1", is_admin=False)
    assert incoming["lim"]["filters"] == "not json"
    assert incoming["open"]["filters"] == {"recordGroups": ["y"]}        # unlimited: saved as sent
    admin = {"lim": {"connectorId": "lim", "filters": {"recordGroups": ["x"]}}}
    await keep_hidden_titles(graph, admin, stored, "u-1", "org-1", is_admin=True)
    assert admin["lim"]["filters"] == {"recordGroups": ["x"]}


SCOPE_LABEL = "Project Nightjar"     # a sync filter's list label on alice's creator-limited connector


@pytest.mark.parametrize(("viewer", "hidden"), [("bob", True), ("bob-admin", False), ("alice", False)])
def test_a_refused_members_prompt_gets_no_connector_config_of_the_hidden_source(
    graph: InMemoryGraph, monkeypatch: pytest.MonkeyPatch, viewer: str, hidden: bool,
) -> None:
    from app.modules.agents.context.source_catalog import SourceCatalog
    from app.modules.agents.qna.router import build_capability_context

    calls: list[dict[str, Any]] = []
    asked: list[list[str]] = []

    def loop(query_info: dict, *_a: object, **_k: object) -> Any:  # noqa: ANN401 - an async iterator
        calls.append(query_info)

        async def gen() -> Any:  # noqa: ANN401
            if False:
                yield ""
        return gen()

    async def llm(*_a: object, **_k: object) -> tuple[object, dict, dict]:
        return object(), {"isMultimodal": False, "provider": "openai"}, {}

    async def turn_filters(*, agent_knowledge: list[dict[str, Any]], **_k: object) -> dict[str, Any]:
        return {"apps": [k["connectorId"] for k in agent_knowledge]}

    async def configs(_config_service: object, connector_ids: list[str]) -> dict[str, Any]:
        asked.append(list(connector_ids))
        return {cid: {"sync": {"projects": {"type": "list", "value": [{"id": "p1", "label": f"{SCOPE_LABEL} {cid}"}]}}}
                for cid in connector_ids}
    monkeypatch.setattr("app.api.routes.agent.run_agent_loop_stream", loop)
    monkeypatch.setattr("app.api.routes.agent.get_llm_for_chat", llm)
    monkeypatch.setattr("app.api.routes.agent._resolve_turn_filters", turn_filters)
    monkeypatch.setattr("app.api.routes.agent.fetch_connector_configs", configs)
    client, user = _client(graph, viewer)
    client.post("/api/v1/agent/shared/chat/stream", headers=as_user(user), json={"query": "hi"})
    (query_info,) = calls
    (ids,) = asked
    secret = f"{SCOPE_LABEL} byAlice"
    catalog = SourceCatalog.from_state({"agent_knowledge": query_info["knowledge"],
                                        "connector_configs": query_info["connector_configs"]}).render()
    summary = build_capability_context(query_info)[0]
    assert ("byAlice" in ids) is not hidden
    assert (secret in catalog) is not hidden and (secret in summary) is not hidden
    assert f"{SCOPE_LABEL} open" in catalog      # an unlimited source keeps its scope lines


def _version_1(graph: InMemoryGraph) -> None:
    """Freezes the shared agent as version 1: an agent from before versions gets it on the first read of its versions."""
    client, _ = _client(graph, "alice")
    assert client.get("/api/v1/agent/shared/versions", headers=as_user("alice")).status_code == 200


@pytest.mark.parametrize(("viewer", "admitted"), VIEWERS)
def test_a_published_version_names_only_the_sources_the_viewer_may_open(
    graph: InMemoryGraph, viewer: str, admitted: set[str],
) -> None:
    _version_1(graph)
    client, user = _client(graph, viewer)
    response = client.get("/api/v1/agent/shared/versions/1", headers=as_user(user))
    assert response.status_code == 200, response.text
    assert _names(response.json()["version"]["definition"]["knowledge"]) == _expected(admitted)
    assert (TITLE in response.text) is ("byAlice" in admitted)


def test_a_connector_limited_after_the_version_was_published_is_weighed_as_it_is_now(graph: InMemoryGraph) -> None:
    """The version froze byAlice unlimited; its connector is limited to alice since. Bob is shown its id only."""
    graph.nodes["agentKnowledge"]["k-byAlice"]["accessLimit"] = None
    _version_1(graph)
    graph.nodes["apps"]["byAlice"]["accessLimit"] = "CREATOR"
    client, user = _client(graph, "bob")
    response = client.get("/api/v1/agent/shared/versions/1", headers=as_user(user))
    assert response.status_code == 200, response.text
    assert _names(response.json()["version"]["definition"]["knowledge"])["byAlice"] == "byAlice"
    assert TITLE not in response.text


@pytest.mark.parametrize(("viewer", "admitted"), VIEWERS)
@pytest.mark.parametrize("version", ["draft", "1"])
def test_an_export_carries_only_the_sources_the_viewer_may_open(
    graph: InMemoryGraph, viewer: str, admitted: set[str], version: str,
) -> None:
    _version_1(graph)
    client, user = _client(graph, viewer)
    response = client.get(f"/api/v1/agent/shared/export?version={version}", headers=as_user(user))
    assert response.status_code == 200, response.text
    named = {k["name"] for k in response.json()["agent"]["knowledge"]}
    # A hidden source is named by its id, which the portable form leaves out as a source that is gone.
    assert named == {f"Name {k}" for k in SOURCES if SOURCES[k][0] is None or k in admitted}


@pytest.mark.asyncio
@pytest.mark.parametrize(("is_admin", "listed"), [(False, {"orgWide", "open"}), (True, set(SOURCES) - {"elsewhere"})])
async def test_an_import_binds_only_the_connectors_the_importers_limit_admits(
    graph: InMemoryGraph, monkeypatch: pytest.MonkeyPatch, is_admin: bool, listed: set[str],
) -> None:
    """A member's import is offered, and told of, only what their limit admits; an admin's, every org connector."""
    import app.agents.mcp.service as mcp_service
    import app.agents.toolset_store as toolset_store
    import app.edition_config as edition
    from app.api.routes import agent as routes

    async def nothing(*_a: object, **_k: object) -> list[dict[str, Any]]:
        return []
    for module, name in ((routes, "configured_llm_entries"), (edition, "load_instances_for_mutation"),
                         (toolset_store, "load_user_instances"), (mcp_service, "load_visible_instances")):
        monkeypatch.setattr(module, name, nothing)
    catalog = await routes._import_catalog(
        {"config_service": object(), "graph_provider": graph}, "org-1", "u-bob", is_admin=is_admin,
    )
    assert {c["_key"] for c in catalog.connectors} == listed
