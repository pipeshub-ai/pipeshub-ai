"""Agent-loop events -> PipesHub SSE event format (`sse_emitter.py`) and the
push-to-pull streaming bridge (`stream_bridge.py`) — Phase 7/8 of the
migration, plus the live-answer-streaming fix (`stream_bridge.py` now drives
`agent.stream(goal)` + `agent.last_stream_result` rather than plain
`agent.run(goal)` — see `answer_streamer.py`)."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from app.agent_loop_lib.events.base import AgentEvent, EventType, RunContext, ToolCallStatus
from app.agents.agent_loop.sse_emitter import SSEEventEmitter
from app.agents.agent_loop.stream_bridge import (
    QueueEventSink,
    run_agent_loop_stream,
    sse_queue_maxsize,
    _heartbeat
)

_RUN_CTX = RunContext(role_name="pipeshub-agent", model="gpt-4")


def _event(event_type: EventType, payload: dict[str, Any]) -> AgentEvent:
    return AgentEvent(event_type=event_type, run_context=_RUN_CTX, payload=payload)


def _stream_agent(result: Any, events: list[AgentEvent] | None = None) -> MagicMock:
    """Builds a `MagicMock` agent whose `.stream(goal)` yields `events` (if
    any) then leaves `.last_stream_result` set to `result` — the shape
    `stream_bridge.py::_produce()` drives now (`agent.stream()` +
    `agent.last_stream_result`, not a plain `await agent.run(goal)`)."""
    agent = MagicMock()
    agent.last_stream_result = None

    async def _fake_stream(goal, **kwargs):
        for event in events or []:
            yield event
        agent.last_stream_result = result

    agent.stream = _fake_stream
    return agent


def _slow_stream_agent(result: Any, delay: float) -> MagicMock:
    """Like `_stream_agent`, but `.stream()` sleeps `delay` seconds before
    completing (yielding no events) — used to exercise the `_heartbeat`
    task, which only has something to do while the agent run is between
    real events."""
    agent = MagicMock()
    agent.last_stream_result = None

    async def _fake_stream(goal, **kwargs):
        await asyncio.sleep(delay)
        agent.last_stream_result = result
        return
        yield  # pragma: no cover - unreachable; marks this as an async generator

    agent.stream = _fake_stream
    return agent


class TestQueueEventSink:
    async def test_write_puts_event_on_queue(self) -> None:
        queue: asyncio.Queue = asyncio.Queue()
        sink = QueueEventSink(queue)

        result = await sink.write({"event": "status", "data": {"message": "hi"}})

        assert result is True
        assert queue.qsize() == 1
        assert await queue.get() == {"event": "status", "data": {"message": "hi"}}

    async def test_preserves_fifo_order(self) -> None:
        queue: asyncio.Queue = asyncio.Queue()
        sink = QueueEventSink(queue)

        await sink.write({"event": "a"})
        await sink.write({"event": "b"})

        assert (await queue.get())["event"] == "a"
        assert (await queue.get())["event"] == "b"


class TestSseQueueMaxsize:
    def test_default_is_bounded(self, monkeypatch) -> None:
        monkeypatch.delenv("PIPESHUB_SSE_QUEUE_MAXSIZE", raising=False)
        assert sse_queue_maxsize() == 1000

    def test_reads_env_override(self, monkeypatch) -> None:
        monkeypatch.setenv("PIPESHUB_SSE_QUEUE_MAXSIZE", "42")
        assert sse_queue_maxsize() == 42

    def test_invalid_env_falls_back_to_default(self, monkeypatch) -> None:
        monkeypatch.setenv("PIPESHUB_SSE_QUEUE_MAXSIZE", "not-a-number")
        assert sse_queue_maxsize() == 1000

    def test_non_positive_env_falls_back_to_default(self, monkeypatch) -> None:
        monkeypatch.setenv("PIPESHUB_SSE_QUEUE_MAXSIZE", "0")
        assert sse_queue_maxsize() == 1000


class TestQueueEventSinkCoalescing:
    """Regression coverage for the reduce-request-hotpath todo: token/
    thinking deltas must coalesce (not grow the queue unbounded, and not
    stall the run) once the bounded queue is full, while every other event
    kind keeps strict FIFO ordering and is never dropped or merged."""

    async def _fill_queue(self, queue: "asyncio.Queue[Any]") -> None:
        for i in range(queue.maxsize):
            queue.put_nowait({"event": "filler", "data": {"i": i}})

    async def test_answer_chunk_deltas_merge_while_queue_is_full(self) -> None:
        queue: asyncio.Queue = asyncio.Queue(maxsize=2)
        sink = QueueEventSink(queue)
        await self._fill_queue(queue)

        await sink.write({"event": "answer_chunk", "data": {"chunk": "Hel", "accumulated": "Hel"}})
        await sink.write({"event": "answer_chunk", "data": {"chunk": "lo", "accumulated": "Hello"}})

        # Still full -- both deltas merged into the one pending slot rather
        # than blocking `write()` or growing the queue past maxsize.
        assert queue.qsize() == 2

        await queue.get()  # drain one filler -> room for the merged delta below
        await sink.flush()
        assert queue.qsize() == 2  # remaining filler + the merged delta
        await queue.get()  # remaining filler
        merged = await queue.get()
        assert merged == {"event": "answer_chunk", "data": {"chunk": "Hello", "accumulated": "Hello"}}

    async def test_text_message_content_deltas_key_on_message_id(self) -> None:
        """Two different messages' deltas (e.g. main answer vs. a spawned
        sub-agent's) must never merge into each other even though both use
        the same event name."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=1)
        sink = QueueEventSink(queue)
        await self._fill_queue(queue)

        await sink.write({"event": "TEXT_MESSAGE_CONTENT", "data": {"messageId": "m1", "delta": "A"}})
        # Pending now holds m1's delta (queue full, so it wasn't flushed yet).

        write_task = asyncio.ensure_future(
            sink.write({"event": "TEXT_MESSAGE_CONTENT", "data": {"messageId": "m2", "delta": "B"}})
        )
        await asyncio.sleep(0)
        assert not write_task.done()  # blocked: flushing m1 (different key) needs a freed slot

        await queue.get()  # drain filler -> unblocks flushing m1; m2 becomes the new pending
        await asyncio.wait_for(write_task, timeout=1)

        assert await queue.get() == {"event": "TEXT_MESSAGE_CONTENT", "data": {"messageId": "m1", "delta": "A"}}
        # m2 never merged into m1 -- different messageId keys them apart.
        await sink.flush()
        assert await queue.get() == {"event": "TEXT_MESSAGE_CONTENT", "data": {"messageId": "m2", "delta": "B"}}

    async def test_non_coalescable_event_flushes_pending_delta_first_then_blocks(self) -> None:
        """A tool/lifecycle event must never be silently dropped or
        reordered ahead of an already-pending delta, even under backpressure."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=1)
        sink = QueueEventSink(queue)
        await self._fill_queue(queue)

        await sink.write({"event": "answer_chunk", "data": {"chunk": "a", "accumulated": "a"}})

        write_task = asyncio.ensure_future(
            sink.write({"event": "tool_call_start", "data": {"tool": "web_search"}})
        )
        await asyncio.sleep(0)
        assert not write_task.done()  # blocked: queue still full of filler + pending delta

        # `maxsize=1` means the pending delta and the new event each need
        # their own freed slot -- two drains before `write_task` (flush-
        # pending-then-put-new) can finish.
        await queue.get()  # drain filler -> pending delta flushes into the freed slot
        await queue.get()  # drain the flushed delta -> tool_call_start itself flushes
        await asyncio.wait_for(write_task, timeout=1)
        assert await queue.get() == {"event": "tool_call_start", "data": {"tool": "web_search"}}

    async def test_flush_is_a_noop_with_nothing_pending(self) -> None:
        queue: asyncio.Queue = asyncio.Queue()
        sink = QueueEventSink(queue)
        await sink.flush()
        assert queue.qsize() == 0

    async def test_uncoalescable_events_never_merge(self) -> None:
        queue: asyncio.Queue = asyncio.Queue()
        sink = QueueEventSink(queue)

        await sink.write({"event": "status", "data": {"message": "one"}})
        await sink.write({"event": "status", "data": {"message": "two"}})

        assert queue.qsize() == 2


class TestSSEEventEmitterTranslation:
    async def test_run_started_maps_to_planning_status(self) -> None:
        sink = MagicMock()
        sink.write = AsyncMock(return_value=True)
        emitter = SSEEventEmitter(sink)

        await emitter.emit(_event(EventType.RUN_STARTED, {}))

        sink.write.assert_awaited_once_with(
            {"event": "status", "data": {"status": "planning", "message": "Planning next step..."}}
        )

    async def test_tool_call_start_maps_to_executing_status(self) -> None:
        sink = MagicMock()
        sink.write = AsyncMock(return_value=True)
        emitter = SSEEventEmitter(sink)

        await emitter.emit(_event(EventType.TOOL_CALL_START, {"tool": "jira_search"}))

        sink.write.assert_awaited_once_with(
            {"event": "status", "data": {"status": "executing", "message": "Using jira_search..."}}
        )

    async def test_tool_call_end_success_maps_to_tool_result(self) -> None:
        sink = MagicMock()
        sink.write = AsyncMock(return_value=True)
        emitter = SSEEventEmitter(sink)

        await emitter.emit(_event(EventType.TOOL_CALL_END, {"tool": "jira_search", "content": "3 issues found"}))

        sink.write.assert_awaited_once_with(
            {
                "event": "tool_result",
                "data": {
                    "tool": "jira_search",
                    "result": "3 issues found",
                    "status": "success",
                    "summary": None,
                },
            }
        )

    async def test_tool_call_end_success_includes_result_summary_when_present(self) -> None:
        sink = MagicMock()
        sink.write = AsyncMock(return_value=True)
        emitter = SSEEventEmitter(sink)

        await emitter.emit(
            _event(
                EventType.TOOL_CALL_END,
                {"tool": "jira_search", "content": "3 issues found", "result_summary": "Found 3 issues"},
            )
        )

        sink.write.assert_awaited_once_with(
            {
                "event": "tool_result",
                "data": {
                    "tool": "jira_search",
                    "result": "3 issues found",
                    "status": "success",
                    "summary": "Found 3 issues",
                },
            }
        )

    async def test_tool_call_end_error_maps_to_error_status(self) -> None:
        sink = MagicMock()
        sink.write = AsyncMock(return_value=True)
        emitter = SSEEventEmitter(sink)

        await emitter.emit(
            _event(EventType.TOOL_CALL_END, {"tool": "jira_search", "content": None, "is_error": True})
        )

        sink.write.assert_awaited_once_with(
            {
                "event": "tool_result",
                "data": {"tool": "jira_search", "result": None, "status": "error", "summary": None},
            }
        )

    async def test_tool_blocked_alias_maps_to_error_tool_result(self) -> None:
        """`TOOL_CALL_END` aliased from `TOOL_BLOCKED` carries an explicit
        `status: ToolCallStatus.BLOCKED` — see `sse_emitter.py`'s module
        docstring."""
        sink = MagicMock()
        sink.write = AsyncMock(return_value=True)
        emitter = SSEEventEmitter(sink)

        await emitter.emit(
            _event(EventType.TOOL_CALL_END, {
                "tool": "jira_search", "reason": "blocked after 3 failures", "status": ToolCallStatus.BLOCKED,
            })
        )

        sink.write.assert_awaited_once_with(
            {"event": "tool_result", "data": {"tool": "jira_search", "result": "blocked after 3 failures", "status": "error"}}
        )

    async def test_unmapped_event_types_are_silently_dropped(self) -> None:
        sink = MagicMock()
        sink.write = AsyncMock(return_value=True)
        emitter = SSEEventEmitter(sink)

        await emitter.emit(_event(EventType.RUN_FINISHED, {}))
        await emitter.emit(_event(EventType.TEXT_MESSAGE_CONTENT, {"delta": "hi"}))

        sink.write.assert_not_awaited()


_CHILD_CTX = _RUN_CTX.child(role_name="internal_exploration_agent", model="gpt-4")


def _child_event(event_type: EventType, payload: dict[str, Any]) -> AgentEvent:
    return AgentEvent(event_type=event_type, run_context=_CHILD_CTX, payload=payload)


class TestSSEEventEmitterChildVisibility:
    """A sub-agent's events (`run_context.parent_run_id is not None`) must
    be distinguishable from the top-level agent's own — mirrors
    `AGUIEventEmitter`'s `parentRunId` nesting, translated into the legacy
    flat `{event, data}` shape instead of AG-UI frames (see `sse_emitter.py`
    module docstring). Every root-level case above is unaffected — asserted
    again here as a contrast, not just by omission."""

    async def test_child_run_started_is_not_a_second_planning_status(self) -> None:
        sink = MagicMock()
        sink.write = AsyncMock(return_value=True)
        emitter = SSEEventEmitter(sink)

        await emitter.emit(_child_event(EventType.RUN_STARTED, {}))

        sink.write.assert_awaited_once_with({
            "event": "status",
            "data": {
                "status": "sub_agent_started",
                "message": "Delegating to internal_exploration_agent...",
                "agent": "internal_exploration_agent",
                "run_id": _CHILD_CTX.run_id,
                "parent_run_id": _RUN_CTX.run_id,
            },
        })

    async def test_child_run_finished_is_surfaced_unlike_root(self) -> None:
        """Root RUN_FINISHED is silently dropped (see
        `test_unmapped_event_types_are_silently_dropped`) — a child's is
        the only thing that closes its nested activity for a legacy client."""
        sink = MagicMock()
        sink.write = AsyncMock(return_value=True)
        emitter = SSEEventEmitter(sink)

        await emitter.emit(_child_event(EventType.RUN_FINISHED, {}))

        sink.write.assert_awaited_once_with({
            "event": "status",
            "data": {
                "status": "sub_agent_finished",
                "message": "internal_exploration_agent finished.",
                "agent": "internal_exploration_agent",
                "run_id": _CHILD_CTX.run_id,
                "parent_run_id": _RUN_CTX.run_id,
            },
        })

    async def test_child_run_error_is_surfaced_unlike_root(self) -> None:
        sink = MagicMock()
        sink.write = AsyncMock(return_value=True)
        emitter = SSEEventEmitter(sink)

        await emitter.emit(_child_event(EventType.RUN_ERROR, {"error": "connector timed out"}))

        sink.write.assert_awaited_once_with({
            "event": "status",
            "data": {
                "status": "sub_agent_error",
                "message": "internal_exploration_agent failed: connector timed out",
                "agent": "internal_exploration_agent",
                "run_id": _CHILD_CTX.run_id,
                "parent_run_id": _RUN_CTX.run_id,
            },
        })

    async def test_child_tool_call_start_names_the_sub_agent(self) -> None:
        sink = MagicMock()
        sink.write = AsyncMock(return_value=True)
        emitter = SSEEventEmitter(sink)

        await emitter.emit(_child_event(EventType.TOOL_CALL_START, {"tool": "jira_search"}))

        sink.write.assert_awaited_once_with({
            "event": "status",
            "data": {
                "status": "executing",
                "message": "internal_exploration_agent using jira_search...",
                "agent": "internal_exploration_agent",
                "run_id": _CHILD_CTX.run_id,
                "parent_run_id": _RUN_CTX.run_id,
            },
        })

    async def test_child_tool_call_end_includes_agent_and_run_ids(self) -> None:
        sink = MagicMock()
        sink.write = AsyncMock(return_value=True)
        emitter = SSEEventEmitter(sink)

        await emitter.emit(
            _child_event(EventType.TOOL_CALL_END, {"tool": "jira_search", "content": "3 issues found"})
        )

        sink.write.assert_awaited_once_with({
            "event": "tool_result",
            "data": {
                "tool": "jira_search",
                "result": "3 issues found",
                "status": "success",
                "summary": None,
                "agent": "internal_exploration_agent",
                "run_id": _CHILD_CTX.run_id,
                "parent_run_id": _RUN_CTX.run_id,
            },
        })

    async def test_child_tool_blocked_alias_includes_agent_and_run_ids(self) -> None:
        sink = MagicMock()
        sink.write = AsyncMock(return_value=True)
        emitter = SSEEventEmitter(sink)

        await emitter.emit(_child_event(EventType.TOOL_CALL_END, {
            "tool": "jira_search", "reason": "blocked after 3 failures", "status": ToolCallStatus.BLOCKED,
        }))

        sink.write.assert_awaited_once_with({
            "event": "tool_result",
            "data": {
                "tool": "jira_search",
                "result": "blocked after 3 failures",
                "status": "error",
                "agent": "internal_exploration_agent",
                "run_id": _CHILD_CTX.run_id,
                "parent_run_id": _RUN_CTX.run_id,
            },
        })

    async def test_root_events_are_completely_unaffected_by_child_support(self) -> None:
        """Contrast case: the exact same event types on the ROOT context
        must keep producing the EXACT pre-existing shape (no `agent`/
        `run_id`/`parent_run_id` keys) — this is the back-compat guarantee
        every test in `TestSSEEventEmitterTranslation` above already pins,
        restated here next to the child cases for visibility."""
        sink = MagicMock()
        sink.write = AsyncMock(return_value=True)
        emitter = SSEEventEmitter(sink)

        await emitter.emit(_event(EventType.TOOL_CALL_START, {"tool": "jira_search"}))

        sink.write.assert_awaited_once_with(
            {"event": "status", "data": {"status": "executing", "message": "Using jira_search..."}}
        )


class TestRunAgentLoopStream:
    @staticmethod
    def _base_kwargs() -> dict[str, Any]:
        return {
            "query_info": {"query": "hello", "chatMode": "react"},
            "user_info": {"userId": "user-1", "orgId": "org-1"},
            "llm": MagicMock(),
            "log": MagicMock(),
            "retrieval_service": MagicMock(),
            "graph_provider": MagicMock(),
            "reranker_service": MagicMock(),
            "config_service": MagicMock(),
        }

    async def test_build_initial_state_failure_yields_error_event(self) -> None:
        with (
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                side_effect=RuntimeError("boom"),
            ),
            patch(
                "app.utils.execute_query.has_sql_connector_configured",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.utils.fetch_slack_thread.has_slack_connector_configured",
                new=AsyncMock(return_value=False),
            ),
        ):
            events = [chunk async for chunk in run_agent_loop_stream(**self._base_kwargs())]

        assert len(events) == 1
        assert events[0].startswith("event: error\n")
        # Raw exception text is classified (`error_classification.py`) into a
        # stable `type` + friendly `message` rather than leaked verbatim.
        payload = json.loads(events[0].split("data: ", 1)[1].strip())
        assert payload["type"] == "unknown"
        assert "boom" not in payload["message"]

    async def test_successful_run_streams_events_then_completes(self) -> None:
        async def _fake_create(self, context, llm, chat_mode, *, query, model_name="", session_id=None):
            await context.event_sink.write({"event": "status", "data": {"status": "planning", "message": "..."}})
            agent = _stream_agent(MagicMock(success=True, error=None))
            return agent, MagicMock(), MagicMock(), []

        async def _fake_finalizer_run(self, *, agent_success, agent_error, event_sink, agent_output=None, streamed_answer="", reasoning_turns=None):
            await event_sink.write({"event": "complete", "data": {"answer": "42"}})
            return {"answer": "42"}

        with (
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "hello"},
            ),
            patch(
                "app.utils.execute_query.has_sql_connector_configured",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.utils.fetch_slack_thread.has_slack_connector_configured",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.agents.agent_loop.stream_bridge.PipesHubAgentFactory.create",
                new=_fake_create,
            ),
            patch(
                "app.agents.agent_loop.stream_bridge.AnswerFinalizer.run",
                new=_fake_finalizer_run,
            ),
        ):
            events = [chunk async for chunk in run_agent_loop_stream(**self._base_kwargs())]

        assert len(events) == 2
        assert events[0].startswith("event: status\n")
        assert events[1].startswith("event: complete\n")
        assert json.loads(events[1].split("data: ", 1)[1].strip()) == {"answer": "42"}

    async def test_terminal_answer_streamed_live_before_finalizer_runs(self) -> None:
        """`_produce()` must drive the agent via `agent.stream(goal)` and
        feed every yielded event through `TerminalAnswerStreamer` — a
        `TEXT_MESSAGE_CONTENT` delta must reach the client as a live
        `answer_chunk`, BEFORE the finalizer's own `complete` event."""
        events_to_yield = [
            _event(EventType.TEXT_MESSAGE_START, {}),
            _event(EventType.TEXT_MESSAGE_CONTENT, {"delta": "The answer is 42."}),
            _event(EventType.AGENT_COMPLETE, {"output": "The answer is 42."}),
        ]

        captured_streamed_answer: dict[str, str] = {}

        async def _fake_create(self, context, llm, chat_mode, *, query, model_name="", session_id=None):
            agent = _stream_agent(
                MagicMock(success=True, error=None, output="The answer is 42."),
                events=events_to_yield,
            )
            return agent, MagicMock(), MagicMock(), []

        async def _fake_finalizer_run(self, *, agent_success, agent_error, event_sink, agent_output=None, streamed_answer="", reasoning_turns=None):
            captured_streamed_answer["value"] = streamed_answer
            await event_sink.write({"event": "complete", "data": {"answer": agent_output}})
            return {"answer": agent_output}

        with (
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "hello"},
            ),
            patch(
                "app.utils.execute_query.has_sql_connector_configured",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.utils.fetch_slack_thread.has_slack_connector_configured",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.agents.agent_loop.stream_bridge.PipesHubAgentFactory.create",
                new=_fake_create,
            ),
            patch(
                "app.agents.agent_loop.stream_bridge.AnswerFinalizer.run",
                new=_fake_finalizer_run,
            ),
        ):
            events = [chunk async for chunk in run_agent_loop_stream(**self._base_kwargs())]

        event_names = [chunk.split("\n", 1)[0] for chunk in events]
        assert event_names == ["event: answer_chunk", "event: complete"]
        chunk_payload = json.loads(events[0].split("data: ", 1)[1].strip())
        assert chunk_payload["accumulated"] == "The answer is 42."
        assert captured_streamed_answer["value"] == "The answer is 42."

    async def test_clarifying_questions_short_circuit_skips_agent_run(self) -> None:
        """When `factory.create()` returns non-empty `clarifying_questions`,
        `_produce()` must call `emit_pre_run_clarification()` instead of
        `agent.stream()`/`AnswerFinalizer.run()` — see `clarification.py`."""
        from app.agent_loop_lib.core.types import Goal
        from app.agents.actions.internal_tools.intrim_tools import AskUserQuestionItemInput

        question = AskUserQuestionItemInput(
            question="Which project should this apply to?",
            options=[{"label": "Project A"}, {"label": "Project B"}, {"label": "Project C"}],
            multiSelect=False,
        )
        goal = Goal(description="do the ambiguous thing")
        agent_stream = MagicMock(side_effect=AssertionError("agent.stream() must not be called"))
        finalizer_run = AsyncMock(side_effect=AssertionError("AnswerFinalizer.run() must not be called"))

        async def _fake_create(self, context, llm, chat_mode, *, query, model_name="", session_id=None):
            agent = MagicMock()
            agent.stream = agent_stream
            return agent, MagicMock(), goal, [question]

        with (
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={
                    "org_id": "org-1", "user_id": "user-1", "query": "hello", "has_ui_client": True,
                },
            ),
            patch(
                "app.utils.execute_query.has_sql_connector_configured",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.utils.fetch_slack_thread.has_slack_connector_configured",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.agents.agent_loop.stream_bridge.PipesHubAgentFactory.create",
                new=_fake_create,
            ),
            patch(
                "app.agents.agent_loop.stream_bridge.AnswerFinalizer.run",
                new=finalizer_run,
            ),
        ):
            events = [chunk async for chunk in run_agent_loop_stream(**self._base_kwargs())]

        agent_stream.assert_not_called()
        finalizer_run.assert_not_called()
        event_names = [chunk.split("\n", 1)[0] for chunk in events]
        assert "event: ask_user_question" in event_names
        assert event_names[-1] == "event: complete"
        complete_payload = json.loads(events[-1].split("data: ", 1)[1].strip())
        assert complete_payload["answerMatchType"] == "Clarification Needed"

    async def test_agent_run_failure_emits_error_event(self) -> None:
        async def _fake_create(self, context, llm, chat_mode, *, query, model_name="", session_id=None):
            raise RuntimeError("transport exploded")

        with (
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "hello"},
            ),
            patch(
                "app.utils.execute_query.has_sql_connector_configured",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.utils.fetch_slack_thread.has_slack_connector_configured",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.agents.agent_loop.stream_bridge.PipesHubAgentFactory.create",
                new=_fake_create,
            ),
        ):
            events = [chunk async for chunk in run_agent_loop_stream(**self._base_kwargs())]

        assert len(events) == 1
        assert events[0].startswith("event: error\n")
        payload = json.loads(events[0].split("data: ", 1)[1].strip())
        assert payload["type"] == "unknown"
        assert "transport exploded" not in payload["message"]

    async def test_agent_run_failure_classifies_rate_limit_error(self) -> None:
        """A 429 raised anywhere in `_produce()` must surface with
        `type: "rate_limit"` and a friendly message — see
        `error_classification.py`."""
        async def _fake_create(self, context, llm, chat_mode, *, query, model_name="", session_id=None):
            raise RuntimeError("Error code: 429 - rate limit exceeded")

        with (
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "hello"},
            ),
            patch(
                "app.utils.execute_query.has_sql_connector_configured",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.utils.fetch_slack_thread.has_slack_connector_configured",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.agents.agent_loop.stream_bridge.PipesHubAgentFactory.create",
                new=_fake_create,
            ),
        ):
            events = [chunk async for chunk in run_agent_loop_stream(**self._base_kwargs())]

        assert len(events) == 1
        payload = json.loads(events[0].split("data: ", 1)[1].strip())
        assert payload["type"] == "rate_limit"
        assert payload["message"] == "The AI service is currently rate limited. Please try again in a moment."

    async def test_sandbox_manager_destroyed_on_successful_completion(self) -> None:
        """Phase 8's `_produce()` `finally` block must tear down the
        per-request `SandboxManager` whenever one was stashed on the
        context — normal completion, agent failure, and (separately,
        see below) client-disconnect cancellation alike."""
        sandbox_manager = MagicMock()
        sandbox_manager.destroy_all = AsyncMock()

        async def _fake_create(self, context, llm, chat_mode, *, query, model_name="", session_id=None):
            context.sandbox_manager = sandbox_manager
            agent = _stream_agent(MagicMock(success=True, error=None))
            return agent, MagicMock(), MagicMock(), []

        async def _fake_finalizer_run(self, *, agent_success, agent_error, event_sink, agent_output=None, streamed_answer="", reasoning_turns=None):
            await event_sink.write({"event": "complete", "data": {"answer": "42"}})
            return {"answer": "42"}

        with (
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "hello"},
            ),
            patch(
                "app.utils.execute_query.has_sql_connector_configured",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.utils.fetch_slack_thread.has_slack_connector_configured",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.agents.agent_loop.stream_bridge.PipesHubAgentFactory.create",
                new=_fake_create,
            ),
            patch(
                "app.agents.agent_loop.stream_bridge.AnswerFinalizer.run",
                new=_fake_finalizer_run,
            ),
        ):
            events = [chunk async for chunk in run_agent_loop_stream(**self._base_kwargs())]

        assert len(events) == 1
        sandbox_manager.destroy_all.assert_awaited_once()

    async def test_sandbox_manager_destroyed_even_when_agent_run_fails(self) -> None:
        sandbox_manager = MagicMock()
        sandbox_manager.destroy_all = AsyncMock()

        async def _fake_create(self, context, llm, chat_mode, *, query, model_name="", session_id=None):
            context.sandbox_manager = sandbox_manager
            raise RuntimeError("boom")

        with (
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "hello"},
            ),
            patch(
                "app.utils.execute_query.has_sql_connector_configured",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.utils.fetch_slack_thread.has_slack_connector_configured",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.agents.agent_loop.stream_bridge.PipesHubAgentFactory.create",
                new=_fake_create,
            ),
        ):
            events = [chunk async for chunk in run_agent_loop_stream(**self._base_kwargs())]

        assert len(events) == 1
        assert events[0].startswith("event: error\n")
        sandbox_manager.destroy_all.assert_awaited_once()

    async def test_sandbox_cleanup_failure_does_not_prevent_stream_completion(self) -> None:
        """A broken `destroy_all()` must be swallowed (logged), not
        propagate and break the SSE stream for the client."""
        sandbox_manager = MagicMock()
        sandbox_manager.destroy_all = AsyncMock(side_effect=RuntimeError("teardown exploded"))

        async def _fake_create(self, context, llm, chat_mode, *, query, model_name="", session_id=None):
            context.sandbox_manager = sandbox_manager
            agent = _stream_agent(MagicMock(success=True, error=None))
            return agent, MagicMock(), MagicMock(), []

        async def _fake_finalizer_run(self, *, agent_success, agent_error, event_sink, agent_output=None, streamed_answer="", reasoning_turns=None):
            await event_sink.write({"event": "complete", "data": {"answer": "42"}})
            return {"answer": "42"}

        with (
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "hello"},
            ),
            patch(
                "app.utils.execute_query.has_sql_connector_configured",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.utils.fetch_slack_thread.has_slack_connector_configured",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.agents.agent_loop.stream_bridge.PipesHubAgentFactory.create",
                new=_fake_create,
            ),
            patch(
                "app.agents.agent_loop.stream_bridge.AnswerFinalizer.run",
                new=_fake_finalizer_run,
            ),
        ):
            events = [chunk async for chunk in run_agent_loop_stream(**self._base_kwargs())]

        assert len(events) == 1
        assert events[0].startswith("event: complete\n")
        sandbox_manager.destroy_all.assert_awaited_once()

    async def test_detached_and_orphaned_spawn_tasks_are_cancelled_before_sandbox_teardown(self) -> None:
        """Regression for the disconnect-cancellation fix: `_produce()`'s
        `finally` must cancel+await any still-running `agent._detached_tasks`
        / `agent._pending_spawn_tasks` entries BEFORE destroying the
        sandbox — otherwise a still-running child can execute against an
        already-destroyed sandbox."""
        sandbox_manager = MagicMock()
        destroy_order: list[str] = []
        detached_started = asyncio.Event()
        pending_started = asyncio.Event()

        async def _slow_detached() -> None:
            detached_started.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                destroy_order.append("detached_cancelled")
                raise

        async def _slow_pending() -> None:
            pending_started.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                destroy_order.append("pending_cancelled")
                raise

        async def _destroy_all() -> None:
            destroy_order.append("sandbox_destroyed")

        sandbox_manager.destroy_all = AsyncMock(side_effect=_destroy_all)

        async def _fake_create(self, context, llm, chat_mode, *, query, model_name="", session_id=None):
            context.sandbox_manager = sandbox_manager
            agent = _stream_agent(MagicMock(success=True, error=None))
            agent._detached_tasks = {asyncio.create_task(_slow_detached())}
            agent._pending_spawn_tasks = {"c1": asyncio.create_task(_slow_pending())}
            # Let the event loop actually start both background tasks (past
            # their first await point) before `_produce()` reaches its
            # `finally` — otherwise cancelling a task that never got its
            # first scheduling turn skips its body (including the except
            # block) entirely, which would make this test's ordering
            # assertion vacuous rather than a real race.
            await detached_started.wait()
            await pending_started.wait()
            return agent, MagicMock(), MagicMock(), []

        async def _fake_finalizer_run(self, *, agent_success, agent_error, event_sink, agent_output=None, streamed_answer="", reasoning_turns=None):
            await event_sink.write({"event": "complete", "data": {"answer": "42"}})
            return {"answer": "42"}

        with (
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "hello"},
            ),
            patch(
                "app.utils.execute_query.has_sql_connector_configured",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.utils.fetch_slack_thread.has_slack_connector_configured",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.agents.agent_loop.stream_bridge.PipesHubAgentFactory.create",
                new=_fake_create,
            ),
            patch(
                "app.agents.agent_loop.stream_bridge.AnswerFinalizer.run",
                new=_fake_finalizer_run,
            ),
        ):
            events = [chunk async for chunk in run_agent_loop_stream(**self._base_kwargs())]

        assert len(events) == 1
        # Both background tasks must have been cancelled, and BOTH before
        # the sandbox was destroyed — never the other way around.
        assert destroy_order[-1] == "sandbox_destroyed"
        assert set(destroy_order[:-1]) == {"detached_cancelled", "pending_cancelled"}
        sandbox_manager.destroy_all.assert_awaited_once()

    async def test_no_sandbox_manager_is_a_no_op(self) -> None:
        """When code execution isn't enabled for the request,
        `context.sandbox_manager` stays `None` — the cleanup block must
        skip cleanly rather than erroring on a missing manager."""
        async def _fake_create(self, context, llm, chat_mode, *, query, model_name="", session_id=None):
            assert context.sandbox_manager is None
            agent = _stream_agent(MagicMock(success=True, error=None))
            return agent, MagicMock(), MagicMock(), []

        async def _fake_finalizer_run(self, *, agent_success, agent_error, event_sink, agent_output=None, streamed_answer="", reasoning_turns=None):
            await event_sink.write({"event": "complete", "data": {"answer": "42"}})
            return {"answer": "42"}

        with (
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "hello"},
            ),
            patch(
                "app.utils.execute_query.has_sql_connector_configured",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.utils.fetch_slack_thread.has_slack_connector_configured",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.agents.agent_loop.stream_bridge.PipesHubAgentFactory.create",
                new=_fake_create,
            ),
            patch(
                "app.agents.agent_loop.stream_bridge.AnswerFinalizer.run",
                new=_fake_finalizer_run,
            ),
        ):
            events = [chunk async for chunk in run_agent_loop_stream(**self._base_kwargs())]

        assert len(events) == 1
        assert events[0].startswith("event: complete\n")


class TestHeartbeat:
    """Task 6: `_heartbeat()` keeps AG-UI SSE connections alive during long
    sub-agent tool executions by putting periodic `HEARTBEAT` frames on the
    shared queue — see `stream_bridge.py`'s module docstring and
    `_heartbeat`'s own docstring."""

    async def test_heartbeat_puts_heartbeat_frames_on_queue(self) -> None:
        queue: asyncio.Queue = asyncio.Queue()
        task = asyncio.create_task(_heartbeat(queue, interval=0.01))
        await asyncio.sleep(0.05)  # enough for ~4 heartbeats
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

        assert queue.qsize() >= 2  # at least a couple fired
        item = await queue.get()
        assert item["event"] == "HEARTBEAT"
        assert item["data"]["type"] == "HEARTBEAT"

    async def test_no_heartbeat_events_for_legacy_protocol(self) -> None:
        """Default `protocol="legacy"` must not start the heartbeat task at
        all — only AG-UI clients understand a `HEARTBEAT` frame."""
        async def _fake_create(self, context, llm, chat_mode, *, query, model_name="", session_id=None):
            agent = _stream_agent(MagicMock(success=True, error=None))
            return agent, MagicMock(), MagicMock(), []

        async def _fake_finalizer_run(self, *, agent_success, agent_error, event_sink, agent_output=None, streamed_answer="", reasoning_turns=None):
            await event_sink.write({"event": "complete", "data": {"answer": "42"}})
            return {"answer": "42"}

        with (
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "hello"},
            ),
            patch(
                "app.utils.execute_query.has_sql_connector_configured",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.utils.fetch_slack_thread.has_slack_connector_configured",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.agents.agent_loop.stream_bridge.PipesHubAgentFactory.create",
                new=_fake_create,
            ),
            patch(
                "app.agents.agent_loop.stream_bridge.AnswerFinalizer.run",
                new=_fake_finalizer_run,
            ),
        ):
            events = [
                chunk
                async for chunk in run_agent_loop_stream(**TestRunAgentLoopStream._base_kwargs())
            ]

        assert not any(chunk.startswith("event: HEARTBEAT") for chunk in events)
        assert events[-1].startswith("event: complete\n")

    async def test_heartbeat_started_for_agui_protocol(self) -> None:
        """`protocol="agui"` must start the heartbeat task, and it must fire
        while the agent run is still in progress (before the terminal
        `complete` event)."""
        async def _fast_heartbeat(queue: "asyncio.Queue[Any]", interval: float = 15.0) -> None:
            # Patched in place of the real `_heartbeat` so the test doesn't
            # need to wait out the real 15s interval.
            await _heartbeat(queue, interval=0.01)

        async def _fake_create(self, context, llm, chat_mode, *, query, model_name="", session_id=None):
            agent = _slow_stream_agent(MagicMock(success=True, error=None, output="done"), delay=0.05)
            return agent, MagicMock(), MagicMock(), []

        async def _fake_finalizer_run(self, *, agent_success, agent_error, event_sink, agent_output=None, streamed_answer="", reasoning_turns=None):
            await event_sink.write({"event": "complete", "data": {"answer": "42"}})
            return {"answer": "42"}

        with (
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "hello"},
            ),
            patch(
                "app.utils.execute_query.has_sql_connector_configured",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.utils.fetch_slack_thread.has_slack_connector_configured",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.agents.agent_loop.stream_bridge.PipesHubAgentFactory.create",
                new=_fake_create,
            ),
            patch(
                "app.agents.agent_loop.stream_bridge.AnswerFinalizer.run",
                new=_fake_finalizer_run,
            ),
            patch(
                "app.agents.agent_loop.stream_bridge._heartbeat",
                new=_fast_heartbeat,
            ),
        ):
            events = [
                chunk
                async for chunk in run_agent_loop_stream(
                    protocol="agui", **TestRunAgentLoopStream._base_kwargs()
                )
            ]

        assert any(chunk.startswith("event: HEARTBEAT") for chunk in events)
        assert events[-1].startswith("event: complete\n")

    async def test_heartbeat_cancelled_cleanly_on_stream_end(self) -> None:
        """Once the stream ends, the heartbeat task must be cancelled and
        awaited (not leaked) — `run_agent_loop_stream`'s `finally` block
        gathers `[producer, heartbeat]` before the generator returns."""
        async def _fake_create(self, context, llm, chat_mode, *, query, model_name="", session_id=None):
            agent = _stream_agent(MagicMock(success=True, error=None))
            return agent, MagicMock(), MagicMock(), []

        async def _fake_finalizer_run(self, *, agent_success, agent_error, event_sink, agent_output=None, streamed_answer="", reasoning_turns=None):
            await event_sink.write({"event": "complete", "data": {"answer": "42"}})
            return {"answer": "42"}

        with (
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "hello"},
            ),
            patch(
                "app.utils.execute_query.has_sql_connector_configured",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.utils.fetch_slack_thread.has_slack_connector_configured",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.agents.agent_loop.stream_bridge.PipesHubAgentFactory.create",
                new=_fake_create,
            ),
            patch(
                "app.agents.agent_loop.stream_bridge.AnswerFinalizer.run",
                new=_fake_finalizer_run,
            ),
        ):
            tasks_before = asyncio.all_tasks()
            events = [
                chunk
                async for chunk in run_agent_loop_stream(
                    protocol="agui", **TestRunAgentLoopStream._base_kwargs()
                )
            ]
            tasks_after = asyncio.all_tasks()

        assert events[-1].startswith("event: complete\n")
        # The generator's `finally` block already awaited both background
        # tasks before yielding StopAsyncIteration, so nothing new is left
        # running by the time the comprehension above finishes.
        leaked_tasks = tasks_after - tasks_before
        assert all(task.done() for task in leaked_tasks)
