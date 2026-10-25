"""PRE_TURN hooks that carry what earlier turns of the conversation made
visible (tool schemas) into this run. Earlier turns themselves reach the
model as seeded history (`factory.py::_seed_conversation_history`)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.agents.agent_loop.parked_turn import VISIBLE_TOOLS

if TYPE_CHECKING:
    from app.agent_loop_lib.hooks.middleware.context import TurnContext
    from app.agent_loop_lib.hooks.middleware.pipeline import Middleware, Next
    from app.agents.agent_loop.context import AgentContext

def tool_names_used_in_history(previous_conversations: list[dict[str, Any]]) -> set[str]:
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
        registry = run_scope.runtime.tool_registry
        if registry is None or not registry.has_toolsets():
            await next_fn()
            return

        spec = run_scope.spec
        prior_names = tool_names_used_in_history(context.previous_conversations)
        # What the turn an answered question stopped could see (`parked_turn.restore_question`).
        prior_names |= set(context.tool_state.get(VISIBLE_TOOLS) or [])
        registered = set(registry.names())
        prior_names &= registered
        if spec.tool_names:
            prior_names &= set(spec.tool_names)

        if prior_names:
            # Added to what PRE_AGENT already made visible (tool preloading sets it for every lazy
            # agent), not skipped when it is set: otherwise a follow-up never sees them again.
            run_scope.visible_tools = (run_scope.visible_tools or initial_visible_tools(spec, run_scope.runtime)) | prior_names

        await next_fn()

    return _middleware


def sync_visible_tools_for_prompt(context: AgentContext) -> "Middleware[TurnContext]":
    """PRE_TURN: copy ``RunScope.visible_tools`` into ``tool_state`` for the
    prompt builder.

    Must register *after* hooks that grow visibility (history seed, CODE_FILE
    unlock) so the lazy "must load" block matches schemas already bound.
    """
    from app.agents.agent_loop.prompt_builder import BOUND_TOOL_NAMES_KEY

    async def _middleware(ctx: TurnContext, next_fn: "Next") -> None:
        if ctx.scope is not None:
            visible = ctx.scope.run.visible_tools
            if visible is not None:
                context.tool_state[BOUND_TOOL_NAMES_KEY] = sorted(visible)
        await next_fn()

    return _middleware


__all__ = [
    "seed_visible_tools_from_history",
    "sync_visible_tools_for_prompt",
    "tool_names_used_in_history",
]
