"""
Additional tests for app.modules.agents.qna.tool_system targeting uncovered lines:
- _extract_tool_names_from_toolsets: various tool formats
- _load_all_tools: tool limit, blocked tools, knowledge-dependent tools
- ToolLoader.load_tools: cache validity, cache rebuilds
- get_agent_tools: with virtual_record_map
- get_tool_by_name: direct match, suffix match, not found
- get_tool_results_summary: multiple categories
- _parse_tool_name: with and without dot
- _initialize_tool_state
- clear_tool_cache
"""

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.modules.agents.qna.tool_system import (
    MAX_TOOLS_LIMIT,
    _extract_tool_names_from_toolsets,
    _get_blocked_tools,
    _initialize_tool_state,
    _is_internal_tool,
    _is_knowledge_dependent_tool,
    _load_all_tools,
    _parse_tool_name,
    _sanitize_tool_name_if_needed,
    clear_tool_cache,
    get_tool_by_name,
    get_tool_results_summary,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state(**kwargs):
    """Create a minimal ChatState (dict)."""
    state = {
        "logger": MagicMock(spec=logging.Logger),
        "has_knowledge": False,
        "agent_toolsets": [],
    }
    state.update(kwargs)
    return state


def _make_registry_tool(**kwargs):
    """Create a simple namespace that mimics a registry Tool object."""
    return SimpleNamespace(**kwargs)


# ============================================================================
# _extract_tool_names_from_toolsets
# ============================================================================


class TestExtractToolNamesFromToolsets:
    def test_empty_returns_none(self):
        assert _extract_tool_names_from_toolsets([]) is None
        assert _extract_tool_names_from_toolsets(None) is None

    def test_dict_tool_with_fullname(self):
        toolsets = [{"name": "slack", "tools": [{"fullName": "slack.send_message"}]}]
        result = _extract_tool_names_from_toolsets(toolsets)
        assert "slack.send_message" in result

    def test_dict_tool_with_toolname(self):
        toolsets = [{"name": "jira", "tools": [{"toolName": "create_issue"}]}]
        result = _extract_tool_names_from_toolsets(toolsets)
        assert "jira.create_issue" in result

    def test_dict_tool_with_name_field(self):
        toolsets = [{"name": "drive", "tools": [{"name": "list_files"}]}]
        result = _extract_tool_names_from_toolsets(toolsets)
        assert "drive.list_files" in result

    def test_string_tool_with_dot(self):
        toolsets = [{"name": "slack", "tools": ["slack.send_message"]}]
        result = _extract_tool_names_from_toolsets(toolsets)
        assert "slack.send_message" in result

    def test_string_tool_without_dot(self):
        toolsets = [{"name": "slack", "tools": ["send_message"]}]
        result = _extract_tool_names_from_toolsets(toolsets)
        assert "slack.send_message" in result

    def test_empty_toolset_name_skipped(self):
        toolsets = [{"name": "", "tools": [{"fullName": "x.y"}]}]
        result = _extract_tool_names_from_toolsets(toolsets)
        assert result is None

    def test_no_tools_returns_none(self):
        toolsets = [{"name": "slack", "tools": []}]
        result = _extract_tool_names_from_toolsets(toolsets)
        assert result is None

    def test_dict_tool_empty_name_skipped(self):
        toolsets = [{"name": "slack", "tools": [{"name": ""}]}]
        result = _extract_tool_names_from_toolsets(toolsets)
        assert result is None


# ============================================================================
# _parse_tool_name
# ============================================================================


class TestParseToolName:
    def test_with_dot(self):
        result = _parse_tool_name("slack.send")
        assert list(result) == ["slack", "send"]

    def test_without_dot(self):
        result = _parse_tool_name("standalone")
        assert list(result) == ["default", "standalone"]

    def test_multiple_dots(self):
        result = _parse_tool_name("app.sub.tool")
        assert list(result) == ["app", "sub.tool"]


# ============================================================================
# _initialize_tool_state
# ============================================================================


class TestInitializeToolState:
    def test_sets_defaults(self):
        state = {}
        _initialize_tool_state(state)
        assert "tool_results" in state
        assert "all_tool_results" in state

    def test_does_not_overwrite_existing(self):
        state = {"tool_results": [1, 2, 3]}
        _initialize_tool_state(state)
        assert state["tool_results"] == [1, 2, 3]


# ============================================================================
# _is_knowledge_dependent_tool
# ============================================================================


class TestIsKnowledgeDependentTool:
    def test_retrieval_app_name(self):
        tool = _make_registry_tool(app_name="retrieval")
        assert _is_knowledge_dependent_tool("retrieval.search", tool) is True

    def test_knowledgehub_app_name(self):
        tool = _make_registry_tool(app_name="knowledgehub")
        assert _is_knowledge_dependent_tool("knowledgehub.browse", tool) is True

    def test_non_knowledge_tool(self):
        tool = _make_registry_tool(app_name="slack")
        assert _is_knowledge_dependent_tool("slack.send", tool) is False

    def test_pattern_match_in_name(self):
        tool = _make_registry_tool()
        assert _is_knowledge_dependent_tool("retrieval.search_docs", tool) is True

    def test_knowledgehub_pattern_in_name(self):
        tool = _make_registry_tool()
        assert _is_knowledge_dependent_tool("knowledgehub.list_files", tool) is True


# ============================================================================
# _is_internal_tool
# ============================================================================


class TestIsInternalToolExtra:
    def test_internal_category_in_metadata(self):
        metadata = SimpleNamespace(category="internal", is_internal=False)
        tool = _make_registry_tool(metadata=metadata)
        assert _is_internal_tool("custom.tool", tool) is True

    def test_is_internal_flag_in_metadata(self):
        metadata = SimpleNamespace(category="other", is_internal=True)
        tool = _make_registry_tool(metadata=metadata)
        assert _is_internal_tool("custom.tool", tool) is True

    def test_calculator_app_name(self):
        tool = _make_registry_tool(app_name="calculator")
        assert _is_internal_tool("calculator.add", tool) is True

    def test_datetime_app_name(self):
        tool = _make_registry_tool(app_name="datetime")
        assert _is_internal_tool("datetime.now", tool) is True

    def test_utility_app_name(self):
        tool = _make_registry_tool(app_name="utility")
        assert _is_internal_tool("utility.format", tool) is True

    def test_web_search_pattern(self):
        tool = _make_registry_tool()
        assert _is_internal_tool("tools.web_search", tool) is True

    def test_get_current_datetime_pattern(self):
        tool = _make_registry_tool()
        assert _is_internal_tool("util.get_current_datetime", tool) is True


# ============================================================================
# _load_all_tools
# ============================================================================


class TestLoadAllTools:
    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    @patch("app.modules.agents.qna.tool_system.RegistryToolWrapper")
    def test_loads_internal_tools(self, mock_wrapper, mock_registry):
        mock_tool = _make_registry_tool(app_name="calculator")
        mock_registry.get_all_tools.return_value = {"calculator.add": mock_tool}
        mock_wrapper.return_value = MagicMock(name="calculator.add")

        state = _make_state()
        tools = _load_all_tools(state, {})
        assert len(tools) == 1

    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    @patch("app.modules.agents.qna.tool_system.RegistryToolWrapper")
    def test_blocks_failed_tools(self, mock_wrapper, mock_registry):
        mock_tool = _make_registry_tool(app_name="calculator")
        mock_registry.get_all_tools.return_value = {"calculator.add": mock_tool}

        state = _make_state()
        tools = _load_all_tools(state, {"calculator.add": 3})
        assert len(tools) == 0

    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    @patch("app.modules.agents.qna.tool_system.RegistryToolWrapper")
    def test_skips_retrieval_when_no_knowledge(self, mock_wrapper, mock_registry):
        mock_tool = _make_registry_tool(app_name="retrieval")
        mock_registry.get_all_tools.return_value = {"retrieval.search": mock_tool}

        state = _make_state(has_knowledge=False)
        tools = _load_all_tools(state, {})
        assert len(tools) == 0

    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    @patch("app.modules.agents.qna.tool_system.RegistryToolWrapper")
    def test_includes_retrieval_when_has_knowledge(self, mock_wrapper, mock_registry):
        mock_tool = _make_registry_tool(app_name="retrieval")
        mock_registry.get_all_tools.return_value = {"retrieval.search": mock_tool}
        mock_wrapper.return_value = MagicMock(name="retrieval.search")

        state = _make_state(has_knowledge=True)
        tools = _load_all_tools(state, {})
        assert len(tools) == 1

    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    @patch("app.modules.agents.qna.tool_system.RegistryToolWrapper")
    def test_user_enabled_tools(self, mock_wrapper, mock_registry):
        mock_tool = _make_registry_tool(app_name="slack")
        mock_registry.get_all_tools.return_value = {"slack.send": mock_tool}
        mock_wrapper.return_value = MagicMock(name="slack.send")

        state = _make_state(agent_toolsets=[{"name": "slack", "tools": ["send"]}])
        tools = _load_all_tools(state, {})
        assert len(tools) == 1

    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    @patch("app.modules.agents.qna.tool_system.RegistryToolWrapper")
    def test_tool_not_user_enabled_skipped(self, mock_wrapper, mock_registry):
        mock_tool = _make_registry_tool(app_name="slack")
        mock_registry.get_all_tools.return_value = {"slack.send": mock_tool}

        state = _make_state(agent_toolsets=[{"name": "jira", "tools": ["create"]}])
        tools = _load_all_tools(state, {})
        assert len(tools) == 0

    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    @patch("app.modules.agents.qna.tool_system.RegistryToolWrapper")
    def test_tool_load_exception_handled(self, mock_wrapper, mock_registry):
        mock_registry.get_all_tools.return_value = {"bad.tool": _make_registry_tool(app_name="bad")}
        mock_wrapper.side_effect = Exception("wrapper error")

        state = _make_state(agent_toolsets=[{"name": "bad", "tools": ["tool"]}])
        tools = _load_all_tools(state, {})
        assert len(tools) == 0


# ============================================================================
# ToolLoader.load_tools caching
# ============================================================================


class TestToolLoaderCaching:
    @patch("app.modules.agents.qna.tool_system._load_all_tools")
    @patch("app.modules.agents.qna.tool_system._initialize_tool_state")
    def test_cache_hit(self, mock_init, mock_load):
        from app.modules.agents.qna.tool_system import ToolLoader

        cached_tools = [MagicMock()]
        state = _make_state(
            _cached_agent_tools=cached_tools,
            _cached_blocked_tools={},
            _cached_has_knowledge=False,
        )
        result = ToolLoader.load_tools(state)
        assert result is cached_tools
        mock_load.assert_not_called()

    @patch("app.modules.agents.qna.tool_system._load_all_tools", return_value=[])
    @patch("app.modules.agents.qna.tool_system._initialize_tool_state")
    def test_cache_invalidated_on_knowledge_change(self, mock_init, mock_load):
        from app.modules.agents.qna.tool_system import ToolLoader

        state = _make_state(
            _cached_agent_tools=[MagicMock()],
            _cached_blocked_tools={},
            _cached_has_knowledge=True,  # Was True
            has_knowledge=False,  # Now False
        )
        ToolLoader.load_tools(state)
        mock_load.assert_called_once()

    @patch("app.modules.agents.qna.tool_system._load_all_tools", return_value=[])
    @patch("app.modules.agents.qna.tool_system._initialize_tool_state")
    def test_cache_invalidated_on_blocked_tools_change(self, mock_init, mock_load):
        from app.modules.agents.qna.tool_system import ToolLoader

        state = _make_state(
            _cached_agent_tools=[MagicMock()],
            _cached_blocked_tools={},
            _cached_has_knowledge=False,
            all_tool_results=[
                {"status": "error", "tool_name": "x"},
                {"status": "error", "tool_name": "x"},
                {"status": "error", "tool_name": "x"},
            ],
        )
        ToolLoader.load_tools(state)
        mock_load.assert_called_once()


# ============================================================================
# get_tool_by_name
# ============================================================================


class TestGetToolByNameExtra:
    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    @patch("app.modules.agents.qna.tool_system.RegistryToolWrapper")
    def test_direct_match(self, mock_wrapper, mock_registry):
        mock_tool = _make_registry_tool(app_name="slack")
        mock_registry.get_all_tools.return_value = {"slack.send": mock_tool}
        mock_wrapper.return_value = MagicMock()

        state = _make_state()
        result = get_tool_by_name("slack.send", state)
        assert result is not None

    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    @patch("app.modules.agents.qna.tool_system.RegistryToolWrapper")
    def test_suffix_match(self, mock_wrapper, mock_registry):
        mock_tool = _make_registry_tool(app_name="slack")
        mock_registry.get_all_tools.return_value = {"slack.send_message": mock_tool}
        mock_wrapper.return_value = MagicMock()

        state = _make_state()
        result = get_tool_by_name("send_message", state)
        assert result is not None

    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    def test_not_found(self, mock_registry):
        mock_registry.get_all_tools.return_value = {}
        state = _make_state()
        result = get_tool_by_name("nonexistent", state)
        assert result is None


# ============================================================================
# get_tool_results_summary
# ============================================================================


class TestGetToolResultsSummaryExtra:
    def test_multiple_categories(self):
        state = _make_state(all_tool_results=[
            {"tool_name": "slack.send", "status": "success"},
            {"tool_name": "slack.send", "status": "error"},
            {"tool_name": "jira.create", "status": "success"},
            {"tool_name": "calculator", "status": "success"},
        ])
        summary = get_tool_results_summary(state)
        assert "Slack" in summary
        assert "Jira" in summary
        assert "Utility" in summary

    def test_unknown_status(self):
        state = _make_state(all_tool_results=[
            {"tool_name": "x.y", "status": "pending"},
        ])
        summary = get_tool_results_summary(state)
        assert "X" in summary


# ============================================================================
# clear_tool_cache
# ============================================================================


class TestClearToolCacheExtra:
    def test_clears_all_cache_keys(self):
        state = _make_state(
            _cached_agent_tools=[1, 2],
            _tool_instance_cache={"x": 1},
            _cached_blocked_tools={"y": 2},
            _cached_schema_tools=[3],
        )
        clear_tool_cache(state)
        assert "_cached_agent_tools" not in state
        assert "_tool_instance_cache" not in state
        assert "_cached_blocked_tools" not in state
        assert "_cached_schema_tools" not in state


# ============================================================================
# _sanitize_tool_name_if_needed
# ============================================================================


class TestSanitizeToolNameIfNeeded:
    def test_replaces_dots(self):
        state = _make_state()
        result = _sanitize_tool_name_if_needed("slack.send_message", None, state)
        assert result == "slack_send_message"

    def test_no_dots(self):
        state = _make_state()
        result = _sanitize_tool_name_if_needed("standalone", None, state)
        assert result == "standalone"


# ============================================================================
# _needs_tool_name_sanitization — provider-specific branches (lines 60, 69, 72)
# ============================================================================


class TestNeedsToolNameSanitization:
    """Cover lines 60, 69, 72: ChatAnthropic/ChatOpenAI isinstance checks."""

    def test_anthropic_returns_true(self):
        """Anthropic LLM instances require sanitization."""
        from langchain_anthropic import ChatAnthropic
        from app.modules.agents.qna.tool_system import _requires_sanitized_tool_names
        # Use spec to make isinstance check pass
        mock_llm = MagicMock(spec=ChatAnthropic)
        result = _requires_sanitized_tool_names(mock_llm)
        assert result is True

    def test_openai_returns_true(self):
        """OpenAI LLM instances require sanitization."""
        from langchain_openai import ChatOpenAI
        from app.modules.agents.qna.tool_system import _requires_sanitized_tool_names
        mock_llm = MagicMock(spec=ChatOpenAI)
        result = _requires_sanitized_tool_names(mock_llm)
        assert result is True

    def test_import_error_fallback(self):
        """When both imports fail, defaults to True."""
        from app.modules.agents.qna.tool_system import _requires_sanitized_tool_names
        mock_llm = MagicMock()
        with patch.dict("sys.modules", {"langchain_anthropic": None, "langchain_openai": None}):
            result = _requires_sanitized_tool_names(mock_llm)
        assert result is True


# ============================================================================
# get_agent_tools — exception in fetch_full_record (lines 456-459)
# ============================================================================


class TestGetAgentToolsFetchRecordError:
    """Cover lines 456-459: exception adding fetch_full_record tool."""

    def test_fetch_record_exception_logged(self):
        from app.modules.agents.qna.tool_system import get_agent_tools
        state = _make_state(
            has_knowledge=True,
            virtual_record_id_to_result={"rec1": "data1"},
            _cached_agent_tools=None,
        )
        with patch("app.modules.agents.qna.tool_system.ToolLoader.load_tools", return_value=[]), \
             patch("app.utils.fetch_full_record.create_fetch_full_record_tool", side_effect=RuntimeError("boom")):
            result = get_agent_tools(state)
        assert isinstance(result, list)
        state["logger"].warning.assert_called()


# ============================================================================
# get_agent_tools_with_schemas — fallback paths (lines 655-662)
# ============================================================================


class TestGetAgentToolsWithSchemasFallback:
    """Cover lines 655-662: ImportError and generic Exception fallbacks."""

    def test_import_error_falls_back(self):
        from app.modules.agents.qna.tool_system import get_agent_tools_with_schemas
        state = _make_state(
            has_knowledge=True,
            _cached_schema_tools=None,
        )
        with patch("app.modules.agents.qna.tool_system.get_agent_tools", return_value=["tool1"]), \
             patch.dict("sys.modules", {"langchain_core.tools": None}):
            result = get_agent_tools_with_schemas(state)
        assert result == ["tool1"]

    def test_generic_exception_falls_back(self):
        from app.modules.agents.qna.tool_system import get_agent_tools_with_schemas
        state = _make_state(
            has_knowledge=True,
            _cached_schema_tools=None,
        )
        with patch("app.modules.agents.qna.tool_system.get_agent_tools", return_value=["tool1"]), \
             patch("app.modules.agents.qna.tool_system.ToolLoader.load_tools", side_effect=RuntimeError("kaboom")):
            result = get_agent_tools_with_schemas(state)
        assert isinstance(result, list)
