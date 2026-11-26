"""Cancelling a turn stops the work it started: the model call (billed while it runs) and the input
guardrails racing it are cancelled before the cancellation reaches the caller."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport


class _Pending:
    """A call that never answers on its own; records that it started and that it was cancelled."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def hold(self) -> None:
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


class _PendingTransport(ScriptedTransport):
    def __init__(self, call: _Pending) -> None:
        super().__init__()
        self.call = call

    async def complete(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        await self.call.hold()

    async def stream(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        await self.call.hold()
        yield None


def _agent(model: _Pending, guard: _Pending) -> Agent:
    transports = TransportRegistry()
    transports.register("scripted", lambda: _PendingTransport(model))
    spec = AgentSpec(
        name="cancel-agent",
        system_prompt="You are a helpful assistant.",
        model=ModelSpec(provider="scripted", model="scripted-model"),
        loop=ReActLoop(),
    )
    agent = Agent(spec, AgentRuntime(transport_registry=transports, tool_registry=ToolRegistry()))

    async def _slow_guard(ctx: Any, next_fn: Any) -> None:  # noqa: ANN401
        await guard.hold()

    agent.runtime.hooks.on(HookEvent.GUARDRAIL_INPUT).use(_slow_guard)
    return agent


async def test_fx5_1_cancelling_a_turn_cancels_its_model_call_and_input_guardrail() -> None:
    model, guard = _Pending(), _Pending()
    turn = asyncio.create_task(_agent(model, guard).run(Goal(description="What changed?")))
    await asyncio.wait_for(asyncio.gather(model.started.wait(), guard.started.wait()), 5)

    turn.cancel()
    with pytest.raises(asyncio.CancelledError):
        await turn

    assert model.cancelled.is_set()
    assert guard.cancelled.is_set()
