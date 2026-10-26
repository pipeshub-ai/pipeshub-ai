"""Name the run's selected nodes in the prompt, one page at a time.

The page is the product's own selection cap, so a normal selection fits and
the model only needs the cursor when the list is longer than that. The next
page is `knowledgegraph__list_files` with that cursor.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent_loop_lib.hooks.middleware.context import AgentLifecycleContext
    from app.agent_loop_lib.hooks.middleware.pipeline import Middleware, Next
    from app.agents.agent_loop.context import AgentContext

logger = logging.getLogger(__name__)

__all__ = ["selected_nodes_prompt"]


def selected_nodes_prompt(context: "AgentContext") -> "Middleware[AgentLifecycleContext]":
    """PRE_AGENT hook. The selection does not change during one run, and the
    section is rebuilt into the system prompt on every turn."""

    async def _middleware(ctx: "AgentLifecycleContext", next_fn: "Next") -> None:
        scope = ctx.scope
        if scope is None or not context.graph_provider:
            await next_fn()
            return
        try:
            from app.agents.actions.knowledge_graph.ops.scope import list_selected_nodes

            text = await list_selected_nodes(
                {"filters": context.filters or {}},
                context.graph_provider,
                context.user_id,
                context.org_id,
            )
        except Exception:
            logger.exception("selected nodes could not be named for the prompt")
            text = None
        if text:
            scope.extra_prompt_sections["selected_nodes"] = text
        await next_fn()

    return _middleware
