"""`PipesHubToolLoader` (`app/agents/agent_loop/tool_loader.py`) — adapter
selection (registry-backed vs. dynamic `StructuredTool`) and duplicate-name
tolerance when bulk-registering into an agent-loop `ToolRegistry`."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.tool_adapter import (
    PipesHubStructuredToolAdapter,
    PipesHubToolAdapter,
)
from app.agents.agent_loop.tool_loader import PipesHubToolLoader
from app.agents.tools.models import Tool as RegistryTool
from app.agents.tools.wrapper import RegistryToolWrapper


def _make_context() -> AgentContext:
    return AgentContext(
        org_id="org-1",
        user_id="user-1",
        user_email="u@example.com",
        logger=MagicMock(),
        retrieval_service=MagicMock(config_service=MagicMock()),
    )


class _NoArgs(BaseModel):
    pass


def _make_registry_backed_structured_tool(
    context: AgentContext, *, app_name: str = "calc", tool_name: str = "add",
) -> StructuredTool:
    def _fn(**kwargs: Any) -> str:
        return "ok"

    registry_tool = RegistryTool(
        app_name=app_name, tool_name=tool_name, description="Add numbers", function=_fn,
    )
    wrapper = RegistryToolWrapper(app_name, tool_name, registry_tool, context.tool_state)
    structured_tool = StructuredTool.from_function(
        name=f"{app_name}.{tool_name}", description="Add numbers",
        args_schema=_NoArgs, coroutine=wrapper.arun,
    )
    structured_tool._tool_wrapper = wrapper
    return structured_tool


def _make_dynamic_structured_tool(name: str = "web_search") -> StructuredTool:
    async def _coro(**kwargs: Any) -> str:
        return "search result"

    return StructuredTool.from_function(
        name=name, description="Search the web", args_schema=_NoArgs, coroutine=_coro,
    )


class TestBuildAdapter:
    def test_registry_backed_tool_uses_pipeshub_tool_adapter(self) -> None:
        context = _make_context()
        structured_tool = _make_registry_backed_structured_tool(context)
        adapter = PipesHubToolLoader._build_adapter(structured_tool, context)
        assert isinstance(adapter, PipesHubToolAdapter)
        assert adapter.name == "calc_add"

    def test_dynamic_tool_uses_structured_tool_adapter(self) -> None:
        context = _make_context()
        structured_tool = _make_dynamic_structured_tool()
        adapter = PipesHubToolLoader._build_adapter(structured_tool, context)
        assert isinstance(adapter, PipesHubStructuredToolAdapter)
        assert adapter.name == "dynamic_web_search"

    def test_dynamic_tool_with_dotted_original_name(self) -> None:
        context = _make_context()
        structured_tool = _make_dynamic_structured_tool(name="fetch_slack_thread")
        structured_tool._original_name = "slack.fetch_slack_thread"
        adapter = PipesHubToolLoader._build_adapter(structured_tool, context)
        assert adapter.name == "slack_fetch_slack_thread"


class TestLoad:
    def test_empty_context_yields_empty_registry(self, monkeypatch) -> None:
        """`get_agent_tools_with_schemas` (not `PipesHubToolLoader` itself)
        owns tool-selection logic, including always-on internal tools
        sourced from the process-wide `_global_tools_registry` singleton —
        stubbing it here isolates `load()`'s OWN registration behavior from
        whatever action modules happen to already be imported (and thus
        registered) elsewhere in the test process."""
        context = _make_context()
        monkeypatch.setattr(
            "app.agents.agent_loop.tool_loader.get_agent_tools_with_schemas",
            lambda state: [],
        )
        registry = PipesHubToolLoader().load(context)
        assert registry.names() == []

    def test_load_skips_duplicate_tool_names(self, monkeypatch) -> None:
        context = _make_context()
        dup1 = _make_dynamic_structured_tool(name="web_search")
        dup2 = _make_dynamic_structured_tool(name="web_search")

        monkeypatch.setattr(
            "app.agents.agent_loop.tool_loader.get_agent_tools_with_schemas",
            lambda state: [dup1, dup2],
        )
        registry = PipesHubToolLoader().load(context)
        assert registry.names().count("dynamic_web_search") == 1

    def test_load_registers_multiple_distinct_tools(self, monkeypatch) -> None:
        context = _make_context()
        tool_a = _make_dynamic_structured_tool(name="web_search")
        tool_b = _make_dynamic_structured_tool(name="fetch_url")

        monkeypatch.setattr(
            "app.agents.agent_loop.tool_loader.get_agent_tools_with_schemas",
            lambda state: [tool_a, tool_b],
        )
        registry = PipesHubToolLoader().load(context)
        assert set(registry.names()) == {"dynamic_web_search", "dynamic_fetch_url"}


class TestLoadSkipApps:
    """`skip_apps` filters registry-backed tools by `wrapper.app_name` —
    used by `PipesHubAgentFactory.create()` to drop the legacy
    `coding_sandbox` tools once `agent_loop_lib`'s own `run_code` is
    registered, while always keeping `database_sandbox` (see
    `tool_loader.py`'s `load()` docstring)."""

    def test_default_skip_apps_keeps_every_tool(self, monkeypatch) -> None:
        context = _make_context()
        coding = _make_registry_backed_structured_tool(context, app_name="coding_sandbox", tool_name="execute_python")

        monkeypatch.setattr(
            "app.agents.agent_loop.tool_loader.get_agent_tools_with_schemas",
            lambda state: [coding],
        )
        registry = PipesHubToolLoader().load(context)
        assert "coding_sandbox_execute_python" in registry.names()

    def test_skip_apps_filters_only_named_app(self, monkeypatch) -> None:
        context = _make_context()
        coding = _make_registry_backed_structured_tool(context, app_name="coding_sandbox", tool_name="execute_python")
        database = _make_registry_backed_structured_tool(context, app_name="database_sandbox", tool_name="run_query")

        monkeypatch.setattr(
            "app.agents.agent_loop.tool_loader.get_agent_tools_with_schemas",
            lambda state: [coding, database],
        )
        registry = PipesHubToolLoader().load(context, skip_apps={"coding_sandbox"})

        assert "coding_sandbox_execute_python" not in registry.names()
        assert "database_sandbox_run_query" in registry.names()

    def test_skip_apps_does_not_affect_dynamic_tools_without_a_wrapper(self, monkeypatch) -> None:
        """Dynamic `StructuredTool`s (no `_tool_wrapper`) have no `app_name`
        to filter on — `skip_apps` must be a no-op for them regardless of
        the tool's synthetic `dynamic`/dotted-name bucket."""
        context = _make_context()
        dynamic = _make_dynamic_structured_tool(name="web_search")

        monkeypatch.setattr(
            "app.agents.agent_loop.tool_loader.get_agent_tools_with_schemas",
            lambda state: [dynamic],
        )
        registry = PipesHubToolLoader().load(context, skip_apps={"dynamic", "coding_sandbox"})
        assert "dynamic_web_search" in registry.names()
