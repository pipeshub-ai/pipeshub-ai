"""`task_scheduled_card_sse`: POST_TOOL_USE hook that emits a `workflow_created`
CUSTOM event the moment a `task_manage(action="create")` call succeeds and
produced at least one trigger, so the chat UI can render an inline `WorkflowCard`
confirmation (Part F2 of the task engine plan) without waiting for -- or
depending on -- the agent's own text response to mention it.

Gated on the response actually containing a non-empty `triggers` list rather
than on the `action` argument itself: `create` is the only `task_manage`
action whose success payload has a `triggers` key at all (see
`TaskManageTool._create`) -- `run_now`/`pause`/`resume`/`cancel`/`update`/
`promote_to_agent` responses never do, so this naturally no-ops for all of
them, and also for a `create` call that made a run_now-only task (no
triggers): a task nothing is scheduled to fire isn't a "scheduled task" card.

Mirrors `hooks/ask_user_question.py`'s shape exactly (same POST_TOOL_USE +
`resolve_tool_name` + `context.formatter` pattern) -- see that module's own
docstring for why this can't hook off `Agent.emit()` instead.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.agents.agent_loop.hooks._tool_naming import resolve_tool_name

if TYPE_CHECKING:
    from app.agent_loop_lib.hooks.middleware.context import ToolResultContext
    from app.agent_loop_lib.hooks.middleware.pipeline import Middleware, Next
    from app.agents.agent_loop.context import AgentContext

__all__ = ["task_scheduled_card_sse"]

# `"task_manage"` is what `resolve_tool_name` returns via the real
# `ToolRegistry` (matches `TaskManageTool.name` exactly). `"tasks_task_manage"`
# is what its path-splitting fallback produces from the tool's registered
# path (`/toolsets/tasks/task_manage`) when no registry is available (e.g. in
# isolated hook unit tests) -- same defensive multi-variant pattern as
# `ask_user_question.py`'s own `_ASK_USER_QUESTION_TOOL_NAMES`.
_TASK_MANAGE_TOOL_NAMES = frozenset({"task_manage", "tasks_task_manage", "workflow_manage", "tasks_workflow_manage"})


def task_scheduled_card_sse(context: AgentContext) -> "Middleware[ToolResultContext]":
    """POST_TOOL_USE hook factory closing over the per-request `AgentContext`."""

    async def _middleware(ctx: ToolResultContext, next_fn: "Next") -> None:
        await next_fn()

        tool_name = resolve_tool_name(ctx)
        if tool_name not in _TASK_MANAGE_TOOL_NAMES:
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
        task_id = payload.get("task_id") or payload.get("workflow_id")
        triggers = payload.get("triggers")
        if not isinstance(triggers, list):
            triggers = []
        codegen_note = payload.get("codegen_note")
        # A workflow whose codegen failed silently falls back to agent mode, so
        # it gets a card even with no triggers -- that degradation is exactly
        # what the user needs to see, and it is otherwise invisible.
        if not task_id or (not triggers and not codegen_note):
            return

        import uuid as _uuid
        trigger_summaries = [
            {
                "triggerId": t.get("trigger_id"),
                "kind": t.get("kind"),
                "nextRunAt": t.get("next_run_at"),
            }
            for t in triggers
            if isinstance(t, dict)
        ]
        card_payload = {
            "name": "workflow_created",
            "workflowId": task_id,
            "title": payload.get("title"),
            "status": payload.get("status"),
            "executionKind": payload.get("execution_kind"),
            "toolNames": payload.get("tool_names") or [],
            "connectorIds": payload.get("connector_ids") or [],
            "collectionIds": payload.get("collection_ids") or [],
            "triggers": trigger_summaries,
            "codegenNote": codegen_note,
        }

        for evt in context.formatter.ui_card(
            context,
            card_type="workflow_created",
            card_id=str(_uuid.uuid4()),
            payload=card_payload,
            actions=[{"label": "View Workflow", "href": f"/workflows?workflowId={task_id}"}],
        ):
            await context.event_sink.write(evt)

    return _middleware
