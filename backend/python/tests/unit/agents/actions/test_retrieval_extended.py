"""
Extended tests for app/agents/actions/retrieval/retrieval.py.

Targets additional coverage for:
- search_internal_knowledge: status_code 202 and 500 error paths
- search_internal_knowledge: collection_ids filtering
- search_internal_knowledge: connector_ids not in agent scope (fallback)
- search_internal_knowledge: collection_ids not in agent scope (fallback)
- search_internal_knowledge: multimodal LLM detection
- search_internal_knowledge: flattened_results empty (uses search_results)
- search_internal_knowledge: filters is None in state
- search_internal_knowledge: exception with no state (fallback logger)
- Retrieval init: state from kwargs (not positional)
- _normalize_list_param: list with non-string items
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.retrieval.retrieval import (
    Retrieval,
    RetrievalToolOutput,
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
        "filters": {"apps": ["app-1", "app-2"], "kb": ["kb-1", "kb-2"]},
        "retrieval_service": AsyncMock(),
        "graph_provider": AsyncMock(),
        "config_service": AsyncMock(),
        "logger": MagicMock(),
        "llm": None,
    }
    state.update(overrides)
    return state


# ============================================================================
# _normalize_list_param edge cases
# ============================================================================


class TestNormalizeListParamExtended:
    def test_dict_returns_none(self):
        """Dict input is not str or list, returns None."""
        assert _normalize_list_param({"key": "val"}) is None

    def test_float_returns_none(self):
        assert _normalize_list_param(3.14) is None

    def test_bool_returns_none(self):
        assert _normalize_list_param(True) is None

    def test_list_with_mixed_types(self):
        """List with mixed types, all converted to strings."""
        result = _normalize_list_param(["hello", 42, True])
        assert result == ["hello", "42", "True"]

    def test_string_with_whitespace_stripped(self):
        result = _normalize_list_param("  hello  ")
        assert result == ["hello"]


# ============================================================================
# RetrievalToolOutput edge cases
# ============================================================================


class TestRetrievalToolOutputExtended:
    def test_model_dump(self):
        output = RetrievalToolOutput(
            content="test content",
            final_results=[{"id": 1}],
            virtual_record_id_to_result={"vr-1": {"id": "r-1"}},
            metadata={"query": "test"},
        )
        dumped = output.model_dump()
        assert dumped["status"] == "success"
        assert dumped["content"] == "test content"
        assert len(dumped["final_results"]) == 1


# ============================================================================
# search_internal_knowledge: status_code 202 and 500
# ============================================================================


class TestSearchStatusCodes:
    @pytest.mark.asyncio
    async def test_status_202_returns_error(self):
        """Status code 202 should return error."""
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={"status_code": 202, "message": "Processing"}
        )
        state = _make_state(retrieval_service=retrieval_service)
        r = Retrieval(state=state)
        result = await r.search_internal_knowledge(query="test")
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert parsed["status_code"] == 202

    @pytest.mark.asyncio
    async def test_status_500_returns_error(self):
        """Status code 500 should return error."""
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={"status_code": 500, "message": "Internal error"}
        )
        state = _make_state(retrieval_service=retrieval_service)
        r = Retrieval(state=state)
        result = await r.search_internal_knowledge(query="test")
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert parsed["status_code"] == 500


# ============================================================================
# search_internal_knowledge: collection_ids filtering
# ============================================================================


class TestCollectionIdsFiltering:
    @pytest.mark.asyncio
    async def test_collection_ids_within_agent_scope(self):
        """Only collection IDs within agent scope are used."""
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={"status_code": 200, "searchResults": [], "virtual_to_record_map": {}}
        )
        state = _make_state(
            retrieval_service=retrieval_service,
            filters={"apps": [], "kb": ["kb-1", "kb-2"]},
        )
        r = Retrieval(state=state)
        await r.search_internal_knowledge(
            query="test", collection_ids=["kb-1", "kb-999"]
        )
        call_kwargs = retrieval_service.search_with_filters.call_args[1]
        # Only kb-1 is in agent scope
        assert call_kwargs["filter_groups"]["kb"] == ["kb-1"]

    @pytest.mark.asyncio
    async def test_collection_ids_none_in_scope_falls_back(self):
        """When no collection IDs match scope, fall back to full agent scope."""
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={"status_code": 200, "searchResults": [], "virtual_to_record_map": {}}
        )
        state = _make_state(
            retrieval_service=retrieval_service,
            filters={"apps": [], "kb": ["kb-1", "kb-2"]},
        )
        r = Retrieval(state=state)
        await r.search_internal_knowledge(
            query="test", collection_ids=["kb-999"]
        )
        call_kwargs = retrieval_service.search_with_filters.call_args[1]
        # Falls back to all agent KB
        assert set(call_kwargs["filter_groups"]["kb"]) == {"kb-1", "kb-2"}


# ============================================================================
# search_internal_knowledge: connector_ids not in scope fallback
# ============================================================================


class TestConnectorIdsNotInScope:
    @pytest.mark.asyncio
    async def test_connector_ids_none_in_scope_falls_back(self):
        """When no connector IDs match scope, fall back to agent scope."""
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={"status_code": 200, "searchResults": [], "virtual_to_record_map": {}}
        )
        state = _make_state(
            retrieval_service=retrieval_service,
            filters={"apps": ["app-1", "app-2"], "kb": []},
        )
        r = Retrieval(state=state)
        await r.search_internal_knowledge(
            query="test", connector_ids=["app-999"]
        )
        call_kwargs = retrieval_service.search_with_filters.call_args[1]
        assert set(call_kwargs["filter_groups"]["apps"]) == {"app-1", "app-2"}


# ============================================================================
# search_internal_knowledge: multimodal LLM detection
# ============================================================================


class TestMultimodalLLMDetection:
    @pytest.mark.asyncio
    async def test_gpt4o_detected_as_multimodal(self):
        """Model with 'gpt-4o' in name should be detected as multimodal."""
        search_results = [{"virtual_record_id": "vr-1", "content": "result 1"}]
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={
                "status_code": 200,
                "searchResults": search_results,
                "virtual_to_record_map": {},
            }
        )

        mock_llm = MagicMock()
        mock_llm.model_name = "gpt-4o-mini"

        state = _make_state(retrieval_service=retrieval_service, llm=mock_llm)

        with patch(
            "app.agents.actions.retrieval.retrieval.get_flattened_results",
            new_callable=AsyncMock,
            return_value=[{"content": "flat"}],
        ) as mock_flatten, patch(
            "app.agents.actions.retrieval.retrieval.BlobStorage",
        ), patch(
            "app.agents.actions.retrieval.retrieval.build_message_content_array",
            return_value=([[{"type": "text", "text": "record content"}]], CitationRefMapper()),
        ):
            r = Retrieval(state=state)
            result = await r.search_internal_knowledge(query="test")
            assert "Retrieved" in result
            mock_flatten.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_claude3_detected_as_multimodal(self):
        """Model with 'claude-3' in name should be detected as multimodal."""
        search_results = [{"virtual_record_id": "vr-1", "content": "result 1"}]
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={
                "status_code": 200,
                "searchResults": search_results,
                "virtual_to_record_map": {},
            }
        )

        mock_llm = MagicMock()
        mock_llm.model_name = "claude-3-opus"

        state = _make_state(retrieval_service=retrieval_service, llm=mock_llm)

        with patch(
            "app.agents.actions.retrieval.retrieval.get_flattened_results",
            new_callable=AsyncMock,
            return_value=[{"content": "flat"}],
        ), patch(
            "app.agents.actions.retrieval.retrieval.BlobStorage",
        ), patch(
            "app.agents.actions.retrieval.retrieval.build_message_content_array",
            return_value=([[{"type": "text", "text": "record content"}]], CitationRefMapper()),
        ):
            r = Retrieval(state=state)
            result = await r.search_internal_knowledge(query="test")
            assert "Retrieved" in result


# ============================================================================
# search_internal_knowledge: flattened_results empty (use raw search_results)
# ============================================================================


class TestFlattenedResultsEmpty:
    @pytest.mark.asyncio
    async def test_empty_flattened_uses_search_results(self):
        """When get_flattened_results returns empty, use original search_results."""
        search_results = [{"virtual_record_id": "vr-1", "content": "raw"}]
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={
                "status_code": 200,
                "searchResults": search_results,
                "virtual_to_record_map": {},
            }
        )
        state = _make_state(retrieval_service=retrieval_service)

        with patch(
            "app.agents.actions.retrieval.retrieval.get_flattened_results",
            new_callable=AsyncMock,
            return_value=[],  # empty
        ), patch(
            "app.agents.actions.retrieval.retrieval.BlobStorage",
        ), patch(
            "app.agents.actions.retrieval.retrieval.build_message_content_array",
            return_value=([[{"type": "text", "text": "record content"}]], CitationRefMapper()),
        ):
            r = Retrieval(state=state)
            result = await r.search_internal_knowledge(query="test")
            assert "Retrieved" in result
            assert "1" in result


# ============================================================================
# search_internal_knowledge: filters is None in state
# ============================================================================


class TestFiltersNoneInState:
    @pytest.mark.asyncio
    async def test_none_filters_defaults_to_empty(self):
        """When filters is None in state, should not crash."""
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={"status_code": 200, "searchResults": [], "virtual_to_record_map": {}}
        )
        state = _make_state(
            retrieval_service=retrieval_service,
            filters=None,
        )
        r = Retrieval(state=state)
        result = await r.search_internal_knowledge(query="test")
        parsed = json.loads(result)
        assert parsed["status"] == "success"


# ============================================================================
# search_internal_knowledge: no connector_ids or collection_ids (defaults)
# ============================================================================


class TestNoFilterIdsProvided:
    @pytest.mark.asyncio
    async def test_no_ids_uses_full_agent_scope(self):
        """When no connector_ids or collection_ids, uses full agent scope."""
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={"status_code": 200, "searchResults": [], "virtual_to_record_map": {}}
        )
        # For non-placeholder agents, broad search should use curated filters.
        state = _make_state(
            retrieval_service=retrieval_service,
            filters={"apps": ["app-1"], "kb": ["kb-1"]},
            apps=["app-from-knowledge"],
            kb=["kb-from-knowledge"],
        )
        r = Retrieval(state=state)
        await r.search_internal_knowledge(query="test")
        call_kwargs = retrieval_service.search_with_filters.call_args[1]
        assert call_kwargs["filter_groups"]["apps"] == ["app-1"]
        assert call_kwargs["filter_groups"]["kb"] == ["kb-1"]

    @pytest.mark.asyncio
    async def test_placeholder_no_ids_uses_state_apps_and_kb(self):
        """Placeholder agents should broaden to state apps/kb instead of filters."""
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={"status_code": 200, "searchResults": [], "virtual_to_record_map": {}}
        )
        state = _make_state(
            retrieval_service=retrieval_service,
            filters={"apps": ["curated-app"], "kb": ["curated-kb"]},
            apps=["a1", "a2"],
            kb=["k1", "k2", "k3"],
            is_placeholder_agent=True,
        )
        r = Retrieval(state=state)
        await r.search_internal_knowledge(query="test")
        call_kwargs = retrieval_service.search_with_filters.call_args[1]
        assert set(call_kwargs["filter_groups"]["apps"]) == {"a1", "a2"}
        assert set(call_kwargs["filter_groups"]["kb"]) == {"k1", "k2", "k3"}

    @pytest.mark.asyncio
    async def test_placeholder_limit_uses_state_scope_counts(self):
        """Placeholder adjusted limit should use state apps/kb count."""
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={"status_code": 200, "searchResults": [], "virtual_to_record_map": {}}
        )
        state = _make_state(
            retrieval_service=retrieval_service,
            filters={"apps": [], "kb": []},
            apps=["a1", "a2"],
            kb=["k1", "k2", "k3"],
            is_placeholder_agent=True,
        )
        r = Retrieval(state=state)
        await r.search_internal_knowledge(query="test")
        call_kwargs = retrieval_service.search_with_filters.call_args[1]
        assert call_kwargs["limit"] == 100 // 5


# ============================================================================
# search_internal_knowledge: exception in state.get for logger
# ============================================================================


class TestExceptionFallbackLogger:
    @pytest.mark.asyncio
    async def test_exception_with_state_uses_state_logger(self):
        """When exception occurs with state, uses state's logger."""
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            side_effect=RuntimeError("search failed")
        )
        mock_logger = MagicMock()
        state = _make_state(
            retrieval_service=retrieval_service,
            logger=mock_logger,
        )
        r = Retrieval(state=state)
        result = await r.search_internal_knowledge(query="test")
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "search failed" in parsed["message"]
        mock_logger.error.assert_called()


# ============================================================================
# search_internal_knowledge: top_k used when limit is None
# ============================================================================


class TestInternalLimitComputation:
    @pytest.mark.asyncio
    async def test_limit_computed_from_base(self):
        """Limit is computed internally from base_limit (50), capped at 100."""
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={"status_code": 200, "searchResults": [], "virtual_to_record_map": {}}
        )
        state = _make_state(retrieval_service=retrieval_service)
        r = Retrieval(state=state)
        await r.search_internal_knowledge(query="test")
        call_kwargs = retrieval_service.search_with_filters.call_args[1]
        assert call_kwargs["limit"] <= 100


# ============================================================================
# search_internal_knowledge: limit adjusted by agent scope size
# ============================================================================


class TestLimitAdjustedByScope:
    @pytest.mark.asyncio
    async def test_limit_divided_by_scope_count(self):
        """When agent has both apps and kbs, limit is divided by total count."""
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={"status_code": 200, "searchResults": [], "virtual_to_record_map": {}}
        )
        state = _make_state(
            retrieval_service=retrieval_service,
            filters={"apps": ["a1", "a2"], "kb": ["k1", "k2", "k3"]},
        )
        r = Retrieval(state=state)
        await r.search_internal_knowledge(query="test")
        call_kwargs = retrieval_service.search_with_filters.call_args[1]
        assert call_kwargs["limit"] == 100 // 5


# ============================================================================
# search_internal_knowledge: virtual_to_record_map passed correctly
# ============================================================================


class TestVirtualToRecordMap:
    @pytest.mark.asyncio
    async def test_virtual_to_record_map_forwarded(self):
        """virtual_to_record_map from search results is forwarded to get_flattened_results."""
        search_results = [{"virtual_record_id": "vr-1", "content": "result"}]
        v2r_map = {"vr-1": {"record_id": "r-1", "web_url": "http://example.com"}}

        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={
                "status_code": 200,
                "searchResults": search_results,
                "virtual_to_record_map": v2r_map,
            }
        )
        state = _make_state(retrieval_service=retrieval_service)

        with patch(
            "app.agents.actions.retrieval.retrieval.get_flattened_results",
            new_callable=AsyncMock,
            return_value=[{"content": "flat"}],
        ) as mock_flatten:
            r = Retrieval(state=state)
            await r.search_internal_knowledge(query="test")
            # Verify virtual_to_record_map was passed
            call_args = mock_flatten.call_args[0]
            assert call_args[5] == v2r_map


# ============================================================================
# Retrieval init: state via kwargs
# ============================================================================


class TestRetrievalInitExtended:
    def test_state_via_kwargs_dict(self):
        """State can be passed via kwargs."""
        state = _make_state()
        r = Retrieval(**{"state": state})
        assert r.state is state

    def test_writer_default_none(self):
        """Writer defaults to None."""
        r = Retrieval(state=_make_state())
        assert r.writer is None


# ============================================================================
# search_internal_knowledge: empty agent filters
# ============================================================================


class TestEmptyAgentFilters:
    @pytest.mark.asyncio
    async def test_empty_agent_apps_and_kbs(self):
        """Empty agent apps and kbs should produce empty filter lists."""
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={"status_code": 200, "searchResults": [], "virtual_to_record_map": {}}
        )
        state = _make_state(
            retrieval_service=retrieval_service,
            filters={"apps": [], "kb": []},
        )
        r = Retrieval(state=state)
        await r.search_internal_knowledge(query="test")
        call_kwargs = retrieval_service.search_with_filters.call_args[1]
        assert call_kwargs["filter_groups"]["apps"] == []
        assert call_kwargs["filter_groups"]["kb"] == []

    @pytest.mark.asyncio
    async def test_connector_ids_with_empty_agent_apps(self):
        """connector_ids provided but agent apps is empty, falls back to empty."""
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={"status_code": 200, "searchResults": [], "virtual_to_record_map": {}}
        )
        state = _make_state(
            retrieval_service=retrieval_service,
            filters={"apps": [], "kb": []},
        )
        r = Retrieval(state=state)
        await r.search_internal_knowledge(
            query="test", connector_ids=["app-999"]
        )
        call_kwargs = retrieval_service.search_with_filters.call_args[1]
        # No match, agent apps is empty too
        assert call_kwargs["filter_groups"]["apps"] == []
