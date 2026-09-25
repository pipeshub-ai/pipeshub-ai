"""What the chat window receives while the model is still writing.

`Agent.stream()` turns the model's token stream into the events the UI
renders live: the "thinking" panel (reasoning start/content/end), the
answer text, and a "still working" state while a long tool call is being
composed. The order matters — the UI closes the thinking panel when answer
text starts — so these tests pin it, driving a real `Agent` with a scripted
token stream.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.messages import AssistantMessage, Message, ToolCall
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
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.builtin.planning.final_answer import FinalAnswerTool
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import pytest

    from app.agent_loop_lib.core.tool_schema import ToolSchema

_GOAL = Goal(description="Explain the outage")
_LIVE = {
    EventType.REASONING_MESSAGE_START, EventType.REASONING_MESSAGE_CONTENT, EventType.REASONING_MESSAGE_END,
    EventType.TEXT_MESSAGE_START, EventType.TEXT_MESSAGE_CONTENT, EventType.TEXT_MESSAGE_END,
}


def _done(message: AssistantMessage) -> StreamCompleteEvent:
    return StreamCompleteEvent(response=ModelResponse(message=message, usage=TokenUsage(), model="scripted-model"))


class _TokenStream(ScriptedTransport):
    """Replays one scripted list of stream events per model call."""

    def __init__(self, turns: list[list[StreamEvent]]) -> None:
        super().__init__()
        self._turns = list(turns)

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
        for event in self._turns.pop(0):
            yield event


def _agent(transport: ScriptedTransport, registry: ToolRegistry | None = None) -> Agent:
    transports = TransportRegistry()
    transports.register("scripted", lambda: transport)
    spec = AgentSpec(
        name="streaming-agent",
        system_prompt="You are a helpful assistant.",
        model=ModelSpec(provider="scripted", model="scripted-model"),
        loop=ReActLoop(),
        max_turns=3,
    )
    return Agent(spec, AgentRuntime(transport_registry=transports, tool_registry=registry or ToolRegistry()))


async def _collect(agent: Agent) -> list[AgentEvent]:
    return [event async for event in agent.stream(_GOAL)]


def _live(events: list[AgentEvent]) -> list[tuple[EventType, str | None]]:
    return [(e.event_type, e.payload.get("delta")) for e in events if e.event_type in _LIVE]


class TestLiveAnswerEvents:
    async def test_thinking_closes_before_the_answer_starts(self) -> None:
        transport = _TokenStream([[
            ThinkingDeltaEvent(delta="Check the logs. "),
            ThinkingDeltaEvent(delta="Then the deploy."),
            TextDeltaEvent(delta="The deploy "),
            TextDeltaEvent(delta="broke it."),
            _done(AssistantMessage(content="The deploy broke it.")),
        ]])
        agent = _agent(transport)

        events = await _collect(agent)

        assert _live(events) == [
            (EventType.TEXT_MESSAGE_START, None),
            (EventType.REASONING_MESSAGE_START, None),
            (EventType.REASONING_MESSAGE_CONTENT, "Check the logs. "),
            (EventType.REASONING_MESSAGE_CONTENT, "Then the deploy."),
            (EventType.REASONING_MESSAGE_END, None),
            (EventType.TEXT_MESSAGE_CONTENT, "The deploy "),
            (EventType.TEXT_MESSAGE_CONTENT, "broke it."),
            (EventType.TEXT_MESSAGE_END, None),
        ]
        assert agent.last_stream_result.output == "The deploy broke it."
        assert agent.streaming is False

    async def test_thinking_that_ends_in_a_tool_call_is_still_closed(self) -> None:
        transport = _TokenStream([
            [
                ThinkingDeltaEvent(delta="Need the timeline."),
                _done(AssistantMessage(tool_calls=[ToolCall(id="t1", name="missing_tool", arguments={})])),
            ],
            [TextDeltaEvent(delta="Done."), _done(AssistantMessage(content="Done."))],
        ])

        events = await _collect(_agent(transport))

        first_turn = _live(events)[:4]
        assert first_turn == [
            (EventType.TEXT_MESSAGE_START, None),
            (EventType.REASONING_MESSAGE_START, None),
            (EventType.REASONING_MESSAGE_CONTENT, "Need the timeline."),
            (EventType.REASONING_MESSAGE_END, None),
        ]

    async def test_a_long_tool_call_reasserts_working_state_once(self) -> None:
        call = ToolCall(id="t1", name="run_code", arguments={"code": "print(1)"})
        transport = _TokenStream([
            [
                TextDeltaEvent(delta="Let me compute that."),
                ToolCallDeltaEvent(index=0, id="t1", name="run_code", arguments_delta='{"code": "pri'),
                ToolCallDeltaEvent(index=0, arguments_delta='nt(1)"}'),
                _done(AssistantMessage(content="Let me compute that.", tool_calls=[call])),
            ],
            [TextDeltaEvent(delta="1"), _done(AssistantMessage(content="1"))],
        ])

        events = await _collect(_agent(transport))

        first_turn_end = next(i for i, e in enumerate(events) if e.event_type == EventType.TEXT_MESSAGE_END)
        first_turn = events[:first_turn_end]
        after_text = first_turn[[e.event_type for e in first_turn].index(EventType.TEXT_MESSAGE_CONTENT) + 1:]
        snapshots = [e for e in after_text if e.event_type == EventType.STATE_SNAPSHOT]
        assert len(snapshots) == 1
        assert snapshots[0].payload["status"] == "calling_llm"
        assert not any(e.event_type == EventType.TEXT_MESSAGE_CONTENT for e in after_text)

    async def test_final_answer_arguments_stream_as_answer_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PIPESHUB_ENABLE_FINAL_ANSWER", "true")
        registry = ToolRegistry()
        registry.register_tool(FinalAnswerTool())
        call = ToolCall(id="fa", name="final_answer", arguments={"answer_markdown": "It was DNS.", "confidence": "high"})
        transport = _TokenStream([[
            ThinkingDeltaEvent(delta="Obviously DNS."),
            ToolCallDeltaEvent(index=0, id="fa", name="final_answer", arguments_delta='{"answer_markdown": "It was'),
            ToolCallDeltaEvent(index=0, arguments_delta=' DNS."'),
            ToolCallDeltaEvent(index=0, arguments_delta=', "confidence": "high"}'),
            _done(AssistantMessage(tool_calls=[call])),
        ]])

        events = await _collect(_agent(transport, registry))

        live = _live(events)
        assert (EventType.REASONING_MESSAGE_END, None) in live
        end_of_thinking = live.index((EventType.REASONING_MESSAGE_END, None))
        streamed = "".join(d for t, d in live[end_of_thinking:] if t == EventType.TEXT_MESSAGE_CONTENT)
        assert streamed == "It was DNS."


class TestBrokenStream:
    async def test_stream_that_never_completes_fails_the_run_cleanly(self) -> None:
        transport = _TokenStream([[TextDeltaEvent(delta="partial")]])
        agent = _agent(transport)

        events = await _collect(agent)

        result = agent.last_stream_result
        assert result.success is False
        assert result.error == "LLM call failed: Model.stream() completed without a StreamCompleteEvent"
        types = [e.event_type for e in events]
        assert EventType.RUN_ERROR in types
        assert types.index(EventType.TEXT_MESSAGE_END) < types.index(EventType.RUN_ERROR)
        assert [e for e in events if e.event_type == EventType.STATE_SNAPSHOT][-1].payload["status"] == "failed"
