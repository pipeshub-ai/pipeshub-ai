"""Unlock the lazy `codegraph` toolset when retrieval already holds CODE_FILE.

CodeGraph stays behind `search_tools` / `fetch_tools` when a turn has no code.
Once knowledge search (or prefetch) writes a `CODE_FILE` into
`virtual_record_id_to_result`, the model already has connector IDs and paths —
forcing another meta-tool round trip is pure friction. This hook grows
`visible_tools` the same way `fetch_tools` would, without registering anything
new: the toolset is already in the registry when `has_code_connector` and
`has_code_knowledge` are true (`tool_loader.py`).

Mirrors `citation_tracking`'s mid-run grant for `fetch_record`, but only for
visibility of an already-loaded toolset.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.models.entities import RecordType

if TYPE_CHECKING:
    from app.agent_loop_lib.hooks.middleware.context import ToolResultContext, TurnContext
    from app.agent_loop_lib.hooks.middleware.pipeline import Middleware, Next
    from app.agents.agent_loop.context import AgentContext

CODE_GRAPH_TOOLSET = "codegraph"
_UNLOCK_FLAG = "unlock_code_graph"
_CODE_FILE = RecordType.CODE_FILE.value


def _record_is_code_file(record: Any) -> bool:
    if not isinstance(record, dict):
        return False
    rt = record.get("record_type") or record.get("recordType")
    if isinstance(rt, str) and rt.upper() == _CODE_FILE:
        return True
    meta = record.get("context_metadata") or ""
    return isinstance(meta, str) and f"Type: {_CODE_FILE}" in meta


def virtual_records_include_code(virtual_records: dict[str, Any] | None) -> bool:
    if not virtual_records:
        return False
    return any(_record_is_code_file(r) for r in virtual_records.values())


def unlock_code_graph_tools(run_scope: Any, registry: Any) -> list[str]:
    """Add every granted `codegraph` tool to ``run_scope.visible_tools``.

    No-op when the toolset was never loaded, or when the agent's permission
    ceiling excludes those names. Returns the names that were made visible.
    """
    if run_scope is None or registry is None:
        return []
    try:
        names = list(registry.tools_in_toolset(CODE_GRAPH_TOOLSET) or [])
    except Exception:
        return []
    if not names:
        return []

    spec = getattr(run_scope, "spec", None)
    grant = set(spec.tool_names) if spec is not None and spec.tool_names else None
    allowed = [n for n in names if grant is None or n in grant]
    if not allowed:
        return []

    visible = getattr(run_scope, "visible_tools", None)
    if visible is None:
        from app.agent_loop_lib.agent.tool_loop import initial_visible_tools

        runtime = getattr(run_scope, "runtime", None)
        if spec is None or runtime is None:
            run_scope.visible_tools = set(allowed)
        else:
            run_scope.visible_tools = initial_visible_tools(spec, runtime) | set(allowed)
    else:
        run_scope.visible_tools |= set(allowed)

    return allowed


def _maybe_unlock(context: "AgentContext", run_scope: Any) -> list[str]:
    records = (context.tool_state or {}).get("virtual_record_id_to_result") or {}
    code_hits = virtual_records_include_code(records)
    if context.tool_state.get(_UNLOCK_FLAG) or code_hits:
        context.tool_state[_UNLOCK_FLAG] = True
    else:
        return []
    registry = getattr(getattr(run_scope, "runtime", None), "tool_registry", None)
    unlocked = unlock_code_graph_tools(run_scope, registry)
    if unlocked:
        context.tool_state["unlocked_codegraph_tools"] = list(unlocked)
    return unlocked


def code_graph_unlock_after_tools(context: "AgentContext") -> "Middleware[ToolResultContext]":
    """POST_TOOL_USE: unlock after knowledge search / retrieval writes CODE_FILE."""

    async def _middleware(ctx: "ToolResultContext", next_fn: "Next") -> None:
        await next_fn()
        run_scope = ctx.scope.turn.run if ctx.scope is not None else None
        _maybe_unlock(context, run_scope)

    return _middleware


def code_graph_unlock_on_turn(context: "AgentContext") -> "Middleware[TurnContext]":
    """PRE_TURN: unlock when prefetch (or a prior child turn) already saw CODE_FILE.

    Prefetch merges code hits into ``tool_state`` before the agent runs, and a
    domain child's search sets ``unlock_code_graph`` on the shared context —
    both need the *current* run's ``visible_tools`` updated once ``RunScope``
    exists (same timing as ``seed_visible_tools_from_history``).
    """

    async def _middleware(ctx: "TurnContext", next_fn: "Next") -> None:
        if ctx.scope is not None:
            _maybe_unlock(context, ctx.scope.run)
        await next_fn()

    return _middleware


__all__ = [
    "CODE_GRAPH_TOOLSET",
    "code_graph_unlock_after_tools",
    "code_graph_unlock_on_turn",
    "unlock_code_graph_tools",
    "virtual_records_include_code",
]
