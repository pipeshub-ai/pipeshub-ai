"""``search_entities`` operation — the *essential* (turn-0) counterpart
to the progressive ``resolve_entity_filters`` tool (see ``ops/entity_search.py``).

Semantic lookup by name/description, same underlying
``EntityVectorStore.search_entities`` call as ``resolve_entity_filters``,
but does NOT cache a filter-key mapping (no side effect on a later
``search()`` call) and optionally enriches top hits with a graph
relationship summary (parent/children, co-occurring entities, connected
record count) via ``IGraphDBProvider.get_entity_relationships``. Any
``connectedRecords`` surfaced by that enrichment are also remembered via
``remember_record_ids`` so ``fetch_record`` is available immediately,
without waiting for a ``find_records_by_entity`` call.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.modules.agents.qna.chat_state import ChatState

logger = logging.getLogger(__name__)

_MAX_TOP_K = 25
_DEFAULT_TOP_K = 10
_MAX_ENRICHED_RESULTS = 5


async def execute_search_entities(
    state: "ChatState",
    query: str | None,
    entity_types: list[str] | None = None,
    top_k: int = _DEFAULT_TOP_K,
    include_relationships: bool = True,
) -> str:
    """Semantic entity discovery — essential-tier sibling of
    ``resolve_entity_filters`` (see ``ops/entity_search.py``).

    Returns ``{"status": "success", "results": [...]}`` where each result
    has the same shape as ``resolve_entity_filters`` plus, for the top
    ``_MAX_ENRICHED_RESULTS`` hits when ``include_relationships`` is true, a
    ``relationships`` key: ``{parentEntity, childEntities, relationshipTypes,
    connectedRecordCount}`` (see ``IGraphDBProvider.get_entity_relationships``).
    """
    if not query or not query.strip():
        return json.dumps({"status": "error", "message": "No query provided"})

    if not state:
        return json.dumps({"status": "error", "message": "Tool state not initialized"})

    entity_vector_store = state.get("entity_vector_store")
    if not entity_vector_store:
        return json.dumps({
            "status": "error",
            "message": "Entity search is not available in this deployment",
        })

    org_id = state.get("org_id", "")
    bounded_top_k = max(1, min(top_k or _DEFAULT_TOP_K, _MAX_TOP_K))

    try:
        entities = await entity_vector_store.search_entities(
            query,
            org_id,
            entity_types=entity_types or None,
            top_k=bounded_top_k,
        )
    except Exception as exc:
        logger.warning("search_entities: search_entities failed: %s", exc, exc_info=True)
        return json.dumps({"status": "error", "message": f"Entity search failed: {exc}"})

    if not entities:
        return json.dumps({
            "status": "success",
            "message": "No matching entities found",
            "results": [],
        })

    from app.agents.actions.knowledge_graph.ops.entity_filters import (
        ENTITY_TYPE_TO_FILTER_KEY,
    )

    results: list[dict[str, Any]] = []
    for entity in entities:
        entity_id = entity.get("entityId")
        entity_type = entity.get("entityType")
        if not entity_id or not entity_type:
            continue
        entity_name = entity.get("name") or entity.get("canonicalName")
        filter_key = ENTITY_TYPE_TO_FILTER_KEY.get(entity_type)
        results.append({
            "entityId": entity_id,
            "entityType": entity_type,
            "name": entity.get("name"),
            "canonicalName": entity.get("canonicalName") or None,
            "aliases": entity.get("aliases") or [],
            "parentEntityId": entity.get("parentEntityId"),
            "parentEntityType": entity.get("parentEntityType"),
            "score": entity.get("score"),
            "filterable": bool(filter_key and entity_name),
        })

    if include_relationships:
        await _enrich_with_relationships(state, org_id, results)
        _remember_connected_record_ids(state, results)

    try:
        from app.agents.agent_loop.hooks.mention_binding import remember_entity_mentions
        remember_entity_mentions(state, entities)
    except Exception:
        logger.debug("search_entities: mention tracking skipped", exc_info=True)

    return json.dumps({"status": "success", "results": results})


async def _enrich_with_relationships(
    state: "ChatState", org_id: str, results: list[dict[str, Any]],
) -> None:
    """Best-effort: attach a ``relationships`` summary to the top few hits.
    Any per-entity failure is swallowed — this is orientation context, never
    required for the tool call to succeed."""
    graph_provider = state.get("graph_provider")
    if not graph_provider:
        return
    for result in results[:_MAX_ENRICHED_RESULTS]:
        try:
            result["relationships"] = await graph_provider.get_entity_relationships(
                org_id=org_id,
                entity_id=result["entityId"],
                entity_type=result["entityType"],
            )
        except Exception as exc:
            logger.debug(
                "search_entities: relationship enrichment failed for %s: %s",
                result.get("entityId"), exc,
            )


def _remember_connected_record_ids(
    state: "ChatState", results: list[dict[str, Any]],
) -> None:
    """Surfaces ``connectedRecords`` IDs to ``citation_tracking`` immediately,
    so ``fetch_record`` is available on the same POST_TOOL_USE pass instead
    of requiring an intermediate ``find_records_by_entity`` call first."""
    record_ids = [
        cr["recordId"]
        for result in results
        for cr in (result.get("relationships") or {}).get("connectedRecords", [])
        if cr.get("recordId")
    ]
    if not record_ids:
        return
    try:
        from app.modules.agents.qna.chat_state import remember_record_ids
        remember_record_ids(state, record_ids)
    except Exception:
        logger.debug("search_entities: remember_record_ids skipped", exc_info=True)


__all__ = ["execute_search_entities"]
