"""Doom-loop detection and the tool-denial circuit breaker, as wired by the
legacy factory (`PipesHubAgentFactory._build_hooks`) — P0.16."""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.hooks.middleware.builtin.denial_breaker import (
    MAX_CONSECUTIVE_DENIALS,
    MAX_TOTAL_DENIALS,
)
from app.agent_loop_lib.hooks.middleware.builtin.stall_detection import STEP_BACK_MARKER
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from app.agents.agent_loop.error_classification import classify_error
from app.agents.agent_loop.factory import PipesHubAgentFactory
from app.agents.agent_loop.tool_adapter import PipesHubStructuredToolAdapter
from tests.unit.agents.adapter.conftest import make_context
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport

_ADD = "calc__add"
_BLOCKED = "calc__blocked"


class _Args(BaseModel):
    a: int
    b: int


def _tool(name: str) -> PipesHubStructuredToolAdapter:
    structured = StructuredTool.from_function(
        name=name, description=name, args_schema=_Args, func=lambda a, b: f"sum is {a + b}",
    )
    return PipesHubStructuredToolAdapter(structured, "calc", name)


async def _deny_blocked(ctx, next_fn) -> None:
    if ctx.tool_path.endswith("/blocked"):
        ctx.deny("blocked by policy")
        return
    await next_fn()


def _agent(transport: ScriptedTransport, *, max_turns: int = 15) -> Agent:
    registry = ToolRegistry()
    registry.register_tool(_tool("add"))
    registry.register_tool(_tool("blocked"))
    transport_registry = TransportRegistry()
    transport_registry.register("scripted", lambda: transport)
    hooks = PipesHubAgentFactory._build_hooks(make_context())
    hooks.on(HookEvent.PRE_TOOL_USE).use(_deny_blocked)
    runtime = AgentRuntime(transport_registry=transport_registry, tool_registry=registry, hooks=hooks)
    spec = AgentSpec(
        name="pipeshub-agent", system_prompt="You are helpful.", tool_names=registry.names(),
        model=ModelSpec(provider="scripted", model="scripted-model"), loop=ReActLoop(), max_turns=max_turns,
    )
    return Agent(spec, runtime)


def _step_backs(transport: ScriptedTransport) -> list[int]:
    return [
        i for i, call in enumerate(transport.calls)
        if any(STEP_BACK_MARKER in str(m.content) for m in call["messages"])
    ]


class TestDoomLoop:
    async def test_identical_calls_step_back_once_then_stop_gracefully(self) -> None:
        transport = ScriptedTransport()
        for _ in range(8):
            transport.add_tool_call(ToolCall(id="c", name=_ADD, arguments={"a": 1, "b": 2}))

        result = await _agent(transport).run(Goal(description="loop"))

        assert _step_backs(transport) == [3]
        assert len(transport.calls) == 4
        assert result.success is False
        code, message = classify_error(result.error)
        assert code == "agent_stalled"
        assert "same step" in message

    async def test_changing_course_after_step_back_completes_normally(self) -> None:
        transport = ScriptedTransport()
        for _ in range(3):
            transport.add_tool_call(ToolCall(id="c", name=_ADD, arguments={"a": 1, "b": 2}))
        transport.add_tool_call(ToolCall(id="d", name=_ADD, arguments={"a": 5, "b": 5}))
        transport.add_text("The answer is 10.")

        result = await _agent(transport).run(Goal(description="loop then recover"))

        assert result.success is True
        assert _step_backs(transport) == [3]

    async def test_same_tool_with_new_arguments_is_not_a_loop(self) -> None:
        transport = ScriptedTransport()
        for i in range(6):
            transport.add_tool_call(ToolCall(id=f"c{i}", name=_ADD, arguments={"a": i, "b": 1}))
        transport.add_text("done")

        result = await _agent(transport).run(Goal(description="paging"))

        assert result.success is True
        assert _step_backs(transport) == []


class TestDenialBreaker:
    async def test_consecutive_denials_trip_the_breaker(self) -> None:
        transport = ScriptedTransport()
        for i in range(6):
            transport.add_tool_call(ToolCall(id=f"c{i}", name=_BLOCKED, arguments={"a": i, "b": 0}))

        result = await _agent(transport).run(Goal(description="keep hitting a blocked tool"))

        assert len(transport.calls) == MAX_CONSECUTIVE_DENIALS
        assert result.success is False
        code, message = classify_error(result.error)
        assert code == "tool_denied"
        assert "blocked" in message

    async def test_total_denials_trip_even_when_interleaved_with_successes(self) -> None:
        transport = ScriptedTransport()
        for i in range(MAX_TOTAL_DENIALS + 3):
            transport.add_tool_calls([
                ToolCall(id=f"d{i}", name=_BLOCKED, arguments={"a": i, "b": 0}),
                ToolCall(id=f"a{i}", name=_ADD, arguments={"a": i, "b": 1}),
            ])

        result = await _agent(transport, max_turns=MAX_TOTAL_DENIALS + 5).run(Goal(description="mixed"))

        assert len(transport.calls) == MAX_TOTAL_DENIALS
        assert classify_error(result.error)[0] == "tool_denied"

    async def test_denials_below_threshold_let_the_run_finish(self) -> None:
        transport = ScriptedTransport()
        for i in range(MAX_CONSECUTIVE_DENIALS - 1):
            transport.add_tool_call(ToolCall(id=f"c{i}", name=_BLOCKED, arguments={"a": i, "b": 0}))
        transport.add_text("I could not use that tool.")

        result = await _agent(transport).run(Goal(description="two denials"))

        assert result.success is True
