"""`knowledgehub.list_files` driven through the real `KnowledgeHubService`
and the real scope resolver; only the graph database is stubbed, with an
autospec of the production provider so a call with the wrong arguments fails.
"""

from __future__ import annotations

import inspect
import json
import logging
from typing import Any
from unittest.mock import MagicMock, create_autospec

import pytest

from app.agents.actions.knowledge_hub.knowledge_hub import (
    MAX_QUERY_LENGTH,
    KnowledgeHub,
)
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

from .kh_listing_stub import connector_pages, node, root_listings, stub_listing

ORG_ID = "org-1"
USER_ID = "user-ext-1"
USER_KEY = "user-key-1"
AGENT_APPS = ["app-jira", "app-drive"]
AGENT_KBS = ["kb-hr"]
AGENT_SOURCES = [*AGENT_APPS, *AGENT_KBS]
# The user can open more than the agent is configured with.
OTHER_APP = "app-not-on-this-agent"


def _docs(count: int) -> list[dict[str, Any]]:
    return [node(f"rec-{i:02d}", f"doc {i:02d}") for i in range(count)]


@pytest.fixture
def graph() -> MagicMock:
    g = create_autospec(ArangoHTTPProvider, instance=True)
    stub_listing(g, USER_KEY, {**{source: [] for source in AGENT_SOURCES}, OTHER_APP: []})
    return g


def _state(graph: MagicMock, **overrides: object) -> dict[str, Any]:
    state: dict[str, Any] = {
        "graph_provider": graph,
        "org_id": ORG_ID,
        "user_id": USER_ID,
        "apps": list(AGENT_APPS),
        "kb": list(AGENT_KBS),
        "logger": logging.getLogger("test.knowledge_hub"),
    }
    state.update(overrides)
    return state


def _search(graph: MagicMock) -> tuple[list[str], dict[str, Any]]:
    """The sources a search read, and the filters it handed every one of them."""
    pages = connector_pages(graph)
    assert pages, "nothing was searched"
    filters = pages[0]["filters"]
    assert all(page["filters"] == filters for page in pages)
    return sorted(page["app_id"] for page in pages), filters


def _item_ids(payload: str) -> set[str]:
    return {i["id"] for i in json.loads(payload)["items"]}


class TestSearchByName:
    async def test_query_reaches_the_graph_search(self, graph: MagicMock) -> None:
        stub_listing(graph, USER_KEY, {"app-drive": [node("rec-1", "Q3 budget.xlsx"), node("rec-2", "Roadmap.md")]})
        ok, payload = await KnowledgeHub(_state(graph)).list_files(query="budget")

        assert ok is True
        searched, filters = _search(graph)
        assert searched == ["app-drive"]
        assert filters["search_query"] == "budget"
        assert root_listings(graph) == []
        assert [i["name"] for i in json.loads(payload)["items"]] == ["Q3 budget.xlsx"]

    async def test_search_is_scoped_to_the_user_and_agent_sources(self, graph: MagicMock) -> None:
        await KnowledgeHub(_state(graph)).list_files(query="budget")

        graph.get_user_by_user_id.assert_awaited_once_with(user_id=USER_ID)
        graph.get_knowledge_hub_access_context_v2.assert_awaited_once_with(user_key=USER_KEY, org_id=ORG_ID)
        graph.get_knowledge_hub_access_v3.assert_awaited_once_with(user_key=USER_KEY, org_id=ORG_ID)
        searched, filters = _search(graph)
        assert searched == sorted(AGENT_SOURCES)
        assert {(p["org_id"], *p["grantee_ids"]) for p in connector_pages(graph)} == {(ORG_ID, USER_KEY)}
        assert filters["connector_ids"] == AGENT_SOURCES
        assert filters["record_group_ids"] == AGENT_KBS

    async def test_caller_cannot_choose_the_user_or_org(self) -> None:
        params = inspect.signature(KnowledgeHub.list_files).parameters
        assert not {"user_id", "org_id", "user_key"} & set(params)

    async def test_requested_connectors_are_narrowed_never_widened(self, graph: MagicMock) -> None:
        await KnowledgeHub(_state(graph)).list_files(
            query="budget", connector_ids=["app-drive", OTHER_APP],
        )
        searched, filters = _search(graph)
        assert searched == ["app-drive"]
        assert filters["connector_ids"] == ["app-drive"]

    async def test_connectors_outside_the_agent_fall_back_to_its_own(self, graph: MagicMock) -> None:
        await KnowledgeHub(_state(graph)).list_files(
            query="budget", connector_ids=[OTHER_APP],
        )
        searched, filters = _search(graph)
        assert searched == sorted(AGENT_SOURCES)
        assert filters["connector_ids"] == AGENT_SOURCES

    async def test_requested_kbs_are_narrowed_never_widened(self, graph: MagicMock) -> None:
        state = _state(graph, kb=["kb-hr", "kb-eng"])
        await KnowledgeHub(state).list_files(query="policy", record_group_ids=["kb-eng", "kb-finance"])
        assert _search(graph)[1]["record_group_ids"] == ["kb-eng"]

    async def test_kbs_outside_the_agent_fall_back_to_its_own(self, graph: MagicMock) -> None:
        await KnowledgeHub(_state(graph)).list_files(query="policy", record_group_ids=["kb-finance"])
        assert _search(graph)[1]["record_group_ids"] == AGENT_KBS

    async def test_single_strings_are_accepted_for_list_filters(self, graph: MagicMock) -> None:
        await KnowledgeHub(_state(graph)).list_files(
            query="budget", connector_ids="app-jira", node_types="record", record_types="FILE",
        )
        searched, filters = _search(graph)
        assert searched == ["app-jira"]
        assert filters["connector_ids"] == ["app-jira"]
        assert filters["node_types"] == ["record"]
        assert filters["record_types"] == ["FILE"]

    async def test_unknown_node_types_and_sort_values_are_dropped(self, graph: MagicMock) -> None:
        await KnowledgeHub(_state(graph)).list_files(
            query="budget", node_types=["spreadsheet"], sort_by="DROP TABLE", sort_order="sideways",
        )
        assert _search(graph)[1]["node_types"] is None
        assert {(p["sort_field"], p["sort_dir"]) for p in connector_pages(graph)} == {("updatedAt", "DESC")}

    async def test_long_queries_are_cut_to_the_maximum(self, graph: MagicMock) -> None:
        await KnowledgeHub(_state(graph)).list_files(query="x" * (MAX_QUERY_LENGTH + 200))
        assert _search(graph)[1]["search_query"] == "x" * MAX_QUERY_LENGTH


# What the user can see: three apps and two KBs, only some of which each
# agent below is configured with.
_USER_VISIBLE = {
    "app-jira": [node("jira-1", "budget ticket")],
    "app-drive": [node("drive-1", "budget sheet")],
    "app-slack": [node("slack-1", "budget thread")],
    "kb-hr": [node("hr-1", "budget policy", origin="COLLECTION"), node("hr-2", "budget faq", origin="COLLECTION")],
    "kb-finance": [node("fin-1", "budget plan", origin="COLLECTION")],
}


class TestSearchStaysInsideTheAgentsSources:
    @pytest.fixture(autouse=True)
    def _user_sees_more_than_the_agent(self, graph: MagicMock) -> None:
        stub_listing(graph, USER_KEY, _USER_VISIBLE)

    async def test_kb_only_agent_gets_nothing_from_other_sources(self, graph: MagicMock) -> None:
        other_sources = {"jira-1", "drive-1", "slack-1", "fin-1"}
        every_source = _state(graph, apps=["app-jira", "app-drive", "app-slack"], kb=["kb-hr", "kb-finance"])
        _, unscoped = await KnowledgeHub(every_source).list_files(query="budget")
        assert other_sources <= _item_ids(unscoped)
        graph.get_knowledge_hub_connector_page_v3.reset_mock()

        state = _state(graph, apps=[], kb=["kb-hr"])
        ok, payload = await KnowledgeHub(state).list_files(query="budget")

        assert ok is True
        searched, filters = _search(graph)
        assert filters["search_query"] == "budget"
        assert not _item_ids(payload) & other_sources
        assert searched == ["kb-hr"]
        assert filters["connector_ids"] == ["kb-hr"]

    async def test_kb_only_agent_finds_its_kb_files(self, graph: MagicMock) -> None:
        _, payload = await KnowledgeHub(_state(graph, apps=[], kb=["kb-hr"])).list_files(query="budget")
        assert _item_ids(payload) == {"hr-1", "hr-2"}

    async def test_mixed_agent_gets_nothing_from_other_sources(self, graph: MagicMock) -> None:
        _, payload = await KnowledgeHub(_state(graph, apps=["app-jira"], kb=["kb-hr"])).list_files(query="budget")

        assert _item_ids(payload) <= {"jira-1", "hr-1", "hr-2"}
        assert "jira-1" in _item_ids(payload)

    async def test_mixed_agent_finds_its_kb_files_too(self, graph: MagicMock) -> None:
        _, payload = await KnowledgeHub(_state(graph, apps=["app-jira"], kb=["kb-hr"])).list_files(query="budget")
        assert _item_ids(payload) == {"jira-1", "hr-1", "hr-2"}

    async def test_scoped_results_page_with_correct_totals(self, graph: MagicMock) -> None:
        tool = KnowledgeHub(_state(graph, apps=["app-jira", "app-drive"], kb=[]))

        pages = [json.loads((await tool.list_files(query="budget", page=p, limit=1))[1]) for p in (1, 2)]

        assert [len(p["items"]) for p in pages] == [1, 1]
        assert {p["pagination"]["totalItems"] for p in pages} == {2}
        assert [p["pagination"]["hasNext"] for p in pages] == [True, False]
        assert {i["id"] for p in pages for i in p["items"]} == {"jira-1", "drive-1"}

    async def test_kb_only_agent_root_listing_shows_only_its_kbs(self, graph: MagicMock) -> None:
        state = _state(graph, apps=[], kb=["kb-hr"])
        _, payload = await KnowledgeHub(state).list_files()

        (listing,) = root_listings(graph)
        assert listing["connector_ids"] == ["kb-hr"]
        assert _item_ids(payload) == {"kb-hr"}


class TestPagination:
    async def test_a_page_number_reaches_that_page(self, graph: MagicMock) -> None:
        stub_listing(graph, USER_KEY, {"app-drive": _docs(45)})
        ok, payload = await KnowledgeHub(_state(graph)).list_files(
            query="doc", page=3, limit=10, sort_by="name", sort_order="asc",
        )

        assert ok is True
        body = json.loads(payload)
        assert [i["name"] for i in body["items"]] == [f"doc {i:02d}" for i in range(20, 30)]
        # Each page resumes after the one before it; none is re-read from the top.
        pages = connector_pages(graph)
        assert [p["limit"] for p in pages] == [10, 10, 10]
        assert [(p["after"] or {}).get("id") for p in pages] == [None, "rec-09", "rec-19"]
        shown = {key: body["pagination"][key] for key in (
            "page", "limit", "totalItems", "totalPages", "hasNext", "hasPrev",
        )}
        assert shown == {
            "page": 3, "limit": 10, "totalItems": 45, "totalPages": 5,
            "hasNext": True, "hasPrev": True,
        }
        assert "nextCursor" not in body["pagination"]

    @pytest.mark.parametrize(
        ("page", "limit", "expected_page", "expected_limit"),
        [(0, 20, 1, 20), (-4, 0, 1, 1), (2, 500, 2, 50)],
    )
    async def test_out_of_range_values_are_clamped(
        self, graph: MagicMock, page: int, limit: int, expected_page: int, expected_limit: int,
    ) -> None:
        _, payload = await KnowledgeHub(_state(graph)).list_files(query="doc", page=page, limit=limit)

        assert {p["limit"] for p in connector_pages(graph)} == {expected_limit}
        pagination = json.loads(payload)["pagination"]
        assert (pagination["page"], pagination["limit"]) == (expected_page, expected_limit)

    async def test_last_page_reports_no_next_page(self, graph: MagicMock) -> None:
        stub_listing(graph, USER_KEY, {"app-drive": _docs(21)})
        _, payload = await KnowledgeHub(_state(graph)).list_files(query="doc", page=3, limit=10)
        body = json.loads(payload)
        assert len(body["items"]) == 1
        assert (body["pagination"]["hasNext"], body["pagination"]["hasPrev"]) == (False, True)


class TestBrowsing:
    @pytest.mark.parametrize("kwargs", [{}, {"query": "a"}], ids=["no-query", "one-character-query"])
    async def test_browse_without_a_query_lists_only_the_agents_sources(
        self, graph: MagicMock, kwargs: dict[str, str],
    ) -> None:
        ok, payload = await KnowledgeHub(_state(graph)).list_files(**kwargs)

        assert ok is True
        assert connector_pages(graph) == []
        (listing,) = root_listings(graph)
        assert listing["connector_ids"] == AGENT_SOURCES
        assert listing["search_query"] is None
        assert _item_ids(payload) == set(AGENT_SOURCES)

    async def test_explicit_flattened_still_searches_without_a_query(self, graph: MagicMock) -> None:
        await KnowledgeHub(_state(graph)).list_files(flattened=True)

        searched, filters = _search(graph)
        assert searched == sorted(AGENT_SOURCES)
        assert filters["search_query"] is None
        assert filters["connector_ids"] == AGENT_SOURCES
        assert root_listings(graph) == []

    async def test_explicit_flattened_false_with_a_query_stays_a_listing(self, graph: MagicMock) -> None:
        await KnowledgeHub(_state(graph)).list_files(query="budget", flattened=False)

        assert connector_pages(graph) == []
        (listing,) = root_listings(graph)
        assert listing["connector_ids"] == AGENT_SOURCES

    async def test_parent_without_type_is_refused(self, graph: MagicMock) -> None:
        ok, payload = await KnowledgeHub(_state(graph)).list_files(query="x", parent_id="folder-1")

        assert ok is False
        assert "parent_type is required" in json.loads(payload)["message"]
        graph.get_user_by_user_id.assert_not_awaited()

    async def test_browsing_a_folder_lists_its_children_for_this_user(self, graph: MagicMock) -> None:
        stub_listing(graph, USER_KEY, {"app-drive": [
            node("folder-1", "Plans", "folder"),
            node("rec-2", "plan.md", parent_id="folder-1"),
            node("rec-3", "elsewhere.md"),
        ]})

        ok, payload = await KnowledgeHub(_state(graph)).list_files(parent_id="folder-1", parent_type="folder")

        assert ok is True
        (page,) = connector_pages(graph)
        assert (page["start_id"], page["user_key"], page["org_id"]) == ("folder-1", USER_KEY, ORG_ID)
        assert page["grantee_ids"] == [USER_KEY]
        assert page["flatten"] is False
        assert page["filters"]["record_group_ids"] == AGENT_KBS
        body = json.loads(payload)
        assert body["currentNode"]["name"] == "Plans"
        assert [i["id"] for i in body["items"]] == ["rec-2"]

    async def test_a_folder_that_is_gone_or_hidden_reads_as_not_found(self, graph: MagicMock) -> None:
        ok, payload = await KnowledgeHub(_state(graph)).list_files(parent_id="folder-x", parent_type="folder")

        assert ok is False
        assert json.loads(payload) == {"status": "error", "message": "Node not found"}

    async def test_switched_off_demo_data_is_not_browsable(self, graph: MagicMock) -> None:
        stub_listing(graph, USER_KEY, {"app-demo": [node("demo-1", "Pricing")]})
        browse = {"parent_id": "app-demo", "parent_type": "app"}
        _, shown = await KnowledgeHub(_state(graph)).list_files(**browse)
        assert _item_ids(shown) == {"demo-1"}

        state = _state(graph, excluded_app_ids=frozenset({"app-demo"}))
        ok, payload = await KnowledgeHub(state).list_files(**browse)

        assert ok is False
        # The same answer as for a node that is not there.
        assert json.loads(payload) == {"status": "error", "message": "Node not found"}
        assert "app-demo" not in connector_pages(graph)[-1]["gated_app_ids"]

    async def test_only_fetchable_ids_are_remembered(self, graph: MagicMock) -> None:
        stub_listing(graph, USER_KEY, {"app-jira": [
            node("rec-1", "docs a.pdf"),
            node("fold-1", "Docs", "folder"),
            node("rg-1", "Docs space", "recordGroup"),
        ]})
        state = _state(graph)
        _, found = await KnowledgeHub(state).list_files(query="docs")
        assert _item_ids(found) == {"rec-1", "fold-1", "rg-1"}
        _, apps = await KnowledgeHub(state).list_files()
        assert _item_ids(apps) == {"app-jira"}

        assert state["known_record_ids"] == {"rec-1", "fold-1"}


class TestFailures:
    async def test_without_state(self) -> None:
        ok, payload = await KnowledgeHub(None).list_files(query="budget")
        assert ok is False
        assert json.loads(payload)["status"] == "error"

    async def test_without_graph_provider(self, graph: MagicMock) -> None:
        ok, payload = await KnowledgeHub(_state(graph, graph_provider=None)).list_files(query="budget")
        assert ok is False
        assert json.loads(payload)["message"] == "Graph provider not available"

    async def test_agent_without_sources_searches_nothing(self, graph: MagicMock) -> None:
        state = _state(graph, apps=[], kb=[], has_knowledge=False)
        ok, payload = await KnowledgeHub(state).list_files(query="budget")

        assert ok is False
        assert json.loads(payload)["message"] == "No knowledge sources configured for this agent"
        assert connector_pages(graph) == []
        assert root_listings(graph) == []

    async def test_unknown_user_gets_an_error_not_results(self, graph: MagicMock) -> None:
        graph.get_user_by_user_id.return_value = None
        ok, payload = await KnowledgeHub(_state(graph)).list_files(query="budget")

        assert ok is False
        assert json.loads(payload)["message"] == "User not found"
        assert connector_pages(graph) == []

    async def test_database_failure_reads_as_plain_language(self, graph: MagicMock, caplog: pytest.LogCaptureFixture) -> None:
        graph.get_knowledge_hub_connector_page_v3.side_effect = ConnectionError(
            "arangodb://root:hunter2@graph:8529 refused"
        )
        ok, payload = await KnowledgeHub(_state(graph)).list_files(query="budget")

        assert ok is False
        message = json.loads(payload)["message"]
        assert message.startswith("We couldn't")
        assert "hunter2" not in payload
        assert "Traceback" not in payload

    async def test_unexpected_error_is_reported_without_a_traceback(self, graph: MagicMock) -> None:
        state = _state(graph, excluded_app_ids=42)
        ok, payload = await KnowledgeHub(state).list_files(query="budget")

        assert ok is False
        body = json.loads(payload)
        assert body["status"] == "error"
        assert body["message"].startswith("Knowledge hub error:")
        assert "Traceback" not in payload
