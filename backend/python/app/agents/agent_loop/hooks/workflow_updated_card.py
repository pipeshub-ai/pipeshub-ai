"""`workflow_updated_card_sse`: POST_TOOL_USE hook that emits a `workflow_updated`
ui_card when `workflow_manage(action="update")` succeeds and code was regenerated,
so the chat UI renders an inline confirmation linking to the updated workflow's
detail view without waiting for -- or depending on -- the agent's own text response.

Mirrors `task_scheduled_card.py`'s shape exactly (same POST_TOOL_USE +
`resolve_tool_name` + `context.formatter` pattern) — see that module's own
docstring for why this can't hook off `Agent.emit()` instead.

Gated on: the response carrying `regenerated: true` (set by
`WorkflowManageTool._update` when `_run_codegen` succeeds), which is the
authoritative signal that a new code version was stored. Update calls that
only changed metadata (title, description, timeouts) without triggering
codegen are deliberately *not* surfaced here, since there's no visual diff to
show.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.agents.agent_loop.hooks._tool_naming import resolve_tool_name

if TYPE_CHECKING:
    from app.agent_loop_lib.hooks.middleware.context import ToolResultContext
    from app.agent_loop_lib.hooks.middleware.pipeline import Middleware, Next
    from app.agents.agent_loop.context import AgentContext

__all__ = ["workflow_updated_card_sse"]

_WORKFLOW_MANAGE_TOOL_NAMES = frozenset({"workflow_manage", "tasks_workflow_manage"})


def workflow_updated_card_sse(context: AgentContext) -> "Middleware[ToolResultContext]":
    """POST_TOOL_USE hook factory closing over the per-request `AgentContext`."""

    async def _middleware(ctx: ToolResultContext, next_fn: "Next") -> None:
        await next_fn()

        tool_name = resolve_tool_name(ctx)
        if tool_name not in _WORKFLOW_MANAGE_TOOL_NAMES:
            return
        if context.event_sink is None or not context.has_ui_client:
            return

        output = ctx.tool_response
        if not output.success:
            return

        payload: Any = output.data
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (json.JSONDecodeError, TypeError):
                return
        if not isinstance(payload, dict):
            return

        # Pure metadata updates (title, timeouts) have no visual diff to show.
        # A failed regeneration does: the workflow silently keeps running its
        # old code, or falls back to agent mode, and nothing else says so.
        regenerated = bool(payload.get("regenerated"))
        codegen_note = payload.get("codegen_note")
        if not regenerated and not codegen_note:
            return

        workflow_id = payload.get("workflow_id") or payload.get("workflowId")
        if not workflow_id:
            return

        import uuid as _uuid

        card_payload = {
            "name": "workflow_updated",
            "workflowId": workflow_id,
            "versionId": payload.get("workflow_version_id"),
            "changesSummary": (
                "Code regenerated for fields: "
                + ", ".join(payload.get("updated_fields", []))
                if regenerated
                else codegen_note
            ),
            "regenerated": regenerated,
        }

        for evt in context.formatter.ui_card(
            context,
            card_type="workflow_updated",
            card_id=str(_uuid.uuid4()),
            payload=card_payload,
            actions=[{"label": "View Changes", "href": f"/workflows?workflowId={workflow_id}"}],
        ):
            await context.event_sink.write(evt)

    return _middleware
