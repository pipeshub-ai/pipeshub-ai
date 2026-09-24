"""Unit tests for ``ops/entity_records.py`` (``find_records_by_entity`` and
record-group scoping for ``search(entity_ids)``)."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.actions.knowledge_graph.ops import entity_records
from app.agents.actions.knowledge_graph.ops.entity_filters import ENTITY_INDEX_CACHE_KEY
from app.agents.actions.knowledge_graph.ops.entity_records import (
    LOOKUP_FAILED_MSG,
    NO_ACCESSIBLE_RECORDS_MSG,
    NO_FURTHER_RECORDS_MSG,
    execute_find_records_by_entity,
    resolve_entity_virtual_ids,
)
from app.modules.retrieval.entity_permissions import (
    EntityAccessContext,
    EntityAccessError,
    EntityRecordPage,
)

CONTEXT = EntityAccessContext(
    org_id="org-1",
    user_key="ukey",
    app_level_app_ids=frozenset({"kb-1"}),
    record_level_app_ids=frozenset({"conf-1"}),
    record_group_ids=frozenset(),
    app_names={"kb-1": "Team KB", "conf-1": "Confluence"},
)


def _state(**overrides) -> dict:
    state = {"org_id": "org-1", "user_id": "user-1", "graph_provider": MagicMock()}
    state.update(overrides)
    return state


def _row(key: str, **extra: object) -> dict:
    return {"_key": key, "recordName": f"doc {key}", "recordType": "FILE", "connectorId": "conf-1", **extra}


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch) -> tuple[AsyncMock, AsyncMock]:
    access = AsyncMock(return_value=CONTEXT)
    listing = AsyncMock(return_value=EntityRecordPage(records=[], next_cursor=None))
    monkeypatch.setattr(entity_records, "get_entity_access_context", access)
    monkeypatch.setattr(entity_records, "list_accessible_entity_records", listing)
    return access, listing


class TestFindRecordsByEntity:
    @pytest.mark.asyncio
    async def test_type_required_when_not_indexed(self, patched) -> None:
        ok, text = await execute_find_records_by_entity(_state(), "t1")
        assert ok is False
        assert "entity_type is required" in text
        patched[1].assert_not_called()

    @pytest.mark.asyncio
    async def test_unsupported_type_rejected(self, patched) -> None:
        ok, _ = await execute_find_records_by_entity(_state(), "t1", entity_type="person")
        assert ok is False

    @pytest.mark.asyncio
    async def test_type_and_name_come_from_search_entities_index(self, patched) -> None:
        patched[1].return_value = EntityRecordPage(records=[_row("r1")], next_cursor=None)
        state = _state(**{ENTITY_INDEX_CACHE_KEY: {"t1": {"type": "topic", "name": "Roadmap"}}})

        ok, text = await execute_find_records_by_entity(state, "t1")

        assert ok is True
        assert 'topic "Roadmap"' in text
        _, kwargs = patched[1].call_args
        assert kwargs["entity_type"] == "topic"

    @pytest.mark.asyncio
    async def test_no_rows_is_the_same_response_as_no_access(self, patched) -> None:
        ok, text = await execute_find_records_by_entity(_state(), "rg-x", entity_type="record_group")
        assert (ok, text) == (True, NO_ACCESSIBLE_RECORDS_MSG)

    @pytest.mark.asyncio
    async def test_empty_page_past_the_end_does_not_claim_the_entity_is_empty(
        self, patched
    ) -> None:
        """Saying "no accessible records" on page 2 contradicts the page of
        results the caller was just shown."""
        ok, text = await execute_find_records_by_entity(
            _state(), "t1", entity_type="topic", cursor="40",
        )
        assert (ok, text) == (True, NO_FURTHER_RECORDS_MSG)

    @pytest.mark.asyncio
    async def test_access_failure_is_a_failed_call(self, patched) -> None:
        patched[1].side_effect = EntityAccessError("db down")
        ok, text = await execute_find_records_by_entity(_state(), "t1", entity_type="topic")
        assert (ok, text) == (False, LOOKUP_FAILED_MSG)

    @pytest.mark.asyncio
    async def test_invalid_cursor_is_reported(self, patched) -> None:
        patched[1].side_effect = ValueError("Invalid cursor 'x'")
        ok, text = await execute_find_records_by_entity(_state(), "t1", entity_type="topic", cursor="x")
        assert ok is False
        assert "Invalid cursor" in text

    @pytest.mark.asyncio
    async def test_renders_rows_cursor_and_remembers_ids(self, patched) -> None:
        patched[1].return_value = EntityRecordPage(
            records=[_row("r1", webUrl="https://x/1", sourceLastModifiedTimestamp=1_767_225_600_000)],
            next_cursor="40",
        )
        state = _state()

        ok, text = await execute_find_records_by_entity(
            state, "t1", entity_type="Topic", record_types=["file"], limit=500,
        )

        assert ok is True
        assert "- [FILE] doc r1 | record_id=r1 | app: Confluence | modified: 2026-01-01 | url=https://x/1" in text
        assert 'cursor="40"' in text
        assert "this topic" in text
        assert state["known_record_ids"] == {"r1"}
        _, kwargs = patched[1].call_args
        assert kwargs["record_types"] == ["FILE"]
        assert kwargs["limit"] == 50

    @pytest.mark.asyncio
    async def test_shortened_record_id_is_resolved(self, patched) -> None:
        state = _state(enable_record_id_shortening=True)
        from app.utils.chat_helpers import get_record_id_shortener_if_enabled
        short = get_record_id_shortener_if_enabled(state).get_or_create_short_id("rec-9")
        patched[1].return_value = EntityRecordPage(records=[_row("rec-9")], next_cursor=None)

        ok, text = await execute_find_records_by_entity(state, short, entity_type="record")

        assert ok is True
        _, kwargs = patched[1].call_args
        assert kwargs["entity_id"] == "rec-9"
        assert f"record_id={short}" in text


class TestResolveEntityVirtualIds:
    @pytest.mark.asyncio
    async def test_dedupes_virtual_ids_across_entities(self, patched) -> None:
        patched[1].side_effect = [
            EntityRecordPage(records=[_row("r1", virtualRecordId="v1"), _row("r2", virtualRecordId="v2")], next_cursor=None),
            EntityRecordPage(records=[_row("r3", virtualRecordId="v1")], next_cursor=None),
        ]

        ids = await resolve_entity_virtual_ids(
            _state(), [("rg-1", "record_group"), ("s1", "subcategory"), ("rg-1", "record_group")],
        )

        assert ids == ["v1", "v2"]
        assert patched[1].await_count == 2
        assert patched[1].call_args_list[1].kwargs["entity_type"] == "subcategory"

    @pytest.mark.asyncio
    async def test_failure_propagates(self, patched) -> None:
        patched[1].side_effect = EntityAccessError("db down")
        with pytest.raises(EntityAccessError):
            await resolve_entity_virtual_ids(_state(), [("rg-1", "record_group")])
