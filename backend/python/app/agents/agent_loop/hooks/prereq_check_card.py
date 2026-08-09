"""`prereq_check_card_sse`: POST_TOOL_USE hook that emits a
`prerequisite_check_result` CUSTOM event when `workflow_manage(action="validate")`
succeeds, so the chat UI can render an inline `PrereqCheckCard` showing each
resource's pass/fail state without waiting for the agent's own text reply.

Mirrors `task_scheduled_card.py`'s shape exactly.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.agents.agent_loop.hooks._tool_naming import resolve_tool_name

if TYPE_CHECKING:
    from app.agent_loop_lib.hooks.middleware.context import ToolResultContext
    from app.agent_loop_lib.hooks.middleware.pipeline import Middleware, Next
    from app.agents.agent_loop.context import AgentContext

__all__ = ["prereq_check_card_sse"]

_WORKFLOW_MANAGE_TOOL_NAMES = frozenset({
    "workflow_manage", "tasks_workflow_manage",
})


def prereq_check_card_sse(context: AgentContext) -> "Middleware[ToolResultContext]":
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
        if payload.get("action") != "validate":
            return

        import uuid as _uuid

        card_payload = {
            "name": "prerequisite_check_result",
            "ok": payload.get("ok", True),
            "issues": payload.get("issues", []),
            "summary": payload.get("summary", ""),
        }

        for evt in context.formatter.ui_card(
            context,
            card_type="prerequisite_check_result",
            card_id=str(_uuid.uuid4()),
            payload=card_payload,
            actions=[],
        ):
            await context.event_sink.write(evt)

    return _middleware
