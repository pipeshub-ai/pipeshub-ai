from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from app.agent_loop_lib.core.types import ToolCall
from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.hooks.registry import HookRegistry
from app.agent_loop_lib.tools.base import ParameterType, Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.executor import ToolExecutor
from app.agent_loop_lib.tools.invocation import InvocationStart
from app.agent_loop_lib.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from app.agent_loop_lib.hooks.middleware.context import ToolResultContext
    from app.agent_loop_lib.hooks.middleware.pipeline import Next


class _CountingTool(Tool):
    calls = 0

    @property
    def name(self) -> str:
        return "count"

    @property
    def short_description(self) -> str:
        return "Counts executions"

    @property
    def description(self) -> str:
        return self.short_description

    @property
    def path(self) -> str:
        return "/toolsets/test/count"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [ToolParameter(name="value", type=ParameterType.STRING, description="value")]

    async def execute(self, **kwargs: object) -> ToolOutput:
        self.calls += 1
        return ToolOutput(success=True, data=kwargs["value"])


class _Journal:
    def __init__(self) -> None:
        self.result: dict[str, Any] | None = None
        self.interruptions = 0

    async def begin(self, **_kwargs: object) -> InvocationStart:
        return InvocationStart(invocation_id="invocation-1", cached_result=self.result)

    async def finish(
        self,
        invocation_id: str,
        *,
        result: dict[str, Any],
        success: bool,
    ) -> None:
        assert invocation_id == "invocation-1"
        assert success is True
        self.result = result

    async def interrupted(self, invocation_id: str) -> None:
        assert invocation_id == "invocation-1"
        self.interruptions += 1


def _scope() -> SimpleNamespace:
    identity = SimpleNamespace(run_id="run-1")
    return SimpleNamespace(
        tool_path="",
        tags=(),
        turn=SimpleNamespace(run=SimpleNamespace(identity=identity)),
    )


@pytest.mark.asyncio
async def test_journal_caches_only_fully_post_processed_result() -> None:
    tool = _CountingTool()
    registry = ToolRegistry()
    registry.register_tool(tool)
    journal = _Journal()
    kernel = HookRegistry()
    post_calls = 0

    async def post(ctx: ToolResultContext, next_fn: Next) -> None:
        nonlocal post_calls
        post_calls += 1
        ctx.tool_response = ToolOutput(success=True, data="post-processed")
        ctx.metadata["artifact_meta"] = {"artifact_id": "artifact-1"}
        await next_fn()

    kernel.on(HookEvent.POST_TOOL_USE).use(post)
    executor = ToolExecutor(registry, kernel, invocation_journal=journal)
    call = ToolCall(id="call-1", name="count", arguments={"value": "raw"})

    first = await executor.call_tool(call, scope=_scope())
    second = await executor.call_tool(call, scope=_scope())

    assert first == second
    assert first.content == "post-processed"
    assert first.artifact_meta is not None
    assert tool.calls == 1
    assert post_calls == 1
    assert journal.interruptions == 0


@pytest.mark.asyncio
async def test_post_hook_cancellation_marks_started_invocation_unknown() -> None:
    tool = _CountingTool()
    registry = ToolRegistry()
    registry.register_tool(tool)
    journal = _Journal()
    kernel = HookRegistry()

    async def fail_post(_ctx: ToolResultContext, _next_fn: Next) -> None:
        raise asyncio.CancelledError

    kernel.on(HookEvent.POST_TOOL_USE).use(fail_post)
    executor = ToolExecutor(registry, kernel, invocation_journal=journal)

    with pytest.raises(asyncio.CancelledError):
        await executor.call_tool(
            ToolCall(id="call-2", name="count", arguments={"value": "raw"}),
            scope=_scope(),
        )

    assert journal.result is None
    assert journal.interruptions == 1
