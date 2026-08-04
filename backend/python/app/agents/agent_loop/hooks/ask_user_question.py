"""`ask_user_question_sse`: POST_TOOL_USE hook reproducing the eager
`ask_user_question` SSE emission from `nodes.py`'s `_execute_sequential`
(lines ~873-891) — emit the structured question payload the moment the tool
result is ready, before any `answer_chunk`/`complete` event, so the frontend
can render interactive option cards and suppress the plain-text answer.

`state["ask_user_question_emitted"]` is the same flag `nodes.py`'s
`_emit_ask_user_question_tool_event` (its end-of-turn fallback emitter, run
before Phase 6's `RespondPipeline` synthesizes the final answer) checks
before emitting again — setting it here means that fallback naturally
no-ops for agent-loop runs exactly as it does for a LangGraph run that hit
the eager path.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.agents.agent_loop.hooks._tool_naming import resolve_tool_name

if TYPE_CHECKING:
    from app.agent_loop_lib.hooks.middleware.context import ToolResultContext
    from app.agent_loop_lib.hooks.middleware.pipeline import Middleware, Next
    from app.agents.agent_loop.context import AgentContext

_ASK_USER_QUESTION_TOOL_NAMES = frozenset({
    "internaltools__ask_user_question",
    "internaltools_ask_user_question",
    "internaltools.ask_user_question",
})


def ask_user_question_sse(context: AgentContext) -> "Middleware[ToolResultContext]":
    """POST_TOOL_USE hook factory closing over the per-request `AgentContext`."""

    async def _middleware(ctx: ToolResultContext, next_fn: "Next") -> None:
        await next_fn()

        tool_name = resolve_tool_name(ctx)
        if tool_name not in _ASK_USER_QUESTION_TOOL_NAMES:
            return
        if context.event_sink is None or not context.has_ui_client:
            return

        output = ctx.tool_response
        raw_result = output.data if output.success else output.error
        payload: Any = raw_result
        if isinstance(raw_result, str):
            try:
                payload = json.loads(raw_result)
            except (json.JSONDecodeError, TypeError):
                payload = raw_result

        for evt in context.formatter.ask_user_question(
            context, status="success" if output.success else "error", tool_data=payload,
        ):
            await context.event_sink.write(evt)
        context.tool_state["ask_user_question_emitted"] = True

    return _middleware


__all__ = ["ask_user_question_sse"]
