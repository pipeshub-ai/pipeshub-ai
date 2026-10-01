"""The answer-only turn after `max_turns` (`agent/loops.py::_finish_after_max_turns`,
`Agent.step(final_answer_only=True)`).

A run that hits the turn cap right after a tool turn has never shown the
model those tool results, and that turn's text is only the model's
narration before its calls. Returning that narration as "the answer" put a
sentence like "I'll join the member table to the ZIP codes and list the
names" on screen instead of the names. One extra turn without tool
execution lets the model answer from what it gathered.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.context import CancellationToken
from app.agent_loop_lib.core.messages import AssistantMessage, ToolCall, UserMessage
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.events.base import EventType
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.base import ParameterType, Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from tests.unit.agents.adapter.support.scripted_transport import (
    ScriptedStep,
    ScriptedTransport,
)

if TYPE_CHECKING:
    from collections.abc import Callable

_NARRATION = "I'll join the member table to the ZIP codes and list the matching full names."
_ANSWER = "The members who grew up in Illinois are Trent Smith, Tyler Hewitt and Annabella Warren."


class _CountingTool(Tool):
    def __init__(self, on_execute: Callable[[int], None] | None = None) -> None:
        self.executions = 0
        self._on_execute = on_execute

    @property
    def name(self) -> str:
        return "run_query"

    @property
    def short_description(self) -> str:
        return "Runs a query"

    @property
    def description(self) -> str:
        return "Runs a query and returns its rows"

    @property
    def path(self) -> str:
        return "/toolsets/test/run_query"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [ToolParameter(name="sql", type=ParameterType.STRING, description="query")]

    async def execute(self, **kwargs: object) -> ToolOutput:
        self.executions += 1
        if self._on_execute is not None:
            self._on_execute(self.executions)
        return ToolOutput(success=True, data="Trent Smith | Tyler Hewitt | Annabella Warren")


def _build(
    transport: ScriptedTransport,
    tool: _CountingTool,
    *,
    max_turns: int,
    cancellation_token: CancellationToken | None = None,
) -> Agent:
    registry = ToolRegistry()
    registry.register_tool(tool)
    transports = TransportRegistry()
    transports.register("scripted", lambda: transport)
    spec = AgentSpec(
        name="agent-under-test",
        system_prompt="You are a helpful assistant.",
        model=ModelSpec(provider="scripted", model="scripted-model"),
        loop=ReActLoop(),
        max_turns=max_turns,
    )
    runtime = AgentRuntime(
        transport_registry=transports, tool_registry=registry, cancellation_token=cancellation_token,
    )
    return Agent(spec, runtime)


def _tool_turn(text: str = "", call_id: str = "c") -> ScriptedStep:
    call = ToolCall(id=call_id, name="run_query", arguments={"sql": "SELECT 1"})
    return ScriptedStep(message=AssistantMessage(content=text, tool_calls=[call]))


def _tool_turns(max_turns: int) -> list[ScriptedStep]:
    """`max_turns` tool turns, the last one narrating before its call."""
    return [_tool_turn(call_id=f"c{i}") for i in range(max_turns - 1)] + [
        _tool_turn(_NARRATION, call_id="last"),
    ]


_GOAL = Goal(description="List the members who grew up in Illinois")


class TestFinalAnswerTurn:
    async def test_answers_from_the_last_tool_results_instead_of_returning_narration(self) -> None:
        transport = ScriptedTransport(script=[
            *_tool_turns(3),
            ScriptedStep(message=AssistantMessage(content=_ANSWER)),
        ])
        tool = _CountingTool()

        result = await _build(transport, tool, max_turns=3).run(_GOAL)

        assert result.success is True
        assert result.error is None
        assert result.output == _ANSWER
        assert tool.executions == 3

    async def test_one_extra_call_carries_the_instruction_and_the_tool_list(self) -> None:
        transport = ScriptedTransport(script=[
            *_tool_turns(3),
            ScriptedStep(message=AssistantMessage(content=_ANSWER)),
        ])

        await _build(transport, _CountingTool(), max_turns=3).run(_GOAL)

        assert len(transport.calls) == 4
        final_call = transport.calls[-1]
        instruction = final_call["messages"][-1]
        assert isinstance(instruction, UserMessage)
        assert "maximum number of steps" in instruction.content
        assert "Do not call any more tools" in instruction.content
        # Still sent: providers reject tool history without tool definitions.
        assert [schema.name for schema in final_call["tools"]] == ["run_query"]

    async def test_tool_calls_in_the_final_turn_are_not_executed(self) -> None:
        transport = ScriptedTransport(script=[*_tool_turns(3), _tool_turn(call_id="ignored")])
        tool = _CountingTool()

        result = await _build(transport, tool, max_turns=3).run(_GOAL)

        assert tool.executions == 3
        # Falls back to the degraded tail: the narration is all there is.
        assert result.success is True
        assert result.output == _NARRATION

    async def test_a_failed_final_call_falls_back_instead_of_failing_the_run(self) -> None:
        transport = ScriptedTransport(script=[
            *_tool_turns(3),
            ScriptedStep(error=RuntimeError("provider unavailable")),
        ])

        result = await _build(transport, _CountingTool(), max_turns=3).run(_GOAL)

        assert result.success is True
        assert result.error is None
        assert result.output == _NARRATION

    async def test_an_empty_final_reply_falls_back(self) -> None:
        transport = ScriptedTransport(script=[
            _tool_turn(call_id="a"),
            _tool_turn(call_id="b"),
            ScriptedStep(message=AssistantMessage(content="   ")),
        ])

        result = await _build(transport, _CountingTool(), max_turns=2).run(_GOAL)

        assert result.success is False
        assert "Exceeded max_turns=2" in result.error

    async def test_no_final_turn_when_the_last_turn_did_not_call_tools(self) -> None:
        partial = "Here is the first part of a long answer that was cut off at the output limit"
        transport = ScriptedTransport(script=[
            _tool_turn(call_id="a"),
            ScriptedStep(message=AssistantMessage(content=partial, truncated=True)),
        ])

        result = await _build(transport, _CountingTool(), max_turns=2).run(_GOAL)

        assert len(transport.calls) == 2
        assert result.output == partial

    async def test_cancellation_before_the_final_turn_ends_the_run_as_cancelled(self) -> None:
        token = CancellationToken()
        tool = _CountingTool(on_execute=lambda n: token.cancel() if n == 3 else None)
        transport = ScriptedTransport(script=[
            *_tool_turns(3),
            ScriptedStep(message=AssistantMessage(content=_ANSWER)),
        ])

        result = await _build(transport, tool, max_turns=3, cancellation_token=token).run(_GOAL)

        assert result.cancelled is True
        assert len(transport.calls) == 3

    async def test_the_final_answer_streams_like_any_answer(self) -> None:
        transport = ScriptedTransport(script=[
            *_tool_turns(2),
            ScriptedStep(
                message=AssistantMessage(content=_ANSWER),
                text_chunks=["The members who grew up in Illinois are ", "Trent Smith, Tyler Hewitt and Annabella Warren."],
            ),
        ])
        agent = _build(transport, _CountingTool(), max_turns=2)

        events = [event async for event in agent.stream(_GOAL)]

        deltas = [e.payload.get("delta") for e in events if e.event_type == EventType.TEXT_MESSAGE_CONTENT]
        assert "".join(deltas) == _ANSWER
        assert agent.last_stream_result.output == _ANSWER


class TestDeadlineWarningInARun:
    async def test_runs_without_task_complete_are_asked_for_a_plain_answer(self) -> None:
        transport = ScriptedTransport(script=[
            *_tool_turns(4),
            ScriptedStep(message=AssistantMessage(content=_ANSWER)),
        ])

        await _build(transport, _CountingTool(), max_turns=4).run(_GOAL)

        warned = [
            call for call in transport.calls
            if "turns remaining" in str(getattr(call["messages"][-1], "content", ""))
        ]
        assert len(warned) == 1
        warning = warned[0]["messages"][-1].content
        assert warned[0] is transport.calls[2]
        assert "task_complete" not in warning
        assert "Reply with your final answer" in warning
