"""Tests for ``app.agents.actions.knowledge_graph.ops.search``."""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.knowledge_graph.ops.search import (
    NARROWED_SEARCH_EMPTY_MESSAGE,
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
