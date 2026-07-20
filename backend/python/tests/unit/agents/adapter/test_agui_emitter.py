"""`AGUIEventEmitter` (AG-UI migration plan, Phase 1b/1d) — the `EventEmitter`
sibling of `SSEEventEmitter` (see `test_sse_bridge.py`'s
`TestSSEEventEmitterTranslation` for the legacy-path equivalent) that
translates `AgentEvent`s into AG-UI-shaped `{"event", "data"}` frames,
including nested sub-agent run lifecycle via `run_context.parent_run_id`.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from app.agent_loop_lib.events.base import AgentEvent, EventType, RunContext, ToolCallStatus
from app.agents.agent_loop.protocol.agui_emitter import AGUIEventEmitter

_ROOT_CTX = RunContext(role_name="pipeshub-agent", model="gpt-4")
_CHILD_CTX = _ROOT_CTX.child(role_name="internal_exploration_agent", model="gpt-4")


def _event(event_type: EventType, payload: dict[str, Any], run_context: RunContext = _ROOT_CTX) -> AgentEvent:
    return AgentEvent(event_type=event_type, run_context=run_context, payload=payload)


def _make_emitter() -> tuple[AGUIEventEmitter, MagicMock]:
    sink = MagicMock()
    sink.write = AsyncMock(return_value=True)
    return AGUIEventEmitter(sink, thread_id="thread-1"), sink


class TestRootRunLifecycle:
    async def test_run_started_maps_to_run_started_with_thread_id(self) -> None:
        emitter, sink = _make_emitter()

        await emitter.emit(_event(EventType.RUN_STARTED, {}))

        sink.write.assert_awaited_once()
        frame = sink.write.await_args.args[0]
        assert frame["event"] == "RUN_STARTED"
        assert frame["data"]["type"] == "RUN_STARTED"
        assert frame["data"]["threadId"] == "thread-1"
        assert frame["data"]["runId"] == _ROOT_CTX.run_id

    async def test_root_run_finished_is_suppressed(self) -> None:
        """Superseded by `AnswerFinalizer`'s authoritative STATE_SNAPSHOT +
        RUN_FINISHED (via `AGUIFormatter.answer_final`) — emitting it here
        too would double-signal completion before the final answer exists."""
        emitter, sink = _make_emitter()

        await emitter.emit(_event(EventType.RUN_FINISHED, {}))

        sink.write.assert_not_awaited()

    async def test_root_run_error_is_suppressed(self) -> None:
        """Graceful root failures are RUN_FINISHED (via AnswerFinalizer's
        error path); only child failures surface RUN_ERROR from this emitter."""
        emitter, sink = _make_emitter()

        await emitter.emit(_event(EventType.RUN_ERROR, {"error": "boom"}))

        sink.write.assert_not_awaited()


class TestChildRunLifecycle:
    async def test_child_run_started_emits_nested_step_and_run(self) -> None:
        emitter, sink = _make_emitter()

        await emitter.emit(_event(EventType.RUN_STARTED, {}, run_context=_CHILD_CTX))

        assert sink.write.await_count == 2
        step_frame, run_frame = (call.args[0] for call in sink.write.await_args_list)
        assert step_frame["event"] == "STEP_STARTED"
        assert step_frame["data"]["stepName"] == "sub_agent:internal_exploration_agent"
        assert step_frame["data"]["runId"] == _ROOT_CTX.run_id
        assert run_frame["event"] == "RUN_STARTED"
        assert run_frame["data"]["runId"] == _CHILD_CTX.run_id
        assert run_frame["data"]["parentRunId"] == _ROOT_CTX.run_id

    async def test_child_run_finished_emits_nested_finish_and_step_finished(self) -> None:
        emitter, sink = _make_emitter()

        await emitter.emit(_event(EventType.RUN_FINISHED, {}, run_context=_CHILD_CTX))

        assert sink.write.await_count == 2
        run_frame, step_frame = (call.args[0] for call in sink.write.await_args_list)
        assert run_frame["event"] == "RUN_FINISHED"
        assert run_frame["data"]["runId"] == _CHILD_CTX.run_id
        assert run_frame["data"]["parentRunId"] == _ROOT_CTX.run_id
        assert step_frame["event"] == "STEP_FINISHED"

    async def test_child_run_error_is_not_suppressed(self) -> None:
        """Unlike the root case, a child has nothing else to close its
        nested run — its RUN_ERROR must actually reach the wire."""
        emitter, sink = _make_emitter()

        await emitter.emit(_event(EventType.RUN_ERROR, {"error": "tool crashed"}, run_context=_CHILD_CTX))

        sink.write.assert_awaited_once()
        frame = sink.write.await_args.args[0]
        assert frame["event"] == "RUN_ERROR"
        assert frame["data"]["parentRunId"] == _ROOT_CTX.run_id
        assert frame["data"]["message"] == "tool crashed"


class TestToolCallTranslation:
    async def test_tool_call_start_emits_start_and_args(self) -> None:
        emitter, sink = _make_emitter()

        await emitter.emit(
            _event(EventType.TOOL_CALL_START, {"tool": "jira_search", "args": {"q": "bug"}, "tool_call_id": "call-1"})
        )

        assert sink.write.await_count == 2
        start_frame, args_frame = (call.args[0] for call in sink.write.await_args_list)
        assert start_frame["event"] == "TOOL_CALL_START"
        assert start_frame["data"]["toolCallId"] == "call-1"
        assert start_frame["data"]["toolCallName"] == "jira_search"
        assert args_frame["event"] == "TOOL_CALL_ARGS"
        assert json.loads(args_frame["data"]["delta"]) == {"q": "bug"}

    async def test_tool_call_end_success_emits_end_and_result(self) -> None:
        emitter, sink = _make_emitter()

        await emitter.emit(
            _event(EventType.TOOL_CALL_END, {"tool": "jira_search", "content": "3 issues", "is_error": False, "tool_call_id": "call-1"})
        )

        assert sink.write.await_count == 2
        end_frame, result_frame = (call.args[0] for call in sink.write.await_args_list)
        assert end_frame["event"] == "TOOL_CALL_END"
        assert end_frame["data"]["toolCallId"] == "call-1"
        assert result_frame["event"] == "TOOL_CALL_RESULT"
        assert result_frame["data"]["content"] == "3 issues"
        assert result_frame["data"]["role"] == "tool"
        assert result_frame["data"]["status"] == "completed"

    async def test_tool_call_end_failure_sets_failed_status(self) -> None:
        emitter, sink = _make_emitter()

        await emitter.emit(
            _event(EventType.TOOL_CALL_END, {"tool": "jira_search", "content": "boom", "is_error": True, "tool_call_id": "call-3"})
        )

        _, result_frame = (call.args[0] for call in sink.write.await_args_list)
        assert result_frame["data"]["status"] == "failed"

    async def test_tool_blocked_alias_uses_reason_as_content(self) -> None:
        """`TOOL_CALL_END` aliased from `TOOL_BLOCKED` carries an explicit
        `status: ToolCallStatus.BLOCKED` — same disambiguation
        `SSEEventEmitter` uses."""
        emitter, sink = _make_emitter()

        await emitter.emit(
            _event(EventType.TOOL_CALL_END, {
                "tool": "jira_search", "reason": "blocked after 3 failures", "tool_call_id": "call-2",
                "status": ToolCallStatus.BLOCKED,
            })
        )

        _, result_frame = (call.args[0] for call in sink.write.await_args_list)
        assert result_frame["data"]["content"] == "blocked after 3 failures"
        assert result_frame["data"]["status"] == "blocked"

    async def test_missing_tool_call_id_falls_back_to_generated_id(self) -> None:
        emitter, sink = _make_emitter()

        await emitter.emit(_event(EventType.TOOL_CALL_START, {"tool": "jira_search", "args": {}}))

        start_frame = sink.write.await_args_list[0].args[0]
        assert start_frame["data"]["toolCallId"].startswith("call_")

    async def test_tool_call_start_forwards_args_summary(self) -> None:
        emitter, sink = _make_emitter()

        await emitter.emit(
            _event(EventType.TOOL_CALL_START, {
                "tool": "jira_search", "args": {"q": "bug"}, "tool_call_id": "call-4",
                "args_summary": "Searched for 'bug'",
            })
        )

        _, args_frame = (call.args[0] for call in sink.write.await_args_list)
        assert args_frame["data"]["argsSummary"] == "Searched for 'bug'"

    async def test_tool_call_end_forwards_result_summary(self) -> None:
        emitter, sink = _make_emitter()

        await emitter.emit(
            _event(EventType.TOOL_CALL_END, {
                "tool": "jira_search", "content": "3 issues", "is_error": False, "tool_call_id": "call-5",
                "result_summary": "Found 3 issues",
            })
        )

        _, result_frame = (call.args[0] for call in sink.write.await_args_list)
        assert result_frame["data"]["resultSummary"] == "Found 3 issues"

    async def test_tool_call_without_summaries_omits_fields(self) -> None:
        emitter, sink = _make_emitter()

        await emitter.emit(
            _event(EventType.TOOL_CALL_START, {"tool": "jira_search", "args": {"q": "bug"}, "tool_call_id": "call-6"})
        )
        await emitter.emit(
            _event(EventType.TOOL_CALL_END, {"tool": "jira_search", "content": "3 issues", "is_error": False, "tool_call_id": "call-6"})
        )

        _, args_frame, _, result_frame = (call.args[0] for call in sink.write.await_args_list)
        assert args_frame["data"]["argsSummary"] is None
        assert result_frame["data"]["resultSummary"] is None


class TestTextMessageTranslation:
    async def test_text_message_lifecycle_shares_one_message_id(self) -> None:
        emitter, sink = _make_emitter()

        await emitter.emit(_event(EventType.TEXT_MESSAGE_START, {}))
        await emitter.emit(_event(EventType.TEXT_MESSAGE_CONTENT, {"delta": "Hello"}))
        await emitter.emit(_event(EventType.TEXT_MESSAGE_END, {}))

        start_frame, content_frame, end_frame = (call.args[0] for call in sink.write.await_args_list)
        message_id = start_frame["data"]["messageId"]
        assert content_frame["data"]["messageId"] == message_id
        assert content_frame["data"]["delta"] == "Hello"
        assert end_frame["data"]["messageId"] == message_id

    async def test_content_before_start_is_dropped(self) -> None:
        emitter, sink = _make_emitter()

        await emitter.emit(_event(EventType.TEXT_MESSAGE_CONTENT, {"delta": "orphaned"}))

        sink.write.assert_not_awaited()

    async def test_second_turn_gets_a_fresh_message_id(self) -> None:
        emitter, sink = _make_emitter()

        await emitter.emit(_event(EventType.TEXT_MESSAGE_START, {}))
        first_id = sink.write.await_args.args[0]["data"]["messageId"]
        await emitter.emit(_event(EventType.TEXT_MESSAGE_END, {}))
        await emitter.emit(_event(EventType.TEXT_MESSAGE_START, {}))
        second_id = sink.write.await_args.args[0]["data"]["messageId"]

        assert first_id != second_id


class TestReasoningTranslation:
    async def test_reasoning_lifecycle_wraps_with_start_and_end_markers(self) -> None:
        emitter, sink = _make_emitter()

        await emitter.emit(_event(EventType.REASONING_MESSAGE_START, {}))
        await emitter.emit(_event(EventType.REASONING_MESSAGE_CONTENT, {"delta": "thinking..."}))
        await emitter.emit(_event(EventType.REASONING_MESSAGE_END, {}))

        frames = [call.args[0] for call in sink.write.await_args_list]
        event_names = [f["event"] for f in frames]
        assert event_names == [
            "REASONING_START",
            "REASONING_MESSAGE_START",
            "REASONING_MESSAGE_CONTENT",
            "REASONING_MESSAGE_END",
            "REASONING_END",
        ]
        assert frames[2]["data"]["delta"] == "thinking..."


class TestUnmappedEvents:
    async def test_unmapped_event_types_are_silently_dropped(self) -> None:
        emitter, sink = _make_emitter()

        await emitter.emit(_event(EventType.CHECKPOINT_SAVED, {}))
        await emitter.emit(_event(EventType.TURN_START, {}))

        sink.write.assert_not_awaited()
