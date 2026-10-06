"""`agent_draft_sse`: POST_TOOL_USE hook that sends the draft card to the client
the moment `agent_builder__draft_agent` returns a draft."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from app.agents.actions.agent_builder.models import DRAFT_TOOL_NAME, AgentDraft
from app.agents.agent_loop.hooks._tool_naming import resolve_tool_name

if TYPE_CHECKING:
    from app.agent_loop_lib.hooks.middleware.context import ToolResultContext
    from app.agent_loop_lib.hooks.middleware.pipeline import Middleware, Next
    from app.agents.agent_loop.context import AgentContext

_DRAFT_TOOL_NAME = f"agent_builder__{DRAFT_TOOL_NAME}"


def agent_draft_sse(context: AgentContext) -> "Middleware[ToolResultContext]":
    async def _middleware(ctx: ToolResultContext, next_fn: "Next") -> None:
        await next_fn()

        if resolve_tool_name(ctx) != _DRAFT_TOOL_NAME or context.invocation != "assistant":
            return
        if context.event_sink is None or not context.has_ui_client:
            return
        output = ctx.tool_response
        if not output.success or not isinstance(output.data, str):
            return
        try:
            draft = AgentDraft.model_validate(json.loads(output.data)["draft"])
        except (ValueError, KeyError, TypeError):
            return

        for evt in context.formatter.agent_draft(context, draft=draft):
            await context.event_sink.write(evt)

    return _middleware


__all__ = ["agent_draft_sse"]
