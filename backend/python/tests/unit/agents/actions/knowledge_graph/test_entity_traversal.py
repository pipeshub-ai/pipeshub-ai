"""Tests for ``app.agents.actions.knowledge_graph.ops.entity_traversal`` —
the progressive ``expand_neighbors`` and ``get_relationships`` tools.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from app.agents.actions.knowledge_graph.ops.entity_traversal import (
    execute_expand_neighbors,
    execute_get_relationships,
)


class TestExecuteExpandNeighborsGuards:
    @pytest.mark.asyncio
    async def test_no_state(self) -> None:
        result = await execute_expand_neighbors(None, "d1", "department")
        parsed = json.loads(result)
        assert parsed["status"] == "error"

    @pytest.mark.asyncio
    async def test_missing_entity_id(self) -> None:
        result = await execute_expand_neighbors({"org_id": "o1"}, None, "department")
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "required" in parsed["message"]

    @pytest.mark.asyncio
    async def test_missing_entity_type(self) -> None:
        result = await execute_expand_neighbors({"org_id": "o1"}, "d1", None)
        parsed = json.loads(result)
        assert parsed["status"] == "error"

    @pytest.mark.asyncio
    async def test_unsupported_entity_type(self) -> None:
        result = await execute_expand_neighbors({"org_id": "o1"}, "x1", "record_group")
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "Unsupported entity_type" in parsed["message"]

    @pytest.mark.asyncio
    async def test_no_graph_provider(self) -> None:
        result = await execute_expand_neighbors({"org_id": "o1"}, "d1", "department")
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "not available" in parsed["message"]

    @pytest.mark.asyncio
    async def test_graph_provider_raises(self) -> None:
        graph_provider = AsyncMock()
        graph_provider.get_entity_relationships.side_effect = RuntimeError("boom")
        state = {"org_id": "o1", "graph_provider": graph_provider}
        result = await execute_expand_neighbors(state, "d1", "department")
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "boom" in parsed["message"]


class TestExecuteExpandNeighborsHappyPath:
    @pytest.mark.asyncio
    async def test_returns_relationships_shape(self) -> None:
        graph_provider = AsyncMock()
        graph_provider.get_entity_relationships.return_value = {
            "parentEntity": None,
            "childEntities": [],
            "relationshipTypes": [],
            "connectedRecordCount": 2,
            "connectedEntities": [{"entityType": "topic", "entityId": "t1", "name": "Billing"}],
            "connectedRecords": [{"recordId": "r1", "name": "Invoice", "recordType": "FILE"}],
        }
        state = {"org_id": "o1", "graph_provider": graph_provider}
        result = await execute_expand_neighbors(state, "d1", "department")
        parsed = json.loads(result)
        assert parsed["status"] == "success"
        assert parsed["entityId"] == "d1"
        assert parsed["entityType"] == "department"
        assert parsed["relationships"]["connectedRecordCount"] == 2
        graph_provider.get_entity_relationships.assert_awaited_once_with(
            org_id="o1", entity_id="d1", entity_type="department",
        )

    @pytest.mark.asyncio
    async def test_remembers_connected_record_ids(self) -> None:
        graph_provider = AsyncMock()
        graph_provider.get_entity_relationships.return_value = {
            "parentEntity": None, "childEntities": [], "relationshipTypes": [],
            "connectedRecordCount": 1, "connectedEntities": [],
            "connectedRecords": [{"recordId": "r1", "name": "Invoice", "recordType": "FILE"}],
        }
        state = {"org_id": "o1", "graph_provider": graph_provider}
        await execute_expand_neighbors(state, "d1", "department")
        assert state["known_record_ids"] == {"r1"}

    @pytest.mark.asyncio
    async def test_no_known_record_ids_when_none_connected(self) -> None:
        graph_provider = AsyncMock()
        graph_provider.get_entity_relationships.return_value = {
            "parentEntity": None, "childEntities": [], "relationshipTypes": [],
            "connectedRecordCount": 0, "connectedEntities": [], "connectedRecords": [],
        }
        state = {"org_id": "o1", "graph_provider": graph_provider}
        await execute_expand_neighbors(state, "d1", "department")
        assert "known_record_ids" not in state


class TestExecuteGetRelationshipsGuards:
    @pytest.mark.asyncio
    async def test_no_state(self) -> None:
        result = await execute_get_relationships(None, "d1", "department", "t1", "topic")
        parsed = json.loads(result)
        assert parsed["status"] == "error"

    @pytest.mark.asyncio
    async def test_missing_args(self) -> None:
        result = await execute_get_relationships({"org_id": "o1"}, "d1", "department", None, "topic")
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "required" in parsed["message"]

    @pytest.mark.asyncio
    async def test_unsupported_entity_type(self) -> None:
        result = await execute_get_relationships({"org_id": "o1"}, "x1", "record_group", "t1", "topic")
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "Unsupported entity_type" in parsed["message"]

    @pytest.mark.asyncio
    async def test_no_graph_provider(self) -> None:
        result = await execute_get_relationships(
            {"org_id": "o1"}, "d1", "department", "t1", "topic",
        )
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "not available" in parsed["message"]

    @pytest.mark.asyncio
    async def test_graph_provider_raises(self) -> None:
        graph_provider = AsyncMock()
        graph_provider.get_entity_pair_relationships.side_effect = RuntimeError("boom")
        state = {"org_id": "o1", "graph_provider": graph_provider}
        result = await execute_get_relationships(state, "d1", "department", "t1", "topic")
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "boom" in parsed["message"]


class TestExecuteGetRelationshipsHappyPath:
    @pytest.mark.asyncio
    async def test_returns_pair_relationship_shape(self) -> None:
        graph_provider = AsyncMock()
        graph_provider.get_entity_pair_relationships.return_value = {
            "directEdges": [{"edgeType": "CATEGORY_HIERARCHY", "edgeCollection": "interCategoryRelations", "direction": "outbound"}],
            "sharedRecordCount": 3,
            "sharedRecords": [{"recordId": "r1", "name": "Doc", "recordType": "FILE"}],
        }
        state = {"org_id": "o1", "graph_provider": graph_provider}
        result = await execute_get_relationships(state, "c1", "category", "s1", "subcategory")
        parsed = json.loads(result)
        assert parsed["status"] == "success"
        assert parsed["sourceEntityId"] == "c1"
        assert parsed["targetEntityId"] == "s1"
        assert parsed["sharedRecordCount"] == 3
        graph_provider.get_entity_pair_relationships.assert_awaited_once_with(
            org_id="o1",
            source_entity_id="c1",
            source_entity_type="category",
            target_entity_id="s1",
            target_entity_type="subcategory",
        )

    @pytest.mark.asyncio
    async def test_remembers_shared_record_ids(self) -> None:
        graph_provider = AsyncMock()
        graph_provider.get_entity_pair_relationships.return_value = {
            "directEdges": [], "sharedRecordCount": 1,
            "sharedRecords": [{"recordId": "r1", "name": "Doc", "recordType": "FILE"}],
        }
        state = {"org_id": "o1", "graph_provider": graph_provider}
        await execute_get_relationships(state, "d1", "department", "t1", "topic")
        assert state["known_record_ids"] == {"r1"}

    @pytest.mark.asyncio
    async def test_no_known_record_ids_when_no_shared_records(self) -> None:
        graph_provider = AsyncMock()
        graph_provider.get_entity_pair_relationships.return_value = {
            "directEdges": [], "sharedRecordCount": 0, "sharedRecords": [],
        }
        state = {"org_id": "o1", "graph_provider": graph_provider}
        await execute_get_relationships(state, "d1", "department", "t1", "topic")
        assert "known_record_ids" not in state
