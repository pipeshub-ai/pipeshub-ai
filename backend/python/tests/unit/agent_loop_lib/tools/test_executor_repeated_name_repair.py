"""`ToolExecutor._run`'s repair for a hallucinated tool name that's just a
valid registered name repeated back-to-back (`tools/executor.py`,
`_collapse_repeated_name`): some models/gateways degenerate into echoing an
already-bad name doubled (then quadrupled, ...) once it enters conversation
history — see `app/agents/agent_loop/converters.py::_clamp_tool_call_name`
for the companion defense that stops such a name from growing past a
provider's length limit and crashing the whole request. Collapsing to the
valid base name here means the call succeeds outright instead of bouncing
back another "Unknown tool" error."""

from __future__ import annotations

from app.agent_loop_lib.core.types import ToolCall
from app.agent_loop_lib.tools.base import Tool, ToolOutput
from app.agent_loop_lib.tools.executor import UNKNOWN_TOOL_ERROR_PREFIX, ToolExecutor
from app.agent_loop_lib.tools.registry import ToolRegistry


class _EchoTool(Tool):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def short_description(self) -> str:
        return "Echo"

    @property
    def description(self) -> str:
        return "Echoes back its input"

    @property
    def path(self) -> str:
        return f"/toolsets/test/{self._name}"

    @property
    def parameters(self) -> list:
        return []

    async def execute(self, **kwargs: object) -> ToolOutput:
        return ToolOutput(success=True, data="ok")


def _registry_with(*tools: Tool) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in tools:
        registry.register_tool(tool)
    return registry


class TestRepeatedNameRepair:
    async def test_doubled_valid_name_resolves_to_base_tool(self) -> None:
        executor = ToolExecutor(_registry_with(_EchoTool("knowledgegraph__search")))
        call = ToolCall(
            id="c1",
            name="knowledgegraph__searchknowledgegraph__search",
            arguments={},
        )

        result = await executor.call_tool(call)

        assert result.is_error is False
        assert result.name == "knowledgegraph__searchknowledgegraph__search"

    async def test_many_times_repeated_valid_name_also_resolves(self) -> None:
        executor = ToolExecutor(_registry_with(_EchoTool("search")))
        call = ToolCall(id="c1", name="search" * 5, arguments={})

        result = await executor.call_tool(call)

        assert result.is_error is False

    async def test_repeated_but_unregistered_base_still_errors(self) -> None:
        executor = ToolExecutor(_registry_with(_EchoTool("search")))
        call = ToolCall(id="c1", name="unknown" * 3, arguments={})

        result = await executor.call_tool(call)

        assert result.is_error is True
        assert UNKNOWN_TOOL_ERROR_PREFIX in result.content

    async def test_non_repeated_unknown_name_still_errors_as_before(self) -> None:
        executor = ToolExecutor(_registry_with(_EchoTool("search")))
        call = ToolCall(id="c1", name="totally_made_up_tool", arguments={})

        result = await executor.call_tool(call)

        assert result.is_error is True
        assert UNKNOWN_TOOL_ERROR_PREFIX in result.content
