"""`conversation_enrichment`: PRE_TURN hook that reuses the existing
`ConversationMemory` utility (`app/modules/agents/qna/conversation_memory.py`
— today only consumed by the respond-phase's `create_response_messages()` in
`response_prompt.py`) to detect short follow-up queries ("yes", "send it",
"do it") on the FIRST turn of a ReAct run and surface a reminder of what the
previous turn already fetched, so the model reuses that data instead of
re-calling tools.

Only fires on `turn_index == 0` — conversation history is fixed for the
whole run, so there's nothing new to detect on later turns.

`Goal` (not `AgentContext`) is the one run-scoped object a hook can mutate
that `PipesHubPromptBuilder` (Phase 4) actually reads back — see its
`goal.constraints` section — since `AgentContext` is a plain object closed
over by value at wiring time, not part of any `RunScope`/`TurnScope`
`Agent`/hook machinery reaches into.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.modules.agents.qna.conversation_memory import ConversationMemory

if TYPE_CHECKING:
    from app.agent_loop_lib.hooks.middleware.context import TurnContext
    from app.agent_loop_lib.hooks.middleware.pipeline import Middleware, Next
    from app.agents.agent_loop.context import AgentContext

_FOLLOW_UP_NOTE = (
    "This looks like a follow-up to the previous turn — reuse previously fetched "
    "data/results where applicable instead of re-calling the same tools."
)


def conversation_enrichment(context: AgentContext) -> "Middleware[TurnContext]":
    """PRE_TURN hook factory closing over the per-request `AgentContext`."""

    async def _middleware(ctx: TurnContext, next_fn: "Next") -> None:
        if ctx.turn_index != 0 or ctx.scope is None or not context.previous_conversations:
            await next_fn()
            return

        goal = ctx.scope.run.goal
        if ConversationMemory.should_reuse_tool_results(goal.description, context.previous_conversations):
            memory = ConversationMemory.extract_tool_context_from_history(context.previous_conversations)
            reminder = ConversationMemory.build_context_reminder(memory).strip()
            note = f"{_FOLLOW_UP_NOTE}\n{reminder}" if reminder else _FOLLOW_UP_NOTE
            goal.constraints.append(note)

        await next_fn()

    return _middleware


__all__ = ["conversation_enrichment"]
