"""
Unit tests for app.agents.actions.retrieval.retrieval

Tests the retrieval tool used by agents for semantic search.
All external dependencies (retrieval_service, graph_provider, BlobStorage) are mocked.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.retrieval.retrieval import (
    Retrieval,
    RetrievalToolOutput,
    SearchInternalKnowledgeInput,
    _dedupe_append_final_results,
    _normalize_list_param,
)
from app.utils.chat_helpers import CitationRefMapper

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state(**overrides):
    """Create a ChatState-like dict with sensible defaults."""
    state = {
        "org_id": "org-1",
        "user_id": "user-1",
        "limit": 50,
        "filters": {"apps": [], "kb": []},
        "retrieval_service": AsyncMock(),
        "graph_provider": AsyncMock(),
        "config_service": AsyncMock(),
        "logger": MagicMock(),
        "llm": None,
    }
    state.update(overrides)
    return state


# ============================================================================
# _normalize_list_param
# ============================================================================

class TestNormalizeListParam:
    def test_none_returns_none(self):
        assert _normalize_list_param(None) is None

    def test_string_returns_list(self):
        result = _normalize_list_param("hello")
        assert result == ["hello"]

    def test_empty_string_returns_none(self):
        assert _normalize_list_param("") is None

    def test_whitespace_string_returns_none(self):
        assert _normalize_list_param("   ") is None

    def test_list_of_strings(self):
        result = _normalize_list_param(["a", "b", "c"])
        assert result == ["a", "b", "c"]

    def test_empty_list_returns_none(self):
        assert _normalize_list_param([]) is None

    def test_list_with_empty_values_filtered(self):
        result = _normalize_list_param(["a", "", None, "b"])
        assert result == ["a", "b"]

    def test_list_all_empty_returns_none(self):
        assert _normalize_list_param(["", None, ""]) is None

    def test_non_string_non_list_returns_none(self):
        assert _normalize_list_param(42) is None

    def test_list_with_ints_converted_to_strings(self):
        result = _normalize_list_param([1, 2, 3])
        assert result == ["1", "2", "3"]


# ============================================================================
# SearchInternalKnowledgeInput
# ============================================================================

class TestSearchInternalKnowledgeInput:
    def test_defaults(self):
        inp = SearchInternalKnowledgeInput(query="test")
        assert inp.query == "test"
        assert inp.connector_ids is None

    def test_custom_values(self):
        inp = SearchInternalKnowledgeInput(
            query="how to",
            connector_ids=["c1", "c2"],
        )
        assert inp.connector_ids == ["c1", "c2"]

    def test_no_collection_ids_field(self):
        """collection_ids has been unified into connector_ids — it no
        longer exists as a separate parameter."""
        assert "collection_ids" not in SearchInternalKnowledgeInput.model_fields


# ============================================================================
# RetrievalToolOutput
# ============================================================================

class TestRetrievalToolOutput:
    def test_defaults(self):
        output = RetrievalToolOutput(
            content="hello",
            final_results=[],
            virtual_record_id_to_result={},
        )
        assert output.status == "success"
        assert output.metadata == {}

    def test_custom_values(self):
        output = RetrievalToolOutput(
            status="error",
            content="something wrong",
            final_results=[{"r": 1}],
            virtual_record_id_to_result={"vr-1": {"id": "r-1"}},
            metadata={"query": "test"},
        )
        assert output.status == "error"
        assert len(output.final_results) == 1
        assert output.metadata["query"] == "test"


# ============================================================================
# Retrieval.__init__
# ============================================================================

class TestRetrievalInit:
    def test_state_from_arg(self):
        state = _make_state()
        r = Retrieval(state=state)
        assert r.state is state

    def test_state_from_kwargs(self):
        state = _make_state()
        r = Retrieval(state=state)
        assert r.state is state

    def test_no_state(self):
        r = Retrieval()
        assert r.state is None


# ============================================================================
# Retrieval.search_internal_knowledge
# ============================================================================

class TestSearchInternalKnowledge:
    @pytest.mark.asyncio
    async def test_no_query_returns_error(self):
        state = _make_state()
        r = Retrieval(state=state)
        result = await r.search_internal_knowledge(query=None)
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "No search query" in parsed["message"]

    @pytest.mark.asyncio
    async def test_empty_query_returns_error(self):
        state = _make_state()
        r = Retrieval(state=state)
        result = await r.search_internal_knowledge(query="")
        parsed = json.loads(result)
        assert parsed["status"] == "error"

    @pytest.mark.asyncio
    async def test_no_state_returns_error(self):
        r = Retrieval(state=None)
        result = await r.search_internal_knowledge(query="test query")
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "not initialized" in parsed["message"]

    @pytest.mark.asyncio
    async def test_no_retrieval_service_returns_error(self):
        state = _make_state(retrieval_service=None)
        r = Retrieval(state=state)
        result = await r.search_internal_knowledge(query="test query")
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "not available" in parsed["message"]

    @pytest.mark.asyncio
    async def test_no_graph_provider_returns_error(self):
        state = _make_state(graph_provider=None)
        r = Retrieval(state=state)
        result = await r.search_internal_knowledge(query="test query")
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "not available" in parsed["message"]

    @pytest.mark.asyncio
    async def test_retrieval_returns_none(self):
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(return_value=None)
        state = _make_state(retrieval_service=retrieval_service)
        r = Retrieval(state=state)
        result = await r.search_internal_knowledge(query="test query")
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "no results" in parsed["message"].lower()

    @pytest.mark.asyncio
    async def test_retrieval_status_503(self):
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={"status_code": 503, "message": "Service unavailable"}
        )
        state = _make_state(retrieval_service=retrieval_service)
        r = Retrieval(state=state)
        result = await r.search_internal_knowledge(query="test query")
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert parsed["status_code"] == 503

    @pytest.mark.asyncio
    async def test_empty_results_returns_success_no_results(self):
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={"status_code": 200, "searchResults": [], "virtual_to_record_map": {}}
        )
        state = _make_state(retrieval_service=retrieval_service)
        r = Retrieval(state=state)
        result = await r.search_internal_knowledge(query="test query")
        parsed = json.loads(result)
        assert parsed["status"] == "success"
        assert parsed["result_count"] == 0

    @pytest.mark.asyncio
    async def test_successful_search_returns_results(self):
        search_results = [
            {"virtual_record_id": "vr-1", "content": "result 1", "score": 0.95},
        ]
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={
                "status_code": 200,
                "searchResults": search_results,
                "virtual_to_record_map": {},
            }
        )
        state = _make_state(retrieval_service=retrieval_service)

        flattened = [{"virtual_record_id": "vr-1", "content": "flat result"}]
        with patch(
            "app.agents.actions.retrieval.retrieval.get_flattened_results",
            new_callable=AsyncMock,
            return_value=flattened,
        ), patch(
            "app.agents.actions.retrieval.retrieval.BlobStorage",
        ), patch(
            "app.agents.actions.retrieval.retrieval.build_message_content_array",
            return_value=([[{"type": "text", "text": "record content"}]], CitationRefMapper()),
        ):
            r = Retrieval(state=state)
            result = await r.search_internal_knowledge(query="test query")
            assert "Top 1 block from 0 records (ranked sample — other records may match)." in result
            assert "1" in result

    @pytest.mark.asyncio
    async def test_results_trimmed_to_adjusted_limit(self):
        """Results are trimmed to internal adjusted_limit."""
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={
                "status_code": 200,
                "searchResults": [{"virtual_record_id": f"vr-{i}", "content": f"r{i}"} for i in range(150)],
                "virtual_to_record_map": {},
            }
        )
        state = _make_state(retrieval_service=retrieval_service)

        with patch(
            "app.agents.actions.retrieval.retrieval.get_flattened_results",
            new_callable=AsyncMock,
            return_value=[{"virtual_record_id": f"vr-{i}", "content": f"r{i}"} for i in range(150)],
        ), patch(
            "app.agents.actions.retrieval.retrieval.BlobStorage",
        ), patch(
            "app.agents.actions.retrieval.retrieval.build_message_content_array",
            return_value=([[{"type": "text", "text": "record content"}]], CitationRefMapper()),
        ):
            r = Retrieval(state=state)
            result = await r.search_internal_knowledge(query="test")
            assert "Top 50 blocks from 0 records (ranked sample — other records may match)." in result

    @pytest.mark.asyncio
    async def test_exception_returns_error(self):
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            side_effect=RuntimeError("search engine down")
        )
        state = _make_state(retrieval_service=retrieval_service)
        r = Retrieval(state=state)
        result = await r.search_internal_knowledge(query="test query")
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "search engine down" in parsed["message"]

    @pytest.mark.asyncio
    async def test_connector_ids_filtered_to_agent_scope(self):
        """Only connector IDs within agent scope are used."""
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={
                "status_code": 200,
                "searchResults": [],
                "virtual_to_record_map": {},
            }
        )
        state = _make_state(
            retrieval_service=retrieval_service,
            filters={"apps": ["app-1", "app-2"], "kb": []},
        )
        r = Retrieval(state=state)
        await r.search_internal_knowledge(
            query="test", connector_ids=["app-1", "app-999"]
        )
        call_kwargs = retrieval_service.search_with_filters.call_args[1]
        # Only app-1 is in agent scope
        assert call_kwargs["filter_groups"]["apps"] == ["app-1"]

    @pytest.mark.asyncio
    async def test_limit_computed_internally(self):
        """Limit is computed internally from base_limit and agent scope."""
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={
                "status_code": 200,
                "searchResults": [],
                "virtual_to_record_map": {},
            }
        )
        state = _make_state(retrieval_service=retrieval_service)
        r = Retrieval(state=state)
        await r.search_internal_knowledge(query="test")
        call_kwargs = retrieval_service.search_with_filters.call_args[1]
        assert call_kwargs["limit"] <= 100


# ============================================================================
# Block dedup on accumulation (Phase 8a)
# ============================================================================


class TestBlockDedupOnAccumulation:
    def test_dedupe_append_skips_duplicate_blocks(self):
        existing = [{"virtual_record_id": "vr-1", "block_index": 0, "content": "a"}]
        new_blocks = [
            {"virtual_record_id": "vr-1", "block_index": 0, "content": "dup"},
            {"virtual_record_id": "vr-1", "block_index": 1, "content": "b"},
        ]
        merged = _dedupe_append_final_results(existing, new_blocks)
        assert len(merged) == 2
        assert merged[0]["content"] == "a"
        assert merged[1]["content"] == "b"

    def test_dedupe_append_keeps_incomplete_keys(self):
        existing = [{"virtual_record_id": "vr-1", "content": "no index"}]
        new_blocks = [
            {"virtual_record_id": "vr-1", "content": "also no index"},
            {"virtual_record_id": "vr-2", "block_index": 0, "content": "ok"},
        ]
        merged = _dedupe_append_final_results(existing, new_blocks)
        assert len(merged) == 3

    @pytest.mark.asyncio
    async def test_two_overlapping_calls_do_not_duplicate_final_results(self):
        """Parallel single-source calls with the same block dedupe in state."""
        block = {"virtual_record_id": "vr-1", "block_index": 0, "content": "shared"}
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={
                "status_code": 200,
                "searchResults": [block],
                "virtual_to_record_map": {},
            }
        )
        state = _make_state(
            retrieval_service=retrieval_service,
            filters={"apps": ["app-1"], "kb": []},
            final_results=[],
        )

        with patch(
            "app.agents.actions.retrieval.retrieval.get_flattened_results",
            new_callable=AsyncMock,
            side_effect=lambda *args, **kwargs: [block],
        ), patch(
            "app.agents.actions.retrieval.retrieval.BlobStorage",
        ), patch(
            "app.agents.actions.retrieval.retrieval.build_message_content_array",
            return_value=([[{"type": "text", "text": "record content"}]], CitationRefMapper()),
        ):
            r = Retrieval(state=state)
            await r.search_internal_knowledge(query="first", connector_ids=["app-1"])
            await r.search_internal_knowledge(query="second", connector_ids=["app-1"])

        assert len(state["final_results"]) == 1
        assert state["final_results"][0]["virtual_record_id"] == "vr-1"
        assert state["final_results"][0]["block_index"] == 0


# ============================================================================
# Multi-ID fan-out (Phase 8b)
# ============================================================================


class TestMultiIdFanOut:
    @pytest.mark.asyncio
    async def test_multi_id_call_fans_out_one_search_per_source(self):
        """One call with multiple connector_ids issues per-source searches."""
        retrieval_service = AsyncMock()

        async def _search_side_effect(**kwargs):
            apps = kwargs["filter_groups"].get("apps") or []
            kbs = kwargs["filter_groups"].get("kb") or []
            source_id = (apps or kbs)[0]
            return {
                "status_code": 200,
                "searchResults": [{"virtual_record_id": f"vr-{source_id}", "content": source_id}],
                "virtual_to_record_map": {f"vr-{source_id}": {"id": source_id}},
            }

        retrieval_service.search_with_filters = AsyncMock(side_effect=_search_side_effect)
        state = _make_state(
            retrieval_service=retrieval_service,
            filters={"apps": ["app-1", "app-2"], "kb": []},
        )

        flattened = [
            {"virtual_record_id": "vr-app-1", "block_index": 0, "content": "a"},
            {"virtual_record_id": "vr-app-2", "block_index": 0, "content": "b"},
        ]

        with patch(
            "app.agents.actions.retrieval.retrieval.get_flattened_results",
            new_callable=AsyncMock,
            return_value=flattened,
        ), patch(
            "app.agents.actions.retrieval.retrieval.BlobStorage",
        ), patch(
            "app.agents.actions.retrieval.retrieval.build_message_content_array",
            return_value=([[{"type": "text", "text": "record content"}]], CitationRefMapper()),
        ):
            r = Retrieval(state=state)
            await r.search_internal_knowledge(
                query="test", connector_ids=["app-1", "app-2"],
            )

        assert retrieval_service.search_with_filters.await_count == 2
        filter_groups_seen = [
            call.kwargs["filter_groups"]
            for call in retrieval_service.search_with_filters.await_args_list
        ]
        assert {"apps": ["app-1"], "kb": ["NO_KB_SELECTED"]} in filter_groups_seen
        assert {"apps": ["app-2"], "kb": ["NO_KB_SELECTED"]} in filter_groups_seen
        for call in retrieval_service.search_with_filters.await_args_list:
            assert call.kwargs["limit"] == 50

    @pytest.mark.asyncio
    async def test_single_id_still_uses_one_search(self):
        """Explicit single-source calls keep the single-call path."""
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={"status_code": 200, "searchResults": [], "virtual_to_record_map": {}}
        )
        state = _make_state(
            retrieval_service=retrieval_service,
            filters={"apps": ["app-1", "app-2"], "kb": []},
        )
        r = Retrieval(state=state)
        await r.search_internal_knowledge(query="test", connector_ids=["app-1"])
        assert retrieval_service.search_with_filters.await_count == 1


# ============================================================================
# Navigate tip — appended when a hierarchical record type is in the results
# ============================================================================

def _make_flattened_results_mock(record_type: str):
    """Build a `get_flattened_results` replacement that mutates the
    `virtual_record_id_to_result` output param the way the real
    implementation does — the tip check reads `record_type` off exactly
    that dict, so a fully-mocked no-op leaves it permanently empty."""

    async def _fake_get_flattened_results(
        search_results, blob_store, org_id, is_multimodal_llm,
        virtual_record_id_to_result, virtual_to_record_map=None, **kwargs,
    ):
        virtual_record_id_to_result["vr-1"] = {"id": "r-1", "record_type": record_type}
        return [{"virtual_record_id": "vr-1", "content": "flat result"}]

    return _fake_get_flattened_results


class TestNavigateTip:
    @pytest.mark.asyncio
    async def test_tip_appended_for_hierarchical_record_type(self):
        search_results = [{"virtual_record_id": "vr-1", "content": "result 1", "score": 0.95}]
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={"status_code": 200, "searchResults": search_results, "virtual_to_record_map": {}}
        )
        state = _make_state(retrieval_service=retrieval_service)

        with patch(
            "app.agents.actions.retrieval.retrieval.get_flattened_results",
            new=_make_flattened_results_mock("TICKET"),
        ), patch(
            "app.agents.actions.retrieval.retrieval.BlobStorage",
        ), patch(
            "app.agents.actions.retrieval.retrieval.build_message_content_array",
            return_value=([[{"type": "text", "text": "record content"}]], CitationRefMapper()),
        ):
            r = Retrieval(state=state)
            result = await r.search_internal_knowledge(query="test query")

        assert "Tip:" in result
        assert "knowledgegraph.navigate" in result

    @pytest.mark.asyncio
    async def test_tip_omitted_for_plain_file_record_type(self):
        search_results = [{"virtual_record_id": "vr-1", "content": "result 1", "score": 0.95}]
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={"status_code": 200, "searchResults": search_results, "virtual_to_record_map": {}}
        )
        state = _make_state(retrieval_service=retrieval_service)

        with patch(
            "app.agents.actions.retrieval.retrieval.get_flattened_results",
            new=_make_flattened_results_mock("FILE"),
        ), patch(
            "app.agents.actions.retrieval.retrieval.BlobStorage",
        ), patch(
            "app.agents.actions.retrieval.retrieval.build_message_content_array",
            return_value=([[{"type": "text", "text": "record content"}]], CitationRefMapper()),
        ):
            r = Retrieval(state=state)
            result = await r.search_internal_knowledge(query="test query")

        assert "Tip:" not in result
