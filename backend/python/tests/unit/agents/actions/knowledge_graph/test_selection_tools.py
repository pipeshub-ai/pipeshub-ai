"""The agent's knowledge tools under a selection below app level.

A content search carries the selection to the retrieval service; the tools
that work per app see the apps it touches; browsing is allowed only inside
it; and the file listing names what the user selected.
"""

import logging
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

SELECTION = {
    "apps": ["app-whole"], "kb": [], "records": ["folder-1"],
    "selectionApps": ["app-touched"], "allowedApps": ["app-whole", "app-touched"],
}


@pytest.fixture(autouse=True)
def _clear_memo():
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


class TestABoundBelowAppLevelWithoutASelection:
    """R1-20: a project limited to folder QA-A, and a turn that picks its whole
    collection. Without a selection the root listing ran over the collection and
    named every record in it."""

    PROJECT_FOLDER = {
        "apps": [], "kb": ["kb-main"],
        "projectApps": [], "projectRecordGroups": [], "projectRecords": ["qa-a"],
    }
    WHOLE_APP_AGENT = {
        "apps": ["a"], "kb": [], "allowedApps": ["a"], "allowedRecordGroups": [], "allowedRecords": [],
    }

    @pytest.mark.asyncio
    async def test_navigate_has_no_root_to_list(self) -> None:
        refusal = await selection_browse_refusal({"filters": self.PROJECT_FOLDER}, _graph(), "u", "o", None)
        assert refusal == SELECTION_BROWSE_HINT

    @pytest.mark.asyncio
    async def test_list_files_names_the_bounds_nodes_not_the_collection(self) -> None:
        from app.agents.actions.knowledge_graph.ops.listing import execute_list_files

        graph = _graph(
            readable={"qa-a"}, inside={"qa-a"},
            nodes={"records": [{"id": "qa-a", "recordName": "QA-A", "mimeType": "text/directory"}]},
        )
        state = {"filters": self.PROJECT_FOLDER, "graph_provider": graph, "org_id": "o", "user_id": "u"}

        ok, text = await execute_list_files(state, query="budget")

        assert ok is True
        assert text.startswith("This conversation is limited to these items:")
        assert "- QA-A [folder] id=qa-a" in text
        assert "kb-main" not in text

    @pytest.mark.asyncio
    async def test_a_bounds_node_the_turn_does_not_pick_is_not_listed(self) -> None:
        """An agent whose knowledge is Jira whole and folder proj-a of a collection;
        the turn picks only Jira."""
        graph = _ScopedGraph({"jira": "jira", "proj-a": "kb-1"}, readable={"jira", "proj-a"})
        graph.get_selection_nodes = AsyncMock(return_value={
            "groups": [{"id": "proj-a", "connectorId": "kb-1"}], "records": [],
        })
        graph.get_nodes_by_field_in = AsyncMock(side_effect=lambda collection, field, ids, **_: [
            row for row in {
                "apps": [{"id": "jira", "name": "Jira"}],
                "recordGroups": [{"id": "proj-a", "groupName": "Project A"}],
            }.get(collection, []) if row["id"] in ids
        ])
        filters = {
            "apps": ["jira"], "kb": [],
            "allowedApps": ["jira"], "allowedRecordGroups": ["proj-a"], "allowedRecords": [],
        }

        text = await list_selected_nodes({"filters": filters}, graph, "u", "o")

        assert "- Jira [app] id=jira" in text and "proj-a" not in text

    @pytest.mark.asyncio
    async def test_a_bound_of_whole_apps_keeps_the_normal_listing_and_root(self) -> None:
        graph = _graph()
        state = {"filters": self.WHOLE_APP_AGENT}
        assert await list_selected_nodes(state, graph, "u", "o") is None
        assert await selection_browse_refusal(state, graph, "u", "o", None) is None
        graph.check_access.assert_not_awaited()


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
    async def test_a_turn_of_whole_apps_holds_them_to_those_apps(self) -> None:
        """GS-02: a record of another connector used to be reachable by id."""
        graph = _ScopedGraph({"x": "a", "y": "b", "z": "k"}, readable={"x", "y", "z"})
        state = {"filters": {"apps": ["a"], "kb": ["k"]}}
        assert await ids_within_scope(state, graph, "u", "o", ["x", "y", "z"]) == {"x", "z"}

    @pytest.mark.asyncio
    async def test_a_turn_that_picks_no_app_is_not_checked(self) -> None:
        graph = _graph()
        state = {"filters": {"apps": [], "kb": []}}
        assert await ids_within_scope(state, graph, "u", "o", ["x", "y"]) == {"x", "y"}
        graph.check_access.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_attachment_sent_in_kb_is_not_a_pick(self) -> None:
        graph = _graph()
        state = {
            "filters": {"apps": [], "kb": ["v-up"]},
            "attachments": [{"recordId": "up", "virtualRecordId": "v-up"}],
        }
        assert await ids_within_scope(state, graph, "u", "o", ["x"]) == {"x"}
        graph.check_access.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_file_attached_in_an_earlier_turn_stays_readable(self) -> None:
        graph = _ScopedGraph({"up": "attachments_o", "y": "b"}, readable={"up", "y"})
        state = {
            "filters": {"apps": ["a"], "kb": []},
            "previous_conversations": [{"role": "user", "attachments": [{"recordId": "up", "virtualRecordId": "v"}]}],
        }
        assert await ids_within_scope(state, graph, "u", "o", ["up", "y"]) == {"up"}

    @pytest.mark.asyncio
    async def test_navigate_holds_a_turn_of_whole_apps_to_those_apps(self) -> None:
        graph = _ScopedGraph({"x": "a", "y": "b"}, readable={"x", "y"})
        state = {"filters": {"apps": ["a"], "kb": []}}
        assert await selection_browse_refusal(state, graph, "u", "o", "y") == SELECTION_OUTSIDE_MESSAGE
        assert await selection_browse_refusal(state, graph, "u", "o", "x") is None

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
        graph = _graph(readable={"upload-1"}, inside=set())
        state = {
            "filters": {"apps": [], "kb": [], "records": ["folder-a"]},
            "attachments": [{"recordId": "upload-1", "virtualRecordId": "v-up"}],
        }
        assert await ids_within_scope(state, graph, "u", "o", ["upload-1", "b1"]) == {"upload-1"}

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
    async def test_an_id_outside_the_apps_picked_whole_is_reported(self) -> None:
        """GS-02 / F8: fetch_record used to read any connector's record."""
        graph = _ScopedGraph({"x": "a", "y": "b"}, readable={"x", "y"})
        assert await _ids_outside_scope(self._context({"apps": ["a"], "kb": []}, graph), ["x", "y"]) == ["y"]

    @pytest.mark.asyncio
    async def test_a_turn_that_picks_no_app_asks_the_graph_nothing(self) -> None:
        graph = _graph()
        assert await _ids_outside_scope(self._context({"apps": [], "kb": []}, graph), ["x"]) == []
        graph.get_user_by_user_id.assert_not_awaited()

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


class _ScopedGraph:
    """Records as ``id -> connectorId``; ``check_access`` applies the scopes it
    is given as the real one does."""

    def __init__(self, rows: dict[str, str], readable: set[str]) -> None:
        self.rows, self.readable = rows, readable
        self.get_user_by_user_id = AsyncMock(return_value={"_key": "user-key"})
        self.get_selection_nodes = AsyncMock(return_value={"groups": [], "records": []})
        self.check_access = AsyncMock(side_effect=self._check_access)

    async def _check_access(self, _user_key, _org, *, node_ids=(), scopes=(), **_) -> AccessCheck:
        readable = [i for i in node_ids if i in self.readable]
        return AccessCheck(
            node_ids=frozenset(readable),
            node_ids_in_scope=frozenset(
                i for i in readable
                if all(s.admits({"id": i, "connectorId": self.rows.get(i), "groupIds": []}) for s in scopes)
            ),
        )


class TestTheNeo4jQaServiceAccountRepro:
    """GS-01 / GL-01 path 2 exactly as the Neo4j QA built it (n4mig ids,
    `neo4j/qa/misc/gs/out/svc/svc.sa.jsonl`): a service-account agent whose
    knowledge is one RSS app runs as an admin who reads everything; a caller asks
    for a Jira-security ticket and a Confluence page. The probe passed the turn
    filters the route used to build, `{"apps": [RSS], "kb": ["NO_KB_SELECTED"]}`;
    the route now also adds the agent's bound."""

    RSS, JIRA, CONFLUENCE = "3fd5f604-f557-480a-b262-49e22ec178e9", "86ea9fa4-0fd5-44de-8f46-707d1b4b7086", "74e373ab"
    PT67, PLAN, ARTICLE = "032506af-3ec5-4085-a47f-cf1888790edb", "001cd964", "f835ac74"

    def _graph(self) -> _ScopedGraph:
        rows = {self.PT67: self.JIRA, self.PLAN: self.CONFLUENCE, self.ARTICLE: self.RSS}
        return _ScopedGraph(rows, readable=set(rows))

    async def _turns(self, graph: _ScopedGraph) -> list[dict]:
        from app.api.routes.agent import _resolve_turn_filters

        routed = await _resolve_turn_filters(
            agent_id="svc-agent", agent_knowledge=[{"connectorId": self.RSS, "type": "RSS"}],
            requested_filters=None, graph_provider=graph, caller_user_id="test", org_id="o",
            logger=logging.getLogger("t"), retrieval_user_id="admin",
        )
        return [{"apps": [self.RSS], "kb": ["NO_KB_SELECTED"]}, routed]

    @pytest.mark.asyncio
    async def test_lookup_fetch_and_navigate_reach_only_the_rss_app(self) -> None:
        graph = self._graph()
        for filters in await self._turns(graph):
            state = {"filters": filters}
            asked = [self.PT67, self.PLAN, self.ARTICLE]
            assert await ids_within_scope(state, graph, "admin-key", "o", asked) == {self.ARTICLE}, filters
            assert await _ids_outside_scope(TestOpeningARecordById._context(filters, graph), asked) == [
                self.PT67, self.PLAN,
            ], filters
            for node in (self.PT67, self.PLAN):
                assert await selection_browse_refusal(state, graph, "admin-key", "o", node) == SELECTION_OUTSIDE_MESSAGE


class TestAnAgentWithAWholeSource:
    """GS-01: a service-account agent runs its tools as its creator, so its
    knowledge must bound what a tool reaches by id even when no source is
    limited below app level.

    A turn that picks nothing (``{"apps": [], "kb": []}``) is the case only the
    agent's bound limits (R1-19): with no filters at all the agent's sources are
    picked whole, and apps picked whole bound the turn on their own."""

    TURNS = pytest.mark.parametrize("requested", [None, {"apps": [], "kb": []}], ids=["no-filters", "picks-nothing"])

    @staticmethod
    async def _turn(graph: _ScopedGraph, requested: dict | None) -> dict:
        from app.api.routes.agent import _resolve_turn_filters

        filters = await _resolve_turn_filters(
            agent_id="svc-agent",
            agent_knowledge=[{"connectorId": "kb-1", "type": "KB", "filters": ""}],
            requested_filters=requested, graph_provider=graph, caller_user_id="caller", org_id="o",
            logger=logging.getLogger("t"), retrieval_user_id="creator",
        )
        return {"filters": filters}

    @TURNS
    @pytest.mark.asyncio
    async def test_the_agents_knowledge_is_the_turns_bound(self, requested) -> None:
        graph = _ScopedGraph({}, readable=set())
        state = await self._turn(graph, requested)
        assert state["filters"]["allowedApps"] == ["kb-1"]

    @TURNS
    @pytest.mark.asyncio
    async def test_lookup_and_fetch_reach_nothing_outside_its_knowledge(self, requested) -> None:
        graph = _ScopedGraph({"kb-rec": "kb-1", "jira-1": "jira"}, readable={"kb-rec", "jira-1"})
        state = await self._turn(graph, requested)
        assert await ids_within_scope(state, graph, "user-key", "o", ["kb-rec", "jira-1"]) == {"kb-rec"}

    @TURNS
    @pytest.mark.asyncio
    async def test_navigate_reaches_nothing_outside_its_knowledge(self, requested) -> None:
        graph = _ScopedGraph({"kb-rec": "kb-1", "jira-1": "jira"}, readable={"kb-rec", "jira-1"})
        state = await self._turn(graph, requested)
        assert await selection_browse_refusal(state, graph, "user-key", "o", "jira-1") == SELECTION_OUTSIDE_MESSAGE
        assert await selection_browse_refusal(state, graph, "user-key", "o", "kb-rec") is None
        assert await selection_browse_refusal(state, graph, "user-key", "o", None) is None
