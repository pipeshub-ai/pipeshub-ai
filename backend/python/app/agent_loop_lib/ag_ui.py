"""AG-UI aliases of the legacy agent loop's events.

The kernel emits only its own event vocabulary. The legacy surfaces (the SSE
emitter, the AG-UI emitter, the transcript collector, the stream bridge) also
expect the AG-UI-shaped equivalents, fired right after the legacy event with
the same payload. `composition.install()` binds `derive_aliases` as every
runtime's default `derive_events`.
"""

from __future__ import annotations

from app.agent_loop_lib.events.base import AgentEvent, EventType

# TOOL_BLOCKED counts as a TOOL_CALL_END too: a blocked call is a terminal
# outcome for that tool_call, same as a normal TOOL_RESULT.
AG_UI_ALIASES: dict[EventType, EventType] = {
    EventType.AGENT_START: EventType.RUN_STARTED,
    EventType.AGENT_COMPLETE: EventType.RUN_FINISHED,
    EventType.ERROR: EventType.RUN_ERROR,
    EventType.TOOL_CALL: EventType.TOOL_CALL_START,
    EventType.TOOL_RESULT: EventType.TOOL_CALL_END,
    EventType.TOOL_BLOCKED: EventType.TOOL_CALL_END,
}


def derive_aliases(event: AgentEvent) -> list[AgentEvent]:
    alias = AG_UI_ALIASES.get(event.event_type)
    if alias is None:
        return []
    return [AgentEvent(event_type=alias, run_context=event.run_context, payload=event.payload)]
