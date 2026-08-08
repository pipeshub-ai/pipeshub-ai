"""``entity_filter_resolution``: PRE_TOOL_USE hook — the deterministic,
always-on counterpart to the ``resolve_entity_filters`` tool (see
``app/agents/actions/knowledge_graph/ops/entity_search.py``).

Every ``knowledgegraph__search`` call is checked against the entities
vector collection for department/category/subcategory/topic/language
mentions in the query text; confident matches are cached (keyed by the
exact query string) for ``ops/search.py::execute_search`` to fold into the
graph-side ``filter_groups`` — narrowing the permission-scoped candidate
set *before* vector search runs, with zero extra agent turns and no LLM
call of its own.

This mirrors the query-as-typed request's own "probabilistic vs
deterministic" framing: the ``resolve_entity_filters`` tool is the
probabilistic path (the agent decides whether/when to call it); this hook
is the deterministic path (always runs, same policy every time). Neither
requires the other — a query with no taxonomy mentions in it is a no-op
here and an unfiltered search proceeds exactly as before.

Fails open unconditionally: any exception, missing ``entity_vector_store``,
or empty match set just means "no automatic filter", never a blocked or
degraded search.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent_loop_lib.hooks.middleware.context import ToolCallContext
    from app.agent_loop_lib.hooks.middleware.pipeline import Middleware, Next
    from app.agents.agent_loop.context import AgentContext

logger = logging.getLogger(__name__)

# Same state key ops/search.py::resolve_entity_filter_groups reads.
_QUERY_ENTITY_FILTER_CACHE_KEY = "_kg_query_entity_filters"

# search_entities() top_k for the auto-filter pass — small on purpose: this
# runs on every search call, so it stays a cheap "did any taxonomy term
# clearly match" check, not a broad entity-discovery search (that's what
# the resolve_entity_filters tool is for).
_AUTO_FILTER_TOP_K = 5


def entity_filter_resolution(context: "AgentContext") -> "Middleware[ToolCallContext]":
    """PRE_TOOL_USE hook factory closing over the per-request `AgentContext`."""

    async def _middleware(ctx: "ToolCallContext", next_fn: "Next") -> None:
        try:
            from app.agents.actions.knowledge_graph.ops.entity_filters import (
                FILTERABLE_ENTITY_TYPES,
                group_entities_into_filters,
            )
            from app.agents.agent_loop.hooks._tool_naming import (
                INTERNAL_SEARCH_TOOL_NAMES,
                resolve_tool_name,
            )

            if resolve_tool_name(ctx) not in INTERNAL_SEARCH_TOOL_NAMES:
                await next_fn()
                return

            query = ctx.tool_input.get("query")
            if not query or not isinstance(query, str):
                await next_fn()
                return

            entity_vector_store = context.tool_state.get("entity_vector_store")
            if not entity_vector_store:
                await next_fn()
                return

            entities = await entity_vector_store.search_entities(
                query,
                context.org_id,
                entity_types=list(FILTERABLE_ENTITY_TYPES),
                top_k=_AUTO_FILTER_TOP_K,
            )
            filters = group_entities_into_filters(entities)
            if filters:
                context.tool_state.setdefault(_QUERY_ENTITY_FILTER_CACHE_KEY, {})[query] = filters
        except Exception as exc:
            logger.debug("entity_filter_resolution: skipped (fail-open): %s", exc, exc_info=True)
        await next_fn()

    return _middleware


__all__ = ["entity_filter_resolution"]
