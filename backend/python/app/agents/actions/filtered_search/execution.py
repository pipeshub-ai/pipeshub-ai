"""Shared execution path behind the three native-query search tools
(`filtered_search/tools.py`): validate the query is filter-only, run the
adapter, and permission-gate the hits through
`FilteredRetrievalBridge.resolve_and_gate`.

Deliberately stops at the gated listing and does NOT run PipesHub content
search itself, even when the caller passed `content_query` — that step is
the POST_TOOL_USE `filtered_retrieval` hook's job
(`agent_loop/hooks/filtered_retrieval.py`), by design: content search is a
deterministic step the model cannot skip or misuse, not something buried
in a tool method a future edit could quietly change. This function still
returns each accessible record's `virtual_record_id` precisely so that
hook can scope retrieval without redoing the permission gate.

Validation lives HERE, not in the tool method or the PRE_TOOL_USE hook, so
it always runs no matter how this function is called (including directly
from a unit test) and cannot be bypassed by a future caller. Identity
substitution (`currentUser()` -> the asking user's real account id) is the
PRE_TOOL_USE hook's job instead, because it must mutate the query *before*
this function ever sees it, using `ChatState` the hook already has —
see `agent_loop/hooks/filter_value_resolution.py`.

Kept as a plain function (not a method) so the tool layer stays a thin
wrapper around it — adding connector N+1 needs a new adapter (see
`filtered_search/adapters/`) plus one new thin tool method, and nothing
here changes.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from app.agents.actions.filtered_search.adapter import Pagination
from app.agents.actions.filtered_search.bridge import FilteredRetrievalBridge
from app.agents.actions.filtered_search.connector_context import get_user_key
from app.agents.actions.filtered_search.registry import FilterAdapterRegistry

if TYPE_CHECKING:
    from app.modules.agents.qna.chat_state import ChatState

logger = logging.getLogger(__name__)

_DEFAULT_LIMIT = 50


async def run_filtered_search(
    *,
    state: ChatState,
    connector_id: str,
    connector_type: str,
    client: Any,  # noqa: ANN401
    query: str,
    limit: int = _DEFAULT_LIMIT,
) -> tuple[bool, str]:
    """Validate, execute, and permission-gate one native filter-only search.

    *connector_id* is the graph connector this call targets and *client*
    is its already-authenticated client (both resolved by the caller via
    `connector_context.resolve_client_for_connector` — this function does
    not re-derive either, so it stays usable directly from unit tests).
    *query* is the connector-native query string (JQL/CQL/Slack operators),
    already identity-substituted by the PRE_TOOL_USE hook if it contained
    a self-reference token.
    """
    adapter_cls = FilterAdapterRegistry.get(connector_type)
    if adapter_cls is None:
        return False, json.dumps({"error": f"No filter adapter registered for connector type {connector_type!r}"})

    error = adapter_cls.validate_query(query)
    if error:
        return False, json.dumps({"error": error})

    adapter = adapter_cls()
    try:
        universe = await adapter.execute(query, client, Pagination(limit=limit))
    except Exception as e:
        logger.exception("run_filtered_search: adapter.execute failed for %s", connector_type)
        return False, json.dumps({"error": f"Native filter search failed: {e}"})

    graph_provider = state.get("graph_provider")
    org_id = state.get("org_id", "")
    user_id = state.get("user_id", "")
    if not graph_provider:
        return False, json.dumps({"error": "Graph provider not available"})

    user_key = await get_user_key(graph_provider, user_id)
    if not user_key:
        return False, json.dumps({"error": "User not found"})

    from app.connectors.sources.localKB.handlers.knowledge_hub_service import (
        FOLDER_MIME_TYPES,
    )

    bridge = FilteredRetrievalBridge(
        graph_provider=graph_provider,
        retrieval_service=state.get("retrieval_service"),
        folder_mime_types=FOLDER_MIME_TYPES,
    )
    bridged = await bridge.resolve_and_gate(universe, connector_id, user_key, org_id)

    response: dict[str, Any] = {
        "native_query": universe.native_query,
        "total_available": universe.total_available,
        "truncated": universe.truncated,
        "accessible_count": len(bridged.accessible_records),
        "denied_count": bridged.denied_count,
        "unresolved_count": len(bridged.unresolved_external_ids),
        "records": [
            {
                "record_id": r.id,
                "name": r.record_name,
                "web_url": r.weburl,
                "external_id": r.external_record_id,
                # Consumed by the POST_TOOL_USE `filtered_retrieval` hook to
                # scope content search — harmless, if opaque, if a model
                # reads it directly instead.
                "virtual_record_id": r.virtual_record_id,
            }
            for r in bridged.accessible_records
        ],
    }

    return True, json.dumps(response, default=str)


__all__ = ["run_filtered_search"]
