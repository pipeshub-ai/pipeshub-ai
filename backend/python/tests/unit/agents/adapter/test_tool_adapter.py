"""`PipesHubToolAdapter`/`PipesHubStructuredToolAdapter`
(`app/agents/agent_loop/tool_adapter.py`) — registry tools and dynamic
LangChain `StructuredTool`s execute correctly through agent-loop's `Tool`
ABC, with success/failure verdicts matching the legacy extraction logic."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from app.agents.agent_loop.tool_adapter import (
    PipesHubStructuredToolAdapter,
    PipesHubToolAdapter,
    split_original_tool_name,
)
from app.agents.tools.enums import ParameterType as RegistryParameterType
from app.agents.tools.models import Tool as RegistryTool
from app.agents.tools.models import ToolParameter


def _plain_function(**kwargs: Any) -> str:
    """A plain top-level function so RegistryToolWrapper treats it as a
    standalone function, not a class method (`__qualname__` has no dot)."""
    if kwargs.get("boom"):
        raise RuntimeError("kaboom")
    return f"searched for {kwargs.get('query', '')}"


class _SearchArgs(BaseModel):
    query: str


def _make_registry_tool(**overrides: Any) -> RegistryTool:
    defaults: dict[str, Any] = {
        "app_name": "search",
        "tool_name": "run_search",
        "description": "Search things",
        "function": _plain_function,
        "parameters": [
            ToolParameter(name="query", type=RegistryParameterType.STRING, description="query text", required=True),
        ],
        "args_schema": _SearchArgs,
        "llm_description": "Use this to search for things.",
        "when_to_use": ["when the user asks to find something"],
        "when_not_to_use": ["when the user wants to create something"],
    }
    defaults.update(overrides)
    return RegistryTool(**defaults)


def _make_context() -> Any:
    from unittest.mock import MagicMock

    from app.agents.agent_loop.context import AgentContext

    return AgentContext(
        org_id="org-1",
        user_id="user-1",
        user_email="u@example.com",
        logger=MagicMock(),
        retrieval_service=MagicMock(config_service=MagicMock()),
    )


class TestPipesHubToolAdapter:
    def test_name_joins_app_and_tool(self) -> None:
        context = _make_context()
        adapter = PipesHubToolAdapter(_make_registry_tool(), "search", "run_search", lambda: context)
        assert adapter.name == "search_run_search"
        assert adapter.path == "/connectors/search/run_search"

    def test_description_includes_when_to_use_guidance(self) -> None:
        context = _make_context()
        adapter = PipesHubToolAdapter(_make_registry_tool(), "search", "run_search", lambda: context)
        assert "Use this to search for things." in adapter.description
        assert "When to use:" in adapter.description
        assert "When NOT to use:" in adapter.description

    def test_description_falls_back_to_short_description(self) -> None:
        context = _make_context()
        tool = _make_registry_tool(llm_description=None, when_to_use=[], when_not_to_use=[])
        adapter = PipesHubToolAdapter(tool, "search", "run_search", lambda: context)
        assert adapter.description == adapter.short_description

    def test_parameters_extracted_from_registry_tool(self) -> None:
        context = _make_context()
        adapter = PipesHubToolAdapter(_make_registry_tool(), "search", "run_search", lambda: context)
        params = adapter.parameters
        assert any(p.name == "query" for p in params)

    async def test_execute_success(self) -> None:
        context = _make_context()
        adapter = PipesHubToolAdapter(_make_registry_tool(), "search", "run_search", lambda: context)
        output = await adapter.execute(query="cats")
        assert output.success is True
        assert "cats" in output.data

    async def test_execute_failure_returns_error_output(self) -> None:
        context = _make_context()
        adapter = PipesHubToolAdapter(_make_registry_tool(), "search", "run_search", lambda: context)
        output = await adapter.execute(boom=True)
        assert output.success is False
        assert output.error

    def test_validate_is_permissive(self) -> None:
        context = _make_context()
        adapter = PipesHubToolAdapter(_make_registry_tool(), "search", "run_search", lambda: context)
        # Should not raise even with unexpected/loosely-typed kwargs.
        adapter.validate({"unexpected_key": 123, "query": 5})


class _EchoArgs(BaseModel):
    text: str = ""


async def _echo_coroutine(**kwargs: Any) -> str:
    if kwargs.get("fail"):
        raise ValueError("dynamic tool failure")
    return f"echo: {kwargs.get('text', '')}"


def _make_structured_tool() -> StructuredTool:
    return StructuredTool.from_function(
        name="web_search",
        description="Search the web",
        args_schema=_EchoArgs,
        coroutine=_echo_coroutine,
    )


class TestPipesHubStructuredToolAdapter:
    def test_name_and_path(self) -> None:
        adapter = PipesHubStructuredToolAdapter(_make_structured_tool(), "dynamic", "web_search")
        assert adapter.name == "dynamic_web_search"
        assert adapter.path == "/dynamic/dynamic/web_search"

    async def test_execute_success_calls_coroutine(self) -> None:
        adapter = PipesHubStructuredToolAdapter(_make_structured_tool(), "dynamic", "web_search")
        output = await adapter.execute(text="hello")
        assert output.success is True
        assert "hello" in output.data

    async def test_execute_failure_wraps_exception(self) -> None:
        adapter = PipesHubStructuredToolAdapter(_make_structured_tool(), "dynamic", "web_search")
        output = await adapter.execute(fail=True)
        assert output.success is False
        assert "dynamic tool failure" in output.error


class TestSplitOriginalToolName:
    def test_dotted_name_splits_into_app_and_tool(self) -> None:
        tool = _make_structured_tool()
        tool._original_name = "slack.fetch_slack_thread"
        app_name, tool_name = split_original_tool_name(tool)
        assert app_name == "slack"
        assert tool_name == "fetch_slack_thread"

    def test_no_dot_falls_back_to_dynamic_bucket(self) -> None:
        tool = _make_structured_tool()
        app_name, tool_name = split_original_tool_name(tool)
        assert app_name == "dynamic"
        assert tool_name == "web_search"
