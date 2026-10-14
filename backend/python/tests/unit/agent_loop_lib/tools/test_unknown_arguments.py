"""Unknown argument names on method and dynamic tools.

A model that invents an argument (``created_after`` on a listing tool that has
no date filter) used to get Python's bare ``TypeError`` back. It now gets a
validation error naming the argument plus the tool's usage, and the tool is not
run, so it cannot believe the invented filter was applied.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain_core.tools import StructuredTool

from app.agent_loop_lib.core.types import ToolCall, ToolResult
from app.agent_loop_lib.tools.base import ParameterType, Tool, ToolParameter
from app.agent_loop_lib.tools.decorators import BoundMethodTool, ToolMeta
from app.agent_loop_lib.tools.executor import ToolExecutor
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop.tool_adapter import PipesHubStructuredToolAdapter

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


class _Listing:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def list_files(self, query: str | None = None, limit: int = 20, attachment_ids: list[str] | None = None) -> tuple[bool, str]:
        self.calls.append({"query": query, "limit": limit, "attachment_ids": attachment_ids})
        return True, "listed"

    async def anything(self, **kwargs: object) -> tuple[bool, str]:
        self.calls.append(kwargs)
        return True, "ok"


def _method_tool(method: Callable[..., Awaitable[tuple[bool, str]]], name: str) -> BoundMethodTool:
    # attachment_ids is accepted by the method but not advertised, like the
    # arguments hooks inject.
    meta = ToolMeta(
        path=f"/toolsets/kb/{name}",
        short_description=name,
        description=name,
        parameters=[
            ToolParameter(name="query", type=ParameterType.STRING, required=False, description="Name to match."),
            ToolParameter(name="limit", type=ParameterType.INTEGER, required=False, default=20, description="Page size."),
        ],
    )
    return BoundMethodTool(method, meta)


async def _call(tool: Tool, arguments: dict[str, Any]) -> ToolResult:
    registry = ToolRegistry()
    registry.register_tool(tool)
    return await ToolExecutor(registry).call_tool(ToolCall(id="c1", name=tool.name, arguments=arguments))


class TestMethodTool:
    async def test_invented_argument_is_named_and_the_tool_not_run(self) -> None:
        listing = _Listing()

        result = await _call(_method_tool(listing.list_files, "list_files"), {"query": "scale", "created_after": "2026-01-25"})

        assert result.is_error is True
        assert "Unknown argument(s): created_after" in result.content
        assert "Correct usage of `kb__list_files`:" in result.content
        assert listing.calls == []

    async def test_accepted_arguments_run_including_unadvertised_ones(self) -> None:
        listing = _Listing()

        result = await _call(_method_tool(listing.list_files, "list_files"), {"query": "scale", "attachment_ids": ["r1"]})

        assert result.is_error is False
        assert listing.calls == [{"query": "scale", "limit": 20, "attachment_ids": ["r1"]}]

    async def test_a_method_taking_kwargs_accepts_any_name(self) -> None:
        listing = _Listing()

        result = await _call(_method_tool(listing.anything, "anything"), {"whatever": 1})

        assert result.is_error is False
        assert listing.calls == [{"whatever": 1}]


class TestDynamicTool:
    async def test_invented_argument_is_named_and_the_tool_not_run(self) -> None:
        calls: list[str] = []

        async def search(query: str) -> str:
            calls.append(query)
            return "found"

        adapter = PipesHubStructuredToolAdapter(
            StructuredTool.from_function(coroutine=search, name="search", description="Search."), "web", "search"
        )

        rejected = await _call(adapter, {"query": "q", "created_after": "2026-01-01"})
        accepted = await _call(adapter, {"query": "q"})

        assert rejected.is_error is True
        assert "Unknown argument(s): created_after" in rejected.content
        assert accepted.is_error is False
        assert calls == ["q"]
