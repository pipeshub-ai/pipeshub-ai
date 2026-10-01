"""POST_MODEL "completion gate": vetoes a text-only finish in two cases by
injecting a nudge and giving the model another turn.

1. An empty response (no text, no tool calls): ask the model to either call
   a tool or provide a text answer.
2. A "not found / not specified" answer while knowledge tools and turns
   remain: once per request, ask the model to first look up the entity,
   record or item its evidence named, since that record often holds the
   missing fact.

Uses the `recovery_message` mechanism `truncation_recovery.py` already
established for POST_MODEL: set it, and `Agent.step()` injects it and
`continue`s instead of succeeding.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.agent_loop_lib.core.messages import AssistantMessage, UserMessage
from app.agent_loop_lib.hooks.middleware.context import ModelResponseContext
from app.modules.agents.context.tool_surface import ToolSurfaces

if TYPE_CHECKING:
    from app.agent_loop_lib.hooks.middleware.pipeline import Next
    from app.agents.agent_loop.context import AgentContext

__all__ = ["completion_gate", "is_abstaining_answer"]

_DEFAULT_MAX_NUDGES = 2

# Worded as a request, not a bracketed "[System: ...]" command: Azure's content
# filter rejects imperative stop-and-answer notes sent as user messages mid-run
# (every try in a replay), while the same ask phrased as a request passes.
_EMPTY_RESPONSE_NUDGE = (
    "Your last reply came through empty. Please continue: call a tool if you "
    "need more information, or reply with your answer as plain text."
)

_ABSTENTION_NUDGE = (
    "Before you finalise: your answer says this information was not found. "
    "If the records you read name a related person, item or document that "
    "would hold it, please search for or read that one's own record first "
    "(for a count over a list, check each item). Then answer, or say it is "
    "unavailable if it still is."
)

# The nudge costs a search turn plus an answer turn; below this many turns
# left after the current one, the model should keep the answer it has.
_MIN_TURNS_LEFT_FOR_ABSTENTION_NUDGE = 3

# Only the opening sentences are checked: an answer that leads with the fact
# and notes a minor gap later is not an abstention.
_ABSTENTION_OPENING_SENTENCES = 2
_ABSTENTION_OPENING_CHARS = 400
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")

_ABSTENTION_RE = re.compile(
    r"\b(?:"
    r"(?:does|do|did)\s+not|(?:does|do|did)n['’]t"
    r")\s+(?:explicitly\s+|clearly\s+|directly\s+)?"
    r"(?:specify|mention|state|say|include|contain|provide|indicate|identify|list|name|describe|record)\b"
    r"|\b(?:is|are|was|were)(?:\s+not|n['’]t)\s+(?:explicitly\s+|clearly\s+)?"
    r"(?:specified|mentioned|stated|available|provided|found|included|listed|documented|indicated|recorded)\b"
    r"|\b(?:could|can|was|were)(?:\s*not|n['’]t)\s+(?:find|locate|determine|identify|confirm|be\s+determined|be\s+found|be\s+confirmed)\b"
    r"|\bcannot\s+(?:find|locate|determine|identify|confirm|be\s+determined|be\s+found|be\s+confirmed)\b"
    r"|\bunable\s+to\s+(?:find|locate|determine|identify|confirm)\b"
    r"|\bno\s+(?:information|mention|details?|data|records?|results?)\s+(?:about|on|regarding|for|of|was|were|is|that)\b"
    r"|\bnot\s+(?:found|available)\s+in\s+the\b"
    r"|\bnone\s+of\s+[\w\s]{0,40}?\b(?:mention|specif|state|say|contain|provide|include|list)\w*\b"
    r"|\b(?:I|we)\s+(?:do\s+not|don['’]t)\s+have\s+(?:enough\s+|any\s+|sufficient\s+)?(?:information|details|data)\b",
    re.IGNORECASE,
)


def _response_text(message: object) -> str:
    if isinstance(message, AssistantMessage):
        return message.text
    return ""


def is_abstaining_answer(text: str) -> bool:
    """True when the answer opens by saying the information is not
    available/specified/found, rather than stating it."""
    sentences = _SENTENCE_END_RE.split(text.strip(), maxsplit=_ABSTENTION_OPENING_SENTENCES)
    opening = " ".join(sentences[:_ABSTENTION_OPENING_SENTENCES])[:_ABSTENTION_OPENING_CHARS]
    return bool(_ABSTENTION_RE.search(opening))


def _can_look_further(ctx: ModelResponseContext) -> bool:
    if ctx.scope is None:
        return False
    spec = ctx.scope.run.spec
    turns_left = spec.max_turns - (ctx.turn_index + 1)
    if turns_left < _MIN_TURNS_LEFT_FOR_ABSTENTION_NUDGE:
        return False
    surfaces = ToolSurfaces.resolve(spec.tool_names, {})
    return surfaces.retrieval is not None or surfaces.can_fetch_full_record


def completion_gate(context: "AgentContext", *, max_nudges: int = _DEFAULT_MAX_NUDGES):
    """POST_MODEL middleware factory. `context` is the SAME `AgentContext`
    threaded through the whole request (top-level agent + every spawned
    domain-agent child), so both nudge budgets are tracked tree-wide, not
    per-agent."""

    async def _middleware(ctx: ModelResponseContext, next_fn: "Next") -> None:
        await next_fn()

        if ctx.tool_calls or getattr(ctx.response, "truncated", False):
            return

        text = _response_text(ctx.response)

        if text.strip():
            if (
                not context.completion_gate_abstention_nudged
                and is_abstaining_answer(text)
                and _can_look_further(ctx)
            ):
                context.completion_gate_abstention_nudged = True
                ctx.recovery_message = UserMessage(content=_ABSTENTION_NUDGE, injected=True)
            return

        if context.completion_gate_nudges >= max_nudges:
            return
        context.completion_gate_nudges += 1
        ctx.recovery_message = UserMessage(content=_EMPTY_RESPONSE_NUDGE, injected=True)

    return _middleware
