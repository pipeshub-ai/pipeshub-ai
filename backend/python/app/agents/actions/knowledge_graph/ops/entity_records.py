"""``find_records_by_entity`` operation — records connected to one entity
(taxonomy entity, record group, or a record title), limited to what the user
can access, newest first, paged by cursor.

Access is decided by ``app.modules.retrieval.entity_permissions``. An entity
the user cannot reach and an entity with no accessible records get the same
response, so the tool never confirms that inaccessible data exists.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.agents.actions.knowledge_graph.ops.entity_filters import ENTITY_INDEX_CACHE_KEY
from app.agents.actions.knowledge_graph.ops.scope import derive_scope
from app.agents.actions.knowledge_graph.views import (
    _compact_date,
    _read_hint,
    _short,
    _trunc,
)
from app.modules.agents.qna.chat_state import remember_record_ids
from app.modules.retrieval.entity_permissions import (
    RECORD_GROUP_ENTITY_TYPE,
    SEARCH_SCOPE_MAX_ENTITIES,
    SEARCH_SCOPE_MAX_RECORDS,
    SEARCH_SCOPE_MAX_SCAN,
    SEARCHABLE_ENTITY_TYPES,
    EntityAccessContext,
    EntityAccessError,
    get_entity_access_context,
    list_accessible_entity_records,
)
from app.utils.chat_helpers import get_record_id_shortener_if_enabled

if TYPE_CHECKING:
    from app.modules.agents.qna.chat_state import ChatState
    from app.utils.chat_helpers import RecordIdShortener

logger = logging.getLogger(__name__)

_MAX_LIMIT = 50
_DEFAULT_LIMIT = 20
_MAX_READ_HINT_IDS = 8
NO_ACCESSIBLE_RECORDS_MSG = "No accessible records found for this entity."
NO_FURTHER_RECORDS_MSG = (
    "No further accessible records for this entity - the previous page was the last."
)
LOOKUP_FAILED_MSG = "Lookup failed — try again."


async def _access_context(state: "ChatState") -> EntityAccessContext:
    scope = derive_scope(state)
    return await get_entity_access_context(
        state,
        state.get("graph_provider"),
        org_id=state.get("org_id", ""),
        user_id=state.get("user_id", ""),
        source_ids=[*scope.app_ids, *scope.kb_ids],
    )


async def execute_find_records_by_entity(
    state: "ChatState",
    entity_id: str | None,
    entity_type: str | None = None,
    record_types: list[str] | None = None,
    limit: int = _DEFAULT_LIMIT,
    cursor: str | None = None,
) -> tuple[bool, str]:
    if not state:
        return False, "Knowledge graph tool state not initialized."
    raw_id = (entity_id or "").strip()
    if not raw_id:
        return False, "entity_id is required."
    if not state.get("graph_provider"):
        return False, "Graph provider not available."

    shortener = get_record_id_shortener_if_enabled(state)
    resolved_id = shortener.resolve(raw_id) if shortener is not None else raw_id
    indexed: dict[str, str] = (state.get(ENTITY_INDEX_CACHE_KEY) or {}).get(resolved_id) or {}
    resolved_type = (entity_type or "").strip().lower() or indexed.get("type")
    supported = ", ".join(sorted(SEARCHABLE_ENTITY_TYPES))
    if not resolved_type:
        return False, (
            "entity_type is required for an entity that search_entities did not return "
            f"in this conversation — pass one of: {supported}."
        )
    if resolved_type not in SEARCHABLE_ENTITY_TYPES:
        return False, f"Unsupported entity_type {resolved_type!r} — pass one of: {supported}."

    wanted_types = [str(t).strip().upper() for t in (record_types or []) if str(t).strip()] or None
    bounded_limit = min(max(1, limit or _DEFAULT_LIMIT), _MAX_LIMIT)
    try:
        context = await _access_context(state)
        page = await list_accessible_entity_records(
            state["graph_provider"],
            context,
            entity_id=resolved_id,
            entity_type=resolved_type,
            record_types=wanted_types,
            limit=bounded_limit,
            cursor=cursor,
        )
    except ValueError as exc:
        return False, str(exc)
    except EntityAccessError:
        logger.warning("find_records_by_entity failed", exc_info=True)
        return False, LOOKUP_FAILED_MSG

    if not page.records:
        # After a page of results, "none found" reads as "this entity is empty"
        # and contradicts what the caller was just shown.
        return True, NO_FURTHER_RECORDS_MSG if cursor else NO_ACCESSIBLE_RECORDS_MSG

    record_ids = [r["_key"] for r in page.records if r.get("_key")]
    remember_record_ids(state, record_ids)
    name = indexed.get("name") if indexed.get("type") == resolved_type else None
    return True, _render_page(
        page.records,
        context,
        entity_ref=raw_id,
        entity_type=resolved_type,
        entity_name=name,
        next_cursor=page.next_cursor,
        shortener=shortener,
    )


def _render_page(
    records: list[dict[str, Any]],
    context: EntityAccessContext,
    *,
    entity_ref: str,
    entity_type: str,
    entity_name: str | None,
    next_cursor: str | None,
    shortener: "RecordIdShortener | None",
) -> str:
    preposition = "in" if entity_type == RECORD_GROUP_ENTITY_TYPE else "connected to"
    subject = f'{entity_type} "{_trunc(entity_name)}"' if entity_name else f"this {entity_type}"
    lines = [f"Records {preposition} {subject}, newest first ({len(records)} shown):"]
    for record in records:
        parts = [
            f"- [{record.get('recordType') or 'RECORD'}] {_trunc(record.get('recordName') or record['_key'])}",
            f"record_id={_short(record['_key'], shortener)}",
        ]
        app = context.app_names.get(record.get("connectorId"))
        if app:
            parts.append(f"app: {app}")
        modified = _compact_date(
            record.get("sourceLastModifiedTimestamp") or record.get("updatedAtTimestamp")
        )
        if modified:
            parts.append(f"modified: {modified}")
        if record.get("webUrl"):
            parts.append(f"url={record['webUrl']}")
        lines.append(" | ".join(parts))

    lines.append("")
    if next_cursor is not None:
        lines.append(
            "More records may exist: knowledgegraph__find_records_by_entity("
            f'entity_id="{entity_ref}", entity_type="{entity_type}", cursor="{next_cursor}")'
        )
    record_ids = [r["_key"] for r in records[:_MAX_READ_HINT_IDS]]
    lines.append(
        f"Next: {_read_hint(record_ids, shortener)}, or knowledgegraph__navigate(node_id=...) "
        "to see where a record sits."
    )
    return "\n".join(lines)


async def resolve_entity_virtual_ids(
    state: "ChatState", entities: list[tuple[str, str]],
) -> list[str]:
    """Deduplicated ``virtualRecordId``s of the accessible records connected to
    each ``(entity_id, entity_type)``, used to scope ``search(entity_ids=[...])``.
    Bounded per call; raises ``EntityAccessError`` on failure so search never
    silently widens to an unscoped query."""
    context = await _access_context(state)
    graph_provider = state["graph_provider"]
    seen: set[str] = set()
    virtual_ids: list[str] = []
    for entity_id, entity_type in list(dict.fromkeys(entities))[:SEARCH_SCOPE_MAX_ENTITIES]:
        remaining = SEARCH_SCOPE_MAX_RECORDS - len(virtual_ids)
        if remaining <= 0:
            break
        page = await list_accessible_entity_records(
            graph_provider,
            context,
            entity_id=entity_id,
            entity_type=entity_type,
            limit=remaining,
            max_scan=SEARCH_SCOPE_MAX_SCAN,
        )
        for record in page.records:
            virtual_id = record.get("virtualRecordId")
            if virtual_id and virtual_id not in seen:
                seen.add(virtual_id)
                virtual_ids.append(virtual_id)
    return virtual_ids


__all__ = [
    "LOOKUP_FAILED_MSG",
    "NO_ACCESSIBLE_RECORDS_MSG",
    "NO_FURTHER_RECORDS_MSG",
    "execute_find_records_by_entity",
    "resolve_entity_virtual_ids",
]
