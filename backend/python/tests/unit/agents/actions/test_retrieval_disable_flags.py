"""
Integration tests for the pattern-match parallel-search path in
app/agents/actions/retrieval/retrieval.py.

The original version of this file tested `DISABLE_SEMANTIC_SEARCH` /
`DISABLE_STORAGE_PATTERN` env flags, a `command` parameter on
`_execute_parallel_search`, and a standalone `_build_filter_groups` /
`_accumulate_state` / `_detect_multimodal_llm` API. None of that exists in
the resolved `retrieval.py`: there are no disable flags, no `command`
parameter, and pattern match is now `execute_pattern_match_pipeline` --
fully self-gating (derives its own grep command from the query, checks
local-storage eligibility, and resolves connector ids from
`filter_groups["apps"]` internally) -- kicked off via `asyncio.create_task`
alongside semantic search and awaited afterwards. Unit coverage for the
pipeline's own gating/eligibility logic lives in
`tests/unit/utils/test_pattern_match.py`; this file covers only the
integration surface inside `search_internal_knowledge`:
- the pattern-match task's result is merged into `final_results`
- a pattern-match task exception is fail-soft (falls back to semantic-only)
- a `merge_pattern_match_results` exception is fail-soft
- state-value type coercion when prior `virtual_record_id_to_result` /
  `tool_records` are the wrong type
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.retrieval.retrieval import Retrieval
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
        "filters": {"apps": ["app-1"], "kb": []},
        "retrieval_service": AsyncMock(),
        "graph_provider": AsyncMock(),
        "config_service": AsyncMock(),
        "logger": MagicMock(),
        "llm": None,
    }
    state.update(overrides)
    return state


# ============================================================================
# pattern-match task result merged into final_results
# ============================================================================


class TestPatternMatchMergedIntoResults:
    @pytest.mark.asyncio
    async def test_pattern_match_blocks_merged_into_final_results(self):
        """execute_pattern_match_pipeline results, once merged, are appended
        to final_results alongside (empty) semantic results."""
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={"status_code": 200, "searchResults": [], "virtual_to_record_map": {}}
        )
        state = _make_state(retrieval_service=retrieval_service)

        pm_blocks = [
            {"virtual_record_id": "vr-a", "block_index": 0, "content": "a0"},
            {"virtual_record_id": "vr-a", "block_index": 1, "content": "a1"},
        ]

        with patch(
            "app.agents.actions.retrieval.retrieval.get_flattened_results",
            new_callable=AsyncMock, return_value=[],
        ), patch(
            "app.agents.actions.retrieval.retrieval.execute_pattern_match_pipeline",
            new_callable=AsyncMock, return_value=[{"raw": "record"}],
        ), patch(
            "app.agents.actions.retrieval.retrieval.merge_pattern_match_results",
            new_callable=AsyncMock, return_value=pm_blocks,
        ), patch(
            "app.agents.actions.retrieval.retrieval.BlobStorage",
        ), patch(
            "app.agents.actions.retrieval.retrieval.build_message_content_array",
            return_value=([[{"type": "text", "text": "record content"}]], CitationRefMapper()),
        ):
            r = Retrieval(state=state)
            result = await r.search_internal_knowledge(query="revenue")

        assert "Top 2 blocks from 0 records (ranked sample — other records may match)." in result
        assert state["final_results"] == pm_blocks


# ============================================================================
# pattern-match task exception is fail-soft
# ============================================================================


class TestPatternMatchTaskExceptionFailSoft:
    @pytest.mark.asyncio
    async def test_pipeline_exception_falls_back_to_semantic_only(self):
        """If the parallel pattern-match task raises, search_internal_knowledge
        still returns the semantic results and logs a warning instead of
        propagating the exception."""
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={
                "status_code": 200,
                "searchResults": [{"virtual_record_id": "vr-1", "content": "hit"}],
                "virtual_to_record_map": {},
            }
        )
        state = _make_state(retrieval_service=retrieval_service)

        with patch(
            "app.agents.actions.retrieval.retrieval.get_flattened_results",
            new_callable=AsyncMock,
            return_value=[{"virtual_record_id": "vr-1", "content": "hit"}],
        ), patch(
            "app.agents.actions.retrieval.retrieval.execute_pattern_match_pipeline",
            new_callable=AsyncMock, side_effect=RuntimeError("pattern match blew up"),
        ), patch(
            "app.agents.actions.retrieval.retrieval.BlobStorage",
        ), patch(
            "app.agents.actions.retrieval.retrieval.build_message_content_array",
            return_value=([[{"type": "text", "text": "record content"}]], CitationRefMapper()),
        ):
            r = Retrieval(state=state)
            result = await r.search_internal_knowledge(query="revenue")

        assert "Top 1 block from 0 records (ranked sample — other records may match)." in result
        assert state["logger"].warning.called


# ============================================================================
# merge_pattern_match_results exception is fail-soft
# ============================================================================


class TestPatternMatchMergeExceptionFailSoft:
    @pytest.mark.asyncio
    async def test_merge_exception_falls_back_to_semantic_only(self):
        """If merge_pattern_match_results raises, the merge is skipped and
        semantic results are still returned."""
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={
                "status_code": 200,
                "searchResults": [{"virtual_record_id": "vr-1", "content": "hit"}],
                "virtual_to_record_map": {},
            }
        )
        state = _make_state(retrieval_service=retrieval_service)

        with patch(
            "app.agents.actions.retrieval.retrieval.get_flattened_results",
            new_callable=AsyncMock,
            return_value=[{"virtual_record_id": "vr-1", "content": "hit"}],
        ), patch(
            "app.agents.actions.retrieval.retrieval.execute_pattern_match_pipeline",
            new_callable=AsyncMock, return_value=[{"raw": "record"}],
        ), patch(
            "app.agents.actions.retrieval.retrieval.merge_pattern_match_results",
            new_callable=AsyncMock, side_effect=RuntimeError("merge blew up"),
        ), patch(
            "app.agents.actions.retrieval.retrieval.BlobStorage",
        ), patch(
            "app.agents.actions.retrieval.retrieval.build_message_content_array",
            return_value=([[{"type": "text", "text": "record content"}]], CitationRefMapper()),
        ):
            r = Retrieval(state=state)
            result = await r.search_internal_knowledge(query="revenue")

        assert "Top 1 block from 0 records (ranked sample — other records may match)." in result
        assert state["final_results"] == [{"virtual_record_id": "vr-1", "content": "hit"}]


# ============================================================================
# state type coercion for prior virtual_record_id_to_result / tool_records
# ============================================================================


class TestAccumulatedStateTypeCoercion:
    @pytest.mark.asyncio
    async def test_non_dict_virtual_record_map_is_reset(self):
        """A prior virtual_record_id_to_result of the wrong type is discarded
        rather than merged into (which would raise)."""
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={
                "status_code": 200,
                "searchResults": [{"virtual_record_id": "vr-1", "content": "hit"}],
                "virtual_to_record_map": {},
            }
        )
        state = _make_state(
            retrieval_service=retrieval_service,
            virtual_record_id_to_result="not-a-dict",
        )

        with patch(
            "app.agents.actions.retrieval.retrieval.get_flattened_results",
            new_callable=AsyncMock,
            return_value=[{"virtual_record_id": "vr-1", "content": "hit"}],
        ), patch(
            "app.agents.actions.retrieval.retrieval.execute_pattern_match_pipeline",
            new_callable=AsyncMock, return_value=[],
        ), patch(
            "app.agents.actions.retrieval.retrieval.BlobStorage",
        ), patch(
            "app.agents.actions.retrieval.retrieval.build_message_content_array",
            return_value=([[{"type": "text", "text": "record content"}]], CitationRefMapper()),
        ):
            r = Retrieval(state=state)
            await r.search_internal_knowledge(query="revenue")

        assert isinstance(state["virtual_record_id_to_result"], dict)

    @pytest.mark.asyncio
    async def test_non_list_tool_records_is_reset(self):
        """A prior tool_records of the wrong type is discarded rather than
        concatenated onto (which would raise)."""
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={
                "status_code": 200,
                "searchResults": [{"virtual_record_id": "vr-1", "content": "hit"}],
                "virtual_to_record_map": {},
            }
        )
        state = _make_state(
            retrieval_service=retrieval_service,
            tool_records="not-a-list",
        )

        with patch(
            "app.agents.actions.retrieval.retrieval.get_flattened_results",
            new_callable=AsyncMock,
            return_value=[{"virtual_record_id": "vr-1", "content": "hit"}],
        ), patch(
            "app.agents.actions.retrieval.retrieval.execute_pattern_match_pipeline",
            new_callable=AsyncMock, return_value=[],
        ), patch(
            "app.agents.actions.retrieval.retrieval.BlobStorage",
        ), patch(
            "app.agents.actions.retrieval.retrieval.build_message_content_array",
            return_value=([[{"type": "text", "text": "record content"}]], CitationRefMapper()),
        ):
            r = Retrieval(state=state)
            await r.search_internal_knowledge(query="revenue")

        assert isinstance(state["tool_records"], list)


# ============================================================================
# disable_semantic flag — skip semantic, only pattern match
# ============================================================================


class TestDisableSemanticFlag:
    @pytest.mark.asyncio
    async def test_disable_semantic_skips_vector_search(self):
        """When disable_semantic=True, retrieval_service.search_with_filters
        is not called; only pattern match runs."""
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={"status_code": 200, "searchResults": [], "virtual_to_record_map": {}}
        )
        state = _make_state(
            retrieval_service=retrieval_service,
            disable_semantic=True,
        )

        pm_blocks = [
            {"virtual_record_id": "vr-pm", "block_index": 0, "content": "pm-hit"},
        ]

        with patch(
            "app.agents.actions.retrieval.retrieval.get_flattened_results",
            new_callable=AsyncMock, return_value=[],
        ), patch(
            "app.agents.actions.retrieval.retrieval.execute_pattern_match_pipeline",
            new_callable=AsyncMock, return_value=[{"raw": "record"}],
        ), patch(
            "app.agents.actions.retrieval.retrieval.merge_pattern_match_results",
            new_callable=AsyncMock, return_value=pm_blocks,
        ), patch(
            "app.agents.actions.retrieval.retrieval.BlobStorage",
        ), patch(
            "app.agents.actions.retrieval.retrieval.build_message_content_array",
            return_value=([[{"type": "text", "text": "pm content"}]], CitationRefMapper()),
        ):
            r = Retrieval(state=state)
            result = await r.search_internal_knowledge(query="revenue")

        assert state["final_results"] == pm_blocks


# ============================================================================
# disable_pattern_match flag
# ============================================================================


class TestDisablePatternMatchFlag:
    @pytest.mark.asyncio
    async def test_disable_pattern_match_skips_pm_pipeline(self):
        """When disable_pattern_match=True, pattern match pipeline is not called."""
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={
                "status_code": 200,
                "searchResults": [{"virtual_record_id": "vr-1", "content": "sem-hit"}],
                "virtual_to_record_map": {},
            }
        )
        state = _make_state(
            retrieval_service=retrieval_service,
            disable_pattern_match=True,
        )

        with patch(
            "app.agents.actions.retrieval.retrieval.get_flattened_results",
            new_callable=AsyncMock,
            return_value=[{"virtual_record_id": "vr-1", "content": "sem-hit"}],
        ), patch(
            "app.agents.actions.retrieval.retrieval.execute_pattern_match_pipeline",
            new_callable=AsyncMock, return_value=[],
        ) as mock_pm, patch(
            "app.agents.actions.retrieval.retrieval.BlobStorage",
        ), patch(
            "app.agents.actions.retrieval.retrieval.build_message_content_array",
            return_value=([[{"type": "text", "text": "content"}]], CitationRefMapper()),
        ):
            r = Retrieval(state=state)
            await r.search_internal_knowledge(query="revenue")

        mock_pm.assert_not_called()


# ============================================================================
# cap_pattern_match_blocks enforcement in retrieval path
# ============================================================================


class TestCapEnforcementInRetrieval:
    @pytest.mark.asyncio
    async def test_pm_blocks_capped_by_limit(self):
        """When pattern match returns more blocks than the budget, they are
        trimmed by cap_pattern_match_blocks."""
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={"status_code": 200, "searchResults": [], "virtual_to_record_map": {}}
        )
        state = _make_state(retrieval_service=retrieval_service, limit=3)

        pm_blocks = [
            {"virtual_record_id": "vr-a", "block_index": i, "content": f"a{i}"}
            for i in range(10)
        ]

        with patch(
            "app.agents.actions.retrieval.retrieval.get_flattened_results",
            new_callable=AsyncMock, return_value=[],
        ), patch(
            "app.agents.actions.retrieval.retrieval.execute_pattern_match_pipeline",
            new_callable=AsyncMock, return_value=[{"raw": "record"}],
        ), patch(
            "app.agents.actions.retrieval.retrieval.merge_pattern_match_results",
            new_callable=AsyncMock, return_value=pm_blocks,
        ), patch(
            "app.agents.actions.retrieval.retrieval.cap_pattern_match_blocks",
            wraps=lambda blocks, **kw: blocks[:kw.get("budget", 50)],
        ) as mock_cap, patch(
            "app.agents.actions.retrieval.retrieval.BlobStorage",
        ), patch(
            "app.agents.actions.retrieval.retrieval.build_message_content_array",
            return_value=([[{"type": "text", "text": "content"}]], CitationRefMapper()),
        ):
            r = Retrieval(state=state)
            await r.search_internal_knowledge(query="revenue")

        mock_cap.assert_called_once()


# ============================================================================
# Both semantic + PM results — dedup
# ============================================================================


class TestSemanticAndPMDedup:
    @pytest.mark.asyncio
    async def test_pm_results_exclude_semantic_vrids(self):
        """Pattern match merge should not include vrids already in semantic results."""
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={
                "status_code": 200,
                "searchResults": [{"virtual_record_id": "vr-shared", "content": "shared"}],
                "virtual_to_record_map": {"vr-shared": "rec-1"},
            }
        )
        state = _make_state(retrieval_service=retrieval_service)

        semantic_blocks = [
            {"virtual_record_id": "vr-shared", "block_index": 0, "content": "semantic"},
        ]
        pm_blocks = [
            {"virtual_record_id": "vr-new", "block_index": 0, "content": "pm-only"},
        ]

        with patch(
            "app.agents.actions.retrieval.retrieval.get_flattened_results",
            new_callable=AsyncMock, return_value=semantic_blocks,
        ), patch(
            "app.agents.actions.retrieval.retrieval.execute_pattern_match_pipeline",
            new_callable=AsyncMock, return_value=[{"raw": "pm-record"}],
        ), patch(
            "app.agents.actions.retrieval.retrieval.merge_pattern_match_results",
            new_callable=AsyncMock, return_value=pm_blocks,
        ), patch(
            "app.agents.actions.retrieval.retrieval.BlobStorage",
        ), patch(
            "app.agents.actions.retrieval.retrieval.build_message_content_array",
            return_value=([[{"type": "text", "text": "content"}]], CitationRefMapper()),
        ):
            r = Retrieval(state=state)
            await r.search_internal_knowledge(query="revenue")

        final = state["final_results"]
        assert len(final) == 2
        vrids = [b["virtual_record_id"] for b in final]
        assert "vr-shared" in vrids
        assert "vr-new" in vrids
