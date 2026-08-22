"""Knowledge graph listing operation.

Extracted from KnowledgeHub.list_files and updated to:
- Accept ``source_ids`` instead of the split ``connector_ids`` / ``record_group_ids``
- Return flat-text output (same grammar as navigate) instead of JSON
- Drop the four unreachable parameters: parent_id, parent_type, only_containers, flattened

The underlying KnowledgeHubService call is unchanged; only the LLM surface
is simplified.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from app.connectors.sources.localKB.api.knowledge_hub_models import (
    KnowledgeHubNodesResponse,
    NodeItem,
    NodeType,
)
from app.connectors.sources.localKB.handlers.knowledge_hub_service import KnowledgeHubService

if TYPE_CHECKING:
    from app.modules.agents.qna.chat_state import ChatState
    from app.utils.chat_helpers import RecordIdShortener

logger = logging.getLogger(__name__)

_VALID_NODE_TYPES = {nt.value for nt in NodeType}
_VALID_SORT_FIELDS = {"name", "createdAt", "updatedAt", "size", "type"}
_VALID_SORT_ORDERS = {"asc", "desc"}

_FETCHABLE_NODE_TYPES = frozenset({NodeType.RECORD.value, NodeType.FOLDER.value})

MIN_QUERY_LENGTH = 2
MAX_QUERY_LENGTH = 500

_NODE_TYPE_LABELS = {
    NodeType.APP.value: "App",
    NodeType.RECORD_GROUP.value: "RecordGroup",
    NodeType.FOLDER.value: "Folder",
    NodeType.RECORD.value: "Record",
}


def _record_ids_in_items(items: list[NodeItem] | None) -> list[str]:
    ids: list[str] = []
    for item in items or []:
        node_type = getattr(item.nodeType, "value", item.nodeType)
        if node_type in _FETCHABLE_NODE_TYPES and item.id:
            ids.append(item.id)
    return ids


def _normalize_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        v = value.strip()
        return [v] if v else None
    if isinstance(value, list):
        filtered = [str(v) for v in value if v]
        return filtered if filtered else None
    return None


def _render_flat_text(
    response: KnowledgeHubNodesResponse,
    query: str | None,
    shortener: "RecordIdShortener | None" = None,
) -> str:
    """Render list_files output in the same flat-text grammar as navigate().

    `shortener` is the TEMPORARY token-savings `RecordIdShortener` (see
    `app.utils.chat_helpers`) — when supplied, every printed id becomes a
    short `R<n>` label instead of the full record id.
    """
    if not response.success:
        error = response.error or "Failed to browse knowledge files"
        return f"Error: {error}"

    items = response.items or []
    if not items:
        if query:
            return f'No items found matching "{query}".'
        return "No items found."

    lines: list[str] = []
    header_parts: list[str] = [f"Found {len(items)} item{'s' if len(items) != 1 else ''}"]
    if query:
        header_parts.append(f'matching "{query}"')
    lines.append(" ".join(header_parts) + ".")
    lines.append("")

    for item in items:
        node_type = getattr(item.nodeType, "value", str(item.nodeType))
        label = _NODE_TYPE_LABELS.get(node_type, node_type)
        sub = getattr(item, "subType", None) or ""
        type_str = f"{label}/{sub}" if sub else label

        id_label = "record_id" if node_type in _FETCHABLE_NODE_TYPES else "node_id"
        item_id = shortener.get_or_create_short_id(item.id) if shortener is not None else item.id
        line = f"  [{type_str}] {item.name}  | {id_label}={item_id}"
        if hasattr(item, "webUrl") and item.webUrl:
            line += f"  | url={item.webUrl}"
        lines.append(line)

    lines.append("")
    total = getattr(response, "total", None)
    if total and total > len(items):
        remaining = total - len(items)
        lines.append(f"({remaining} more items — increase page or limit to see them)")
        lines.append("")

    lines.append(
        "Next: pass record_id to knowledgegraph__navigate() to see its children, "
        "or to knowledgegraph__fetch_record() to read its content."
    )
    return "\n".join(lines)


async def execute_list_files(
    state: "ChatState",
    *,
    query: str | None = None,
    source_ids: list[str] | None = None,
    node_types: list[str] | None = None,
    record_types: list[str] | None = None,
    page: int = 1,
    limit: int = 20,
    sort_by: str = "updatedAt",
    sort_order: str = "desc",
) -> tuple[bool, str]:
    """List or search items in the knowledge graph.

    ``source_ids`` accepts both app-connector IDs and KB-collection IDs;
    the function partitions them internally.

    Returns ``(success, text)`` in the flat-text grammar.
    """
    if not state:
        return False, "Tool state not initialized."

    try:
        logger_instance = state.get("logger", logger)
        graph_provider = state.get("graph_provider")
        org_id = state.get("org_id", "")
        user_id = state.get("user_id", "")

        if not graph_provider:
            return False, "Graph provider not available."

        from app.agents.actions.knowledge_graph.ops.scope import resolve_scope

        scope = await resolve_scope(state, allow_catalog_fallback=True)
        if scope.is_empty():
            return False, "No knowledge sources configured for this agent."

        agent_connector_ids = list(scope.app_ids)
        kb_ids = set(scope.kb_ids)

        # Input normalization
        query = query.strip() if query else None
        if query and len(query) < MIN_QUERY_LENGTH:
            query = None
        elif query and len(query) > MAX_QUERY_LENGTH:
            query = query[:MAX_QUERY_LENGTH]

        source_ids_norm = _normalize_list(source_ids)
        node_types = _normalize_list(node_types)
        record_types = _normalize_list(record_types)

        if node_types:
            node_types = [nt for nt in node_types if nt in _VALID_NODE_TYPES] or None

        page = max(1, page)
        limit = min(max(1, limit), 50)
        if sort_by not in _VALID_SORT_FIELDS:
            sort_by = "updatedAt"
        if sort_order not in _VALID_SORT_ORDERS:
            sort_order = "desc"

        # Resolve source_ids against the agent's scope.
        # source_ids can contain both app connector ids and KB ids.
        if source_ids_norm:
            all_scope = set(agent_connector_ids) | kb_ids
            matched = [sid for sid in source_ids_norm if sid in all_scope]
            if matched:
                use_apps = [sid for sid in matched if sid in set(agent_connector_ids)]
                use_kbs = {sid for sid in matched if sid in kb_ids}
            else:
                use_apps = agent_connector_ids
                use_kbs = kb_ids
        elif query:
            use_apps = agent_connector_ids
            use_kbs = kb_ids
        else:
            # Browse without query — scope to all configured apps
            use_apps = agent_connector_ids
            use_kbs = kb_ids

        use_connector_ids = use_apps or None
        use_record_group_ids = list(use_kbs) if use_kbs else None

        service = KnowledgeHubService(
            logger=logger_instance,
            graph_provider=graph_provider,
        )

        response = await service.get_nodes(
            user_id=user_id,
            org_id=org_id,
            parent_id=None,
            parent_type=None,
            only_containers=False,
            page=page,
            limit=limit,
            sort_by=sort_by,
            sort_order=sort_order,
            q=query,
            node_types=node_types,
            record_types=record_types,
            connector_ids=use_connector_ids,
            flattened=False,
            record_group_ids=use_record_group_ids,
        )

        from app.modules.agents.qna.chat_state import remember_record_ids
        remember_record_ids(state, _record_ids_in_items(response.items))

        from app.utils.chat_helpers import get_record_id_shortener_if_enabled
        record_id_shortener = get_record_id_shortener_if_enabled(state)

        return True, _render_flat_text(response, query, record_id_shortener)

    except Exception as exc:
        logger_instance = state.get("logger", logger) if state else logger
        logger_instance.error("knowledgegraph__list_files error: %s", exc, exc_info=True)
        return False, json.dumps({"status": "error", "message": f"Listing error: {exc}"})
