"""A selection below app level: what it resolves to, what it admits, and how
the retrieval service searches inside it.

The hierarchy walk itself (`get_selection_nodes`) is tested against real graphs
on both backends in `tests/integration/graph_permissions/`.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.modules.retrieval.retrieval_service as retrieval_mod
import app.modules.retrieval.selection_scope as mod
from app.exceptions.fastapi_responses import Status
from app.modules.retrieval.selection_scope import (
    SELECTION_TOO_LARGE_MESSAGE,
    SelectionNotReadyError,
    SelectionScope,
    SelectionTooLargeError,
    allowed_filters,
    has_selection,
    prepare_turn_scope,
    resolve_record_scopes,
    resolve_request_scopes,
    require_membership_ready,
    resolve_selection_scope,
    selection_app_ids,
    selection_error,
)
from app.services.graph_db.interface.graph_db_provider import AccessCheck, AccessibleContainers
from app.services.vector_db.const.const import CONNECTOR_IDS_FIELD, RECORD_GROUP_IDS_FIELD


@pytest.fixture(autouse=True)
def _clear_memo():
    mod._memo.clear()
    retrieval_mod._user_cache.clear()
    yield
    mod._memo.clear()
    retrieval_mod._user_cache.clear()


def _provider(*, readable=(), groups=(), records=(), apps=()) -> MagicMock:
    provider = MagicMock()
    provider.check_access = AsyncMock(return_value=AccessCheck(node_ids=frozenset(readable)))
    provider.get_selection_nodes = AsyncMock(
        return_value={"groups": list(groups), "records": list(records)}
    )
    provider.get_nodes_by_field_in = AsyncMock(return_value=list(apps))
    return provider


SCOPE = SelectionScope(
    app_ids=frozenset({"app-whole"}),
    groups={"g1": "app-g", "g1-nested": "app-g"},
    records={"r1": ("v1", "app-r"), "r2": ("v2", "app-r")},
)


class TestAdmits:
    def test_a_record_of_a_whole_app_is_inside(self) -> None:
        assert SCOPE.admits({"id": "x", "connectorId": "app-whole", "groupIds": []})

    def test_a_record_under_a_selected_folder_is_inside(self) -> None:
        assert SCOPE.admits({"id": "r2", "connectorId": "app-r", "groupIds": ["other"]})

    def test_a_record_of_a_nested_group_is_inside(self) -> None:
        assert SCOPE.admits({"id": "x", "connectorId": "app-g", "groupIds": ["g1-nested"]})

    def test_another_record_of_a_touched_app_is_outside(self) -> None:
        """Touching an app through a folder does not admit the rest of it."""
        assert not SCOPE.admits({"id": "x", "connectorId": "app-r", "groupIds": ["g9"]})

    def test_a_row_without_groups_is_judged_by_app_and_id_alone(self) -> None:
        assert not SCOPE.admits({"id": "x", "connectorId": "app-g"})

    def test_the_selected_nodes_themselves_are_inside(self) -> None:
        """Browsing asks about a node, not a record under one."""
        assert SCOPE.admits({"id": "g1", "connectorId": "app-g", "groupIds": []})
        assert SCOPE.admits({"id": "app-whole"})

    def test_a_folder_under_a_selected_folder_is_inside_but_holds_nothing_to_search(self) -> None:
        scope = SelectionScope(records={"folder": ("", "app"), "file": ("v1", "app")})
        assert scope.admits({"id": "folder", "connectorId": "app"})
        assert scope.should_clauses() == {"virtualRecordId": ["v1"]}

    def test_a_selection_of_folders_alone_has_nothing_to_search(self) -> None:
        assert SelectionScope(records={"folder": ("", "app")}).is_empty


class TestShouldClauses:
    def test_each_kind_of_selection_becomes_one_alternative(self) -> None:
        should = SCOPE.should_clauses()
        assert should[CONNECTOR_IDS_FIELD] == ["app-whole"]
        assert should[RECORD_GROUP_IDS_FIELD] == ["g1", "g1-nested"]
        assert should["virtualRecordId"] == ["v1", "v2"]

    def test_a_whole_app_the_user_does_not_reach_is_left_out(self) -> None:
        should = SCOPE.should_clauses(accessible_app_ids={"elsewhere"})
        assert CONNECTOR_IDS_FIELD not in should
        assert RECORD_GROUP_IDS_FIELD in should

    def test_every_app_the_selection_touches(self) -> None:
        assert SCOPE.connector_ids == frozenset({"app-whole", "app-g", "app-r"})


class TestFilterKeys:
    def test_app_only_filters_are_not_a_selection(self) -> None:
        assert not has_selection({"apps": ["a"], "kb": ["k"], "records": []})

    def test_any_sub_app_key_is_a_selection(self) -> None:
        assert has_selection({"recordsExact": ["r1"]})

    def test_a_bound_is_read_as_the_selection_keys_it_mirrors(self) -> None:
        assert allowed_filters({"allowedApps": ["a"], "allowedRecords": ["r"], "apps": ["x"]}) == [
            {"apps": ["a"], "recordGroups": [], "records": ["r"]},
        ]

    def test_an_agent_bound_and_a_project_bound_are_kept_apart(self) -> None:
        bounds = allowed_filters({"allowedApps": ["a"], "projectApps": ["a", "b"], "projectRecords": ["r"]})
        assert bounds == [
            {"apps": ["a"], "recordGroups": [], "records": []},
            {"apps": ["a", "b"], "recordGroups": [], "records": ["r"]},
        ]

    def test_a_bound_that_lists_nothing_is_still_a_bound(self) -> None:
        assert allowed_filters({"projectApps": []}) == [{"apps": [], "recordGroups": [], "records": []}]
        assert allowed_filters({"apps": ["a"], "records": ["r"]}) == []


class TestResolve:
    @pytest.mark.asyncio
    async def test_app_only_filters_resolve_to_nothing_and_ask_the_graph_nothing(self) -> None:
        provider = _provider()
        assert await resolve_selection_scope(provider, "u", "o", {"apps": ["a"]}) is None
        provider.check_access.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_selected_node_the_user_cannot_read_is_left_out(self) -> None:
        provider = _provider(readable={"g-ok", "r-ok"}, groups=[{"id": "g-ok", "connectorId": "a"}])
        await resolve_selection_scope(
            provider, "u", "o",
            {"recordGroups": ["g-ok", "g-denied"], "records": ["r-ok", "r-denied"], "recordsExact": ["x-denied"]},
        )
        asked = provider.get_selection_nodes.await_args.kwargs
        assert asked["group_ids"] == ["g-ok"]
        assert asked["record_ids"] == ["r-ok"]
        assert asked["exact_record_ids"] == []

    @pytest.mark.asyncio
    async def test_whole_apps_and_legacy_kb_are_one_set(self) -> None:
        provider = _provider(readable={"r1"}, records=[{"id": "r1", "vrid": "v1", "connectorId": "a3"}])
        scope = await resolve_selection_scope(
            provider, "u", "o", {"apps": ["a1"], "kb": ["a2"], "records": ["r1"]},
        )
        assert scope.app_ids == frozenset({"a1", "a2"})
        assert scope.connector_ids == frozenset({"a1", "a2", "a3"})

    @pytest.mark.asyncio
    async def test_nothing_readable_is_an_empty_scope_not_an_unscoped_one(self) -> None:
        scope = await resolve_selection_scope(_provider(), "u", "o", {"records": ["gone"]})
        assert scope is not None and scope.is_empty

    @pytest.mark.asyncio
    async def test_more_nodes_than_the_cap_is_refused(self, monkeypatch) -> None:
        monkeypatch.setenv("SEARCH_SELECTION_MAX_NODES", "2")
        provider = _provider(
            readable={"f"},
            records=[{"id": f"r{i}", "vrid": f"v{i}", "connectorId": "a"} for i in range(3)],
        )
        with pytest.raises(SelectionTooLargeError):
            await resolve_selection_scope(provider, "u", "o", {"records": ["f"]})
        assert provider.get_selection_nodes.await_args.kwargs["limit"] == 2

    @pytest.mark.asyncio
    async def test_exactly_the_cap_is_allowed(self, monkeypatch) -> None:
        monkeypatch.setenv("SEARCH_SELECTION_MAX_NODES", "2")
        provider = _provider(
            readable={"f"},
            records=[{"id": f"r{i}", "vrid": f"v{i}", "connectorId": "a"} for i in range(2)],
        )
        scope = await resolve_selection_scope(provider, "u", "o", {"records": ["f"]})
        assert len(scope.records) == 2

    @pytest.mark.asyncio
    async def test_the_same_selection_is_resolved_once_within_a_turn(self) -> None:
        provider = _provider(readable={"g"}, groups=[{"id": "g", "connectorId": "a"}])
        first = await resolve_selection_scope(provider, "u", "o", {"recordGroups": ["g"]})
        second = await resolve_selection_scope(provider, "u", "o", {"recordGroups": ["g"]})
        assert first is second
        assert provider.get_selection_nodes.await_count == 1

    @pytest.mark.asyncio
    async def test_another_user_does_not_share_the_memo(self) -> None:
        provider = _provider(readable={"g"}, groups=[{"id": "g", "connectorId": "a"}])
        await resolve_selection_scope(provider, "u1", "o", {"recordGroups": ["g"]})
        await resolve_selection_scope(provider, "u2", "o", {"recordGroups": ["g"]})
        assert provider.check_access.await_count == 2

    @pytest.mark.asyncio
    async def test_an_allow_list_is_taken_as_stored(self) -> None:
        provider = _provider(groups=[{"id": "g", "connectorId": "a"}])
        scope = await resolve_selection_scope(
            provider, "u", "o", {"recordGroups": ["g"]}, readable_only=False,
        )
        provider.check_access.assert_not_awaited()
        assert "g" in scope.groups


class TestTurnErrors:
    def test_the_users_own_selection_is_named_as_theirs(self) -> None:
        code, message = selection_error(SelectionTooLargeError())
        assert code == "selection_too_large" and "you selected" in message

    def test_a_saved_agents_own_limit_is_not_blamed_on_the_user(self) -> None:
        for exc, code in ((SelectionTooLargeError(), "selection_too_large"),
                          (SelectionNotReadyError(), "selection_not_ready")):
            got, message = selection_error(exc, agent_limit=True)
            assert got == code
            assert "This agent is limited" in message or "this agent is limited" in message
            assert "you selected" not in message


class TestBounds:
    """`resolve_request_scopes`: every scope a cited record must lie in."""

    @pytest.mark.asyncio
    async def test_a_selection_inside_an_agent_and_a_project_must_lie_in_all_three(self) -> None:
        provider = _provider(readable={"f1"}, records=[{"id": "r1", "vrid": "v1", "connectorId": "app-1"}])
        selection, scopes = await resolve_request_scopes(provider, "u", "o", {
            "records": ["f1"], "allowedApps": ["app-1"], "projectApps": ["app-2"],
        })
        assert selection is scopes[0] and len(scopes) == 3
        row = {"id": "r1", "connectorId": "app-1", "groupIds": []}
        assert scopes[0].admits(row) and scopes[1].admits(row)
        assert not scopes[2].admits(row)

    @pytest.mark.asyncio
    async def test_a_bound_that_lists_nothing_admits_nothing(self) -> None:
        provider = _provider(readable={"f1"}, records=[{"id": "r1", "vrid": "v1", "connectorId": "app-1"}])
        _, scopes = await resolve_request_scopes(provider, "u", "o", {
            "records": ["f1"], "projectApps": [], "projectRecordGroups": [], "projectRecords": [],
        })
        assert len(scopes) == 2
        assert not scopes[1].admits({"id": "r1", "connectorId": "app-1", "groupIds": []})

    @pytest.mark.asyncio
    async def test_a_bound_of_folders_admits_only_what_lies_under_them(self) -> None:
        provider = _provider(readable={"f1"})
        provider.get_selection_nodes = AsyncMock(side_effect=lambda org_id, *, record_ids, **_: {
            "groups": [],
            "records": [{"id": "inside", "vrid": "v1", "connectorId": "app-1"}] if "bound-folder" in record_ids
            else [{"id": "inside", "vrid": "v1", "connectorId": "app-1"}, {"id": "outside", "vrid": "v2", "connectorId": "app-1"}],
        })
        _, scopes = await resolve_request_scopes(provider, "u", "o", {
            "records": ["f1"], "allowedApps": [], "allowedRecords": ["bound-folder"],
        })
        bound = scopes[1]
        assert bound.admits({"id": "inside", "connectorId": "app-1", "groupIds": []})
        assert not bound.admits({"id": "outside", "connectorId": "app-1", "groupIds": []})


class TestRecordScopes:
    """`resolve_record_scopes`: what a record reached by id or by a graph walk must lie in."""

    @pytest.mark.asyncio
    async def test_a_turn_limited_by_nothing_has_no_scope_and_asks_the_graph_nothing(self) -> None:
        provider = _provider()
        assert await resolve_record_scopes(provider, "u", "o", {"apps": [], "kb": ["NO_KB_SELECTED"]}) is None
        provider.check_access.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_apps_picked_whole_are_a_scope(self) -> None:
        scopes = await resolve_record_scopes(_provider(), "u", "o", {"apps": ["a"], "kb": ["k"]})
        assert [s.app_ids for s in scopes] == [frozenset({"a", "k"})]
        assert scopes[0].admits({"id": "r", "connectorId": "k"})
        assert not scopes[0].admits({"id": "r", "connectorId": "b"})

    @pytest.mark.asyncio
    async def test_an_attachment_id_sent_as_a_collection_is_not_a_pick(self) -> None:
        assert await resolve_record_scopes(_provider(), "u", "o", {"apps": [], "kb": ["vrid"]}, ignore=["vrid"]) is None

    @pytest.mark.asyncio
    async def test_a_strict_turn_that_picks_nothing_admits_nothing(self) -> None:
        scopes = await resolve_record_scopes(_provider(), "u", "o", {"apps": [], "kb": [], "strictScope": True})
        assert scopes == [SelectionScope()] and not scopes[0].admits({"id": "r", "connectorId": "a"})

    @pytest.mark.asyncio
    async def test_a_bound_alone_is_the_scope(self) -> None:
        scopes = await resolve_record_scopes(
            _provider(), "u", "o", {"allowedApps": ["a"], "allowedRecordGroups": [], "allowedRecords": []},
        )
        assert [s.app_ids for s in scopes] == [frozenset({"a"})]


class TestMembershipReadiness:
    @pytest.mark.asyncio
    async def test_a_selection_of_records_only_needs_no_membership(self) -> None:
        provider = _provider()
        await require_membership_ready(provider, SelectionScope(records={"r": ("v", "a")}))
        provider.get_nodes_by_field_in.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_group_of_an_app_that_was_never_backfilled_is_refused(self) -> None:
        provider = _provider(apps=[{"id": "a", "vectorMembershipBackfilled": False}])
        with pytest.raises(SelectionNotReadyError):
            await require_membership_ready(provider, SelectionScope(groups={"g": "a"}))

    @pytest.mark.asyncio
    async def test_a_backfill_that_gave_up_is_refused(self) -> None:
        provider = _provider(apps=[{
            "id": "a", "vectorMembershipBackfilled": True, "vectorMembershipBackfillExhausted": True,
        }])
        with pytest.raises(SelectionNotReadyError):
            await require_membership_ready(provider, SelectionScope(app_ids=frozenset({"a"})))

    @pytest.mark.asyncio
    async def test_an_id_with_no_app_document_is_ignored(self) -> None:
        provider = _provider(apps=[{"id": "a", "vectorMembershipBackfilled": True}])
        await require_membership_ready(
            provider, SelectionScope(app_ids=frozenset({"a", "NO_KB_SELECTED"})),
        )


class TestTurnEntry:
    """`prepare_turn_scope`: what a chat turn's filters become before the model runs."""

    @pytest.mark.asyncio
    async def test_an_app_only_turn_asks_the_graph_nothing_and_keeps_the_agents_bound(self) -> None:
        provider = _provider()
        provider.get_user_by_user_id = AsyncMock()
        turn = await prepare_turn_scope(
            provider, "u", "o", {"apps": ["a"], "kb": []}, agent_sources={"apps": ["a"]},
        )
        assert turn == {
            "apps": ["a"], "kb": [], "allowedApps": ["a"], "allowedRecordGroups": [], "allowedRecords": [],
        }
        provider.get_user_by_user_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_projects_bound_holds_without_a_selection(self) -> None:
        """GS-03: a turn naming only a collection used to drop the project's bound."""
        provider = _provider()
        provider.get_user_by_user_id = AsyncMock(return_value={"_key": "user-key"})
        turn = await prepare_turn_scope(
            provider, "u", "o", {"apps": [], "kb": ["main"]},
            project_sources={"apps": [], "recordGroups": [], "records": ["qa-a"]},
        )
        assert (turn["projectApps"], turn["projectRecordGroups"], turn["projectRecords"]) == ([], [], ["qa-a"])
        assert "strictScope" not in turn and "selectionApps" not in turn

    @pytest.mark.asyncio
    async def test_a_bound_below_app_level_too_large_to_search_fails_the_turn(self, monkeypatch) -> None:
        """R1-23: resolved lazily inside each tool, the error surfaced as a failed
        fetch, a "not found" or a generic error instead of its reason."""
        monkeypatch.setenv("SEARCH_SELECTION_MAX_NODES", "1")
        provider = _provider(groups=[{"id": "g1", "connectorId": "a"}, {"id": "g1-sub", "connectorId": "a"}])
        provider.get_user_by_user_id = AsyncMock(return_value={"_key": "user-key"})
        with pytest.raises(SelectionTooLargeError) as raised:
            await prepare_turn_scope(
                provider, "u", "o", {"apps": ["a"], "kb": []},
                agent_sources={"apps": [], "recordGroups": ["g1"], "records": []},
            )
        assert str(raised.value) == SELECTION_TOO_LARGE_MESSAGE

    @pytest.mark.asyncio
    async def test_server_set_keys_from_a_client_never_survive(self) -> None:
        turn = await prepare_turn_scope(
            _provider(), "u", "o", {"apps": ["a"], "allowedApps": ["x"], "selectionApps": ["y"]},
        )
        assert turn == {"apps": ["a"]}

    @pytest.mark.asyncio
    async def test_a_selection_records_the_apps_it_touches_and_its_bounds(self) -> None:
        provider = _provider(
            readable={"f1"}, records=[{"id": "r1", "vrid": "v1", "connectorId": "app-2"}],
            apps=[{"id": "app-1", "vectorMembershipBackfilled": True}],
        )
        provider.get_user_by_user_id = AsyncMock(return_value={"_key": "user-key"})
        turn = await prepare_turn_scope(
            provider, "u", "o", {"apps": ["app-1"], "kb": [], "records": ["f1"]},
            agent_sources={"apps": ["app-2", "app-1", "app-1"], "records": ["f0"]},
            project_sources={"apps": [], "recordGroups": ["g9"]},
        )
        assert turn["selectionApps"] == ["app-2"]
        assert (turn["allowedApps"], turn["allowedRecordGroups"], turn["allowedRecords"]) == (
            ["app-1", "app-2"], [], ["f0"],
        )
        assert (turn["projectApps"], turn["projectRecordGroups"], turn["projectRecords"]) == ([], ["g9"], [])
        assert turn["strictScope"] is True
        assert turn["records"] == ["f1"] and turn["apps"] == ["app-1"]

    @pytest.mark.asyncio
    async def test_a_turn_outside_a_saved_agent_or_a_project_gets_no_bound(self) -> None:
        provider = _provider(readable={"f1"}, records=[{"id": "r1", "vrid": "v1", "connectorId": "a"}])
        provider.get_user_by_user_id = AsyncMock(return_value={"_key": "user-key"})
        turn = await prepare_turn_scope(provider, "u", "o", {"records": ["f1"]})
        assert not any(key.startswith(("allowed", "project")) for key in turn)

    @pytest.mark.asyncio
    async def test_a_selection_over_the_cap_fails_the_turn(self, monkeypatch) -> None:
        monkeypatch.setenv("SEARCH_SELECTION_MAX_NODES", "1")
        provider = _provider(readable={"f1"}, records=[
            {"id": "r1", "vrid": "v1", "connectorId": "a"}, {"id": "r2", "vrid": "v2", "connectorId": "a"},
        ])
        provider.get_user_by_user_id = AsyncMock(return_value={"_key": "user-key"})
        with pytest.raises(SelectionTooLargeError) as raised:
            await prepare_turn_scope(provider, "u", "o", {"records": ["f1"]})
        assert selection_error(raised.value)[0] == "selection_too_large"

    @pytest.mark.asyncio
    async def test_a_selection_that_resolves_to_nothing_touches_no_app(self) -> None:
        provider = _provider()
        provider.get_user_by_user_id = AsyncMock(return_value={"_key": "user-key"})
        turn = await prepare_turn_scope(provider, "u", "o", {"records": ["gone"]})
        assert "selectionApps" not in turn and selection_app_ids(turn) == []
        # ... and the tools that work per app must read that as nothing.
        assert turn["strictScope"] is True

    def test_the_apps_a_selection_touches(self) -> None:
        assert selection_app_ids({"apps": ["a"], "kb": ["k"], "records": ["r"], "selectionApps": ["t", "a"]}) == [
            "a", "k", "t",
        ]
        assert selection_app_ids({"apps": ["a"]}) is None


# ---------------------------------------------------------------------------
# The retrieval service
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _a_user_with_a_graph_key(mock_graph_provider):
    mock_graph_provider.get_user_by_user_id.return_value = {"_key": "user-key", "email": "t@example.com"}


@pytest.fixture
def retrieval_service(
    logger, mock_config_service, mock_vector_db_service, mock_graph_provider, mock_blob_store,
):
    with patch("app.modules.retrieval.retrieval_service.SparseEmbedder") as sparse:
        instance = AsyncMock()
        instance.embed_query = AsyncMock(return_value=MagicMock(indices=[0], values=[1.0]))
        sparse.return_value = instance
        registry = MagicMock()
        registry.strategy_name = "single"
        registry.resolve_for_query = AsyncMock(return_value=["test_collection"])
        service = retrieval_mod.RetrievalService(
            logger=logger,
            config_service=mock_config_service,
            collection_registry=registry,
            vector_db_service=mock_vector_db_service,
            graph_provider=mock_graph_provider,
            blob_store=mock_blob_store,
        )
        service._execute_parallel_searches = AsyncMock(return_value=[])
        yield service


def _select(graph, *, readable=(), groups=(), records=(), apps=None) -> None:
    graph.check_access = AsyncMock(return_value=AccessCheck(node_ids=frozenset(readable)))
    graph.get_selection_nodes = AsyncMock(return_value={"groups": list(groups), "records": list(records)})
    graph.get_nodes_by_field_in = AsyncMock(
        return_value=apps if apps is not None else [{"id": "app-1", "vectorMembershipBackfilled": True}]
    )


class TestSearchInsideASelection:
    @pytest.mark.asyncio
    async def test_the_permission_query_sees_only_the_apps_the_selection_touches(
        self, retrieval_service, mock_graph_provider, mock_vector_db_service,
    ) -> None:
        _select(mock_graph_provider, readable={"f1"},
                records=[{"id": "r1", "vrid": "v1", "connectorId": "app-1"}])
        mock_graph_provider.get_accessible_containers = AsyncMock(return_value=AccessibleContainers(
            app_ids=frozenset({"app-1"}), scope_connector_ids=frozenset({"app-1"}),
        ))

        await retrieval_service.search_with_filters(
            queries=["q"], user_id="u1", org_id="o1", filter_groups={"records": ["f1"]},
        )

        assert mock_graph_provider.get_accessible_containers.await_args.args[2] == {"apps": ["app-1"]}
        kwargs = mock_vector_db_service.filter_collection.await_args.kwargs
        assert kwargs["must"] == {"orgId": "o1"}
        assert kwargs["should"] == {"virtualRecordId": ["v1"]}

    @pytest.mark.asyncio
    async def test_other_filter_keys_survive_beside_a_selection(
        self, retrieval_service, mock_graph_provider,
    ) -> None:
        _select(mock_graph_provider, readable={"g1"}, groups=[{"id": "g1", "connectorId": "app-1"}])
        mock_graph_provider.get_accessible_virtual_record_ids = AsyncMock(return_value={})

        await retrieval_service.search_with_filters(
            queries=["q"], user_id="u1", org_id="o1",
            filter_groups={"recordGroups": ["g1"], "Departments": ["hr"], "strictScope": True},
        )

        sent = mock_graph_provider.get_accessible_virtual_record_ids.await_args.kwargs["filters"]
        assert sent == {"apps": ["app-1"], "departments": ["hr"], "strictScope": True}

    @pytest.mark.asyncio
    async def test_the_record_id_path_narrows_with_the_same_clause(
        self, retrieval_service, mock_graph_provider, mock_vector_db_service,
    ) -> None:
        _select(mock_graph_provider, readable={"g1"}, groups=[{"id": "g1", "connectorId": "app-1"}])
        mock_graph_provider.get_accessible_virtual_record_ids = AsyncMock(return_value={"v1": "r1"})

        await retrieval_service.search_with_filters(
            queries=["q"], user_id="u1", org_id="o1",
            filter_groups={"recordGroups": ["g1"]}, time_range={"source_created_after_ms": 1},
        )

        kwargs = mock_vector_db_service.filter_collection.await_args.kwargs
        assert kwargs["must"] == {"orgId": "o1", "virtualRecordId": ["v1"]}
        assert kwargs["should"] == {RECORD_GROUP_IDS_FIELD: ["g1"]}

    @pytest.mark.asyncio
    async def test_a_selection_with_nothing_readable_searches_nothing(
        self, retrieval_service, mock_graph_provider, mock_vector_db_service,
    ) -> None:
        _select(mock_graph_provider)

        result = await retrieval_service.search_with_filters(
            queries=["q"], user_id="u1", org_id="o1", filter_groups={"records": ["gone"]},
        )

        assert result["status"] == Status.SELECTION_EMPTY.value and result["status_code"] == 404
        mock_vector_db_service.filter_collection.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_selection_over_the_cap_is_refused(
        self, retrieval_service, mock_graph_provider, mock_vector_db_service, monkeypatch,
    ) -> None:
        monkeypatch.setenv("SEARCH_SELECTION_MAX_NODES", "1")
        _select(mock_graph_provider, readable={"f1"}, records=[
            {"id": "r1", "vrid": "v1", "connectorId": "app-1"},
            {"id": "r2", "vrid": "v2", "connectorId": "app-1"},
        ])

        result = await retrieval_service.search_with_filters(
            queries=["q"], user_id="u1", org_id="o1", filter_groups={"records": ["f1"]},
        )

        assert result["status"] == Status.SELECTION_TOO_LARGE.value and result["status_code"] == 422
        mock_vector_db_service.filter_collection.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_group_of_an_app_without_membership_is_refused(
        self, retrieval_service, mock_graph_provider,
    ) -> None:
        _select(mock_graph_provider, readable={"g1"}, groups=[{"id": "g1", "connectorId": "app-1"}],
                apps=[{"id": "app-1", "vectorMembershipBackfilled": False}])

        result = await retrieval_service.search_with_filters(
            queries=["q"], user_id="u1", org_id="o1", filter_groups={"recordGroups": ["g1"]},
        )

        assert result["status"] == Status.SELECTION_NOT_READY.value

    @pytest.mark.asyncio
    async def test_an_app_only_request_asks_nothing_about_a_selection(
        self, retrieval_service, mock_graph_provider,
    ) -> None:
        mock_graph_provider.get_selection_nodes = AsyncMock()
        mock_graph_provider.get_accessible_containers = AsyncMock(return_value=AccessibleContainers(
            app_ids=frozenset({"app-1"}), scope_connector_ids=frozenset({"app-1"}),
        ))

        await retrieval_service.search_with_filters(
            queries=["q"], user_id="u1", org_id="o1", filter_groups={"apps": ["app-1"]},
        )

        mock_graph_provider.get_selection_nodes.assert_not_awaited()
        assert mock_graph_provider.get_accessible_containers.await_args.args[2] == {"apps": ["app-1"]}


class TestCitingInsideASelection:
    @pytest.mark.asyncio
    async def test_the_enumerated_copy_is_not_kept_by_id(self, retrieval_service, mock_graph_provider) -> None:
        """Asked by id, the copy the enumeration chose would be admitted even
        when it lies outside the selection."""
        mock_graph_provider.check_access = AsyncMock(
            return_value=AccessCheck(records_by_vrid={"v1": "r-inside"})
        )
        hits = [{"metadata": {"virtualRecordId": "v1"}}, {"metadata": {"virtualRecordId": "v2"}}]

        kept = await retrieval_service._keep_permitted_hits(
            hits, {"v1": "r-outside", "v2": "r-other"}, "user-key", "o1", scopes=[SCOPE],
        )

        assert kept == {"v1": "r-inside"}
        call = mock_graph_provider.check_access.await_args.kwargs
        assert "node_ids" not in call and call["scopes"] == [SCOPE]

    def test_the_container_filter_is_built_from_the_selection(self, retrieval_service) -> None:
        must, should = retrieval_service._build_container_clauses(
            "o1", AccessibleContainers(app_ids=frozenset({"app-whole", "app-g"})), None, SCOPE,
        )
        assert must == {"orgId": "o1"}
        assert should == SCOPE.should_clauses({"app-whole", "app-g"})
        assert should[CONNECTOR_IDS_FIELD] == ["app-whole"]
