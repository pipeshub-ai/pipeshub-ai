"""A model stream that drops mid-answer is retried, and the client is told to
drop what the failed attempt streamed.

The retry wrapper re-runs the whole model call, so retrying once output has
reached the client would show it twice. `Agent.step()` closes the failed
attempt with `discarded: True` and only then lets a transient failure be
retried. A non-transient failure is never retried, and a connection that
keeps dropping is retried a bounded number of times.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.exceptions import TransportError
from app.agent_loop_lib.core.messages import AssistantMessage, Message
from app.agent_loop_lib.core.responses import ModelResponse, TokenUsage
from app.agent_loop_lib.core.streaming import (
    StreamCompleteEvent,
    StreamEvent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
    ToolCallDeltaEvent,
)
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.events.base import AgentEvent, EventType
from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.hooks.middleware.builtin.retry import retry_model_call
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.base import RetryConfig
from app.agent_loop_lib.transport.registry import TransportRegistry
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.agent_loop_lib.core.tool_schema import ToolSchema

_GOAL = Goal(description="Explain the outage")
_ANSWER = "The deploy broke it."


def _dropped(*, transient: bool = True) -> TransportError:
    return TransportError(
        "RemoteProtocolError: peer closed connection without sending complete message body",
        retryable=transient, partial_output=True,
    )


def _done(text: str = _ANSWER) -> StreamCompleteEvent:
    return StreamCompleteEvent(response=ModelResponse(
        message=AssistantMessage(content=text), usage=TokenUsage(), model="scripted-model",
    ))


class _DroppingStream(ScriptedTransport):
    """One scripted attempt per model call; an attempt ending in an exception
    raises it after yielding the events before it."""

    def __init__(self, attempts: list[list[StreamEvent | Exception]]) -> None:
        super().__init__()
        self._attempts = list(attempts)
        self.calls = 0

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
        system_blocks: list[str] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self.calls += 1
        for item in self._attempts.pop(0):
            if isinstance(item, Exception):
                raise item
            yield item


def _agent(transport: ScriptedTransport) -> Agent:
    transports = TransportRegistry()
    transports.register("scripted", lambda: transport)
    runtime = AgentRuntime(transport_registry=transports, tool_registry=ToolRegistry())
    runtime.hooks.wrapper(HookEvent.PRE_MODEL_CALL).use(retry_model_call(RetryConfig(
        max_retries=3, initial_delay=0.0, backoff_factor=1.0, max_delay=0.0,
    )))
    spec = AgentSpec(
        name="streaming-agent",
        system_prompt="You are a helpful assistant.",
        model=ModelSpec(provider="scripted", model="scripted-model"),
        loop=ReActLoop(),
        max_turns=3,
    )
    return Agent(spec, runtime)


async def _collect(agent: Agent) -> list[AgentEvent]:
    return [event async for event in agent.stream(_GOAL)]


def _shown_text(events: list[AgentEvent]) -> str:
    """The text a client is left with: deltas of every attempt that was not
    discarded."""
    shown: list[str] = []
    attempt: list[str] = []
    for event in events:
        if event.event_type == EventType.TEXT_MESSAGE_START:
            attempt = []
        elif event.event_type == EventType.TEXT_MESSAGE_CONTENT:
            attempt.append(event.payload["delta"])
        elif event.event_type == EventType.TEXT_MESSAGE_END and not event.payload.get("discarded"):
            shown.extend(attempt)
    return "".join(shown)


def _ends(events: list[AgentEvent], event_type: EventType) -> list[dict]:
    return [e.payload for e in events if e.event_type == event_type]


class TestRetryAfterPartialOutput:
    async def test_answer_text_then_drop_is_retried_and_the_fragment_discarded(self) -> None:
        transport = _DroppingStream([
            [TextDeltaEvent(delta="The dep"), _dropped()],
            [TextDeltaEvent(delta="The deploy "), TextDeltaEvent(delta="broke it."), _done()],
        ])
        agent = _agent(transport)

        events = await _collect(agent)

        assert transport.calls == 2
        assert agent.last_stream_result.success is True
        assert agent.last_stream_result.output == _ANSWER
        assert _ends(events, EventType.TEXT_MESSAGE_END) == [
            {"turn_index": 0, "discarded": True}, {"turn_index": 0},
        ]
        assert _shown_text(events) == _ANSWER

    async def test_reasoning_then_drop_closes_the_reasoning_as_discarded(self) -> None:
        transport = _DroppingStream([
            [ThinkingDeltaEvent(delta="Check the logs."), _dropped()],
            [ThinkingDeltaEvent(delta="Check the logs."), TextDeltaEvent(delta=_ANSWER), _done()],
        ])
        agent = _agent(transport)

        events = await _collect(agent)

        assert transport.calls == 2
        assert agent.last_stream_result.output == _ANSWER
        live = [
            (e.event_type, e.payload.get("discarded"))
            for e in events
            if e.event_type in (EventType.REASONING_MESSAGE_END, EventType.TEXT_MESSAGE_END)
        ]
        assert live[:2] == [
            (EventType.REASONING_MESSAGE_END, True), (EventType.TEXT_MESSAGE_END, True),
        ]
        assert live[2:] == [(EventType.REASONING_MESSAGE_END, None), (EventType.TEXT_MESSAGE_END, None)]

    async def test_tool_call_arguments_then_drop_is_retried(self) -> None:
        transport = _DroppingStream([
            [ToolCallDeltaEvent(index=0, id="c1", name="search", arguments_delta='{"q": "lo'), _dropped()],
            [TextDeltaEvent(delta=_ANSWER), _done()],
        ])
        agent = _agent(transport)

        await _collect(agent)

        assert transport.calls == 2
        assert agent.last_stream_result.output == _ANSWER

    async def test_a_non_transient_failure_is_not_retried_but_still_discarded(self) -> None:
        transport = _DroppingStream([
            [TextDeltaEvent(delta="The dep"), _dropped(transient=False)],
            [TextDeltaEvent(delta=_ANSWER), _done()],
        ])
        agent = _agent(transport)

        events = await _collect(agent)

        assert transport.calls == 1
        assert agent.last_stream_result.success is False
        assert _ends(events, EventType.TEXT_MESSAGE_END) == [{"turn_index": 0, "discarded": True}]
        assert _shown_text(events) == ""

    async def test_a_connection_that_keeps_dropping_is_retried_twice_then_fails(self) -> None:
        transport = _DroppingStream([
            [TextDeltaEvent(delta="The dep"), _dropped()],
            [TextDeltaEvent(delta="The dep"), _dropped()],
            [TextDeltaEvent(delta="The dep"), _dropped()],
            [TextDeltaEvent(delta=_ANSWER), _done()],
        ])
        agent = _agent(transport)

        events = await _collect(agent)

        assert transport.calls == 3
        assert agent.last_stream_result.success is False
        assert _shown_text(events) == ""

    async def test_no_retry_when_the_client_could_not_be_told_to_discard(self) -> None:
        """A retry the client was not told about would show the fragment twice."""
        transport = _DroppingStream([
            [TextDeltaEvent(delta="The dep"), _dropped()],
            [TextDeltaEvent(delta=_ANSWER), _done()],
        ])
        agent = _agent(transport)
        original_emit = agent.emit

        async def _emit(event_type: EventType, payload: dict) -> None:
            if payload.get("discarded"):
                raise RuntimeError("client gone")
            await original_emit(event_type, payload)

        agent.emit = _emit  # type: ignore[method-assign]

        await _collect(agent)

        assert transport.calls == 1
        assert agent.last_stream_result.success is False

    async def test_a_drop_before_any_output_still_retries_up_to_the_wrapper_limit(self) -> None:
        """Failures with nothing streamed were already retryable; the bound on
        retries after partial output must not tighten them."""
        nothing_shown = TransportError("connect timeout", retryable=True)
        transport = _DroppingStream([
            [nothing_shown], [nothing_shown], [nothing_shown], [TextDeltaEvent(delta=_ANSWER), _done()],
        ])
        agent = _agent(transport)

        await _collect(agent)

        assert transport.calls == 4
        assert agent.last_stream_result.output == _ANSWER


class TestTransportErrorPartialOutput:
    def test_partial_output_holds_back_a_transient_retry(self) -> None:
        exc = _dropped()
        assert exc.retryable is False
        exc.discard_partial_output()
        assert exc.retryable is True
        assert exc.partial_output is False

    @pytest.mark.parametrize("partial_output", [True, False])
    def test_discarding_never_makes_a_non_transient_failure_retryable(self, partial_output: bool) -> None:
        exc = TransportError("bad request", status_code=400, retryable=False, partial_output=partial_output)
        exc.discard_partial_output()
        assert exc.retryable is False
