"""``search_entities`` operation — semantic lookup over knowledge-graph
entities (departments, categories, subcategories, topics, languages, record
groups, record titles), limited to what the user can access.

Access is decided by ``app.modules.retrieval.entity_permissions``; this module
only shapes the result for the model and remembers the returned ids for
``find_records_by_entity`` and ``search(entity_ids=[...])``.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from app.agents.actions.knowledge_graph.ops.entity_filters import remember_entities
from app.agents.actions.knowledge_graph.ops.scope import derive_scope
from app.agents.actions.knowledge_graph.views import _compact_date, _short, _trunc
from app.modules.agents.qna.chat_state import remember_record_ids
from app.modules.retrieval.entity_permissions import (
    RECORD_ENTITY_TYPE,
    SEARCHABLE_ENTITY_TYPES,
    EntityAccessContext,
    EntityAccessError,
    EntityHit,
    get_entity_access_context,
    search_entities_for_user,
)
from app.utils.chat_helpers import get_record_id_shortener_if_enabled

if TYPE_CHECKING:
    from app.modules.agents.qna.chat_state import ChatState
    from app.utils.chat_helpers import RecordIdShortener

logger = logging.getLogger(__name__)

_MAX_TOP_K = 25
_DEFAULT_TOP_K = 10
_PREVIEW_ENTITY_COUNT = 3
_MAX_ALIASES_SHOWN = 5


def _error(message: str) -> str:
    return json.dumps({"status": "error", "message": message})


async def execute_search_entities(
    state: "ChatState",
    query: str | None,
    entity_types: list[str] | None = None,
    top_k: int | None = _DEFAULT_TOP_K,
) -> tuple[bool, str]:
    """Returns ``(success, json)``. A failure to resolve access or run the
    search is reported as a failed call, never as "no matching entities"."""
    if not query or not query.strip():
        return False, _error("No query provided")
    if not state:
        return False, _error("Tool state not initialized")

    entity_vector_store = state.get("entity_vector_store")
    graph_provider = state.get("graph_provider")
    if not entity_vector_store or not graph_provider:
        return False, _error("Entity search is not available in this deployment")

    requested = [str(t).strip().lower() for t in (entity_types or []) if str(t).strip()]
    valid_types = [t for t in dict.fromkeys(requested) if t in SEARCHABLE_ENTITY_TYPES]
    if requested and not valid_types:
        supported = ", ".join(sorted(SEARCHABLE_ENTITY_TYPES))
        return False, _error(f"Unsupported entity_types {requested}; supported: {supported}")

    bounded_top_k = max(
        1, min(top_k if top_k is not None else _DEFAULT_TOP_K, _MAX_TOP_K)
    )
    scope = derive_scope(state)
    try:
        context = await get_entity_access_context(
            state,
            graph_provider,
            org_id=state.get("org_id", ""),
            user_id=state.get("user_id", ""),
            source_ids=[*scope.app_ids, *scope.kb_ids],
        )
        hits = await search_entities_for_user(
            entity_vector_store,
            graph_provider,
            context,
            query,
            entity_types=valid_types or None,
            top_k=bounded_top_k,
        )
    except EntityAccessError:
        logger.warning("search_entities failed", exc_info=True)
        return False, _error("Entity search failed — try again.")

    ignored = sorted(set(requested) - SEARCHABLE_ENTITY_TYPES)
    if not hits:
        payload: dict[str, Any] = {
            "status": "success",
            "message": "No accessible entities matched",
            "results": [],
        }
        if ignored:
            payload["ignoredEntityTypes"] = ignored
        return True, json.dumps(payload)

    remember_entities(
        state,
        [{"entityId": h.entity_id, "entityType": h.entity_type, "name": h.name} for h in hits],
    )
    shortener = get_record_id_shortener_if_enabled(state)
    results, shown_record_ids = _render_hits(hits, context, shortener)
    remember_record_ids(state, shown_record_ids)

    payload = {"status": "success", "results": results}
    if ignored:
        payload["ignoredEntityTypes"] = ignored
    return True, json.dumps(payload)


def _render_hits(
    hits: list[EntityHit],
    context: EntityAccessContext,
    shortener: "RecordIdShortener | None",
) -> tuple[list[dict[str, Any]], list[str]]:
    results: list[dict[str, Any]] = []
    shown_record_ids: list[str] = []
    previews_left = _PREVIEW_ENTITY_COUNT
    for hit in hits:
        item: dict[str, Any] = {
            "entityId": hit.entity_id,
            "entityType": hit.entity_type,
            "name": _trunc(hit.name),
            "score": round(hit.score, 4),
        }
        if hit.aliases:
            # Other spellings merged into this entity, so the model can tell
            # the user's wording apart from the canonical name.
            item["aliases"] = [_trunc(a) for a in hit.aliases[:_MAX_ALIASES_SHOWN]]
        apps = sorted({
            context.app_names[r["connectorId"]]
            for r in hit.records
            if r.get("connectorId") in context.app_names
        })
        if apps:
            item["apps"] = apps
        if hit.entity_type == RECORD_ENTITY_TYPE:
            item["entityId"] = _short(hit.entity_id, shortener)
            shown_record_ids.append(hit.entity_id)
        elif previews_left > 0 and hit.records:
            previews_left -= 1
            item["records"] = [_preview_row(r, context, shortener) for r in hit.records]
            if hit.more_records:
                item["moreRecords"] = True
            shown_record_ids.extend(r["_key"] for r in hit.records if r.get("_key"))
        results.append(item)
    return results, shown_record_ids


def _preview_row(
    row: dict[str, Any],
    context: EntityAccessContext,
    shortener: "RecordIdShortener | None",
) -> dict[str, Any]:
    preview: dict[str, Any] = {
        "recordId": _short(row["_key"], shortener),
        "name": _trunc(row.get("recordName") or row["_key"]),
        "type": row.get("recordType"),
    }
    app = context.app_names.get(row.get("connectorId"))
    if app:
        preview["app"] = app
    modified = _compact_date(row.get("sourceLastModifiedTimestamp") or row.get("updatedAtTimestamp"))
    if modified:
        preview["modified"] = modified
    return preview


__all__ = ["execute_search_entities"]
