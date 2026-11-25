"""Tool calls as the ``tool.call.completed`` platform event.

``harness_core`` never imports ``app``: its ``ToolExecutor`` tells the observer bound in
``harness_core.bindings`` that a call ended, and this observer announces it. Bound by both
composition roots (the legacy loop's and a durable run's).
"""

from __future__ import annotations

TOOL_CALL_COMPLETED = "tool.call.completed"


class ToolCallEvents:
    """Announces each tool call that ended, with the org it ran for; never an argument or an output."""

    def wanted(self) -> bool:
        from app import platform_events

        return platform_events.has_listeners(TOOL_CALL_COMPLETED)

    def ended(self, *, tool: str, kind: str | None, outcome: str, duration_seconds: float) -> None:
        from app import platform_events
        from app.core import org_context

        org = org_context.peek()
        platform_events.emit(
            TOOL_CALL_COMPLETED,
            tool=tool,
            kind=kind,
            org_id=org.org_id if org is not None else None,
            outcome=outcome,
            duration_seconds=duration_seconds,
        )


TOOL_CALL_EVENTS = ToolCallEvents()
