"""Tests for ``app.agents.actions.knowledge_graph.ops.entity_search``."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.actions.knowledge_graph.ops.entity_search import (
    execute_resolve_entity_filters,
)


class TestExecuteResolveEntityFiltersGuards:
    @pytest.mark.asyncio
    async def test_no_query(self) -> None:
        result = await execute_resolve_entity_filters({}, None)
        parsed = json.loads(result)
        assert parsed["status"] == "error"

    @pytest.mark.asyncio
    async def test_blank_query(self) -> None:
        result = await execute_resolve_entity_filters({}, "   ")
        parsed = json.loads(result)
        assert parsed["status"] == "error"

    @pytest.mark.asyncio
    async def test_no_state(self) -> None:
        result = await execute_resolve_entity_filters(None, "legal")
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "not initialized" in parsed["message"]

    @pytest.mark.asyncio
    async def test_no_entity_vector_store(self) -> None:
        state = {"entity_vector_store": None, "org_id": "o1"}
        result = await execute_resolve_entity_filters(state, "legal")
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "not available" in parsed["message"]

    @pytest.mark.asyncio
    async def test_search_entities_raises(self) -> None:
        evs = AsyncMock()
        evs.search_entities.side_effect = RuntimeError("boom")
        state = {"entity_vector_store": evs, "org_id": "o1"}
        result = await execute_resolve_entity_filters(state, "legal")
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "boom" in parsed["message"]


class TestExecuteResolveEntityFiltersHappyPath:
    @pytest.mark.asyncio
    async def test_no_matches(self) -> None:
        evs = AsyncMock()
        evs.search_entities.return_value = []
        state = {"entity_vector_store": evs, "org_id": "o1"}
        result = await execute_resolve_entity_filters(state, "legal")
        parsed = json.loads(result)
        assert parsed["status"] == "success"
        assert parsed["results"] == []

    @pytest.mark.asyncio
    async def test_returns_results_and_filterable_flag(self) -> None:
        evs = AsyncMock()
        evs.search_entities.return_value = [
            {"entityId": "d1", "entityType": "department", "name": "Legal", "score": 0.9},
            {"entityId": "p1", "entityType": "person", "name": "Jane Doe", "score": 0.8},
        ]
        state = {"entity_vector_store": evs, "org_id": "o1"}
        result = await execute_resolve_entity_filters(state, "legal team")
        parsed = json.loads(result)
        assert parsed["status"] == "success"
        by_id = {r["entityId"]: r for r in parsed["results"]}
        assert by_id["d1"]["filterable"] is True
        assert by_id["p1"]["filterable"] is False

    @pytest.mark.asyncio
    async def test_caches_entity_id_to_filter_key_and_name_for_search_tool(self) -> None:
        evs = AsyncMock()
        evs.search_entities.return_value = [
            {"entityId": "d1", "entityType": "department", "name": "Legal", "score": 0.9},
        ]
        state = {"entity_vector_store": evs, "org_id": "o1"}
        await execute_resolve_entity_filters(state, "legal")
        assert state["_kg_entity_id_filter_key"] == {"d1": ("departments", "Legal")}

    @pytest.mark.asyncio
    async def test_passes_through_aliases_and_parent_entity(self) -> None:
        """aliases/parentEntityId/parentEntityType are already returned by
        EntityVectorStore.search_entities() — must not be dropped before
        reaching the agent (they're free context: no extra graph call)."""
        evs = AsyncMock()
        evs.search_entities.return_value = [
            {
                "entityId": "s1", "entityType": "subcategory", "name": "Backend",
                "score": 0.9, "aliases": ["Server-side"],
                "parentEntityId": "c1", "parentEntityType": "category",
            },
        ]
        state = {"entity_vector_store": evs, "org_id": "o1"}
        result = await execute_resolve_entity_filters(state, "backend")
        parsed = json.loads(result)
        entity = parsed["results"][0]
        assert entity["aliases"] == ["Server-side"]
        assert entity["parentEntityId"] == "c1"
        assert entity["parentEntityType"] == "category"

    @pytest.mark.asyncio
    async def test_bounds_top_k(self) -> None:
        evs = AsyncMock()
        evs.search_entities.return_value = []
        state = {"entity_vector_store": evs, "org_id": "o1"}
        await execute_resolve_entity_filters(state, "legal", top_k=1000)
        _, kwargs = evs.search_entities.call_args
        assert kwargs["top_k"] == 25

    @pytest.mark.asyncio
    async def test_passes_entity_types_through(self) -> None:
        evs = AsyncMock()
        evs.search_entities.return_value = []
        state = {"entity_vector_store": evs, "org_id": "o1"}
        await execute_resolve_entity_filters(state, "legal", entity_types=["department"])
        args, kwargs = evs.search_entities.call_args
        assert kwargs["entity_types"] == ["department"]

    @pytest.mark.asyncio
    async def test_remembers_mentions_best_effort(self) -> None:
        evs = AsyncMock()
        evs.search_entities.return_value = [
            {"entityId": "d1", "entityType": "department", "name": "Legal", "score": 0.9},
        ]
        state = {"entity_vector_store": evs, "org_id": "o1"}
        await execute_resolve_entity_filters(state, "legal")
        assert state.get("_kg_mentions") == [{"id": "d1", "name": "Legal", "type": "department"}]

    @pytest.mark.asyncio
    async def test_mention_tracking_failure_does_not_break_tool(self, monkeypatch) -> None:
        import app.agents.actions.knowledge_graph.ops.entity_search as mod

        def _boom(*_args, **_kwargs):
            raise RuntimeError("mention tracking broke")

        monkeypatch.setattr(mod, "_remember_mentions", _boom)
        evs = AsyncMock()
        evs.search_entities.return_value = [
            {"entityId": "d1", "entityType": "department", "name": "Legal", "score": 0.9},
        ]
        state = {"entity_vector_store": evs, "org_id": "o1"}
        result = await execute_resolve_entity_filters(state, "legal")
        parsed = json.loads(result)
        assert parsed["status"] == "success"
