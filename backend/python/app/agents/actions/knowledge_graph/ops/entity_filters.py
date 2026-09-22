"""State shared between ``search_entities`` and the tools that consume its ids.

``search_entities`` (``ops/entity_discovery.py``) remembers what it returned
so later calls in the same request can use those ids without trusting the
model for anything but the id itself:

- ``search(entity_ids=[...])`` (``ops/search.py``) turns a department/
  category/topic/language id into the name-based ``filter_groups`` key
  ``get_accessible_virtual_record_ids`` understands, and a record-group or
  subcategory id into a permission-checked ``virtualRecordId`` allow-list.
- ``find_records_by_entity`` (``ops/entity_records.py``) reads the entity's
  type and display name, so the model can omit ``entity_type``.

Ids that were never returned by ``search_entities`` in this request have no
cache entry and are dropped (search) or require an explicit type (find).
"""
from __future__ import annotations

from typing import Any

from app.modules.retrieval.entity_permissions import (
    RECORD_GROUP_ENTITY_TYPE,
    SEARCHABLE_ENTITY_TYPES,
    TAXONOMY_ENTITY_TYPES,
)

# EntityType value -> get_accessible_virtual_record_ids() filter key. Graph
# filters match entity *names*, never ids.
ENTITY_TYPE_TO_FILTER_KEY: dict[str, str] = {
    "category": "categories",
    "topic": "topics",
    "department": "departments",
    "language": "languages",
}

# Scoped through the permission-checked record resolver instead of a name
# filter: record groups have no filter key, and subcategory entities carry no
# level while the per-level subcategory filters do.
RECORD_SCOPED_ENTITY_TYPES: frozenset[str] = frozenset({RECORD_GROUP_ENTITY_TYPE, "subcategory"})

ENTITY_ID_FILTER_KEY_CACHE_KEY = "_kg_entity_id_filter_key"
RECORD_SCOPED_ENTITY_CACHE_KEY = "_kg_record_scoped_entities"
ENTITY_INDEX_CACHE_KEY = "_kg_entity_index"


def is_filterable_entity(entity_type: str | None, entity_name: str | None) -> bool:
    """Whether an entity id can scope ``search(entity_ids=[...])``."""
    if entity_type in RECORD_SCOPED_ENTITY_TYPES:
        return True
    return bool(ENTITY_TYPE_TO_FILTER_KEY.get(entity_type or "") and entity_name)


def remember_entities(state: dict[str, Any], entities: list[dict[str, Any]]) -> None:
    """Cache ``{entityId, entityType, name}`` dicts returned by
    ``search_entities`` for ``search(entity_ids)`` and ``find_records_by_entity``."""
    id_to_key: dict[str, tuple[str, str]] = state.get(ENTITY_ID_FILTER_KEY_CACHE_KEY) or {}
    record_scoped: dict[str, str] = state.get(RECORD_SCOPED_ENTITY_CACHE_KEY) or {}
    index: dict[str, dict[str, str]] = state.get(ENTITY_INDEX_CACHE_KEY) or {}
    for entity in entities:
        entity_id = entity.get("entityId")
        entity_type = entity.get("entityType")
        if not entity_id or entity_type not in SEARCHABLE_ENTITY_TYPES:
            continue
        entity_name = entity.get("name") or ""
        index[entity_id] = {"type": entity_type, "name": entity_name}
        if entity_type in RECORD_SCOPED_ENTITY_TYPES:
            record_scoped[entity_id] = entity_type
            continue
        filter_key = ENTITY_TYPE_TO_FILTER_KEY.get(entity_type)
        if filter_key and entity_name:
            id_to_key[entity_id] = (filter_key, entity_name)
    state[ENTITY_ID_FILTER_KEY_CACHE_KEY] = id_to_key
    state[RECORD_SCOPED_ENTITY_CACHE_KEY] = record_scoped
    state[ENTITY_INDEX_CACHE_KEY] = index


def merge_filter_groups(
    base: dict[str, list[str]],
    extra: dict[str, list[str]] | None,
) -> dict[str, list[str]]:
    """Union *extra*'s lists into *base* (new dict; *base* not mutated)."""
    if not extra:
        return base
    merged = {k: list(v) for k, v in base.items()}
    for key, values in extra.items():
        if not values:
            continue
        existing = merged.setdefault(key, [])
        for value in values:
            if value not in existing:
                existing.append(value)
    return merged


__all__ = [
    "ENTITY_ID_FILTER_KEY_CACHE_KEY",
    "ENTITY_INDEX_CACHE_KEY",
    "ENTITY_TYPE_TO_FILTER_KEY",
    "RECORD_SCOPED_ENTITY_CACHE_KEY",
    "RECORD_SCOPED_ENTITY_TYPES",
    "SEARCHABLE_ENTITY_TYPES",
    "TAXONOMY_ENTITY_TYPES",
    "is_filterable_entity",
    "merge_filter_groups",
    "remember_entities",
]
