"""P4.9 ST-12, ST-13: today's loop (`Agent`) gets the same stall bound through `TransportModel`; its
PRE_MODEL_CALL retry wrapper retries a stall only while nothing was shown."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.messages import AssistantMessage
from app.agent_loop_lib.core.responses import ModelResponse, StopReason, TokenUsage
from app.agent_loop_lib.core.streaming import StreamCompleteEvent, TextDeltaEvent
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.hooks.middleware.builtin.retry import retry_model_call
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.base import RetryConfig
from app.agent_loop_lib.transport.registry import TransportRegistry
from app.agents.agent_loop.error_classification import classify_error
from harness_core.models.stream_watch import StreamTimeouts
from harness_core.models.transport_model import TransportModel
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.agent_loop_lib.core.streaming import StreamEvent

FAST = StreamTimeouts(first_event_s=0.2, idle_s=0.05, response_s=0.2)


class _Plays(ScriptedTransport):
    """One play per call: "stall" (nothing, ever), "partial" ("Hel" then nothing), "answer"."""

    def __init__(self, *plays: str) -> None:
        super().__init__()
        self._plays = list(plays)

    async def stream(self, messages: Any, tools: Any = None, system: Any = None, model: Any = None,  # noqa: ANN401
                     thinking_budget: Any = None, effort: Any = None,  # noqa: ANN401
                     system_blocks: Any = None, *, tool_choice: object = None) -> AsyncIterator[StreamEvent]:  # noqa: ANN401
        self.calls.append({"messages": messages})
        play = self._plays.pop(0)
        if play != "stall":
            yield TextDeltaEvent(delta="Hel")
        if play in ("stall", "partial"):
            await asyncio.Event().wait()
        yield TextDeltaEvent(delta="lo")
        yield StreamCompleteEvent(response=ModelResponse(
            message=AssistantMessage(content="Hello"), usage=TokenUsage(), stop_reason=StopReason.END_TURN,
        ))


def _agent(transport: _Plays) -> Agent:
    transports = TransportRegistry()
    transports.register("scripted", lambda: transport)
    runtime = AgentRuntime(transport_registry=transports, tool_registry=ToolRegistry())
    runtime.hooks.wrapper(HookEvent.PRE_MODEL_CALL).use(retry_model_call(RetryConfig(initial_delay=0.0)))  # type: ignore[union-attr]
    spec = AgentSpec(
        name="stall-agent",
        system_prompt="You are a helpful assistant.",
        model=ModelSpec(provider="scripted", model="scripted-model"),
        loop=ReActLoop(),
        max_turns=3,
    )
    agent = Agent(spec, runtime)
    agent._model = TransportModel(transport, timeouts=FAST)
    return agent


async def _answer(agent: Agent) -> Any:  # noqa: ANN401
    async for _ in agent.stream(Goal(description="Say hello")):
        pass
    return agent.last_stream_result


async def test_st12_a_stall_before_any_output_is_retried_by_the_loop() -> None:
    transport = _Plays("stall", "answer")
    result = await _answer(_agent(transport))
    assert result.success and result.output == "Hello"
    assert len(transport.calls) == 2


async def test_st13_a_stall_after_partial_output_is_not_replayed() -> None:
    transport = _Plays("partial", "answer")
    result = await _answer(_agent(transport))
    assert not result.success
    assert len(transport.calls) == 1, "retrying would stream the shown text twice"
    assert classify_error(str(result.error))[0] == "timeout"
