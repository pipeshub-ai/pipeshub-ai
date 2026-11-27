"""POST_MODEL "completion gate": recovers from an empty model response
(no text, no tool calls) by injecting a nudge that asks the model to
either call a tool or provide a text answer.

Uses the `recovery_message` mechanism `truncation_recovery.py` already
established for POST_MODEL: set it, and `Agent.step()` injects it and
`continue`s instead of succeeding.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.core.messages import AssistantMessage, UserMessage
from app.agent_loop_lib.hooks.middleware.context import ModelResponseContext

if TYPE_CHECKING:
    from collections.abc import Iterable

    from app.agent_loop_lib.hooks.middleware.pipeline import Next
    from app.agents.agent_loop.context import AgentContext

__all__ = ["completion_gate", "restore_gate_state"]

_DEFAULT_MAX_NUDGES = 2

# Worded as a request, not a bracketed "[System: ...]" command: Azure's content
# filter rejects imperative notes injected as user messages mid-run, while the
# same ask phrased as a request passes.
_EMPTY_RESPONSE_NUDGE = (
    "Your last reply came through empty. Please continue: call a tool if you "
    "need more information, or reply with your answer as plain text."
)


def _response_text(message: object) -> str:
    if isinstance(message, AssistantMessage):
        return message.text
    return ""


def restore_gate_state(context: "AgentContext", events: "Iterable[Any]") -> None:
    """The nudge budget from the nudges a durable run journaled (each an injected
    user message whose content hash is its text's), so a resumed attempt nudges
    exactly as an uninterrupted one would."""
    nudge = f"sha256:{hashlib.sha256(_EMPTY_RESPONSE_NUDGE.encode('utf-8')).hexdigest()}"
    context.completion_gate_nudges = sum(
        1
        for e in events
        if e.type == "message.completed"
        and (e.data or {}).get("injected")
        and (e.data or {}).get("role") == "user"
        and ((e.data or {}).get("textRef") or {}).get("hash") == nudge
    )


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
