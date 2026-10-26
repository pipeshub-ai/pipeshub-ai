"""An Azure OpenAI stream that drops mid-answer
(`httpx.RemoteProtocolError: peer closed connection without sending complete
message body`) is retried instead of failing the run, and the answer reaches
the client and the saved transcript exactly once.

Drives a real `Agent` through `LangChainTransport` and the PipesHub
`retry_with_status` wrapper, with the AG-UI emitter and the transcript
collector the chat endpoint uses.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
from langchain_core.messages import AIMessageChunk

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.events.base import CompositeEmitter
from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.base import RetryConfig
from app.agent_loop_lib.transport.registry import TransportRegistry
from app.agents.agent_loop.hooks.retry_with_status import retry_with_status
from app.agents.agent_loop.langchain_transport import LangChainTransport
from app.agents.agent_loop.protocol.agui_emitter import (
    MESSAGE_DISCARDED_EVENT_NAME,
    AGUIEventEmitter,
)
from app.agents.agent_loop.protocol.transcript_collector import TranscriptCollector

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_ANSWER = ["The deploy ", "broke ", "it."]


class _Sink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def write(self, event: dict) -> bool:
        self.events.append(event)
        return True


class _DropsOnceModel:
    """A LangChain chat model whose first stream sends `first_attempt` and
    then loses the connection; later streams complete."""

    def __init__(self, first_attempt: list[AIMessageChunk]) -> None:
        self._first_attempt = first_attempt
        self.attempts = 0

    def bind_tools(self, tools: list[object], **kwargs: object) -> _DropsOnceModel:
        return self

    async def astream(
        self, messages: list, config: object = None, **kwargs: object,
    ) -> AsyncIterator[AIMessageChunk]:
        self.attempts += 1
        if self.attempts == 1:
            for chunk in self._first_attempt:
                yield chunk
            raise httpx.RemoteProtocolError(
                "peer closed connection without sending complete message body (incomplete chunked read)",
            )
        for text in _ANSWER:
            yield AIMessageChunk(content=text)


async def _run(model: _DropsOnceModel) -> tuple[Agent, _Sink, _Sink, TranscriptCollector]:
    transports = TransportRegistry()
    transports.register("langchain", lambda: LangChainTransport(model))
    agui_sink, status_sink = _Sink(), _Sink()
    transcript = TranscriptCollector()
    runtime = AgentRuntime(
        transport_registry=transports, tool_registry=ToolRegistry(),
        event_emitter=CompositeEmitter([AGUIEventEmitter(agui_sink, thread_id="t1"), transcript]),
    )
    runtime.hooks.wrapper(HookEvent.PRE_MODEL_CALL).use(retry_with_status(status_sink, RetryConfig(
        initial_delay=0.0, backoff_factor=1.0, max_delay=0.0,
    )))
    agent = Agent(AgentSpec(
        name="pipeshub-agent", system_prompt="Answer.",
        model=ModelSpec(provider="langchain", model="gpt-6.1-sol"), loop=ReActLoop(), max_turns=2,
    ), runtime)
    _ = [event async for event in agent.stream(Goal(description="Why did it break?"))]
    return agent, agui_sink, status_sink, transcript


def _frames(sink: _Sink, frame_type: str) -> list[dict]:
    return [e["data"] for e in sink.events if e["data"].get("type") == frame_type]


def _text_left_on_screen(sink: _Sink) -> str:
    """Append-only TEXT_MESSAGE_CONTENT per message, minus every message a
    `message_discarded` frame took back — what the frontend renders."""
    discarded = {
        f["value"]["messageId"] for f in _frames(sink, "CUSTOM") if f["name"] == MESSAGE_DISCARDED_EVENT_NAME
    }
    return "".join(f["delta"] for f in _frames(sink, "TEXT_MESSAGE_CONTENT") if f["messageId"] not in discarded)


class TestStreamDropMidAnswer:
    async def test_dropped_answer_is_retried_and_shown_once(self) -> None:
        model = _DropsOnceModel([AIMessageChunk(content="The dep"), AIMessageChunk(content="loy br")])

        agent, agui, status, transcript = await _run(model)

        assert model.attempts == 2
        assert agent.last_stream_result.success is True
        assert agent.last_stream_result.output == "".join(_ANSWER)
        assert _text_left_on_screen(agui) == "".join(_ANSWER)
        assert [p["content"] for p in transcript.parts if p["type"] == "text"] == ["".join(_ANSWER)]
        assert [e["data"]["status"] for e in status.events] == ["retrying"]

    async def test_reasoning_streamed_before_the_drop_is_not_kept_twice(self) -> None:
        reasoning = AIMessageChunk(content=[{"type": "reasoning", "reasoning": "Check the deploy log."}])
        model = _DropsOnceModel([reasoning])

        agent, agui, _status, transcript = await _run(model)

        assert model.attempts == 2
        assert agent.last_stream_result.output == "".join(_ANSWER)
        assert [p["type"] for p in transcript.parts] == ["text"]
        discarded = [f for f in _frames(agui, "CUSTOM") if f["name"] == MESSAGE_DISCARDED_EVENT_NAME]
        assert len(discarded) == 1

    async def test_a_drop_before_any_output_needs_no_discard_on_the_client(self) -> None:
        """A role-only chunk shows the client nothing, so the retry was always
        allowed; the closed attempt still leaves no empty part behind."""
        model = _DropsOnceModel([AIMessageChunk(content="")])

        agent, _agui, _status, transcript = await _run(model)

        assert model.attempts == 2
        assert agent.last_stream_result.output == "".join(_ANSWER)
        assert [p["content"] for p in transcript.parts if p["type"] == "text"] == ["".join(_ANSWER)]
