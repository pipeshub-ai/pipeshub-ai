"""The agent's knowledge tools under a selection below app level.

A content search carries the selection to the retrieval service; the tools
that work per app see the apps it touches; browsing is allowed only inside
it; and the file listing names what the user selected.
"""

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.modules.retrieval.selection_scope as selection_scope
from app.agents.actions.knowledge_graph.ops.fetch import _ids_outside_scope
from app.agents.actions.knowledge_graph.ops.scope import (
    SELECTION_ALONE_MESSAGE,
    SELECTION_BROWSE_HINT,
    SELECTION_OUTSIDE_MESSAGE,
    apps_usable_whole,
    derive_scope,
    ids_within_scope,
    list_selected_nodes,
    search_scope,
    selection_browse_refusal,
)
from app.services.graph_db.interface.graph_db_provider import AccessCheck

if TYPE_CHECKING:
    from collections.abc import Iterator

SELECTION = {
    "apps": ["app-whole"], "kb": [], "records": ["folder-1"],
    "selectionApps": ["app-touched"], "allowedApps": ["app-whole", "app-touched"],
}


@pytest.fixture(autouse=True)
def _clear_memo() -> "Iterator[None]":
    selection_scope._memo.clear()
    yield
    selection_scope._memo.clear()


class TestSearchScope:
    def test_a_selection_is_carried_to_the_retrieval_service(self) -> None:
        scope = search_scope({"filters": SELECTION})
        assert scope.to_filter_groups() == {
            "apps": ["app-whole"], "kb": [], "records": ["folder-1"],
            "allowedApps": ["app-whole", "app-touched"], "selectionApps": ["app-touched"],
        }

    def test_the_universal_agent_searches_its_selection_not_its_sources(self) -> None:
        state = {"is_placeholder_agent": True, "apps": ["every-app"], "kb": ["every-kb"], "filters": SELECTION}
        assert search_scope(state).app_ids == ("app-whole",)

    def test_without_a_selection_the_universal_agent_searches_its_sources(self) -> None:
        state = {"is_placeholder_agent": True, "apps": ["a"], "kb": ["k"], "filters": {"apps": [], "kb": []}}
        scope = search_scope(state)
        assert (scope.app_ids, scope.kb_ids) == (("a",), ("k",))
        assert scope.to_filter_groups() == {"apps": ["a"], "kb": ["k"]}

    def test_a_selection_is_not_narrowed_by_app(self) -> None:
        """Its nodes are not known per app, so the model's narrowing is ignored."""
        scope = search_scope({"filters": SELECTION})
        assert scope.narrow_to(["app-whole"]) is scope

    def test_an_app_only_scope_still_narrows_and_keeps_the_allow_list(self) -> None:
        scope = search_scope({"filters": {"apps": ["a", "b"], "kb": [], "allowedApps": ["a", "b"]}})
        narrowed = scope.narrow_to(["a"])
        assert narrowed.to_filter_groups() == {"apps": ["a"], "kb": [], "allowedApps": ["a", "b"]}

    def test_a_selection_alone_is_not_an_empty_scope(self) -> None:
        assert not search_scope({"filters": {"apps": [], "kb": [], "recordsExact": ["r1"]}}).is_empty()


class TestPerAppScope:
    def test_tools_that_work_per_app_see_the_apps_the_selection_touches(self) -> None:
        state = {"apps": ["every-app"], "kb": ["every-kb"], "filters": SELECTION}
        scope = derive_scope(state)
        assert scope.app_ids == ("app-whole", "app-touched") and scope.kb_ids == ()
        assert not scope.has_selection

    def test_without_a_selection_nothing_changes(self) -> None:
        scope = derive_scope({"apps": ["a"], "kb": ["k"], "filters": {"apps": ["x"], "kb": []}})
        assert (scope.app_ids, scope.kb_ids) == (("a",), ("k",))


def _graph(*, readable=(), inside=(), nodes=None) -> MagicMock:
    graph = MagicMock()
    graph.get_user_by_user_id = AsyncMock(return_value={"_key": "user-key"})
    graph.check_access = AsyncMock(return_value=AccessCheck(
        node_ids=frozenset(readable), node_ids_in_scope=frozenset(inside),
    ))
    graph.get_selection_nodes = AsyncMock(return_value={"groups": [], "records": []})
    graph.get_nodes_by_field_in = AsyncMock(side_effect=lambda collection, field, ids, **_: [
        row for row in (nodes or {}).get(collection, []) if row["id"] in ids
    ])
    return graph


class TestBrowsing:
    @pytest.mark.asyncio
    async def test_without_a_selection_browsing_is_not_gated(self) -> None:
        graph = _graph()
        assert await selection_browse_refusal({"filters": {"apps": ["a"]}}, graph, "u", "o", None) is None
        graph.check_access.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_without_a_selection_a_node_outside_the_picked_apps_is_refused(self) -> None:
        graph = _graph(readable={"in-b"}, inside=set())
        state = {"filters": {
            "apps": ["a"], "kb": [], "allowedApps": ["a", "b"], "allowedRecordGroups": [], "allowedRecords": [],
        }}
        assert await selection_browse_refusal(state, graph, "u", "o", "in-b") == SELECTION_OUTSIDE_MESSAGE

    @pytest.mark.asyncio
    async def test_without_a_selection_a_node_inside_the_picked_apps_may_be_browsed(self) -> None:
        graph = _graph(readable={"in-a"}, inside={"in-a"})
        assert await selection_browse_refusal({"filters": {"apps": ["a"], "kb": []}}, graph, "u", "o", "in-a") is None

    @pytest.mark.asyncio
    async def test_without_a_selection_or_a_pick_browsing_is_not_gated(self) -> None:
        graph = _graph()
        assert await selection_browse_refusal({"filters": {"apps": [], "kb": []}}, graph, "u", "o", "x") is None
        graph.check_access.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_selection_has_no_root_to_browse(self) -> None:
        assert await selection_browse_refusal({"filters": SELECTION}, _graph(), "u", "o", None) == SELECTION_BROWSE_HINT

    @pytest.mark.asyncio
    async def test_a_node_inside_the_selection_may_be_browsed(self) -> None:
        graph = _graph(readable={"folder-1", "sub"}, inside={"sub"})
        assert await selection_browse_refusal({"filters": SELECTION}, graph, "u", "o", "sub") is None
        scopes = graph.check_access.await_args.kwargs["scopes"]
        assert len(scopes) == 2    # the selection and the allow-list

    @pytest.mark.asyncio
    async def test_a_readable_node_outside_the_selection_is_refused(self) -> None:
        graph = _graph(readable={"folder-1", "elsewhere"}, inside=set())
        refusal = await selection_browse_refusal({"filters": SELECTION}, graph, "u", "o", "elsewhere")
        assert refusal == SELECTION_OUTSIDE_MESSAGE


class TestBrowsingARecordSelectedAlone:
    """`recordsExact` selects one record without what is under it."""

    @pytest.mark.asyncio
    async def test_it_has_nothing_to_browse_into(self) -> None:
        graph = _graph(readable={"issue"}, inside={"issue"})
        state = {"filters": {"apps": [], "kb": [], "recordsExact": ["issue"]}}
        assert await selection_browse_refusal(state, graph, "u", "o", "issue") == SELECTION_ALONE_MESSAGE

    @pytest.mark.asyncio
    async def test_it_may_be_browsed_when_a_selected_folder_covers_it_too(self) -> None:
        graph = _graph(readable={"issue"}, inside={"issue"})
        state = {"filters": {"apps": [], "kb": [], "records": ["folder"], "recordsExact": ["issue"]}}
        assert await selection_browse_refusal(state, graph, "u", "o", "issue") is None


class TestListingTheSelection:
    @pytest.mark.asyncio
    async def test_without_a_selection_the_normal_listing_runs(self) -> None:
        graph = _graph()
        assert await list_selected_nodes({"filters": {"apps": ["a"], "kb": []}}, graph, "u", "o") is None
        graph.get_user_by_user_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_each_readable_selected_node_is_named_with_its_kind(self) -> None:
        graph = _graph(
            readable={"app-1", "g1", "folder-1", "file-1"},
            nodes={
                "apps": [{"id": "app-1", "name": "Drive"}],
                "recordGroups": [{"id": "g1", "groupName": "Engineering"}],
                "records": [
                    {"id": "folder-1", "recordName": "Specs", "mimeType": "text/directory"},
                    {"id": "file-1", "recordName": "notes.md", "mimeType": "text/markdown"},
                ],
            },
        )
        state = {"filters": {
            "apps": ["app-1"], "kb": ["NO_KB_SELECTED"], "recordGroups": ["g1", "g-denied"],
            "records": ["folder-1"], "recordsExact": ["file-1"],
        }}
        text = await list_selected_nodes(state, graph, "u", "o")
        assert "- Drive [app] id=app-1" in text
        assert "- Engineering [record group] id=g1" in text
        assert "- Specs [folder] id=folder-1" in text
        assert "- notes.md [record] id=file-1 (this item only, nothing under it)" in text
        assert "g-denied" not in text and "NO_KB_SELECTED" not in text

    @pytest.mark.asyncio
    async def test_a_selected_node_outside_an_agents_limit_is_not_offered(self) -> None:
        graph = _graph(
            readable={"folder-a", "folder-b"}, inside={"folder-a"},
            nodes={"records": [
                {"id": "folder-a", "recordName": "A", "mimeType": "text/directory"},
                {"id": "folder-b", "recordName": "B", "mimeType": "text/directory"},
            ]},
        )
        state = {"filters": {
            "apps": [], "kb": [], "records": ["folder-a", "folder-b"],
            "allowedApps": [], "allowedRecordGroups": [], "allowedRecords": ["folder-a"],
        }}
        text = await list_selected_nodes(state, graph, "u", "o")
        assert "- A [folder] id=folder-a" in text and "folder-b" not in text
        assert text.startswith("This conversation is limited to these items:")

    @pytest.mark.asyncio
    async def test_a_selection_with_nothing_readable_says_so(self) -> None:
        text = await list_selected_nodes({"filters": {"records": ["gone"]}}, _graph(), "u", "o")
        assert text == "Nothing this conversation is limited to is available."


BOUNDED = {
    "apps": [], "kb": [], "records": ["beta-root"], "selectionApps": ["beta"],
    "allowedApps": ["whole-app"], "allowedRecordGroups": [], "allowedRecords": ["beta-root"],
}


class TestToolsThatWorkOnAWholeApp:
    """The entity tools run per app: never on an app a bound limits to some nodes."""

    def test_an_app_a_bound_limits_is_left_out(self) -> None:
        filters = {**BOUNDED, "apps": ["whole-app", "beta"]}
        assert apps_usable_whole({"filters": filters}, ["beta", "whole-app"]) == ["whole-app"]

    def test_an_app_must_be_whole_in_every_bound(self) -> None:
        filters = {**BOUNDED, "apps": ["whole-app", "beta"], "projectApps": ["beta"], "projectRecords": []}
        assert apps_usable_whole({"filters": filters}, ["beta", "whole-app"]) == []

    def test_an_app_a_selection_only_touches_is_left_out(self) -> None:
        state = {"filters": {"apps": ["a"], "kb": ["k"], "records": ["r"], "selectionApps": ["b"]}}
        assert apps_usable_whole(state, ["a", "k", "b"]) == ["a", "k"]

    def test_without_a_selection_or_a_bound_every_app_is_kept(self) -> None:
        assert apps_usable_whole({"filters": {"apps": ["a"], "kb": ["k"]}}, ["a", "k", "x"]) == ["a", "k", "x"]


class TestReachingARecordByNameOrId:
    """Find-by-name and open-by-id are held to a saved agent's and a project's sources."""

    @pytest.mark.asyncio
    async def test_a_turn_that_picked_nothing_is_not_checked(self) -> None:
        graph = _graph()
        state = {"filters": {"apps": [], "kb": ["NO_KB_SELECTED"]}}
        assert await ids_within_scope(state, graph, "u", "o", ["x", "y"]) == {"x", "y"}
        graph.check_access.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_picked_apps_hold_them_as_they_hold_search(self) -> None:
        """A project's apps narrowed by the picker: an id in another app is not read."""
        graph = _graph(readable={"in-a", "in-b"}, inside={"in-a"})
        state = {"filters": {"apps": ["a"], "kb": ["k", "NO_KB_SELECTED"], "strictScope": True}}
        assert await ids_within_scope(state, graph, "u", "o", ["in-a", "in-b"]) == {"in-a"}
        assert [s.app_ids for s in graph.check_access.await_args.kwargs["scopes"]] == [frozenset({"a", "k"})]

    @pytest.mark.asyncio
    async def test_a_strict_turn_that_picked_nothing_admits_nothing(self) -> None:
        graph = _graph(readable={"x"}, inside=set())
        state = {"filters": {"apps": [], "kb": [], "strictScope": True}}
        assert await ids_within_scope(state, graph, "u", "o", ["x"]) == set()
        assert [s.app_ids for s in graph.check_access.await_args.kwargs["scopes"]] == [frozenset()]

    @pytest.mark.asyncio
    async def test_the_picked_apps_and_an_agent_bound_hold_them_together(self) -> None:
        graph = _graph(readable={"in-a"}, inside={"in-a"})
        state = {"filters": {
            "apps": ["a"], "kb": [], "allowedApps": ["a", "b"], "allowedRecordGroups": [], "allowedRecords": [],
        }}
        assert await ids_within_scope(state, graph, "u", "o", ["in-a"]) == {"in-a"}
        assert [s.app_ids for s in graph.check_access.await_args.kwargs["scopes"]] == [
            frozenset({"a", "b"}), frozenset({"a"}),
        ]

    @pytest.mark.asyncio
    async def test_the_users_own_selection_holds_them(self) -> None:
        graph = _graph(readable={"a1", "b1"}, inside={"a1"})
        state = {"filters": {"apps": [], "kb": [], "records": ["folder-a"]}}
        assert await ids_within_scope(state, graph, "u", "o", ["a1", "b1"]) == {"a1"}
        assert len(graph.check_access.await_args.kwargs["scopes"]) == 1

    @pytest.mark.asyncio
    async def test_a_selection_and_the_bounds_hold_them_together(self) -> None:
        graph = _graph(readable={"beta-root", "dup"}, inside={"beta-root"})
        kept = await ids_within_scope({"filters": BOUNDED}, graph, "u", "o", ["beta-root", "dup"])
        assert kept == {"beta-root"}
        assert len(graph.check_access.await_args.kwargs["scopes"]) == 2

    @pytest.mark.asyncio
    async def test_a_file_attached_to_the_conversation_is_always_readable(self) -> None:
        upload = {"id": "upload-1", "orgId": "o", "origin": "UPLOAD", "connectorId": "attachments_o"}
        graph = _graph(readable={"upload-1"}, inside=set(), nodes={"records": [upload]})
        state = {
            "filters": {"apps": [], "kb": [], "records": ["folder-a"]},
            "attachments": [{"recordId": "upload-1", "virtualRecordId": "v-up"}],
        }
        assert await ids_within_scope(state, graph, "u", "o", ["upload-1", "b1"]) == {"upload-1"}

    @pytest.mark.asyncio
    async def test_an_attachment_naming_a_record_outside_the_bound_is_refused(self) -> None:
        """The client names an attachment's recordId; a connector record it
        names is not an attachment and stays outside an agent's folder bound."""
        other = {"id": "secret", "orgId": "o", "origin": "CONNECTOR", "connectorId": "drive-1"}
        graph = _graph(readable={"secret"}, inside=set(), nodes={"records": [other]})
        state = {"filters": BOUNDED, "attachments": [{"recordId": "secret", "virtualRecordId": "v-up"}]}
        assert await ids_within_scope(state, graph, "u", "o", ["secret"]) == set()

    @pytest.mark.asyncio
    async def test_another_orgs_chat_attachment_is_not_an_attachment_here(self) -> None:
        theirs = {"id": "up-2", "orgId": "o2", "origin": "UPLOAD", "connectorId": "attachments_o2"}
        graph = _graph(readable=set(), inside=set(), nodes={"records": [theirs]})
        state = {"filters": BOUNDED, "attachments": [{"recordId": "up-2", "virtualRecordId": "v"}]}
        assert await ids_within_scope(state, graph, "u", "o", ["up-2"]) == set()

    @pytest.mark.asyncio
    async def test_a_bound_that_lists_nothing_admits_nothing(self) -> None:
        graph = _graph(readable={"dup"}, inside=set())
        state = {"filters": {"records": ["r"], "projectApps": [], "projectRecordGroups": [], "projectRecords": []}}
        assert await ids_within_scope(state, graph, "u", "o", ["dup"]) == set()
        assert len(graph.check_access.await_args.kwargs["scopes"]) == 2    # the selection and the empty bound


class TestOpeningARecordById:
    """`fetch_record` answers for an id outside a bound as for one that does not exist."""

    @staticmethod
    def _context(filters: dict, graph: MagicMock) -> MagicMock:
        context = MagicMock()
        context.tool_state = {"filters": filters}
        context.graph_provider = graph
        context.user_id, context.org_id = "u", "o"
        return context

    @pytest.mark.asyncio
    async def test_a_turn_that_picked_nothing_asks_the_graph_nothing(self) -> None:
        graph = _graph()
        assert await _ids_outside_scope(self._context({"apps": [], "kb": []}, graph), ["x"]) == []
        graph.get_user_by_user_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_record_outside_the_picked_apps_is_refused(self) -> None:
        graph = _graph(readable={"in-a", "in-b"}, inside={"in-a"})
        context = self._context({"apps": ["a"], "kb": []}, graph)
        assert await _ids_outside_scope(context, ["in-a", "in-b"]) == ["in-b"]

    @pytest.mark.asyncio
    async def test_a_whole_app_agent_refuses_a_record_of_another_app(self) -> None:
        """A saved agent on whole apps is bound even when the turn selects nothing."""
        graph = _graph(readable={"in-a", "in-b"}, inside={"in-a"})
        context = self._context(
            {"apps": ["a"], "kb": [], "allowedApps": ["a"], "allowedRecordGroups": [], "allowedRecords": []},
            graph,
        )
        assert await _ids_outside_scope(context, ["in-a", "in-b"]) == ["in-b"]
        assert [s.app_ids for s in graph.check_access.await_args.kwargs["scopes"]] == [
            frozenset({"a"}), frozenset({"a"}),
        ]

    @pytest.mark.asyncio
    async def test_an_id_outside_the_users_selection_is_reported(self) -> None:
        graph = _graph(readable={"a1", "b1"}, inside={"a1"})
        context = self._context({"apps": [], "kb": [], "records": ["folder-a"]}, graph)
        assert await _ids_outside_scope(context, ["a1", "b1"]) == ["b1"]

    @pytest.mark.asyncio
    async def test_an_id_outside_the_bound_is_reported(self) -> None:
        graph = _graph(readable={"beta-root", "dup"}, inside={"beta-root"})
        outside = await _ids_outside_scope(self._context(BOUNDED, graph), ["beta-root", "dup"])
        assert outside == ["dup"]

    @pytest.mark.asyncio
    async def test_an_unknown_user_reads_nothing_under_a_bound(self) -> None:
        graph = _graph()
        graph.get_user_by_user_id = AsyncMock(return_value=None)
        assert await _ids_outside_scope(self._context(BOUNDED, graph), ["beta-root"]) == ["beta-root"]
