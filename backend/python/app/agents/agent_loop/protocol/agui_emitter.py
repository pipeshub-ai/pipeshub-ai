"""`AGUIEventEmitter`: `EventEmitter` sibling of `SSEEventEmitter`
(`app/agents/agent_loop/sse_emitter.py`), wired in by `PipesHubAgentFactory`
in place of it when a request negotiates the `agui` protocol.

Why this can't be a queue-drain adapter over `SSEEventEmitter`'s output:
run/parent-run identity (`AgentEvent.run_context`) and per-call correlation
(`payload["tool_call_id"]`, added in Phase 0) exist ONLY on the `AgentEvent`
itself — `SSEEventEmitter._translate()` already discards both, producing
anonymous `{event, data}` dicts. Sub-agent nesting and tool-call
correlation are unrecoverable once that happens, so the AG-UI translation
has to sit exactly where `SSEEventEmitter` sits: receiving the full
`AgentEvent` straight from `Agent.emit()`.

Mirrors `SSEEventEmitter`'s "translate only the AG-UI-aliased vocabulary"
convention (see that module's docstring): `Agent.emit()` fires BOTH a
legacy event type (`TOOL_CALL`, `AGENT_START`, ...) and its AG-UI alias
(`TOOL_CALL_START`, `RUN_STARTED`, ...) for the same occurrence with the
same payload (`_AG_UI_ALIASES` in `agent/__init__.py`). Translating on the
legacy AND the alias would double-emit every occurrence, so this class
only matches on the alias side, exactly like `SSEEventEmitter` does.

Sub-agent identity: every child agent (domain-agent delegates via
`AgentTool.handle()`, deep-mode `spawn_agent`/`best_of_n`) is launched
through `AgentRuntime.run_child()` and shares the runtime's ONE
`event_emitter` — so this same instance sees top-level AND every child's
events, distinguished only by `event.run_context.parent_run_id`. Without
branching on that, a child's `AGENT_START`/`AGENT_COMPLETE` would emit a
SECOND top-level `RUN_STARTED`/`RUN_FINISHED` and corrupt the run
lifecycle the moment the first domain-agent call happens — see the
`RUN_STARTED`/`RUN_FINISHED`/`RUN_ERROR` branches below.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from app.agent_loop_lib.events.base import EventEmitter, EventType, ToolCallStatus
from app.agents.agent_loop.protocol.agui import AGUIEventType, frame, new_id

if TYPE_CHECKING:
    from app.agent_loop_lib.core.context import RunContext
    from app.agent_loop_lib.events.base import AgentEvent
    from app.modules.agents.event_sink import EventSink

logger = logging.getLogger(__name__)


class AGUIEventEmitter(EventEmitter):
    """Translates agent-loop `AgentEvent`s into AG-UI-shaped SSE frames and
    forwards them through an `EventSink`. One instance per request/run
    tree — `_open_messages` and `_tool_call_ids` are keyed by `run_id` so
    a root run and any number of concurrently-spawned children never
    collide on message/tool-call bookkeeping."""

    def __init__(self, event_sink: "EventSink", *, thread_id: str) -> None:
        self._event_sink = event_sink
        self._thread_id = thread_id
        # (run_id, kind) -> open messageId, kind in {"text", "reasoning"}
        self._open_messages: dict[tuple[str, str], str] = {}

    async def emit(self, event: "AgentEvent") -> None:
        for sse_event in self._translate(event):
            await self._event_sink.write(sse_event)

    def _translate(self, event: "AgentEvent") -> list[dict]:
        run_ctx: "RunContext" = event.run_context
        payload = event.payload
        run_id = run_ctx.run_id
        parent_run_id = run_ctx.parent_run_id
        is_child = parent_run_id is not None

        if event.event_type == EventType.RUN_STARTED:
            if not is_child:
                return [frame(AGUIEventType.RUN_STARTED, threadId=self._thread_id, runId=run_id)]
            return [
                frame(AGUIEventType.STEP_STARTED, stepName=f"sub_agent:{run_ctx.role_name}", runId=parent_run_id),
                frame(
                    AGUIEventType.RUN_STARTED, threadId=self._thread_id, runId=run_id, parentRunId=parent_run_id,
                ),
            ]

        if event.event_type == EventType.RUN_FINISHED:
            # Root RUN_FINISHED is superseded by `AnswerFinalizer`'s
            # authoritative STATE_SNAPSHOT + RUN_FINISHED (via
            # `ProtocolFormatter.answer_final` — see formatter.py):
            # emitting it here too would double-signal completion for
            # the SAME run_id before the final answer is even attached.
            if not is_child:
                return []
            return [
                frame(AGUIEventType.RUN_FINISHED, runId=run_id, parentRunId=parent_run_id),
                frame(AGUIEventType.STEP_FINISHED, stepName=f"sub_agent:{run_ctx.role_name}", runId=parent_run_id),
            ]

        if event.event_type == EventType.RUN_ERROR:
            # Root-level failures reach `AnswerFinalizer._emit_error_response`
            # too, which emits a graceful RUN_FINISHED for the same run —
            # see the migration plan's "graceful error answers are
            # RUN_FINISHED, not RUN_ERROR" decision. Only a CHILD failure
            # has nothing else to close its nested run, so only that case
            # surfaces RUN_ERROR here.
            if not is_child:
                return []
            return [frame(
                AGUIEventType.RUN_ERROR, runId=run_id, parentRunId=parent_run_id,
                message=str(payload.get("error", "")), code="agent_error",
            )]

        if event.event_type == EventType.TOOL_CALL_START:
            tool_call_id = payload.get("tool_call_id") or new_id("call")
            return [
                frame(
                    AGUIEventType.TOOL_CALL_START, toolCallId=tool_call_id,
                    toolCallName=payload.get("tool", "tool"), runId=run_id,
                ),
                frame(
                    AGUIEventType.TOOL_CALL_ARGS, toolCallId=tool_call_id,
                    delta=json.dumps(payload.get("args", {}), default=str), runId=run_id,
                    argsSummary=payload.get("args_summary"),
                ),
            ]

        if event.event_type == EventType.TOOL_CALL_END:
            tool_call_id = payload.get("tool_call_id") or new_id("call")
            # Same disambiguation `SSEEventEmitter._translate` uses: both
            # TOOL_RESULT and TOOL_BLOCKED alias to TOOL_CALL_END, and only
            # `payload["status"]` (set by the producer — see `ToolCallStatus`'s
            # docstring) says which one this actually was.
            # `status` (the outgoing AG-UI field, distinct from the payload's
            # own `status` key) is additive (old clients only ever read
            # `content`) — it lets the frontend's live parts timeline (see
            # `agui-event-handler.ts`) show the same running/completed/
            # failed/blocked state `TranscriptCollector` persists, instead
            # of only ever knowing "the call ended".
            if payload.get("status") == ToolCallStatus.BLOCKED:
                content = payload.get("reason")
                status = "blocked"
                result_summary = None
            else:
                content = payload.get("content")
                status = "failed" if payload.get("is_error") else "completed"
                result_summary = payload.get("result_summary")
            return [
                frame(AGUIEventType.TOOL_CALL_END, toolCallId=tool_call_id, runId=run_id),
                frame(
                    AGUIEventType.TOOL_CALL_RESULT, toolCallId=tool_call_id,
                    content=content, role="tool", status=status, runId=run_id,
                    resultSummary=result_summary,
                ),
            ]

        if event.event_type == EventType.TEXT_MESSAGE_START:
            message_id = new_id("msg")
            self._open_messages[(run_id, "text")] = message_id
            return [frame(AGUIEventType.TEXT_MESSAGE_START, messageId=message_id, role="assistant", runId=run_id)]

        if event.event_type == EventType.TEXT_MESSAGE_CONTENT:
            message_id = self._open_messages.get((run_id, "text"))
            if message_id is None:
                return []
            return [frame(
                AGUIEventType.TEXT_MESSAGE_CONTENT, messageId=message_id,
                delta=payload.get("delta", ""), runId=run_id,
            )]

        if event.event_type == EventType.TEXT_MESSAGE_END:
            message_id = self._open_messages.pop((run_id, "text"), None)
            if message_id is None:
                return []
            return [frame(AGUIEventType.TEXT_MESSAGE_END, messageId=message_id, runId=run_id)]

        if event.event_type == EventType.REASONING_MESSAGE_START:
            message_id = new_id("msg")
            self._open_messages[(run_id, "reasoning")] = message_id
            return [
                frame(AGUIEventType.REASONING_START, runId=run_id),
                frame(AGUIEventType.REASONING_MESSAGE_START, messageId=message_id, role="assistant", runId=run_id),
            ]

        if event.event_type == EventType.REASONING_MESSAGE_CONTENT:
            message_id = self._open_messages.get((run_id, "reasoning"))
            if message_id is None:
                return []
            return [frame(
                AGUIEventType.REASONING_MESSAGE_CONTENT, messageId=message_id,
                delta=payload.get("delta", ""), runId=run_id,
            )]

        if event.event_type == EventType.REASONING_MESSAGE_END:
            message_id = self._open_messages.pop((run_id, "reasoning"), None)
            if message_id is None:
                return []
            return [
                frame(AGUIEventType.REASONING_MESSAGE_END, messageId=message_id, runId=run_id),
                frame(AGUIEventType.REASONING_END, runId=run_id),
            ]

        if event.event_type == EventType.TOOL_UNAVAILABLE:
            return [frame(AGUIEventType.CUSTOM, name="tool_unavailable", value=dict(payload), runId=run_id)]

        if event.event_type == EventType.STATE_SNAPSHOT:
            # Low-level AgentState progress snapshot (see
            # `agent/observability.py::write_state`) — distinct from, and
            # superseded by, `AnswerFinalizer`'s final-answer STATE_SNAPSHOT
            # (see formatter.py); AG-UI allows multiple STATE_SNAPSHOTs per
            # run, the frontend just applies the latest.
            return [frame(AGUIEventType.STATE_SNAPSHOT, runId=run_id, snapshot=payload)]

        # Everything else (legacy AGENT_START/AGENT_COMPLETE/ERROR/TOOL_CALL/
        # TOOL_RESULT/TOOL_BLOCKED duplicates already handled via their
        # alias above; TURN_START/TURN_COMPLETE/CHECKPOINT_SAVED/
        # BUDGET_WARNING/CANCELLATION/STATUS_CHANGE/CHILD_*/HIL_*/
        # STATE_DELTA) has no AG-UI wire equivalent in this migration and
        # is silently ignored — same convention `SSEEventEmitter` documents.
        return []


__all__ = ["AGUIEventEmitter"]
