"""Tests for ``app.agents.actions.knowledge_graph.ops.search``."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.knowledge_graph.ops.entity_records import EntitySearchScope
from app.agents.actions.knowledge_graph.ops.search import (
    ENTITY_SCOPE_INCOMPLETE_MESSAGE,
    NARROWED_SEARCH_EMPTY_MESSAGE,
    execute_search,
    normalize_source_ids,
    resolve_entity_filter_groups,
    resolve_record_scoped_entities,
    unresolved_entity_ids,
)
from app.modules.retrieval.entity_permissions import EntityAccessError

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
        # No source was searched, so this is not an empty result.
        assert parsed["status"] == "error"
        assert "result_count" not in parsed

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
        # No source was searched, so this is not an empty result.
        assert parsed["status"] == "error"
        assert "result_count" not in parsed

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


# ---------------------------------------------------------------------------
# An empty narrowed search tells the model to look everywhere before concluding
# ---------------------------------------------------------------------------


_RENDER_PATCHES = (
    patch("app.agents.actions.retrieval.retrieval.compose_result_tail", return_value="\n---\n"),
    patch("app.agents.actions.retrieval.retrieval._dedupe_append_final_results", side_effect=lambda old, new: old + new),
    patch("app.modules.agents.record_escalation.render_coverage_note", return_value=""),
    patch("app.modules.agents.record_escalation.render_candidate_table", return_value=""),
    patch("app.modules.agents.record_escalation.build_candidates", return_value=SimpleNamespace(has_candidates=False)),
    patch("app.modules.agents.record_escalation.analyze_coverage", return_value={}),
    patch(
        "app.agents.actions.knowledge_graph.ops.search.build_message_content_array",
        return_value=([[{"type": "text", "text": "Enterprise pricing strategy 2026"}]], MagicMock()),
    ),
    patch("app.agents.actions.knowledge_graph.ops.search.enrich_records_with_graph_context", new_callable=AsyncMock),
    patch(
        "app.agents.actions.knowledge_graph.ops.search.get_flattened_results",
        new_callable=AsyncMock,
        return_value=[{"virtual_record_id": "vr1", "block_index": 0}],
    ),
    patch("app.agents.actions.knowledge_graph.ops.search.BlobStorage"),
    patch("app.agents.actions.knowledge_graph.ops.search.get_record_id_shortener_if_enabled", return_value=None),
)


def _found() -> dict[str, Any]:
    return {
        "status_code": 200,
        "searchResults": [{"virtual_record_id": "vr1", "block_index": 0}],
        "virtual_to_record_map": {"vr1": {"id": "r1"}},
    }


def _empty() -> dict[str, Any]:
    return {"status_code": 200, "searchResults": [], "virtual_to_record_map": {}}


def _state(retrieval: AsyncMock) -> dict[str, Any]:
    # A user's private collection, the connector that actually holds the answer, and one more.
    return {
        "logger": MagicMock(),
        "retrieval_service": retrieval,
        "graph_provider": AsyncMock(),
        "config_service": MagicMock(),
        "org_id": "o1",
        "user_id": "u1",
        "filters": {"apps": ["private-kb-app", "demo-connector", "wiki"], "kb": []},
        "final_results": [],
    }


class TestEmptyNarrowedSearch:
    """The model picks sources by name, and a name rarely says what a source holds.

    Asked for a pricing strategy, it may search only the user's private
    collection, find nothing, and report that nothing exists while the answer
    sits in another connector. source_ids stays a hard filter; an empty
    narrowed search instead tells the model to search again without it, the
    same way the date filters already do.
    """

    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_an_empty_narrowed_search_says_to_search_everywhere(self, mock_parse) -> None:
        retrieval = AsyncMock()
        retrieval.search_with_filters.side_effect = [_empty()]

        parsed = json.loads(await execute_search(_state(retrieval), "pricing", source_ids=["private-kb-app"]))

        assert parsed["result_count"] == 0
        assert parsed["message"] == NARROWED_SEARCH_EMPTY_MESSAGE
        assert "source_ids omitted" in parsed["message"]
        assert retrieval.search_with_filters.await_count == 1

    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_parallel_per_source_searches_stay_in_their_sources(self, mock_parse) -> None:
        from app.agents.actions.knowledge_graph.ops.scope import KnowledgeScope

        retrieval = AsyncMock()
        retrieval.search_with_filters.side_effect = [_empty(), _empty()]
        state = _state(retrieval)

        first = json.loads(await execute_search(state, "pricing", source_ids=["private-kb-app"]))
        second = json.loads(await execute_search(state, "pricing", source_ids=["wiki"]))

        assert retrieval.search_with_filters.await_count == 2
        sent = [c.kwargs["filter_groups"] for c in retrieval.search_with_filters.await_args_list]
        assert sent == [
            KnowledgeScope(app_ids=("private-kb-app",), kb_ids=()).to_filter_groups(),
            KnowledgeScope(app_ids=("wiki",), kb_ids=()).to_filter_groups(),
        ]
        assert first["message"] == second["message"] == NARROWED_SEARCH_EMPTY_MESSAGE

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "failure",
        [RuntimeError("vector store down"), {"status_code": 503, "message": "Retrieval service unavailable"}],
    )
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_a_source_that_failed_is_not_reported_as_empty(self, mock_parse, failure) -> None:
        retrieval = AsyncMock()
        retrieval.search_with_filters.side_effect = [_empty(), failure]

        parsed = json.loads(await execute_search(_state(retrieval), "pricing", source_ids=["private-kb-app", "wiki"]))

        assert retrieval.search_with_filters.await_count == 2
        assert parsed["status"] == "error"
        assert parsed["message"] != NARROWED_SEARCH_EMPTY_MESSAGE
        assert "could not be searched" in parsed["message"]

    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_sources_that_all_failed_are_not_reported_as_empty(self, mock_parse) -> None:
        retrieval = AsyncMock()
        retrieval.search_with_filters.side_effect = [RuntimeError("vector store down"), None]

        parsed = json.loads(await execute_search(_state(retrieval), "pricing", source_ids=["private-kb-app", "wiki"]))

        assert parsed["status"] == "error"
        assert "No results found" not in parsed["message"]

    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_a_search_that_was_not_narrowed_just_reports_nothing(self, mock_parse) -> None:
        retrieval = AsyncMock()
        retrieval.search_with_filters.side_effect = [_empty()]

        parsed = json.loads(await execute_search(_state(retrieval), "pricing"))

        assert parsed["message"] == "No results found"

    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_naming_every_source_is_not_a_narrowed_search(self, mock_parse) -> None:
        retrieval = AsyncMock()
        retrieval.search_with_filters.side_effect = [_empty(), _empty(), _empty()]

        parsed = json.loads(
            await execute_search(_state(retrieval), "pricing", source_ids=["private-kb-app", "demo-connector", "wiki"])
        )

        assert parsed["message"] == "No results found"

    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_a_narrowed_search_that_found_something_is_unchanged(self, mock_parse) -> None:
        retrieval = AsyncMock()
        retrieval.search_with_filters.side_effect = [_found()]
        with _RENDER_PATCHES[0], _RENDER_PATCHES[1], _RENDER_PATCHES[2], _RENDER_PATCHES[3], \
                _RENDER_PATCHES[4], _RENDER_PATCHES[5], _RENDER_PATCHES[6], _RENDER_PATCHES[7], \
                _RENDER_PATCHES[8], _RENDER_PATCHES[9], _RENDER_PATCHES[10]:
            result = await execute_search(_state(retrieval), "pricing", source_ids=["demo-connector"])

        assert retrieval.search_with_filters.await_count == 1
        assert result.startswith("Top 1 block")
        assert "source_ids omitted" not in result

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failure", [RuntimeError("grep blew up"), asyncio.CancelledError()])
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_a_failing_pattern_match_never_costs_the_semantic_answer(self, mock_parse, failure) -> None:
        retrieval = AsyncMock()
        retrieval.search_with_filters.side_effect = [_found()]
        search_mod = "app.agents.actions.knowledge_graph.ops.search"
        with patch(f"{search_mod}.run_pattern_match_with_llm_grep", AsyncMock(side_effect=failure)), \
                _RENDER_PATCHES[0], _RENDER_PATCHES[1], _RENDER_PATCHES[2], _RENDER_PATCHES[3], \
                _RENDER_PATCHES[4], _RENDER_PATCHES[5], _RENDER_PATCHES[6], _RENDER_PATCHES[7], \
                _RENDER_PATCHES[8], _RENDER_PATCHES[9], _RENDER_PATCHES[10]:
            result = await execute_search(_state(retrieval), "pricing", source_ids=["demo-connector"])

        assert result.startswith("Top 1 block")

    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_a_failing_pattern_match_hint_never_costs_the_semantic_answer(self, mock_parse) -> None:
        retrieval = AsyncMock()
        retrieval.search_with_filters.side_effect = [_found()]
        search_mod = "app.agents.actions.knowledge_graph.ops.search"
        with patch(f"{search_mod}.render_pattern_match_hint", side_effect=KeyError("bad entry")), \
                _RENDER_PATCHES[0], _RENDER_PATCHES[1], _RENDER_PATCHES[2], _RENDER_PATCHES[3], \
                _RENDER_PATCHES[4], _RENDER_PATCHES[5], _RENDER_PATCHES[6], _RENDER_PATCHES[7], \
                _RENDER_PATCHES[8], _RENDER_PATCHES[9], _RENDER_PATCHES[10]:
            result = await execute_search(_state(retrieval), "pricing", source_ids=["demo-connector"])

        assert result.startswith("Top 1 block")

    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_grep_hits_all_dropped_by_the_merge_still_report_an_empty_narrowed_search(
        self, mock_parse,
    ) -> None:
        retrieval = AsyncMock()
        retrieval.search_with_filters.side_effect = [_empty()]
        search_mod = "app.agents.actions.knowledge_graph.ops.search"
        with patch(f"{search_mod}.run_pattern_match_with_llm_grep", AsyncMock(return_value=[{"_key": "r9"}])), \
                patch(f"{search_mod}.merge_pattern_match_results", AsyncMock(return_value=[])), \
                patch(f"{search_mod}.get_flattened_results", AsyncMock(return_value=[])):
            parsed = json.loads(
                await execute_search(_state(retrieval), "pricing", source_ids=["private-kb-app"])
            )

        assert parsed["result_count"] == 0
        assert parsed["message"] == NARROWED_SEARCH_EMPTY_MESSAGE


# ---------------------------------------------------------------------------
# resolve_entity_filter_groups + execute_search entity-filter wiring
# ---------------------------------------------------------------------------

def _entity_search_state(retrieval, **extra):
    state = {
        "logger": MagicMock(),
        "retrieval_service": retrieval,
        "graph_provider": AsyncMock(),
        "config_service": MagicMock(),
        "org_id": "o1",
        "user_id": "u1",
        "filters": {"apps": ["app-1"], "kb": []},
    }
    state.update(extra)
    return state


def _empty_retrieval():
    retrieval = AsyncMock()
    retrieval.search_with_filters.return_value = {
        "status_code": 200, "searchResults": [], "virtual_to_record_map": {},
    }
    return retrieval


class TestResolveEntityFilterGroups:
    def test_no_entity_ids_returns_empty(self) -> None:
        assert resolve_entity_filter_groups({}, None) == {}

    def test_resolves_entity_ids_to_names_via_cache(self) -> None:
        state = {
            "_kg_entity_id_filter_key": {
                "d1": ("departments", "Legal"),
                "t1": ("topics", "Roadmap"),
            }
        }
        result = resolve_entity_filter_groups(state, ["d1", "t1"])
        assert result == {"departments": ["Legal"], "topics": ["Roadmap"]}

    def test_unresolvable_entity_id_is_dropped_not_errored(self) -> None:
        state = {"_kg_entity_id_filter_key": {"d1": ("departments", "Legal")}}
        assert resolve_entity_filter_groups(state, ["d1", "unknown-id"]) == {"departments": ["Legal"]}

    def test_query_text_is_never_auto_resolved(self) -> None:
        """The automatic query-text entity filter was removed; a stale cache
        entry keyed by query must not leak into a search."""
        state = {"_kg_query_entity_filters": {"legal docs": {"departments": ["Legal"]}}}
        assert resolve_entity_filter_groups(state, None) == {}


class TestStrictScope:
    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_strict_scope_reaches_retrieval_as_a_control_flag(self, mock_parse) -> None:
        """Without it an agent whose sources were all removed searched
        everything the user can reach, while the entity tools searched nothing."""
        retrieval = _empty_retrieval()
        state = _entity_search_state(retrieval)
        state["filters"] = {"apps": [], "kb": [], "strictScope": True}

        await execute_search(state, "roadmap")

        _, kwargs = retrieval.search_with_filters.call_args
        assert kwargs["filter_groups"]["strictScope"] is True

    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_no_strict_scope_adds_no_flag(self, mock_parse) -> None:
        retrieval = _empty_retrieval()
        await execute_search(_entity_search_state(retrieval), "roadmap")
        _, kwargs = retrieval.search_with_filters.call_args
        assert "strictScope" not in kwargs["filter_groups"]


class TestUnknownEntityIds:
    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_all_unknown_ids_fail_without_searching(self, mock_parse) -> None:
        """Ids from an earlier turn are not in this turn's cache; an unscoped
        search would be presented as the entity's results."""
        retrieval = _empty_retrieval()
        state = _entity_search_state(retrieval)

        result = await execute_search(state, "nda renewal", entity_ids=["dept-123", "dept-123"])

        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "dept-123" in parsed["message"]
        retrieval.search_with_filters.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_partly_unknown_ids_search_and_name_the_dropped_ones(self, mock_parse) -> None:
        retrieval = _empty_retrieval()
        state = _entity_search_state(
            retrieval, _kg_entity_id_filter_key={"t1": ("topics", "Roadmap")},
        )

        result = await execute_search(state, "roadmap", entity_ids=["t1", "stale-9"])

        first_kwargs = retrieval.search_with_filters.call_args_list[0].kwargs
        assert first_kwargs["filter_groups"]["topics"] == ["Roadmap"]
        assert "stale-9" in json.loads(result)["message"]

    def test_record_ids_are_unresolved(self) -> None:
        state = {
            "_kg_entity_id_filter_key": {"t1": ("topics", "Roadmap")},
            "_kg_record_scoped_entities": {"rg1": "record_group"},
            "_kg_entity_index": {"rec-1": {"type": "record", "name": "Doc"}},
        }
        assert unresolved_entity_ids(state, ["t1", "rg1", "rec-1", "", "x"]) == ["rec-1", "x"]


class TestExecuteSearchEntityFilters:
    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_entity_ids_merged_into_search_with_filters(self, mock_parse) -> None:
        retrieval = _empty_retrieval()
        state = _entity_search_state(
            retrieval, _kg_entity_id_filter_key={"t1": ("topics", "Roadmap")},
        )
        await execute_search(state, "roadmap", entity_ids=["t1"])
        _, kwargs = retrieval.search_with_filters.call_args_list[0]
        assert kwargs["filter_groups"]["topics"] == ["Roadmap"]
        assert kwargs["filter_groups"]["apps"] == ["app-1"]

    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_no_entity_ids_leaves_filter_groups_unchanged(self, mock_parse) -> None:
        retrieval = _empty_retrieval()
        state = _entity_search_state(
            retrieval, _kg_query_entity_filters={"test query": {"departments": ["d1"]}},
        )
        await execute_search(state, "test query")
        _, kwargs = retrieval.search_with_filters.call_args
        assert kwargs["filter_groups"] == {"apps": ["app-1"], "kb": []}
        assert retrieval.search_with_filters.call_count == 1

    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_zero_results_retry_without_entity_filter_says_so(self, mock_parse) -> None:
        retrieval = AsyncMock()
        retrieval.search_with_filters.side_effect = [
            {"status_code": 200, "searchResults": [], "virtual_to_record_map": {}},
            {
                "status_code": 200,
                "searchResults": [{"virtual_record_id": "vr1", "block_index": 0}],
                "virtual_to_record_map": {"vr1": {"id": "r1"}},
            },
        ]
        state = _entity_search_state(
            retrieval, _kg_entity_id_filter_key={"t1": ("topics", "Context graph governance")},
        )
        with patch(
            "app.agents.actions.knowledge_graph.ops.search.get_flattened_results",
            new_callable=AsyncMock,
        ) as mock_flatten, patch(
            "app.agents.actions.knowledge_graph.ops.search.enrich_records_with_graph_context",
            new_callable=AsyncMock,
        ), patch(
            "app.agents.actions.knowledge_graph.ops.search.build_message_content_array",
        ) as mock_build_content, patch(
            "app.agents.actions.knowledge_graph.ops.search.get_record_id_shortener_if_enabled",
            return_value=None,
        ), patch("app.agents.actions.knowledge_graph.ops.search.BlobStorage"), patch(
            "app.modules.agents.record_escalation.build_candidates",
        ) as mock_build_cands, patch(
            "app.agents.actions.retrieval.retrieval._dedupe_append_final_results",
            side_effect=lambda old, new: old + new,
        ):
            mock_flatten.return_value = [{"virtual_record_id": "vr1", "block_index": 0}]
            mock_build_content.return_value = (
                [[{"type": "text", "text": "Fallback content"}]], MagicMock(),
            )
            plan = MagicMock()
            plan.has_candidates = False
            mock_build_cands.return_value = plan

            result = await execute_search(state, "context graph", entity_ids=["t1"])

        assert retrieval.search_with_filters.call_count == 2
        first_kwargs = retrieval.search_with_filters.call_args_list[0].kwargs
        second_kwargs = retrieval.search_with_filters.call_args_list[1].kwargs
        assert first_kwargs["filter_groups"].get("topics") == ["Context graph governance"]
        assert "topics" not in second_kwargs["filter_groups"]
        assert "Fallback content" in result
        assert "NOT limited to it" in result
        assert "record group/subcategory scope still applies" not in result

    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_zero_results_persist_after_fallback_reports_no_results(self, mock_parse) -> None:
        retrieval = _empty_retrieval()
        state = _entity_search_state(
            retrieval, _kg_entity_id_filter_key={"t1": ("topics", "Context graph governance")},
        )
        result = await execute_search(state, "context graph", entity_ids=["t1"])
        assert retrieval.search_with_filters.call_count == 2
        parsed = json.loads(result)
        assert parsed["status"] == "success"
        assert parsed["result_count"] == 0


# ---------------------------------------------------------------------------
# resolve_record_scoped_entities + execute_search record-scoped entities
# ---------------------------------------------------------------------------

class TestResolveRecordScopedEntities:
    def test_no_entity_ids_returns_empty(self) -> None:
        assert resolve_record_scoped_entities({}, None) == []

    def test_filters_to_known_ids_with_their_types(self) -> None:
        state = {"_kg_record_scoped_entities": {"rg1": "record_group", "s1": "subcategory"}}
        result = resolve_record_scoped_entities(state, ["rg1", "unknown-id", "s1"])
        assert result == [("rg1", "record_group"), ("s1", "subcategory")]

    def test_no_cache_drops_all_ids(self) -> None:
        assert resolve_record_scoped_entities({}, ["rg1"]) == []


class TestExecuteSearchRecordScopedEntities:
    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_scopes_via_virtual_record_ids_from_tool(self, mock_parse) -> None:
        retrieval = _empty_retrieval()
        state = _entity_search_state(
            retrieval, _kg_record_scoped_entities={"rg1": "record_group"},
        )
        with patch(
            "app.agents.actions.knowledge_graph.ops.search.resolve_entity_virtual_ids",
            new_callable=AsyncMock,
            return_value=EntitySearchScope(virtual_ids=["vr-rg-1"], truncated=False),
        ) as resolver:
            await execute_search(state, "roadmap", entity_ids=["rg1"])
        resolver.assert_awaited_once_with(state, [("rg1", "record_group")])
        _, kwargs = retrieval.search_with_filters.call_args_list[0]
        assert kwargs["virtual_record_ids_from_tool"] == ["vr-rg-1"]

    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_zero_accessible_records_reports_no_results(self, mock_parse) -> None:
        retrieval = AsyncMock()
        state = _entity_search_state(
            retrieval, _kg_record_scoped_entities={"rg1": "record_group"},
        )
        with patch(
            "app.agents.actions.knowledge_graph.ops.search.resolve_entity_virtual_ids",
            new_callable=AsyncMock,
            return_value=EntitySearchScope(virtual_ids=[], truncated=False),
        ):
            result = await execute_search(state, "roadmap", entity_ids=["rg1"])
        parsed = json.loads(result)
        assert parsed["status"] == "success"
        assert parsed["result_count"] == 0
        assert parsed["message"] == "No accessible records found for the requested entities."
        retrieval.search_with_filters.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_empty_truncated_scope_is_reported_as_inconclusive(self, mock_parse) -> None:
        retrieval = AsyncMock()
        state = _entity_search_state(
            retrieval, _kg_record_scoped_entities={"rg1": "record_group"},
        )
        with patch(
            "app.agents.actions.knowledge_graph.ops.search.resolve_entity_virtual_ids",
            new_callable=AsyncMock,
            return_value=EntitySearchScope(virtual_ids=[], truncated=True),
        ):
            result = await execute_search(state, "roadmap", entity_ids=["rg1"])
        parsed = json.loads(result)
        assert parsed["message"] == ENTITY_SCOPE_INCOMPLETE_MESSAGE
        retrieval.search_with_filters.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_truncated_scope_with_no_hits_says_so(self, mock_parse) -> None:
        retrieval = _empty_retrieval()
        state = _entity_search_state(
            retrieval, _kg_record_scoped_entities={"rg1": "record_group"},
        )
        with patch(
            "app.agents.actions.knowledge_graph.ops.search.resolve_entity_virtual_ids",
            new_callable=AsyncMock,
            return_value=EntitySearchScope(virtual_ids=["vr-1"], truncated=True),
        ):
            result = await execute_search(state, "roadmap", entity_ids=["rg1"])
        assert "only its newest accessible records were searched" in json.loads(result)["message"]

    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_retrieval_error_in_a_scoped_search_is_an_error(self, mock_parse) -> None:
        """A failed retrieval must not read as "the folder has nothing on this"."""
        retrieval = AsyncMock()
        retrieval.search_with_filters.return_value = {
            "searchResults": [], "status": "error", "status_code": 500,
            "message": "Unexpected server error during search.",
        }
        state = _entity_search_state(
            retrieval, _kg_record_scoped_entities={"rg1": "record_group"},
        )
        with patch(
            "app.agents.actions.knowledge_graph.ops.search.resolve_entity_virtual_ids",
            new_callable=AsyncMock,
            return_value=EntitySearchScope(virtual_ids=["vr-1"], truncated=False),
        ):
            result = await execute_search(state, "roadmap", entity_ids=["rg1"])
        assert json.loads(result)["status"] == "error"

    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_scoping_failure_is_an_error_not_an_unscoped_search(self, mock_parse) -> None:
        retrieval = AsyncMock()
        state = _entity_search_state(
            retrieval, _kg_record_scoped_entities={"s1": "subcategory"},
        )
        with patch(
            "app.agents.actions.knowledge_graph.ops.search.resolve_entity_virtual_ids",
            new_callable=AsyncMock, side_effect=EntityAccessError("db down"),
        ):
            result = await execute_search(state, "roadmap", entity_ids=["s1"])
        assert json.loads(result)["status"] == "error"
        retrieval.search_with_filters.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_no_record_scoped_entity_ids_passes_none(self, mock_parse) -> None:
        """Without a record-scoped entity_id, virtual_record_ids_from_tool
        must stay None (no restriction) — not an empty list, which would
        wrongly restrict to nothing."""
        retrieval = _empty_retrieval()
        state = _entity_search_state(retrieval)
        await execute_search(state, "test query")
        _, kwargs = retrieval.search_with_filters.call_args
        assert kwargs["virtual_record_ids_from_tool"] is None
