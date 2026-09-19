"""Unit tests for ``ops/entity_discovery.py`` (``search_entities``)."""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.actions.knowledge_graph.ops import entity_discovery
from app.agents.actions.knowledge_graph.ops.entity_discovery import (
    execute_search_entities,
)
from app.agents.actions.knowledge_graph.ops.entity_filters import (
    ENTITY_ID_FILTER_KEY_CACHE_KEY,
    ENTITY_INDEX_CACHE_KEY,
    RECORD_SCOPED_ENTITY_CACHE_KEY,
)
from app.modules.retrieval.entity_permissions import (
    EntityAccessContext,
    EntityAccessError,
    EntityHit,
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
    state = {
        "org_id": "org-1",
        "user_id": "user-1",
        "graph_provider": MagicMock(),
        "entity_vector_store": MagicMock(),
        "apps": ["conf-1"],
        "kb": ["kb-1"],
    }
    state.update(overrides)
    return state


def _hit(entity_id, entity_type, score=0.9, records=(), more=False) -> EntityHit:
    return EntityHit(
        entity_id=entity_id, entity_type=entity_type, name=f"name {entity_id}",
        score=score, records=list(records), more_records=more,
    )


def _row(key: str, connector_id: str = "conf-1") -> dict:
    return {
        "_key": key, "recordName": f"doc {key}", "recordType": "FILE",
        "connectorId": connector_id, "sourceLastModifiedTimestamp": 1_767_225_600_000,
    }


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch) -> tuple[AsyncMock, AsyncMock]:
    access = AsyncMock(return_value=CONTEXT)
    search = AsyncMock(return_value=[])
    monkeypatch.setattr(entity_discovery, "get_entity_access_context", access)
    monkeypatch.setattr(entity_discovery, "search_entities_for_user", search)
    return access, search


class TestGuards:
    @pytest.mark.asyncio
    async def test_blank_query_fails(self, patched) -> None:
        ok, text = await execute_search_entities(_state(), "  ")
        assert ok is False
        assert json.loads(text)["status"] == "error"

    @pytest.mark.asyncio
    async def test_missing_store_fails(self, patched) -> None:
        ok, _ = await execute_search_entities(_state(entity_vector_store=None), "legal")
        assert ok is False

    @pytest.mark.asyncio
    async def test_only_unknown_entity_types_fails(self, patched) -> None:
        ok, text = await execute_search_entities(_state(), "legal", entity_types=["person"])
        assert ok is False
        assert "Unsupported" in json.loads(text)["message"]
        patched[1].assert_not_called()

    @pytest.mark.asyncio
    async def test_access_failure_is_a_failed_call_not_empty_results(self, patched) -> None:
        patched[0].side_effect = EntityAccessError("db down")
        ok, text = await execute_search_entities(_state(), "legal")
        assert ok is False
        assert "try again" in json.loads(text)["message"]

    @pytest.mark.asyncio
    async def test_search_failure_is_a_failed_call(self, patched) -> None:
        patched[1].side_effect = EntityAccessError("vector down")
        ok, _ = await execute_search_entities(_state(), "legal")
        assert ok is False


class TestSearch:
    @pytest.mark.asyncio
    async def test_scope_comes_from_agent_sources(self, patched) -> None:
        await execute_search_entities(_state(), "legal", entity_types=["topic", "person"], top_k=99)

        _, kwargs = patched[0].call_args
        assert kwargs["source_ids"] == ["conf-1", "kb-1"]
        _, search_kwargs = patched[1].call_args
        assert search_kwargs["entity_types"] == ["topic"]
        assert search_kwargs["top_k"] == 25

    @pytest.mark.asyncio
    async def test_no_hits_is_success(self, patched) -> None:
        ok, text = await execute_search_entities(_state(), "legal")
        assert ok is True
        assert json.loads(text)["results"] == []

    @pytest.mark.asyncio
    async def test_results_shape_previews_and_caches(self, patched) -> None:
        patched[1].return_value = [
            _hit("t1", "topic", records=[_row("r1"), _row("r2", "kb-1")], more=True),
            _hit("rg-1", "record_group", records=[_row("r3")]),
            _hit("rec-9", "record", records=[_row("rec-9")]),
            _hit("d1", "department", records=[_row("r4")]),
            _hit("c1", "category", records=[_row("r5")]),
        ]
        state = _state()

        ok, text = await execute_search_entities(state, "legal")

        assert ok is True
        results = json.loads(text)["results"]
        topic = results[0]
        assert topic["apps"] == ["Confluence", "Team KB"]
        assert [r["recordId"] for r in topic["records"]] == ["r1", "r2"]
        assert topic["records"][0]["modified"] == "2026-01-01"
        assert topic["moreRecords"] is True
        assert "records" not in results[2]
        assert "records" in results[3]
        assert "records" not in results[4]
        assert "connectedEntities" not in topic

        assert state["known_record_ids"] == {"r1", "r2", "r3", "rec-9", "r4"}
        assert state[ENTITY_INDEX_CACHE_KEY]["rec-9"]["type"] == "record"
        assert state[ENTITY_ID_FILTER_KEY_CACHE_KEY]["t1"] == ("topics", "name t1")
        assert state[RECORD_SCOPED_ENTITY_CACHE_KEY] == {"rg-1": "record_group"}

    @pytest.mark.asyncio
    async def test_record_ids_go_through_shortener(self, patched) -> None:
        patched[1].return_value = [
            _hit("rec-9", "record", records=[_row("rec-9")]),
            _hit("t1", "topic", records=[_row("r1")]),
        ]
        state = _state(enable_record_id_shortening=True)

        _, text = await execute_search_entities(state, "legal")

        results = json.loads(text)["results"]
        assert results[0]["entityId"] == "R1"
        assert results[1]["entityId"] == "t1"
        assert results[1]["records"][0]["recordId"] == "R2"
