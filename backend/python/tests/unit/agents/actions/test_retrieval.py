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
    _build_time_range_from_iso,
    _normalize_list_param,
    _parse_iso_time_bound,
)
from app.utils.time_conversion import parse_timestamp
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
        assert inp.collection_ids is None

    def test_custom_values(self):
        inp = SearchInternalKnowledgeInput(
            query="how to",
            connector_ids=["c1", "c2"],
            collection_ids=["k1"],
        )
        assert inp.connector_ids == ["c1", "c2"]
        assert inp.collection_ids == ["k1"]


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
        ), patch(
            "app.agents.actions.retrieval.retrieval.BlobStorage",
        ), patch(
            "app.agents.actions.retrieval.retrieval.build_message_content_array",
            return_value=([[{"type": "text", "text": "record content"}]], CitationRefMapper()),
        ):
            r = Retrieval(state=state)
            result = await r.search_internal_knowledge(query="test query")
            assert "Retrieved" in result
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
            assert "Retrieved" in result

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
# Time range filtering
# ============================================================================


class TestTimeRangeHelpers:
    def test_iso_with_offset_converted_to_epoch_ms(self):
        after_ms, err = _parse_iso_time_bound("2026-05-14T00:00:00-07:00", "created_after")
        assert err is None
        assert after_ms == 1778742000000

    def test_naive_iso_returns_error(self):
        after_ms, err = _parse_iso_time_bound("2026-05-14T00:00:00", "created_after")
        assert after_ms is None
        assert err is not None
        parsed = json.loads(err)
        assert parsed["status"] == "error"
        assert "timezone offset" in parsed["message"]

    def test_malformed_iso_returns_error(self):
        after_ms, err = _parse_iso_time_bound("last week", "created_after")
        assert after_ms is None
        parsed = json.loads(err)
        assert "ISO 8601" in parsed["message"]

    def test_inverted_range_returns_error(self):
        time_range, err = _build_time_range_from_iso(
            "2026-05-21T00:00:00-07:00",
            "2026-05-14T00:00:00-07:00",
        )
        assert time_range is None
        parsed = json.loads(err)
        assert parsed["status"] == "error"

    def test_open_ended_after_only(self):
        time_range, err = _build_time_range_from_iso("2026-05-14T00:00:00-07:00", None)
        assert err is None
        assert "source_created_after_ms" in time_range
        assert "source_created_before_ms" not in time_range

    def test_open_ended_before_only(self):
        time_range, err = _build_time_range_from_iso(None, "2026-05-21T00:00:00-07:00")
        assert err is None
        assert "source_created_before_ms" in time_range
        assert "source_created_after_ms" not in time_range

    def test_dst_crossing_offset(self):
        after_ms, err = _parse_iso_time_bound("2026-03-08T01:00:00-08:00", "created_after")
        assert err is None
        assert after_ms == parse_timestamp("2026-03-08T01:00:00-08:00")

    def test_future_created_after_rejected(self):
        future_iso = "2099-01-01T00:00:00Z"
        time_range, err = _build_time_range_from_iso(future_iso, None)
        assert time_range is None
        assert err is not None
        parsed = json.loads(err)
        assert parsed["status"] == "error"
        assert "ingestion time" in parsed["message"]
        assert "future" in parsed["message"]
        # Error should steer the LLM toward the approximation fix.
        assert "planning lead time" in parsed["message"]

    def test_future_created_before_allowed(self):
        """Only created_after is guarded; future created_before is fine (open-ended past)."""
        time_range, err = _build_time_range_from_iso(None, "2099-01-01T00:00:00Z")
        assert err is None
        assert "source_created_before_ms" in time_range

    def test_clock_skew_grace_allows_near_now_future(self):
        """A few seconds in the future is within grace; must not be rejected."""
        from datetime import datetime, timedelta, timezone

        near_future = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
        time_range, err = _build_time_range_from_iso(near_future, None)
        assert err is None
        assert "source_created_after_ms" in time_range

    # --- updated_after / updated_before ---

    def test_updated_after_populates_source_updated_after_ms(self):
        time_range, err = _build_time_range_from_iso(
            None, None,
            updated_after="2026-05-14T00:00:00-07:00",
            updated_before=None,
        )
        assert err is None
        assert "source_updated_after_ms" in time_range
        assert time_range["source_updated_after_ms"] == 1778742000000
        assert "source_created_after_ms" not in time_range

    def test_updated_before_populates_source_updated_before_ms(self):
        time_range, err = _build_time_range_from_iso(
            None, None,
            updated_after=None,
            updated_before="2026-05-21T00:00:00-07:00",
        )
        assert err is None
        assert "source_updated_before_ms" in time_range
        assert time_range["source_updated_before_ms"] == 1779346800000

    def test_inverted_updated_range_returns_error(self):
        time_range, err = _build_time_range_from_iso(
            None, None,
            updated_after="2026-05-21T00:00:00-07:00",
            updated_before="2026-05-14T00:00:00-07:00",
        )
        assert time_range is None
        parsed = json.loads(err)
        assert parsed["status"] == "error"
        assert "updated_after" in parsed["message"]

    def test_created_and_updated_combined_in_one_dict(self):
        time_range, err = _build_time_range_from_iso(
            "2026-04-01T00:00:00Z",
            None,
            updated_after="2026-05-14T00:00:00-07:00",
        )
        assert err is None
        assert "source_created_after_ms" in time_range
        assert "source_updated_after_ms" in time_range
        assert "source_created_before_ms" not in time_range
        assert "source_updated_before_ms" not in time_range

    def test_updated_naive_iso_returns_error(self):
        time_range, err = _build_time_range_from_iso(
            None, None, updated_after="2026-05-14T00:00:00"
        )
        assert time_range is None
        parsed = json.loads(err)
        assert "timezone offset" in parsed["message"]


class TestSearchInternalKnowledgeTimeRange:
    @pytest.mark.asyncio
    async def test_iso_bounds_forwarded_to_search_with_filters(self):
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
        await r.search_internal_knowledge(
            query="test",
            created_after="2026-05-14T00:00:00-07:00",
            created_before="2026-05-21T00:00:00-07:00",
        )
        time_range = retrieval_service.search_with_filters.call_args.kwargs["time_range"]
        assert time_range["source_created_after_ms"] == 1778742000000
        assert time_range["source_created_before_ms"] == 1779346800000

    @pytest.mark.asyncio
    async def test_after_greater_than_before_does_not_call_search(self):
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock()
        state = _make_state(retrieval_service=retrieval_service)
        r = Retrieval(state=state)
        result = await r.search_internal_knowledge(
            query="test",
            created_after="2026-05-21T00:00:00-07:00",
            created_before="2026-05-14T00:00:00-07:00",
        )
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        retrieval_service.search_with_filters.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_omitted_bounds_pass_none(self):
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
        assert (
            retrieval_service.search_with_filters.call_args.kwargs.get("time_range") is None
        )

    @pytest.mark.asyncio
    async def test_future_created_after_does_not_call_search(self):
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock()
        state = _make_state(retrieval_service=retrieval_service)
        r = Retrieval(state=state)
        result = await r.search_internal_knowledge(
            query="ECOs scheduled for deployment next week",
            created_after="2099-01-01T00:00:00Z",
        )
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "ingestion time" in parsed["message"]
        assert "planning lead time" in parsed["message"]
        retrieval_service.search_with_filters.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_event_time_query_with_planning_lead_time_approximation(self):
        """LLM follows the lead-time heuristic: future event -> past created_after."""
        from datetime import datetime, timedelta, timezone

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
        four_weeks_ago = (datetime.now(timezone.utc) - timedelta(weeks=4)).isoformat()
        await r.search_internal_knowledge(
            query="ECOs scheduled for deployment next week",
            created_after=four_weeks_ago,
        )
        kwargs = retrieval_service.search_with_filters.call_args.kwargs
        time_range = kwargs["time_range"]
        assert "source_created_after_ms" in time_range
        assert "source_created_before_ms" not in time_range

    @pytest.mark.asyncio
    async def test_unrelated_existing_args_still_work(self):
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
            filters={"apps": ["c1"], "kb": []},
        )
        r = Retrieval(state=state)
        await r.search_internal_knowledge(query="test", connector_ids=["c1"])
        kwargs = retrieval_service.search_with_filters.call_args.kwargs
        assert kwargs["filter_groups"]["apps"] == ["c1"]
        assert kwargs.get("time_range") is None

    @pytest.mark.asyncio
    async def test_updated_after_forwarded_to_search_with_filters(self):
        """updated_after/before should produce source_updated_*_ms keys in time_range."""
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
        await r.search_internal_knowledge(
            query="pages updated last week",
            updated_after="2026-05-14T00:00:00-07:00",
            updated_before="2026-05-21T00:00:00-07:00",
        )
        time_range = retrieval_service.search_with_filters.call_args.kwargs["time_range"]
        assert time_range["source_updated_after_ms"] == 1778742000000
        assert time_range["source_updated_before_ms"] == 1779346800000
        assert "source_created_after_ms" not in time_range
        assert "source_created_before_ms" not in time_range

    @pytest.mark.asyncio
    async def test_inverted_updated_bounds_does_not_call_search(self):
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock()
        state = _make_state(retrieval_service=retrieval_service)
        r = Retrieval(state=state)
        result = await r.search_internal_knowledge(
            query="test",
            updated_after="2026-05-21T00:00:00-07:00",
            updated_before="2026-05-14T00:00:00-07:00",
        )
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        retrieval_service.search_with_filters.assert_not_awaited()
