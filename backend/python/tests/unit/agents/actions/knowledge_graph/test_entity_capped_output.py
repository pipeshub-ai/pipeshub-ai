"""In the tools: a capped entity's listing and entity-scoped search say
the result is a bounded sample, never "newest first" over everything, "last
page", or "no records"."""
from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.actions.knowledge_graph.ops import entity_records
from app.agents.actions.knowledge_graph.ops.entity_records import (
    CAPPED_EMPTY_MSG,
    NO_ACCESSIBLE_RECORDS_MSG,
    NO_FURTHER_RECORDS_MSG,
    execute_find_records_by_entity,
    resolve_entity_virtual_ids,
)
from app.agents.actions.knowledge_graph.ops.search import _entity_notes
from app.modules.retrieval.entity_permissions import (
    SEARCH_SCOPE_MAX_ENTITIES,
    EntityAccessContext,
    EntityRecordPage,
)

if TYPE_CHECKING:
    from collections.abc import Callable

CONTEXT = EntityAccessContext(
    org_id="org-1",
    user_key="ukey",
    app_level_app_ids=frozenset({"kb-1"}),
    record_level_app_ids=frozenset(),
    record_group_ids=frozenset(),
    app_names={"kb-1": "Team KB"},
)


def _state() -> dict:
    return {"org_id": "org-1", "user_id": "user-1", "graph_provider": MagicMock()}


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch) -> Callable[[EntityRecordPage], AsyncMock]:
    def install(page: EntityRecordPage) -> AsyncMock:
        monkeypatch.setattr(entity_records, "load_entity_access_context", AsyncMock(return_value=CONTEXT))
        listing = AsyncMock(return_value=page)
        monkeypatch.setattr(entity_records, "list_accessible_entity_records", listing)
        return listing

    return install


def _rows(n: int) -> list[dict]:
    return [
        {"_key": f"r{i}", "recordName": f"doc {i}", "recordType": "FILE", "connectorId": "kb-1",
         "virtualRecordId": f"v{i}"}
        for i in range(n)
    ]


class TestFindRecordsByEntityWhenCapped:
    async def test_capped_page_says_it_is_a_sample_and_how_to_narrow(self, patched) -> None:
        patched(EntityRecordPage(records=_rows(2), next_cursor=None, capped=True))
        ok, text = await execute_find_records_by_entity(_state(), entity_id="t1", entity_type="topic")
        assert ok
        assert "newest first" not in text.splitlines()[0]
        assert "more records than" in text
        assert "record_types" in text and "entity_ids" in text

    async def test_capped_entity_with_no_visible_records_is_not_reported_empty(self, patched) -> None:
        patched(EntityRecordPage(records=[], next_cursor=None, capped=True))
        ok, text = await execute_find_records_by_entity(_state(), entity_id="t1", entity_type="topic")
        assert ok
        assert text == CAPPED_EMPTY_MSG
        assert text not in (NO_ACCESSIBLE_RECORDS_MSG, NO_FURTHER_RECORDS_MSG)

    async def test_capped_entity_paged_past_its_window_is_not_reported_finished(self, patched) -> None:
        patched(EntityRecordPage(records=[], next_cursor=None, capped=True))
        ok, text = await execute_find_records_by_entity(
            _state(), entity_id="t1", entity_type="topic", cursor="40",
        )
        assert text == CAPPED_EMPTY_MSG

    async def test_uncapped_page_still_reads_newest_first(self, patched) -> None:
        patched(EntityRecordPage(records=_rows(2), next_cursor=None))
        ok, text = await execute_find_records_by_entity(_state(), entity_id="t1", entity_type="topic")
        assert "newest first" in text.splitlines()[0]
        assert "more records than" not in text


class TestEntitySearchScope:
    async def test_capped_entity_marks_the_scope_truncated(self, monkeypatch) -> None:
        monkeypatch.setattr(entity_records, "load_entity_access_context", AsyncMock(return_value=CONTEXT))
        monkeypatch.setattr(
            entity_records, "list_accessible_entity_records",
            AsyncMock(return_value=EntityRecordPage(records=_rows(3), next_cursor=None, capped=True)),
        )
        state = _state()
        scope = await resolve_entity_virtual_ids(state, [("rg-1", "record_group")])
        assert scope.virtual_ids == ["v0", "v1", "v2"]
        assert scope.truncated is True

    async def test_entities_beyond_the_limit_are_counted_not_blamed_on_size(self, monkeypatch) -> None:
        monkeypatch.setattr(entity_records, "load_entity_access_context", AsyncMock(return_value=CONTEXT))
        monkeypatch.setattr(
            entity_records, "list_accessible_entity_records",
            AsyncMock(return_value=EntityRecordPage(records=[], next_cursor=None)),
        )
        entities = [(f"rg-{i}", "record_group") for i in range(SEARCH_SCOPE_MAX_ENTITIES + 2)]
        scope = await resolve_entity_virtual_ids(_state(), entities)
        assert scope.entities_skipped == 2


class TestEntityNotes:
    def test_truncated_scope_does_not_claim_newest(self) -> None:
        note = _entity_notes(
            unknown_entity_ids=[], filter_dropped=False, record_scope_applied=True,
            scope_truncated=True,
        )
        assert "newest" not in note
        assert "only part" in note

    def test_skipped_entities_get_their_own_note(self) -> None:
        note = _entity_notes(
            unknown_entity_ids=[], filter_dropped=False, record_scope_applied=True,
            scope_truncated=False, entities_skipped=2,
        )
        assert f"first {SEARCH_SCOPE_MAX_ENTITIES} record group/subcategory/person entity_ids" in note
        assert "2 more record group/subcategory/person entity_ids" in note

    def test_skipped_note_does_not_count_name_filtered_entities(self) -> None:
        """Topics and other name-filtered ids are never capped, so the note
        must not read as if only the first N of all entity_ids applied."""
        note = _entity_notes(
            unknown_entity_ids=[], filter_dropped=False, record_scope_applied=True,
            scope_truncated=False, entities_skipped=2,
        )
        assert f"first {SEARCH_SCOPE_MAX_ENTITIES} entity_ids" not in note
        assert "topic" in note
