"""``progressive_entity_tools``: POST_TOOL_USE hook that grants
``knowledgegraph__find_records_by_entity`` once ``search_entities`` has run.

``find_records_by_entity`` needs an entityId, and ``search_entities`` is the
only tool that produces one, so its schema is left out of the initial grant
(``factory.py``) and appended to the calling agent's spec here — the same
mechanism ``hooks/citations.py`` uses for ``fetch_record``. A conversation
that already used either entity tool gets it from the first turn instead
(``entity_tools_used_in_history``), since the model may reuse an entityId
from an earlier answer.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.agents.agent_loop.hooks.memory import tool_names_used_in_history

if TYPE_CHECKING:
    from app.agent_loop_lib.agent.spec import AgentSpec
    from app.agent_loop_lib.hooks.middleware.context import ToolResultContext
    from app.agent_loop_lib.hooks.middleware.pipeline import Middleware, Next
    from app.agents.agent_loop.context import AgentContext

logger = logging.getLogger(__name__)

SEARCH_ENTITIES_TOOL_NAME = "knowledgegraph__search_entities"
PROGRESSIVE_FIND_RECORDS_TOOL_NAME = "knowledgegraph__find_records_by_entity"
PROGRESSIVE_TOOL_NAMES: frozenset[str] = frozenset({PROGRESSIVE_FIND_RECORDS_TOOL_NAME})
ENTITY_TOOL_NAMES: frozenset[str] = frozenset(
    {SEARCH_ENTITIES_TOOL_NAME, PROGRESSIVE_FIND_RECORDS_TOOL_NAME}
)
# `resolve_tool_name` falls back to a single-underscore join when the path
# is not registered (see `_tool_naming.py`), so both spellings trigger.
_ENTITY_SEARCH_TRIGGER_NAMES = frozenset(
    {SEARCH_ENTITIES_TOOL_NAME, "knowledgegraph_search_entities"}
)
_ENTITY_HISTORY_NAMES = ENTITY_TOOL_NAMES | _ENTITY_SEARCH_TRIGGER_NAMES | {
    "knowledgegraph_find_records_by_entity"
}


def entity_tools_used_in_history(previous_conversations: list[dict[str, Any]] | None) -> bool:
    if not previous_conversations:
        return False
    return bool(tool_names_used_in_history(previous_conversations) & _ENTITY_HISTORY_NAMES)


def _grant(spec: "AgentSpec | None") -> None:
    """``tool_schemas_for_turn`` binds exactly ``spec.tool_names`` once it is
    non-empty, so a tool left out of the initial grant must be appended."""
    if spec is None or not spec.tool_names:
        return
    for name in PROGRESSIVE_TOOL_NAMES:
        if name not in spec.tool_names:
            spec.tool_names.append(name)


def progressive_entity_tools(context: "AgentContext") -> "Middleware[ToolResultContext]":
    """POST_TOOL_USE hook factory closing over the per-request `AgentContext`."""

    async def _middleware(ctx: "ToolResultContext", next_fn: "Next") -> None:
        await next_fn()

        from app.agents.agent_loop.hooks._tool_naming import resolve_tool_name

        if resolve_tool_name(ctx) not in _ENTITY_SEARCH_TRIGGER_NAMES:
            return

        run_scope = ctx.scope.turn.run if ctx.scope is not None else None
        if run_scope is not None:
            _grant(run_scope.spec)
            if getattr(run_scope, "visible_tools", None) is not None:
                run_scope.visible_tools.update(PROGRESSIVE_TOOL_NAMES)
        # Hooks are shared with sub-agents; only grant to the root when it
        # can call search_entities itself (never deep mode's orchestrator).
        root_spec = context.root_agent_spec
        if root_spec is not None and SEARCH_ENTITIES_TOOL_NAME in (root_spec.tool_names or []):
            _grant(root_spec)

    return _middleware


__all__ = [
    "ENTITY_TOOL_NAMES",
    "PROGRESSIVE_FIND_RECORDS_TOOL_NAME",
    "PROGRESSIVE_TOOL_NAMES",
    "SEARCH_ENTITIES_TOOL_NAME",
    "entity_tools_used_in_history",
    "progressive_entity_tools",
]
