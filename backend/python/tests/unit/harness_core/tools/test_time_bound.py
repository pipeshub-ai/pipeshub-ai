"""DUR2-T4..T9: one tool call can never spin the loop that runs it unbounded (DUR1-F7)."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Literal, override

import pytest

from harness_core.effects import EffectUnknown
from harness_core.kernel.core.types import ToolCall
from harness_core.kernel.core.types import ToolResult as CoreToolResult
from harness_core.testing.clock import settle
from harness_core.tools.base import ParameterType, Tool, ToolOutput, ToolParameter
from harness_core.tools.executor import ToolExecutor
from harness_core.tools.registry import ToolRegistry
from harness_core.tools.time_bound import (
    TOOL_STALLED,
    TOOL_TIMEOUT,
    ToolCallStalled,
    ToolTimeBounds,
    run_bounded,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

BOUNDS = ToolTimeBounds(default_wall_s=0.5, stall_s=0.4)


class BodyTool(Tool):
    def __init__(
        self,
        name: str,
        body: Callable[[], Awaitable[ToolOutput]],
        bound: float | None | Literal["default"] = "default",
    ) -> None:
        self._name = name
        self._body = body
        self._bound = bound

    @property
    @override
    def name(self) -> str:
        return self._name

    @property
    @override
    def short_description(self) -> str:
        return self._name

    @property
    @override
    def description(self) -> str:
        return self._name

    @property
    @override
    def path(self) -> str:
        return f"/tools/test/{self._name}"

    @property
    @override
    def parameters(self) -> list[ToolParameter]:
        return [ToolParameter(name="note", type=ParameterType.STRING, description="free text", required=False)]

    @override
    def time_bound_s(self, arguments: Mapping[str, Any]) -> float | None | Literal["default"]:
        return self._bound

    @override
    async def execute(self, **kwargs: Any) -> ToolOutput:
        return await self._body()


async def forever() -> ToolOutput:
    await asyncio.Event().wait()
    return ToolOutput(success=True, data="never")


async def spin() -> ToolOutput:
    while True:
        pass


async def spin_swallowing_exceptions() -> ToolOutput:
    while True:
        try:
            sum(range(1000))
        except Exception:
            continue


async def yields_for(seconds: float) -> ToolOutput:
    # Many short waits, as I/O-bound work yields: the loop keeps ticking throughout.
    for _ in range(round(seconds / 0.02)):
        await asyncio.sleep(0.02)
    return ToolOutput(success=True, data="done")


def _executor(*tools: Tool) -> ToolExecutor:
    registry = ToolRegistry()
    for tool in tools:
        registry.register_tool(tool)
    return ToolExecutor(registry, bounds=BOUNDS)


async def test_dur2_t4_a_call_that_awaits_forever_ends_at_its_wall_bound() -> None:
    executor = _executor(BodyTool("hang", forever))
    result = await executor.call_tool(ToolCall(id="c1", name="hang", arguments={}))
    assert result.is_error
    assert str(result.content).startswith(f"[{TOOL_TIMEOUT}] hang did not finish within 0.5 s")


@pytest.mark.parametrize("body", [spin, spin_swallowing_exceptions])
async def test_dur2_t5_a_call_that_spins_on_the_loop_is_interrupted(
    body: Callable[[], Awaitable[ToolOutput]],
) -> None:
    executor = _executor(BodyTool("spin", body))
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0)

    other = asyncio.create_task(ticker())
    await settle(lambda: ticks > 0)
    try:
        result = await executor.call_tool(ToolCall(id="c1", name="spin", arguments={}))
        assert result.is_error
        assert str(result.content).startswith(f"[{TOOL_STALLED}] spin")
        before = ticks
        await settle(lambda: ticks > before)
    finally:
        other.cancel()


async def test_dur2_t5_the_run_goes_on_after_a_stalled_call() -> None:
    executor = _executor(BodyTool("spin", spin), BodyTool("ok", lambda: yields_for(0)))
    first = await executor.call_tool(ToolCall(id="c1", name="spin", arguments={}))
    second = await executor.call_tool(ToolCall(id="c2", name="ok", arguments={}))
    assert first.is_error
    assert (second.is_error, second.content) == (False, "done")


async def test_dur2_t6_a_yielding_call_runs_past_the_stall_bound() -> None:
    executor = _executor(BodyTool("io", lambda: yields_for(BOUNDS.stall_s * 2), bound=5.0))
    result = await executor.call_tool(ToolCall(id="c1", name="io", arguments={}))
    assert (result.is_error, result.content) == (False, "done")


async def test_dur2_t6_a_composite_runs_past_the_default_wall_bound_but_not_a_stall() -> None:
    executor = _executor(
        BodyTool("child_loop", lambda: yields_for(BOUNDS.default_wall_s * 2), bound=None),
        BodyTool("child_spin", spin, bound=None),
    )
    long = await executor.call_tool(ToolCall(id="c1", name="child_loop", arguments={}))
    assert (long.is_error, long.content) == (False, "done")
    stalled = await executor.call_tool(ToolCall(id="c2", name="child_spin", arguments={}))
    assert str(stalled.content).startswith(f"[{TOOL_STALLED}]")


def test_dur2_t6_run_code_is_bounded_above_its_own_timeout() -> None:
    from app.agent_loop_lib.tools.builtin.sandbox.coding_sandbox import (
        CodingSandboxTool,
    )
    from harness_core.tools.time_bound import DEFAULT_WALL_S

    tool = CodingSandboxTool(manager=None)  # pyright: ignore[reportArgumentType]
    assert tool.time_bound_s({}) == "default"
    assert tool.time_bound_s({"timeout": 900}) == 900 + 180
    assert tool.time_bound_s({"timeout": "x"}) == "default"
    assert DEFAULT_WALL_S == 600


def test_dur2_t6_composites_declare_no_wall_bound() -> None:
    from app.agent_loop_lib.tools.builtin.coordination.agent_tool import AgentTool
    from app.agent_loop_lib.tools.builtin.coordination.best_of_n import BestOfNTool
    from app.agent_loop_lib.tools.builtin.coordination.clarify import ClarifyTool
    from app.agent_loop_lib.tools.builtin.coordination.handoff import HandoffTool
    from app.agent_loop_lib.tools.builtin.coordination.spawn_agent import SpawnAgentTool

    for cls in (AgentTool, BestOfNTool, ClarifyTool, HandoffTool, SpawnAgentTool):
        assert cls.time_bound_s(object.__new__(cls), {}) is None, cls.__name__  # pyright: ignore[reportArgumentType]


async def test_dur2_t7_special_route_handlers_are_bounded() -> None:
    tool = BodyTool("route", forever)
    executor = _executor(tool)

    async def handler() -> CoreToolResult:
        await asyncio.Event().wait()
        return CoreToolResult(tool_call_id="c1", name="route", content="never")

    result = await executor.call_tool(ToolCall(id="c1", name="route", arguments={}), override_execute=handler)
    assert str(result.content).startswith(f"[{TOOL_TIMEOUT}] route")


@pytest.mark.parametrize("body", [forever, spin])
async def test_dur2_t8_a_recorded_effect_stopped_at_a_bound_is_unknown(
    body: Callable[[], Awaitable[ToolOutput]],
) -> None:
    executor = _executor(BodyTool("write", body))
    with pytest.raises(EffectUnknown):
        await executor.call_tool(ToolCall(id="c1", name="write", arguments={}), unknown_effects=True)


async def test_dur2_t9_one_warning_without_arguments(caplog: pytest.LogCaptureFixture) -> None:
    executor = _executor(BodyTool("hang", forever))
    with caplog.at_level(logging.WARNING, logger="harness_core.tools"):
        await executor.call_tool(ToolCall(id="c1", name="hang", arguments={"note": "s3cr3t"}))
    lines = [r.getMessage() for r in caplog.records if "tool.time_bound_exceeded" in r.getMessage()]
    assert lines == ["tool.time_bound_exceeded reason=tool_timeout tool=hang boundS=0.5 callId=c1"]
    assert "s3cr3t" not in caplog.text


async def test_run_bounded_nested_interrupts_only_the_stuck_call() -> None:
    async def inner() -> str:
        outcome = await run_bounded(spin, tool="inner", wall_s=None, stall_s=0.3)
        return getattr(outcome, "reason", "finished")

    outer = await run_bounded(inner, tool="outer", wall_s=None, stall_s=5.0)
    assert outer == TOOL_STALLED


def test_tool_call_stalled_is_not_an_exception() -> None:
    assert not issubclass(ToolCallStalled, Exception)
