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
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.agent_loop_lib.events.base import EventEmitter, EventType

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

        if event.event_type == EventType.RUN_STARTED:
            return {
                "event": "status",
                "data": {"status": "planning", "message": "Planning next step..."},
            }

        if event.event_type == EventType.TOOL_CALL_START:
            tool_name = payload.get("tool", "tool")
            return {
                "event": "status",
                "data": {"status": "executing", "message": f"Using {tool_name}..."},
            }

        if event.event_type == EventType.TOOL_CALL_END:
            tool_name = payload.get("tool", "tool")
            if "reason" in payload and "is_error" not in payload:
                # Aliased from TOOL_BLOCKED — see module docstring.
                return {
                    "event": "tool_result",
                    "data": {"tool": tool_name, "result": payload.get("reason"), "status": "error"},
                }
            return {
                "event": "tool_result",
                "data": {
                    "tool": tool_name,
                    "result": payload.get("content"),
                    "status": "error" if payload.get("is_error") else "success",
                },
            }

        # RUN_FINISHED/RUN_ERROR intentionally not mapped: the caller drives
        # RespondPipeline directly off `agent.run()`'s return value/exception
        # rather than reacting to an event for it (see factory.py/route
        # integration in Phase 8), so mapping here would just be a second,
        # redundant signal for the same transition.
        return None


__all__ = ["SSEEventEmitter"]
