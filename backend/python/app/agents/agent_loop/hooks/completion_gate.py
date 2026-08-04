"""POST_MODEL "completion gate": recovers from an empty model response
(no text, no tool calls) by injecting a nudge that asks the model to
either call a tool or provide a text answer.

Uses the `recovery_message` mechanism `truncation_recovery.py` already
established for POST_MODEL: set it, and `Agent.step()` injects it and
`continue`s instead of succeeding.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.agent_loop_lib.core.messages import AssistantMessage, UserMessage
from app.agent_loop_lib.hooks.middleware.context import ModelResponseContext

if TYPE_CHECKING:
    from app.agent_loop_lib.hooks.middleware.pipeline import Next
    from app.agents.agent_loop.context import AgentContext

__all__ = ["completion_gate"]

_DEFAULT_MAX_NUDGES = 2

_EMPTY_RESPONSE_NUDGE = (
    "[System: your previous response had no text and called no tool. "
    "Either call a tool to make progress, or provide your final answer as "
    "text now.]"
)


def _response_text(message: object) -> str:
    if isinstance(message, AssistantMessage):
        return message.text
    return ""


def completion_gate(context: "AgentContext", *, max_nudges: int = _DEFAULT_MAX_NUDGES):
    """POST_MODEL middleware factory. Nudges the model when it produces an
    empty response (no text, no tool calls). `context` is the SAME
    `AgentContext` threaded through the whole request (top-level agent +
    every spawned domain-agent child), so `completion_gate_nudges` is
    tracked tree-wide, not per-agent."""

    async def _middleware(ctx: ModelResponseContext, next_fn: "Next") -> None:
        await next_fn()

        if ctx.tool_calls or getattr(ctx.response, "truncated", False):
            return

        text = _response_text(ctx.response)

        if text.strip():
            return

        if context.completion_gate_nudges >= max_nudges:
            return
        context.completion_gate_nudges += 1
        ctx.recovery_message = UserMessage(content=_EMPTY_RESPONSE_NUDGE, injected=True)

    return _middleware
