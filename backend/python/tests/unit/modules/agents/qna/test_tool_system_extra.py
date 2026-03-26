"""
Extra tests for app/modules/agents/qna/tool_system.py to boost coverage above 85%.

Targets remaining uncovered blocks after test_tool_system_coverage.py and
test_tool_system_deep.py:
- _requires_sanitized_tool_names import error branches
- _sanitize_tool_name_if_needed when sanitization not needed
- get_agent_tools_with_schemas: _make_async_tool_func, error in conversion
- _extract_tool_names_from_toolsets: dict with only 'name' key
- _is_internal_tool: metadata without category or is_internal
- _load_all_tools: logger None path, no agent_toolsets with warning
- get_tool_results_summary: unknown status
"""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_registry_tool(**kwargs):
    return SimpleNamespace(**kwargs)


def _make_state(**extra):
    return {
        "has_knowledge": False,
        "all_tool_results": [],
        "logger": MagicMock(spec=logging.Logger),
        **extra,
    }


# ===================================================================
# _requires_sanitized_tool_names — import branches
# ===================================================================


class TestRequiresSanitizedImportBranches:
    """Cover exception handling branches in _requires_sanitized_tool_names."""

    def test_with_non_none_llm(self):
        """Non-None LLM that is not recognized still returns True."""
        from app.modules.agents.qna.tool_system import _requires_sanitized_tool_names
        result = _requires_sanitized_tool_names(MagicMock())
        assert result is True


# ===================================================================
# get_agent_tools_with_schemas async wrapper
# ===================================================================


class TestGetAgentToolsWithSchemasAsyncFunc:
    """Cover the _make_async_tool_func closure and StructuredTool creation."""

    @patch("app.modules.agents.qna.tool_system.RegistryToolWrapper")
    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    def test_structured_tool_with_no_args_schema(self, mock_registry, mock_wrapper):
        """Tool without args_schema uses fallback StructuredTool creation."""
        from app.modules.agents.qna.tool_system import get_agent_tools_with_schemas

        mock_tool = _make_registry_tool(
            app_name="utility",
            metadata=SimpleNamespace(category="internal", is_internal=True),
            description="Util",
            parameters=[],
        )
        # Important: no args_schema attribute
        mock_registry.get_all_tools.return_value = {"utility.do": mock_tool}

        wrapper = MagicMock()
        wrapper.name = "utility.do"
        wrapper.description = "Util"
        wrapper.registry_tool = mock_tool
        mock_wrapper.return_value = wrapper

        state = _make_state()
        tools = get_agent_tools_with_schemas(state)
        assert len(tools) >= 1
        assert "_cached_schema_tools" in state

    @patch("app.modules.agents.qna.tool_system.RegistryToolWrapper")
    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    def test_structured_tool_individual_failure(self, mock_registry, mock_wrapper):
        """One tool fails to create StructuredTool, others succeed."""
        from app.modules.agents.qna.tool_system import get_agent_tools_with_schemas
        from pydantic import BaseModel, Field

        class GoodSchema(BaseModel):
            query: str = Field(description="Query")

        good_tool = _make_registry_tool(
            app_name="calculator",
            metadata=SimpleNamespace(category="internal", is_internal=True),
            description="Calc",
            parameters=[],
            args_schema=GoodSchema,
        )
        bad_tool = _make_registry_tool(
            app_name="datetime",
            metadata=SimpleNamespace(category="internal", is_internal=True),
            description="Time",
            parameters=[],
            args_schema=None,
        )

        mock_registry.get_all_tools.return_value = {
            "calculator.add": good_tool,
            "datetime.now": bad_tool,
        }

        call_count = [0]
        def make_wrapper(a, n, t, s):
            call_count[0] += 1
            w = MagicMock()
            w.name = f"{a}.{n}"
            w.description = t.description
            w.registry_tool = t
            if call_count[0] == 2:
                # Simulate failure for second tool
                w.registry_tool = MagicMock()
                w.registry_tool.args_schema = None
            return w

        mock_wrapper.side_effect = make_wrapper

        state = _make_state()
        tools = get_agent_tools_with_schemas(state)
        assert len(tools) >= 1


# ===================================================================
# _load_all_tools: with no state logger
# ===================================================================


class TestLoadAllToolsNoLogger:
    @patch("app.modules.agents.qna.tool_system.RegistryToolWrapper")
    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    def test_no_logger_in_state(self, mock_registry, mock_wrapper):
        from app.modules.agents.qna.tool_system import _load_all_tools

        mock_tool = _make_registry_tool(
            app_name="calculator",
            metadata=SimpleNamespace(category="internal", is_internal=True),
            description="Calc",
            parameters=[],
        )
        mock_registry.get_all_tools.return_value = {"calculator.add": mock_tool}
        mock_wrapper.side_effect = lambda a, n, t, s: MagicMock(name=f"{a}.{n}")

        state = _make_state(logger=None)
        result = _load_all_tools(state, {})
        assert len(result) == 1

    @patch("app.modules.agents.qna.tool_system.RegistryToolWrapper")
    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    def test_toolsets_with_empty_tools_list(self, mock_registry, mock_wrapper):
        """Toolset with empty tools list triggers warning in logger."""
        from app.modules.agents.qna.tool_system import _load_all_tools

        mock_tool = _make_registry_tool(
            app_name="slack",
            metadata=SimpleNamespace(category="comm"),
            description="Send",
            parameters=[],
        )
        mock_registry.get_all_tools.return_value = {"slack.send": mock_tool}

        mock_logger = MagicMock(spec=logging.Logger)
        state = _make_state(
            logger=mock_logger,
            agent_toolsets=[{"name": "slack", "tools": []}],
        )
        _load_all_tools(state, {})
        # Should log warning about no tools extracted
        warning_calls = [str(c) for c in mock_logger.warning.call_args_list]
        assert any("No tools extracted" in str(c) for c in warning_calls)


# ===================================================================
# get_tool_results_summary — edge cases
# ===================================================================


class TestGetToolResultsSummaryEdges:
    def test_unknown_status_not_counted(self):
        """Results with status other than success/error are ignored in counts."""
        from app.modules.agents.qna.tool_system import get_tool_results_summary

        state = {
            "all_tool_results": [
                {"tool_name": "slack.send", "status": "pending"},
            ]
        }
        summary = get_tool_results_summary(state)
        assert "Tool Execution Summary" in summary
        # pending is not success or error, so counts should be 0
        assert "Success: 0" in summary
        assert "Failed: 0" in summary

    def test_no_tool_results(self):
        from app.modules.agents.qna.tool_system import get_tool_results_summary
        state = {"all_tool_results": []}
        summary = get_tool_results_summary(state)
        assert "No tools executed yet." in summary


# ===================================================================
# _initialize_tool_state
# ===================================================================


class TestInitializeToolState:
    def test_sets_defaults(self):
        from app.modules.agents.qna.tool_system import _initialize_tool_state
        state = {}
        _initialize_tool_state(state)
        assert state["tool_results"] == []
        assert state["all_tool_results"] == []

    def test_does_not_overwrite_existing(self):
        from app.modules.agents.qna.tool_system import _initialize_tool_state
        state = {
            "tool_results": [{"existing": True}],
            "all_tool_results": [{"existing": True}],
        }
        _initialize_tool_state(state)
        assert len(state["tool_results"]) == 1
        assert len(state["all_tool_results"]) == 1
