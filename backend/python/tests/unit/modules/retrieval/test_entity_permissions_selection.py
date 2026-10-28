"""The entity tools inside a turn's limit: a selection below app level (record
groups, folders, records) or a saved agent's or project's sources.

The invariant: the entity layer reaches every app the limit touches, uses only
records every scope of the limit admits, and never reads an unresolvable limit
as "no limit".
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError
from app.modules.retrieval import entity_permissions as ep
from app.modules.retrieval.entity_permissions import (
    EntityAccessContext,
    EntityAccessError,
    admitted_node_ids,
    get_entity_access_context,
    list_accessible_entity_records,
    search_entities_for_user,
)
from app.modules.retrieval.selection_scope import (
    SelectionScope,
    limits_rows,
    row_limit_key,
)
from app.services.graph_db.common.utils import within_param
from app.services.graph_db.interface.graph_db_provider import AccessCheck
from tests.unit.modules.retrieval.entity_access_fakes import (
    RESOLVED_ACCESS,
    entity_graph,
)

ORG = "org-1"
USER = "user-1"
JIRA = "jira-1"
SELECTED = SelectionScope(groups={"proj-a": JIRA})


def _context(row_scopes: tuple[SelectionScope, ...] = (SELECTED,)) -> EntityAccessContext:
    return EntityAccessContext(
        org_id=ORG,
        user_key="ukey",
        app_level_app_ids=frozenset({JIRA}),
        record_level_app_ids=frozenset(),
        record_group_ids=frozenset({"proj-a", "proj-b"}),
        app_names={JIRA: "Jira"},
        row_scopes=row_scopes,
    )


def _row(key: str) -> dict:
    return {"_key": key, "connectorId": JIRA, "recordName": f"doc {key}"}


def _graph(rows_by_entity: dict[str, list[dict]], groups: dict[str, list[str]]) -> MagicMock:
    """Jira is app-level, so every candidate is readable: only the limit decides."""
    return entity_graph(lambda refs, org, **_: rows_by_entity, app_level=(JIRA,), groups=groups)


def _access_graph(raw: dict | None = None) -> MagicMock:
    graph = MagicMock()
    graph.get_entity_access_context = AsyncMock(return_value=raw or {
        "user_key": "ukey", "apps": [{"id": JIRA, "type": "JIRA", "permissionModel": "APP_LEVEL"}],
        "record_group_ids": ["proj-a", "proj-b"],
    })
    return graph


SELECTION_FILTERS = {"apps": [], "kb": [], "recordGroups": ["proj-a"], "selectionApps": [JIRA], "strictScope": True}


class TestTheContextCarriesTheLimit:
    @pytest.mark.asyncio
    async def test_a_selection_resolves_its_scopes_with_the_users_key(self, monkeypatch) -> None:
        resolve = AsyncMock(return_value=(SELECTED, [SELECTED]))
        monkeypatch.setattr(ep, "resolve_request_scopes", resolve)

        context = await get_entity_access_context(
            {}, _access_graph(), org_id=ORG, user_id=USER, source_ids=[JIRA], strict=True,
            filters=SELECTION_FILTERS,
        )

        assert context.app_ids == {JIRA}
        assert context.row_scopes == (SELECTED,)
        assert context.limited_group_ids == {"proj-a"}
        resolve.assert_awaited_once()
        assert resolve.await_args.args[1:3] == ("ukey", ORG)

    @pytest.mark.asyncio
    async def test_a_turn_without_a_limit_resolves_no_scope(self, monkeypatch) -> None:
        resolve = AsyncMock()
        monkeypatch.setattr(ep, "resolve_request_scopes", resolve)

        context = await get_entity_access_context(
            {}, _access_graph(), org_id=ORG, user_id=USER, source_ids=[JIRA], filters={"apps": [JIRA]},
        )

        assert context.row_scopes == ()
        resolve.assert_not_called()

    @pytest.mark.asyncio
    async def test_each_limit_gets_its_own_cached_context(self, monkeypatch) -> None:
        monkeypatch.setattr(ep, "resolve_request_scopes", AsyncMock(return_value=(SELECTED, [SELECTED])))
        graph, state = _access_graph(), {}

        first = await get_entity_access_context(state, graph, org_id=ORG, user_id=USER, source_ids=[JIRA],
                                                filters=SELECTION_FILTERS)
        same = await get_entity_access_context(state, graph, org_id=ORG, user_id=USER, source_ids=[JIRA],
                                               filters=dict(SELECTION_FILTERS))
        other = await get_entity_access_context(state, graph, org_id=ORG, user_id=USER, source_ids=[JIRA],
                                                filters={**SELECTION_FILTERS, "recordGroups": ["proj-b"]})

        assert same is first
        assert other is not first
        assert graph.get_entity_access_context.await_count == 2

    @pytest.mark.asyncio
    async def test_strict_with_no_source_reaches_nothing_and_resolves_nothing(self, monkeypatch) -> None:
        resolve = AsyncMock()
        monkeypatch.setattr(ep, "resolve_request_scopes", resolve)

        context = await get_entity_access_context(
            {}, _access_graph(), org_id=ORG, user_id=USER, source_ids=[], strict=True, filters=SELECTION_FILTERS,
        )

        assert context.app_ids == frozenset()
        resolve.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_selection_with_nothing_readable_is_still_a_limit(self, monkeypatch) -> None:
        """A member selects a project they cannot read: no source is left, the
        context reaches nothing, and it still knows a limit applied."""
        monkeypatch.setattr(ep, "resolve_request_scopes", AsyncMock())

        context = await get_entity_access_context(
            {}, _access_graph(), org_id=ORG, user_id=USER, source_ids=[], strict=True, filters=SELECTION_FILTERS,
        )

        assert context.app_ids == frozenset()
        assert context.row_scopes == (SelectionScope(),)

    @pytest.mark.asyncio
    async def test_a_limit_the_graph_cannot_resolve_fails_closed(self, monkeypatch) -> None:
        monkeypatch.setattr(
            ep, "resolve_request_scopes", AsyncMock(side_effect=PermissionVerificationUnavailableError("down")),
        )
        with pytest.raises(EntityAccessError):
            await get_entity_access_context(
                {}, _access_graph(), org_id=ORG, user_id=USER, source_ids=[JIRA], filters=SELECTION_FILTERS,
            )

    @pytest.mark.asyncio
    async def test_a_whole_app_bound_does_not_limit_the_turn(self, monkeypatch) -> None:
        """A saved agent's whole sources repeat what source_ids already limit."""
        monkeypatch.setattr(
            ep, "resolve_request_scopes",
            AsyncMock(return_value=(None, [SelectionScope(app_ids=frozenset({JIRA, "kb-9"}))])),
        )

        context = await get_entity_access_context(
            {}, _access_graph(), org_id=ORG, user_id=USER, source_ids=[JIRA],
            filters={"allowedApps": [JIRA, "kb-9"], "allowedRecordGroups": [], "allowedRecords": []},
        )

        assert context.row_scopes == ()

    @pytest.mark.asyncio
    async def test_a_bound_that_leaves_out_a_source_app_still_limits(self, monkeypatch) -> None:
        bound = SelectionScope(app_ids=frozenset({"kb-9"}))
        monkeypatch.setattr(ep, "resolve_request_scopes", AsyncMock(return_value=(None, [bound])))

        context = await get_entity_access_context(
            {}, _access_graph(), org_id=ORG, user_id=USER, source_ids=[JIRA],
            filters={"allowedApps": ["kb-9"], "allowedRecordGroups": [], "allowedRecords": []},
        )

        assert context.row_scopes == (bound,)

    @pytest.mark.asyncio
    async def test_a_limit_that_resolves_to_no_scope_admits_nothing(self, monkeypatch) -> None:
        monkeypatch.setattr(ep, "resolve_request_scopes", AsyncMock(return_value=(None, [])))

        context = await get_entity_access_context(
            {}, _access_graph(), org_id=ORG, user_id=USER, source_ids=[JIRA], filters={"allowedApps": []},
        )

        assert context.row_scopes == (SelectionScope(),)
        assert not context.row_scopes[0].admits({"id": "r1", "connectorId": JIRA, "groupIds": ["proj-a"]})


class TestOnlyRecordsInsideTheLimitAreUsed:
    @pytest.mark.asyncio
    async def test_the_access_check_is_given_the_limit_and_its_in_scope_answer_is_used(self) -> None:
        graph = MagicMock()
        graph.check_access = AsyncMock(return_value=AccessCheck(
            node_ids=frozenset({"r1", "r2"}), node_ids_in_scope=frozenset({"r1"}),
        ))
        context = _context()
        context._resolved_access["access"] = RESOLVED_ACCESS

        assert await admitted_node_ids(graph, context, ["r1", "r2"]) == {"r1"}
        assert graph.check_access.await_args.kwargs["scopes"] == (SELECTED,)

    @pytest.mark.asyncio
    async def test_without_a_limit_every_readable_id_is_admitted(self) -> None:
        graph = MagicMock()
        graph.check_access = AsyncMock(return_value=AccessCheck(node_ids=frozenset({"r1", "r2"})))
        context = _context(row_scopes=())
        context._resolved_access["access"] = RESOLVED_ACCESS

        assert await admitted_node_ids(graph, context, ["r1", "r2"]) == {"r1", "r2"}
        assert graph.check_access.await_args.kwargs["scopes"] == ()

    @pytest.mark.asyncio
    async def test_a_listing_returns_only_records_of_the_selected_group(self) -> None:
        graph = _graph({"t1": [_row("r1"), _row("r2"), _row("r3")]}, groups={"r1": ["proj-a"], "r2": ["proj-b"],
                                                                         "r3": ["proj-a"]})

        page = await list_accessible_entity_records(graph, _context(), entity_id="t1", entity_type="topic", limit=5)

        assert [r["_key"] for r in page.records] == ["r1", "r3"]
        assert graph.fake.within_calls == [[SELECTED.candidate_filter()]]

    @pytest.mark.asyncio
    async def test_the_limit_narrows_candidates_before_the_window(self) -> None:
        """A small selection inside a big app: the in-selection record sits past
        the first window of the app's newest candidates, and is still found by
        the first query because the query narrows before it pages."""
        outside = [_row(f"o{i}") for i in range(ep.LISTING_WINDOW_MAX + 5)]
        graph = _graph({"t1": [*outside, _row("in-1")]},
                       groups={**{r["_key"]: ["proj-b"] for r in outside}, "in-1": ["proj-a"]})

        page = await list_accessible_entity_records(graph, _context(), entity_id="t1", entity_type="topic", limit=5)

        assert [r["_key"] for r in page.records] == ["in-1"]
        assert graph.get_permitted_entity_records.await_count == 1

    @pytest.mark.asyncio
    async def test_without_a_limit_the_query_is_not_narrowed(self) -> None:
        graph = _graph({"t1": [_row("r1"), _row("r2")]}, groups={"r1": ["proj-a"], "r2": ["proj-b"]})

        page = await list_accessible_entity_records(
            graph, _context(row_scopes=()), entity_id="t1", entity_type="topic", limit=5,
        )

        assert [r["_key"] for r in page.records] == ["r1", "r2"]
        assert graph.fake.within_calls == [None]

    @pytest.mark.asyncio
    async def test_a_nested_group_is_inside_its_selected_parent(self) -> None:
        nested = SelectionScope(groups={"proj-a": JIRA, "proj-a-child": JIRA})
        graph = _graph({"t1": [_row("r1")]}, groups={"r1": ["proj-a-child"]})

        page = await list_accessible_entity_records(
            graph, _context(row_scopes=(nested,)), entity_id="t1", entity_type="topic", limit=5,
        )

        assert [r["_key"] for r in page.records] == ["r1"]

    @pytest.mark.asyncio
    async def test_a_folder_selection_admits_the_records_under_it_only(self) -> None:
        folder = SelectionScope(records={"folder": ("", "kb-1"), "f-child": ("vr", "kb-1")})
        graph = _graph({"t1": [_row("f-child"), _row("sibling")]}, groups={})

        page = await list_accessible_entity_records(
            graph, _context(row_scopes=(folder,)), entity_id="t1", entity_type="topic", limit=5,
        )

        assert [r["_key"] for r in page.records] == ["f-child"]

    @pytest.mark.asyncio
    async def test_a_selection_and_a_bound_must_both_admit_a_record(self) -> None:
        bound = SelectionScope(records={"r2": ("vr2", JIRA)})
        graph = _graph({"t1": [_row("r1"), _row("r2")]}, groups={"r1": ["proj-a"], "r2": ["proj-a"]})

        page = await list_accessible_entity_records(
            graph, _context(row_scopes=(SELECTED, bound)), entity_id="t1", entity_type="topic", limit=5,
        )

        assert [r["_key"] for r in page.records] == ["r2"]

    @pytest.mark.asyncio
    async def test_paging_advances_when_nothing_in_a_window_is_inside(self) -> None:
        """The access check (the last gate) refuses a whole window: the cursor
        still moves forward, so the caller is never offered the same window."""
        graph = _graph({"t1": [_row(f"r{i}") for i in range(3)]}, groups={})
        graph.check_access = AsyncMock(return_value=AccessCheck(
            node_ids=frozenset({"r0", "r1", "r2"}), node_ids_in_scope=frozenset(),
        ))

        page = await list_accessible_entity_records(graph, _context(), entity_id="t1", entity_type="topic", limit=5)

        assert page.records == []
        assert page.next_cursor is None


def _store(*pass_results) -> MagicMock:
    store = MagicMock()

    async def _passes(query: str, org_id: str, passes: list, **_: object) -> list[list[dict]]:
        return [list(pass_results[i]) if i < len(pass_results) else [] for i in range(len(passes))]

    store.search_entities_passes = AsyncMock(side_effect=_passes)
    return store


def _hit(entity_id: str, entity_type: str = "topic", score: float = 0.9) -> dict:
    return {"entityId": entity_id, "entityType": entity_type, "name": entity_id, "score": score}


class TestSearchInsideTheLimit:
    @pytest.mark.asyncio
    async def test_the_selected_groups_are_searched_first(self) -> None:
        store = _store([_hit("t1")])
        graph = _graph({"t1": [_row("r1")]}, groups={"r1": ["proj-a"]})

        await search_entities_for_user(store, graph, _context(), "roadmap", top_k=5)

        first = store.search_entities_passes.await_args.args[2][0]
        assert first.record_group_ids == {"proj-a"}
        assert first.connector_ids == frozenset() and first.org_wide is False

    @pytest.mark.asyncio
    async def test_without_a_selection_there_is_no_selection_pass(self) -> None:
        store = _store([_hit("t1")])
        graph = _graph({"t1": [_row("r1")]}, groups={})

        await search_entities_for_user(store, graph, _context(row_scopes=()), "roadmap", top_k=5)

        first = store.search_entities_passes.await_args.args[2][0]
        assert first.record_group_ids == {"proj-a", "proj-b"}

    @pytest.mark.asyncio
    async def test_an_entity_named_only_outside_the_selection_is_not_shown(self) -> None:
        store = _store([_hit("inside", score=0.8), _hit("outside", score=0.9)])
        graph = _graph(
            {"inside": [_row("r-in")], "outside": [_row("r-out")]},
            groups={"r-in": ["proj-a"], "r-out": ["proj-b"]},
        )

        hits = await search_entities_for_user(store, graph, _context(), "roadmap", top_k=5)

        assert [h.entity_id for h in hits] == ["inside"]
        assert [r["_key"] for r in hits[0].records] == ["r-in"]

    @pytest.mark.asyncio
    async def test_a_readable_group_outside_the_selection_is_not_admitted_as_a_whole(self) -> None:
        store = _store([_hit("proj-b", "record_group")])
        graph = _graph({"proj-b": [_row("r-b")]}, groups={"r-b": ["proj-b"]})

        hits = await search_entities_for_user(store, graph, _context(), "project b", top_k=5)

        assert hits == []

    @pytest.mark.asyncio
    async def test_the_selected_group_itself_is_found(self) -> None:
        store = _store([_hit("proj-a", "record_group")])
        graph = _graph({"proj-a": [_row("r-a")]}, groups={"r-a": ["proj-a"]})

        hits = await search_entities_for_user(store, graph, _context(), "project a", top_k=5)

        assert [h.entity_id for h in hits] == ["proj-a"]


class TestTheLimitAsData:
    def test_a_scope_narrows_a_query_by_the_same_tests_it_admits_a_row_by(self) -> None:
        scope = SelectionScope(app_ids=frozenset({"kb-1"}), groups={"g": JIRA}, records={"r": ("vr", JIRA)})
        assert scope.candidate_filter() == {"apps": ["kb-1"], "groups": ["g"], "records": ["r"]}

    @pytest.mark.parametrize(
        ("filters", "limited"),
        [
            (None, False),
            ({"apps": ["a"], "kb": ["k"]}, False),
            ({"recordGroups": ["g"]}, True),
            ({"records": ["r"]}, True),
            ({"recordsExact": ["r"]}, True),
            ({"allowedApps": []}, True),
            ({"projectRecordGroups": ["g"]}, True),
        ],
    )
    def test_what_limits_a_turn(self, filters, limited) -> None:
        assert limits_rows(filters) is limited

    def test_the_cache_key_ignores_order_and_tells_limits_apart(self) -> None:
        assert row_limit_key({"recordGroups": ["b", "a"]}) == row_limit_key({"recordGroups": ["a", "b"]})
        assert row_limit_key({"recordGroups": ["a"]}) != row_limit_key({"records": ["a"]})
        assert row_limit_key({"allowedApps": []}) != row_limit_key({})

    @pytest.mark.parametrize(
        ("within", "expected"),
        [
            (None, None),
            ([], [{"apps": [], "groups": [], "records": []}]),
            ([{"groups": ["g", ""], "apps": None}], [{"apps": [], "groups": ["g"], "records": []}]),
        ],
    )
    def test_the_query_parameter_never_reads_an_empty_limit_as_none(self, within, expected) -> None:
        assert within_param(within) == expected


class TestTheProvidersNarrowInsideTheQuery:
    def test_neo4j_narrows_candidates_by_within_before_the_scan_cap(self) -> None:
        from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

        provider = Neo4jProvider.__new__(Neo4jProvider)
        for entity_type in ("topic", "record_group", "record", "named_entity"):
            cypher = provider._permitted_entity_records_cypher(entity_type)
            assert "$within" in cypher
            if entity_type != "record":
                assert cypher.index("$within") < cypher.index("LIMIT $scan_cap")
        assert "$within" not in provider._entity_candidate_records_cypher("topic")

    def test_arango_narrows_candidates_by_within_before_the_scan_cap(self) -> None:
        from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

        provider = ArangoHTTPProvider.__new__(ArangoHTTPProvider)
        for entity_type in ("topic", "record_group", "record", "named_entity"):
            aql = provider._permitted_entity_records_aql(entity_type, filter_record_types=False)
            assert "@within" in aql
            if entity_type != "record":
                assert aql.index("@within") < aql.index("LIMIT @scan_cap")
        # Arango rejects a bind variable the query does not reference.
        assert "@within" not in provider._entity_candidate_records_aql("topic", filter_record_types=False)
