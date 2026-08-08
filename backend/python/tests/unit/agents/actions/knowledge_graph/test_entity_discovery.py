"""Tests for ``app.agents.actions.knowledge_graph.ops.entity_discovery`` —
the essential (turn-0) ``search_entities`` tool.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from app.agents.actions.knowledge_graph.ops.entity_discovery import (
    execute_search_entities,
)


class TestExecuteSearchEntitiesGuards:
    @pytest.mark.asyncio
    async def test_no_query(self) -> None:
        result = await execute_search_entities({}, None)
        parsed = json.loads(result)
        assert parsed["status"] == "error"

    @pytest.mark.asyncio
    async def test_blank_query(self) -> None:
        result = await execute_search_entities({}, "   ")
        parsed = json.loads(result)
        assert parsed["status"] == "error"

    @pytest.mark.asyncio
    async def test_no_state(self) -> None:
        result = await execute_search_entities(None, "legal")
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "not initialized" in parsed["message"]

    @pytest.mark.asyncio
    async def test_no_entity_vector_store(self) -> None:
        state = {"entity_vector_store": None, "org_id": "o1"}
        result = await execute_search_entities(state, "legal")
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "not available" in parsed["message"]

    @pytest.mark.asyncio
    async def test_search_entities_raises(self) -> None:
        evs = AsyncMock()
        evs.search_entities.side_effect = RuntimeError("boom")
        state = {"entity_vector_store": evs, "org_id": "o1"}
        result = await execute_search_entities(state, "legal")
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "boom" in parsed["message"]


class TestExecuteSearchEntitiesHappyPath:
    @pytest.mark.asyncio
    async def test_no_matches(self) -> None:
        evs = AsyncMock()
        evs.search_entities.return_value = []
        state = {"entity_vector_store": evs, "org_id": "o1"}
        result = await execute_search_entities(state, "legal", include_relationships=False)
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
        result = await execute_search_entities(state, "legal team", include_relationships=False)
        parsed = json.loads(result)
        assert parsed["status"] == "success"
        by_id = {r["entityId"]: r for r in parsed["results"]}
        assert by_id["d1"]["filterable"] is True
        assert by_id["p1"]["filterable"] is False

    @pytest.mark.asyncio
    async def test_passes_through_aliases_and_parent_entity(self) -> None:
        evs = AsyncMock()
        evs.search_entities.return_value = [
            {
                "entityId": "s1", "entityType": "subcategory", "name": "Backend",
                "score": 0.9, "aliases": ["Server-side"],
                "parentEntityId": "c1", "parentEntityType": "category",
            },
        ]
        state = {"entity_vector_store": evs, "org_id": "o1"}
        result = await execute_search_entities(state, "backend", include_relationships=False)
        parsed = json.loads(result)
        entity = parsed["results"][0]
        assert entity["aliases"] == ["Server-side"]
        assert entity["parentEntityId"] == "c1"
        assert entity["parentEntityType"] == "category"

    @pytest.mark.asyncio
    async def test_does_not_cache_filter_key(self) -> None:
        """Unlike resolve_entity_filters, search_entities must have no
        side-effect on the entity-id-to-filter-key cache that ops/search.py
        and find_records_by_entity rely on."""
        evs = AsyncMock()
        evs.search_entities.return_value = [
            {"entityId": "d1", "entityType": "department", "name": "Legal", "score": 0.9},
        ]
        state = {"entity_vector_store": evs, "org_id": "o1"}
        await execute_search_entities(state, "legal", include_relationships=False)
        assert "_kg_entity_id_filter_key" not in state

    @pytest.mark.asyncio
    async def test_bounds_top_k(self) -> None:
        evs = AsyncMock()
        evs.search_entities.return_value = []
        state = {"entity_vector_store": evs, "org_id": "o1"}
        await execute_search_entities(state, "legal", top_k=1000, include_relationships=False)
        _, kwargs = evs.search_entities.call_args
        assert kwargs["top_k"] == 25

    @pytest.mark.asyncio
    async def test_passes_entity_types_through(self) -> None:
        evs = AsyncMock()
        evs.search_entities.return_value = []
        state = {"entity_vector_store": evs, "org_id": "o1"}
        await execute_search_entities(state, "legal", entity_types=["department"], include_relationships=False)
        _, kwargs = evs.search_entities.call_args
        assert kwargs["entity_types"] == ["department"]

    @pytest.mark.asyncio
    async def test_remembers_mentions_best_effort(self) -> None:
        evs = AsyncMock()
        evs.search_entities.return_value = [
            {"entityId": "d1", "entityType": "department", "name": "Legal", "score": 0.9},
        ]
        state = {"entity_vector_store": evs, "org_id": "o1"}
        await execute_search_entities(state, "legal", include_relationships=False)
        assert state.get("_kg_mentions") == [{"id": "d1", "name": "Legal", "type": "department"}]

    @pytest.mark.asyncio
    async def test_mention_tracking_failure_does_not_break_tool(self, monkeypatch) -> None:
        import sys

        # ``hooks/__init__.py`` re-exports a same-named ``mention_binding``
        # hook function, shadowing the submodule as a package attribute --
        # go through sys.modules directly so we patch the actual module the
        # deferred `from ... import remember_entity_mentions` resolves to.
        mention_mod = sys.modules["app.agents.agent_loop.hooks.mention_binding"]

        def _boom(*_args, **_kwargs) -> None:
            raise RuntimeError("mention tracking broke")

        monkeypatch.setattr(mention_mod, "remember_entity_mentions", _boom)
        evs = AsyncMock()
        evs.search_entities.return_value = [
            {"entityId": "d1", "entityType": "department", "name": "Legal", "score": 0.9},
        ]
        state = {"entity_vector_store": evs, "org_id": "o1"}
        result = await execute_search_entities(state, "legal", include_relationships=False)
        parsed = json.loads(result)
        assert parsed["status"] == "success"


class TestExecuteSearchEntitiesRelationshipEnrichment:
    @pytest.mark.asyncio
    async def test_enriches_top_results_when_graph_provider_present(self) -> None:
        evs = AsyncMock()
        evs.search_entities.return_value = [
            {"entityId": "t1", "entityType": "topic", "name": "Billing", "score": 0.9},
        ]
        graph_provider = AsyncMock()
        graph_provider.get_entity_relationships.return_value = {
            "parentEntity": None,
            "childEntities": [],
            "relationshipTypes": [],
            "connectedRecordCount": 3,
        }
        state = {"entity_vector_store": evs, "org_id": "o1", "graph_provider": graph_provider}
        result = await execute_search_entities(state, "billing")
        parsed = json.loads(result)
        entity = parsed["results"][0]
        assert entity["relationships"]["connectedRecordCount"] == 3
        graph_provider.get_entity_relationships.assert_awaited_once_with(
            org_id="o1", entity_id="t1", entity_type="topic",
        )

    @pytest.mark.asyncio
    async def test_skips_enrichment_when_include_relationships_false(self) -> None:
        evs = AsyncMock()
        evs.search_entities.return_value = [
            {"entityId": "t1", "entityType": "topic", "name": "Billing", "score": 0.9},
        ]
        graph_provider = AsyncMock()
        state = {"entity_vector_store": evs, "org_id": "o1", "graph_provider": graph_provider}
        result = await execute_search_entities(state, "billing", include_relationships=False)
        parsed = json.loads(result)
        assert "relationships" not in parsed["results"][0]
        graph_provider.get_entity_relationships.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_enrichment_without_graph_provider(self) -> None:
        evs = AsyncMock()
        evs.search_entities.return_value = [
            {"entityId": "t1", "entityType": "topic", "name": "Billing", "score": 0.9},
        ]
        state = {"entity_vector_store": evs, "org_id": "o1"}
        result = await execute_search_entities(state, "billing")
        parsed = json.loads(result)
        assert "relationships" not in parsed["results"][0]

    @pytest.mark.asyncio
    async def test_per_entity_enrichment_failure_is_swallowed(self) -> None:
        evs = AsyncMock()
        evs.search_entities.return_value = [
            {"entityId": "t1", "entityType": "topic", "name": "Billing", "score": 0.9},
        ]
        graph_provider = AsyncMock()
        graph_provider.get_entity_relationships.side_effect = RuntimeError("graph down")
        state = {"entity_vector_store": evs, "org_id": "o1", "graph_provider": graph_provider}
        result = await execute_search_entities(state, "billing")
        parsed = json.loads(result)
        assert parsed["status"] == "success"
        assert "relationships" not in parsed["results"][0]

    @pytest.mark.asyncio
    async def test_caps_enrichment_to_max_results(self) -> None:
        evs = AsyncMock()
        evs.search_entities.return_value = [
            {"entityId": f"t{i}", "entityType": "topic", "name": f"Topic {i}", "score": 0.9}
            for i in range(8)
        ]
        graph_provider = AsyncMock()
        graph_provider.get_entity_relationships.return_value = {
            "parentEntity": None, "childEntities": [], "relationshipTypes": [],
            "connectedRecordCount": 0,
        }
        state = {"entity_vector_store": evs, "org_id": "o1", "graph_provider": graph_provider}
        result = await execute_search_entities(state, "topic")
        parsed = json.loads(result)
        enriched = [r for r in parsed["results"] if "relationships" in r]
        assert len(enriched) == 5


class TestExecuteSearchEntitiesRememberRecordIds:
    @pytest.mark.asyncio
    async def test_remembers_connected_record_ids_from_enrichment(self) -> None:
        evs = AsyncMock()
        evs.search_entities.return_value = [
            {"entityId": "t1", "entityType": "topic", "name": "Billing", "score": 0.9},
        ]
        graph_provider = AsyncMock()
        graph_provider.get_entity_relationships.return_value = {
            "parentEntity": None, "childEntities": [], "relationshipTypes": [],
            "connectedRecordCount": 2,
            "connectedEntities": [],
            "connectedRecords": [
                {"recordId": "r1", "name": "Invoice", "recordType": "FILE"},
                {"recordId": "r2", "name": "Receipt", "recordType": "FILE"},
            ],
        }
        state = {"entity_vector_store": evs, "org_id": "o1", "graph_provider": graph_provider}
        await execute_search_entities(state, "billing")
        assert state["known_record_ids"] == {"r1", "r2"}

    @pytest.mark.asyncio
    async def test_no_known_record_ids_key_when_no_connected_records(self) -> None:
        evs = AsyncMock()
        evs.search_entities.return_value = [
            {"entityId": "t1", "entityType": "topic", "name": "Billing", "score": 0.9},
        ]
        graph_provider = AsyncMock()
        graph_provider.get_entity_relationships.return_value = {
            "parentEntity": None, "childEntities": [], "relationshipTypes": [],
            "connectedRecordCount": 0, "connectedEntities": [], "connectedRecords": [],
        }
        state = {"entity_vector_store": evs, "org_id": "o1", "graph_provider": graph_provider}
        await execute_search_entities(state, "billing")
        assert "known_record_ids" not in state

    @pytest.mark.asyncio
    async def test_skipped_when_include_relationships_false(self) -> None:
        evs = AsyncMock()
        evs.search_entities.return_value = [
            {"entityId": "t1", "entityType": "topic", "name": "Billing", "score": 0.9},
        ]
        graph_provider = AsyncMock()
        state = {"entity_vector_store": evs, "org_id": "o1", "graph_provider": graph_provider}
        await execute_search_entities(state, "billing", include_relationships=False)
        assert "known_record_ids" not in state
        graph_provider.get_entity_relationships.assert_not_awaited()
