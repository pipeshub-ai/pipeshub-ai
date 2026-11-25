"""Tests for the ``KnowledgeGraph`` delegation wiring of the ``search_entities``
and ``find_records_by_entity`` tools, plus their result-summary helpers.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.agent_loop_lib.tools.decorators import TOOL_META_ATTR, BoundMethodTool
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
            result = await tool.search_entities(
                query="legal", entity_types=["department"], top_k=5, named_entity_kinds=["currency"],
            )
        mock_exec.assert_awaited_once_with(
            tool.state, query="legal", entity_types=["department"], top_k=5, named_entity_kinds=["currency"],
        )
        assert result == (True, '{"status": "success", "results": []}')


@pytest.mark.asyncio
class TestKnowledgeGraphFindRecordsByValueDelegation:
    async def test_delegates_every_bound_to_ops_values(self) -> None:
        tool = KnowledgeGraph(state={"org_id": "o1"})
        with patch(
            "app.agents.actions.knowledge_graph.ops.values.execute_find_records_by_value",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = (True, '{"status": "success", "records": []}')
            await tool.find_records_by_value(
                entity_ids=["e-1"], amount_min=5000, currency="USD", quantity_max=2.5, percent_min=0.2,
                record_types=["FILE"], source_ids=["kb-1"], limit=30, cursor="40",
            )
        assert mock_exec.await_args.kwargs == {
            "kinds": None, "name": None, "entity_ids": ["e-1"], "date_from": None, "date_to": None,
            "amount_min": 5000, "amount_max": None, "currency": "USD",
            "quantity_min": None, "quantity_max": 2.5, "dimension": None,
            "percent_min": 0.2, "percent_max": None,
            "record_types": ["FILE"], "source_ids": ["kb-1"], "limit": 30, "cursor": "40",
        }


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


def test_people_are_named_in_the_entity_tool_descriptions() -> None:
    """KG-13: the model can only ask for a type it is told exists."""
    from app.agents.actions.knowledge_graph.knowledge_graph import KnowledgeGraph

    search = KnowledgeGraph.search_entities._agent_tool_meta
    params = {p.name: p.description for p in search.parameters}
    assert "people" in search.short_description and "people" in search.description
    assert "'person'" in params["entity_types"]
    assert "person" in KnowledgeGraph.find_records_by_entity._agent_tool_meta.description


def test_organizations_are_named_in_the_entity_tool_descriptions() -> None:
    from app.agents.actions.knowledge_graph.knowledge_graph import KnowledgeGraph

    search = KnowledgeGraph.search_entities._agent_tool_meta
    params = {p.name: p.description for p in search.parameters}
    assert "organizations" in search.short_description and "organizations" in search.description
    assert "'organization'" in params["entity_types"]
    find = KnowledgeGraph.find_records_by_entity._agent_tool_meta
    assert "organization" in find.description
    assert "'organization'" in {p.name: p.description for p in find.parameters}["entity_type"]



def _wrapped_search(tool: KnowledgeGraph) -> BoundMethodTool:
    # The wrapper the agent loop calls; its ToolOutput.success becomes tool_call.end outcome and is_error.
    return BoundMethodTool(tool.search, getattr(tool.search, TOOL_META_ATTR))


@pytest.mark.asyncio
class TestKnowledgeGraphSearchReportsFailures:
    """A failed search is a failed tool call, not a success holding an error payload."""

    @pytest.mark.parametrize("payload", [
        json.dumps({"status": "error", "status_code": 500, "message": "Unexpected server error during search."}),
        json.dumps({"status": "error", "message": "Retrieval services not available"}),
        json.dumps({"status": "error", "message": "No search query provided"}),
    ])
    async def test_error_payload_is_a_failed_call_with_the_same_text(self, payload: str) -> None:
        tool = KnowledgeGraph(state={"org_id": "o1"})
        with patch(
            "app.agents.actions.knowledge_graph.ops.search.execute_search",
            new_callable=AsyncMock, return_value=payload,
        ):
            out = await _wrapped_search(tool).execute(query="pricing")
        assert out.success is False
        assert out.error == payload

    @pytest.mark.parametrize("result", [
        "<record>pricing sheet</record>",
        json.dumps({"status": "success", "message": "No results found", "results": []}),
        [],
    ])
    async def test_results_and_empty_results_stay_successful(self, result: object) -> None:
        tool = KnowledgeGraph(state={"org_id": "o1"})
        with patch(
            "app.agents.actions.knowledge_graph.ops.search.execute_search",
            new_callable=AsyncMock, return_value=result,
        ):
            out = await _wrapped_search(tool).execute(query="pricing")
        assert out.success is True
        assert out.data == result

    async def test_timeline_error_is_a_failed_call(self) -> None:
        payload = json.dumps({"status": "error", "message": "Timeline search failed. Try again."})
        tool = KnowledgeGraph(state={"org_id": "o1"})
        with patch(
            "app.agents.actions.knowledge_graph.ops.timeline.execute_timeline_search",
            new_callable=AsyncMock, return_value=payload,
        ):
            out = await BoundMethodTool(tool.search_timeline, getattr(tool.search_timeline, TOOL_META_ATTR)).execute(query="q")
        assert out.success is False
        assert out.error == payload

    @pytest.mark.parametrize("result, success", [
        (json.dumps({"entities": "Acme", "status": "error", "message": "Search error: boom"}), False),
        ((True, '{"status": "success", "results": []}'), True),
    ])
    async def test_entity_search_error_with_preamble_is_a_failed_call(self, result: object, success: bool) -> None:
        tool = KnowledgeGraph(state={"org_id": "o1"})
        with patch(
            "app.agents.actions.knowledge_graph.ops.entity_search.execute_search_by_entities",
            new_callable=AsyncMock, return_value=result,
        ):
            out = await BoundMethodTool(tool.search_by_entities, getattr(tool.search_by_entities, TOOL_META_ATTR)).execute(query="q")
        assert out.success is success
