"""PRE_TOOL_USE hook for the three native-query filter-search tools
(`search_jira_issues`, `search_confluence_content`, `search_slack_messages`
— see `filtered_search/tools.py`).

Deterministically (never left to the model) substitutes a self-reference
token in the model-authored native query (`currentUser()` for Jira/
Confluence, `from:me`/`to:me` for Slack) with the ASKING session user's
real identity on that connector.

This is a correctness fix, not a convenience. PipesHub runs both
team-scope connectors (admin/service credentials) and personal-scope ones;
on a team-scope connector, the native API would otherwise resolve
`currentUser()` against the connector's OWN service-account identity, so
"my tickets" would silently answer for the wrong person. Substituting the
real identity here means the native API never sees an ambiguous
self-reference at all.

The graph lookup (`resolve_self_identity`) only runs when the query
actually contains a self-reference token — `adapter_cls.has_self_reference`
is a cheap, synchronous, per-adapter check — so a plain filter query with
no self-reference costs this hook nothing beyond the connector-type
lookup already needed to find the adapter.

Scoped to exactly the three filtered-search tool paths (wired in
`factory.py::_build_hooks`), so it costs nothing for every other tool call.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.agents.actions.filtered_search.connector_context import (
    resolve_connector_type,
    resolve_self_identity,
)
from app.agents.actions.filtered_search.registry import FilterAdapterRegistry
from app.agents.actions.filtered_search.tools import NATIVE_QUERY_PARAM_BY_PATH

if TYPE_CHECKING:
    from app.agent_loop_lib.hooks.middleware.context import ToolCallContext
    from app.agent_loop_lib.hooks.middleware.pipeline import Middleware, Next
    from app.agents.agent_loop.context import AgentContext

logger = logging.getLogger(__name__)


def filter_value_resolution(context: "AgentContext") -> "Middleware[ToolCallContext]":
    async def _middleware(ctx: "ToolCallContext", next_fn: "Next") -> None:
        state = context.tool_state
        tool_input = ctx.tool_input
        connector_id = tool_input.get("connector_id")
        query_param = NATIVE_QUERY_PARAM_BY_PATH.get(ctx.tool_path)

        if not connector_id or not query_param:
            await next_fn()
            return

        query = tool_input.get(query_param)
        if not query:
            await next_fn()
            return

        connector_type = await resolve_connector_type(state, connector_id)
        if not connector_type:
            ctx.deny(
                f"Unknown or inaccessible connector_id={connector_id!r}. Check the Knowledge "
                "Sources section of the system prompt for a valid id."
            )
            return

        adapter_cls = FilterAdapterRegistry.get(connector_type)
        if adapter_cls is None:
            ctx.deny(f"Connector type {connector_type!r} does not support filter search.")
            return

        if adapter_cls.has_self_reference(query):
            try:
                source_user_id = await resolve_self_identity(state, connector_id)
            except Exception:
                logger.exception(
                    "filter_value_resolution: self-identity lookup failed for connector=%s", connector_id,
                )
                source_user_id = None
            if not source_user_id:
                ctx.deny(
                    "You have no resolvable identity on this connector, so the self-reference in "
                    f"{query_param}={query!r} cannot be safely substituted — executing it as-is "
                    "risks answering for the wrong person. Ask the user for the exact person "
                    "instead, or call people_search."
                )
                return
            tool_input[query_param] = adapter_cls.substitute_identity(query, source_user_id)

        # Handed to the POST_TOOL_USE `filtered_retrieval` hook via the
        # shared `tool_use_id` (see `ToolResultContext.metadata`'s
        # docstring) — that hook has no other way to see this call's
        # `content_query`/`limit`, since `ToolResultContext` only carries
        # the tool's OUTPUT, not its original input.
        ctx.metadata["filtered_search_call"] = {
            "content_query": tool_input.get("content_query"),
            "limit": tool_input.get("limit"),
        }

        await next_fn()

    return _middleware


__all__ = ["filter_value_resolution"]
