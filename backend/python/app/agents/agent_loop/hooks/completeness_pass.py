"""POST_MODEL completeness pass (``ANSWER_COMPLETENESS_PASS``).

A final text draft that relies on at least one record is sent back once, in the same conversation, to add the
specifics its records give for each part of the question that it left out (``app.agents.agent_loop.completeness_pass``).

Registered outermost of the send-backs, so it acts last: only on a draft that no other check sends back in the same
turn, and after the coverage gate's revision it checks the revised answer. The pass never raises: on any failure the
draft stands.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.agent_loop_lib.core.messages import AssistantMessage, UserMessage
from app.agents.agent_loop.completeness_pass import render_request, should_check

if TYPE_CHECKING:
    from app.agent_loop_lib.hooks.middleware.context import ModelResponseContext
    from app.agent_loop_lib.hooks.middleware.pipeline import Middleware, Next
    from app.agents.agent_loop.context import AgentContext

__all__ = ["COMPLETENESS_PASS_SENT", "record_completeness_pass"]

logger = logging.getLogger(__name__)

COMPLETENESS_PASS_SENT = "completeness_pass_sent"
# The revision takes one turn; with fewer left the draft stands.
_MIN_TURNS_LEFT = 2


def _turns_left(ctx: "ModelResponseContext") -> int:
    if ctx.scope is None:
        return 0
    return ctx.scope.run.spec.max_turns - (ctx.turn_index + 1)


def record_completeness_pass(context: "AgentContext") -> "Middleware[ModelResponseContext]":
    """POST_MODEL middleware factory over the request's ``AgentContext``."""

    async def _middleware(ctx: "ModelResponseContext", next_fn: "Next") -> None:
        await next_fn()
        if ctx.tool_calls or getattr(ctx.response, "truncated", False) or ctx.recovery_message is not None:
            return
        if context.tool_state.get(COMPLETENESS_PASS_SENT):
            return
        if ctx.scope is None or ctx.scope.run.identity.spawn_depth != 0 or _turns_left(ctx) < _MIN_TURNS_LEFT:
            return
        text = ctx.response.text if isinstance(ctx.response, AssistantMessage) else ""
        try:
            ref_mapper = context.tool_state.get("citation_ref_mapper")
            if not should_check(text, getattr(ref_mapper, "ref_to_url", None)):
                return
            request = render_request()
        except Exception:
            logger.warning("completeness_pass: check failed; the draft stands", exc_info=True)
            return
        context.tool_state[COMPLETENESS_PASS_SENT] = True
        logger.info("completeness_pass: sent back conversation=%s", context.conversation_id)
        ctx.recovery_message = UserMessage(content=request, injected=True)

    return _middleware
