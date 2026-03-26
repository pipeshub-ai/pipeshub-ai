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
    _normalize_list_param,
)


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
        assert inp.limit == 50
        assert inp.connector_ids is None
        assert inp.collection_ids is None
        assert inp.top_k is None

    def test_custom_values(self):
        inp = SearchInternalKnowledgeInput(
            query="how to",
            limit=20,
            connector_ids=["c1", "c2"],
            collection_ids=["k1"],
            top_k=10,
        )
        assert inp.limit == 20
        assert inp.connector_ids == ["c1", "c2"]
        assert inp.top_k == 10


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

    def test_writer_stored(self):
        writer = MagicMock()
        r = Retrieval(state=_make_state(), writer=writer)
        assert r.writer is writer

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
        ):
            r = Retrieval(state=state)
            result = await r.search_internal_knowledge(query="test query")
            parsed = json.loads(result)
            assert parsed["status"] == "success"
            assert len(parsed["final_results"]) == 1

    @pytest.mark.asyncio
    async def test_limit_capped_at_100(self):
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={
                "status_code": 200,
                "searchResults": [{"r": i} for i in range(150)],
                "virtual_to_record_map": {},
            }
        )
        state = _make_state(retrieval_service=retrieval_service)

        with patch(
            "app.agents.actions.retrieval.retrieval.get_flattened_results",
            new_callable=AsyncMock,
            return_value=[{"r": i} for i in range(150)],
        ):
            r = Retrieval(state=state)
            result = await r.search_internal_knowledge(query="test", limit=200)
            parsed = json.loads(result)
            assert len(parsed["final_results"]) <= 100

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
    async def test_top_k_alias_for_limit(self):
        """top_k should be used when limit is not provided."""
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
        await r.search_internal_knowledge(query="test", top_k=25)
        call_kwargs = retrieval_service.search_with_filters.call_args[1]
        assert call_kwargs["limit"] == 25
