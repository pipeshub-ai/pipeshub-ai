"""POST_TOOL_USE hook for the three native-query filter-search tools
(`search_jira_issues`, `search_confluence_content`, `search_slack_messages`
— see `filtered_search.tools.FILTERED_SEARCH_TOOL_PATHS`).

Owns the ONE thing the tool method deliberately does not do itself: when
the call included a `content_query`, run PipesHub's own permission-scoped
retrieval over exactly the accessible records the filter matched, and
replace the tool's plain listing with the enriched content results.

Splitting this out of the tool method (see `filtered_search/execution.py`'s
docstring) makes "filters go to the native API, content search goes
through PipesHub" a property of the wiring in `factory.py::_build_hooks`,
not of the tool implementation happening to call the right thing today —
consistent with the design's general preference for deterministic
middleware over relying on a tool (or, upstream, the model) to always do
the right probabilistic thing.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from app.agent_loop_lib.tools.base import ToolOutput
from app.agents.actions.filtered_search.bridge import search_within_virtual_record_ids

if TYPE_CHECKING:
    from app.agent_loop_lib.hooks.middleware.context import ToolResultContext
    from app.agent_loop_lib.hooks.middleware.pipeline import Middleware, Next
    from app.agents.agent_loop.context import AgentContext

logger = logging.getLogger(__name__)

_DEFAULT_LIMIT = 50


def filtered_retrieval(context: "AgentContext") -> "Middleware[ToolResultContext]":
    async def _middleware(ctx: "ToolResultContext", next_fn: "Next") -> None:
        await next_fn()

        output = ctx.tool_response
        if not output.success or not isinstance(output.data, str):
            return

        call_meta = ctx.metadata.get("filtered_search_call") or {}
        content_query = call_meta.get("content_query")
        if not content_query:
            return

        try:
            payload = json.loads(output.data)
        except (TypeError, ValueError):
            logger.warning("filtered_retrieval: tool output was not JSON, skipping")
            return

        records = payload.get("records") or []
        virtual_record_ids = [r["virtual_record_id"] for r in records if r.get("virtual_record_id")]

        response = await search_within_virtual_record_ids(
            context.retrieval_service,
            virtual_record_ids,
            content_query,
            context.user_id,
            context.org_id,
            limit=call_meta.get("limit") or _DEFAULT_LIMIT,
        )

        if response is None:
            payload["content_query"] = content_query
            payload["message"] = (
                "Filter search succeeded but content search could not run "
                "(no accessible records matched, or retrieval is unavailable)."
            )
        else:
            search_results = response.get("searchResults", [])
            payload = {
                **payload,
                "content_query": content_query,
                "content_matches": len(search_results),
                "results": search_results,
            }
            payload.pop("records", None)

        ctx.tool_response = ToolOutput(success=True, data=json.dumps(payload, default=str), sources=output.sources)

    return _middleware


__all__ = ["filtered_retrieval"]
