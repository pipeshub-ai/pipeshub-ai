"""Tests for ``KnowledgeGraph.resolve_entity_filters`` delegation, the
``search()`` tool's ``entity_ids`` passthrough, and the tool's result
summary helper."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.actions.knowledge_graph.knowledge_graph import (
    KnowledgeGraph,
    _resolve_entity_filters_result_summary,
)


def _tool_result(content: str = "") -> SimpleNamespace:
    return SimpleNamespace(content=content, is_error=False)


class TestResolveEntityFiltersResultSummary:
    def test_empty_content(self) -> None:
        assert _resolve_entity_filters_result_summary({}, _tool_result("")) is None

    def test_error_status(self) -> None:
        result = _resolve_entity_filters_result_summary(
            {}, _tool_result('{"status": "error", "message": "boom"}'),
        )
        assert result == "boom"

    def test_no_results(self) -> None:
        result = _resolve_entity_filters_result_summary(
            {}, _tool_result('{"status": "success", "results": []}'),
        )
        assert result == "No matching entities found"

    def test_lists_entity_names(self) -> None:
        payload = (
            '{"status": "success", "results": ['
            '{"name": "Legal"}, {"name": "Finance"}]}'
        )
        result = _resolve_entity_filters_result_summary({}, _tool_result(payload))
        assert "Legal" in result and "Finance" in result
        assert "Found 2 entities" in result

    def test_truncates_beyond_three_with_suffix(self) -> None:
        names = [{"name": f"Entity{i}"} for i in range(5)]
        payload = f'{{"status": "success", "results": {names}}}'.replace("'", '"')
        result = _resolve_entity_filters_result_summary({}, _tool_result(payload))
        assert "+2 more" in result


@pytest.mark.asyncio
class TestKnowledgeGraphResolveEntityFiltersDelegation:
    async def test_delegates_to_ops_entity_search(self) -> None:
        tool = KnowledgeGraph(state={"org_id": "o1"})
        with patch(
            "app.agents.actions.knowledge_graph.ops.entity_search.execute_resolve_entity_filters",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = '{"status": "success", "results": []}'
            result = await tool.resolve_entity_filters(query="legal", entity_types=["department"], top_k=5)
        mock_exec.assert_awaited_once_with(
            tool.state, query="legal", entity_types=["department"], top_k=5,
        )
        assert result == '{"status": "success", "results": []}'


@pytest.mark.asyncio
class TestKnowledgeGraphSearchEntityIdsPassthrough:
    async def test_search_forwards_entity_ids_to_execute_search(self) -> None:
        tool = KnowledgeGraph(state={"org_id": "o1"})
        with patch(
            "app.agents.actions.knowledge_graph.ops.search.execute_search",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = "ok"
            await tool.search(query="roadmap", entity_ids=["t1", "t2"])
        _, kwargs = mock_exec.call_args
        assert kwargs["entity_ids"] == ["t1", "t2"]

    async def test_search_defaults_entity_ids_to_none(self) -> None:
        tool = KnowledgeGraph(state={"org_id": "o1"})
        with patch(
            "app.agents.actions.knowledge_graph.ops.search.execute_search",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = "ok"
            await tool.search(query="roadmap")
        _, kwargs = mock_exec.call_args
        assert kwargs["entity_ids"] is None
