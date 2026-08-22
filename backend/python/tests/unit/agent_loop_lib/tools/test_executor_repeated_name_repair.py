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
from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.hooks.middleware.context import ToolCallContext
from app.agent_loop_lib.hooks.registry import HookRegistry
from app.agent_loop_lib.tools.base import Tag, Tool, ToolOutput
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


class TestPreToolUseSeesResolvedTool:
    """PRE_TOOL_USE must authorize against the tool the call actually
    resolves to, not the raw hallucinated name — otherwise permission/
    risk-tag middleware (keyed off `ctx.tool_path`/`ctx.tags`) either
    misses a check that should apply to the real tool, or applies one
    meant for a different tool entirely. Regression coverage for
    `ToolExecutor._resolve_tool_name` running before `ToolCallContext`
    is built (see its docstring in `tools/executor.py`)."""

    def _capturing_kernel(self, seen: list[ToolCallContext]) -> HookRegistry:
        kernel = HookRegistry()

        async def _capture(ctx: ToolCallContext, next_fn) -> None:
            seen.append(ctx)
            await next_fn()

        kernel.on(HookEvent.PRE_TOOL_USE).use(_capture)
        return kernel

    async def test_doubled_name_authorizes_against_resolved_path_and_tags(self) -> None:
        registry = ToolRegistry()
        registry.register_tool(_EchoTool("knowledgegraph__search"), extra_tags=(Tag("risk", "high"),))
        seen: list[ToolCallContext] = []
        executor = ToolExecutor(registry, self._capturing_kernel(seen))
        call = ToolCall(
            id="c1",
            name="knowledgegraph__searchknowledgegraph__search",
            arguments={},
        )

        result = await executor.call_tool(call)

        assert result.is_error is False
        assert len(seen) == 1
        assert seen[0].tool_path == "/toolsets/test/knowledgegraph__search"
        assert Tag("risk", "high") in seen[0].tags

    async def test_toolset_group_name_authorizes_against_resolved_path_and_tags(self) -> None:
        registry = ToolRegistry()
        registry.register_tool(_EchoTool("list_files"), extra_tags=(Tag("risk", "high"),))
        registry.register_toolset("knowledgehub", "KnowledgeHub", ["list_files"])
        seen: list[ToolCallContext] = []
        executor = ToolExecutor(registry, self._capturing_kernel(seen))
        call = ToolCall(id="c1", name="knowledgehub", arguments={})

        result = await executor.call_tool(call)

        assert result.is_error is False
        assert len(seen) == 1
        assert seen[0].tool_path == "/toolsets/test/list_files"
        assert Tag("risk", "high") in seen[0].tags

    async def test_unresolvable_name_still_authorizes_against_unresolved_placeholder(self) -> None:
        """No change in behavior for a genuinely unknown name: PRE_TOOL_USE
        still sees the `/unresolved/<name>` placeholder with no tags, so a
        default-deny-on-unresolved policy keeps working."""
        registry = ToolRegistry()
        registry.register_tool(_EchoTool("search"))
        seen: list[ToolCallContext] = []
        executor = ToolExecutor(registry, self._capturing_kernel(seen))
        call = ToolCall(id="c1", name="totally_made_up_tool", arguments={})

        result = await executor.call_tool(call)

        assert result.is_error is True
        assert len(seen) == 1
        assert seen[0].tool_path == "/unresolved/totally_made_up_tool"
        assert seen[0].tags == ()
