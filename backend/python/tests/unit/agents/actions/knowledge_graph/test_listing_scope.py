"""`knowledgegraph__list_files` through the real `KnowledgeHubService` and
scope resolver; only the graph database is stubbed, with an autospec of the
production `ArangoHTTPProvider`. The stubbed search mirrors the provider's
query: it projects knowledge-base records with `connectorId` set to null, then
applies the connector filter to the projection, before paging.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock, create_autospec

import pytest

from app.agents.actions.knowledge_graph.ops.listing import execute_list_files
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

USER_KEY = "user-key-1"


def _node(node_id: str, name: str, node_type: str = "record") -> dict[str, Any]:
    return {"id": node_id, "name": name, "nodeType": node_type, "origin": "CONNECTOR"}


# (connector id, node) pairs the user can see: three apps and two KBs.
_USER_VISIBLE = [
    ("app-jira", _node("jira-1", "budget ticket")),
    ("app-drive", _node("drive-1", "budget sheet")),
    ("app-slack", _node("slack-1", "budget thread")),
    ("kb-hr", _node("hr-1", "budget policy")),
    ("kb-hr", _node("hr-2", "budget faq")),
    ("kb-finance", _node("fin-1", "budget plan")),
]
_USER_APPS = ["app-jira", "app-drive", "app-slack", "kb-hr", "kb-finance"]
_KB_APPS = frozenset({"kb-hr", "kb-finance"})

KB_SEARCH_GAP = pytest.mark.xfail(
    strict=True,
    reason="graph providers null connectorId on KB records before the connector filter",
)


def _project_like_current_providers(connector_id: str, node: dict[str, Any]) -> dict[str, Any]:
    """Mirror both graph providers' current search projection, which sets
    connectorId to null on knowledge-base records before the connector filter."""
    # When the providers stop nulling connectorId, change this helper and remove KB_SEARCH_GAP.
    return {**node, "connectorId": None if connector_id in _KB_APPS else connector_id}


def _passes_connector_filter(node: dict[str, Any], connector_ids: list[str]) -> bool:
    # _build_knowledge_hub_filter_conditions: (app node whose id is listed) OR
    # (node.connectorId listed), evaluated on the projected node.
    return (node["nodeType"] == "app" and node["id"] in connector_ids) or node["connectorId"] in connector_ids


def _provider_search(
    *, skip: int, limit: int, search_query: str | None = None,
    connector_ids: list[str] | None = None, **_: object,
) -> dict[str, Any]:
    projected = [_project_like_current_providers(cid, node) for cid, node in _USER_VISIBLE]
    matches = [
        node for node in projected
        if (not connector_ids or _passes_connector_filter(node, connector_ids))
        and (not search_query or search_query in node["name"])
    ]
    return {"nodes": matches[skip:skip + limit], "total": len(matches)}


def _provider_root_nodes(*, user_app_ids: list[str], skip: int, limit: int, **_: object) -> dict[str, Any]:
    nodes = [_node(app_id, app_id, "app") for app_id in user_app_ids]
    return {"nodes": nodes[skip:skip + limit], "total": len(nodes)}


@pytest.fixture
def graph() -> MagicMock:
    g = create_autospec(ArangoHTTPProvider, instance=True)
    g.get_user_by_user_id.return_value = {"_key": USER_KEY}
    g.get_knowledge_hub_search.side_effect = _provider_search
    g.get_knowledge_hub_root_nodes.side_effect = _provider_root_nodes
    g.get_user_app_ids.return_value = list(_USER_APPS)
    g.get_user_permission_app_ids.return_value = []
    g.get_knowledge_hub_filter_options.return_value = {"apps": []}
    return g


def _state(graph: MagicMock, *, apps: list[str], kb: list[str]) -> dict[str, Any]:
    return {
        "graph_provider": graph,
        "org_id": "org-1",
        "user_id": "user-ext-1",
        "apps": apps,
        "kb": kb,
        "logger": logging.getLogger("test.kg_listing"),
    }


def _ids(text: str) -> set[str]:
    return {part.split("=", 1)[1].split()[0] for part in text.split("|") if "_id=" in part}


class TestSearchByName:
    async def test_a_name_search_searches(self, graph: MagicMock) -> None:
        ok, text = await execute_list_files(_state(graph, apps=["app-jira"], kb=[]), query="budget")

        assert ok is True
        graph.get_knowledge_hub_search.assert_awaited_once()
        assert graph.get_knowledge_hub_search.await_args.kwargs["search_query"] == "budget"
        graph.get_knowledge_hub_root_nodes.assert_not_awaited()
        assert _ids(text) == {"jira-1"}

    async def test_without_a_query_it_lists(self, graph: MagicMock) -> None:
        await execute_list_files(_state(graph, apps=["app-jira"], kb=[]))

        graph.get_knowledge_hub_search.assert_not_awaited()
        graph.get_knowledge_hub_root_nodes.assert_awaited_once()


class TestStaysInsideTheAgentsSources:
    async def test_kb_only_agent_search_returns_nothing_from_other_sources(self, graph: MagicMock) -> None:
        other_sources = {"jira-1", "drive-1", "slack-1", "fin-1"}
        unscoped = {n["id"] for n in _provider_search(skip=0, limit=50, search_query="budget")["nodes"]}
        assert other_sources <= unscoped

        _, text = await execute_list_files(_state(graph, apps=[], kb=["kb-hr"]), query="budget")

        assert graph.get_knowledge_hub_search.await_args.kwargs["search_query"] == "budget"
        assert not _ids(text) & other_sources

    @KB_SEARCH_GAP
    async def test_kb_only_agent_search_finds_its_kb_files(self, graph: MagicMock) -> None:
        _, text = await execute_list_files(_state(graph, apps=[], kb=["kb-hr"]), query="budget")
        assert _ids(text) == {"hr-1", "hr-2"}

    async def test_kb_only_agent_listing_shows_only_its_kbs(self, graph: MagicMock) -> None:
        _, text = await execute_list_files(_state(graph, apps=[], kb=["kb-hr"]))
        assert _ids(text) == {"kb-hr"}

    async def test_mixed_agent_search_returns_nothing_from_other_sources(self, graph: MagicMock) -> None:
        _, text = await execute_list_files(_state(graph, apps=["app-jira"], kb=["kb-hr"]), query="budget")
        assert _ids(text) <= {"jira-1", "hr-1", "hr-2"}
        assert "jira-1" in _ids(text)

    @KB_SEARCH_GAP
    async def test_mixed_agent_search_finds_its_kb_files_too(self, graph: MagicMock) -> None:
        _, text = await execute_list_files(_state(graph, apps=["app-jira"], kb=["kb-hr"]), query="budget")
        assert _ids(text) == {"jira-1", "hr-1", "hr-2"}

    async def test_mixed_agent_listing_shows_only_its_sources(self, graph: MagicMock) -> None:
        _, text = await execute_list_files(_state(graph, apps=["app-jira"], kb=["kb-hr"]))
        assert _ids(text) == {"app-jira", "kb-hr"}

    async def test_narrowing_never_widens(self, graph: MagicMock) -> None:
        state = _state(graph, apps=["app-jira", "app-drive"], kb=["kb-hr"])
        _, text = await execute_list_files(state, query="budget", source_ids=["app-jira", "app-slack"])
        assert _ids(text) == {"jira-1"}

    @KB_SEARCH_GAP
    async def test_narrowing_to_one_kb_finds_its_files(self, graph: MagicMock) -> None:
        state = _state(graph, apps=["app-jira"], kb=["kb-hr"])
        _, text = await execute_list_files(state, query="budget", source_ids=["kb-hr", "kb-finance"])
        assert _ids(text) == {"hr-1", "hr-2"}

    async def test_scoped_results_page_with_correct_totals(self, graph: MagicMock) -> None:
        state = _state(graph, apps=["app-jira", "app-drive"], kb=[])
        pages = [await execute_list_files(state, query="budget", page=p, limit=1) for p in (1, 2)]

        assert [len(_ids(text)) for _, text in pages] == [1, 1]
        assert set().union(*(_ids(text) for _, text in pages)) == {"jira-1", "drive-1"}
        kwargs = [c.kwargs for c in graph.get_knowledge_hub_search.await_args_list]
        assert [(k["skip"], k["limit"]) for k in kwargs] == [(0, 1), (1, 1)]
