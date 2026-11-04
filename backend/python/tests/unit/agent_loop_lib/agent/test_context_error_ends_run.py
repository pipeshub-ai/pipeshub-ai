"""A context the shapers cannot make valid ends the run without a model call.

Before, the PRE_MODEL pipeline recorded `ContextBudgetExceeded` and went on,
so the request was sent anyway: over the window (a provider error) or, when
eviction had emptied it, without the user's question (a wrong answer).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.context.errors import ContextBudgetExceeded
from app.agent_loop_lib.core.messages import AssistantMessage
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.hooks.registry import HookRegistry
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from tests.unit.agents.adapter.support.scripted_transport import (
    ScriptedStep,
    ScriptedTransport,
)

if TYPE_CHECKING:
    from app.agent_loop_lib.hooks.middleware.context import ModelCallContext
    from app.agent_loop_lib.hooks.middleware.pipeline import Middleware


def _context_cannot_fit() -> "Middleware[ModelCallContext]":
    async def _middleware(ctx: ModelCallContext, next_fn) -> None:
        raise ContextBudgetExceeded(250_000, 200_000)

    return _middleware


def _agent(transport: ScriptedTransport, hooks: HookRegistry) -> Agent:
    transports = TransportRegistry()
    transports.register("scripted", lambda: transport)
    spec = AgentSpec(
        name="agent-under-test",
        system_prompt="You are a helpful assistant.",
        model=ModelSpec(provider="scripted", model="scripted-model"),
        loop=ReActLoop(),
        max_turns=3,
    )
    runtime = AgentRuntime(transport_registry=transports, tool_registry=ToolRegistry(), hooks=hooks)
    return Agent(spec, runtime)


class TestContextErrorEndsRun:
    async def test_run_fails_with_the_context_error_and_no_model_call(self) -> None:
        transport = ScriptedTransport(script=[ScriptedStep(message=AssistantMessage(content="answer"))])
        hooks = HookRegistry()
        hooks.on(HookEvent.PRE_MODEL).use(_context_cannot_fit())

        result = await _agent(transport, hooks).run(Goal(description="Which practice do I log into?"))

        assert result.success is False
        assert "Context could not be shaped" in (result.error or "")
        assert "250000 tokens" in (result.error or "")
        assert transport.calls == []

    async def test_other_shaper_errors_do_not_end_the_run(self) -> None:
        def _broken() -> "Middleware[ModelCallContext]":
            async def _middleware(ctx: ModelCallContext, next_fn) -> None:
                raise RuntimeError("a shaper bug")

            return _middleware

        transport = ScriptedTransport(script=[ScriptedStep(message=AssistantMessage(content="answer"))])
        hooks = HookRegistry()
        hooks.on(HookEvent.PRE_MODEL).use(_broken())

        result = await _agent(transport, hooks).run(Goal(description="Which practice do I log into?"))

        assert result.success is True
        assert len(transport.calls) == 1
