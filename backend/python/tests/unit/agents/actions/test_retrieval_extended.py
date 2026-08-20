"""
Extended tests for app/agents/actions/retrieval/retrieval.py.

Targets additional coverage for:
- search_internal_knowledge: status_code 202 and 500 error paths
- search_internal_knowledge: connector_ids resolving to a KB collection
- search_internal_knowledge: connector_ids not in agent scope (fallback)
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

from app.agent_loop_lib.core.messages import ImagePart, TextPart
from app.agents.actions.retrieval.retrieval import (
    Retrieval,
    RetrievalToolOutput,
    _normalize_list_param,
)
from app.models.blocks import BlockType
from app.utils.chat_helpers import CitationRefMapper, ImageBudget

_MIN_PNG_DATA_URI = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
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
# search_internal_knowledge: connector_ids resolving to a KB collection
#
# There is no separate collection_ids parameter — a KB collection's id IS a
# connector id, so connector_ids alone must resolve into filter_groups["kb"]
# when the supplied id matches the agent's KB scope rather than its app scope.
# ============================================================================


class TestConnectorIdsResolveToKb:
    @pytest.mark.asyncio
    async def test_connector_id_matching_kb_scope_within_agent_scope(self):
        """A connector_ids value that matches agent KB scope routes to filter_groups['kb']."""
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
            query="test", connector_ids=["kb-1", "kb-999"]
        )
        call_kwargs = retrieval_service.search_with_filters.call_args[1]
        # Only kb-1 is in agent scope; apps bucket is excluded since none matched.
        assert call_kwargs["filter_groups"]["kb"] == ["kb-1"]
        assert call_kwargs["filter_groups"]["apps"] == []

    @pytest.mark.asyncio
    async def test_connector_id_none_in_scope_falls_back(self):
        """When no supplied ids match ANY agent scope, fall back to full agent scope."""
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
            query="test", connector_ids=["kb-999"]
        )
        call_kwargs = retrieval_service.search_with_filters.call_args[1]
        # Falls back to all agent KB
        assert set(call_kwargs["filter_groups"]["kb"]) == {"kb-1", "kb-2"}

    @pytest.mark.asyncio
    async def test_connector_id_mix_of_app_and_kb_scopes_both_resolved(self):
        """A single connector_ids call naming one app id and one KB id scopes to exactly both."""
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={"status_code": 200, "searchResults": [], "virtual_to_record_map": {}}
        )
        state = _make_state(
            retrieval_service=retrieval_service,
            filters={"apps": ["app-1"], "kb": ["kb-1"]},
        )
        r = Retrieval(state=state)
        await r.search_internal_knowledge(
            query="test", connector_ids=["app-1", "kb-1"]
        )
        call_kwargs = retrieval_service.search_with_filters.call_args[1]
        assert call_kwargs["filter_groups"]["apps"] == ["app-1"]
        assert call_kwargs["filter_groups"]["kb"] == ["kb-1"]


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
# search_internal_knowledge: is_multimodal_llm flag (state-based, not
# model-name substring matching -- a brittle heuristic that missed GPT-5,
# Gemini 2.x, and any Ollama VLM; see `context.py`'s `AgentContext.
# is_multimodal_llm`, seeded from the LLM config's `isMultimodal` field)
# ============================================================================


class TestMultimodalLLMFlag:
    @pytest.mark.asyncio
    async def test_model_name_alone_does_not_enable_multimodal(self):
        """A model literally named 'gpt-4o' must NOT be treated as
        multimodal unless `state["is_multimodal_llm"]` says so -- the
        LLM's `model_name` attribute is irrelevant to this flag."""
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
        ) as mock_build:
            r = Retrieval(state=state)
            result = await r.search_internal_knowledge(query="test")
            assert "Top 1 block from 0 records (ranked sample — other records may match)." in result
            mock_flatten.assert_awaited_once()
            assert mock_flatten.await_args[0][3] is False  # is_multimodal_llm positional arg
            assert mock_build.call_args[1]["is_multimodal_llm"] is False

    @pytest.mark.asyncio
    async def test_is_multimodal_llm_state_flag_is_honored(self):
        """`state["is_multimodal_llm"] = True` is the sole switch, regardless
        of what the LLM object's `model_name` is (or whether it's set at all)."""
        search_results = [{"virtual_record_id": "vr-1", "content": "result 1"}]
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={
                "status_code": 200,
                "searchResults": search_results,
                "virtual_to_record_map": {},
            }
        )
        state = _make_state(retrieval_service=retrieval_service, is_multimodal_llm=True)

        with patch(
            "app.agents.actions.retrieval.retrieval.get_flattened_results",
            new_callable=AsyncMock,
            return_value=[{"content": "flat"}],
        ) as mock_flatten, patch(
            "app.agents.actions.retrieval.retrieval.BlobStorage",
        ), patch(
            "app.agents.actions.retrieval.retrieval.build_message_content_array",
            return_value=([[{"type": "text", "text": "record content"}]], CitationRefMapper()),
        ) as mock_build:
            r = Retrieval(state=state)
            result = await r.search_internal_knowledge(query="test")
            assert "Top 1 block from 0 records (ranked sample — other records may match)." in result
            assert mock_flatten.await_args[0][3] is True
            assert mock_build.call_args[1]["is_multimodal_llm"] is True


# ============================================================================
# search_internal_knowledge: multipart output with collected images
#
# Exercises the REAL `build_message_content_array` (not mocked) so the
# search -> multipart-tool-result -> image-in-context path is verified
# end-to-end, matching the plan's integration-test item #1.
# ============================================================================


def _image_flatten_side_effect(vr_id: str = "vr-1"):
    async def _fake_flatten(
        search_results, blob_store, org_id, is_multimodal_llm,
        virtual_record_id_to_result, virtual_to_record_map, **kwargs,
    ):
        virtual_record_id_to_result[vr_id] = {
            "frontend_url": "https://app.example.com",
            "id": "rec-1",
            "context_metadata": "ctx",
        }
        return [{
            "virtual_record_id": vr_id,
            "block_index": 0,
            "block_type": BlockType.IMAGE.value,
            "content": _MIN_PNG_DATA_URI,
        }]

    return _fake_flatten


class TestSearchImageMultipart:
    @pytest.mark.asyncio
    async def test_multimodal_llm_with_image_result_returns_multipart_output(self):
        """Search hitting an IMAGE block, with a multimodal LLM, must
        return `[TextPart, ImagePart, ...]` -- not a bare string -- so the
        image actually reaches the model (the bug: 'search with retrieval,
        image blocks not appearing')."""
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={
                "status_code": 200,
                "searchResults": [{"virtual_record_id": "vr-1", "content": "x"}],
                "virtual_to_record_map": {},
            }
        )
        state = _make_state(retrieval_service=retrieval_service, is_multimodal_llm=True)

        with patch(
            "app.agents.actions.retrieval.retrieval.get_flattened_results",
            new_callable=AsyncMock,
            side_effect=_image_flatten_side_effect(),
        ), patch("app.agents.actions.retrieval.retrieval.BlobStorage"):
            r = Retrieval(state=state)
            result = await r.search_internal_knowledge(query="test")

        assert isinstance(result, list)
        assert any(isinstance(p, TextPart) for p in result)
        image_parts = [p for p in result if isinstance(p, ImagePart)]
        assert len(image_parts) == 1
        from app.agent_loop_lib.core.messages import image_data_url

        assert image_parts[0].source.type == "base64"
        from app.agent_loop_lib.core.messages import image_data_url

        assert image_data_url(image_parts[0].source) == _MIN_PNG_DATA_URI
        # No fallback stash: `supports_multipart_tool_result` defaults to
        # True, and `shape_retrieved_image_injection` (its only consumer)
        # is never registered for such models — retaining the images here
        # too would just leak memory across turns.
        assert "pending_tool_images" not in state

    @pytest.mark.asyncio
    async def test_no_native_multipart_support_also_stashes_fallback_images(self):
        """When the resolved chat model lacks native multipart tool-result
        support (Ollama), the search must ALSO stash a fallback copy into
        `pending_tool_images` so `shape_retrieved_image_injection` can
        re-inject it via a `UserMessage` on the next model call."""
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={
                "status_code": 200,
                "searchResults": [{"virtual_record_id": "vr-1", "content": "x"}],
                "virtual_to_record_map": {},
            }
        )
        state = _make_state(
            retrieval_service=retrieval_service, is_multimodal_llm=True,
            supports_multipart_tool_result=False,
        )

        with patch(
            "app.agents.actions.retrieval.retrieval.get_flattened_results",
            new_callable=AsyncMock,
            side_effect=_image_flatten_side_effect(),
        ), patch("app.agents.actions.retrieval.retrieval.BlobStorage"):
            r = Retrieval(state=state)
            result = await r.search_internal_knowledge(query="test")

        assert isinstance(result, list)
        assert len(state["pending_tool_images"]) == 1

    @pytest.mark.asyncio
    async def test_non_multimodal_llm_returns_plain_text_no_image_parts(self):
        """A text-only LLM must never receive an `ImagePart` -- the image
        degrades to a text-only reference instead."""
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={
                "status_code": 200,
                "searchResults": [{"virtual_record_id": "vr-1", "content": "x"}],
                "virtual_to_record_map": {},
            }
        )
        state = _make_state(retrieval_service=retrieval_service, is_multimodal_llm=False)

        with patch(
            "app.agents.actions.retrieval.retrieval.get_flattened_results",
            new_callable=AsyncMock,
            side_effect=_image_flatten_side_effect(),
        ), patch("app.agents.actions.retrieval.retrieval.BlobStorage"):
            r = Retrieval(state=state)
            result = await r.search_internal_knowledge(query="test")

        assert isinstance(result, str)
        assert "pending_tool_images" not in state

    @pytest.mark.asyncio
    async def test_exhausted_image_budget_falls_back_to_text(self):
        """When the shared conversation `ImageBudget` is already exhausted
        (by prior tool calls/attachments this turn), a new search hit on
        an IMAGE block must NOT produce an `ImagePart` -- it degrades to
        text, matching the 50-image-conversation-cap contract."""
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={
                "status_code": 200,
                "searchResults": [{"virtual_record_id": "vr-1", "content": "x"}],
                "virtual_to_record_map": {},
            }
        )
        exhausted_budget = ImageBudget(max_images=1)
        exhausted_budget.try_consume(1)
        state = _make_state(
            retrieval_service=retrieval_service, is_multimodal_llm=True,
            image_budget=exhausted_budget,
        )

        with patch(
            "app.agents.actions.retrieval.retrieval.get_flattened_results",
            new_callable=AsyncMock,
            side_effect=_image_flatten_side_effect(),
        ), patch("app.agents.actions.retrieval.retrieval.BlobStorage"):
            r = Retrieval(state=state)
            result = await r.search_internal_knowledge(query="test")

        assert isinstance(result, str)
        assert "pending_tool_images" not in state


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
            assert "Top 1 block from 0 records (ranked sample — other records may match)." in result
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
# search_internal_knowledge: no connector_ids (defaults)
# ============================================================================


class TestNoFilterIdsProvided:
    @pytest.mark.asyncio
    async def test_no_ids_uses_full_agent_scope(self):
        """When no connector_ids provided, uses full agent scope."""
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
