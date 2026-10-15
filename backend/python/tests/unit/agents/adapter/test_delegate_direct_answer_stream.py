"""A delegate's answer reaching the user directly (`final=true`).

Drives what `stream_bridge.py` wires together for a chat request, with only
the model scripted: a real root `Agent` streaming, a real `AgentTool`
delegate with its own tool, `TerminalAnswerStreamer` for the live answer,
`TranscriptCollector` for the saved parts (agui), and `AnswerFinalizer` for
the saved answer, citations and `answeredVia`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.core.types import AgentResult, Goal
from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.hooks.registry import HookRegistry
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.builtin.coordination.agent_tool import AgentTool
from app.agent_loop_lib.tools.builtin.planning.task_complete import TaskCompleteTool
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from app.agents.agent_loop.answer_streamer import TerminalAnswerStreamer
from app.agents.agent_loop.hooks.citations import CitationCollector
from app.agents.agent_loop.protocol.transcript_collector import TranscriptCollector
from app.agents.agent_loop.respond import AnswerFinalizer
from tests.unit.agent_loop_lib.agent.test_agent_step_outcomes import _NoteTool
from tests.unit.agent_loop_lib.agent.test_cut_off_reply_continuation import (
    _TokenStream,
    _turn,
)
from tests.unit.agents.adapter.conftest import make_context
from tests.unit.agents.adapter.test_cut_off_answer_saved_whole import (
    _REPORT,
    _Sink,
    _tool_turn,
)

if TYPE_CHECKING:
    from app.agents.agent_loop.context import AgentContext

_CHILD_ANSWER = f"The chart shows sign-ups beat plan, per the [report]({_REPORT})."
_NARRATION = "I'll hand this to the coding agent."
_CHILD_NARRATION = "Let me record the numbers first."


def _delegate_call(**arguments: object) -> ToolCall:
    return ToolCall(id="d1", name="coding_agent", arguments={"goal": "plot sign-ups", **arguments})


def _context(protocol: str) -> AgentContext:
    overrides: dict = {"protocol": protocol}
    if protocol == "agui":
        overrides["transcript_collector"] = TranscriptCollector()
    context = make_context(**overrides)
    context.tool_state["web_records"] = [{"url": _REPORT, "title": "Report", "content": "Report body."}]
    return context


async def _chat(
    context: AgentContext, transport: _TokenStream, sink: _Sink,
) -> tuple[dict, TerminalAnswerStreamer, AgentResult]:
    registry = ToolRegistry()
    registry.register_tool(TaskCompleteTool())
    registry.register_tool(_NoteTool())
    hooks = HookRegistry()

    async def _deliver_file(ctx, next_fn) -> None:
        if "note" in ctx.tool_path:
            await sink.write({"event": "artifact", "data": {"artifactName": "chart.png"}})
        await next_fn()

    hooks.on(HookEvent.POST_TOOL_USE).use(_deliver_file)
    transports = TransportRegistry()
    transports.register("scripted", lambda: transport)
    runtime = AgentRuntime(
        transport_registry=transports, tool_registry=registry, hooks=hooks,
        event_emitter=context.transcript_collector,
    )
    registry.register_tool(AgentTool(
        AgentSpec(
            name="coding_agent", system_prompt="You write and run code.",
            tool_names=["note", "task_complete"],
            model=ModelSpec(provider="scripted", model="scripted-model"), loop=ReActLoop(), max_turns=4,
        ),
        runtime, name="coding_agent", description="Run code.",
        result_note="[NOTE] present in full", direct_answer_note="[DIRECT] write for the user",
    ))
    agent = Agent(
        AgentSpec(
            name="root", system_prompt="You are the root agent.", tool_names=["coding_agent"],
            model=ModelSpec(provider="scripted", model="scripted-model"), loop=ReActLoop(), max_turns=5,
        ),
        runtime,
    )
    collector = CitationCollector(context)
    streamer = TerminalAnswerStreamer(context, collector, sink)
    streamer._emit_interval = 0.0
    async for event in agent.stream(Goal(description="Plot sign-ups")):
        await streamer.on_event(event)
    result = agent.last_stream_result
    completion = await AnswerFinalizer(context, collector).run(
        agent_success=result.success, agent_error=result.error, agent_output=result.output,
        streamed_answer=streamer.streamed_answer, reasoning_turns=streamer.reasoning_turns,
        event_sink=sink, answered_via=result.answered_by,
    )
    return completion, streamer, result


def _direct_script() -> _TokenStream:
    return _TokenStream([
        _tool_turn(_NARRATION, _delegate_call(final=True)),
        _tool_turn(_CHILD_NARRATION, ToolCall(id="n1", name="note", arguments={"text": "numbers"})),
        _turn(_CHILD_ANSWER),
    ])


@pytest.mark.parametrize("protocol", ["legacy", "agui"])
class TestDirectAnswer:
    async def test_parent_model_is_not_called_again_after_the_delegate(self, protocol: str) -> None:
        transport = _direct_script()
        _, _, result = await _chat(_context(protocol), transport, _Sink())

        # parent decision, delegate tool turn, delegate answer: no fourth call to write it up
        assert len(transport.calls) == 3
        assert result.output == _CHILD_ANSWER
        assert result.answered_by == "coding_agent"

    async def test_streamed_text_equals_the_final_output(self, protocol: str) -> None:
        completion, streamer, result = await _chat(_context(protocol), _direct_script(), _Sink())

        assert streamer.streamed_answer == result.output == _CHILD_ANSWER
        assert completion["answeredVia"] == "coding_agent"

    async def test_no_full_text_fallback_chunk_and_no_duplicate(self, protocol: str) -> None:
        sink = _Sink()
        completion, _, _ = await _chat(_context(protocol), _direct_script(), sink)

        if protocol == "legacy":
            chunks = [e["data"] for e in sink.events if e["event"] == "answer_chunk"]
            assert chunks[-1]["chunk"] == ""  # finalizer only corrects state; the text was already live
            assert chunks[-1]["accumulated"] == completion["answer"]
        assert completion["answer"].count("sign-ups beat plan") == 1

    async def test_the_delegates_text_is_live_and_its_narration_is_cleared(self, protocol: str) -> None:
        if protocol != "legacy":
            pytest.skip("answer_chunk frames are the legacy wire")
        sink = _Sink()
        await _chat(_context(protocol), _direct_script(), sink)

        shown = [e["data"]["accumulated"] for e in sink.events if e["event"] == "answer_chunk"]
        narration_at = [i for i, a in enumerate(shown) if _CHILD_NARRATION in a]
        answer_at = [i for i, a in enumerate(shown) if "sign-ups beat plan" in a]
        assert narration_at and answer_at
        cleared_between = [i for i, a in enumerate(shown) if a == "" and narration_at[-1] < i < answer_at[0]]
        assert cleared_between
        # The parent's own preamble is cleared before the delegate's text appears, as for any tool call.
        parent_at = [i for i, a in enumerate(shown) if a == _NARRATION or _NARRATION in a]
        assert any(a == "" for a in shown[parent_at[-1] + 1:narration_at[0]])

    async def test_citations_normalise_like_a_parent_answer(self, protocol: str) -> None:
        completion, _, _ = await _chat(_context(protocol), _direct_script(), _Sink())

        assert "[report]" not in completion["answer"]
        assert "[1]" in completion["answer"]
        assert len(completion["citations"]) == 1

    async def test_files_the_delegate_produced_still_reach_the_user(self, protocol: str) -> None:
        sink = _Sink()
        await _chat(_context(protocol), _direct_script(), sink)

        assert [e["data"]["artifactName"] for e in sink.events if e["event"] == "artifact"] == ["chart.png"]


class TestSavedTranscript:
    async def test_the_answer_is_the_one_final_root_text_part(self) -> None:
        completion, _, _ = await _chat(_context("agui"), _direct_script(), _Sink())

        finals = [p for p in completion["parts"] if p["type"] == "text" and p.get("isFinal")]
        assert [p["content"] for p in finals] == [completion["answer"]]
        sub_agents = [p for p in completion["parts"] if p["type"] == "sub_agent"]
        assert [p["roleName"] for p in sub_agents] == ["coding_agent"]


class TestFallbackToDelegation:
    async def test_needs_input_escalation_leaves_the_answer_to_the_parent(self) -> None:
        escalate = ToolCall(id="t1", name="task_complete", arguments={"output": "partial", "needs_input": "the key"})
        transport = _TokenStream([
            _tool_turn(_NARRATION, _delegate_call(final=True)),
            _tool_turn("", escalate),
            _turn("I need the project key to continue."),
        ])

        completion, streamer, result = await _chat(_context("legacy"), transport, _Sink())

        assert len(transport.calls) == 3
        assert result.output == streamer.streamed_answer == "I need the project key to continue."
        assert result.answered_by is None
        assert "answeredVia" not in completion
