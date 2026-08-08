"""``find_records_by_entity`` operation — once an entity (department,
category, topic, language) is identified via ``search_entities``/
``resolve_entity_filters``, find the records connected to it.

Uses a two-step approach instead of a single metadata-filtered
``get_accessible_virtual_record_ids`` call, because the metadata-filter
AQL path has a known false-negative issue (same root cause as Bug 7 —
see ``ops/search.py``'s filtered-then-fallback comment):

1. **Graph edge traversal** — INBOUND from the entity node through the
   ``belongsTo*`` edge collection to find all record keys directly linked
   to the entity.  This is the same edge set ``get_entity_relationships``
   counts for ``connectedRecordCount``.
2. **Permission check** — ``get_accessible_virtual_record_ids`` with NO
   metadata filters (proven reliable) to obtain the set of records the
   current user can actually see.
3. **Intersection** — only records that appear in BOTH sets are returned.

``person`` entities are informational-only today (see
``entity_filters.ENTITY_TYPE_TO_FILTER_KEY``'s docstring) — there is no
edge collection linking records to person entities, so they are rejected
with a clear message rather than silently returning nothing.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.agents.actions.knowledge_graph.ops.entity_filters import (
    ENTITY_TYPE_TO_EDGE_COLLECTION,
    ENTITY_TYPE_TO_FILTER_KEY,
    ENTITY_TYPE_TO_NAME_FIELD,
    ENTITY_TYPE_TO_NODE_COLLECTION,
)
from app.config.constants.arangodb import CollectionNames

if TYPE_CHECKING:
    from app.modules.agents.qna.chat_state import ChatState
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
    from app.utils.chat_helpers import RecordIdShortener

logger = logging.getLogger(__name__)

_MAX_LIMIT = 50
_DEFAULT_LIMIT = 20
_ENTITY_ID_FILTER_KEY_CACHE_KEY = "_kg_entity_id_filter_key"


async def execute_find_records_by_entity(
    state: "ChatState",
    entity_id: str | None,
    entity_type: str | None,
    record_types: list[str] | None = None,
    page: int = 1,
    limit: int = _DEFAULT_LIMIT,
) -> tuple[bool, str]:
    """Find records connected to a single entity, permission-filtered to the
    current user. Returns ``(success, text)`` in the same flat-text grammar
    as ``navigate``/``list_files`` — record_id= references are immediately
    usable with ``fetch_record``/``navigate``.
    """
    if not state:
        return False, "Knowledge graph tool state not initialized."
    if not entity_id or not entity_type:
        return False, "entity_id and entity_type are required."

    filter_key = ENTITY_TYPE_TO_FILTER_KEY.get(entity_type)
    if not filter_key:
        supported = ", ".join(sorted(ENTITY_TYPE_TO_FILTER_KEY))
        return False, (
            f"find_records_by_entity does not support entity_type={entity_type!r} yet — "
            f"supported types: {supported}. Person entities are informational only today; "
            "there is no record-linking filter for them."
        )

    edge_collection = ENTITY_TYPE_TO_EDGE_COLLECTION.get(entity_type)
    if not edge_collection:
        return False, f"No edge collection mapping for entity_type={entity_type!r}."

    node_collection = ENTITY_TYPE_TO_NODE_COLLECTION.get(entity_type)
    if not node_collection:
        return False, f"No node collection mapping for entity_type={entity_type!r}."

    graph_provider = state.get("graph_provider")
    if not graph_provider:
        return False, "Graph provider not available."

    org_id = state.get("org_id", "")
    user_id = state.get("user_id", "")

    entity_name = await _resolve_entity_name(state, graph_provider, entity_id, entity_type)
    if not entity_name:
        return False, f"Entity {entity_id!r} of type {entity_type!r} was not found."

    # Step 1: INBOUND traversal from entity to find connected record keys.
    entity_node_ref = f"{node_collection}/{entity_id}"
    try:
        connected_records = await graph_provider.get_related_nodes(
            entity_node_ref,
            edge_collection,
            CollectionNames.RECORDS.value,
            direction="inbound",
        )
    except Exception:
        logger.exception("find_records_by_entity: INBOUND edge traversal failed")
        return False, "Lookup failed — try again."

    if not connected_records:
        return True, f'No records found connected to "{entity_name}".'

    connected_keys = {r.get("_key") for r in connected_records if r.get("_key")}
    if not connected_keys:
        return True, f'No records found connected to "{entity_name}".'

    # Step 2: get the user's full accessible-record set (NO metadata filter —
    # the filterless path is proven reliable, unlike the metadata-filtered one).
    try:
        accessible_map = await graph_provider.get_accessible_virtual_record_ids(
            user_id=user_id, org_id=org_id,
        )
    except Exception:
        logger.exception("find_records_by_entity: get_accessible_virtual_record_ids failed")
        return False, "Lookup failed — try again."

    # Step 3: intersect — only records the user can access AND that are linked
    # to the entity.
    accessible_record_ids = set(accessible_map.values()) if accessible_map else set()
    permitted_keys = connected_keys & accessible_record_ids
    if not permitted_keys:
        return True, f'No accessible records found for "{entity_name}".'

    record_ids = sorted(permitted_keys)
    try:
        records = await graph_provider.get_records_by_record_ids(record_ids, org_id)
    except Exception:
        logger.exception("find_records_by_entity: get_records_by_record_ids failed")
        return False, "Lookup failed — try again."

    if record_types:
        wanted = {str(t).upper() for t in record_types}
        records = [r for r in records if str(r.get("recordType") or "").upper() in wanted]

    records.sort(key=lambda r: r.get("recordName") or "")

    page = max(1, page)
    limit = min(max(1, limit), _MAX_LIMIT)
    total = len(records)
    start = (page - 1) * limit
    page_records = records[start:start + limit]

    from app.modules.agents.qna.chat_state import remember_record_ids
    remember_record_ids(state, [r.get("_key") for r in page_records if r.get("_key")])

    from app.utils.chat_helpers import get_record_id_shortener_if_enabled
    record_id_shortener = get_record_id_shortener_if_enabled(state)

    text = _render_records(
        entity_name, entity_type, page_records, total, page, limit, record_id_shortener,
    )
    return True, text


async def _resolve_entity_name(
    state: "ChatState", graph_provider: "IGraphDBProvider", entity_id: str, entity_type: str,
) -> str | None:
    id_to_key: dict[str, tuple[str, str]] = state.get(_ENTITY_ID_FILTER_KEY_CACHE_KEY) or {}
    cached = id_to_key.get(entity_id)
    if cached and cached[0] == ENTITY_TYPE_TO_FILTER_KEY.get(entity_type):
        return cached[1]

    node_collection = ENTITY_TYPE_TO_NODE_COLLECTION.get(entity_type)
    name_field = ENTITY_TYPE_TO_NAME_FIELD.get(entity_type, "name")
    if not node_collection:
        return None
    try:
        doc = await graph_provider.get_document(entity_id, node_collection)
    except Exception:
        logger.warning("find_records_by_entity: get_document lookup failed", exc_info=True)
        return None
    if not doc:
        return None
    return doc.get(name_field) or doc.get("name")


def _render_records(
    entity_name: str,
    entity_type: str,
    records: list[dict[str, Any]],
    total: int,
    page: int,
    limit: int,
    record_id_shortener: "RecordIdShortener | None",
) -> str:
    if not records:
        return f'No accessible records found for "{entity_name}" (page {page}).'

    lines: list[str] = [
        f'Found {total} record{"s" if total != 1 else ""} connected to '
        f'{entity_type} "{entity_name}" (page {page}):',
        "",
    ]
    for record in records:
        name = record.get("recordName") or record.get("_key") or "Untitled"
        record_type = record.get("recordType") or "RECORD"
        record_id = record.get("_key")
        display_id = (
            record_id_shortener.get_or_create_short_id(record_id)
            if record_id_shortener is not None and record_id
            else record_id
        )
        line = f"  [{record_type}] {name}  | record_id={display_id}"
        web_url = record.get("webUrl")
        if web_url:
            line += f"  | url={web_url}"
        lines.append(line)

    lines.append("")
    start_next = page * limit
    if start_next and start_next < total:
        remaining = total - start_next
        lines.append(f"({remaining} more record{'s' if remaining != 1 else ''} — increase page to see them)")
        lines.append("")

    lines.append(
        "Next: pass record_id to knowledgegraph__navigate() to see its children, "
        "or to knowledgegraph__fetch_record() to read its content."
    )
    return "\n".join(lines)


__all__ = ["execute_find_records_by_entity"]
