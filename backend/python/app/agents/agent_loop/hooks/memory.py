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

from typing import TYPE_CHECKING, Any

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


def _tool_names_used_in_history(previous_conversations: list[dict[str, Any]]) -> set[str]:
    """Every `tool_name` recorded in each turn's `tool_results` (same shape
    `_convert_conversation_turn` replays into context — see
    `factory.py::_convert_conversation_turn`'s docstring for the field
    contract). Not schema-validated: a name that no longer resolves in
    THIS request's `ToolRegistry` (toolset removed/renamed/deauthorized
    since the prior turn) is simply dropped by the intersection the
    caller performs below, same as any other stale reference."""
    names: set[str] = set()
    for turn in previous_conversations:
        if turn.get("role") != "bot_response":
            continue
        for entry in turn.get("tool_results") or []:
            if isinstance(entry, dict):
                name = entry.get("tool_name")
                if isinstance(name, str) and name:
                    names.add(name)
    return names


def seed_visible_tools_from_history(context: AgentContext) -> "Middleware[TurnContext]":
    """PRE_TURN hook (turn 0 only): pre-populates `agent.visible_tools`
    with essentials/pinned (the same set `initial_visible_tools()` in
    `agent_loop_lib/agent/tool_loop.py` would compute) UNION whichever
    tool names this conversation already used in a previous turn — so a
    toolset the model fetched via `fetch_tools`/`search_tools` earlier in
    the conversation stays directly callable on the next request instead
    of forcing a redundant re-fetch (Issue 7: a fresh `Agent`/`RunScope`
    is built per HTTP request, so without this, `agent.visible_tools`
    would otherwise start over from scratch every turn even though
    `_seed_conversation_history` already replayed those exact tool calls
    into the model's own context).

    Must run as a PRE_TURN hook, not in `factory.create()` directly:
    `Agent.visible_tools`'s setter is backed by `RunScope`, which
    `Agent.run()` only constructs once the run actually starts (see
    `core/scope.py`) — `factory.create()` returns before that, so setting
    it there would silently no-op. PRE_TURN dispatches AFTER `RunScope`
    exists but BEFORE `tool_schemas_for_turn()` reads/lazily-initializes
    `agent.visible_tools` (see `Agent.step()`), making it the earliest
    point this can actually take effect.
    """
    from app.agent_loop_lib.agent.tool_loop import initial_visible_tools

    async def _middleware(ctx: TurnContext, next_fn: "Next") -> None:
        if ctx.turn_index != 0 or ctx.scope is None or not context.previous_conversations:
            await next_fn()
            return

        run_scope = ctx.scope.run
        if run_scope.visible_tools is not None:
            await next_fn()
            return

        registry = run_scope.runtime.tool_registry
        if registry is None or not registry.has_toolsets():
            await next_fn()
            return

        spec = run_scope.spec
        prior_names = _tool_names_used_in_history(context.previous_conversations)
        registered = set(registry.names())
        prior_names &= registered
        if spec.tool_names:
            prior_names &= set(spec.tool_names)

        if prior_names:
            run_scope.visible_tools = initial_visible_tools(spec, run_scope.runtime) | prior_names

        await next_fn()

    return _middleware


__all__ = ["conversation_enrichment", "seed_visible_tools_from_history"]
