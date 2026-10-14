"""A reply cut off at the model's output-token limit, then continued.

When the model runs out of output tokens mid-answer, the turn loop asks it
to continue from where it stopped. The answer the user gets must be the
whole reply, first part then continuation, not just the continuation. These
tests drive a real `Agent` with the production turn guards; only the model
is scripted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.messages import AssistantMessage, Message, ToolCall
from app.agent_loop_lib.core.responses import ModelResponse, StopReason, TokenUsage
from app.agent_loop_lib.core.streaming import (
    StreamCompleteEvent,
    StreamEvent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
)
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.events.base import AgentEvent, EventType
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.agent_loop_lib.core.tool_schema import ToolSchema

_GOAL = Goal(description="Summarise the quarter")


def _build(transport: ScriptedTransport, *, max_turns: int = 6) -> Agent:
    transports = TransportRegistry()
    transports.register("scripted", lambda: transport)
    spec = AgentSpec(
        name="continuation-agent",
        system_prompt="You are a helpful assistant.",
        model=ModelSpec(provider="scripted", model="scripted-model"),
        loop=ReActLoop(),
        max_turns=max_turns,
    )
    return Agent(spec, AgentRuntime(transport_registry=transports, tool_registry=ToolRegistry()))


def _turn(text: str, *, cut_off: bool = False, thinking: str = "") -> list[StreamEvent]:
    """One streamed model reply: optional thinking, then the text in two deltas."""
    events: list[StreamEvent] = [ThinkingDeltaEvent(delta=thinking)] if thinking else []
    half = len(text) // 2
    events += [TextDeltaEvent(delta=text[:half]), TextDeltaEvent(delta=text[half:])]
    events.append(StreamCompleteEvent(response=ModelResponse(
        message=AssistantMessage(content=text, truncated=cut_off),
        usage=TokenUsage(),
        stop_reason=StopReason.MAX_TOKENS if cut_off else StopReason.END_TURN,
        model="scripted-model",
    )))
    return events


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
        self.calls.append({"messages": messages})
        for event in self._turns.pop(0):
            yield event


async def _stream(agent: Agent) -> list[AgentEvent]:
    return [event async for event in agent.stream(_GOAL)]


def _text_starts(events: list[AgentEvent]) -> list[dict]:
    return [e.payload for e in events if e.event_type == EventType.TEXT_MESSAGE_START]


def _text_ends(events: list[AgentEvent]) -> list[dict]:
    return [e.payload for e in events if e.event_type == EventType.TEXT_MESSAGE_END]


class TestCutOffReplyIsReturnedWhole:
    async def test_one_cut_off_then_continuation(self) -> None:
        transport = ScriptedTransport().add_truncated("Revenue rose 12% this quarter, ")
        transport.add_text("driven by the new enterprise plan.")
        agent = _build(transport)

        result = await agent.run(_GOAL)

        assert result.success is True
        assert result.output == "Revenue rose 12% this quarter, driven by the new enterprise plan."

    async def test_two_continuations_are_joined_in_order(self) -> None:
        transport = ScriptedTransport().add_truncated("First, sales grew. ")
        transport.add_truncated("Second, churn fell. ")
        transport.add_text("Third, margins held.")
        agent = _build(transport)

        result = await agent.run(_GOAL)

        assert len(transport.calls) == 3
        assert result.output == "First, sales grew. Second, churn fell. Third, margins held."

    async def test_cut_off_mid_word_is_rejoined_without_a_gap(self) -> None:
        transport = ScriptedTransport().add_truncated("The quarterly revenue gr")
        transport.add_text("ew by 12% year over year.")
        agent = _build(transport)

        result = await agent.run(_GOAL)

        assert result.output == "The quarterly revenue grew by 12% year over year."

    async def test_normal_reply_is_unchanged(self) -> None:
        transport = ScriptedTransport().add_text("Revenue rose 12% this quarter.")
        agent = _build(transport)

        result = await agent.run(_GOAL)

        assert result.output == "Revenue rose 12% this quarter."
        assert len(transport.calls) == 1

    async def test_text_before_a_tool_call_is_not_joined(self) -> None:
        # A cut-off reply the model abandons for a tool call was never finished;
        # the later answer stands on its own.
        transport = ScriptedTransport().add_truncated("Let me start writing the summ")
        transport.add_tool_call(ToolCall(id="t1", name="missing_tool", arguments={}))
        transport.add_text("Here is the summary.")
        agent = _build(transport)

        result = await agent.run(_GOAL)

        assert result.output == "Here is the summary."

    async def test_running_out_of_turns_mid_continuation_keeps_every_part(self) -> None:
        transport = ScriptedTransport().add_truncated("Part one of the summary runs long, ")
        transport.add_truncated("and part two keeps going")
        agent = _build(transport, max_turns=2)

        result = await agent.run(_GOAL)

        assert result.success is True
        assert result.output == "Part one of the summary runs long, and part two keeps going"


class TestContinuationIsMarkedOnTheLiveStream:
    async def test_only_the_continuation_turn_is_marked(self) -> None:
        transport = _TokenStream([
            _turn("Revenue rose 12% this quarter, ", cut_off=True),
            _turn("driven by the new enterprise plan."),
        ])
        agent = _build(transport)

        events = await _stream(agent)

        starts = _text_starts(events)
        assert [s.get("continues_truncated", False) for s in starts] == [False, True]
        assert agent.last_stream_result.output == (
            "Revenue rose 12% this quarter, driven by the new enterprise plan."
        )

    async def test_thinking_still_closes_before_the_continued_answer(self) -> None:
        transport = _TokenStream([
            _turn("Revenue rose 12% this quarter, ", cut_off=True, thinking="Start with revenue."),
            _turn("driven by the new enterprise plan.", thinking="Now explain why."),
        ])
        agent = _build(transport)

        events = await _stream(agent)

        second_turn = events[[i for i, e in enumerate(events) if e.event_type == EventType.TEXT_MESSAGE_START][1]:]
        order = [
            e.event_type for e in second_turn
            if e.event_type in (EventType.REASONING_MESSAGE_END, EventType.TEXT_MESSAGE_CONTENT)
        ]
        assert order[0] == EventType.REASONING_MESSAGE_END
        assert set(order[1:]) == {EventType.TEXT_MESSAGE_CONTENT}

    async def test_the_join_is_confirmed_only_when_the_turn_continues_in_text(self) -> None:
        transport = _TokenStream([
            _turn("Revenue rose 12% this quarter, ", cut_off=True),
            _turn("driven by the new enterprise plan."),
        ])

        events = await _stream(_build(transport))

        assert [s.get("joins_truncated", False) for s in _text_ends(events)] == [False, True]

    async def test_normal_reply_is_not_marked(self) -> None:
        transport = _TokenStream([_turn("Revenue rose 12% this quarter.")])
        agent = _build(transport)

        events = await _stream(agent)

        assert _text_starts(events) == [{"turn_index": 0}]
        assert _text_ends(events) == [{"turn_index": 0}]
        assert agent.last_stream_result.output == "Revenue rose 12% this quarter."
