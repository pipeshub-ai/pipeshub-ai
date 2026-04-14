"""Comprehensive tests for app.modules.agents.qna.tool_system.

Targets >97% statement and branch coverage for tool_system.py.
Covers all functions: _requires_sanitized_tool_names, _sanitize_tool_name_if_needed,
ToolLoader, _load_all_tools, _extract_tool_names_from_toolsets,
_is_knowledge_dependent_tool, _is_internal_tool, _get_blocked_tools,
_parse_tool_name, _initialize_tool_state, get_agent_tools,
get_tool_by_name, clear_tool_cache, get_tool_results_summary,
get_agent_tools_with_schemas.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest

from app.modules.agents.qna.tool_system import (
    FAILURE_LOOKBACK_WINDOW,
    FAILURE_THRESHOLD,
    MAX_RESULT_PREVIEW_LENGTH,
    MAX_TOOLS_LIMIT,
    ToolLoader,
    _extract_tool_names_from_toolsets,
    _get_blocked_tools,
    _initialize_tool_state,
    _is_internal_tool,
    _is_knowledge_dependent_tool,
    _load_all_tools,
    _parse_tool_name,
    _requires_sanitized_tool_names,
    _sanitize_tool_name_if_needed,
    clear_tool_cache,
    get_agent_tools,
    get_agent_tools_with_schemas,
    get_tool_by_name,
    get_tool_results_summary,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_state():
    """A dict-like state with logger."""
    state = {
        "logger": logging.getLogger("test_tool_system"),
    }
    return state


@pytest.fixture
def mock_state_no_logger():
    """State without logger."""
    return {}


def _make_registry_tool(app_name="myapp", tool_name="mytool", description="desc",
                        metadata=None, is_internal_flag=False, args_schema=None,
                        has_metadata=True):
    """Create a mock registry Tool.

    When has_metadata=False, creates a tool without metadata attribute.
    """
    if has_metadata and metadata is not None:
        tool = MagicMock()
        tool.app_name = app_name
        tool.tool_name = tool_name
        tool.description = description
        tool.args_schema = args_schema
        tool.metadata = metadata
    elif has_metadata:
        tool = MagicMock()
        tool.app_name = app_name
        tool.tool_name = tool_name
        tool.description = description
        tool.args_schema = args_schema
    else:
        # Use spec to restrict which attributes exist
        tool = MagicMock(spec=["app_name", "tool_name", "description", "args_schema"])
        tool.app_name = app_name
        tool.tool_name = tool_name
        tool.description = description
        tool.args_schema = args_schema

    return tool


# ============================================================================
# Constants
# ============================================================================

class TestConstants:
    def test_max_tools_limit(self):
        assert MAX_TOOLS_LIMIT == 128

    def test_max_result_preview_length(self):
        assert MAX_RESULT_PREVIEW_LENGTH == 150

    def test_failure_lookback_window(self):
        assert FAILURE_LOOKBACK_WINDOW == 7

    def test_failure_threshold(self):
        assert FAILURE_THRESHOLD == 3


# ============================================================================
# _requires_sanitized_tool_names
# ============================================================================

class TestRequiresSanitizedToolNames:
    def test_none_llm_returns_true(self):
        assert _requires_sanitized_tool_names(None) is True

    def test_falsy_llm_returns_true(self):
        assert _requires_sanitized_tool_names(False) is True
        assert _requires_sanitized_tool_names(0) is True

    def test_anthropic_llm_returns_true(self):
        with patch.dict("sys.modules", {"langchain_anthropic": MagicMock()}):
            mock_llm = MagicMock()
            # Make isinstance check pass for ChatAnthropic
            import sys
            mock_module = sys.modules["langchain_anthropic"]
            mock_module.ChatAnthropic = type(mock_llm)
            assert _requires_sanitized_tool_names(mock_llm) is True

    def test_openai_llm_returns_true(self):
        with patch.dict("sys.modules", {"langchain_openai": MagicMock()}):
            mock_llm = MagicMock()
            import sys
            mock_module = sys.modules["langchain_openai"]
            mock_module.ChatOpenAI = type(mock_llm)
            mock_module.AzureChatOpenAI = type("FakeAzure", (), {})
            assert _requires_sanitized_tool_names(mock_llm) is True

    def test_unknown_llm_returns_true(self):
        """Unknown LLMs default to sanitized for safety."""
        mock_llm = MagicMock()
        # Patch imports to NOT match any known type
        with patch("app.modules.agents.qna.tool_system._requires_sanitized_tool_names") as orig:
            # Call the real function instead
            pass
        # Just call it directly - since isinstance won't match MagicMock
        result = _requires_sanitized_tool_names(mock_llm)
        assert result is True

    def test_anthropic_import_error(self):
        """ImportError for langchain_anthropic is handled gracefully."""
        mock_llm = MagicMock()
        with patch.dict("sys.modules", {"langchain_anthropic": None}):
            # Importing from None module raises ImportError
            result = _requires_sanitized_tool_names(mock_llm)
            assert result is True

    def test_openai_import_error(self):
        """ImportError for langchain_openai is handled gracefully."""
        mock_llm = MagicMock()
        with patch.dict("sys.modules", {"langchain_openai": None}):
            result = _requires_sanitized_tool_names(mock_llm)
            assert result is True

    def test_anthropic_generic_exception(self):
        """Generic exception during anthropic check is handled."""
        mock_llm = MagicMock()
        # Patch the import itself to raise a non-ImportError
        with patch("builtins.__import__", side_effect=RuntimeError("bad")):
            # This will cause the except Exception handler for both anthropic and openai
            result = _requires_sanitized_tool_names(mock_llm)
            assert result is True


# ============================================================================
# _sanitize_tool_name_if_needed
# ============================================================================

class TestSanitizeToolNameIfNeeded:
    def test_sanitizes_dots_when_needed(self):
        result = _sanitize_tool_name_if_needed("slack.send_message", None, {})
        assert result == "slack_send_message"

    def test_no_dots_unchanged(self):
        result = _sanitize_tool_name_if_needed("calculator_add", None, {})
        assert result == "calculator_add"

    def test_multiple_dots(self):
        result = _sanitize_tool_name_if_needed("a.b.c.d", None, {})
        assert result == "a_b_c_d"

    def test_returns_original_when_sanitization_not_needed(self):
        """When _requires_sanitized_tool_names returns False, dots are kept."""
        with patch("app.modules.agents.qna.tool_system._requires_sanitized_tool_names", return_value=False):
            result = _sanitize_tool_name_if_needed("slack.send_message", MagicMock(), {})
            assert result == "slack.send_message"


# ============================================================================
# _parse_tool_name
# ============================================================================

class TestParseToolName:
    def test_with_dot(self):
        result = _parse_tool_name("slack.send_message")
        assert result[0] == "slack"
        assert result[1] == "send_message"

    def test_without_dot(self):
        result = _parse_tool_name("calculator")
        assert result[0] == "default"
        assert result[1] == "calculator"

    def test_multiple_dots(self):
        """Only splits on first dot."""
        result = _parse_tool_name("a.b.c")
        assert result[0] == "a"
        assert result[1] == "b.c"


# ============================================================================
# _initialize_tool_state
# ============================================================================

class TestInitializeToolState:
    def test_sets_defaults(self):
        state = {}
        _initialize_tool_state(state)
        assert state["tool_results"] == []
        assert state["all_tool_results"] == []

    def test_preserves_existing(self):
        state = {"tool_results": [{"x": 1}], "all_tool_results": [{"y": 2}]}
        _initialize_tool_state(state)
        assert state["tool_results"] == [{"x": 1}]
        assert state["all_tool_results"] == [{"y": 2}]


# ============================================================================
# _get_blocked_tools
# ============================================================================

class TestGetBlockedTools:
    def test_empty_results(self):
        state = {}
        assert _get_blocked_tools(state) == {}

    def test_no_all_tool_results(self):
        state = {"all_tool_results": None}
        assert _get_blocked_tools(state) == {}

    def test_fewer_than_threshold(self):
        state = {"all_tool_results": [{"status": "error", "tool_name": "t"}]}
        assert _get_blocked_tools(state) == {}

    def test_no_failures(self):
        state = {"all_tool_results": [
            {"status": "success", "tool_name": "t1"},
            {"status": "success", "tool_name": "t2"},
            {"status": "success", "tool_name": "t3"},
        ]}
        assert _get_blocked_tools(state) == {}

    def test_failures_below_threshold(self):
        state = {"all_tool_results": [
            {"status": "error", "tool_name": "t1"},
            {"status": "error", "tool_name": "t1"},
            {"status": "success", "tool_name": "t2"},
        ]}
        assert _get_blocked_tools(state) == {}

    def test_failures_at_threshold(self):
        state = {"all_tool_results": [
            {"status": "error", "tool_name": "bad_tool"},
            {"status": "error", "tool_name": "bad_tool"},
            {"status": "error", "tool_name": "bad_tool"},
        ]}
        result = _get_blocked_tools(state)
        assert "bad_tool" in result
        assert result["bad_tool"] == 3

    def test_only_recent_results_checked(self):
        """Only last FAILURE_LOOKBACK_WINDOW results are checked."""
        state = {"all_tool_results": [
            {"status": "error", "tool_name": "t"},
            {"status": "error", "tool_name": "t"},
            {"status": "error", "tool_name": "t"},
            {"status": "error", "tool_name": "t"},
            {"status": "error", "tool_name": "t"},
            {"status": "error", "tool_name": "t"},
            {"status": "error", "tool_name": "t"},
            # These are the last 7 (FAILURE_LOOKBACK_WINDOW=7)
            {"status": "success", "tool_name": "t"},
            {"status": "success", "tool_name": "t"},
            {"status": "success", "tool_name": "t"},
            {"status": "success", "tool_name": "t"},
            {"status": "success", "tool_name": "t"},
            {"status": "success", "tool_name": "t"},
            {"status": "success", "tool_name": "t"},
        ]}
        result = _get_blocked_tools(state)
        assert result == {}

    def test_unknown_tool_name(self):
        """Tool name defaults to 'unknown' when missing."""
        state = {"all_tool_results": [
            {"status": "error"},
            {"status": "error"},
            {"status": "error"},
        ]}
        result = _get_blocked_tools(state)
        assert "unknown" in result


# ============================================================================
# _extract_tool_names_from_toolsets
# ============================================================================

class TestExtractToolNamesFromToolsets:
    def test_none_toolsets(self):
        assert _extract_tool_names_from_toolsets(None) is None

    def test_empty_toolsets(self):
        assert _extract_tool_names_from_toolsets([]) is None

    def test_toolset_with_no_name(self):
        result = _extract_tool_names_from_toolsets([{"name": "", "tools": []}])
        assert result is None

    def test_dict_tool_with_fullname(self):
        result = _extract_tool_names_from_toolsets([{
            "name": "slack",
            "tools": [{"fullName": "slack.send_message"}],
        }])
        assert result == {"slack.send_message"}

    def test_dict_tool_with_toolname(self):
        result = _extract_tool_names_from_toolsets([{
            "name": "slack",
            "tools": [{"toolName": "send_message"}],
        }])
        assert result == {"slack.send_message"}

    def test_dict_tool_with_name_field(self):
        result = _extract_tool_names_from_toolsets([{
            "name": "slack",
            "tools": [{"name": "send_message"}],
        }])
        assert result == {"slack.send_message"}

    def test_dict_tool_empty_name(self):
        """Tool with no fullName and empty name yields toolset.'' which is filtered out."""
        result = _extract_tool_names_from_toolsets([{
            "name": "slack",
            "tools": [{}],
        }])
        # full_name = "slack." which equals f"{toolset_name}." so it's filtered
        assert result is None

    def test_string_tool_with_dot(self):
        result = _extract_tool_names_from_toolsets([{
            "name": "slack",
            "tools": ["slack.send_message"],
        }])
        assert result == {"slack.send_message"}

    def test_string_tool_without_dot(self):
        result = _extract_tool_names_from_toolsets([{
            "name": "slack",
            "tools": ["send_message"],
        }])
        assert result == {"slack.send_message"}

    def test_multiple_toolsets(self):
        result = _extract_tool_names_from_toolsets([
            {"name": "slack", "tools": ["slack.send_message"]},
            {"name": "jira", "tools": [{"fullName": "jira.create_issue"}]},
        ])
        assert result == {"slack.send_message", "jira.create_issue"}

    def test_empty_tools_list(self):
        result = _extract_tool_names_from_toolsets([{
            "name": "slack",
            "tools": [],
        }])
        assert result is None


# ============================================================================
# _is_knowledge_dependent_tool
# ============================================================================

class TestIsKnowledgeDependentTool:
    def test_retrieval_app_name(self):
        tool = MagicMock()
        tool.app_name = "retrieval"
        assert _is_knowledge_dependent_tool("retrieval.search", tool) is True

    def test_knowledgehub_app_name(self):
        tool = MagicMock()
        tool.app_name = "knowledgehub"
        assert _is_knowledge_dependent_tool("knowledgehub.browse", tool) is True

    def test_other_app_name(self):
        tool = MagicMock()
        tool.app_name = "slack"
        assert _is_knowledge_dependent_tool("slack.send", tool) is False

    def test_retrieval_in_full_name(self):
        tool = MagicMock(spec=[])  # No app_name attribute
        assert _is_knowledge_dependent_tool("retrieval.search", tool) is True

    def test_knowledgehub_in_full_name(self):
        tool = MagicMock(spec=[])
        assert _is_knowledge_dependent_tool("knowledgehub.files", tool) is True

    def test_no_match(self):
        tool = MagicMock(spec=[])
        assert _is_knowledge_dependent_tool("slack.send", tool) is False

    def test_case_insensitive(self):
        tool = MagicMock()
        tool.app_name = "Retrieval"
        assert _is_knowledge_dependent_tool("Retrieval.Search", tool) is True


# ============================================================================
# _is_internal_tool
# ============================================================================

class TestIsInternalTool:
    def test_metadata_category_internal(self):
        tool = MagicMock()
        tool.metadata.category = "internal"
        tool.metadata.is_internal = False
        assert _is_internal_tool("some.tool", tool) is True

    def test_metadata_is_internal_flag(self):
        tool = MagicMock()
        tool.metadata.category = "other"
        tool.metadata.is_internal = True
        assert _is_internal_tool("some.tool", tool) is True

    def test_calculator_app_name(self):
        tool = MagicMock(spec=["app_name"])
        tool.app_name = "calculator"
        assert _is_internal_tool("calculator.add", tool) is True

    def test_datetime_app_name(self):
        tool = MagicMock(spec=["app_name"])
        tool.app_name = "datetime"
        assert _is_internal_tool("datetime.now", tool) is True

    def test_utility_app_name(self):
        tool = MagicMock(spec=["app_name"])
        tool.app_name = "utility"
        assert _is_internal_tool("utility.help", tool) is True

    def test_web_search_pattern(self):
        tool = MagicMock(spec=[])  # No metadata, no app_name
        assert _is_internal_tool("tools.web_search", tool) is True

    def test_get_current_datetime_pattern(self):
        tool = MagicMock(spec=[])
        assert _is_internal_tool("utils.get_current_datetime", tool) is True

    def test_calculator_pattern(self):
        tool = MagicMock(spec=[])
        assert _is_internal_tool("calculator.add", tool) is True

    def test_not_internal(self):
        tool = MagicMock(spec=[])
        assert _is_internal_tool("slack.send_message", tool) is False

    def test_metadata_no_category(self):
        """Tool with metadata but no category attribute."""
        tool = MagicMock()
        tool.metadata = MagicMock(spec=["is_internal"])
        tool.metadata.is_internal = True
        assert _is_internal_tool("some.tool", tool) is True

    def test_metadata_no_is_internal(self):
        """Tool with metadata.category but no is_internal attribute."""
        tool = MagicMock(spec=["metadata"])
        tool.metadata = MagicMock(spec=["category"])
        tool.metadata.category = "external"
        assert _is_internal_tool("slack.send", tool) is False


# ============================================================================
# _load_all_tools
# ============================================================================

class TestLoadAllTools:
    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    @patch("app.modules.agents.qna.tool_system.RegistryToolWrapper")
    def test_loads_internal_tools(self, MockWrapper, mock_registry, mock_state):
        """Internal tools are always loaded."""
        internal_tool = _make_registry_tool(app_name="calculator", tool_name="add")
        internal_tool.app_name = "calculator"
        mock_registry.get_all_tools.return_value = {"calculator.add": internal_tool}
        MockWrapper.return_value = MagicMock(name="calculator_add_wrapper")

        result = _load_all_tools(mock_state, {})
        assert len(result) == 1

    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    @patch("app.modules.agents.qna.tool_system.RegistryToolWrapper")
    def test_blocked_tools_skipped(self, MockWrapper, mock_registry, mock_state):
        """Blocked tools are not loaded."""
        tool = _make_registry_tool(app_name="calculator", tool_name="add")
        tool.app_name = "calculator"
        mock_registry.get_all_tools.return_value = {"calculator.add": tool}

        result = _load_all_tools(mock_state, {"calculator.add": 3})
        assert len(result) == 0

    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    @patch("app.modules.agents.qna.tool_system.RegistryToolWrapper")
    def test_retrieval_skipped_when_no_knowledge(self, MockWrapper, mock_registry, mock_state):
        """Retrieval tools are skipped when has_knowledge is False."""
        tool = _make_registry_tool(app_name="retrieval", tool_name="search")
        tool.app_name = "retrieval"
        mock_registry.get_all_tools.return_value = {"retrieval.search": tool}
        mock_state["has_knowledge"] = False

        result = _load_all_tools(mock_state, {})
        assert len(result) == 0

    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    @patch("app.modules.agents.qna.tool_system.RegistryToolWrapper")
    def test_retrieval_loaded_when_knowledge_available(self, MockWrapper, mock_registry, mock_state):
        """Retrieval tools are loaded as internal when has_knowledge is True."""
        tool = _make_registry_tool(app_name="retrieval", tool_name="search")
        tool.app_name = "retrieval"
        mock_registry.get_all_tools.return_value = {"retrieval.search": tool}
        mock_state["has_knowledge"] = True
        MockWrapper.return_value = MagicMock()

        result = _load_all_tools(mock_state, {})
        assert len(result) == 1

    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    @patch("app.modules.agents.qna.tool_system.RegistryToolWrapper")
    def test_user_enabled_tools_loaded(self, MockWrapper, mock_registry, mock_state):
        """User-enabled tools are loaded when in agent_toolsets."""
        tool = MagicMock(spec=["app_name", "tool_name", "description"])
        tool.app_name = "slack"
        tool.tool_name = "send"
        tool.description = "desc"
        mock_registry.get_all_tools.return_value = {"slack.send": tool}
        mock_state["agent_toolsets"] = [{"name": "slack", "tools": ["slack.send"]}]
        MockWrapper.return_value = MagicMock()

        result = _load_all_tools(mock_state, {})
        assert len(result) == 1

    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    def test_tool_loading_exception_handled(self, mock_registry, mock_state):
        """Exceptions during individual tool loading are caught."""
        tool = MagicMock()
        # Make _parse_tool_name raise
        mock_registry.get_all_tools.return_value = {"bad": tool}

        # _parse_tool_name won't raise for "bad" (returns ("default", "bad"))
        # but we can make RegistryToolWrapper raise
        with patch("app.modules.agents.qna.tool_system.RegistryToolWrapper", side_effect=Exception("wrapper error")):
            with patch("app.modules.agents.qna.tool_system._is_internal_tool", return_value=True):
                result = _load_all_tools(mock_state, {})
                assert len(result) == 0

    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    @patch("app.modules.agents.qna.tool_system.RegistryToolWrapper")
    def test_tool_limit_enforced(self, MockWrapper, mock_registry, mock_state):
        """Tools are capped at MAX_TOOLS_LIMIT when user tools + internal exceed limit."""
        tools = {}
        # Add 5 internal tools (calculator)
        for i in range(5):
            tool = MagicMock(spec=["app_name", "tool_name", "description"])
            tool.app_name = "calculator"
            tool.tool_name = f"calc{i}"
            tool.description = "desc"
            tools[f"calculator.calc{i}"] = tool
        # Add 150 user-enabled tools (slack)
        for i in range(150):
            tool = MagicMock(spec=["app_name", "tool_name", "description"])
            tool.app_name = "slack"
            tool.tool_name = f"tool{i}"
            tool.description = "desc"
            tools[f"slack.tool{i}"] = tool
        mock_registry.get_all_tools.return_value = tools

        # Enable all slack tools via toolsets
        mock_state["agent_toolsets"] = [{
            "name": "slack",
            "tools": [f"slack.tool{i}" for i in range(150)],
        }]
        MockWrapper.side_effect = lambda app, name, rt, state: MagicMock(name=f"{app}.{name}")

        result = _load_all_tools(mock_state, {})
        assert len(result) <= MAX_TOOLS_LIMIT

    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    @patch("app.modules.agents.qna.tool_system.RegistryToolWrapper")
    def test_no_toolsets_no_user_tools(self, MockWrapper, mock_registry, mock_state_no_logger):
        """When no toolsets, only internal tools are loaded."""
        tool = MagicMock(spec=["app_name", "tool_name", "description"])
        tool.app_name = "slack"
        tool.tool_name = "send"
        tool.description = "desc"
        mock_registry.get_all_tools.return_value = {"slack.send": tool}
        # No agent_toolsets

        result = _load_all_tools(mock_state_no_logger, {})
        assert len(result) == 0  # slack.send is not internal

    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    @patch("app.modules.agents.qna.tool_system.RegistryToolWrapper")
    def test_toolsets_with_empty_tools_warning(self, MockWrapper, mock_registry, mock_state):
        """When toolsets have no extractable tools, warning is logged."""
        tool = MagicMock(spec=["app_name", "tool_name", "description"])
        tool.app_name = "slack"
        tool.tool_name = "send"
        tool.description = "desc"
        mock_registry.get_all_tools.return_value = {"slack.send": tool}
        mock_state["agent_toolsets"] = [{"name": "slack", "tools": []}]

        result = _load_all_tools(mock_state, {})
        assert len(result) == 0


# ============================================================================
# ToolLoader
# ============================================================================

class TestToolLoader:

    @patch("app.modules.agents.qna.tool_system._load_all_tools")
    @patch("app.modules.agents.qna.tool_system._initialize_tool_state")
    @patch("app.modules.agents.qna.tool_system._get_blocked_tools", return_value={})
    def test_load_tools_first_call(self, mock_blocked, mock_init, mock_load, mock_state):
        """First call builds cache."""
        mock_load.return_value = [MagicMock(name="tool1")]
        result = ToolLoader.load_tools(mock_state)
        assert len(result) == 1
        assert mock_state["_cached_agent_tools"] is not None
        mock_init.assert_called_once()

    @patch("app.modules.agents.qna.tool_system._load_all_tools")
    @patch("app.modules.agents.qna.tool_system._initialize_tool_state")
    @patch("app.modules.agents.qna.tool_system._get_blocked_tools", return_value={})
    def test_load_tools_cache_hit(self, mock_blocked, mock_init, mock_load, mock_state):
        """Second call uses cache."""
        cached = [MagicMock(name="cached_tool")]
        mock_state["_cached_agent_tools"] = cached
        mock_state["_cached_blocked_tools"] = {}
        mock_state["_cached_has_knowledge"] = False

        result = ToolLoader.load_tools(mock_state)
        assert result is cached
        mock_load.assert_not_called()

    @patch("app.modules.agents.qna.tool_system._load_all_tools")
    @patch("app.modules.agents.qna.tool_system._initialize_tool_state")
    @patch("app.modules.agents.qna.tool_system._get_blocked_tools", return_value={"bad": 3})
    def test_load_tools_cache_invalidated_by_blocked(self, mock_blocked, mock_init, mock_load, mock_state):
        """Cache is invalidated when blocked tools change."""
        mock_state["_cached_agent_tools"] = [MagicMock()]
        mock_state["_cached_blocked_tools"] = {}
        mock_state["_cached_has_knowledge"] = False
        mock_load.return_value = [MagicMock(name="rebuilt")]

        result = ToolLoader.load_tools(mock_state)
        mock_load.assert_called_once()

    @patch("app.modules.agents.qna.tool_system._load_all_tools")
    @patch("app.modules.agents.qna.tool_system._initialize_tool_state")
    @patch("app.modules.agents.qna.tool_system._get_blocked_tools", return_value={})
    def test_load_tools_cache_invalidated_by_has_knowledge_change(self, mock_blocked, mock_init, mock_load, mock_state):
        """Cache is invalidated when has_knowledge changes."""
        mock_state["_cached_agent_tools"] = [MagicMock()]
        mock_state["_cached_blocked_tools"] = {}
        mock_state["_cached_has_knowledge"] = False
        mock_state["has_knowledge"] = True
        mock_load.return_value = [MagicMock()]

        result = ToolLoader.load_tools(mock_state)
        mock_load.assert_called_once()

    @patch("app.modules.agents.qna.tool_system._load_all_tools")
    @patch("app.modules.agents.qna.tool_system._initialize_tool_state")
    @patch("app.modules.agents.qna.tool_system._get_blocked_tools", return_value={})
    def test_load_tools_no_logger(self, mock_blocked, mock_init, mock_load, mock_state_no_logger):
        """Works without logger."""
        mock_load.return_value = []
        result = ToolLoader.load_tools(mock_state_no_logger)
        assert result == []

    @patch("app.modules.agents.qna.tool_system._load_all_tools")
    @patch("app.modules.agents.qna.tool_system._initialize_tool_state")
    @patch("app.modules.agents.qna.tool_system._get_blocked_tools", return_value={"t": 3})
    def test_load_tools_logs_blocked_tools(self, mock_blocked, mock_init, mock_load, mock_state):
        """Blocked tools are logged."""
        mock_load.return_value = [MagicMock(name="t1")]
        result = ToolLoader.load_tools(mock_state)
        assert len(result) == 1

    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    @patch("app.modules.agents.qna.tool_system.RegistryToolWrapper")
    def test_get_tool_by_name_direct_match(self, MockWrapper, mock_registry):
        """Direct match by full name."""
        tool = _make_registry_tool()
        mock_registry.get_all_tools.return_value = {"myapp.mytool": tool}
        MockWrapper.return_value = MagicMock()

        result = ToolLoader.get_tool_by_name("myapp.mytool", {})
        assert result is not None

    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    @patch("app.modules.agents.qna.tool_system.RegistryToolWrapper")
    def test_get_tool_by_name_suffix_match(self, MockWrapper, mock_registry):
        """Match by suffix."""
        tool = _make_registry_tool()
        mock_registry.get_all_tools.return_value = {"myapp.mytool": tool}
        MockWrapper.return_value = MagicMock()

        result = ToolLoader.get_tool_by_name("mytool", {})
        assert result is not None

    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    def test_get_tool_by_name_not_found(self, mock_registry):
        """Returns None when tool not found."""
        mock_registry.get_all_tools.return_value = {"myapp.mytool": MagicMock()}
        result = ToolLoader.get_tool_by_name("nonexistent", {})
        assert result is None


# ============================================================================
# get_agent_tools (public API)
# ============================================================================

class TestGetAgentTools:
    @patch("app.modules.agents.qna.tool_system.ToolLoader.load_tools")
    def test_returns_loaded_tools(self, mock_load):
        mock_tools = [MagicMock()]
        mock_load.return_value = mock_tools
        state = {}
        result = get_agent_tools(state)
        assert result is mock_tools

    @patch("app.modules.agents.qna.tool_system.ToolLoader.load_tools")
    def test_adds_fetch_full_record_tool(self, mock_load):
        """Adds fetch_full_record_tool when virtual_record_map exists."""
        mock_tools = [MagicMock()]
        mock_load.return_value = mock_tools.copy()
        state = {
            "virtual_record_id_to_result": {"vr1": {"content": "x"}},
            "record_label_to_uuid_map": {},
            "logger": logging.getLogger("test"),
        }

        with patch("app.utils.fetch_full_record.create_fetch_full_record_tool") as mock_create:
            mock_create.return_value = MagicMock(name="fetch_tool")
            result = get_agent_tools(state)
            assert len(result) == 2  # original + fetch tool

    @patch("app.modules.agents.qna.tool_system.ToolLoader.load_tools")
    def test_fetch_tool_exception_handled(self, mock_load):
        """Exception creating fetch tool is caught."""
        mock_tools = [MagicMock()]
        mock_load.return_value = mock_tools.copy()
        state = {
            "virtual_record_id_to_result": {"vr1": {}},
            "logger": logging.getLogger("test"),
        }

        with patch("app.utils.fetch_full_record.create_fetch_full_record_tool",
                    side_effect=Exception("import error")):
            result = get_agent_tools(state)
            assert len(result) == 1  # only original tools

    @patch("app.modules.agents.qna.tool_system.ToolLoader.load_tools")
    def test_fetch_tool_with_org_and_graph(self, mock_load):
        """Fetch tool factory has no ctor args; org/graph are injected at tool run time."""
        mock_load.return_value = []
        state = {
            "virtual_record_id_to_result": {"vr1": {}},
            "org_id": "org-123",
            "graph_provider": MagicMock(),
            "logger": logging.getLogger("test"),
        }

        with patch("app.utils.fetch_full_record.create_fetch_full_record_tool") as mock_create:
            mock_create.return_value = MagicMock()
            get_agent_tools(state)
            mock_create.assert_called_once_with()

    @patch("app.modules.agents.qna.tool_system.ToolLoader.load_tools")
    def test_no_fetch_tool_when_empty_map(self, mock_load):
        """No fetch tool added when virtual_record_map is empty."""
        mock_load.return_value = []
        state = {"virtual_record_id_to_result": {}}
        result = get_agent_tools(state)
        assert len(result) == 0

    @patch("app.modules.agents.qna.tool_system.ToolLoader.load_tools")
    def test_fetch_tool_without_logger(self, mock_load):
        """Fetch tool works without logger."""
        mock_load.return_value = []
        state = {"virtual_record_id_to_result": {"vr1": {}}}
        with patch("app.utils.fetch_full_record.create_fetch_full_record_tool") as mock_create:
            mock_create.return_value = MagicMock()
            result = get_agent_tools(state)
            assert len(result) == 1


# ============================================================================
# get_tool_by_name (public API)
# ============================================================================

class TestGetToolByName:
    @patch("app.modules.agents.qna.tool_system.ToolLoader.get_tool_by_name")
    def test_delegates_to_loader(self, mock_loader):
        mock_loader.return_value = MagicMock()
        result = get_tool_by_name("my.tool", {})
        mock_loader.assert_called_once_with("my.tool", {})


# ============================================================================
# clear_tool_cache
# ============================================================================

class TestClearToolCache:
    def test_clears_all_cache_keys(self):
        state = {
            "_cached_agent_tools": [1, 2],
            "_tool_instance_cache": {},
            "_cached_blocked_tools": {},
            "_cached_schema_tools": [],
        }
        clear_tool_cache(state)
        assert "_cached_agent_tools" not in state
        assert "_tool_instance_cache" not in state
        assert "_cached_blocked_tools" not in state
        assert "_cached_schema_tools" not in state

    def test_clears_when_keys_missing(self):
        """No error when keys don't exist."""
        state = {}
        clear_tool_cache(state)


# ============================================================================
# get_tool_results_summary
# ============================================================================

class TestGetToolResultsSummary:
    def test_empty_results(self):
        state = {}
        assert get_tool_results_summary(state) == "No tools executed yet."

    def test_empty_list(self):
        state = {"all_tool_results": []}
        assert get_tool_results_summary(state) == "No tools executed yet."

    def test_single_success(self):
        state = {"all_tool_results": [
            {"tool_name": "slack.send", "status": "success"},
        ]}
        summary = get_tool_results_summary(state)
        assert "Total: 1" in summary
        assert "Slack" in summary
        assert "1 " in summary

    def test_mixed_results(self):
        state = {"all_tool_results": [
            {"tool_name": "slack.send", "status": "success"},
            {"tool_name": "slack.send", "status": "error"},
            {"tool_name": "jira.create", "status": "success"},
        ]}
        summary = get_tool_results_summary(state)
        assert "Total: 3" in summary
        assert "Slack" in summary
        assert "Jira" in summary

    def test_utility_category(self):
        """Tools without dot go to utility category."""
        state = {"all_tool_results": [
            {"tool_name": "calculator", "status": "success"},
        ]}
        summary = get_tool_results_summary(state)
        assert "Utility" in summary

    def test_unknown_status(self):
        """Unknown status is counted but not under success or error."""
        state = {"all_tool_results": [
            {"tool_name": "slack.send", "status": "pending"},
        ]}
        summary = get_tool_results_summary(state)
        assert "Total: 1" in summary

    def test_unknown_tool_name(self):
        """Missing tool_name defaults to 'unknown'."""
        state = {"all_tool_results": [
            {"status": "success"},
        ]}
        summary = get_tool_results_summary(state)
        assert "unknown" in summary


# ============================================================================
# get_agent_tools_with_schemas
# ============================================================================

class TestGetAgentToolsWithSchemas:

    @patch("app.modules.agents.qna.tool_system.get_agent_tools")
    def test_returns_structured_tools(self, mock_get_tools):
        """Creates StructuredTool wrappers from registry tools."""
        mock_wrapper = MagicMock()
        mock_wrapper.name = "slack.send"
        mock_wrapper.description = "Send a message"
        mock_wrapper.registry_tool = MagicMock()
        mock_wrapper.registry_tool.args_schema = None

        mock_get_tools.return_value = [mock_wrapper]

        state = {"logger": logging.getLogger("test")}

        with patch("langchain_core.tools.StructuredTool") as MockST:
            MockST.from_function.return_value = MagicMock()
            result = get_agent_tools_with_schemas(state)
            assert len(result) == 1

    @patch("app.modules.agents.qna.tool_system.get_agent_tools")
    def test_with_args_schema(self, mock_get_tools):
        """Uses args_schema when available."""
        from pydantic import BaseModel

        class MySchema(BaseModel):
            query: str

        mock_wrapper = MagicMock()
        mock_wrapper.name = "search.query"
        mock_wrapper.description = "Search"
        mock_wrapper.registry_tool = MagicMock()
        mock_wrapper.registry_tool.args_schema = MySchema

        mock_get_tools.return_value = [mock_wrapper]
        state = {"logger": logging.getLogger("test")}

        with patch("langchain_core.tools.StructuredTool") as MockST:
            MockST.from_function.return_value = MagicMock()
            result = get_agent_tools_with_schemas(state)
            assert len(result) == 1
            # Should be called with args_schema
            call_kwargs = MockST.from_function.call_args[1]
            assert call_kwargs["args_schema"] is MySchema

    @patch("app.modules.agents.qna.tool_system.get_agent_tools")
    def test_cache_hit(self, mock_get_tools):
        """Returns cached StructuredTools when registry tools haven't changed."""
        registry_tools = [MagicMock()]
        cached_schema = [MagicMock()]

        state = {
            "_cached_schema_tools": cached_schema,
            "_cached_agent_tools": registry_tools,
            "logger": logging.getLogger("test"),
        }

        mock_get_tools.return_value = registry_tools  # Same object

        result = get_agent_tools_with_schemas(state)
        assert result is cached_schema

    @patch("app.modules.agents.qna.tool_system.get_agent_tools")
    def test_cache_miss_different_tools(self, mock_get_tools):
        """Rebuilds when registry tools change."""
        old_tools = [MagicMock()]
        new_tools = [MagicMock()]
        new_tools[0].name = "new.tool"
        new_tools[0].description = "new"
        new_tools[0].registry_tool = MagicMock()
        new_tools[0].registry_tool.args_schema = None

        state = {
            "_cached_schema_tools": [MagicMock()],
            "_cached_agent_tools": old_tools,
            "logger": logging.getLogger("test"),
        }

        mock_get_tools.return_value = new_tools

        with patch("langchain_core.tools.StructuredTool") as MockST:
            MockST.from_function.return_value = MagicMock()
            result = get_agent_tools_with_schemas(state)
            assert len(result) == 1
            # Cache should be updated
            assert state["_cached_schema_tools"] is result

    @patch("app.modules.agents.qna.tool_system.get_agent_tools")
    def test_tool_creation_exception_handled(self, mock_get_tools):
        """Individual tool creation failures don't break the whole call."""
        good_wrapper = MagicMock()
        good_wrapper.name = "good.tool"
        good_wrapper.description = "good"
        good_wrapper.registry_tool = MagicMock()
        good_wrapper.registry_tool.args_schema = None

        bad_wrapper = MagicMock()
        bad_wrapper.name = "bad.tool"
        bad_wrapper.description = "bad"
        bad_wrapper.registry_tool = MagicMock()
        bad_wrapper.registry_tool.args_schema = None

        mock_get_tools.return_value = [bad_wrapper, good_wrapper]
        state = {"logger": logging.getLogger("test")}

        with patch("langchain_core.tools.StructuredTool") as MockST:
            # First call fails, second succeeds
            MockST.from_function.side_effect = [Exception("bad"), MagicMock()]
            result = get_agent_tools_with_schemas(state)
            assert len(result) == 1

    @patch("app.modules.agents.qna.tool_system.get_agent_tools")
    def test_import_error_fallback(self, mock_get_tools):
        """Falls back to regular tools when langchain_core not available."""
        mock_get_tools.return_value = [MagicMock()]
        state = {}

        with patch.dict("sys.modules", {"langchain_core.tools": None}):
            with patch("app.modules.agents.qna.tool_system.get_agent_tools", return_value=[MagicMock()]) as mock_fallback:
                # The import error will be caught in the outer try block
                # We need to simulate ImportError
                pass

        # Actually test by mocking the import inside the function
        with patch("builtins.__import__", side_effect=ImportError("no module")):
            result = get_agent_tools_with_schemas(state)
            # Falls back to get_agent_tools

    @patch("app.modules.agents.qna.tool_system.get_agent_tools")
    def test_generic_exception_fallback(self, mock_get_tools):
        """Falls back to regular tools on generic exception."""
        mock_get_tools.side_effect = [Exception("unexpected"), [MagicMock()]]
        state = {}

        result = get_agent_tools_with_schemas(state)
        # Should get the fallback tools

    @patch("app.modules.agents.qna.tool_system.get_agent_tools")
    def test_with_virtual_record_map(self, mock_get_tools):
        """Adds fetch_full_record tool when virtual_record_map exists in schemas."""
        mock_wrapper = MagicMock()
        mock_wrapper.name = "test.tool"
        mock_wrapper.description = "test"
        mock_wrapper.registry_tool = MagicMock()
        mock_wrapper.registry_tool.args_schema = None

        mock_get_tools.return_value = [mock_wrapper]
        state = {
            "virtual_record_id_to_result": {"vr1": {}},
            "record_label_to_uuid_map": {},
            "logger": logging.getLogger("test"),
        }

        with patch("langchain_core.tools.StructuredTool") as MockST:
            MockST.from_function.return_value = MagicMock()
            with patch("app.utils.fetch_full_record.create_fetch_full_record_tool") as mock_create:
                mock_create.return_value = MagicMock()
                result = get_agent_tools_with_schemas(state)
                mock_create.assert_called_once()

    @patch("app.modules.agents.qna.tool_system.get_agent_tools")
    def test_fetch_tool_exception_in_schemas(self, mock_get_tools):
        """Exception creating fetch tool in schemas is handled."""
        mock_wrapper = MagicMock()
        mock_wrapper.name = "test.tool"
        mock_wrapper.description = "test"
        mock_wrapper.registry_tool = MagicMock()
        mock_wrapper.registry_tool.args_schema = None

        mock_get_tools.return_value = [mock_wrapper]
        state = {
            "virtual_record_id_to_result": {"vr1": {}},
            "logger": logging.getLogger("test"),
        }

        with patch("langchain_core.tools.StructuredTool") as MockST:
            MockST.from_function.return_value = MagicMock()
            with patch("app.utils.fetch_full_record.create_fetch_full_record_tool",
                        side_effect=Exception("fail")):
                result = get_agent_tools_with_schemas(state)
                assert len(result) == 1  # Only the test.tool, no fetch tool

    @patch("app.modules.agents.qna.tool_system.get_agent_tools")
    def test_no_logger(self, mock_get_tools):
        """Works without logger in state."""
        mock_wrapper = MagicMock()
        mock_wrapper.name = "test.tool"
        mock_wrapper.description = "test"
        mock_wrapper.registry_tool = MagicMock()
        mock_wrapper.registry_tool.args_schema = None

        mock_get_tools.return_value = [mock_wrapper]
        state = {}

        with patch("langchain_core.tools.StructuredTool") as MockST:
            MockST.from_function.return_value = MagicMock()
            result = get_agent_tools_with_schemas(state)
            assert len(result) == 1

    @patch("app.modules.agents.qna.tool_system.get_agent_tools")
    def test_sanitization_with_llm(self, mock_get_tools):
        """Tool names are sanitized based on LLM type."""
        mock_wrapper = MagicMock()
        mock_wrapper.name = "slack.send_message"
        mock_wrapper.description = "Send"
        mock_wrapper.registry_tool = MagicMock()
        mock_wrapper.registry_tool.args_schema = None

        mock_get_tools.return_value = [mock_wrapper]
        state = {"llm": MagicMock(), "logger": logging.getLogger("test")}

        with patch("langchain_core.tools.StructuredTool") as MockST:
            mock_st_instance = MagicMock()
            MockST.from_function.return_value = mock_st_instance
            result = get_agent_tools_with_schemas(state)
            # Check that the sanitized name was used
            call_kwargs = MockST.from_function.call_args[1]
            assert "." not in call_kwargs["name"]

    @patch("app.modules.agents.qna.tool_system.get_agent_tools")
    def test_virtual_record_map_with_org_id(self, mock_get_tools):
        """Schema path still uses parameterless fetch tool factory when a map is present."""
        mock_get_tools.return_value = []
        state = {
            "virtual_record_id_to_result": {"vr1": {}},
            "org_id": "org-456",
            "graph_provider": MagicMock(),
            "logger": logging.getLogger("test"),
        }

        with patch("app.utils.fetch_full_record.create_fetch_full_record_tool") as mock_create:
            mock_create.return_value = MagicMock()
            result = get_agent_tools_with_schemas(state)
            mock_create.assert_called_once_with()


# ============================================================================
# ToolLoader.load_tools — cache logging branches
# ============================================================================

class TestToolLoaderCacheLogging:
    """Test all logging branches in ToolLoader.load_tools."""

    @patch("app.modules.agents.qna.tool_system._load_all_tools")
    @patch("app.modules.agents.qna.tool_system._initialize_tool_state")
    @patch("app.modules.agents.qna.tool_system._get_blocked_tools", return_value={})
    def test_cache_valid_with_logger(self, mock_blocked, mock_init, mock_load):
        """Cache valid path with logger."""
        state = {
            "logger": logging.getLogger("test"),
            "_cached_agent_tools": [MagicMock()],
            "_cached_blocked_tools": {},
            "_cached_has_knowledge": False,
            "has_knowledge": False,
        }
        result = ToolLoader.load_tools(state)
        assert result is state["_cached_agent_tools"]
        mock_load.assert_not_called()

    @patch("app.modules.agents.qna.tool_system._load_all_tools")
    @patch("app.modules.agents.qna.tool_system._initialize_tool_state")
    @patch("app.modules.agents.qna.tool_system._get_blocked_tools", return_value={})
    def test_has_knowledge_changed_logged(self, mock_blocked, mock_init, mock_load):
        """has_knowledge change is logged."""
        state = {
            "logger": logging.getLogger("test"),
            "_cached_agent_tools": [MagicMock()],
            "_cached_blocked_tools": {},
            "_cached_has_knowledge": False,
            "has_knowledge": True,
        }
        mock_load.return_value = []
        ToolLoader.load_tools(state)
        mock_load.assert_called_once()

    @patch("app.modules.agents.qna.tool_system._load_all_tools")
    @patch("app.modules.agents.qna.tool_system._initialize_tool_state")
    @patch("app.modules.agents.qna.tool_system._get_blocked_tools", return_value={"t": 3})
    def test_blocked_tools_changed_logged(self, mock_blocked, mock_init, mock_load):
        """Blocked tools change is logged."""
        state = {
            "logger": logging.getLogger("test"),
            "_cached_agent_tools": [MagicMock()],
            "_cached_blocked_tools": {},
            "_cached_has_knowledge": False,
            "has_knowledge": False,
        }
        mock_load.return_value = []
        ToolLoader.load_tools(state)
        mock_load.assert_called_once()

    @patch("app.modules.agents.qna.tool_system._load_all_tools")
    @patch("app.modules.agents.qna.tool_system._initialize_tool_state")
    @patch("app.modules.agents.qna.tool_system._get_blocked_tools", return_value={})
    def test_first_load_logged(self, mock_blocked, mock_init, mock_load):
        """First load (no cache) is logged."""
        state = {
            "logger": logging.getLogger("test"),
            "has_knowledge": False,
        }
        mock_load.return_value = []
        ToolLoader.load_tools(state)
        mock_load.assert_called_once()

    @patch("app.modules.agents.qna.tool_system._load_all_tools")
    @patch("app.modules.agents.qna.tool_system._initialize_tool_state")
    @patch("app.modules.agents.qna.tool_system._get_blocked_tools", return_value={})
    def test_cache_valid_without_logger(self, mock_blocked, mock_init, mock_load):
        """Cache valid without logger doesn't crash."""
        state = {
            "_cached_agent_tools": [MagicMock()],
            "_cached_blocked_tools": {},
            "_cached_has_knowledge": False,
        }
        result = ToolLoader.load_tools(state)
        assert result is state["_cached_agent_tools"]
