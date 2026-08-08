"""`ToolExecutor.call_tool()`'s `PreDecision.ASK` handling
(`app/agent_loop_lib/tools/executor.py`).

Regression coverage for the fix-ask-decision todo: ASK must no longer be a
silent alias for DENY. When a caller wires an `on_ask` callback (the HIL
checkpoint/suspend seam — see `agent/observability.py::handle_tool_approval`),
an ASK decision defers to that callback's approve/deny outcome instead of
being collapsed into an automatic denial. Without `on_ask` wired at all, ASK
still degrades to the same "not approved" outcome as before, since there is
no one to ask.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agent_loop_lib.core.types import ToolCall
from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.hooks.middleware.context import ToolCallContext
from app.agent_loop_lib.hooks.registry import HookRegistry
from app.agent_loop_lib.tools.base import ParameterType, Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.executor import ToolExecutor
from app.agent_loop_lib.tools.registry import ToolRegistry


class _EchoTool(Tool):
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


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_tool(_EchoTool())
    return registry


def _ask_everything_kernel(reason: str = "needs human approval") -> HookRegistry:
    kernel = HookRegistry()

    async def _ask_mw(ctx: ToolCallContext, next_fn) -> None:
        ctx.ask(reason)
        await next_fn()

    kernel.on(HookEvent.PRE_TOOL_USE).use(_ask_mw)
    return kernel


class TestAskDecision:
    @pytest.mark.asyncio
    async def test_ask_without_on_ask_callback_degrades_to_denial(self) -> None:
        """No HIL wiring at all -> same outcome as today's collapse-to-DENY."""
        executor = ToolExecutor(_registry(), _ask_everything_kernel())
        call = ToolCall(id="c1", name="echo", arguments={"text": "hi"})

        result = await executor.call_tool(call)

        assert result.is_error is True
        assert "not approved" in str(result.content) or "needs human approval" in str(result.content)

    @pytest.mark.asyncio
    async def test_ask_approved_by_on_ask_executes_the_tool(self) -> None:
        executor = ToolExecutor(_registry(), _ask_everything_kernel())
        call = ToolCall(id="c1", name="echo", arguments={"text": "hi"})
        asked: list[tuple[ToolCall, str]] = []

        async def _on_ask(asked_call: ToolCall, reason: str) -> bool:
            asked.append((asked_call, reason))
            return True

        result = await executor.call_tool(call, on_ask=_on_ask)

        assert result.is_error is False
        assert result.content == "echo: hi"
        assert asked == [(call, "needs human approval")]

    @pytest.mark.asyncio
    async def test_ask_denied_by_on_ask_does_not_execute_the_tool(self) -> None:
        executor = ToolExecutor(_registry(), _ask_everything_kernel())
        call = ToolCall(id="c1", name="echo", arguments={"text": "hi"})

        async def _on_ask(asked_call: ToolCall, reason: str) -> bool:
            return False

        denied_reasons: list[str] = []

        async def _on_denied(reason: str) -> None:
            denied_reasons.append(reason)

        result = await executor.call_tool(call, on_ask=_on_ask, on_denied=_on_denied)

        assert result.is_error is True
        assert denied_reasons == ["needs human approval"]

    @pytest.mark.asyncio
    async def test_plain_allow_never_calls_on_ask(self) -> None:
        executor = ToolExecutor(_registry(), HookRegistry())
        call = ToolCall(id="c1", name="echo", arguments={"text": "hi"})
        calls = []

        async def _on_ask(asked_call: ToolCall, reason: str) -> bool:
            calls.append(1)
            return True

        result = await executor.call_tool(call, on_ask=_on_ask)

        assert result.is_error is False
        assert calls == []

    @pytest.mark.asyncio
    async def test_plain_deny_never_calls_on_ask(self) -> None:
        kernel = HookRegistry()

        async def _deny_mw(ctx: ToolCallContext, next_fn) -> None:
            ctx.deny("nope")
            await next_fn()

        kernel.on(HookEvent.PRE_TOOL_USE).use(_deny_mw)
        executor = ToolExecutor(_registry(), kernel)
        call = ToolCall(id="c1", name="echo", arguments={"text": "hi"})
        calls = []

        async def _on_ask(asked_call: ToolCall, reason: str) -> bool:
            calls.append(1)
            return True

        result = await executor.call_tool(call, on_ask=_on_ask)

        assert result.is_error is True
        assert result.content == "nope"
        assert calls == []
