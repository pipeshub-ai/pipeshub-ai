"""``expand_neighbors`` and ``get_relationships`` operations — progressive
entity-to-entity traversal tools that let the agent explicitly inspect the
graph neighborhood around one or two entities, instead of only seeing it as
a side-effect of ``search_entities`` enrichment.

Both reuse ``IGraphDBProvider`` methods that already exist for other
purposes:

- ``expand_neighbors`` wraps ``get_entity_relationships`` (the same call
  ``search_entities`` makes internally) as a directly callable tool — useful
  once the agent already holds an ``entityId`` from an earlier turn and
  wants to re-inspect its neighborhood without a fresh search.
- ``get_relationships`` calls ``get_entity_pair_relationships`` to answer
  "how are entity A and entity B connected?" via direct edges (today, only
  category<->subcategory hierarchy) and shared records.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from app.agents.actions.knowledge_graph.ops.entity_filters import (
    ENTITY_TYPE_TO_NODE_COLLECTION,
)

if TYPE_CHECKING:
    from app.modules.agents.qna.chat_state import ChatState

logger = logging.getLogger(__name__)

_SUPPORTED_ENTITY_TYPES = tuple(ENTITY_TYPE_TO_NODE_COLLECTION)


async def execute_expand_neighbors(
    state: "ChatState",
    entity_id: str | None,
    entity_type: str | None,
) -> str:
    """Full 1-hop neighborhood of a single entity — hierarchy, co-occurring
    entities, connected record preview/count, and (for ``person``)
    relationship types. Same data ``search_entities`` enrichment shows, but
    callable directly once an ``entityId`` is already in hand."""
    if not state:
        return json.dumps({"status": "error", "message": "Tool state not initialized"})
    if not entity_id or not entity_type:
        return json.dumps({"status": "error", "message": "entity_id and entity_type are required"})
    if entity_type not in _SUPPORTED_ENTITY_TYPES:
        supported = ", ".join(sorted(_SUPPORTED_ENTITY_TYPES))
        return json.dumps({
            "status": "error",
            "message": f"Unsupported entity_type={entity_type!r} — supported types: {supported}",
        })

    graph_provider = state.get("graph_provider")
    if not graph_provider:
        return json.dumps({"status": "error", "message": "Graph provider not available"})

    org_id = state.get("org_id", "")
    try:
        relationships = await graph_provider.get_entity_relationships(
            org_id=org_id, entity_id=entity_id, entity_type=entity_type,
        )
    except Exception as exc:
        logger.warning("expand_neighbors: get_entity_relationships failed: %s", exc, exc_info=True)
        return json.dumps({"status": "error", "message": f"Neighborhood lookup failed: {exc}"})

    _remember_relationship_record_ids(state, [relationships])
    return json.dumps({
        "status": "success",
        "entityId": entity_id,
        "entityType": entity_type,
        "relationships": relationships,
    })


async def execute_get_relationships(
    state: "ChatState",
    source_entity_id: str | None,
    source_entity_type: str | None,
    target_entity_id: str | None,
    target_entity_type: str | None,
) -> str:
    """How two entities are connected — direct edges (category<->subcategory
    hierarchy today) plus shared records (records tagged with both
    entities)."""
    if not state:
        return json.dumps({"status": "error", "message": "Tool state not initialized"})
    if not source_entity_id or not source_entity_type or not target_entity_id or not target_entity_type:
        return json.dumps({
            "status": "error",
            "message": "source_entity_id, source_entity_type, target_entity_id, and target_entity_type are required",
        })
    unsupported = [
        t for t in (source_entity_type, target_entity_type) if t not in _SUPPORTED_ENTITY_TYPES
    ]
    if unsupported:
        supported = ", ".join(sorted(_SUPPORTED_ENTITY_TYPES))
        return json.dumps({
            "status": "error",
            "message": f"Unsupported entity_type(s) {unsupported} — supported types: {supported}",
        })

    graph_provider = state.get("graph_provider")
    if not graph_provider:
        return json.dumps({"status": "error", "message": "Graph provider not available"})

    org_id = state.get("org_id", "")
    try:
        pair_relationships = await graph_provider.get_entity_pair_relationships(
            org_id=org_id,
            source_entity_id=source_entity_id,
            source_entity_type=source_entity_type,
            target_entity_id=target_entity_id,
            target_entity_type=target_entity_type,
        )
    except Exception as exc:
        logger.warning("get_relationships: get_entity_pair_relationships failed: %s", exc, exc_info=True)
        return json.dumps({"status": "error", "message": f"Relationship lookup failed: {exc}"})

    _remember_relationship_record_ids(state, [pair_relationships], key="sharedRecords")
    return json.dumps({
        "status": "success",
        "sourceEntityId": source_entity_id,
        "sourceEntityType": source_entity_type,
        "targetEntityId": target_entity_id,
        "targetEntityType": target_entity_type,
        **pair_relationships,
    })


def _remember_relationship_record_ids(
    state: "ChatState",
    relationship_dicts: list[dict],
    key: str = "connectedRecords",
) -> None:
    """Mirrors ``entity_discovery._remember_connected_record_ids`` — surfaces
    record IDs from the neighborhood/pair-relationship response immediately
    so ``fetch_record`` is available on the same POST_TOOL_USE pass."""
    record_ids = [
        record.get("recordId")
        for relationships in relationship_dicts
        for record in (relationships.get(key) or [])
        if record.get("recordId")
    ]
    if not record_ids:
        return
    try:
        from app.modules.agents.qna.chat_state import remember_record_ids
        remember_record_ids(state, record_ids)
    except Exception:
        logger.debug("entity_traversal: remember_record_ids skipped", exc_info=True)


__all__ = ["execute_expand_neighbors", "execute_get_relationships"]
