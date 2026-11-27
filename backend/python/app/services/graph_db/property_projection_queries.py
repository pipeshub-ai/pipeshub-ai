"""Statements that select the records whose projected property values are out of date.

A record is stale when it has a usable stamp (its source's modified time, else the time the platform last
wrote it; zero or missing means "never projectable") and any of: it was projected for another union
(``propsSet``), for an older generation (``propsGen``), or at an older stamp (``propsSrc``). A control that is
not a number counts as missing, so a corrupt value is rewritten rather than trusted.

Two selections, one predicate:

- the change seek: records whose platform write time moved (``updatedAtTimestamp``), keyset-paged by
  ``(updatedAtTimestamp, id)`` so a bulk write that gave thousands of records one millisecond cannot split a page;
- the convergence walk: every stale record of a connector, keyset-paged by id.
"""

from app.config.constants.arangodb import CollectionNames
from app.services.graph_db.common.record_visibility import (
    RecordVisibility,
    aql_record_visibility,
    cypher_record_visibility,
)

#: The name of the Neo4j index; the same statement the knowledge hub's scope listing runs, so an index it made is reused.
NEO4J_CHANGE_INDEX = "kh_rec_conn_updated"
NEO4J_CREATE_CHANGE_INDEX = (
    f"CREATE INDEX {NEO4J_CHANGE_INDEX} IF NOT EXISTS FOR (n:Record) ON (n.connectorId, n.updatedAtTimestamp)"
)
CHANGE_INDEX_FIELDS = ("connectorId", "updatedAtTimestamp")

_NOT_NUMBER_CYPHER = "NOT ({p} IS :: INTEGER NOT NULL OR {p} IS :: FLOAT NOT NULL)"
_RECORDS = CollectionNames.RECORDS.value
_STAMP_CYPHER = "coalesce(r.sourceLastModifiedTimestamp, r.updatedAtTimestamp)"


def _stale_cypher() -> str:
    return f"""(
        ({_STAMP_CYPHER} IS :: INTEGER NOT NULL OR {_STAMP_CYPHER} IS :: FLOAT NOT NULL) AND {_STAMP_CYPHER} > 0
        AND (r.propsSet IS NULL OR r.propsSet <> $props_hash
             OR {_NOT_NUMBER_CYPHER.format(p="r.propsGen")} OR r.propsGen < $props_gen
             OR {_NOT_NUMBER_CYPHER.format(p="r.propsSrc")} OR r.propsSrc < {_STAMP_CYPHER}))"""


def build_records_updated_since_cypher() -> str:
    return f"""
    MATCH (r:Record) USING INDEX r:Record(connectorId, updatedAtTimestamp)
    WHERE r.connectorId = $connector_id AND r.updatedAtTimestamp >= $since
      AND (r.updatedAtTimestamp > $since OR r.id > $after_id)
      AND r.orgId = $org_id AND {cypher_record_visibility("r", RecordVisibility.LIVE)}
      AND {_stale_cypher()}
    RETURN r
    ORDER BY r.updatedAtTimestamp, r.id
    LIMIT $limit
    """


def build_records_needing_projection_cypher(*, has_after_key: bool) -> str:
    after = "AND r.id > $after_key" if has_after_key else ""
    return f"""
    MATCH (r:Record)
    WHERE r.connectorId = $connector_id {after}
      AND r.orgId = $org_id AND {cypher_record_visibility("r", RecordVisibility.LIVE)}
      AND {_stale_cypher()}
    RETURN r
    ORDER BY r.id
    LIMIT $limit
    """


_STALE_AQL = """
      LET stamp = NOT_NULL(r.sourceLastModifiedTimestamp, r.updatedAtTimestamp)
      FILTER IS_NUMBER(stamp) AND stamp > 0
      FILTER r.propsSet == null OR r.propsSet != @props_hash
          OR NOT IS_NUMBER(r.propsGen) OR r.propsGen < @props_gen
          OR NOT IS_NUMBER(r.propsSrc) OR r.propsSrc < stamp"""


def build_records_updated_since_aql() -> str:
    return f"""
    FOR r IN {_RECORDS}
      FILTER r.connectorId == @connector_id AND r.updatedAtTimestamp >= @since
      FILTER r.updatedAtTimestamp > @since OR r._key > @after_id
      FILTER r.orgId == @org_id AND {aql_record_visibility("r", RecordVisibility.LIVE)}
      {_STALE_AQL}
      SORT r.updatedAtTimestamp, r._key
      LIMIT @limit
      RETURN r
    """


def build_records_needing_projection_aql(*, has_after_key: bool) -> str:
    after = "FILTER r._key > @after_key" if has_after_key else ""
    return f"""
    FOR r IN {_RECORDS}
      FILTER r.connectorId == @connector_id
      {after}
      FILTER r.orgId == @org_id AND {aql_record_visibility("r", RecordVisibility.LIVE)}
      {_STALE_AQL}
      SORT r._key
      LIMIT @limit
      RETURN r
    """
