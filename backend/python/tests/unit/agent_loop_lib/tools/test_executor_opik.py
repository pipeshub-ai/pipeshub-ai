"""`ToolExecutor.call_tool()` Opik tool-span wiring
(`app/agent_loop_lib/tools/executor.py`).

Covers the plan's "1a. Tool execution spans" item: every tool call —
normal `Tool.execute()` dispatch AND the `override_execute` special-route
path (`spawn_agent`, `clarify`, ...) — gets one Opik `"tool"` span around
`ToolExecutor._run()`, between PRE_TOOL_USE and POST_TOOL_USE."""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from app.agent_loop_lib.core.types import ToolCall, ToolResult
from app.agent_loop_lib.tools.base import ParameterType, Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.executor import ToolExecutor
from app.agent_loop_lib.tools.registry import ToolRegistry


class _FakeSpan(SimpleNamespace):
    pass


@contextlib.contextmanager
def _fake_span_cm(**_kwargs: Any):
    yield _FakeSpan()


class _EchoTool(Tool):
    """Trivial tool: succeeds and echoes its `text` argument."""

    @property
    def name(self) -> str:
        return "echo"

    @property
    def short_description(self) -> str:
        return "Echoes text"

    @property
    def description(self) -> str:
        return "Echoes the given text back"

    @property
    def path(self) -> str:
        return "/toolsets/test/echo"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [ToolParameter(name="text", type=ParameterType.STRING, description="text to echo")]

    async def execute(self, **kwargs: Any) -> ToolOutput:
        return ToolOutput(success=True, data=f"echo: {kwargs['text']}")


class _FailingTool(_EchoTool):
    @property
    def name(self) -> str:
        return "failing"

    @property
    def path(self) -> str:
        return "/toolsets/test/failing"

    async def execute(self, **kwargs: Any) -> ToolOutput:
        raise RuntimeError("tool blew up")


def _registry_with(*tools: Tool) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in tools:
        registry.register_tool(tool)
    return registry


class TestCallToolOpikSpan:
    async def test_creates_tool_span_with_arguments_and_result(self) -> None:
        executor = ToolExecutor(_registry_with(_EchoTool()), opik_enabled=True, opik_project_name="proj")
        call = ToolCall(id="c1", name="echo", arguments={"text": "hi"})

        with patch("opik.start_as_current_span", return_value=_fake_span_cm()) as mock_span:
            result = await executor.call_tool(call)

        assert isinstance(result, ToolResult)
        assert result.content == "echo: hi"
        assert result.is_error is False

        _, kwargs = mock_span.call_args
        assert kwargs["name"] == "tool.echo"
        assert kwargs["type"] == "tool"
        assert kwargs["input"] == {"name": "echo", "arguments": {"text": "hi"}}
        assert kwargs["project_name"] == "proj"

    async def test_span_records_error_output_on_tool_failure(self) -> None:
        executor = ToolExecutor(_registry_with(_FailingTool()), opik_enabled=True)
        call = ToolCall(id="c1", name="failing", arguments={"text": "x"})

        captured: dict[str, Any] = {}

        @contextlib.contextmanager
        def _capture(**_kwargs: Any):
            span = _FakeSpan()
            yield span
            captured["span"] = span

        with patch("opik.start_as_current_span", side_effect=lambda **kw: _capture(**kw)):
            result = await executor.call_tool(call)

        assert result.is_error is True
        assert "tool blew up" in result.content
        assert captured["span"].output["success"] is False
        assert "tool blew up" in captured["span"].output["error"]

    async def test_no_opik_call_when_disabled(self) -> None:
        executor = ToolExecutor(_registry_with(_EchoTool()), opik_enabled=False)
        call = ToolCall(id="c1", name="echo", arguments={"text": "hi"})

        with patch("opik.start_as_current_span") as mock_span:
            result = await executor.call_tool(call)

        mock_span.assert_not_called()
        assert result.content == "echo: hi"

    async def test_override_execute_special_route_still_traced(self) -> None:
        """Special routes (`spawn_agent`, `clarify`, ...) bypass `Tool.execute()`
        via `override_execute`, but still flow through the SAME `call_tool()`
        choke point — so they must still get a tool span."""
        executor = ToolExecutor(ToolRegistry(), opik_enabled=True)
        call = ToolCall(id="c1", name="spawn_agent", arguments={"role": "researcher"})

        async def _override() -> ToolResult:
            return ToolResult(tool_call_id="c1", name="spawn_agent", content="spawned ok")

        with patch("opik.start_as_current_span", return_value=_fake_span_cm()) as mock_span:
            result = await executor.call_tool(call, override_execute=_override)

        assert result.content == "spawned ok"
        _, kwargs = mock_span.call_args
        assert kwargs["name"] == "tool.spawn_agent"

    async def test_unknown_tool_still_produces_error_result_and_span(self) -> None:
        executor = ToolExecutor(ToolRegistry(), opik_enabled=True)
        call = ToolCall(id="c1", name="does_not_exist", arguments={})

        with patch("opik.start_as_current_span", return_value=_fake_span_cm()):
            result = await executor.call_tool(call)

        assert result.is_error is True
        assert "Unknown tool" in result.content

    async def test_opik_failure_does_not_break_tool_execution(self) -> None:
        executor = ToolExecutor(_registry_with(_EchoTool()), opik_enabled=True)
        call = ToolCall(id="c1", name="echo", arguments={"text": "hi"})

        with patch("opik.start_as_current_span", side_effect=RuntimeError("opik down")):
            result = await executor.call_tool(call)

        assert result.content == "echo: hi"
        assert result.is_error is False

    async def test_denied_call_never_reaches_opik(self) -> None:
        """A PRE_TOOL_USE denial short-circuits before the tool span opens —
        denied calls were never actually executed, so tracing them as a
        tool span would be misleading."""
        from app.agent_loop_lib.hooks.events import HookEvent
        from app.agent_loop_lib.hooks.registry import HookRegistry

        kernel = HookRegistry()

        async def _deny(ctx: Any, _next: Any) -> None:
            ctx.deny("not allowed")

        kernel.on(HookEvent.PRE_TOOL_USE).use(_deny)

        executor = ToolExecutor(_registry_with(_EchoTool()), kernel, opik_enabled=True)
        call = ToolCall(id="c1", name="echo", arguments={"text": "hi"})

        with patch("opik.start_as_current_span") as mock_span:
            result = await executor.call_tool(call)

        mock_span.assert_not_called()
        assert result.is_error is True
        assert result.content == "not allowed"
