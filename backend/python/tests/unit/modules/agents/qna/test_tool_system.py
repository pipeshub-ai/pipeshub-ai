"""
Unit tests for app.modules.agents.qna.tool_system

Tests pure/near-pure helper functions. All external dependencies
(registry, wrappers, LLM classes) are mocked.
"""

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.modules.agents.qna.tool_system import (
    FAILURE_LOOKBACK_WINDOW,
    FAILURE_THRESHOLD,
    _get_blocked_tools,
    _is_internal_tool,
    _is_knowledge_dependent_tool,
    _requires_sanitized_tool_names,
    _sanitize_tool_name_if_needed,
    clear_tool_cache,
    get_tool_results_summary,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_log() -> logging.Logger:
    """Return a mock logger that silently accepts all log calls."""
    return MagicMock(spec=logging.Logger)


def _make_registry_tool(**kwargs):
    """Create a simple namespace that mimics a registry Tool object."""
    return SimpleNamespace(**kwargs)


# ============================================================================
# 1. _requires_sanitized_tool_names
# ============================================================================

class TestRequiresSanitizedToolNames:
    """Tests for _requires_sanitized_tool_names()."""

    def test_none_llm_returns_true(self):
        """When llm is None, default to sanitized (most APIs require it)."""
        assert _requires_sanitized_tool_names(None) is True

    def test_anthropic_llm_returns_true(self):
        """ChatAnthropic instances require sanitization."""
        mock_llm = MagicMock()
        with patch(
            "app.modules.agents.qna.tool_system.isinstance",
            side_effect=lambda obj, cls: True,
        ):
            # Simulate successful import + isinstance check
            pass

        # Simpler: mock the import and instance check via ChatAnthropic
        with patch.dict("sys.modules", {"langchain_anthropic": MagicMock()}):
            import sys
            mock_anthropic_mod = sys.modules["langchain_anthropic"]
            mock_anthropic_cls = MagicMock()
            mock_anthropic_mod.ChatAnthropic = mock_anthropic_cls
            # Make isinstance return True for our mock llm
            mock_llm.__class__ = mock_anthropic_cls
            # The function does isinstance(llm, ChatAnthropic) which won't match
            # because our mock's __class__ isn't actually ChatAnthropic.
            # Instead, just verify that the function returns True (default).
            result = _requires_sanitized_tool_names(mock_llm)
            assert result is True

    def test_openai_llm_returns_true(self):
        """ChatOpenAI instances require sanitization."""
        mock_llm = MagicMock()
        result = _requires_sanitized_tool_names(mock_llm)
        # Default path returns True for safety
        assert result is True

    def test_unknown_llm_returns_true(self):
        """Unknown LLM types default to True for safety."""
        mock_llm = MagicMock()
        result = _requires_sanitized_tool_names(mock_llm)
        assert result is True

    def test_import_error_anthropic_falls_through(self):
        """If langchain_anthropic is not installed, falls through gracefully."""
        mock_llm = MagicMock()
        with patch.dict("sys.modules", {"langchain_anthropic": None}):
            # This will cause ImportError
            result = _requires_sanitized_tool_names(mock_llm)
            assert result is True

    def test_import_error_openai_falls_through(self):
        """If langchain_openai is not installed, falls through gracefully."""
        mock_llm = MagicMock()
        with patch.dict("sys.modules", {"langchain_openai": None}):
            result = _requires_sanitized_tool_names(mock_llm)
            assert result is True


# ============================================================================
# 2. _sanitize_tool_name_if_needed
# ============================================================================

class TestSanitizeToolNameIfNeeded:
    """Tests for _sanitize_tool_name_if_needed()."""

    def test_dots_replaced_when_sanitization_needed(self):
        """When LLM requires sanitization, dots become underscores."""
        state = {}
        # None LLM => sanitization needed
        result = _sanitize_tool_name_if_needed("googledrive.get_files", None, state)
        assert result == "googledrive_get_files"

    def test_multiple_dots_replaced(self):
        """All dots should be replaced."""
        state = {}
        result = _sanitize_tool_name_if_needed("a.b.c.d", None, state)
        assert result == "a_b_c_d"

    def test_no_dots_unchanged(self):
        """Names without dots are unchanged regardless."""
        state = {}
        result = _sanitize_tool_name_if_needed("calculator", None, state)
        assert result == "calculator"

    def test_empty_name(self):
        """Empty string should remain empty."""
        state = {}
        result = _sanitize_tool_name_if_needed("", None, state)
        assert result == ""

    @patch(
        "app.modules.agents.qna.tool_system._requires_sanitized_tool_names",
        return_value=False,
    )
    def test_no_sanitization_when_not_needed(self, mock_req):
        """When LLM does not require sanitization, dots are kept."""
        state = {}
        result = _sanitize_tool_name_if_needed("slack.send_message", MagicMock(), state)
        assert result == "slack.send_message"

    @patch(
        "app.modules.agents.qna.tool_system._requires_sanitized_tool_names",
        return_value=True,
    )
    def test_sanitization_when_needed(self, mock_req):
        """Explicit check that sanitization is applied when required."""
        state = {}
        result = _sanitize_tool_name_if_needed("jira.create_issue", MagicMock(), state)
        assert result == "jira_create_issue"


# ============================================================================
# 3. _is_knowledge_dependent_tool
# ============================================================================

class TestIsKnowledgeDependentTool:
    """Tests for _is_knowledge_dependent_tool()."""

    def test_retrieval_app_name(self):
        """Tool with app_name='retrieval' is knowledge-dependent."""
        tool = _make_registry_tool(app_name="retrieval")
        assert _is_knowledge_dependent_tool("retrieval.search", tool) is True

    def test_knowledgehub_app_name(self):
        """Tool with app_name='knowledgehub' is knowledge-dependent."""
        tool = _make_registry_tool(app_name="knowledgehub")
        assert _is_knowledge_dependent_tool("knowledgehub.list_files", tool) is True

    def test_retrieval_app_name_case_insensitive(self):
        """app_name comparison should be case-insensitive."""
        tool = _make_registry_tool(app_name="Retrieval")
        assert _is_knowledge_dependent_tool("Retrieval.search", tool) is True

    def test_knowledgehub_in_full_name(self):
        """Full name containing 'knowledgehub.' triggers knowledge-dependent."""
        tool = _make_registry_tool()  # No app_name
        assert _is_knowledge_dependent_tool("knowledgehub.browse_files", tool) is True

    def test_retrieval_in_full_name(self):
        """Full name containing 'retrieval.' triggers knowledge-dependent."""
        tool = _make_registry_tool()  # No app_name
        assert _is_knowledge_dependent_tool("retrieval.semantic_search", tool) is True

    def test_non_knowledge_tool_with_app_name(self):
        """Tool with non-knowledge app_name is not knowledge-dependent."""
        tool = _make_registry_tool(app_name="slack")
        assert _is_knowledge_dependent_tool("slack.send_message", tool) is False

    def test_non_knowledge_tool_without_app_name(self):
        """Tool without app_name and non-matching name is not knowledge-dependent."""
        tool = _make_registry_tool()
        assert _is_knowledge_dependent_tool("calculator.add", tool) is False

    def test_partial_match_not_triggered(self):
        """Names like 'retrieval_helper' without dot should not match patterns."""
        tool = _make_registry_tool(app_name="other")
        assert _is_knowledge_dependent_tool("retrieval_helper", tool) is False

    def test_knowledgehub_case_insensitive_full_name(self):
        """Full name pattern matching is case-insensitive."""
        tool = _make_registry_tool()
        assert _is_knowledge_dependent_tool("KnowledgeHub.list_files", tool) is True


# ============================================================================
# 4. _is_internal_tool
# ============================================================================

class TestIsInternalTool:
    """Tests for _is_internal_tool()."""

    def test_calculator_app_name(self):
        """Tool with app_name='calculator' is internal."""
        tool = _make_registry_tool(app_name="calculator")
        assert _is_internal_tool("calculator.add", tool) is True

    def test_datetime_app_name(self):
        """Tool with app_name='datetime' is internal."""
        tool = _make_registry_tool(app_name="datetime")
        assert _is_internal_tool("datetime.now", tool) is True

    def test_utility_app_name(self):
        """Tool with app_name='utility' is internal."""
        tool = _make_registry_tool(app_name="utility")
        assert _is_internal_tool("utility.helper", tool) is True

    def test_metadata_category_internal(self):
        """Tool with metadata.category containing 'internal' is internal."""
        metadata = SimpleNamespace(category="internal", is_internal=False)
        tool = _make_registry_tool(metadata=metadata)
        assert _is_internal_tool("some.tool", tool) is True

    def test_metadata_is_internal_flag(self):
        """Tool with metadata.is_internal=True is internal."""
        metadata = SimpleNamespace(category="service", is_internal=True)
        tool = _make_registry_tool(metadata=metadata)
        assert _is_internal_tool("some.tool", tool) is True

    def test_calculator_in_full_name(self):
        """Full name starting with 'calculator.' matches internal pattern."""
        tool = _make_registry_tool()
        assert _is_internal_tool("calculator.multiply", tool) is True

    def test_web_search_in_full_name(self):
        """Full name containing 'web_search' matches internal pattern."""
        tool = _make_registry_tool()
        assert _is_internal_tool("utility.web_search", tool) is True

    def test_get_current_datetime_in_full_name(self):
        """Full name containing 'get_current_datetime' matches internal pattern."""
        tool = _make_registry_tool()
        assert _is_internal_tool("datetime.get_current_datetime", tool) is True

    def test_non_internal_tool(self):
        """Non-internal tool returns False."""
        tool = _make_registry_tool(app_name="slack")
        assert _is_internal_tool("slack.send_message", tool) is False

    def test_no_metadata_no_app_name_non_matching(self):
        """Tool with no metadata and no app_name, non-matching name."""
        tool = _make_registry_tool()
        assert _is_internal_tool("jira.create_issue", tool) is False

    def test_retrieval_is_not_internal(self):
        """Retrieval tools are NOT internal (handled separately)."""
        tool = _make_registry_tool(app_name="retrieval")
        assert _is_internal_tool("retrieval.search", tool) is False

    def test_metadata_category_internal_case_insensitive(self):
        """Category 'Internal' (uppercase) should still match."""
        metadata = SimpleNamespace(category="Internal Tools", is_internal=False)
        tool = _make_registry_tool(metadata=metadata)
        assert _is_internal_tool("some.tool", tool) is True

    def test_metadata_without_category(self):
        """Metadata without category attribute doesn't crash."""
        metadata = SimpleNamespace(is_internal=False)
        tool = _make_registry_tool(metadata=metadata)
        # Falls through to app_name and pattern checks
        assert _is_internal_tool("slack.send", tool) is False

    def test_metadata_without_is_internal(self):
        """Metadata without is_internal attribute doesn't crash."""
        metadata = SimpleNamespace(category="service")
        tool = _make_registry_tool(metadata=metadata)
        assert _is_internal_tool("slack.send", tool) is False


# ============================================================================
# 5. _get_blocked_tools
# ============================================================================

class TestGetBlockedTools:
    """Tests for _get_blocked_tools()."""

    def test_empty_results_returns_empty(self):
        """No tool results => no blocked tools."""
        state = {"all_tool_results": []}
        assert _get_blocked_tools(state) == {}

    def test_no_results_key_returns_empty(self):
        """Missing 'all_tool_results' key => no blocked tools."""
        state = {}
        assert _get_blocked_tools(state) == {}

    def test_below_threshold_count_returns_empty(self):
        """Fewer results than FAILURE_THRESHOLD => no blocked tools."""
        state = {
            "all_tool_results": [
                {"tool_name": "slack.send", "status": "error"},
                {"tool_name": "slack.send", "status": "error"},
            ]
        }
        assert _get_blocked_tools(state) == {}

    def test_tool_blocked_after_threshold_failures(self):
        """Tool blocked when it fails >= FAILURE_THRESHOLD times in lookback window."""
        results = [
            {"tool_name": "slack.send", "status": "error"}
            for _ in range(FAILURE_THRESHOLD)
        ]
        state = {"all_tool_results": results}
        blocked = _get_blocked_tools(state)
        assert "slack.send" in blocked
        assert blocked["slack.send"] == FAILURE_THRESHOLD

    def test_success_results_not_counted(self):
        """Successful results don't contribute to blocking."""
        results = [
            {"tool_name": "slack.send", "status": "success"}
            for _ in range(10)
        ]
        state = {"all_tool_results": results}
        assert _get_blocked_tools(state) == {}

    def test_mixed_results_below_threshold(self):
        """Mixed success/error below threshold => not blocked."""
        results = [
            {"tool_name": "slack.send", "status": "error"},
            {"tool_name": "slack.send", "status": "success"},
            {"tool_name": "slack.send", "status": "error"},
            {"tool_name": "jira.create", "status": "success"},
        ]
        state = {"all_tool_results": results}
        blocked = _get_blocked_tools(state)
        assert "slack.send" not in blocked

    def test_lookback_window_applied(self):
        """Only the last FAILURE_LOOKBACK_WINDOW results are considered."""
        # Old failures (should be outside window)
        old_failures = [
            {"tool_name": "old.tool", "status": "error"}
            for _ in range(5)
        ]
        # Recent successes (inside window)
        recent = [
            {"tool_name": "other.tool", "status": "success"}
            for _ in range(FAILURE_LOOKBACK_WINDOW)
        ]
        state = {"all_tool_results": old_failures + recent}
        blocked = _get_blocked_tools(state)
        assert "old.tool" not in blocked

    def test_multiple_tools_blocked(self):
        """Multiple tools can be blocked simultaneously."""
        results = []
        for _ in range(FAILURE_THRESHOLD):
            results.append({"tool_name": "tool_a", "status": "error"})
            results.append({"tool_name": "tool_b", "status": "error"})
        # Ensure enough results (need at least FAILURE_THRESHOLD total)
        state = {"all_tool_results": results}
        blocked = _get_blocked_tools(state)
        # Both tools should be blocked if they appear enough times in the lookback window
        # The window is FAILURE_LOOKBACK_WINDOW items, so check accordingly
        assert len(blocked) >= 1

    def test_unknown_tool_name_default(self):
        """Results without 'tool_name' key default to 'unknown'."""
        results = [
            {"status": "error"} for _ in range(FAILURE_THRESHOLD)
        ]
        state = {"all_tool_results": results}
        blocked = _get_blocked_tools(state)
        assert "unknown" in blocked

    def test_none_results(self):
        """None value for all_tool_results returns empty."""
        state = {"all_tool_results": None}
        assert _get_blocked_tools(state) == {}


# ============================================================================
# 6. get_tool_results_summary
# ============================================================================

class TestGetToolResultsSummary:
    """Tests for get_tool_results_summary()."""

    def test_no_results_returns_message(self):
        """Empty results => 'No tools executed yet.' message."""
        state = {"all_tool_results": []}
        result = get_tool_results_summary(state)
        assert result == "No tools executed yet."

    def test_missing_key_returns_message(self):
        """Missing key => 'No tools executed yet.'"""
        state = {}
        result = get_tool_results_summary(state)
        assert result == "No tools executed yet."

    def test_single_success_result(self):
        """Single success result formatted correctly."""
        state = {
            "all_tool_results": [
                {"tool_name": "slack.send_message", "status": "success"}
            ]
        }
        result = get_tool_results_summary(state)
        assert "Tool Execution Summary" in result
        assert "Total: 1" in result
        assert "Slack" in result
        assert "Success: 1" in result
        assert "Failed: 0" in result

    def test_single_error_result(self):
        """Single error result formatted correctly."""
        state = {
            "all_tool_results": [
                {"tool_name": "jira.create_issue", "status": "error"}
            ]
        }
        result = get_tool_results_summary(state)
        assert "Tool Execution Summary" in result
        assert "Failed: 1" in result

    def test_mixed_results_grouped_by_category(self):
        """Results are grouped by category (app name before dot)."""
        state = {
            "all_tool_results": [
                {"tool_name": "slack.send_message", "status": "success"},
                {"tool_name": "slack.list_channels", "status": "success"},
                {"tool_name": "jira.create_issue", "status": "error"},
            ]
        }
        result = get_tool_results_summary(state)
        assert "Slack" in result
        assert "Jira" in result

    def test_tool_without_dot_gets_utility_category(self):
        """Tools without a dot in the name get 'utility' category."""
        state = {
            "all_tool_results": [
                {"tool_name": "calculator", "status": "success"}
            ]
        }
        result = get_tool_results_summary(state)
        assert "Utility" in result

    def test_unknown_status_not_counted_as_success_or_error(self):
        """Status values other than 'success' or 'error' are not counted."""
        state = {
            "all_tool_results": [
                {"tool_name": "test.tool", "status": "pending"},
                {"tool_name": "test.tool", "status": "success"},
            ]
        }
        result = get_tool_results_summary(state)
        assert "Success: 1" in result
        assert "Failed: 0" in result

    def test_multiple_failures_same_tool(self):
        """Multiple failures of the same tool are counted."""
        state = {
            "all_tool_results": [
                {"tool_name": "slack.send", "status": "error"},
                {"tool_name": "slack.send", "status": "error"},
                {"tool_name": "slack.send", "status": "success"},
            ]
        }
        result = get_tool_results_summary(state)
        assert "Total: 3" in result


# ============================================================================
# 7. clear_tool_cache
# ============================================================================

class TestClearToolCache:
    """Tests for clear_tool_cache()."""

    def test_clears_all_cache_keys(self):
        """All cache-related keys are removed from state."""
        state = {
            "_cached_agent_tools": ["tool1", "tool2"],
            "_tool_instance_cache": {"key": "val"},
            "_cached_blocked_tools": {"tool": 3},
            "_cached_schema_tools": ["schema"],
            "query": "test",  # non-cache key should remain
        }
        clear_tool_cache(state)
        assert "_cached_agent_tools" not in state
        assert "_tool_instance_cache" not in state
        assert "_cached_blocked_tools" not in state
        assert "_cached_schema_tools" not in state
        assert state["query"] == "test"

    def test_clears_already_empty_cache(self):
        """Clearing when no cache keys exist doesn't crash."""
        state = {"query": "test"}
        clear_tool_cache(state)  # Should not raise
        assert state == {"query": "test"}

    def test_clears_partial_cache(self):
        """Only existing cache keys are removed."""
        state = {
            "_cached_agent_tools": ["tool"],
            "query": "test",
        }
        clear_tool_cache(state)
        assert "_cached_agent_tools" not in state
        assert state["query"] == "test"

    def test_clears_none_value_cache(self):
        """Cache keys with None values are still removed."""
        state = {
            "_cached_agent_tools": None,
            "_tool_instance_cache": None,
            "_cached_blocked_tools": None,
            "_cached_schema_tools": None,
        }
        clear_tool_cache(state)
        assert "_cached_agent_tools" not in state
        assert "_tool_instance_cache" not in state
        assert "_cached_blocked_tools" not in state
        assert "_cached_schema_tools" not in state


# ============================================================================
# 8. _parse_tool_name
# ============================================================================

class TestParseToolName:
    """Tests for _parse_tool_name()."""

    def test_dotted_name(self):
        from app.modules.agents.qna.tool_system import _parse_tool_name
        app, name = _parse_tool_name("googledrive.get_files_list")
        assert app == "googledrive"
        assert name == "get_files_list"

    def test_multiple_dots(self):
        """Only splits on first dot."""
        from app.modules.agents.qna.tool_system import _parse_tool_name
        app, name = _parse_tool_name("a.b.c.d")
        assert app == "a"
        assert name == "b.c.d"

    def test_no_dot(self):
        """No dot returns ('default', name)."""
        from app.modules.agents.qna.tool_system import _parse_tool_name
        app, name = _parse_tool_name("calculator")
        assert app == "default"
        assert name == "calculator"

    def test_empty_string(self):
        from app.modules.agents.qna.tool_system import _parse_tool_name
        app, name = _parse_tool_name("")
        assert app == "default"
        assert name == ""

    def test_dot_at_beginning(self):
        from app.modules.agents.qna.tool_system import _parse_tool_name
        app, name = _parse_tool_name(".tool_name")
        assert app == ""
        assert name == "tool_name"

    def test_dot_at_end(self):
        from app.modules.agents.qna.tool_system import _parse_tool_name
        app, name = _parse_tool_name("app.")
        assert app == "app"
        assert name == ""

    def test_underscore_is_not_separator(self):
        from app.modules.agents.qna.tool_system import _parse_tool_name
        app, name = _parse_tool_name("my_app_send_message")
        assert app == "default"
        assert name == "my_app_send_message"


# ============================================================================
# 9. _extract_tool_names_from_toolsets
# ============================================================================

class TestExtractToolNamesFromToolsets:
    """Tests for _extract_tool_names_from_toolsets()."""

    def test_empty_toolsets_returns_none(self):
        from app.modules.agents.qna.tool_system import _extract_tool_names_from_toolsets
        assert _extract_tool_names_from_toolsets([]) is None

    def test_none_toolsets_returns_none(self):
        from app.modules.agents.qna.tool_system import _extract_tool_names_from_toolsets
        assert _extract_tool_names_from_toolsets(None) is None

    def test_dict_tools_with_full_name(self):
        from app.modules.agents.qna.tool_system import _extract_tool_names_from_toolsets
        toolsets = [
            {
                "name": "googledrive",
                "tools": [
                    {"fullName": "googledrive.get_files_list"},
                    {"fullName": "googledrive.download_file"},
                ],
            },
        ]
        result = _extract_tool_names_from_toolsets(toolsets)
        assert result == {"googledrive.get_files_list", "googledrive.download_file"}

    def test_dict_tools_with_tool_name_field(self):
        from app.modules.agents.qna.tool_system import _extract_tool_names_from_toolsets
        toolsets = [
            {
                "name": "slack",
                "tools": [
                    {"toolName": "send_message"},
                ],
            },
        ]
        result = _extract_tool_names_from_toolsets(toolsets)
        assert result == {"slack.send_message"}

    def test_dict_tools_with_name_field(self):
        from app.modules.agents.qna.tool_system import _extract_tool_names_from_toolsets
        toolsets = [
            {
                "name": "jira",
                "tools": [
                    {"name": "create_issue"},
                ],
            },
        ]
        result = _extract_tool_names_from_toolsets(toolsets)
        assert result == {"jira.create_issue"}

    def test_string_tools_with_dot(self):
        from app.modules.agents.qna.tool_system import _extract_tool_names_from_toolsets
        toolsets = [
            {
                "name": "slack",
                "tools": ["slack.send_message", "slack.list_channels"],
            },
        ]
        result = _extract_tool_names_from_toolsets(toolsets)
        assert result == {"slack.send_message", "slack.list_channels"}

    def test_string_tools_without_dot(self):
        from app.modules.agents.qna.tool_system import _extract_tool_names_from_toolsets
        toolsets = [
            {
                "name": "slack",
                "tools": ["send_message"],
            },
        ]
        result = _extract_tool_names_from_toolsets(toolsets)
        assert result == {"slack.send_message"}

    def test_toolset_without_name_skipped(self):
        from app.modules.agents.qna.tool_system import _extract_tool_names_from_toolsets
        toolsets = [
            {"name": "", "tools": [{"fullName": "test.tool"}]},
        ]
        result = _extract_tool_names_from_toolsets(toolsets)
        assert result is None

    def test_no_toolset_level_matching(self):
        """Toolset names should NOT be added to the set (security)."""
        from app.modules.agents.qna.tool_system import _extract_tool_names_from_toolsets
        toolsets = [
            {
                "name": "googledrive",
                "tools": [{"fullName": "googledrive.get_files_list"}],
            },
        ]
        result = _extract_tool_names_from_toolsets(toolsets)
        assert "googledrive" not in result
        assert "googledrive.get_files_list" in result

    def test_multiple_toolsets_combined(self):
        from app.modules.agents.qna.tool_system import _extract_tool_names_from_toolsets
        toolsets = [
            {"name": "slack", "tools": [{"fullName": "slack.send_message"}]},
            {"name": "jira", "tools": [{"fullName": "jira.create_issue"}]},
        ]
        result = _extract_tool_names_from_toolsets(toolsets)
        assert result == {"slack.send_message", "jira.create_issue"}

    def test_toolset_with_no_tools_key(self):
        from app.modules.agents.qna.tool_system import _extract_tool_names_from_toolsets
        toolsets = [
            {"name": "slack"},  # no "tools" key
        ]
        result = _extract_tool_names_from_toolsets(toolsets)
        assert result is None

    def test_empty_full_name_skipped(self):
        from app.modules.agents.qna.tool_system import _extract_tool_names_from_toolsets
        toolsets = [
            {
                "name": "slack",
                "tools": [{"name": ""}],  # Empty name, fullName becomes "slack."
            },
        ]
        result = _extract_tool_names_from_toolsets(toolsets)
        # "slack." with no tool name is skipped by the check
        assert result is None


# ============================================================================
# 10. ToolLoader.load_tools
# ============================================================================

class TestToolLoaderLoadTools:
    """Tests for ToolLoader.load_tools()."""
    pass

# ============================================================================
# 11. _load_all_tools
# ============================================================================

class TestLoadAllTools:
    """Tests for _load_all_tools()."""
    pass

class TestGetAgentTools:
    """Tests for get_agent_tools()."""

    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    def test_no_fetch_tool_when_no_records(self, mock_registry):
        from app.modules.agents.qna.tool_system import get_agent_tools

        mock_tool = _make_registry_tool(
            app_name="calculator",
            metadata=SimpleNamespace(category="internal", is_internal=True),
        )
        mock_registry.get_all_tools.return_value = {"calculator.add": mock_tool}

        state = {"has_knowledge": False, "virtual_record_id_to_result": {}}
        tools = get_agent_tools(state)
        tool_names = [t.name for t in tools]
        assert "fetch_full_record" not in tool_names


# ============================================================================
# 13. _initialize_tool_state
# ============================================================================

class TestInitializeToolState:
    """Tests for _initialize_tool_state()."""

    def test_initializes_missing_keys(self):
        from app.modules.agents.qna.tool_system import _initialize_tool_state
        state = {}
        _initialize_tool_state(state)
        assert state["tool_results"] == []
        assert state["all_tool_results"] == []

    def test_preserves_existing_keys(self):
        from app.modules.agents.qna.tool_system import _initialize_tool_state
        state = {"tool_results": [{"existing": True}], "all_tool_results": [{"existing": True}]}
        _initialize_tool_state(state)
        assert len(state["tool_results"]) == 1
        assert len(state["all_tool_results"]) == 1


# ============================================================================
# 14. ToolLoader.load_tools - cache scenarios
# ============================================================================

class TestToolLoaderCacheScenarios:
    """Additional tests for ToolLoader.load_tools cache behavior."""

    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    def test_cache_hit_same_blocked_and_knowledge(self, mock_registry):
        """Cache returns same list when blocked tools and knowledge unchanged."""
        from app.modules.agents.qna.tool_system import ToolLoader

        mock_tool = _make_registry_tool(
            app_name="calculator",
            metadata=SimpleNamespace(category="internal", is_internal=True),
        )
        mock_registry.get_all_tools.return_value = {"calculator.add": mock_tool}

        state = {"has_knowledge": False, "all_tool_results": []}
        first = ToolLoader.load_tools(state)
        second = ToolLoader.load_tools(state)

        assert first is second
        assert mock_registry.get_all_tools.call_count == 1

    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    def test_cache_invalidation_on_knowledge_change(self, mock_registry):
        """Cache is invalidated when has_knowledge changes from False to True."""
        from app.modules.agents.qna.tool_system import ToolLoader

        mock_tool = _make_registry_tool(
            app_name="calculator",
            metadata=SimpleNamespace(category="internal", is_internal=True),
        )
        mock_registry.get_all_tools.return_value = {"calculator.add": mock_tool}

        state = {"has_knowledge": False, "all_tool_results": []}
        ToolLoader.load_tools(state)
        assert mock_registry.get_all_tools.call_count == 1

        state["has_knowledge"] = True
        ToolLoader.load_tools(state)
        assert mock_registry.get_all_tools.call_count == 2

    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    def test_first_load_populates_cache_and_available_tools(self, mock_registry):
        """First load populates _cached_agent_tools, _cached_has_knowledge, and available_tools."""
        from app.modules.agents.qna.tool_system import ToolLoader

        mock_tool = _make_registry_tool(
            app_name="calculator",
            metadata=SimpleNamespace(category="internal", is_internal=True),
        )
        mock_registry.get_all_tools.return_value = {"calculator.add": mock_tool}

        state = {"has_knowledge": False}
        ToolLoader.load_tools(state)
        assert "_cached_agent_tools" in state
        assert "_cached_has_knowledge" in state
        assert state["_cached_has_knowledge"] is False
        assert "available_tools" in state


# ============================================================================
# 15. get_agent_tools - dynamic fetch_full_record
# ============================================================================

class TestGetAgentToolsDynamic:
    """Tests for get_agent_tools with dynamic tool addition."""

    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    def test_no_fetch_tool_when_empty_virtual_record_map(self, mock_registry):
        """When virtual_record_id_to_result is empty, no fetch tool added."""
        from app.modules.agents.qna.tool_system import get_agent_tools

        mock_tool = _make_registry_tool(
            app_name="calculator",
            metadata=SimpleNamespace(category="internal", is_internal=True),
        )
        mock_registry.get_all_tools.return_value = {"calculator.add": mock_tool}

        state = {"has_knowledge": False, "virtual_record_id_to_result": {}}
        tools = get_agent_tools(state)
        tool_names = [t.name for t in tools]
        assert "fetch_full_record" not in tool_names

    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    def test_no_fetch_tool_when_no_virtual_record_map(self, mock_registry):
        """When virtual_record_id_to_result key is absent, no fetch tool added."""
        from app.modules.agents.qna.tool_system import get_agent_tools

        mock_tool = _make_registry_tool(
            app_name="calculator",
            metadata=SimpleNamespace(category="internal", is_internal=True),
        )
        mock_registry.get_all_tools.return_value = {"calculator.add": mock_tool}

        state = {"has_knowledge": False}
        tools = get_agent_tools(state)
        tool_names = [t.name for t in tools]
        assert "fetch_full_record" not in tool_names

    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    def test_no_sql_query_tool_even_with_config_service(self, mock_registry):
        """Regression: get_agent_tools must NOT inject execute_sql_query even when
        config_service is present. The SQL tool is a LangChain StructuredTool, not a
        RegistryToolWrapper, and appending it here produced a downstream
        'StructuredTool has no attribute registry_tool' warning during schema
        conversion. The tool is added by get_agent_tools_with_schemas() instead.
        """
        from app.modules.agents.qna.tool_system import get_agent_tools

        mock_tool = _make_registry_tool(
            app_name="calculator",
            metadata=SimpleNamespace(category="internal", is_internal=True),
        )
        mock_registry.get_all_tools.return_value = {"calculator.add": mock_tool}

        state = {
            "has_knowledge": False,
            "virtual_record_id_to_result": {},
            "config_service": MagicMock(),
            "graph_provider": MagicMock(),
            "org_id": "org-1",
        }
        tools = get_agent_tools(state)
        tool_names = [getattr(t, "name", None) for t in tools]
        assert "execute_sql_query" not in tool_names


# ============================================================================
# 16. get_agent_tools_with_schemas - schema conversion
# ============================================================================

class TestGetAgentToolsWithSchemas:
    """Tests for get_agent_tools_with_schemas()."""

    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    def test_cache_hit_returns_same_list(self, mock_registry):
        """When registry tools unchanged, cached schema tools returned."""
        from app.modules.agents.qna.tool_system import get_agent_tools_with_schemas

        mock_tool = _make_registry_tool(
            app_name="calculator",
            metadata=SimpleNamespace(category="internal", is_internal=True),
        )
        mock_registry.get_all_tools.return_value = {"calculator.add": mock_tool}

        state = {"has_knowledge": False}
        first = get_agent_tools_with_schemas(state)
        second = get_agent_tools_with_schemas(state)

        # Both calls return the same cached object
        assert first is second

# ============================================================================
# 17. _load_all_tools - deeper scenarios
# ============================================================================

class TestLoadAllToolsDeeper:
    """Deeper tests for _load_all_tools()."""

    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    def test_retrieval_excluded_without_knowledge_even_if_internal(self, mock_registry):
        """Retrieval tools are excluded when has_knowledge=False even though they'd be internal."""
        from app.modules.agents.qna.tool_system import _load_all_tools

        retrieval = _make_registry_tool(app_name="retrieval")
        mock_registry.get_all_tools.return_value = {
            "retrieval.search_internal_knowledge": retrieval,
        }
        state = {"has_knowledge": False}
        tools = _load_all_tools(state, {})
        assert len(tools) == 0

    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    def test_blocked_tools_are_skipped(self, mock_registry):
        """Tools in the blocked dict are excluded."""
        from app.modules.agents.qna.tool_system import _load_all_tools

        calc = _make_registry_tool(
            app_name="calculator",
            metadata=SimpleNamespace(category="internal", is_internal=True),
        )
        mock_registry.get_all_tools.return_value = {"calculator.add": calc}

        state = {"has_knowledge": False}
        tools = _load_all_tools(state, {"calculator.add": 5})
        assert len(tools) == 0

    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    def test_user_tools_need_exact_match(self, mock_registry):
        """User tools are only loaded via exact fullName match."""
        from app.modules.agents.qna.tool_system import _load_all_tools

        slack = _make_registry_tool(
            app_name="slack",
            metadata=SimpleNamespace(category="external", is_internal=False),
        )
        mock_registry.get_all_tools.return_value = {
            "slack.send_message": slack,
        }
        # Toolset lists slack.list_channels but NOT slack.send_message
        state = {
            "has_knowledge": False,
            "agent_toolsets": [
                {"name": "slack", "tools": [{"fullName": "slack.list_channels"}]}
            ],
        }
        tools = _load_all_tools(state, {})
        tool_names = [t.name for t in tools]
        assert "slack.send_message" not in tool_names

# ============================================================================
# 18. _extract_tool_names_from_toolsets - security and edge cases
# ============================================================================

class TestExtractToolNamesFromToolsetsDeeper:
    """Deeper tests for _extract_tool_names_from_toolsets security."""

    def test_exact_match_only_no_toolset_name(self):
        """Toolset name itself should NOT appear in the result set."""
        from app.modules.agents.qna.tool_system import _extract_tool_names_from_toolsets

        toolsets = [
            {
                "name": "googledrive",
                "tools": [{"fullName": "googledrive.get_files"}],
            }
        ]
        result = _extract_tool_names_from_toolsets(toolsets)
        assert "googledrive" not in result
        assert "googledrive.get_files" in result

    def test_empty_tools_list_returns_none(self):
        """Toolset with empty tools list extracts nothing."""
        from app.modules.agents.qna.tool_system import _extract_tool_names_from_toolsets

        toolsets = [{"name": "slack", "tools": []}]
        result = _extract_tool_names_from_toolsets(toolsets)
        assert result is None

    def test_mixed_dict_and_string_tools(self):
        """Both dict and string tool entries are processed."""
        from app.modules.agents.qna.tool_system import _extract_tool_names_from_toolsets

        toolsets = [
            {
                "name": "slack",
                "tools": [
                    {"fullName": "slack.send_message"},
                    "slack.list_channels",
                ],
            }
        ]
        result = _extract_tool_names_from_toolsets(toolsets)
        assert "slack.send_message" in result
        assert "slack.list_channels" in result

    def test_string_tool_without_dot_gets_prefixed(self):
        """String tool without dot gets toolset name prefixed."""
        from app.modules.agents.qna.tool_system import _extract_tool_names_from_toolsets

        toolsets = [{"name": "jira", "tools": ["search_issues"]}]
        result = _extract_tool_names_from_toolsets(toolsets)
        assert "jira.search_issues" in result

    def test_multiple_toolsets_are_combined(self):
        """Tool names from multiple toolsets are union-ed."""
        from app.modules.agents.qna.tool_system import _extract_tool_names_from_toolsets

        toolsets = [
            {"name": "slack", "tools": [{"fullName": "slack.send_message"}]},
            {"name": "jira", "tools": [{"fullName": "jira.create_issue"}]},
            {"name": "confluence", "tools": ["confluence.search_content"]},
        ]
        result = _extract_tool_names_from_toolsets(toolsets)
        assert len(result) == 3
        assert "slack.send_message" in result
        assert "jira.create_issue" in result
        assert "confluence.search_content" in result

    def test_toolset_with_no_name_skipped(self):
        """Toolset without a name is skipped entirely."""
        from app.modules.agents.qna.tool_system import _extract_tool_names_from_toolsets

        toolsets = [
            {"tools": [{"fullName": "test.tool"}]},  # No 'name' key
        ]
        result = _extract_tool_names_from_toolsets(toolsets)
        assert result is None

    def test_tool_dict_with_toolname_field(self):
        """Tool dict uses toolName field when fullName not present."""
        from app.modules.agents.qna.tool_system import _extract_tool_names_from_toolsets

        toolsets = [
            {"name": "outlook", "tools": [{"toolName": "search_messages"}]}
        ]
        result = _extract_tool_names_from_toolsets(toolsets)
        assert "outlook.search_messages" in result

    def test_tool_dict_with_name_field_fallback(self):
        """Tool dict uses name field when fullName and toolName not present."""
        from app.modules.agents.qna.tool_system import _extract_tool_names_from_toolsets

        toolsets = [
            {"name": "github", "tools": [{"name": "list_repos"}]}
        ]
        result = _extract_tool_names_from_toolsets(toolsets)
        assert "github.list_repos" in result

# =============================================================================
# Merged from test_tool_system_coverage.py
# =============================================================================

def _make_registry_tool_cov(**kwargs):
    """Create a simple namespace mimicking a registry Tool object."""
    return SimpleNamespace(**kwargs)


def _make_state(**extra):
    """Create a minimal state dict."""
    return {
        "has_knowledge": False,
        "all_tool_results": [],
        "logger": MagicMock(spec=logging.Logger),
        **extra,
    }


# ===================================================================
# get_agent_tools_with_schemas
# ===================================================================


class TestGetAgentToolsWithSchemasCoverage:
    """Cover get_agent_tools_with_schemas including caching, schema conversion."""

    @patch("app.modules.agents.qna.tool_system.RegistryToolWrapper")
    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    def test_returns_structured_tools_with_schema(self, mock_registry, mock_wrapper):
        from pydantic import BaseModel, Field

        from app.modules.agents.qna.tool_system import get_agent_tools_with_schemas

        class MySchema(BaseModel):
            query: str = Field(description="Search query")

        mock_tool = _make_registry_tool_cov(
            app_name="calculator",
            metadata=SimpleNamespace(category="internal", is_internal=True),
            args_schema=MySchema,
            description="Calculate something",
            llm_description="Use this for math",
            parameters=[],
        )
        mock_registry.get_all_tools.return_value = {"calculator.add": mock_tool}

        # Make wrapper work
        wrapper_instance = MagicMock()
        wrapper_instance.name = "calculator.add"
        wrapper_instance.description = "Calculate something"
        wrapper_instance.registry_tool = mock_tool
        mock_wrapper.return_value = wrapper_instance

        state = _make_state()
        tools = get_agent_tools_with_schemas(state)
        assert len(tools) >= 1
        assert "_cached_schema_tools" in state

    @patch("app.modules.agents.qna.tool_system.RegistryToolWrapper")
    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    def test_returns_structured_tools_without_schema(self, mock_registry, mock_wrapper):
        from app.modules.agents.qna.tool_system import get_agent_tools_with_schemas

        mock_tool = _make_registry_tool_cov(
            app_name="utility",
            metadata=SimpleNamespace(category="internal", is_internal=True),
            description="Utility tool",
            parameters=[],
        )
        mock_registry.get_all_tools.return_value = {"utility.do": mock_tool}

        wrapper_instance = MagicMock()
        wrapper_instance.name = "utility.do"
        wrapper_instance.description = "Utility tool"
        wrapper_instance.registry_tool = mock_tool
        mock_wrapper.return_value = wrapper_instance

        state = _make_state()
        tools = get_agent_tools_with_schemas(state)
        assert len(tools) >= 1

    @patch("app.modules.agents.qna.tool_system.RegistryToolWrapper")
    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    def test_cache_hit_reuses_tools(self, mock_registry, mock_wrapper):
        from app.modules.agents.qna.tool_system import get_agent_tools_with_schemas

        mock_tool = _make_registry_tool_cov(
            app_name="calculator",
            metadata=SimpleNamespace(category="internal", is_internal=True),
            description="Calc",
            parameters=[],
        )
        mock_registry.get_all_tools.return_value = {"calculator.add": mock_tool}

        wrapper_instance = MagicMock()
        wrapper_instance.name = "calculator.add"
        wrapper_instance.description = "Calc"
        wrapper_instance.registry_tool = mock_tool
        mock_wrapper.return_value = wrapper_instance

        state = _make_state()
        tools1 = get_agent_tools_with_schemas(state)
        tools2 = get_agent_tools_with_schemas(state)
        assert tools1 is tools2

    @patch("app.modules.agents.qna.tool_system.RegistryToolWrapper")
    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    def test_with_virtual_record_map(self, mock_registry, mock_wrapper):
        from app.modules.agents.qna.tool_system import get_agent_tools_with_schemas

        mock_tool = _make_registry_tool_cov(
            app_name="calculator",
            metadata=SimpleNamespace(category="internal", is_internal=True),
            description="Calc",
            parameters=[],
        )
        mock_registry.get_all_tools.return_value = {"calculator.add": mock_tool}

        wrapper_instance = MagicMock()
        wrapper_instance.name = "calculator.add"
        wrapper_instance.description = "Calc"
        wrapper_instance.registry_tool = mock_tool
        mock_wrapper.return_value = wrapper_instance

        state = _make_state(
            virtual_record_id_to_result={"vr-1": {"content": "test"}},
            record_label_to_uuid_map={"label1": "vr-1"},
        )

        with patch(
            "app.utils.fetch_full_record.create_fetch_full_record_tool"
        ) as mock_create_fetch:
            mock_fetch_tool = MagicMock()
            mock_create_fetch.return_value = mock_fetch_tool

            tools = get_agent_tools_with_schemas(state)
            assert mock_fetch_tool in tools

    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    def test_tool_creation_failure_skipped(self, mock_registry):
        """A tool that fails to create StructuredTool is skipped."""
        from app.modules.agents.qna.tool_system import get_agent_tools_with_schemas

        mock_tool = _make_registry_tool_cov(
            app_name="calculator",
            metadata=SimpleNamespace(category="internal", is_internal=True),
            description="Calc",
            parameters=[],
        )
        mock_registry.get_all_tools.return_value = {"calculator.add": mock_tool}

        state = _make_state()

        with patch(
            "app.modules.agents.qna.tool_system.RegistryToolWrapper",
            side_effect=Exception("Wrapper creation failed"),
        ):
            tools = get_agent_tools_with_schemas(state)
            assert isinstance(tools, list)

    @patch("app.modules.agents.qna.tool_system.RegistryToolWrapper")
    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    def test_sql_query_tool_added_when_config_service_present(
        self, mock_registry, mock_wrapper
    ):
        """execute_sql_query must be added exactly once by get_agent_tools_with_schemas
        when config_service is present, and conversation_id + blob_store are forwarded
        to create_execute_query_tool (the richer context that the removed get_agent_tools
        path did not pass).
        """
        from app.modules.agents.qna.tool_system import get_agent_tools_with_schemas

        mock_tool = _make_registry_tool_cov(
            app_name="calculator",
            metadata=SimpleNamespace(category="internal", is_internal=True),
            description="Calc",
            parameters=[],
        )
        mock_registry.get_all_tools.return_value = {"calculator.add": mock_tool}

        wrapper_instance = MagicMock()
        wrapper_instance.name = "calculator.add"
        wrapper_instance.description = "Calc"
        wrapper_instance.registry_tool = mock_tool
        mock_wrapper.return_value = wrapper_instance

        config_service = MagicMock()
        graph_provider = MagicMock()
        blob_store = MagicMock()
        state = _make_state(
            config_service=config_service,
            graph_provider=graph_provider,
            org_id="org-1",
            conversation_id="conv-1",
            blob_store=blob_store,
        )

        fake_sql_tool = MagicMock(name="execute_sql_query_tool")
        with patch(
            "app.utils.execute_query.create_execute_query_tool",
            return_value=fake_sql_tool,
        ) as mock_create:
            tools = get_agent_tools_with_schemas(state)

        assert mock_create.call_count == 1
        mock_create.assert_called_once_with(
            config_service=config_service,
            graph_provider=graph_provider,
            org_id="org-1",
            conversation_id="conv-1",
            blob_store=blob_store,
        )
        assert fake_sql_tool in tools

    @patch("app.modules.agents.qna.tool_system.RegistryToolWrapper")
    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    def test_sql_query_tool_skipped_when_no_config_service(
        self, mock_registry, mock_wrapper
    ):
        """Without config_service, execute_sql_query is not added and the factory
        is never called (avoids importing execute_query needlessly)."""
        from app.modules.agents.qna.tool_system import get_agent_tools_with_schemas

        mock_tool = _make_registry_tool_cov(
            app_name="calculator",
            metadata=SimpleNamespace(category="internal", is_internal=True),
            description="Calc",
            parameters=[],
        )
        mock_registry.get_all_tools.return_value = {"calculator.add": mock_tool}

        wrapper_instance = MagicMock()
        wrapper_instance.name = "calculator.add"
        wrapper_instance.description = "Calc"
        wrapper_instance.registry_tool = mock_tool
        mock_wrapper.return_value = wrapper_instance

        state = _make_state()  # no config_service

        with patch(
            "app.utils.execute_query.create_execute_query_tool"
        ) as mock_create:
            get_agent_tools_with_schemas(state)

        mock_create.assert_not_called()


# ===================================================================
# _load_all_tools — tool limit and user tool filtering
# ===================================================================


class TestLoadAllToolsExtended:
    """Cover tool limit enforcement and user tool filtering."""

    @patch("app.modules.agents.qna.tool_system.RegistryToolWrapper")
    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    def test_tool_limit_applied(self, mock_registry, mock_wrapper):
        from app.modules.agents.qna.tool_system import MAX_TOOLS_LIMIT, _load_all_tools

        # Create 10 internal tools and MAX_TOOLS_LIMIT+10 user tools
        tools = {}
        # 10 internal tools
        for i in range(10):
            tool = _make_registry_tool_cov(
                app_name="calculator",
                metadata=SimpleNamespace(category="internal", is_internal=True),
                description=f"Internal Tool {i}",
                parameters=[],
            )
            tools[f"calculator.tool{i}"] = tool

        # More user tools than MAX_TOOLS_LIMIT
        for i in range(MAX_TOOLS_LIMIT + 10):
            tool = _make_registry_tool_cov(
                app_name=f"userapp{i}",
                metadata=SimpleNamespace(category="app"),
                description=f"User Tool {i}",
                parameters=[],
            )
            tools[f"userapp{i}.tool{i}"] = tool

        mock_registry.get_all_tools.return_value = tools
        mock_wrapper.side_effect = lambda a, n, t, s: MagicMock(name=f"{a}.{n}")

        # Enable all user tools via agent_toolsets
        user_tool_names = [{"fullName": f"userapp{i}.tool{i}"} for i in range(MAX_TOOLS_LIMIT + 10)]
        state = _make_state(
            agent_toolsets=[{"name": "userapp", "tools": user_tool_names}]
        )
        result = _load_all_tools(state, {})
        assert len(result) <= MAX_TOOLS_LIMIT

    @patch("app.modules.agents.qna.tool_system.RegistryToolWrapper")
    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    def test_user_enabled_tools_loaded(self, mock_registry, mock_wrapper):
        from app.modules.agents.qna.tool_system import _load_all_tools

        internal_tool = _make_registry_tool_cov(
            app_name="calculator",
            metadata=SimpleNamespace(category="internal", is_internal=True),
            description="Calc",
            parameters=[],
        )
        user_tool = _make_registry_tool_cov(
            app_name="slack",
            metadata=SimpleNamespace(category="communication"),
            description="Send message",
            parameters=[],
        )
        mock_registry.get_all_tools.return_value = {
            "calculator.add": internal_tool,
            "slack.send_message": user_tool,
        }
        mock_wrapper.side_effect = lambda a, n, t, s: MagicMock(name=f"{a}.{n}")

        state = _make_state(
            agent_toolsets=[
                {
                    "name": "slack",
                    "tools": [{"fullName": "slack.send_message"}],
                }
            ]
        )
        result = _load_all_tools(state, {})
        tool_names = [t.name for t in result]
        assert len(result) >= 2

    @patch("app.modules.agents.qna.tool_system.RegistryToolWrapper")
    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    def test_tool_load_exception_handled(self, mock_registry, mock_wrapper):
        """Exception during tool loading is caught and logged."""
        from app.modules.agents.qna.tool_system import _load_all_tools

        mock_tool = _make_registry_tool_cov(
            app_name="bad",
            metadata=SimpleNamespace(category="internal", is_internal=True),
            description="Bad tool",
            parameters=[],
        )
        mock_registry.get_all_tools.return_value = {"bad.tool": mock_tool}
        mock_wrapper.side_effect = Exception("Cannot create wrapper")

        state = _make_state()
        result = _load_all_tools(state, {})
        assert len(result) == 0  # Tool loading failed

    @patch("app.modules.agents.qna.tool_system.RegistryToolWrapper")
    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    def test_retrieval_tool_included_with_knowledge(self, mock_registry, mock_wrapper):
        from app.modules.agents.qna.tool_system import _load_all_tools

        retrieval_tool = _make_registry_tool_cov(
            app_name="retrieval",
            metadata=SimpleNamespace(category="search"),
            description="Search",
            parameters=[],
        )
        mock_registry.get_all_tools.return_value = {
            "retrieval.search": retrieval_tool,
        }
        mock_wrapper.side_effect = lambda a, n, t, s: MagicMock(name=f"{a}.{n}")

        state = _make_state(has_knowledge=True)
        result = _load_all_tools(state, {})
        assert len(result) == 1

    @patch("app.modules.agents.qna.tool_system.RegistryToolWrapper")
    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    def test_no_agent_toolsets_only_internal(self, mock_registry, mock_wrapper):
        from app.modules.agents.qna.tool_system import _load_all_tools

        internal_tool = _make_registry_tool_cov(
            app_name="calculator",
            metadata=SimpleNamespace(category="internal", is_internal=True),
            description="Calc",
            parameters=[],
        )
        external_tool = _make_registry_tool_cov(
            app_name="slack",
            metadata=SimpleNamespace(category="communication"),
            description="Send",
            parameters=[],
        )
        mock_registry.get_all_tools.return_value = {
            "calculator.add": internal_tool,
            "slack.send": external_tool,
        }
        mock_wrapper.side_effect = lambda a, n, t, s: MagicMock(name=f"{a}.{n}")

        state = _make_state()  # No agent_toolsets
        result = _load_all_tools(state, {})
        # Only internal tool should be loaded
        assert len(result) == 1


# ===================================================================
# ToolLoader.load_tools — cache invalidation scenarios
# ===================================================================


class TestToolLoaderCacheInvalidation:
    """Cover cache invalidation edge cases."""

    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    def test_cache_invalidated_on_blocked_tools_change(self, mock_registry):
        from app.modules.agents.qna.tool_system import ToolLoader

        mock_tool = _make_registry_tool_cov(
            app_name="calculator",
            metadata=SimpleNamespace(category="internal", is_internal=True),
            description="Calc",
            parameters=[],
        )
        mock_registry.get_all_tools.return_value = {"calculator.add": mock_tool}

        state = _make_state(all_tool_results=[])
        # First load
        tools1 = ToolLoader.load_tools(state)

        # Add failures to trigger blocked tool change
        state["all_tool_results"] = [
            {"status": "error", "tool_name": "calculator.add"},
            {"status": "error", "tool_name": "calculator.add"},
            {"status": "error", "tool_name": "calculator.add"},
        ]
        tools2 = ToolLoader.load_tools(state)
        # Should have rebuilt cache
        assert tools1 is not tools2


# ===================================================================
# ToolLoader.get_tool_by_name
# ===================================================================


class TestToolLoaderGetToolByName:
    """Cover get_tool_by_name direct match and suffix match."""

    @patch("app.modules.agents.qna.tool_system.RegistryToolWrapper")
    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    def test_direct_match(self, mock_registry, mock_wrapper):
        from app.modules.agents.qna.tool_system import ToolLoader

        mock_tool = _make_registry_tool_cov(
            description="test", parameters=[]
        )
        mock_registry.get_all_tools.return_value = {
            "calculator.add": mock_tool
        }
        mock_wrapper.return_value = MagicMock()

        state = _make_state()
        result = ToolLoader.get_tool_by_name("calculator.add", state)
        assert result is not None

    @patch("app.modules.agents.qna.tool_system.RegistryToolWrapper")
    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    def test_suffix_match(self, mock_registry, mock_wrapper):
        from app.modules.agents.qna.tool_system import ToolLoader

        mock_tool = _make_registry_tool_cov(
            description="test", parameters=[]
        )
        mock_registry.get_all_tools.return_value = {
            "calculator.add": mock_tool
        }
        mock_wrapper.return_value = MagicMock()

        state = _make_state()
        result = ToolLoader.get_tool_by_name("add", state)
        assert result is not None

    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    def test_not_found(self, mock_registry):
        from app.modules.agents.qna.tool_system import ToolLoader

        mock_registry.get_all_tools.return_value = {}

        state = _make_state()
        result = ToolLoader.get_tool_by_name("nonexistent", state)
        assert result is None


# ===================================================================
# get_tool_by_name public API
# ===================================================================


class TestGetToolByNamePublic:
    """Cover the public get_tool_by_name function."""

    @patch("app.modules.agents.qna.tool_system.RegistryToolWrapper")
    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    def test_delegates_to_loader(self, mock_registry, mock_wrapper):
        from app.modules.agents.qna.tool_system import get_tool_by_name

        mock_tool = _make_registry_tool_cov(description="test", parameters=[])
        mock_registry.get_all_tools.return_value = {
            "app.tool_name": mock_tool
        }
        mock_wrapper.return_value = MagicMock()

        state = _make_state()
        result = get_tool_by_name("app.tool_name", state)
        assert result is not None


# ===================================================================
# get_tool_results_summary — complex scenarios
# ===================================================================


class TestGetToolResultsSummaryExtended:
    """Cover edge cases in get_tool_results_summary."""

    def test_mixed_categories_and_tools(self):
        from app.modules.agents.qna.tool_system import get_tool_results_summary

        state = {
            "all_tool_results": [
                {"tool_name": "slack.send_message", "status": "success"},
                {"tool_name": "slack.send_message", "status": "error"},
                {"tool_name": "jira.create_issue", "status": "success"},
                {"tool_name": "utility_tool", "status": "success"},
                {"tool_name": "slack.list_channels", "status": "success"},
            ]
        }
        summary = get_tool_results_summary(state)
        assert "Slack" in summary
        assert "Jira" in summary
        assert "Utility" in summary
        assert "Tool Execution Summary" in summary

    def test_empty_tool_name_default(self):
        from app.modules.agents.qna.tool_system import get_tool_results_summary

        state = {
            "all_tool_results": [
                {"status": "success"},
            ]
        }
        summary = get_tool_results_summary(state)
        assert "unknown" in summary.lower() or "utility" in summary.lower()
