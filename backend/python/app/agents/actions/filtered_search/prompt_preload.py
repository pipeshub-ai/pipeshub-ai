"""Prompt-time preload for native filter search: renders one line per
filter-capable enabled connector — name, `connector_id`, which tool to
call — nothing else.

This is NOT a vocabulary dump. Entity types, supported-field prose, and
the record-group listing all used to live here; they are gone because the
tool schema and description now carry the query-language contract (see
`filtered_search/tools.py`), and specific values (project keys, space
keys, channel/account ids) are looked up on demand via
`list_filter_values`/`people_search` rather than preloaded — a tenant with
dozens of Jira projects and Confluence spaces should not pay that token
cost on every single turn regardless of whether the turn touches search
at all.

What is NOT deletable is the `connector_id` pointer itself: there are two
prompt routes, and on the chat route (`/chat/stream`) `SourceCatalog`
renders connector TYPE names only, deduped, with no UUIDs (see
`modules/agents/context/source_catalog.py`) — this preload is the only
place in that route's prompt a `connector_id` appears at all.

Deliberately async and run once per request BEFORE the (synchronous)
`PipesHubPromptBuilder._build_blocks` — see `factory.py::create`, which
awaits `preload_native_filter_search` and stashes the rendered text on
`ChatState` for the builder to read back synchronously, the same pattern
`resolve_attachments_for_goal` uses for attachment text.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.agents.actions.filtered_search.connector_context import get_user_key
from app.agents.actions.filtered_search.registry import FilterAdapterRegistry
from app.agents.actions.filtered_search.tools import TOOL_PATH_BY_ADAPTER

if TYPE_CHECKING:
    from app.agents.agent_loop.context import AgentContext

logger = logging.getLogger(__name__)

STATE_KEY = "_native_filter_search_prompt_text"


async def preload_native_filter_search(context: "AgentContext") -> None:
    """Populate `context.tool_state[STATE_KEY]` with the rendered section, or
    leave it unset when there is nothing to show (no filter-capable connector
    enabled, no graph provider, etc.) — callers treat a missing key as "omit
    the section", never as an error."""
    state = context.tool_state
    try:
        text = await _render(state)
    except Exception:
        logger.exception("preload_native_filter_search: failed, omitting section")
        return
    if text:
        state[STATE_KEY] = text


async def _render(state: dict[str, Any]) -> str:
    from app.agents.actions.knowledge_graph.catalog import ConnectorCatalog

    graph_provider = state.get("graph_provider")
    if not graph_provider:
        return ""
    org_id = state.get("org_id", "")
    user_id = state.get("user_id", "")
    user_key = await get_user_key(graph_provider, user_id)
    if not user_key:
        return ""

    catalog = await ConnectorCatalog.build(state, graph_provider=graph_provider, user_key=user_key, org_id=org_id)
    if catalog.is_empty():
        return ""

    lines = []
    for connector in catalog.connectors:
        adapter_cls = FilterAdapterRegistry.get(connector.type)
        if adapter_cls is None:
            continue
        tool_path = TOOL_PATH_BY_ADAPTER.get(adapter_cls)
        tool_name = tool_path.rsplit("/", 1)[-1] if tool_path else "filter search"
        lines.append(f"- **{connector.name}** — app: {connector.type.lower()} — id `{connector.id}` — use `{tool_name}`")

    if not lines:
        return ""

    return (
        "## Native Filter Search\n\n"
        "These connectors support native query filter search. Call the named tool with the "
        "connector's own query language (JQL/CQL/Slack operators) — filters only, never a "
        "keyword/topic. Use `list_filter_values`/`people_search` to look up exact project/space/"
        "channel keys and account ids before writing the query; do not guess them.\n\n"
        + "\n".join(lines)
    )


__all__ = ["preload_native_filter_search", "STATE_KEY"]
