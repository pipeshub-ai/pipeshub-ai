"""Tests for the ``KnowledgeGraph`` delegation wiring of the ``search_entities``
and ``find_records_by_entity`` tools, plus their result-summary helpers.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.actions.knowledge_graph.knowledge_graph import (
    KnowledgeGraph,
    _find_records_by_entity_result_summary,
    _search_entities_result_summary,
)


def _tool_result(content: str = "", is_error: bool = False) -> SimpleNamespace:
    return SimpleNamespace(content=content, is_error=is_error)


class TestSearchEntitiesResultSummary:
    def test_empty_content(self) -> None:
        assert _search_entities_result_summary({}, _tool_result("")) is None

    def test_failed_call_reports_message(self) -> None:
        payload = json.dumps({"status": "error", "message": "Entity search failed — try again."})
        result = _search_entities_result_summary({}, _tool_result(payload, is_error=True))
        assert result == "Entity search failed — try again."

    def test_no_results(self) -> None:
        payload = json.dumps({"status": "success", "message": "No accessible entities matched", "results": []})
        assert _search_entities_result_summary({}, _tool_result(payload)) == "No accessible entities matched"

    def test_names_first_three(self) -> None:
        results = [{"name": n} for n in ("Legal", "Finance", "Roadmap", "Hiring")]
        payload = json.dumps({"status": "success", "results": results})
        result = _search_entities_result_summary({}, _tool_result(payload))
        assert result == "Found 4 entities: Legal, Finance, Roadmap (+1 more)"


class TestFindRecordsByEntityResultSummary:
    def test_empty_content(self) -> None:
        assert _find_records_by_entity_result_summary({}, _tool_result("")) is None

    def test_error_result_uses_first_line(self) -> None:
        result = _find_records_by_entity_result_summary(
            {}, _tool_result("Lookup failed — try again.", is_error=True),
        )
        assert result == "Lookup failed: Lookup failed — try again."

    def test_happy_path_returns_first_line(self) -> None:
        text = 'Records connected to topic "Legal", newest first (3 shown):\n- [FILE] Doc'
        result = _find_records_by_entity_result_summary({}, _tool_result(text))
        assert result == 'Records connected to topic "Legal", newest first (3 shown):'


@pytest.mark.asyncio
class TestKnowledgeGraphSearchEntitiesDelegation:
    async def test_delegates_to_ops_entity_discovery(self) -> None:
        tool = KnowledgeGraph(state={"org_id": "o1"})
        with patch(
            "app.agents.actions.knowledge_graph.ops.entity_discovery.execute_search_entities",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = (True, '{"status": "success", "results": []}')
            result = await tool.search_entities(query="legal", entity_types=["department"], top_k=5)
        mock_exec.assert_awaited_once_with(
            tool.state, query="legal", entity_types=["department"], top_k=5,
        )
        assert result == (True, '{"status": "success", "results": []}')


@pytest.mark.asyncio
class TestKnowledgeGraphFindRecordsByEntityDelegation:
    async def test_delegates_to_ops_entity_records(self) -> None:
        tool = KnowledgeGraph(state={"org_id": "o1"})
        with patch(
            "app.agents.actions.knowledge_graph.ops.entity_records.execute_find_records_by_entity",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = (True, "Records connected to this department")
            result = await tool.find_records_by_entity(
                entity_id="d1", entity_type="department", record_types=["FILE"], limit=10, cursor="40",
            )
        mock_exec.assert_awaited_once_with(
            tool.state,
            entity_id="d1",
            entity_type="department",
            record_types=["FILE"],
            limit=10,
            cursor="40",
        )
        assert result == (True, "Records connected to this department")
