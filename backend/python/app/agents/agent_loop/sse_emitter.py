"""`SSEEventEmitter`: agent-loop's `EventEmitter` ABC implemented on top of
`EventSink`, so `AgentRuntime.event_emitter` can stream tool-orchestration
progress to the same SSE connection `RespondPipeline` (Phase 6) streams the
final answer through.

`Agent.emit()` (`agent/__init__.py`) fires BOTH the legacy vocabulary
(`AGENT_START`, `TOOL_CALL`, `TOOL_RESULT`, `TOOL_BLOCKED`, ...) and its
AG-UI-aligned alias (`RUN_STARTED`, `TOOL_CALL_START`, `TOOL_CALL_END`, ...)
for the same occurrence, with the SAME payload — see `_AG_UI_ALIASES` there.
This emitter maps ONLY the AG-UI vocabulary (matching the migration plan's
event-bridge table) and no-ops on the legacy duplicates, so each occurrence
produces exactly one SSE event instead of two.

Event types the plan's table has no PipesHub SSE equivalent for
(`TEXT_MESSAGE_*` — not emitted during the tool-orchestration phase since
agent-loop's ReAct loop only streams text once it stops calling tools, which
is what `RespondPipeline` handles; `TURN_START`/`TURN_COMPLETE`/
`CHECKPOINT_SAVED`/`BUDGET_WARNING`/`CANCELLATION`/`CHILD_*`/`HIL_*`/
`STATE_*`) are silently ignored rather than guessed at.

Sub-agent visibility: every child agent (domain-agent delegates, deep-mode
`spawn_agent`) shares the runtime's ONE `event_emitter`, so this same
instance sees top-level AND every child's events — distinguished only by
`event.run_context.parent_run_id` (`AGUIEventEmitter` nests these under
`parentRunId`; see that module). Without surfacing that here too, a legacy
(non-AG-UI) client sees an unlabeled, flattened stream where a sub-agent's
own tool calls are indistinguishable from the top-level agent's — which
tool actually ran and on whose behalf is lost. Root-level events keep their
EXACT pre-existing shape (no new keys) so no existing legacy consumer's
`==` comparisons or strict schemas break; a child event adds `agent`
(the sub-agent's `role_name`) and `parent_run_id`, additive fields only a
client that knows to look for them will see.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.agent_loop_lib.events.base import EventEmitter, EventType, ToolCallStatus

if TYPE_CHECKING:
    from app.agent_loop_lib.events.base import AgentEvent
    from app.modules.agents.event_sink import EventSink


class SSEEventEmitter(EventEmitter):
    """Translates agent-loop `AgentEvent`s into PipesHub's `{event, data}`
    SSE shape and forwards them through an `EventSink`."""

    def __init__(self, event_sink: EventSink) -> None:
        self._event_sink = event_sink

    async def emit(self, event: AgentEvent) -> None:
        sse_event = self._translate(event)
        if sse_event is not None:
            await self._event_sink.write(sse_event)

    @staticmethod
    def _translate(event: AgentEvent) -> dict | None:
        payload = event.payload
        run_ctx = event.run_context
        parent_run_id = run_ctx.parent_run_id
        is_child = parent_run_id is not None
        agent_label = run_ctx.role_name if is_child else None

        if event.event_type == EventType.RUN_STARTED:
            if not is_child:
                return {
                    "event": "status",
                    "data": {"status": "planning", "message": "Planning next step..."},
                }
            # A sub-agent's OWN "planning" is not the top-level run
            # (re)starting — a distinct status value so a client branching
            # on `status` never mistakes this for the top-level agent
            # resetting to "planning" mid-run.
            return {
                "event": "status",
                "data": {
                    "status": "sub_agent_started",
                    "message": f"Delegating to {agent_label}...",
                    "agent": agent_label,
                    "run_id": run_ctx.run_id,
                    "parent_run_id": parent_run_id,
                },
            }

        if event.event_type == EventType.RUN_FINISHED:
            # Root RUN_FINISHED/RUN_ERROR intentionally not mapped (see
            # below) — the caller drives RespondPipeline directly off
            # `agent.run()`'s return value/exception, so mapping the ROOT
            # transition here would just be a second, redundant signal.
            # Only a CHILD's finish has nothing else to close its nested
            # activity for a legacy client, mirroring `AGUIEventEmitter`'s
            # identical root-vs-child split for the same two event types.
            if not is_child:
                return None
            return {
                "event": "status",
                "data": {
                    "status": "sub_agent_finished",
                    "message": f"{agent_label} finished.",
                    "agent": agent_label,
                    "run_id": run_ctx.run_id,
                    "parent_run_id": parent_run_id,
                },
            }

        if event.event_type == EventType.RUN_ERROR:
            if not is_child:
                return None
            return {
                "event": "status",
                "data": {
                    "status": "sub_agent_error",
                    "message": f"{agent_label} failed: {payload.get('error', '')}",
                    "agent": agent_label,
                    "run_id": run_ctx.run_id,
                    "parent_run_id": parent_run_id,
                },
            }

        if event.event_type == EventType.TOOL_CALL_START:
            tool_name = payload.get("tool", "tool")
            data: dict = {
                "status": "executing",
                "message": (
                    f"{agent_label} using {tool_name}..." if is_child else f"Using {tool_name}..."
                ),
            }
            if is_child:
                data.update(agent=agent_label, run_id=run_ctx.run_id, parent_run_id=parent_run_id)
            return {"event": "status", "data": data}

        if event.event_type == EventType.TOOL_CALL_END:
            tool_name = payload.get("tool", "tool")
            if payload.get("status") == ToolCallStatus.BLOCKED:
                # Aliased from TOOL_BLOCKED — see module docstring.
                data = {"tool": tool_name, "result": payload.get("reason"), "status": "error"}
                if is_child:
                    data.update(agent=agent_label, run_id=run_ctx.run_id, parent_run_id=parent_run_id)
                return {"event": "tool_result", "data": data}
            data = {
                "tool": tool_name,
                "result": payload.get("content"),
                "status": "error" if payload.get("is_error") else "success",
                # Additive — legacy consumers ignore unknown fields.
                # Human-readable stand-in for `result` (computed by
                # `PipesHubToolSummarizer` in tool_loop.py from the FULL,
                # untruncated tool output), `None` when unavailable.
                "summary": payload.get("result_summary"),
            }
            if is_child:
                data.update(agent=agent_label, run_id=run_ctx.run_id, parent_run_id=parent_run_id)
            return {"event": "tool_result", "data": data}

        if event.event_type == EventType.TOOL_UNAVAILABLE:
            data = {
                "tool": payload.get("tool"),
                "toolset": payload.get("toolset"),
                "reason": payload.get("reason"),
                "message": payload.get("message"),
            }
            if is_child:
                data.update(agent=agent_label, run_id=run_ctx.run_id, parent_run_id=parent_run_id)
            return {"event": "tool_unavailable", "data": data}

        # RUN_FINISHED/RUN_ERROR: see the root-vs-child branches above.
        return None


__all__ = ["SSEEventEmitter"]
