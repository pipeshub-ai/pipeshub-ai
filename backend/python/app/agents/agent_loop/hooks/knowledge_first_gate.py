"""POST_MODEL "knowledge-first gate": stops the model from answering an
informational question straight from its own training data when the
current agent actually has an internal-knowledge search surface available.

The system prompt already tells the model this is the default (see
`prompt_builder.py`'s "Internal Knowledge First" section) — but nothing
enforced it, same gap `completion_gate.py` closes for file generation. This
uses the identical `recovery_message` mechanism: set it, and `Agent.step()`
injects it and `continue`s instead of accepting the answer as final.

Two hooks, wired together (see `factory.py`):

- `internal_search_attempted_tracking` (POST_TOOL_USE) flips
  `AgentContext.internal_search_attempted` the moment ANY internal-search
  call completes this run — tracked on the shared `AgentContext` (not a
  per-agent flag) because the search happens inside a spawned
  `internal_exploration_agent` child, a different `Agent`/`RunScope` than
  the top-level agent this gate actually nudges.
- `knowledge_first_gate` (POST_MODEL) is the gate itself.

Deliberately scoped by `AgentSpec.name`, not just tool presence: the
`internal_exploration_agent` child's OWN `spec.tool_names` contains the flat
`retrieval_search_internal_knowledge` tool it was built to claim (see
`domain_agents.py`) — nudging that child to "delegate to
`internal_exploration_agent`" would be circular and wrong. Excluding by name
is enough because only the child built FOR that domain is ever named
`internal_exploration_agent`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.agent_loop_lib.core.messages import AssistantMessage, UserMessage
from app.agents.agent_loop.hooks._tool_naming import (
    INTERNAL_SEARCH_DELEGATE_NAME,
    INTERNAL_SEARCH_FLAT_NAME,
    INTERNAL_SEARCH_TOOL_NAMES,
    resolve_tool_name,
)

if TYPE_CHECKING:
    from app.agent_loop_lib.hooks.middleware.context import (
        ModelResponseContext,
        ToolResultContext,
    )
    from app.agent_loop_lib.hooks.middleware.pipeline import Middleware, Next
    from app.agents.agent_loop.context import AgentContext

__all__ = ["internal_search_attempted_tracking", "knowledge_first_gate"]

_DEFAULT_MAX_NUDGES = 1

_NUDGE_TEMPLATE = (
    "[System: this question could plausibly be answered from the "
    "organization's internal knowledge, but it has not been searched yet "
    "this turn. Delegate to `{ref}` before answering from your own "
    "knowledge — or, if this genuinely does not need an internal lookup "
    "(greeting, arithmetic, reformatting a prior answer), say so "
    "explicitly instead of just answering.]"
)


def _response_text(message: object) -> str:
    if isinstance(message, AssistantMessage):
        return message.text
    return ""


def internal_search_attempted_tracking(context: "AgentContext") -> "Middleware[ToolResultContext]":
    """POST_TOOL_USE middleware factory: flips
    `context.internal_search_attempted` once any internal-search tool call
    completes — regardless of success/failure, since even a failed/empty
    search is still an attempt, not a skip."""

    async def _middleware(ctx: "ToolResultContext", next_fn: "Next") -> None:
        await next_fn()
        if resolve_tool_name(ctx) in INTERNAL_SEARCH_TOOL_NAMES:
            context.internal_search_attempted = True

    return _middleware


def knowledge_first_gate(
    context: "AgentContext", *, max_nudges: int = _DEFAULT_MAX_NUDGES,
) -> "Middleware[ModelResponseContext]":
    """POST_MODEL middleware factory. `context` is the SAME `AgentContext`
    threaded through the whole request (top-level agent + every spawned
    domain-agent child), so `internal_search_attempted`/
    `knowledge_first_nudges` are tracked tree-wide, not per-agent."""

    async def _middleware(ctx: "ModelResponseContext", next_fn: "Next") -> None:
        await next_fn()

        if ctx.tool_calls or getattr(ctx.response, "truncated", False):
            return

        run_scope = ctx.scope.run if ctx.scope is not None else None
        if run_scope is None or run_scope.spec.name == INTERNAL_SEARCH_DELEGATE_NAME:
            return

        tool_names = set(run_scope.spec.tool_names or [])
        available = tool_names & INTERNAL_SEARCH_TOOL_NAMES
        if not available:
            return

        if context.internal_search_attempted:
            return

        # An empty/whitespace-only response is `completion_gate`'s nudge to
        # give, not this gate's — avoids two POST_MODEL hooks disagreeing
        # about what to say for the same non-answer.
        if not _response_text(ctx.response).strip():
            return

        if context.knowledge_first_nudges >= max_nudges:
            return
        context.knowledge_first_nudges += 1

        ref = (
            INTERNAL_SEARCH_DELEGATE_NAME if INTERNAL_SEARCH_DELEGATE_NAME in available
            else INTERNAL_SEARCH_FLAT_NAME
        )
        ctx.recovery_message = UserMessage(content=_NUDGE_TEMPLATE.format(ref=ref), injected=True)

    return _middleware
