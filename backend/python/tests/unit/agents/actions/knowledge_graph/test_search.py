"""Tests for ``app.agents.actions.knowledge_graph.ops.search``."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.knowledge_graph.ops.search import (
    NARROWED_SEARCH_EMPTY_MESSAGE,
    execute_search,
    normalize_source_ids,
    resolve_entity_filter_groups,
    resolve_record_scoped_entities,
)
from app.modules.retrieval.context.builder import KnowledgeContext
from app.modules.retrieval.context.manifest import (
    BlockKey,
    ManifestSource,
    manifest_registry,
)
from app.modules.retrieval.context.ranking import RelevanceRanker
from app.modules.retrieval.context.renderer import RenderedKnowledge
from app.modules.retrieval.context.reranking import RerankingRanker
from app.modules.retrieval.entity_permissions import EntityAccessError

_SEARCH = "app.agents.actions.knowledge_graph.ops.search"

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
    async def test_fan_out_interleaves_sources_by_rank(self, mock_parse) -> None:
        """Each source's scores are ranks within its own search; sorting them
        together would let the higher-scoring search take every top slot."""
        def response(vrid: str, scores: list[float]) -> dict:
            return {
                "status_code": 200,
                "searchResults": [
                    {"score": score, "metadata": {"virtualRecordId": vrid, "blockId": f"{vrid}{i}"}}
                    for i, score in enumerate(scores)
                ],
                "virtual_to_record_map": {vrid: {"id": vrid}},
            }

        retrieval = AsyncMock()
        retrieval.search_with_filters.side_effect = [
            response("a", [0.9, 0.8, 0.7]),
            response("b", [0.02, 0.01]),
        ]
        state = _full_path_state(retrieval, filters={"apps": [], "kb": ["kb-1", "kb-2"]})
        builder_patch, render_patch, blob_patch = _pipeline()
        with builder_patch as mock_builder, render_patch, blob_patch:
            await execute_search(state, "test query", source_ids=["kb-1", "kb-2"])

        hits = mock_builder.return_value.build.call_args.args[0]
        assert [h["metadata"]["blockId"] for h in hits] == ["a0", "b0", "a1", "b1", "a2"]
        assert [h["score"] for h in hits] == sorted((h["score"] for h in hits), reverse=True)

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


def _full_path_state(retrieval, **extra):
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
    state.update(extra)
    return state


def _one_hit_retrieval():
    retrieval = AsyncMock()
    retrieval.search_with_filters.return_value = {
        "status_code": 200,
        "searchResults": [{"virtual_record_id": "vr1", "block_index": 0}],
        "virtual_to_record_map": {"vr1": {"id": "r1"}},
    }
    return retrieval


def _pipeline(*, text="Block content", units=None, omitted=0, images=None):
    """Patch the shared context pipeline at its two seams: what it builds and renders."""
    units = units if units is not None else [{"virtual_record_id": "vr1", "block_index": 0}]
    builder = MagicMock()
    builder.return_value.build = AsyncMock(return_value=KnowledgeContext(
        units=units, virtual_record_id_to_result={"vr1": {"id": "r1"}},
    ))
    rendered = RenderedKnowledge(
        records=[text], units=units, images=images or [], omitted_hits=omitted,
    )
    return (
        patch(f"{_SEARCH}.KnowledgeContextBuilder", builder),
        patch(f"{_SEARCH}.render_knowledge", return_value=rendered),
        patch(f"{_SEARCH}.BlobStorage"),
    )


class TestExecuteSearchFullPath:
    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_header_states_the_ranked_order(self, mock_parse) -> None:
        state = _full_path_state(_one_hit_retrieval())
        builder_patch, render_patch, blob_patch = _pipeline()
        with builder_patch, render_patch, blob_patch:
            result = await execute_search(state, "test query")

        assert result.startswith("Top 1 block from 1 record, most relevant record first")
        assert "Block content" in result
        assert state["final_results"] == [{"virtual_record_id": "vr1", "block_index": 0}]

    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_header_says_when_lower_ranked_blocks_were_left_out(self, mock_parse) -> None:
        state = _full_path_state(_one_hit_retrieval())
        builder_patch, render_patch, blob_patch = _pipeline(omitted=3)
        with builder_patch, render_patch, blob_patch:
            result = await execute_search(state, "test query")

        assert "3 lower-ranked blocks left out to fit the result size." in result

    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_only_what_was_shown_becomes_citable(self, mock_parse) -> None:
        state = _full_path_state(_one_hit_retrieval())
        shown = [{"virtual_record_id": "vr1", "block_index": 4}]
        builder_patch, render_patch, blob_patch = _pipeline(units=shown)
        with builder_patch, render_patch as mock_render, blob_patch:
            await execute_search(state, "test query")

        assert state["final_results"] == shown
        assert mock_render.call_args.kwargs["max_chars"] > 0

    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_trims_to_the_source_limit_unless_fanned_out(self, mock_parse) -> None:
        state = _full_path_state(_one_hit_retrieval())
        builder_patch, render_patch, blob_patch = _pipeline()
        with builder_patch as mock_builder, render_patch, blob_patch:
            await execute_search(state, "test query")

        assert mock_builder.return_value.build.call_args.kwargs["max_units"] == 50

    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_a_record_written_by_a_parallel_fetch_survives(self, mock_parse) -> None:
        """Fetch writes into the records map it was handed; a search in the
        same turn must add to that dict, not swap in a new one."""
        state = _full_path_state(_one_hit_retrieval())
        live = state["virtual_record_id_to_result"] = {"vr-old": {"id": "r-old"}}

        async def parallel_fetch() -> None:
            await asyncio.sleep(0)
            live["vr-fetched"] = {"id": "r-fetched"}

        builder_patch, render_patch, blob_patch = _pipeline()
        with builder_patch, render_patch, blob_patch:
            await asyncio.gather(execute_search(state, "test query"), parallel_fetch())

        assert state["virtual_record_id_to_result"] is live
        assert {"vr-old", "vr1", "vr-fetched"} <= live.keys()

    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_registers_which_characters_of_its_result_show_which_blocks(self, mock_parse) -> None:
        """Spans are for the text the model gets, header included, so a later
        step can find each block's copy in the ToolMessage."""
        state = _full_path_state(_one_hit_retrieval())
        unit = {"virtual_record_id": "vr1", "block_index": 0, "block_type": "text",
                "content": "the only hit", "metadata": {}}
        record = {"id": "r1", "virtual_record_id": "vr1", "context_metadata": "Record ID: r1",
                  "frontend_url": "", "block_containers": {"blocks": [], "block_groups": []}}
        builder = MagicMock()
        builder.return_value.build = AsyncMock(return_value=KnowledgeContext(
            units=[unit], virtual_record_id_to_result={"vr1": record},
        ))
        with patch(f"{_SEARCH}.KnowledgeContextBuilder", builder), patch(f"{_SEARCH}.BlobStorage"):
            result = await execute_search(state, "test query")

        manifest = manifest_registry(state).lookup(result)
        assert manifest is not None and manifest.source is ManifestSource.SEARCH
        (segment,) = manifest.segments
        assert result[segment.start:segment.end].endswith("the only hit\n\n")
        assert segment.blocks == {BlockKey("vr1", 0)}

    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_ranks_by_retrieval_score_when_no_reranker_is_active(self, mock_parse) -> None:
        state = _full_path_state(_one_hit_retrieval())
        state["reranker_resolver"] = MagicMock(active=AsyncMock(return_value=None))
        builder_patch, render_patch, blob_patch = _pipeline()
        with builder_patch as mock_builder, render_patch, blob_patch:
            await execute_search(state, "test query")

        assert type(mock_builder.call_args.kwargs["ranker"]) is RelevanceRanker

    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_the_active_reranker_ranks_against_this_search_query(self, mock_parse) -> None:
        state = _full_path_state(_one_hit_retrieval())
        state["query"] = "the user's original question"
        state["reranker_resolver"] = MagicMock(active=AsyncMock(return_value=MagicMock()))
        builder_patch, render_patch, blob_patch = _pipeline()
        with builder_patch as mock_builder, render_patch, blob_patch:
            await execute_search(state, "hop query")

        assert isinstance(mock_builder.call_args.kwargs["ranker"], RerankingRanker)
        assert mock_builder.return_value.build.call_args.kwargs["query"] == "hop query"

    @pytest.mark.asyncio
    @patch("app.modules.agents.record_escalation.render_coverage_note", return_value="")
    @patch("app.modules.agents.record_escalation.render_candidate_table", return_value="\nCANDIDATES")
    @patch("app.modules.agents.record_escalation.build_candidates")
    @patch("app.modules.agents.record_escalation.analyze_coverage", return_value={"r1": (1, 3)})
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_with_candidates(
        self, mock_parse, mock_analyze, mock_build_cands, mock_render_cand, mock_note,
    ) -> None:
        plan = MagicMock()
        plan.has_candidates = True
        mock_build_cands.return_value = plan
        state = _full_path_state(_one_hit_retrieval())
        builder_patch, render_patch, blob_patch = _pipeline()
        with builder_patch, render_patch, blob_patch:
            result = await execute_search(state, "test query")

        assert "CANDIDATES" in result
        mock_render_cand.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_multimodal_flag_comes_from_state_not_model_name(self, mock_parse) -> None:
        state = _full_path_state(
            _one_hit_retrieval(),
            is_multimodal_llm=True,
            llm=SimpleNamespace(model_name="some-model-without-a-known-name"),
        )
        builder_patch, render_patch, blob_patch = _pipeline()
        with builder_patch as mock_builder, render_patch as mock_render, blob_patch:
            await execute_search(state, "test query")

        assert mock_builder.return_value.build.call_args.kwargs["is_multimodal_llm"] is True
        assert mock_render.call_args.kwargs["is_multimodal_llm"] is True

    @pytest.mark.asyncio
    @patch(f"{_SEARCH}.tool_output", side_effect=lambda text, images, state: ["multipart", text, images])
    @patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None))
    async def test_images_are_returned_with_the_text(self, mock_parse, mock_output) -> None:
        state = _full_path_state(_one_hit_retrieval(), is_multimodal_llm=True)
        image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}
        builder_patch, render_patch, blob_patch = _pipeline(images=[image])
        with builder_patch, render_patch, blob_patch:
            result = await execute_search(state, "test query")

        assert result[0] == "multipart"
        assert result[2] == [image]


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
        builder_patch, render_patch, blob_patch = _pipeline(text="Enterprise pricing strategy 2026")
        with builder_patch, render_patch, blob_patch:
            result = await execute_search(_state(retrieval), "pricing", source_ids=["demo-connector"])

        assert retrieval.search_with_filters.await_count == 1
        assert result.startswith("Top 1 block")
        assert "source_ids omitted" not in result


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
        builder_patch, render_patch, blob_patch = _pipeline(text="Fallback content")
        with builder_patch, render_patch, blob_patch:
            result = await execute_search(state, "context graph", entity_ids=["t1"])

        assert retrieval.search_with_filters.call_count == 2
        first_kwargs = retrieval.search_with_filters.call_args_list[0].kwargs
        second_kwargs = retrieval.search_with_filters.call_args_list[1].kwargs
        assert first_kwargs["filter_groups"].get("topics") == ["Context graph governance"]
        assert "topics" not in second_kwargs["filter_groups"]
        assert "Fallback content" in result
        assert "NOT limited to it" in result

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
            new_callable=AsyncMock, return_value=["vr-rg-1"],
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
            new_callable=AsyncMock, return_value=[],
        ):
            result = await execute_search(state, "roadmap", entity_ids=["rg1"])
        parsed = json.loads(result)
        assert parsed["status"] == "success"
        assert parsed["result_count"] == 0
        retrieval.search_with_filters.assert_not_awaited()

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
