"""Per-run timing log for the agent chat stream (`stream_bridge.py`).

`StageTimer` stops at the first agent event, so on its own it cannot say how
a long chat splits between model turns, tool calls, the pattern-match wait
inside a search, and finishing the answer. `RunTimer` logs those as a second
line when the run ends. These tests drive `run_agent_loop_stream` with a fake
two-turn agent and check that line.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

from app.agent_loop_lib.events.base import AgentEvent, EventType, RunContext
from app.agents.agent_loop.stream_bridge import run_agent_loop_stream
from app.utils.pattern_match import await_pattern_match
from app.utils.stage_timer import RunTimer, StageTimer

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from app.agents.agent_loop.context import AgentContext

_ROOT = RunContext(role_name="pipeshub-agent", model="gpt-4")
_CHILD = _ROOT.child(role_name="internal_exploration_agent")


def _ev(event_type: EventType, payload: dict[str, Any], ctx: RunContext = _ROOT) -> AgentEvent:
    return AgentEvent(event_type=event_type, run_context=ctx, payload=payload)


def _two_turn_agent(context: AgentContext) -> MagicMock:
    """Turn 1 calls a search tool that waits on a pattern match; turn 2
    answers. A sub-agent's turn in the middle must not be counted."""
    agent = MagicMock()
    agent.last_stream_result = None

    async def _fake_stream(goal: object, **kwargs: object) -> AsyncIterator[AgentEvent]:
        yield _ev(EventType.TURN_START, {"turn_index": 0})
        yield _ev(EventType.TEXT_MESSAGE_START, {"turn_index": 0})
        yield _ev(EventType.TEXT_MESSAGE_END, {"turn_index": 0})
        yield _ev(EventType.TOOL_CALL, {"tool": "retrieval", "tool_call_id": "c1"})
        # What the retrieval tool does with the shared tool state.
        pattern_task = asyncio.create_task(asyncio.sleep(0, result=[]))
        await await_pattern_match(
            pattern_task, MagicMock(), run_timer=context.tool_state.get("_run_timer"),
        )
        yield _ev(EventType.TURN_START, {"turn_index": 0}, ctx=_CHILD)
        yield _ev(EventType.TEXT_MESSAGE_END, {"turn_index": 0}, ctx=_CHILD)
        yield _ev(EventType.TOOL_RESULT, {"tool": "retrieval", "tool_call_id": "c1", "is_error": False})
        yield _ev(EventType.TURN_COMPLETE, {"turn_index": 0})
        yield _ev(EventType.TURN_START, {"turn_index": 1})
        yield _ev(EventType.TEXT_MESSAGE_START, {"turn_index": 1})
        yield _ev(EventType.TEXT_MESSAGE_END, {"turn_index": 1})
        yield _ev(EventType.TURN_COMPLETE, {"turn_index": 1})
        yield _ev(EventType.AGENT_COMPLETE, {})
        agent.last_stream_result = MagicMock(
            success=True, error=None, output="42", confidence=None,
            cancelled=False, needs_input=None,
        )

    agent.stream = _fake_stream
    return agent


def _model_error_agent(context: AgentContext) -> MagicMock:
    """The model call raises: `Agent.step` has emitted TURN_START, then
    `fail()` emits ERROR — no TEXT_MESSAGE_END or TURN_COMPLETE follows."""
    agent = MagicMock()
    agent.last_stream_result = None

    async def _fake_stream(goal: object, **kwargs: object) -> AsyncIterator[AgentEvent]:
        yield _ev(EventType.TURN_START, {"turn_index": 0})
        yield _ev(EventType.ERROR, {"error": "LLM call failed"})
        agent.last_stream_result = MagicMock(
            success=False, error="LLM call failed", output=None, confidence=None,
            cancelled=False, needs_input=None,
        )

    agent.stream = _fake_stream
    return agent


def _hanging_tool_agent(context: AgentContext) -> MagicMock:
    """A tool call that is still running when the client goes away."""
    agent = MagicMock()
    agent.last_stream_result = None

    async def _fake_stream(goal: object, **kwargs: object) -> AsyncIterator[AgentEvent]:
        yield _ev(EventType.TURN_START, {"turn_index": 0})
        yield _ev(EventType.TEXT_MESSAGE_END, {"turn_index": 0})
        yield _ev(EventType.TOOL_CALL, {"tool": "retrieval", "tool_call_id": "c1"})
        await asyncio.sleep(30)

    agent.stream = _fake_stream
    return agent


async def _run(
    stage_timer: StageTimer | None,
    *,
    patch_finalizer: bool = True,
    make_agent: Callable[[AgentContext], MagicMock] | None = None,
    disconnect_after_first_frame: bool = False,
) -> MagicMock:
    make_agent = make_agent or _two_turn_agent

    async def _fake_create(  # noqa: ANN202 - mirrors PipesHubAgentFactory.create
        self, context, llm, chat_mode, *, query, model_name="", session_id=None, model_key=None,
    ):
        await context.event_sink.write({"event": "status", "data": {"status": "planning"}})
        return make_agent(context), MagicMock(), MagicMock(), []

    async def _fake_finalizer_run(self, *, event_sink, **kwargs) -> None:
        await event_sink.write({"event": "complete", "data": {"answer": "42"}})

    log = MagicMock()
    with (
        patch(
            "app.modules.agents.qna.chat_state.build_initial_state",
            return_value={"org_id": "org-1", "user_id": "user-1", "query": "hello"},
        ),
        patch(
            "app.utils.connector_instances.fetch_user_connector_instances",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.agents.agent_loop.stream_bridge.PipesHubAgentFactory.create",
            new=_fake_create,
        ),
        patch(
            "app.agents.agent_loop.stream_bridge.AnswerFinalizer.run",
            new=_fake_finalizer_run,
        ) if patch_finalizer else contextlib.nullcontext(),
    ):
        stream = run_agent_loop_stream(
            query_info={"query": "hello", "chatMode": "quick"},
            user_info={"userId": "user-1", "orgId": "org-1"},
            llm=MagicMock(), log=log, retrieval_service=MagicMock(),
            graph_provider=MagicMock(), reranker_service=MagicMock(),
            config_service=MagicMock(), model_name="gpt-4",
            stage_timer=stage_timer,
        )
        if disconnect_after_first_frame:
            await stream.__anext__()
            await asyncio.sleep(0.05)  # let the agent reach its tool call
            await stream.aclose()  # the client goes away
        else:
            _ = [chunk async for chunk in stream]
    return log


def _run_lines(log: MagicMock) -> list[tuple[Any, ...]]:
    return [
        c.args for c in log.info.call_args_list
        if len(c.args) > 1 and c.args[1] == "agent chat run"
    ]


class TestRunTimingLog:
    async def test_two_turn_run_logs_turns_tools_pattern_match_and_finish(self) -> None:
        log = await _run(StageTimer(enabled=True))

        lines = _run_lines(log)
        assert len(lines) == 1
        _fmt, _label, _total, spans, suffix = lines[0]
        names = re.findall(r"(\S+?)=\d+ms", spans)
        assert names == [
            "llm_turn_1", "pattern_match_wait", "tool:retrieval", "llm_turn_2", "finish",
        ]
        # The sub-agent's turn is inside the parent's tool span, not a turn of its own.
        assert "turns=2" in suffix
        assert "tool_calls=1" in suffix

    async def test_first_event_line_is_still_logged(self) -> None:
        log = await _run(StageTimer(enabled=True))
        labels = [c.args[1] for c in log.info.call_args_list if len(c.args) > 1]
        assert "agent chat stream" in labels

    async def test_nothing_logged_when_timing_disabled(self) -> None:
        log = await _run(StageTimer(enabled=False))
        assert _run_lines(log) == []

    async def test_nothing_logged_without_a_stage_timer(self) -> None:
        log = await _run(None)
        assert _run_lines(log) == []


class TestRunTimer:
    def test_disabled_timer_records_nothing(self) -> None:
        timer = RunTimer(enabled=False)
        timer.start("a", "a")
        timer.stop("a")
        timer.record("b", 5.0)
        assert timer.spans == []

    def test_stop_without_start_is_ignored(self) -> None:
        timer = RunTimer(enabled=True)
        timer.stop("missing")
        assert timer.spans == []

    def test_emit_logs_once(self) -> None:
        timer = RunTimer(enabled=True)
        timer.record("tool:retrieval", 12.0)
        log = MagicMock()
        timer.emit(log, "agent chat run")
        timer.emit(log, "agent chat run")
        assert log.info.call_count == 1
        assert "tool:retrieval=12ms" in log.info.call_args.args[3]

    def test_emit_includes_spans_still_open(self) -> None:
        timer = RunTimer(enabled=True)
        timer.start("t1", "llm_turn_1")
        timer.stop("t1")
        timer.start("c1", "tool:retrieval")
        log = MagicMock()
        timer.emit(log, "agent chat run (incomplete)")
        spans = log.info.call_args.args[3]
        assert re.fullmatch(r"llm_turn_1=\d+ms tool:retrieval=\d+ms\(open\)", spans)


class TestRunTimingWhenTheRunFails:
    async def test_failed_run_is_logged_as_incomplete(self) -> None:
        async def _failing_finalizer(self, **kwargs) -> None:
            raise RuntimeError("boom")

        with patch(
            "app.agents.agent_loop.stream_bridge.AnswerFinalizer.run", new=_failing_finalizer,
        ):
            log = await _run(StageTimer(enabled=True), patch_finalizer=False)

        labels = [c.args[1] for c in log.info.call_args_list if len(c.args) > 1]
        assert "agent chat run (incomplete)" in labels
        assert "agent chat run" not in labels


class TestUnfinishedSpansAreStillLogged:
    async def test_model_error_logs_the_open_turn_and_is_incomplete(self) -> None:
        log = await _run(StageTimer(enabled=True), make_agent=_model_error_agent)

        lines = [
            c.args for c in log.info.call_args_list
            if len(c.args) > 1 and str(c.args[1]).startswith("agent chat run")
        ]
        assert len(lines) == 1
        _fmt, label, _total, spans, suffix = lines[0]
        assert label == "agent chat run (incomplete)"
        assert re.search(r"llm_turn_1=\d+ms\(open\)", spans)
        assert "turns=1" in suffix

    async def test_disconnect_mid_tool_logs_the_open_tool(self) -> None:
        log = await _run(
            StageTimer(enabled=True), make_agent=_hanging_tool_agent,
            disconnect_after_first_frame=True,
        )

        lines = [
            c.args for c in log.info.call_args_list
            if len(c.args) > 1 and str(c.args[1]).startswith("agent chat run")
        ]
        assert len(lines) == 1
        _fmt, label, _total, spans, suffix = lines[0]
        assert label == "agent chat run (incomplete)"
        assert re.search(r"llm_turn_1=\d+ms(?!\()", spans)
        assert re.search(r"tool:retrieval=\d+ms\(open\)", spans)
        assert "tool_calls=1" in suffix
