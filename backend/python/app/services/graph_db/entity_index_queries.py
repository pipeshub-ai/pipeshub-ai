"""AQL and Cypher for the entity index rebuild
(``app.modules.indexing.entity_index_rebuild``).

Every source is keyset-paged by node key within one scope (a connector or an
org), so a crash resumes from the last key. Sources, collections, labels and
fields come only from ``ENTITY_INDEX_SOURCES``; nothing from a caller is
interpolated into a query. Rows are filtered (indexed, named, not deleted) by
the caller, not here, so each query stays on its scope index and a page
shorter than the limit always means the source is exhausted.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config.constants.arangodb import CollectionNames
from app.config.constants.neo4j import collection_to_label

_APPS = CollectionNames.APPS.value
_ORGS = CollectionNames.ORGS.value
APP_STATUS_DELETING = "DELETING"

ENTITY_INDEX_STATE_FIELD = "entityIndexState"
ENTITY_INDEX_SWEPT_AT_FIELD = "entityIndexSweptAt"
ENTITY_INDEX_CANDIDATE_COLLECTIONS = frozenset({_APPS, _ORGS})


@dataclass(frozen=True)
class EntityIndexSource:
    """How one projected source is read.

    ``scope_field`` is ``connectorId`` for connector-owned sources and
    ``orgId`` for taxonomy. ``canonical_only`` keeps legacy taxonomy nodes
    (no ``normalizedName``) and merged-away ones (``mergedInto``) out; ``include_global`` admits nodes without an
    org (departments are seeded globally)."""

    collection: str
    scope_field: str
    name_field: str
    # Read when name_field is missing, as on the index path.
    name_fallback: str | None = None
    fields: tuple[str, ...] = ()
    list_fields: tuple[str, ...] = ()
    canonical_only: bool = False
    include_global: bool = False


def _taxonomy(collection: str) -> EntityIndexSource:
    return EntityIndexSource(
        collection, "orgId", "name", fields=("createdAtTimestamp",), list_fields=("aliases",),
        canonical_only=True,
    )


ENTITY_INDEX_SOURCES: dict[str, EntityIndexSource] = {
    source.collection: source
    for source in (
        EntityIndexSource(
            CollectionNames.RECORDS.value, "connectorId", "recordName",
            fields=("orgId", "recordGroupId", "indexingStatus", "isDeleted"),
        ),
        EntityIndexSource(
            CollectionNames.RECORD_GROUPS.value, "connectorId", "groupName",
            name_fallback="name", fields=("orgId",),
        ),
        EntityIndexSource(
            CollectionNames.DEPARTMENTS.value, "orgId", "departmentName", include_global=True,
        ),
        *(
            _taxonomy(c.value)
            for c in (
                CollectionNames.CATEGORIES,
                CollectionNames.SUBCATEGORIES1,
                CollectionNames.SUBCATEGORIES2,
                CollectionNames.SUBCATEGORIES3,
                CollectionNames.TOPICS,
                CollectionNames.LANGUAGES,
            )
        ),
    )
}


def entity_index_source(source: str) -> EntityIndexSource:
    spec = ENTITY_INDEX_SOURCES.get(source)
    if spec is None:
        raise ValueError(f"{source!r} is not an entity index source")
    return spec


def _check_candidate_collection(collection: str) -> None:
    if collection not in ENTITY_INDEX_CANDIDATE_COLLECTIONS:
        raise ValueError(f"{collection!r} does not carry entity index state")


def build_entity_index_candidate_aql(collection: str, *, with_sweep: bool) -> str:
    _check_candidate_collection(collection)
    deleting = f'FILTER doc.status != "{APP_STATUS_DELETING}"' if collection == _APPS else ""
    due = f"doc.{ENTITY_INDEX_STATE_FIELD} != @marker"
    if with_sweep:
        swept = f"doc.{ENTITY_INDEX_SWEPT_AT_FIELD}"
        due = f"{due} OR {swept} == null OR {swept} < @sweep_before"
    return f"""
    FOR doc IN {collection}
        {deleting}
        FILTER {due}
        LIMIT 1
        RETURN doc
    """


def build_entity_index_candidate_cypher(collection: str, *, with_sweep: bool) -> str:
    _check_candidate_collection(collection)
    label = collection_to_label(collection)
    deleting = (
        f"AND coalesce(n.status, '') <> '{APP_STATUS_DELETING}'" if collection == _APPS else ""
    )
    due = f"coalesce(n.{ENTITY_INDEX_STATE_FIELD}, '') <> $marker"
    if with_sweep:
        swept = f"n.{ENTITY_INDEX_SWEPT_AT_FIELD}"
        due = f"{due} OR {swept} IS NULL OR {swept} < $sweep_before"
    return f"""
    MATCH (n:{label})
    WHERE ({due})
      {deleting}
    RETURN n
    LIMIT 1
    """


def build_entity_index_source_page_aql(source: str, *, has_after_key: bool) -> str:
    spec = entity_index_source(source)
    scope = f"n.{spec.scope_field} == @scope_id"
    if spec.include_global:
        scope = f"({scope} OR n.{spec.scope_field} == null)"
    filters = [f"FILTER {scope}"]
    if spec.canonical_only:
        filters.append("FILTER n.normalizedName != null AND n.mergedInto == null")
    if has_after_key:
        filters.append("FILTER n._key > @after_key")
    projection = ", ".join(
        [
            "_key: n._key",
            f"name: n.{spec.name_field} || n.{spec.name_fallback}"
            if spec.name_fallback else f"name: n.{spec.name_field}",
            *(f"{f}: n.{f}" for f in spec.fields),
            *(f"{f}: n.{f} || []" for f in spec.list_fields),
        ]
    )
    newline = "\n        "
    return f"""
    FOR n IN {spec.collection}
        {newline.join(filters)}
        SORT n._key
        LIMIT @limit
        RETURN {{ {projection} }}
    """


def build_entity_index_source_page_cypher(source: str, *, has_after_key: bool) -> str:
    """Two variants, like ``build_page_records_for_vector_membership_backfill_cypher``:
    an ``$after_key IS NULL OR`` predicate would stop the planner range-scanning
    on ``id``."""
    spec = entity_index_source(source)
    label = collection_to_label(spec.collection)
    scope = f"n.{spec.scope_field} = $scope_id"
    if spec.include_global:
        scope = f"({scope} OR n.{spec.scope_field} IS NULL)"
    predicates = [scope]
    if spec.canonical_only:
        predicates.append("n.normalizedName IS NOT NULL AND n.mergedInto IS NULL")
    if has_after_key:
        predicates.append("n.id > $after_key")
    projection = ", ".join(
        [
            "n.id AS _key",
            f"coalesce(n.{spec.name_field}, n.{spec.name_fallback}) AS name"
            if spec.name_fallback else f"n.{spec.name_field} AS name",
            *(f"n.{f} AS {f}" for f in spec.fields),
            *(f"coalesce(n.{f}, []) AS {f}" for f in spec.list_fields),
        ]
    )
    return f"""
    MATCH (n:{label})
    WHERE {" AND ".join(predicates)}
    RETURN {projection}
    ORDER BY n.id
    LIMIT $limit
    """


__all__ = [
    "ENTITY_INDEX_CANDIDATE_COLLECTIONS",
    "ENTITY_INDEX_SOURCES",
    "EntityIndexSource",
    "build_entity_index_candidate_aql",
    "build_entity_index_candidate_cypher",
    "build_entity_index_source_page_aql",
    "build_entity_index_source_page_cypher",
    "entity_index_source",
]
