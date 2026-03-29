"""
Extended tests for app/modules/agents/qna/tool_system.py to reach 85%+ coverage.
Covers: get_agent_tools_with_schemas, _load_all_tools edge cases, ToolLoader.load_tools
cache invalidation, get_tool_by_name suffix matching, tool limit logic.
"""

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def _make_registry_tool(**kwargs):
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


class TestGetAgentToolsWithSchemas:
    """Cover get_agent_tools_with_schemas including caching, schema conversion."""

    @patch("app.modules.agents.qna.tool_system.RegistryToolWrapper")
    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    def test_returns_structured_tools_with_schema(self, mock_registry, mock_wrapper):
        from app.modules.agents.qna.tool_system import get_agent_tools_with_schemas

        from pydantic import BaseModel, Field

        class MySchema(BaseModel):
            query: str = Field(description="Search query")

        mock_tool = _make_registry_tool(
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

        mock_tool = _make_registry_tool(
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

        mock_tool = _make_registry_tool(
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

        mock_tool = _make_registry_tool(
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
            "app.utils.agent_fetch_full_record.create_agent_fetch_full_record_tool"
        ) as mock_create_fetch:
            mock_fetch_tool = MagicMock()
            mock_create_fetch.return_value = mock_fetch_tool

            tools = get_agent_tools_with_schemas(state)
            assert mock_fetch_tool in tools

    @patch("app.modules.agents.qna.tool_system._global_tools_registry")
    def test_tool_creation_failure_skipped(self, mock_registry):
        """A tool that fails to create StructuredTool is skipped."""
        from app.modules.agents.qna.tool_system import get_agent_tools_with_schemas

        mock_tool = _make_registry_tool(
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
            tool = _make_registry_tool(
                app_name="calculator",
                metadata=SimpleNamespace(category="internal", is_internal=True),
                description=f"Internal Tool {i}",
                parameters=[],
            )
            tools[f"calculator.tool{i}"] = tool

        # More user tools than MAX_TOOLS_LIMIT
        for i in range(MAX_TOOLS_LIMIT + 10):
            tool = _make_registry_tool(
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

        internal_tool = _make_registry_tool(
            app_name="calculator",
            metadata=SimpleNamespace(category="internal", is_internal=True),
            description="Calc",
            parameters=[],
        )
        user_tool = _make_registry_tool(
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

        mock_tool = _make_registry_tool(
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

        retrieval_tool = _make_registry_tool(
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

        internal_tool = _make_registry_tool(
            app_name="calculator",
            metadata=SimpleNamespace(category="internal", is_internal=True),
            description="Calc",
            parameters=[],
        )
        external_tool = _make_registry_tool(
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

        mock_tool = _make_registry_tool(
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

        mock_tool = _make_registry_tool(
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

        mock_tool = _make_registry_tool(
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

        mock_tool = _make_registry_tool(description="test", parameters=[])
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
