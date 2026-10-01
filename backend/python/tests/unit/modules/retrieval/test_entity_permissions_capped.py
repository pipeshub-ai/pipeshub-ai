"""An entity whose candidate scan hit the provider's cap must never be
reported as fully listed, empty, or listed newest-first across all records.

The provider marks a capped scan with ``EntityCandidateRows.capped``; the
permission layer carries it into the page, the search preview and the
entity-scoped search scope.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from app.modules.retrieval.entity_permissions import (
    EntityAccessContext,
    list_accessible_entity_records,
    search_entities_for_user,
)
from app.services.graph_db.common.utils import EntityCandidateRows

ORG = "org-1"


def _context() -> EntityAccessContext:
    return EntityAccessContext(
        org_id=ORG,
        user_key="ukey",
        app_level_app_ids=frozenset({"kb-1"}),
        record_level_app_ids=frozenset({"conf-1"}),
        record_group_ids=frozenset(),
        app_names={"kb-1": "KB", "conf-1": "Confluence"},
    )


def _row(key: str, connector: str = "kb-1") -> dict:
    return {"_key": key, "connectorId": connector, "recordName": key}


def _graph(rows: list[dict], *, capped: bool, permitted: set[str] | None = None) -> MagicMock:
    """A provider whose single entity has ``rows`` inside its capped window."""
    graph = MagicMock()

    async def _candidates(
        refs: list[dict], _org_id: str, *, record_types: list[str] | None = None,
        limit_per_entity: int = 20, offset: int = 0,
    ) -> dict:
        window = rows[offset: offset + limit_per_entity]
        return {
            (ref["type"], ref["id"]): EntityCandidateRows(window, capped=capped) for ref in refs
        }

    graph.get_entity_candidate_records = AsyncMock(side_effect=_candidates)
    graph.filter_nodes_with_permission_role = AsyncMock(return_value=set(permitted or ()))
    graph.filter_records_shared_with_anyone = AsyncMock(return_value=set())
    return graph


class TestCandidateRowsType:
    def test_compares_equal_to_a_plain_list(self) -> None:
        rows = EntityCandidateRows([{"_key": "a"}], capped=True)
        assert rows == [{"_key": "a"}]
        assert rows.capped is True

    def test_defaults_to_not_capped(self) -> None:
        assert EntityCandidateRows().capped is False


class TestListingReportsTheCap:
    async def test_end_of_a_capped_window_is_marked_capped(self) -> None:
        graph = _graph([_row(f"r{i}") for i in range(5)], capped=True)
        page = await list_accessible_entity_records(
            graph, _context(), entity_id="t1", entity_type="topic", limit=20,
        )
        assert [r["_key"] for r in page.records] == [f"r{i}" for i in range(5)]
        assert page.next_cursor is None
        assert page.capped is True

    async def test_capped_entity_with_nothing_permitted_is_capped_not_empty(self) -> None:
        rows = [_row(f"r{i}", connector="conf-1") for i in range(5)]
        graph = _graph(rows, capped=True, permitted=set())
        page = await list_accessible_entity_records(
            graph, _context(), entity_id="t1", entity_type="topic", limit=20,
        )
        assert page.records == []
        assert page.capped is True

    async def test_uncapped_entity_is_not_marked(self) -> None:
        graph = _graph([_row("r1")], capped=False)
        page = await list_accessible_entity_records(
            graph, _context(), entity_id="t1", entity_type="topic", limit=20,
        )
        assert page.capped is False

    async def test_provider_returning_plain_lists_is_treated_as_uncapped(self) -> None:
        graph = MagicMock()
        graph.get_entity_candidate_records = AsyncMock(
            return_value={("topic", "t1"): [_row("r1")]},
        )
        graph.filter_nodes_with_permission_role = AsyncMock(return_value=set())
        page = await list_accessible_entity_records(
            graph, _context(), entity_id="t1", entity_type="topic", limit=20,
        )
        assert page.capped is False


class TestSearchPreviewReportsTheCap:
    async def test_capped_entity_says_more_records_even_when_its_window_is_exhausted(self) -> None:
        graph = _graph([_row("r1")], capped=True)
        store = MagicMock()
        # Every pass in one call; the hit comes from the first.
        store.search_entities_passes = AsyncMock(side_effect=lambda q, org, passes, **kw: [
            [{"entityId": "t1", "entityType": "topic", "name": "Security", "score": 0.9}],
            *([] for _ in passes[1:]),
        ])
        hits = await search_entities_for_user(store, graph, _context(), "security")
        assert len(hits) == 1
        assert hits[0].more_records is True


class TestEmptyCappedWindow:
    """An empty window past the cap is still capped: ``EntityCandidateRows([])``
    is falsy, so it must not be replaced by a plain list."""

    async def test_cursor_at_the_window_end_reports_capped(self) -> None:
        graph = _graph([_row(f"r{i}") for i in range(5)], capped=True)
        page = await list_accessible_entity_records(
            graph, _context(), entity_id="t1", entity_type="topic", limit=20, cursor="5",
        )
        assert page.records == []
        assert page.next_cursor is None
        assert page.capped is True

    async def test_probe_with_an_empty_capped_window_says_more_records(self) -> None:
        graph = _graph([], capped=True)
        store = MagicMock()
        # Every pass in one call; the hit comes from the first.
        store.search_entities_passes = AsyncMock(side_effect=lambda q, org, passes, **kw: [
            [{"entityId": "rg-1", "entityType": "record_group", "name": "Roadmaps", "score": 0.9}],
            *([] for _ in passes[1:]),
        ])
        context = EntityAccessContext(
            org_id=ORG, user_key="ukey", app_level_app_ids=frozenset({"kb-1"}),
            record_level_app_ids=frozenset(), record_group_ids=frozenset({"rg-1"}),
            app_names={"kb-1": "KB"},
        )
        hits = await search_entities_for_user(store, graph, context, "roadmaps")
        assert hits and hits[0].more_records is True
