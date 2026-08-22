"""Tests for ``app.agents.actions.knowledge_graph.ops.search``."""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.knowledge_graph.ops.search import (
    execute_search,
    normalize_source_ids,
)


# ---------------------------------------------------------------------------
# normalize_source_ids
# ---------------------------------------------------------------------------

class TestNormalizeSourceIds:
    def test_none(self) -> None:
        assert normalize_source_ids(None) is None

    def test_non_empty_string(self) -> None:
        assert normalize_source_ids("abc") == ["abc"]

    def test_whitespace_string(self) -> None:
        assert normalize_source_ids("   ") is None

    def test_empty_string(self) -> None:
        assert normalize_source_ids("") is None

    def test_non_empty_list(self) -> None:
        assert normalize_source_ids(["a", "b"]) == ["a", "b"]

    def test_list_filters_falsy(self) -> None:
        assert normalize_source_ids(["a", "", None, "b"]) == ["a", "b"]

    def test_all_falsy_list(self) -> None:
        assert normalize_source_ids(["", None]) is None

    def test_non_string_non_list(self) -> None:
        assert normalize_source_ids(42) is None

    def test_list_coerces_ints(self) -> None:
        assert normalize_source_ids([1, 2]) == ["1", "2"]


# ---------------------------------------------------------------------------
# execute_search guards
# ---------------------------------------------------------------------------

def _make_scope(app_ids=(), kb_ids=()):
    s = SimpleNamespace(app_ids=app_ids, kb_ids=kb_ids)
    s.is_empty = lambda: not app_ids and not kb_ids
    s.narrow_to = lambda ids: s
    s.to_filter_groups = lambda: {}
    return s


class TestExecuteSearchGuards:
    @pytest.mark.asyncio
    async def test_no_query(self) -> None:
        result = await execute_search({}, None)
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "No search query" in parsed["message"]

    @pytest.mark.asyncio
    async def test_no_state(self) -> None:
        result = await execute_search(None, "test query")
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "not initialized" in parsed["message"]

    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range")
    async def test_time_error(self, mock_parse) -> None:
        mock_parse.return_value = (None, '{"status":"error","message":"bad date"}')
        state = {"logger": MagicMock()}
        result = await execute_search(state, "test query")
        assert "bad date" in result

    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_no_retrieval_service(self, mock_parse) -> None:
        state = {
            "logger": MagicMock(),
            "retrieval_service": None,
            "graph_provider": AsyncMock(),
        }
        result = await execute_search(state, "test query")
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "Retrieval services" in parsed["message"]


class TestExecuteSearchSingleSource:
    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_no_results_returns_success(self, mock_parse) -> None:
        retrieval = AsyncMock()
        retrieval.search_with_filters.return_value = {
            "status_code": 200,
            "searchResults": [],
            "virtual_to_record_map": {},
        }
        state = {
            "logger": MagicMock(),
            "retrieval_service": retrieval,
            "graph_provider": AsyncMock(),
            "config_service": MagicMock(),
            "org_id": "o1",
            "user_id": "u1",
            "filters": {"apps": ["app-1"], "kb": []},
        }
        result = await execute_search(state, "test query")
        parsed = json.loads(result)
        assert parsed["status"] == "success"
        assert parsed["result_count"] == 0

    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_placeholder_agent_scope(self, mock_parse) -> None:
        retrieval = AsyncMock()
        retrieval.search_with_filters.return_value = {
            "status_code": 200,
            "searchResults": [],
            "virtual_to_record_map": {},
        }
        state = {
            "logger": MagicMock(),
            "retrieval_service": retrieval,
            "graph_provider": AsyncMock(),
            "config_service": MagicMock(),
            "org_id": "o1",
            "user_id": "u1",
            "is_placeholder_agent": True,
            "apps": ["app-p1", "app-p2"],
            "kb": ["kb-p1"],
            "filters": {},
        }
        result = await execute_search(state, "test query")
        parsed = json.loads(result)
        assert parsed["status"] == "success"

    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_source_ids_narrowing(self, mock_parse) -> None:
        retrieval = AsyncMock()
        retrieval.search_with_filters.return_value = {
            "status_code": 200,
            "searchResults": [],
            "virtual_to_record_map": {},
        }
        state = {
            "logger": MagicMock(),
            "retrieval_service": retrieval,
            "graph_provider": AsyncMock(),
            "config_service": MagicMock(),
            "org_id": "o1",
            "user_id": "u1",
            "filters": {"apps": ["app-1", "app-2"], "kb": ["kb-1"]},
        }
        result = await execute_search(state, "test query", source_ids=["app-1"])
        parsed = json.loads(result)
        assert parsed["status"] == "success"

    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_retrieval_returns_none(self, mock_parse) -> None:
        retrieval = AsyncMock()
        retrieval.search_with_filters.return_value = None
        state = {
            "logger": MagicMock(),
            "retrieval_service": retrieval,
            "graph_provider": AsyncMock(),
            "config_service": MagicMock(),
            "org_id": "o1",
            "user_id": "u1",
            "filters": {"apps": ["app-1"], "kb": []},
        }
        result = await execute_search(state, "test query")
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "no results" in parsed["message"].lower()

    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_retrieval_error_status(self, mock_parse) -> None:
        retrieval = AsyncMock()
        retrieval.search_with_filters.return_value = {
            "status_code": 500,
            "message": "Internal error",
        }
        state = {
            "logger": MagicMock(),
            "retrieval_service": retrieval,
            "graph_provider": AsyncMock(),
            "config_service": MagicMock(),
            "org_id": "o1",
            "user_id": "u1",
            "filters": {"apps": ["app-1"], "kb": []},
        }
        result = await execute_search(state, "test query")
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert parsed["status_code"] == 500


class TestExecuteSearchFanOut:
    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_fan_out_no_results(self, mock_parse) -> None:
        retrieval = AsyncMock()
        retrieval.search_with_filters.return_value = {
            "status_code": 200,
            "searchResults": [],
            "virtual_to_record_map": {},
        }
        state = {
            "logger": MagicMock(),
            "retrieval_service": retrieval,
            "graph_provider": AsyncMock(),
            "config_service": MagicMock(),
            "org_id": "o1",
            "user_id": "u1",
            "filters": {"apps": ["app-1", "app-2"], "kb": []},
        }
        result = await execute_search(state, "test query", source_ids=["app-1", "app-2"])
        parsed = json.loads(result)
        assert parsed["status"] == "success"
        assert parsed["result_count"] == 0

    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_fan_out_all_errors(self, mock_parse) -> None:
        retrieval = AsyncMock()
        retrieval.search_with_filters.return_value = {
            "status_code": 500,
            "message": "service down",
        }
        state = {
            "logger": MagicMock(),
            "retrieval_service": retrieval,
            "graph_provider": AsyncMock(),
            "config_service": MagicMock(),
            "org_id": "o1",
            "user_id": "u1",
            "filters": {"apps": ["app-1", "app-2"], "kb": []},
        }
        result = await execute_search(state, "test query", source_ids=["app-1", "app-2"])
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert parsed["status_code"] == 500

    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_fan_out_exception_in_gather(self, mock_parse) -> None:
        retrieval = AsyncMock()
        retrieval.search_with_filters.side_effect = RuntimeError("partial fail")
        state = {
            "logger": MagicMock(),
            "retrieval_service": retrieval,
            "graph_provider": AsyncMock(),
            "config_service": MagicMock(),
            "org_id": "o1",
            "user_id": "u1",
            "filters": {"apps": ["app-1", "app-2"], "kb": []},
        }
        result = await execute_search(state, "test query", source_ids=["app-1", "app-2"])
        parsed = json.loads(result)
        assert parsed["status"] == "success"
        assert parsed["result_count"] == 0

    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_fan_out_returns_none(self, mock_parse) -> None:
        retrieval = AsyncMock()
        retrieval.search_with_filters.return_value = None
        state = {
            "logger": MagicMock(),
            "retrieval_service": retrieval,
            "graph_provider": AsyncMock(),
            "config_service": MagicMock(),
            "org_id": "o1",
            "user_id": "u1",
            "filters": {"apps": ["app-1", "app-2"], "kb": []},
        }
        result = await execute_search(state, "test query", source_ids=["app-1", "app-2"])
        parsed = json.loads(result)
        assert parsed["status"] == "success"
        assert parsed["result_count"] == 0

    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_fan_out_kb_sources(self, mock_parse) -> None:
        retrieval = AsyncMock()
        retrieval.search_with_filters.return_value = {
            "status_code": 200,
            "searchResults": [],
            "virtual_to_record_map": {},
        }
        state = {
            "logger": MagicMock(),
            "retrieval_service": retrieval,
            "graph_provider": AsyncMock(),
            "config_service": MagicMock(),
            "org_id": "o1",
            "user_id": "u1",
            "filters": {"apps": [], "kb": ["kb-1", "kb-2"]},
        }
        result = await execute_search(state, "test query", source_ids=["kb-1", "kb-2"])
        parsed = json.loads(result)
        assert parsed["status"] == "success"

    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_retrieval_status_202_treated_as_error(self, mock_parse) -> None:
        retrieval = AsyncMock()
        retrieval.search_with_filters.return_value = {
            "status_code": 202,
            "message": "Indexing in progress",
        }
        state = {
            "logger": MagicMock(),
            "retrieval_service": retrieval,
            "graph_provider": AsyncMock(),
            "config_service": MagicMock(),
            "org_id": "o1",
            "user_id": "u1",
            "filters": {"apps": ["app-1"], "kb": []},
        }
        result = await execute_search(state, "test query")
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert parsed["status_code"] == 202


class TestExecuteSearchFullPath:
    @pytest.mark.asyncio
    @patch("app.agents.actions.retrieval.retrieval.compose_result_tail", return_value="\n---\n")
    @patch("app.agents.actions.retrieval.retrieval._dedupe_append_final_results", side_effect=lambda old, new: old + new)
    @patch("app.modules.agents.record_escalation.render_coverage_note", return_value="")
    @patch("app.modules.agents.record_escalation.render_candidate_table", return_value="")
    @patch("app.modules.agents.record_escalation.build_candidates")
    @patch("app.modules.agents.record_escalation.analyze_coverage", return_value={})
    @patch("app.agents.actions.knowledge_graph.ops.search.build_message_content_array")
    @patch("app.agents.actions.knowledge_graph.ops.search.enrich_records_with_graph_context", new_callable=AsyncMock)
    @patch("app.agents.actions.knowledge_graph.ops.search.get_flattened_results", new_callable=AsyncMock)
    @patch("app.agents.actions.knowledge_graph.ops.search.BlobStorage")
    @patch("app.agents.actions.knowledge_graph.ops.search.get_record_id_shortener_if_enabled", return_value=None)
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_with_results(self, mock_parse, mock_shortener, mock_blob,
                                 mock_flatten, mock_enrich, mock_build_content,
                                 mock_analyze, mock_build_cands, mock_render_cand,
                                 mock_render_note, mock_dedupe, mock_compose) -> None:
        search_result = {"virtual_record_id": "vr1", "block_index": 0}
        retrieval = AsyncMock()
        retrieval.search_with_filters.return_value = {
            "status_code": 200,
            "searchResults": [search_result],
            "virtual_to_record_map": {"vr1": {"id": "r1"}},
        }
        mock_flatten.return_value = [search_result]
        mock_build_content.return_value = (
            [[{"type": "text", "text": "Block content"}]],
            MagicMock(),
        )
        plan = MagicMock()
        plan.has_candidates = False
        mock_build_cands.return_value = plan

        state = {
            "logger": MagicMock(),
            "retrieval_service": retrieval,
            "graph_provider": AsyncMock(),
            "config_service": MagicMock(),
            "org_id": "o1",
            "user_id": "u1",
            "filters": {"apps": ["app-1"], "kb": []},
            "final_results": [],
        }
        result = await execute_search(state, "test query")
        assert "Top 1 block" in result
        assert "Block content" in result
        mock_flatten.assert_called_once()
        assert "final_results" in state

    @pytest.mark.asyncio
    @patch("app.agents.actions.retrieval.retrieval.compose_result_tail", return_value="\n---\n")
    @patch("app.agents.actions.retrieval.retrieval._dedupe_append_final_results", side_effect=lambda old, new: old + new)
    @patch("app.modules.agents.record_escalation.render_coverage_note", return_value="")
    @patch("app.modules.agents.record_escalation.render_candidate_table", return_value="table")
    @patch("app.modules.agents.record_escalation.build_candidates")
    @patch("app.modules.agents.record_escalation.analyze_coverage", return_value={"r1": (1, 3)})
    @patch("app.agents.actions.knowledge_graph.ops.search.build_message_content_array")
    @patch("app.agents.actions.knowledge_graph.ops.search.enrich_records_with_graph_context", new_callable=AsyncMock)
    @patch("app.agents.actions.knowledge_graph.ops.search.get_flattened_results", new_callable=AsyncMock)
    @patch("app.agents.actions.knowledge_graph.ops.search.BlobStorage")
    @patch("app.agents.actions.knowledge_graph.ops.search.get_record_id_shortener_if_enabled", return_value=None)
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_with_candidates(self, mock_parse, mock_shortener, mock_blob,
                                    mock_flatten, mock_enrich, mock_build_content,
                                    mock_analyze, mock_build_cands, mock_render_cand,
                                    mock_render_note, mock_dedupe, mock_compose) -> None:
        search_result = {"virtual_record_id": "vr1", "block_index": 0}
        retrieval = AsyncMock()
        retrieval.search_with_filters.return_value = {
            "status_code": 200,
            "searchResults": [search_result],
            "virtual_to_record_map": {"vr1": {"id": "r1"}},
        }
        mock_flatten.return_value = [search_result]
        mock_build_content.return_value = (
            [[{"type": "text", "text": "Content"}]],
            MagicMock(),
        )
        plan = MagicMock()
        plan.has_candidates = True
        mock_build_cands.return_value = plan

        state = {
            "logger": MagicMock(),
            "retrieval_service": retrieval,
            "graph_provider": AsyncMock(),
            "config_service": MagicMock(),
            "org_id": "o1",
            "user_id": "u1",
            "filters": {"apps": ["app-1"], "kb": []},
            "final_results": [],
        }
        result = await execute_search(state, "test query")
        assert "Top 1 block" in result
        mock_render_cand.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.agents.actions.retrieval.retrieval.compose_result_tail", return_value="")
    @patch("app.agents.actions.retrieval.retrieval._dedupe_append_final_results", side_effect=lambda old, new: old + new)
    @patch("app.modules.agents.record_escalation.render_coverage_note", return_value="")
    @patch("app.modules.agents.record_escalation.render_candidate_table", return_value="")
    @patch("app.modules.agents.record_escalation.build_candidates")
    @patch("app.modules.agents.record_escalation.analyze_coverage", return_value={})
    @patch("app.agents.actions.knowledge_graph.ops.search.build_message_content_array")
    @patch("app.agents.actions.knowledge_graph.ops.search.enrich_records_with_graph_context", new_callable=AsyncMock)
    @patch("app.agents.actions.knowledge_graph.ops.search.get_flattened_results", new_callable=AsyncMock)
    @patch("app.agents.actions.knowledge_graph.ops.search.BlobStorage")
    @patch("app.agents.actions.knowledge_graph.ops.search.get_record_id_shortener_if_enabled", return_value=None)
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_multimodal_detection(self, mock_parse, mock_shortener, mock_blob,
                                         mock_flatten, mock_enrich, mock_build_content,
                                         mock_analyze, mock_build_cands, mock_render_cand,
                                         mock_render_note, mock_dedupe, mock_compose) -> None:
        search_result = {"virtual_record_id": "vr1", "block_index": 0}
        retrieval = AsyncMock()
        retrieval.search_with_filters.return_value = {
            "status_code": 200,
            "searchResults": [search_result],
            "virtual_to_record_map": {"vr1": {"id": "r1"}},
        }
        mock_flatten.return_value = [search_result]
        mock_build_content.return_value = (
            [[{"type": "text", "text": "V"}]],
            MagicMock(),
        )
        plan = MagicMock()
        plan.has_candidates = False
        mock_build_cands.return_value = plan

        llm_config = SimpleNamespace(model_name="gpt-4o-mini")
        state = {
            "logger": MagicMock(),
            "retrieval_service": retrieval,
            "graph_provider": AsyncMock(),
            "config_service": MagicMock(),
            "org_id": "o1",
            "user_id": "u1",
            "filters": {"apps": ["app-1"], "kb": []},
            "final_results": [],
            "llm": llm_config,
        }
        result = await execute_search(state, "test query")
        assert "Top 1 block" in result


class TestExecuteSearchException:
    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_generic_exception(self, mock_parse) -> None:
        retrieval = AsyncMock()
        retrieval.search_with_filters.side_effect = RuntimeError("boom")
        state = {
            "logger": MagicMock(),
            "retrieval_service": retrieval,
            "graph_provider": AsyncMock(),
            "config_service": MagicMock(),
            "org_id": "o1",
            "user_id": "u1",
            "filters": {"apps": ["app-1"], "kb": []},
        }
        result = await execute_search(state, "test query")
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "boom" in parsed["message"]
