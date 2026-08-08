"""Shared entity → graph-filter mapping.

Both the probabilistic path (``resolve_entity_filters`` tool, agent-initiated)
and the deterministic path (``entity_filter_resolution`` PRE_TOOL_USE hook,
always-on) resolve natural-language entity mentions to the same
``filter_groups`` shape ``get_accessible_virtual_record_ids`` already
understands (``departments``/``categories``/``subcategories1``/``languages``/
``topics``) — this module is the single place that owns the
``EntityType`` → filter-key mapping and the score-threshold gate so the two
paths can never drift apart.

Only entity types the graph provider's ``filters`` dict actually supports are
eligible for filter injection (see ``get_accessible_virtual_record_ids``
docstring). ``PERSON``/``RECORD_GROUP``/``CONNECTOR``/etc. are valid
``search_entities`` results but are not filterable this way today — they are
still returned to the agent (or used for the mention map) for informational
value, just not converted into a hard filter.

``SUBCATEGORY`` has no depth field on ``EntityType`` yet (see
``app.models.entities.EntityType``), so it always maps to the level-1 filter
key (``subcategories1``); a true multi-level subcategory filter is future
work once the type carries a depth.
"""
from __future__ import annotations

from typing import Any

from app.config.constants.arangodb import CollectionNames

# EntityType value -> get_accessible_virtual_record_ids() filter key.
# Keep in sync with app.models.entities.EntityType and the filters dict
# documented on IGraphDBProvider.get_accessible_virtual_record_ids.
ENTITY_TYPE_TO_FILTER_KEY: dict[str, str] = {
    "category": "categories",
    "subcategory": "subcategories1",
    "topic": "topics",
    "department": "departments",
    "language": "languages",
}

# EntityType value -> graph node collection, for a direct-by-key lookup
# (``IGraphDBProvider.get_document``) when an entityId wasn't produced by
# this session's own resolve_entity_filters/search_entities call (so isn't
# in the ``_kg_entity_id_filter_key`` cache) — see ops/entity_records.py.
# Deliberately duplicated (not imported) from each provider's own
# ``_ENTITY_TYPE_TO_NODE_COLLECTION`` — providers must not depend on the
# agents/ops layer, so this small, stable mapping is kept at this layer too.
ENTITY_TYPE_TO_NODE_COLLECTION: dict[str, str] = {
    "department": CollectionNames.DEPARTMENTS.value,
    "category": CollectionNames.CATEGORIES.value,
    "subcategory": CollectionNames.SUBCATEGORIES1.value,
    "topic": CollectionNames.TOPICS.value,
    "language": CollectionNames.LANGUAGES.value,
    "person": CollectionNames.USERS.value,
}

# The graph node's own name field, keyed by EntityType — department nodes
# are the one outlier (``departmentName``, matched against pre-existing
# nodes at index time, see GraphDBTransformer); every other taxonomy node
# uses ``name`` (see ``_find_or_create_node`` callers in graphdb.py). Users
# (person) use ``fullName`` with ``email``/id fallbacks, mirrored in
# ``get_entities_for_sync``'s ``_get_person_entities_for_sync``.
ENTITY_TYPE_TO_NAME_FIELD: dict[str, str] = {
    "department": "departmentName",
    "category": "name",
    "subcategory": "name",
    "topic": "name",
    "language": "name",
    "person": "fullName",
}

# EntityType value -> belongsTo* edge collection, for INBOUND traversal
# from an entity node to the records connected to it. Used by
# ops/entity_records.py's ``execute_find_records_by_entity`` to find records
# linked to an entity directly via graph edges (bypassing the metadata-filter
# path in ``get_accessible_virtual_record_ids`` which has a known
# false-negative issue — see ops/search.py's filtered-then-fallback comment).
ENTITY_TYPE_TO_EDGE_COLLECTION: dict[str, str] = {
    "department": CollectionNames.BELONGS_TO_DEPARTMENT.value,
    "category": CollectionNames.BELONGS_TO_CATEGORY.value,
    "subcategory": CollectionNames.BELONGS_TO_CATEGORY.value,
    "topic": CollectionNames.BELONGS_TO_TOPIC.value,
    "language": CollectionNames.BELONGS_TO_LANGUAGE.value,
}

# Entity types eligible for search_entities() calls made purely to resolve
# graph filters (excludes PERSON/RECORD/RECORD_GROUP/CONNECTOR/CUSTOM, which
# are either not filterable this way or too broad to blind-inject).
FILTERABLE_ENTITY_TYPES: tuple[str, ...] = tuple(ENTITY_TYPE_TO_FILTER_KEY)

# RRF-fused scores (see EntityVectorStore.search_entities) are not raw cosine
# similarities; this threshold is intentionally conservative — a false
# negative just means no auto-filter (falls back to unfiltered search), a
# false positive risks a hard AND filter that returns zero records whenever
# the graph has no belongsTo* edge yet linking a record to that entity (a
# real gap seen in production — see ops/search.py's post-attempt fallback,
# which retries without entity filters when this happens). Raised from 0.5
# to 0.7 to cut down how often that fallback is needed; fail toward a missed
# auto-filter over an over-confident one.
DEFAULT_SCORE_THRESHOLD = 0.7


def group_entities_into_filters(
    entities: list[dict[str, Any]],
    *,
    score_threshold: float = DEFAULT_SCORE_THRESHOLD,
) -> dict[str, list[str]]:
    """Group ``search_entities()`` hits into a ``filter_groups``-shaped dict.

    Entities below *score_threshold* or of a non-filterable type are
    dropped. Returns ``{}`` (never ``None``) so callers can always merge it
    into an existing filter_groups dict unconditionally.

    Values are entity **names**, not ``entityId`` graph node keys —
    ``get_accessible_virtual_record_ids`` filters on node name properties
    (``dept.departmentName``, ``cat.name``, ``topic.name``, ...), never on
    node ID, for every provider (Arango/Neo4j). ``EntityRecord.name`` is set
    verbatim from the same string used to create/match that node at index
    time (see ``modules/transformers/graphdb.py``), so it is guaranteed to
    match those name properties. Passing ``entityId`` here instead is a
    silent bug: it never matches, so the graph query returns zero records
    for every filtered search.
    """
    grouped: dict[str, list[str]] = {}
    for entity in entities:
        score = entity.get("score")
        if score is not None and score < score_threshold:
            continue
        entity_type = entity.get("entityType")
        entity_name = entity.get("name") or entity.get("canonicalName")
        if not entity_type or not entity_name:
            continue
        filter_key = ENTITY_TYPE_TO_FILTER_KEY.get(entity_type)
        if not filter_key:
            continue
        bucket = grouped.setdefault(filter_key, [])
        if entity_name not in bucket:
            bucket.append(entity_name)
    return grouped


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
    "DEFAULT_SCORE_THRESHOLD",
    "ENTITY_TYPE_TO_EDGE_COLLECTION",
    "ENTITY_TYPE_TO_FILTER_KEY",
    "ENTITY_TYPE_TO_NAME_FIELD",
    "ENTITY_TYPE_TO_NODE_COLLECTION",
    "FILTERABLE_ENTITY_TYPES",
    "group_entities_into_filters",
    "merge_filter_groups",
]
