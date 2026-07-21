"""
Tests targeting env-based disable flags and other uncovered branches in
app/agents/actions/retrieval/retrieval.py.

Covers:
- DISABLE_SEMANTIC_SEARCH / DISABLE_STORAGE_PATTERN env flags in
  ``_execute_parallel_search`` (both independently and combined)
- Pattern-match command rejection warning (invalid command)
- Pattern-match task raising an exception during asyncio.gather
- ``_build_filter_groups`` placeholder-agent kb override in the
  "explicit connectors only" branch (NO_KB_SELECTED vs [])
- ``_detect_multimodal_llm`` swallowing an unexpected exception
- ``_accumulate_state`` coercing non-list/non-dict prior state values
- The pattern-match merge/budget-distribution block in
  ``search_internal_knowledge`` (small batch vs. over-budget distribution
  with orphan vrid pruning)
"""

import json
import os
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


_VALID_COMMAND = 'grep -ri "revenue" .'


# ============================================================================
# DISABLE_SEMANTIC_SEARCH
# ============================================================================


class TestDisableSemanticSearchFlag:
    @pytest.mark.asyncio
    async def test_disable_semantic_true_skips_search_and_returns_stub(self):
        """DISABLE_SEMANTIC_SEARCH=true → search_with_filters NOT called,
        semantic_response is an empty stub."""
        state = _make_state()
        state["retrieval_service"].search_with_filters = AsyncMock(
            return_value={"status_code": 200, "searchResults": [{"unexpected": "data"}]}
        )
        retrieval = Retrieval(state=state)

        with patch.dict(os.environ, {"DISABLE_SEMANTIC_SEARCH": "true"}):
            semantic_response, raw_pattern_records = await retrieval._execute_parallel_search(
                search_query="revenue",
                filter_groups={"apps": ["conn-1"], "kb": []},
                adjusted_limit=10,
                command=None,
                connector_ids_in_scope=["conn-1"],
                retrieval_service=state["retrieval_service"],
                logger_instance=state["logger"],
            )

        state["retrieval_service"].search_with_filters.assert_not_awaited()
        assert semantic_response == {"status_code": 200, "searchResults": []}
        assert raw_pattern_records == []

    @pytest.mark.asyncio
    async def test_disable_semantic_false_runs_search_normally(self):
        """Control case: with the flag unset (default), semantic search runs."""
        state = _make_state()
        state["retrieval_service"].search_with_filters = AsyncMock(
            return_value={"status_code": 200, "searchResults": [{"virtual_record_id": "vr-1"}]}
        )
        retrieval = Retrieval(state=state)

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DISABLE_SEMANTIC_SEARCH", None)
            semantic_response, _raw_pattern_records = await retrieval._execute_parallel_search(
                search_query="revenue",
                filter_groups={"apps": ["conn-1"], "kb": []},
                adjusted_limit=10,
                command=None,
                connector_ids_in_scope=["conn-1"],
                retrieval_service=state["retrieval_service"],
                logger_instance=state["logger"],
            )

        state["retrieval_service"].search_with_filters.assert_awaited_once()
        assert semantic_response == {"status_code": 200, "searchResults": [{"virtual_record_id": "vr-1"}]}


# ============================================================================
# DISABLE_STORAGE_PATTERN
# ============================================================================


class TestDisableStoragePatternFlag:
    @pytest.mark.asyncio
    async def test_disable_pattern_true_skips_pattern_even_with_valid_command(self):
        """DISABLE_STORAGE_PATTERN=true → pattern match not run even though the
        command is valid and storage is local."""
        state = _make_state()
        state["config_service"].get_config = AsyncMock(return_value={"storageType": "local"})
        state["retrieval_service"].search_with_filters = AsyncMock(
            return_value={"status_code": 200, "searchResults": []}
        )
        retrieval = Retrieval(state=state)

        with patch(
            "app.agents.actions.retrieval.retrieval.run_pattern_match",
            new_callable=AsyncMock,
        ) as mock_run_pm, patch.dict(os.environ, {"DISABLE_STORAGE_PATTERN": "true"}):
            semantic_response, raw_pattern_records = await retrieval._execute_parallel_search(
                search_query="revenue",
                filter_groups={"apps": ["conn-1"], "kb": []},
                adjusted_limit=10,
                command=_VALID_COMMAND,
                connector_ids_in_scope=["conn-1"],
                retrieval_service=state["retrieval_service"],
                logger_instance=state["logger"],
            )

        mock_run_pm.assert_not_awaited()
        # Storage config should not even be read since pm_command_valid gates it.
        state["config_service"].get_config.assert_not_awaited()
        assert raw_pattern_records == []
        assert semantic_response == {"status_code": 200, "searchResults": []}
        state["logger"].info.assert_any_call(
            "Storage pattern match disabled via DISABLE_STORAGE_PATTERN"
        )

    @pytest.mark.asyncio
    async def test_disable_pattern_false_runs_pattern_normally(self):
        """Control case: flag unset, valid command + local storage → pattern match runs."""
        state = _make_state()
        state["config_service"].get_config = AsyncMock(return_value={"storageType": "local"})
        state["retrieval_service"].search_with_filters = AsyncMock(
            return_value={"status_code": 200, "searchResults": []}
        )
        retrieval = Retrieval(state=state)

        with patch(
            "app.agents.actions.retrieval.retrieval.run_pattern_match",
            new_callable=AsyncMock,
            return_value=[{"virtual_record_id": "vr-1"}],
        ) as mock_run_pm, patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DISABLE_STORAGE_PATTERN", None)
            _semantic_response, raw_pattern_records = await retrieval._execute_parallel_search(
                search_query="revenue",
                filter_groups={"apps": ["conn-1"], "kb": []},
                adjusted_limit=10,
                command=_VALID_COMMAND,
                connector_ids_in_scope=["conn-1"],
                retrieval_service=state["retrieval_service"],
                logger_instance=state["logger"],
            )

        mock_run_pm.assert_awaited_once()
        assert raw_pattern_records == [{"virtual_record_id": "vr-1"}]


# ============================================================================
# Both flags disabled
# ============================================================================


class TestBothDisableFlags:
    @pytest.mark.asyncio
    async def test_both_disabled_skips_both_and_no_parallel_tasks(self):
        """Both DISABLE_SEMANTIC_SEARCH and DISABLE_STORAGE_PATTERN true →
        no tasks are scheduled (parallel_results == []), stub semantic
        response is returned and pattern match results are empty."""
        state = _make_state()
        state["retrieval_service"].search_with_filters = AsyncMock()
        retrieval = Retrieval(state=state)

        with patch(
            "app.agents.actions.retrieval.retrieval.run_pattern_match",
            new_callable=AsyncMock,
        ) as mock_run_pm, patch.dict(
            os.environ,
            {"DISABLE_SEMANTIC_SEARCH": "true", "DISABLE_STORAGE_PATTERN": "true"},
        ):
            semantic_response, raw_pattern_records = await retrieval._execute_parallel_search(
                search_query="revenue",
                filter_groups={"apps": ["conn-1"], "kb": []},
                adjusted_limit=10,
                command=_VALID_COMMAND,
                connector_ids_in_scope=["conn-1"],
                retrieval_service=state["retrieval_service"],
                logger_instance=state["logger"],
            )

        state["retrieval_service"].search_with_filters.assert_not_awaited()
        mock_run_pm.assert_not_awaited()
        assert semantic_response == {"status_code": 200, "searchResults": []}
        assert raw_pattern_records == []


# ============================================================================
# Pattern-match command rejected (invalid command, not disabled)
# ============================================================================


class TestPatternMatchCommandRejected:
    @pytest.mark.asyncio
    async def test_invalid_command_logs_warning_and_skips_pattern_match(self):
        state = _make_state()
        state["retrieval_service"].search_with_filters = AsyncMock(
            return_value={"status_code": 200, "searchResults": []}
        )
        retrieval = Retrieval(state=state)

        with patch(
            "app.agents.actions.retrieval.retrieval.run_pattern_match",
            new_callable=AsyncMock,
        ) as mock_run_pm:
            _semantic_response, raw_pattern_records = await retrieval._execute_parallel_search(
                search_query="revenue",
                filter_groups={"apps": ["conn-1"], "kb": []},
                adjusted_limit=10,
                command="rm -rf /",  # disallowed binary
                connector_ids_in_scope=["conn-1"],
                retrieval_service=state["retrieval_service"],
                logger_instance=state["logger"],
            )

        mock_run_pm.assert_not_awaited()
        assert raw_pattern_records == []
        assert state["logger"].warning.call_args[0][0] == "Pattern match command rejected: %s"


# ============================================================================
# Pattern-match task raises during gather
# ============================================================================


class TestPatternMatchTaskException:
    @pytest.mark.asyncio
    async def test_pattern_match_exception_logged_and_results_empty(self):
        state = _make_state()
        state["config_service"].get_config = AsyncMock(return_value={"storageType": "local"})
        state["retrieval_service"].search_with_filters = AsyncMock(
            return_value={"status_code": 200, "searchResults": [{"virtual_record_id": "vr-1"}]}
        )
        retrieval = Retrieval(state=state)

        with patch(
            "app.agents.actions.retrieval.retrieval.run_pattern_match",
            new_callable=AsyncMock,
            side_effect=RuntimeError("pattern match blew up"),
        ):
            semantic_response, raw_pattern_records = await retrieval._execute_parallel_search(
                search_query="revenue",
                filter_groups={"apps": ["conn-1"], "kb": []},
                adjusted_limit=10,
                command=_VALID_COMMAND,
                connector_ids_in_scope=["conn-1"],
                retrieval_service=state["retrieval_service"],
                logger_instance=state["logger"],
            )

        assert raw_pattern_records == []
        assert semantic_response == {
            "status_code": 200,
            "searchResults": [{"virtual_record_id": "vr-1"}],
        }
        assert state["logger"].error.called
        error_args = state["logger"].error.call_args[0]
        assert error_args[0] == "Pattern match failed: %s"


# ============================================================================
# _build_filter_groups: placeholder-agent kb override (explicit connectors,
# no explicit collections branch)
# ============================================================================


class TestBuildFilterGroupsPlaceholderKbOverride:
    def test_non_placeholder_defaults_kb_to_no_kb_selected_sentinel(self):
        """Explicit connector_ids given, no collection_ids → kb defaults to
        the NO_KB_SELECTED sentinel for a non-placeholder agent."""
        state = _make_state(filters={"apps": ["app-1"], "kb": ["kb-1"]})
        retrieval = Retrieval(state=state)
        filter_groups, _adjusted_limit = retrieval._build_filter_groups(
            connector_ids=["app-1"],
            collection_ids=None,
            logger_instance=state["logger"],
        )
        assert filter_groups["kb"] == ["NO_KB_SELECTED"]

    def test_placeholder_agent_overrides_kb_to_empty_list(self):
        """Same scenario, but is_placeholder_agent=True → kb becomes []
        instead of the sentinel."""
        state = _make_state(
            filters={"apps": ["app-1"], "kb": ["kb-1"]},
            is_placeholder_agent=True,
            apps=["app-1"],
            kb=["kb-1"],
        )
        retrieval = Retrieval(state=state)
        filter_groups, _adjusted_limit = retrieval._build_filter_groups(
            connector_ids=["app-1"],
            collection_ids=None,
            logger_instance=state["logger"],
        )
        assert filter_groups["kb"] == []


# ============================================================================
# _detect_multimodal_llm: unexpected exception is swallowed
# ============================================================================


class TestDetectMultimodalLLMException:
    def test_exception_accessing_model_name_returns_false(self):
        class _BadLLM:
            @property
            def model_name(self) -> str:
                raise RuntimeError("boom")

        state = _make_state(llm=_BadLLM())
        retrieval = Retrieval(state=state)
        assert retrieval._detect_multimodal_llm() is False


# ============================================================================
# _accumulate_state: coercion when prior state values are the wrong type
# ============================================================================


class TestAccumulateStateTypeCoercion:
    def test_non_list_final_results_is_reset(self):
        state = _make_state(final_results="not-a-list")
        retrieval = Retrieval(state=state)
        retrieval._accumulate_state(
            final_results=[{"block": 1}],
            virtual_record_id_to_result={},
        )
        assert state["final_results"] == [{"block": 1}]

    def test_non_dict_virtual_record_map_is_reset(self):
        state = _make_state(virtual_record_id_to_result="not-a-dict")
        retrieval = Retrieval(state=state)
        retrieval._accumulate_state(
            final_results=[],
            virtual_record_id_to_result={"vr-1": {"id": "r-1"}},
        )
        assert state["virtual_record_id_to_result"] == {"vr-1": {"id": "r-1"}}

    def test_non_list_tool_records_is_reset(self):
        state = _make_state(tool_records="not-a-list")
        retrieval = Retrieval(state=state)
        retrieval._accumulate_state(
            final_results=[],
            virtual_record_id_to_result={"vr-1": {"_id": "r-1"}},
        )
        assert state["tool_records"] == [{"_id": "r-1"}]


# ============================================================================
# search_internal_knowledge: pattern-match merge / budget distribution
# ============================================================================


class TestPatternMatchMergeAndDistribution:
    @pytest.mark.asyncio
    async def test_small_pm_batch_extends_final_results_directly(self):
        """len(pm_blocks) <= adjusted_limit → straight extend, no distribution."""
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={"status_code": 200, "searchResults": [], "virtual_to_record_map": {}}
        )
        state = _make_state(retrieval_service=retrieval_service)
        state["config_service"].get_config = AsyncMock(return_value={"storageType": "local"})

        pm_blocks = [
            {"virtual_record_id": "vr-a", "block_index": 0, "content": "a0"},
            {"virtual_record_id": "vr-a", "block_index": 1, "content": "a1"},
        ]

        with patch(
            "app.agents.actions.retrieval.retrieval.get_flattened_results",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "app.agents.actions.retrieval.retrieval.run_pattern_match",
            new_callable=AsyncMock,
            return_value=[{"raw": "record"}],
        ), patch(
            "app.agents.actions.retrieval.retrieval.merge_pattern_match_results",
            new_callable=AsyncMock,
            return_value=pm_blocks,
        ), patch(
            "app.agents.actions.retrieval.retrieval.BlobStorage",
        ), patch(
            "app.agents.actions.retrieval.retrieval.build_message_content_array",
            return_value=([[{"type": "text", "text": "record content"}]], CitationRefMapper()),
        ):
            r = Retrieval(state=state)
            result = await r.search_internal_knowledge(
                query="revenue", command=_VALID_COMMAND
            )

        assert "Retrieved 2 knowledge blocks" in result
        assert len(state["final_results"]) == 2

    @pytest.mark.asyncio
    async def test_over_budget_pm_batch_is_distributed_with_orphan_pruning(self):
        """len(pm_blocks) > adjusted_limit → proportional distribution across
        records, with any record that gets fully squeezed out pruned from
        virtual_record_id_to_result."""
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={"status_code": 200, "searchResults": [], "virtual_to_record_map": {}}
        )
        # apps has 1 entry -> adjusted_limit == 50 (see _build_filter_groups)
        state = _make_state(
            retrieval_service=retrieval_service,
            filters={"apps": ["app-1"], "kb": []},
        )
        state["config_service"].get_config = AsyncMock(return_value={"storageType": "local"})

        # 4 records: vr-a (100 blocks), vr-b (5 blocks), vr-c (1 block),
        # vr-d (1 block). Budget (50) is fully consumed by vr-a + vr-b + vr-c,
        # so vr-d should be pruned entirely (orphaned) once budget_left hits 0
        # before the loop reaches it.
        pm_blocks = (
            [{"virtual_record_id": "vr-a", "block_index": i} for i in range(100)]
            + [{"virtual_record_id": "vr-b", "block_index": i} for i in range(5)]
            + [{"virtual_record_id": "vr-c", "block_index": 0}]
            + [{"virtual_record_id": "vr-d", "block_index": 0}]
        )

        async def _fake_merge(*, virtual_record_id_to_result, **_kwargs):
            virtual_record_id_to_result["vr-a"] = {"_id": "vr-a"}
            virtual_record_id_to_result["vr-b"] = {"_id": "vr-b"}
            virtual_record_id_to_result["vr-c"] = {"_id": "vr-c"}
            virtual_record_id_to_result["vr-d"] = {"_id": "vr-d"}
            return pm_blocks

        with patch(
            "app.agents.actions.retrieval.retrieval.get_flattened_results",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "app.agents.actions.retrieval.retrieval.run_pattern_match",
            new_callable=AsyncMock,
            return_value=[{"raw": "record"}],
        ), patch(
            "app.agents.actions.retrieval.retrieval.merge_pattern_match_results",
            new_callable=AsyncMock,
            side_effect=_fake_merge,
        ), patch(
            "app.agents.actions.retrieval.retrieval.BlobStorage",
        ), patch(
            "app.agents.actions.retrieval.retrieval.build_message_content_array",
            return_value=([[{"type": "text", "text": "record content"}]], CitationRefMapper()),
        ):
            r = Retrieval(state=state)
            result = await r.search_internal_knowledge(
                query="revenue", command=_VALID_COMMAND
            )

        # Distributed total should be capped at adjusted_limit (50), never the
        # full 107 raw blocks.
        assert len(state["final_results"]) <= 50
        assert len(state["final_results"]) > 0
        # vr-d should have been pruned as an orphan (budget exhausted before
        # it could get a share).
        assert "vr-d" not in state["virtual_record_id_to_result"]
        assert "vr-a" in state["virtual_record_id_to_result"]
        assert "vr-b" in state["virtual_record_id_to_result"]
        assert "vr-c" in state["virtual_record_id_to_result"]
        assert f"Retrieved {len(state['final_results'])} knowledge blocks" in result
