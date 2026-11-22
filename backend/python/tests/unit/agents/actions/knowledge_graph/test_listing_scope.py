"""`knowledgegraph__list_files` through the real `KnowledgeHubService` and
scope resolver; only the graph database is stubbed, with an autospec of the
production `ArangoHTTPProvider`. The stub applies the providers' connector
filter to each record's own `connectorId` (a knowledge base's id for its
files), before paging.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, create_autospec

import pytest

import app.modules.retrieval.selection_scope as selection_scope
from app.agents.actions.knowledge_graph.ops.listing import execute_list_files
from app.agents.actions.knowledge_graph.ops.scope import (
    SELECTION_ALONE_MESSAGE,
    SELECTION_OUTSIDE_MESSAGE,
)
from app.modules.retrieval.selection_scope import within_connection
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.interface.graph_db_provider import AccessCheck

from ..kh_listing_stub import connector_pages, node, root_listings, stub_listing

if TYPE_CHECKING:
    from collections.abc import Iterator

USER_KEY = "user-key-1"

# What the user can see: three apps and two KBs.
_USER_VISIBLE = {
    "app-jira": [node("jira-1", "budget ticket")],
    "app-drive": [node("drive-1", "budget sheet")],
    "app-slack": [node("slack-1", "budget thread")],
    "kb-hr": [node("hr-1", "budget policy", origin="COLLECTION"), node("hr-2", "budget faq", origin="COLLECTION")],
    "kb-finance": [node("fin-1", "budget plan", origin="COLLECTION")],
}


@pytest.fixture
def graph() -> MagicMock:
    g = create_autospec(ArangoHTTPProvider, instance=True)
    stub_listing(g, USER_KEY, _USER_VISIBLE)
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
        (page,) = connector_pages(graph)
        assert (page["app_id"], page["filters"]["search_query"]) == ("app-jira", "budget")
        assert root_listings(graph) == []
        assert _ids(text) == {"jira-1"}

    async def test_without_a_query_it_lists(self, graph: MagicMock) -> None:
        await execute_list_files(_state(graph, apps=["app-jira"], kb=[]))

        assert connector_pages(graph) == []
        assert len(root_listings(graph)) == 1


class TestStaysInsideTheAgentsSources:
    async def test_kb_only_agent_search_returns_nothing_from_other_sources(self, graph: MagicMock) -> None:
        other_sources = {"jira-1", "drive-1", "slack-1", "fin-1"}
        every_source = _state(graph, apps=["app-jira", "app-drive", "app-slack"], kb=["kb-hr", "kb-finance"])
        _, unscoped = await execute_list_files(every_source, query="budget")
        assert other_sources <= _ids(unscoped)
        graph.get_knowledge_hub_connector_page_v3.reset_mock()

        _, text = await execute_list_files(_state(graph, apps=[], kb=["kb-hr"]), query="budget")

        (page,) = connector_pages(graph)
        assert (page["app_id"], page["filters"]["search_query"]) == ("kb-hr", "budget")
        assert not _ids(text) & other_sources

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

    async def test_narrowing_to_one_kb_finds_its_files(self, graph: MagicMock) -> None:
        state = _state(graph, apps=["app-jira"], kb=["kb-hr"])
        _, text = await execute_list_files(state, query="budget", source_ids=["kb-hr", "kb-finance"])
        assert _ids(text) == {"hr-1", "hr-2"}

    async def test_scoped_results_page_with_correct_totals(self, graph: MagicMock) -> None:
        state = _state(graph, apps=["app-jira", "app-drive"], kb=[])
        pages = [await execute_list_files(state, query="budget", page=p, limit=1) for p in (1, 2)]

        assert [len(_ids(text)) for _, text in pages] == [1, 1]
        assert set().union(*(_ids(text) for _, text in pages)) == {"jira-1", "drive-1"}
        assert {p["limit"] for p in connector_pages(graph)} == {1}


def _check_access(graph: MagicMock) -> None:
    """`check_access` as the providers answer it for whole-app scopes: every
    node the user can see, and of those the ones inside every scope's apps."""
    owner = {row["id"]: app_id for app_id, rows in _USER_VISIBLE.items() for row in rows}
    owner |= {app_id: app_id for app_id in _USER_VISIBLE}

    def check(_user_key: str, _org_id: str, node_ids=(), scopes=(), **_: object) -> AccessCheck:
        seen = frozenset(i for i in node_ids if i in owner)
        inside = frozenset(i for i in seen if all(owner[i] in scope.app_ids for scope in scopes or ()))
        return AccessCheck(node_ids=seen, node_ids_in_scope=inside)

    graph.check_access.side_effect = check


class TestStaysInsideAConnectionLimit:
    """A connected app limited to some sources (a personal access token or an
    OAuth app with a knowledge limit) asks the universal agent, whose sources
    are everything the user can read. A name search must not name what lies
    outside the limit, though the user could open it on the website."""

    @staticmethod
    def _limited_state(graph: MagicMock) -> dict[str, Any]:
        state = _state(graph, apps=["app-jira"], kb=["kb-hr", "kb-finance"])
        state["filters"] = within_connection(
            {"apps": [], "kb": ["kb-hr", "kb-finance"]},
            {"apps": ["kb-hr"], "recordGroups": [], "records": []},
        )
        _check_access(graph)
        return state

    async def test_a_name_search_names_nothing_outside_the_limit(self, graph: MagicMock) -> None:
        _, text = await execute_list_files(self._limited_state(graph), query="budget")

        assert _ids(text) == {"hr-1", "hr-2"}
        assert "budget plan" not in text and "budget ticket" not in text

    async def test_naming_a_source_outside_the_limit_does_not_reach_it(self, graph: MagicMock) -> None:
        _, text = await execute_list_files(
            self._limited_state(graph), query="budget", source_ids=["kb-finance"],
        )

        assert not _ids(text) & {"fin-1", "kb-finance"}

    async def test_the_listing_shows_only_the_limit(self, graph: MagicMock) -> None:
        _, text = await execute_list_files(self._limited_state(graph))

        assert _ids(text) == {"kb-hr"}

    async def test_a_page_is_filled_from_inside_the_limit(self, graph: MagicMock) -> None:
        _, text = await execute_list_files(self._limited_state(graph), query="budget", limit=1)

        assert len(_ids(text)) == 1 and _ids(text) <= {"hr-1", "hr-2"}


# A collection with many files, and a folder in it that a limit names; the only
# file under the folder sorts after every other file the name search matches.
_BIG = {
    "kb-big": [
        *(node(f"big-{i:02d}", f"budget {i:02d}", origin="COLLECTION") for i in range(60)),
        node("shared-folder", "shared folder", "folder", origin="COLLECTION"),
        node("shared-file", "zz budget shared", parent_id="shared-folder", origin="COLLECTION"),
        node("folder-notes", "notes two", parent_id="shared-folder", origin="COLLECTION"),
    ],
    "kb-other": [node("other-1", "budget other", origin="COLLECTION")],
}


class TestStaysInsideAFolderLimit:
    """A limit that names a folder admits that folder and what is under it, not
    the rest of its collection, so a page of the collection is not a page of
    the answer."""

    @staticmethod
    def _graph() -> MagicMock:
        graph = create_autospec(ArangoHTTPProvider, instance=True)
        stub_listing(graph, USER_KEY, _BIG)
        owner = {row["id"]: app_id for app_id, rows in _BIG.items() for row in rows}
        owner |= {app_id: app_id for app_id in _BIG}
        children: dict[str, list[str]] = {}
        for rows in _BIG.values():
            for row in rows:
                if row.get("parentId"):
                    children.setdefault(row["parentId"], []).append(row["id"])

        def selection_nodes(_org_id: str, *, record_ids=(), **_: object) -> dict[str, Any]:
            # A named record and everything under it, as the providers walk it.
            found, todo = [], list(record_ids)
            while todo:
                node_id = todo.pop()
                found.append(node_id)
                todo.extend(children.get(node_id, []))
            return {"groups": [], "records": [
                {"id": i, "vrid": "", "connectorId": owner[i]} for i in sorted(found) if i in owner
            ]}

        graph.get_selection_nodes.side_effect = selection_nodes

        def check(_user_key: str, _org_id: str, node_ids=(), scopes=(), **_: object) -> AccessCheck:
            seen = frozenset(i for i in node_ids if i in owner)
            inside = frozenset(
                i for i in seen
                if all(owner[i] in scope.app_ids or i in scope.records for scope in scopes or ())
            )
            return AccessCheck(node_ids=seen, node_ids_in_scope=inside)

        names = {row["id"]: row for rows in _BIG.values() for row in rows}

        def by_ids(collection: str, _field: str, ids: list[str], **_: object) -> list[dict[str, Any]]:
            if collection == "apps":
                return [{"id": i, "name": i} for i in ids if i in _BIG]
            return [{"id": i, "recordName": names[i]["name"],
                     "mimeType": "text/directory" if names[i]["nodeType"] == "folder" else "text/plain"}
                    for i in ids if i in names]

        graph.check_access.side_effect = check
        graph.get_nodes_by_field_in.side_effect = by_ids
        return graph

    @staticmethod
    def _state(graph: MagicMock) -> dict[str, Any]:
        state = _state(graph, apps=[], kb=["kb-big", "kb-other"])
        state["filters"] = within_connection(
            {"apps": [], "kb": ["kb-big", "kb-other"]},
            {"apps": [], "recordGroups": [], "records": ["shared-folder"]},
        )
        return state

    async def test_a_match_past_the_first_page_of_its_collection_is_found(self) -> None:
        graph = self._graph()

        _, text = await execute_list_files(self._state(graph), query="budget", limit=5)

        assert _ids(text) == {"shared-file"}
        assert "more items" not in text.lower() and "Stopped" not in text
        assert len(connector_pages(graph)) >= 2

    async def test_a_full_page_says_another_page_follows_and_it_holds_the_rest(self) -> None:
        graph = self._graph()
        state = self._state(graph)

        _, first = await execute_list_files(state, query="sh", limit=1)
        _, second = await execute_list_files(state, query="sh", limit=1, page=2)

        assert len(_ids(first)) == 1 and "page=2" in first
        assert _ids(first) | _ids(second) == {"shared-folder", "shared-file"}
        assert "page=3" not in second

    async def test_records_asked_by_type_are_read_from_inside_the_limit(self) -> None:
        graph = self._graph()

        _, text = await execute_list_files(
            self._state(graph), query="budget", source_ids=["kb-big"], node_types=["record"], limit=5,
        )

        assert _ids(text) == {"shared-file"}
        assert len(connector_pages(graph)) >= 2

    async def test_a_root_listing_names_what_the_limit_shares(self) -> None:
        _, text = await execute_list_files(self._state(self._graph()))

        assert "shared-folder" in text
        assert "kb-big" not in text and "kb-other" not in text and "big-00" not in text

    async def test_a_file_one_bound_names_inside_a_folder_another_names_is_listed(self) -> None:
        """A project's sources name the folder; the connection names one file in it."""
        state = self._state(self._graph())
        state["filters"] = {
            **within_connection(
                {"apps": [], "kb": ["kb-big", "kb-other"]},
                {"apps": [], "recordGroups": [], "records": ["shared-file"]},
            ),
            "projectApps": [], "projectRecordGroups": [], "projectRecords": ["shared-folder"],
        }

        _, text = await execute_list_files(state)

        assert "id=shared-file" in text
        assert "id=shared-folder" not in text and "folder-notes" not in text

    async def test_a_whole_app_bound_beside_a_folder_bound_lists_only_the_folder(self) -> None:
        state = self._state(self._graph())
        state["filters"] = {
            **state["filters"], "projectApps": ["kb-big", "kb-other"], "projectRecordGroups": [], "projectRecords": [],
        }

        _, text = await execute_list_files(state)

        assert "id=shared-folder" in text
        assert "kb-big" not in text and "kb-other" not in text


class TestListsInsideAKnowledgeBase:
    """A knowledge base is an app: its files are listed by naming it."""

    async def test_naming_a_kb_lists_its_files(self, graph: MagicMock) -> None:
        state = _state(graph, apps=["app-jira"], kb=["kb-hr"])

        _, text = await execute_list_files(state, source_ids=["kb-hr"], node_types=["record"])

        assert _ids(text) == {"hr-1", "hr-2"}

    async def test_a_source_outside_the_conversation_lends_its_name_to_nothing(self, graph: MagicMock) -> None:
        """A member names a KB he cannot read: the answer is the sources he can
        list, said so, never another source's files under that KB's name."""
        state = _state(graph, apps=["app-jira"], kb=["kb-hr"])

        _, text = await execute_list_files(state, source_ids=["kb-finance"], node_types=["record"])

        assert text.startswith("None of the named sources is available")
        assert _ids(text) == {"app-jira", "kb-hr"}

    async def test_a_large_kb_says_which_page_comes_next(self) -> None:
        graph = create_autospec(ArangoHTTPProvider, instance=True)
        stub_listing(graph, USER_KEY, _BIG)
        state = _state(graph, apps=[], kb=["kb-big"])

        _, text = await execute_list_files(state, source_ids=["kb-big"], node_types=["record"], limit=20)

        assert len(_ids(text)) == 20
        assert "(More items follow: call again with page=2.)" in text

    async def test_nothing_picked_lists_every_source_the_user_reads(self, graph: MagicMock) -> None:
        """The connector catalog leaves knowledge bases out, so a default chat
        with no sources picked lists by the user's own access instead."""
        state = {**_state(graph, apps=[], kb=[]), "has_knowledge": True}

        _, text = await execute_list_files(state)

        assert _ids(text) == set(_USER_VISIBLE)

    async def test_records_across_several_sources_name_the_sources(self, graph: MagicMock) -> None:
        state = _state(graph, apps=["app-jira"], kb=["kb-hr"])

        _, text = await execute_list_files(state, node_types=["record"])

        assert text.startswith("To list records or folders, name one source")
        assert _ids(text) == {"app-jira", "kb-hr"}


# A drive whose record group holds a selected folder; beside the folder sit a
# file outside the selection and a record selected alone, with a child. A
# knowledge base holds a folder with a file two levels down.
_SELECTED = {
    "app-drive": [
        node("rg-1", "budget group", "recordGroup"),
        node("sel-folder", "selected folder", "folder", parent_id="rg-1"),
        node("in-1", "budget inside one", parent_id="sel-folder", record_type="TICKET"),
        node("in-2", "budget inside two", parent_id="sel-folder", record_type="FILE"),
        node("out-1", "budget outside", parent_id="rg-1", record_type="TICKET"),
        node("exact-rec", "budget exact"),
        node("exact-child", "budget exact child", parent_id="exact-rec"),
    ],
    "app-jira": [node("jira-1", "budget ticket", record_type="TICKET")],
    "kb-docs": [
        node("kb-folder", "docs folder", "folder", origin="COLLECTION"),
        node("kb-sub", "docs sub", "folder", parent_id="kb-folder", origin="COLLECTION"),
        node("kb-file", "budget kb file", parent_id="kb-sub", origin="COLLECTION"),
    ],
}


class TestSearchesInsideASelection:
    """A selection below app level limits a name search to what it selects:
    the query, the record types and a named source are applied, and no row
    outside the selection or a bound is named."""

    @pytest.fixture(autouse=True)
    def _clear_memo(self) -> Iterator[None]:
        selection_scope._memo.clear()
        yield
        selection_scope._memo.clear()

    @staticmethod
    def _graph() -> MagicMock:
        graph = create_autospec(ArangoHTTPProvider, instance=True)
        stub_listing(graph, USER_KEY, _SELECTED)
        rows = {row["id"]: (app_id, row) for app_id, app_rows in _SELECTED.items() for row in app_rows}
        children: dict[str, list[str]] = {}
        for _, row in rows.values():
            if row.get("parentId"):
                children.setdefault(row["parentId"], []).append(row["id"])

        def selection_nodes(
            _org_id: str, *, group_ids=(), record_ids=(), exact_record_ids=(), **_: object,
        ) -> dict[str, Any]:
            found, todo = list(exact_record_ids), list(record_ids)
            while todo:
                node_id = todo.pop()
                found.append(node_id)
                todo.extend(children.get(node_id, []))
            return {
                "groups": [{"id": g, "connectorId": rows[g][0]} for g in group_ids],
                "records": [{"id": i, "vrid": "", "connectorId": rows[i][0]} for i in found],
            }

        def ancestors(node_id: str) -> list[str]:
            chain = [node_id]
            while node_id in rows and rows[node_id][1].get("parentId"):
                node_id = rows[node_id][1]["parentId"]
                chain.append(node_id)
            return chain

        def check(_user_key: str, _org_id: str, node_ids=(), scopes=(), **_: object) -> AccessCheck:
            seen = frozenset(i for i in node_ids if i in rows or i in _SELECTED)
            inside = frozenset(
                i for i in seen
                if all(
                    i in scope.records or rows.get(i, (i,))[0] in scope.app_ids
                    or any(a in scope.groups for a in ancestors(i))
                    for scope in scopes or ()
                )
            )
            return AccessCheck(node_ids=seen, node_ids_in_scope=inside)

        def node_access(*, node_id: str, **_: object) -> dict[str, Any] | None:
            return {"id": node_id, "name": rows[node_id][1]["name"], "nodeType": rows[node_id][1]["nodeType"]}

        def by_ids(collection: str, _field: str, ids: list[str], **_: object) -> list[dict[str, Any]]:
            if collection == "apps":
                return [{"id": i, "name": i} for i in ids if i in _SELECTED]
            return [{"id": i, "recordName": rows[i][1]["name"],
                     "mimeType": "text/directory" if rows[i][1]["nodeType"] == "folder" else "text/plain"}
                    for i in ids if i in rows]

        graph.get_selection_nodes.side_effect = selection_nodes
        graph.check_access.side_effect = check
        graph.get_knowledge_hub_node_access.side_effect = node_access
        graph.get_nodes_by_field_in.side_effect = by_ids
        return graph

    @staticmethod
    def _state(graph: MagicMock, **filters: list[str]) -> dict[str, Any]:
        state = _state(graph, apps=[], kb=[])
        state["filters"] = {
            "apps": [], "kb": [], "records": ["sel-folder"], "selectionApps": ["app-drive"], **filters,
        }
        return state

    async def test_a_query_searches_inside_the_selection(self) -> None:
        _, text = await execute_list_files(self._state(self._graph()), query="budget")

        assert _ids(text) == {"in-1", "in-2"}

    async def test_a_selected_record_group_is_searched_by_record_type_from_itself(self) -> None:
        """The QA-03 call: a record group selected and named, records of one type."""
        graph = self._graph()
        state = self._state(graph, records=[], recordGroups=["rg-1"])

        _, text = await execute_list_files(state, source_ids=["rg-1"], record_types=["TICKET"])

        page = connector_pages(graph)[0]
        assert (page["start_type"], page["flatten"], page["filters"]["record_types"]) == (
            "recordGroup", True, ["TICKET"],
        )
        assert page["app_id"] == "" and not page["filters"].get("connector_ids")
        assert _ids(text) == {"in-1", "out-1"}

    async def test_record_types_alone_start_from_the_only_selected_node(self) -> None:
        graph = self._graph()
        state = self._state(graph, records=[], recordGroups=["rg-1"])

        _, text = await execute_list_files(state, record_types=["TICKET"])

        assert connector_pages(graph)[0]["start_id"] == "rg-1"
        assert _ids(text) == {"in-1", "out-1"}

    async def test_a_query_starts_from_a_selected_kb_folder(self) -> None:
        graph = self._graph()
        state = self._state(graph, records=["kb-folder"], selectionApps=["kb-docs"])

        _, text = await execute_list_files(state, query="budget")

        assert {p["start_id"] for p in connector_pages(graph)} == {"kb-folder"}
        assert _ids(text) == {"kb-file"}

    async def test_a_query_across_several_selected_nodes_searches_them_all(self) -> None:
        graph = self._graph()
        state = self._state(graph, records=["sel-folder", "kb-folder"], selectionApps=["app-drive", "kb-docs"])

        _, text = await execute_list_files(state, query="budget")

        assert _ids(text) == {"in-1", "in-2", "kb-file"}

    async def test_record_types_across_several_selected_nodes_ask_for_one(self) -> None:
        graph = self._graph()
        state = self._state(graph, records=["sel-folder", "kb-folder"], selectionApps=["app-drive", "kb-docs"])

        ok, text = await execute_list_files(state, record_types=["TICKET"])

        assert not ok and "name one of their ids per call" in text
        assert connector_pages(graph) == []

    async def test_a_parent_group_and_a_sibling_outside_the_selection_are_not_named(self) -> None:
        _, text = await execute_list_files(self._state(self._graph()), query="budget")

        assert not _ids(text) & {"rg-1", "out-1", "exact-rec", "jira-1"}
        assert "budget group" not in text and "budget outside" not in text

    async def test_a_selected_folder_named_as_source_is_searched_from_itself(self) -> None:
        graph = self._graph()

        _, text = await execute_list_files(self._state(graph), query="budget", source_ids=["sel-folder"])

        assert _ids(text) == {"in-1", "in-2"}
        assert {p.get("start_id") for p in connector_pages(graph)} == {"sel-folder"}

    async def test_a_touched_app_named_as_source_is_a_connector_filter(self) -> None:
        graph = self._graph()

        _, text = await execute_list_files(self._state(graph), query="budget", source_ids=["app-drive"])

        assert _ids(text) == {"in-1", "in-2"}
        graph.get_knowledge_hub_node_access.assert_not_awaited()

    async def test_a_source_outside_the_selection_is_refused_before_any_page(self) -> None:
        graph = self._graph()

        ok, text = await execute_list_files(self._state(graph), query="budget", source_ids=["out-1"])

        assert not ok and text == SELECTION_OUTSIDE_MESSAGE
        assert connector_pages(graph) == []

    async def test_a_record_selected_alone_has_nothing_to_search_under(self) -> None:
        graph = self._graph()
        state = self._state(graph, records=[], recordsExact=["exact-rec"])

        ok, text = await execute_list_files(state, query="budget", source_ids=["exact-rec"])

        assert not ok and text == SELECTION_ALONE_MESSAGE
        assert connector_pages(graph) == []

    async def test_a_record_selected_alone_is_found_but_never_its_children(self) -> None:
        state = self._state(self._graph(), records=[], recordsExact=["exact-rec"])

        _, text = await execute_list_files(state, query="budget")

        assert _ids(text) == {"exact-rec"}

    async def test_several_sources_ask_for_one(self) -> None:
        graph = self._graph()

        ok, text = await execute_list_files(
            self._state(graph), query="budget", source_ids=["sel-folder", "app-drive"],
        )

        assert not ok and "name one of their ids per call" in text
        assert connector_pages(graph) == []

    async def test_without_a_query_the_selection_is_listed(self) -> None:
        graph = self._graph()

        _, text = await execute_list_files(self._state(graph))

        assert text.startswith("This conversation is limited to these items")
        assert "id=sel-folder" in text and connector_pages(graph) == []

    async def test_a_cursor_pages_the_selection_whatever_the_query(self) -> None:
        graph = self._graph()

        _, text = await execute_list_files(self._state(graph), query="budget", cursor="MA")

        assert "id=sel-folder" in text and connector_pages(graph) == []

    @pytest.mark.parametrize("bound", [
        {"projectApps": [], "projectRecordGroups": [], "projectRecords": ["in-1"]},
        {"connectionApps": [], "connectionRecordGroups": [], "connectionRecords": ["in-1"]},
    ])
    async def test_a_bound_drops_a_selected_row_outside_it(self, bound: dict[str, list[str]]) -> None:
        _, text = await execute_list_files(self._state(self._graph(), **bound), query="budget")

        assert _ids(text) == {"in-1"}

    async def test_a_turn_attachment_in_kb_does_not_hide_the_selection(self) -> None:
        state = self._state(self._graph(), kb=["attach-vr-1"])
        state["attachment_record_ids"] = ["attach-vr-1"]

        _, text = await execute_list_files(state, query="budget")

        assert _ids(text) == {"in-1", "in-2"}

    async def test_a_second_page_is_filled_from_inside_the_selection(self) -> None:
        graph = self._graph()
        state = self._state(graph)

        _, first = await execute_list_files(state, query="budget", limit=1)
        _, second = await execute_list_files(state, query="budget", limit=1, page=2)

        assert "page=2" in first
        assert _ids(first) | _ids(second) == {"in-1", "in-2"}
