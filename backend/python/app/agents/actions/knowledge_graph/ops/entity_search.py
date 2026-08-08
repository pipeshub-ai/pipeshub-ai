"""``resolve_entity_filters`` operation — the probabilistic, agent-initiated
counterpart to the deterministic ``entity_filter_resolution`` hook (see
``hooks/entity_filter.py``).

Both resolve natural-language entity mentions (a department, topic,
category, language, or person name) to canonical graph entity IDs via
``EntityVectorStore.search_entities`` — the hook does this silently on
every ``knowledgegraph__search`` call; this tool lets the agent do it
explicitly (e.g. to inspect candidates before deciding, or to pass
``entity_ids`` back into a later ``search()`` call).
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from app.agents.actions.knowledge_graph.ops.entity_filters import (
    ENTITY_TYPE_TO_FILTER_KEY,
)

if TYPE_CHECKING:
    from app.modules.agents.qna.chat_state import ChatState

logger = logging.getLogger(__name__)

# See ops/search.py's _ENTITY_ID_FILTER_KEY_CACHE_KEY — same key, so a
# search() call right after this tool can resolve the IDs it returned.
_ENTITY_ID_FILTER_KEY_CACHE_KEY = "_kg_entity_id_filter_key"
_MAX_TOP_K = 25
_DEFAULT_TOP_K = 10


async def execute_resolve_entity_filters(
    state: "ChatState",
    query: str | None,
    entity_types: list[str] | None = None,
    top_k: int = _DEFAULT_TOP_K,
) -> str:
    """Resolve *query* against the entities vector collection.

    Returns a JSON string: ``{"status": "success", "results": [...]}`` where
    each result carries ``entityId``/``entityType``/``name``/``canonicalName``/
    ``score``/``filterable`` (whether it can be passed as ``entity_ids`` to
    ``search()``). Caches the entityId → filter-key mapping on ``state`` so a
    follow-up ``search(entity_ids=[...])`` call can resolve them.
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
        logger.warning("resolve_entity_filters: search_entities failed: %s", exc, exc_info=True)
        return json.dumps({"status": "error", "message": f"Entity search failed: {exc}"})

    if not entities:
        return json.dumps({
            "status": "success",
            "message": "No matching entities found",
            "results": [],
        })

    # Maps entityId -> (filter_key, entity_name) so a later search(entity_ids=[...])
    # call can both find the right filter_groups bucket AND resolve the actual
    # graph-matchable name — the graph queries filter on name properties, never
    # on entityId (see entity_filters.py's group_entities_into_filters docstring).
    id_to_key: dict[str, tuple[str, str]] = state.get(_ENTITY_ID_FILTER_KEY_CACHE_KEY) or {}
    results: list[dict[str, Any]] = []
    for entity in entities:
        entity_id = entity.get("entityId")
        entity_type = entity.get("entityType")
        entity_name = entity.get("name") or entity.get("canonicalName")
        if not entity_id or not entity_type:
            continue
        filter_key = ENTITY_TYPE_TO_FILTER_KEY.get(entity_type)
        if filter_key and entity_name:
            id_to_key[entity_id] = (filter_key, entity_name)
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
    state[_ENTITY_ID_FILTER_KEY_CACHE_KEY] = id_to_key

    try:
        _remember_mentions(state, entities)
    except Exception:
        logger.debug("resolve_entity_filters: mention tracking skipped", exc_info=True)

    return json.dumps({"status": "success", "results": results})


def _remember_mentions(state: "ChatState", entities: list[dict[str, Any]]) -> None:
    """Feed resolved entities into the mention map ``hooks/mention_binding.py``
    reads for pronoun resolution ("that department", "it") on later turns.
    Best-effort — mention binding degrades gracefully to no-op when empty.
    """
    try:
        from app.agents.agent_loop.hooks.mention_binding import remember_entity_mentions
        remember_entity_mentions(state, entities)
    except Exception:
        logger.debug("resolve_entity_filters: mention tracking skipped", exc_info=True)


__all__ = ["execute_resolve_entity_filters"]
