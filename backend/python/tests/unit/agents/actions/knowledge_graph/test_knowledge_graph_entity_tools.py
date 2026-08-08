"""Tests for the ``KnowledgeGraph`` delegation wiring of the essential
``search_entities`` and progressive ``find_records_by_entity``,
``expand_neighbors``, ``get_relationships`` entity tools, plus their
result-summary helpers.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.actions.knowledge_graph.knowledge_graph import (
    KnowledgeGraph,
    _expand_neighbors_result_summary,
    _find_records_by_entity_result_summary,
    _get_relationships_result_summary,
)


def _tool_result(content: str = "", is_error: bool = False) -> SimpleNamespace:
    return SimpleNamespace(content=content, is_error=is_error)


class TestFindRecordsByEntityResultSummary:
    def test_empty_content(self) -> None:
        assert _find_records_by_entity_result_summary({}, _tool_result("")) is None

    def test_error_result_uses_first_line(self) -> None:
        result = _find_records_by_entity_result_summary(
            {}, _tool_result("Lookup failed — try again.", is_error=True),
        )
        assert result == "Lookup failed: Lookup failed — try again."

    def test_no_accessible_records(self) -> None:
        result = _find_records_by_entity_result_summary(
            {}, _tool_result('No accessible records found for "Legal".'),
        )
        assert result == 'No accessible records found for "Legal".'

    def test_entity_not_found(self) -> None:
        result = _find_records_by_entity_result_summary(
            {}, _tool_result("Entity 'd1' of type 'department' was not found."),
        )
        assert result == "Entity 'd1' of type 'department' was not found."

    def test_happy_path_returns_first_line(self) -> None:
        text = 'Found 3 records connected to department "Legal" (page 1):\n\n  [FILE] Doc'
        result = _find_records_by_entity_result_summary({}, _tool_result(text))
        assert result == 'Found 3 records connected to department "Legal" (page 1):'


@pytest.mark.asyncio
class TestKnowledgeGraphSearchEntitiesDelegation:
    async def test_delegates_to_ops_entity_discovery(self) -> None:
        tool = KnowledgeGraph(state={"org_id": "o1"})
        with patch(
            "app.agents.actions.knowledge_graph.ops.entity_discovery.execute_search_entities",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = '{"status": "success", "results": []}'
            result = await tool.search_entities(query="legal", entity_types=["department"], top_k=5)
        mock_exec.assert_awaited_once_with(
            tool.state, query="legal", entity_types=["department"], top_k=5,
        )
        assert result == '{"status": "success", "results": []}'


@pytest.mark.asyncio
class TestKnowledgeGraphFindRecordsByEntityDelegation:
    async def test_delegates_to_ops_entity_records(self) -> None:
        tool = KnowledgeGraph(state={"org_id": "o1"})
        with patch(
            "app.agents.actions.knowledge_graph.ops.entity_records.execute_find_records_by_entity",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = (True, "Found 1 record")
            result = await tool.find_records_by_entity(
                entity_id="d1", entity_type="department", record_types=["FILE"], page=2, limit=10,
            )
        mock_exec.assert_awaited_once_with(
            tool.state,
            entity_id="d1",
            entity_type="department",
            record_types=["FILE"],
            page=2,
            limit=10,
        )
        assert result == (True, "Found 1 record")


@pytest.mark.asyncio
class TestKnowledgeGraphExpandNeighborsDelegation:
    async def test_delegates_to_ops_entity_traversal(self) -> None:
        tool = KnowledgeGraph(state={"org_id": "o1"})
        with patch(
            "app.agents.actions.knowledge_graph.ops.entity_traversal.execute_expand_neighbors",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = '{"status": "success"}'
            result = await tool.expand_neighbors(entity_id="d1", entity_type="department")
        mock_exec.assert_awaited_once_with(
            tool.state, entity_id="d1", entity_type="department",
        )
        assert result == '{"status": "success"}'


@pytest.mark.asyncio
class TestKnowledgeGraphGetRelationshipsDelegation:
    async def test_delegates_to_ops_entity_traversal(self) -> None:
        tool = KnowledgeGraph(state={"org_id": "o1"})
        with patch(
            "app.agents.actions.knowledge_graph.ops.entity_traversal.execute_get_relationships",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = '{"status": "success"}'
            result = await tool.get_relationships(
                source_entity_id="d1", source_entity_type="department",
                target_entity_id="t1", target_entity_type="topic",
            )
        mock_exec.assert_awaited_once_with(
            tool.state,
            source_entity_id="d1",
            source_entity_type="department",
            target_entity_id="t1",
            target_entity_type="topic",
        )
        assert result == '{"status": "success"}'


class TestExpandNeighborsResultSummary:
    def test_empty_content(self) -> None:
        assert _expand_neighbors_result_summary({}, _tool_result("")) is None

    def test_error_result(self) -> None:
        result = _expand_neighbors_result_summary(
            {}, _tool_result(json.dumps({"status": "error", "message": "boom"})),
        )
        assert result == "boom"

    def test_happy_path_summarizes_counts(self) -> None:
        payload = {
            "status": "success",
            "relationships": {
                "parentEntity": {"entityId": "c1", "entityType": "category", "name": "Root"},
                "childEntities": [{"entityId": "s1"}],
                "connectedEntities": [{"entityId": "d1"}, {"entityId": "d2"}],
                "connectedRecordCount": 4,
            },
        }
        result = _expand_neighbors_result_summary({}, _tool_result(json.dumps(payload)))
        assert result == "Found 1 parent, 1 children, 2 related entities, 4 connected records"


class TestGetRelationshipsResultSummary:
    def test_empty_content(self) -> None:
        assert _get_relationships_result_summary({}, _tool_result("")) is None

    def test_error_result(self) -> None:
        result = _get_relationships_result_summary(
            {}, _tool_result(json.dumps({"status": "error", "message": "boom"})),
        )
        assert result == "boom"

    def test_no_connection_found(self) -> None:
        payload = {"status": "success", "directEdges": [], "sharedRecordCount": 0}
        result = _get_relationships_result_summary({}, _tool_result(json.dumps(payload)))
        assert result == "No direct connection or shared records found"

    def test_happy_path_summarizes_counts(self) -> None:
        payload = {
            "status": "success",
            "directEdges": [{"edgeType": "CATEGORY_HIERARCHY"}],
            "sharedRecordCount": 3,
        }
        result = _get_relationships_result_summary({}, _tool_result(json.dumps(payload)))
        assert result == "Found 1 direct edge, 3 shared records"
