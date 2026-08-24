"""AQL and Cypher for vector membership backfill scans.

Paged by ``connectorId`` and a stable record key so a crash can resume.
Does not join ``IS_OF_TYPE`` or load whole collections.
"""

from __future__ import annotations

from app.config.constants.arangodb import CollectionNames

_APPS = CollectionNames.APPS.value
_RECORDS = CollectionNames.RECORDS.value
_FLAG = "vectorMembershipBackfilled"
_STATUS_DELETING = "DELETING"


def build_app_needing_vector_membership_backfill_aql() -> str:
    return f"""
    FOR doc IN {_APPS}
        FILTER doc.{_FLAG} != true
        FILTER doc.status != "{_STATUS_DELETING}"
        LIMIT 1
        RETURN doc
    """


def build_page_records_for_vector_membership_backfill_aql(*, has_after_key: bool) -> str:
    after_key_clause = "FILTER record._key > @after_key" if has_after_key else ""
    return f"""
    FOR record IN {_RECORDS}
        FILTER record.connectorId == @connector_id
        {after_key_clause}
        SORT record._key
        LIMIT @limit
        RETURN {{ _key: record._key, virtualRecordId: record.virtualRecordId }}
    """


def build_app_needing_vector_membership_backfill_cypher() -> str:
    return f"""
    MATCH (n:App)
    WHERE coalesce(n.{_FLAG}, false) = false
      AND coalesce(n.status, '') <> '{_STATUS_DELETING}'
    RETURN n
    LIMIT 1
    """


def build_page_records_for_vector_membership_backfill_cypher(
    *, has_after_key: bool
) -> str:
    """Two variants, as for AQL.

    A single query using ``$after_key IS NULL OR r.id > $after_key`` reads well but
    stops the planner using a range scan on ``r.id``, which is the whole point of
    the ``(connectorId, id)`` index.
    """
    after_key_clause = "AND r.id > $after_key" if has_after_key else ""
    return f"""
    MATCH (r:Record)
    WHERE r.connectorId = $connector_id
      {after_key_clause}
    RETURN r.id AS _key, r.virtualRecordId AS virtualRecordId
    ORDER BY r.id
    LIMIT $limit
    """
