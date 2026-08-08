"""``progressive_entity_tools``: POST_TOOL_USE hook that reveals
``resolve_entity_filters``, ``find_records_by_entity``, ``expand_neighbors``,
and ``get_relationships`` only after the agent has made its first
knowledge-graph tool call, instead of binding their schemas to every LLM
call from turn 0.

Tool tiering (see the "Entity tools tiering" plan):
  - Essential (turn 0): ``search_entities`` — immediate entity discovery,
    granted unconditionally like ``search``/``navigate``/``list_files``/
    ``lookup_record``.
  - Progressive (this hook): ``resolve_entity_filters``,
    ``find_records_by_entity``, ``expand_neighbors``, ``get_relationships``
    — useful once the agent is actively working the knowledge graph, but
    not needed on most turns:
      * The deterministic ``entity_filter_resolution`` PRE_TOOL_USE hook
        (see ``hooks/entity_filter.py``) already applies entity filters
        silently on every ``knowledgegraph__search`` call, so
        ``resolve_entity_filters`` is genuinely optional.
      * ``find_records_by_entity``, ``expand_neighbors``, and
        ``get_relationships`` only make sense once an entityId is already
        in hand (from ``search_entities``/``resolve_entity_filters``), so
        gating them behind first KG use costs nothing.

``factory.py`` excludes all four from the initial ``AgentSpec.tool_names``
grant; this hook re-grants them — mirroring ``hooks/citations.py``'s
``_grant`` for ``dynamic_fetch_full_record`` — the first time the model
calls any must-have or essential knowledge tool. All four tools are already
registered on the shared ``ToolRegistry`` regardless (they're ``@tool``
methods on the same ``KnowledgeGraph`` essential toolset as the others —
see ``knowledge_graph.py``); only their initial VISIBILITY is deferred.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent_loop_lib.agent.spec import AgentSpec
    from app.agent_loop_lib.hooks.middleware.context import ToolResultContext
    from app.agent_loop_lib.hooks.middleware.pipeline import Middleware, Next
    from app.agents.agent_loop.context import AgentContext

logger = logging.getLogger(__name__)

# Kept in one place so factory.py's initial-grant exclusion and this hook's
# re-grant can never drift apart on the literal.
PROGRESSIVE_ENTITY_TOOL_NAME = "knowledgegraph__resolve_entity_filters"
PROGRESSIVE_FIND_RECORDS_TOOL_NAME = "knowledgegraph__find_records_by_entity"
PROGRESSIVE_EXPAND_NEIGHBORS_TOOL_NAME = "knowledgegraph__expand_neighbors"
PROGRESSIVE_GET_RELATIONSHIPS_TOOL_NAME = "knowledgegraph__get_relationships"
PROGRESSIVE_TOOL_NAMES: frozenset[str] = frozenset({
    PROGRESSIVE_ENTITY_TOOL_NAME, PROGRESSIVE_FIND_RECORDS_TOOL_NAME,
    PROGRESSIVE_EXPAND_NEIGHBORS_TOOL_NAME, PROGRESSIVE_GET_RELATIONSHIPS_TOOL_NAME,
})

# Any one of these counts as "the agent is using the knowledge graph" — the
# must-have content-discovery tools plus the essential entity-discovery
# tool (search_entities) that the progressive tools above complement.
# Both separator forms are listed because `resolve_tool_name` falls back to
# a single-underscore join of the last two path segments when the registry
# doesn't have `ctx.tool_path` registered (see `_tool_naming.py` — the
# same reason `INTERNAL_SEARCH_TOOL_NAMES` lists both forms there too).
_KNOWLEDGE_USE_TOOL_NAMES = frozenset({
    "knowledgegraph__search", "knowledgegraph_search",
    "knowledgegraph__navigate", "knowledgegraph_navigate",
    "knowledgegraph__list_files", "knowledgegraph_list_files",
    "knowledgegraph__lookup_record", "knowledgegraph_lookup_record",
    "knowledgegraph__search_entities", "knowledgegraph_search_entities",
})


def _grant(spec: "AgentSpec | None") -> None:
    """Appends every name in ``PROGRESSIVE_TOOL_NAMES`` onto
    ``spec.tool_names`` if not already present — same reasoning as
    ``hooks/citations.py``'s ``_grant``: ``tool_schemas_for_turn`` binds
    exactly ``spec.tool_names`` once it's non-empty, so a tool deliberately
    left out of the initial grant needs an explicit append to ever become
    callable."""
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

        if resolve_tool_name(ctx) not in _KNOWLEDGE_USE_TOOL_NAMES:
            return

        # No registry existence check needed: this only fires for the
        # calls listed in _KNOWLEDGE_USE_TOOL_NAMES above, which can only
        # happen if the `KnowledgeGraph` toolset loaded — and
        # resolve_entity_filters/find_records_by_entity are `@tool` methods
        # on that SAME class (`knowledge_graph.py`), so they are always
        # registered alongside them.
        run_scope = ctx.scope.turn.run if ctx.scope is not None else None
        if run_scope is not None:
            _grant(run_scope.spec)
            if getattr(run_scope, "visible_tools", None) is not None:
                run_scope.visible_tools.update(PROGRESSIVE_TOOL_NAMES)
        _grant(context.root_agent_spec)

    return _middleware


__all__ = [
    "PROGRESSIVE_ENTITY_TOOL_NAME",
    "PROGRESSIVE_FIND_RECORDS_TOOL_NAME",
    "PROGRESSIVE_EXPAND_NEIGHBORS_TOOL_NAME",
    "PROGRESSIVE_GET_RELATIONSHIPS_TOOL_NAME",
    "PROGRESSIVE_TOOL_NAMES",
    "progressive_entity_tools",
]
