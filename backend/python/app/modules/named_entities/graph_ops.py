"""AQL and Cypher for named entities. Providers execute these; feature code does not."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.config.constants.arangodb import CollectionNames
from app.config.constants.neo4j import (
    EDGE_COLLECTION_TO_RELATIONSHIP,
    Neo4jLabel,
    Neo4jRelationshipType,
)
from app.modules.named_entities.domain.kinds import VALUE_KINDS, EntityKind
from app.modules.named_entities.keys import alias_partition, value_mention_key
from app.services.graph_db.taxonomy import MAX_MERGE_REDIRECT_HOPS, MERGED_INTO_FIELD

_COLLECTION = CollectionNames.NAMED_ENTITIES.value
_EDGE = CollectionNames.MENTIONS_ENTITY.value
_RECORDS = CollectionNames.RECORDS.value
_VALUES = CollectionNames.VALUE_MENTIONS.value
_LABEL = Neo4jLabel.NAMED_ENTITIES.value
_VALUE_LABEL = Neo4jLabel.VALUE_MENTIONS.value
_RETRIES = CollectionNames.NAMED_ENTITY_PERSIST_RETRIES.value
_RETRY_LABEL = Neo4jLabel.NAMED_ENTITY_PERSIST_RETRIES.value
_REL = Neo4jRelationshipType.MENTIONS_ENTITY.value
_RECORD_LABEL = "Record"
_CAP = 5000
# The most a caller may list by asking for more than _CAP: the agent's value
# lookup lists matches to check them for permission instead of loading every
# record the user can read.
_SCAN_CAP = 20_000
# Record ids bound per restricted lookup.
_WITHIN_CHUNK = 5000
_ALIAS_CAP = 20

_NODE_FILTERS_AQL = """\
  FILTER LENGTH(@kinds) == 0 OR n.kind IN @kinds
  FILTER @prefix == "" OR STARTS_WITH(LOWER(n.name), @prefix)"""

_NODE_FILTERS_CYPHER = """\
WHERE (size($kinds) = 0 OR n.kind IN $kinds)
  AND ($prefix = "" OR toLower(n.name) STARTS WITH $prefix)"""

# An empty currency or dimension means "any": the filter binds "" when the
# caller gave only a range, and no stored value has an empty unit.
_VALUE_FILTERS_AQL = """\
  FILTER LENGTH(@kinds) == 0 OR v.kind IN @kinds
  FILTER @dateStart == null OR (v.startMs < @dateEnd AND v.endMs > @dateStart)
  FILTER @amountMin == null OR ((@currency == "" OR v.currency == @currency) AND v.amountFloat >= @amountMin AND v.amountFloat <= @amountMax)
  FILTER @quantityMin == null OR ((@dimension == "" OR v.dimension == @dimension) AND v.siValue >= @quantityMin AND v.siValue <= @quantityMax)
  FILTER @percentMin == null OR (v.kind == "percentage" AND v.numericValue >= @percentMin AND v.numericValue <= @percentMax)"""

_VALUE_FILTERS_CYPHER = """\
WHERE (size($kinds) = 0 OR v.kind IN $kinds)
  AND ($dateStart IS NULL OR (v.startMs < $dateEnd AND v.endMs > $dateStart))
  AND ($amountMin IS NULL OR (($currency = "" OR v.currency = $currency) AND v.amountFloat >= $amountMin AND v.amountFloat <= $amountMax))
  AND ($quantityMin IS NULL OR (($dimension = "" OR v.dimension = $dimension) AND v.siValue >= $quantityMin AND v.siValue <= $quantityMax))
  AND ($percentMin IS NULL OR (v.kind = "percentage" AND v.numericValue >= $percentMin AND v.numericValue <= $percentMax))"""

# Existing nodes are left untouched: writing a popular node from every record
# that mentions it would serialize those records on its lock.
CREATE_AQL = f"""
FOR doc IN @docs
  INSERT doc INTO {_COLLECTION} OPTIONS {{ overwriteMode: "ignore" }}
"""

# A writer about to link a node takes back its orphan mark first. The clear and
# the sweep's delete write the same node, so they conflict instead of the sweep
# deleting a node mid-link; only marked nodes are written, so hot nodes stay read-only.
RECLAIM_AQL = f"""
FOR n IN {_COLLECTION}
  FILTER n._key IN @keys AND n.orphanedAt != null
  UPDATE n WITH {{ orphanedAt: null }} IN {_COLLECTION}
"""

CREATE_CYPHER = f"""
UNWIND $docs AS doc
MERGE (n:{_LABEL} {{id: doc.id}})
ON CREATE SET n += doc
WITH n WHERE n.orphanedAt IS NOT NULL
SET n.orphanedAt = null
"""

FIND_AQL = f"""
FOR n IN {_COLLECTION}
  FILTER n.orgId == @orgId AND n.kind == @kind
  FILTER n.normKey IN @keys OR LENGTH(INTERSECTION(NOT_NULL(n.normalizedAliases, []), @keys)) > 0
  RETURN n
"""

FIND_CYPHER = f"""
MATCH (n:{_LABEL} {{orgId: $orgId, kind: $kind}})
WHERE n.normKey IN $keys
RETURN n
UNION
MATCH (a:TaxonomyAlias {{orgId: $orgId, collection: $partition}})
WHERE a.normalized IN $keys
MATCH (a)-[:ALIAS_OF]->(n:{_LABEL})
WHERE n.orgId = $orgId AND n.kind = $kind
RETURN n
"""

ALIAS_AQL = f"""
FOR n IN {_COLLECTION}
  FILTER n._key == @key AND n.orgId == @orgId AND n.kind == @kind
  FILTER LENGTH(MINUS(@normalized, NOT_NULL(n.normalizedAliases, []))) > 0
  UPDATE n WITH {{
    aliases: SLICE(UNION_DISTINCT(NOT_NULL(n.aliases, []), @aliases), 0, @maxAliases),
    normalizedAliases: SLICE(UNION_DISTINCT(NOT_NULL(n.normalizedAliases, []), @normalized), 0, @maxAliases),
    updatedAtTimestamp: @now
  }} IN {_COLLECTION}
"""

ALIAS_CYPHER = f"""
MATCH (n:{_LABEL} {{id: $key}})
WHERE n.orgId = $orgId AND n.kind = $kind
SET n._aliasLock = true
WITH n, coalesce(n.aliases, []) AS displays, coalesce(n.normalizedAliases, []) AS normals
WITH n, displays, normals,
    [i IN range(0, size($normalized) - 1) WHERE NOT $normalized[i] IN normals] AS fresh
SET n.aliases = (displays + [i IN fresh | $aliases[i]])[0..$maxAliases],
    n.normalizedAliases = (normals + [i IN fresh | $normalized[i]])[0..$maxAliases],
    n.updatedAtTimestamp = $now
REMOVE n._aliasLock
WITH n
UNWIND [normalized IN $normalized WHERE normalized IN n.normalizedAliases] AS normalized
MERGE (a:TaxonomyAlias {{orgId: n.orgId, collection: $partition, normalized: normalized}})
MERGE (a)-[:ALIAS_OF]->(n)
RETURN count(a) AS aliases
"""

QUERY_AQL = f"""
FOR n IN {_COLLECTION}
  FILTER n.orgId == @orgId
{_NODE_FILTERS_AQL}
  SORT n._key
  LIMIT @skip, @limit
  RETURN n
"""

QUERY_CYPHER = f"""
MATCH (n:{_LABEL} {{orgId: $orgId}})
{_NODE_FILTERS_CYPHER}
RETURN n
ORDER BY n.id
SKIP $skip LIMIT $limit
"""

# Each predicate of an entity filter yields record ids; the caller intersects them.
# ``e._to IN`` is served by the edge index; a key computed from ``_to`` would scan
# every mention edge of every org.
NODE_RECORDS_AQL = f"""
FOR e IN {_EDGE}
  FILTER e._to IN @targets
  FILTER e.orgId == @orgId
  COLLECT recordId = PARSE_IDENTIFIER(e._from).key
  LIMIT @limit
  RETURN recordId
"""

NODE_RECORDS_CYPHER = f"""
MATCH (r:{_RECORD_LABEL})-[e:{_REL}]->(n:{_LABEL})
WHERE n.id IN $keys AND e.orgId = $orgId
RETURN DISTINCT r.id AS recordId
LIMIT $limit
"""

# The same predicate checked from a bounded set of records, over the edge index on
# ``_from``, for when its unrestricted match was too large to list.
NODE_RECORDS_WITHIN_AQL = f"""
FOR recordId IN @candidates
  FOR e IN {_EDGE}
    FILTER e._from == CONCAT("{_RECORDS}/", recordId) AND e.orgId == @orgId
    LET n = DOCUMENT(e._to)
    FILTER n != null
    FILTER LENGTH(@ids) == 0 OR n._key IN @ids
{_NODE_FILTERS_AQL}
    COLLECT matched = recordId
    RETURN matched
"""

NODE_RECORDS_WITHIN_CYPHER = f"""
MATCH (r:{_RECORD_LABEL})-[e:{_REL}]->(n:{_LABEL})
WHERE r.id IN $candidates AND e.orgId = $orgId
  AND (size($ids) = 0 OR n.id IN $ids)
  AND (size($kinds) = 0 OR n.kind IN $kinds)
  AND ($prefix = "" OR toLower(n.name) STARTS WITH $prefix)
RETURN DISTINCT r.id AS recordId
"""

VALUE_RECORDS_AQL = f"""
FOR v IN {_VALUES}
  FILTER v.orgId == @orgId
  FILTER NOT @within OR v.recordId IN @candidates
{_VALUE_FILTERS_AQL}
  COLLECT recordId = v.recordId
  LIMIT @limit
  RETURN recordId
"""

VALUE_RECORDS_CYPHER = f"""
MATCH (v:{_VALUE_LABEL} {{orgId: $orgId}})
{_VALUE_FILTERS_CYPHER}
  AND (NOT $within OR v.recordId IN $candidates)
RETURN DISTINCT v.recordId AS recordId
LIMIT $limit
"""

# Trashed records never come back from an entity filter.
RECORD_HITS_AQL = f"""
FOR recordId IN @recordIds
  LET r = DOCUMENT("{_RECORDS}", recordId)
  FILTER r != null AND r.isDeleted != true AND r.orgId == @orgId
  RETURN {{ recordId: r._key, virtualRecordId: r.virtualRecordId, connectorId: r.connectorId }}
"""

RECORD_HITS_CYPHER = f"""
MATCH (r:{_RECORD_LABEL})
WHERE r.id IN $recordIds AND r.orgId = $orgId AND coalesce(r.isDeleted, false) = false
RETURN r.id AS recordId, r.virtualRecordId AS virtualRecordId, r.connectorId AS connectorId
"""

DELETE_STALE_VALUES_AQL = f"""
FOR v IN {_VALUES}
  FILTER v.recordId == @recordId AND v._key NOT IN @keys
  REMOVE v IN {_VALUES}
"""

UPSERT_VALUES_AQL = f"""
FOR doc IN @docs
  INSERT doc INTO {_VALUES} OPTIONS {{ overwriteMode: "replace" }}
"""

DELETE_STALE_VALUES_CYPHER = f"""
MATCH (v:{_VALUE_LABEL} {{recordId: $recordId}})
WHERE NOT v.id IN $keys
DETACH DELETE v
"""

UPSERT_VALUES_CYPHER = f"""
UNWIND $docs AS doc
MERGE (v:{_VALUE_LABEL} {{id: doc.id}})
SET v = doc
"""

RECORD_VALUES_AQL = f"""
FOR v IN {_VALUES}
  FILTER v.recordId == @recordId
  RETURN v
"""

RECORD_VALUES_CYPHER = f"""
MATCH (v:{_VALUE_LABEL} {{recordId: $recordId}})
RETURN v {{.*}} AS entity
"""

# Rows of a deleted record: reads already skip them, since every read joins the record.
DANGLING_VALUES_AQL = f"""
FOR v IN {_VALUES}
  FILTER v.orgId == @orgId
  FILTER DOCUMENT("{_RECORDS}", v.recordId) == null
  LIMIT @batch
  REMOVE v IN {_VALUES}
  RETURN 1
"""

DANGLING_VALUES_CYPHER = f"""
MATCH (v:{_VALUE_LABEL} {{orgId: $orgId}})
WHERE NOT EXISTS {{ MATCH (:{_RECORD_LABEL} {{id: v.recordId}}) }}
WITH v LIMIT $batch
DETACH DELETE v
RETURN count(*) AS removed
"""

RECORD_ENTITIES_AQL = f"""
FOR e IN {_EDGE}
  FILTER e._from == CONCAT("{_RECORDS}/", @recordId)
  FOR n IN {_COLLECTION}
    FILTER n._id == e._to
    RETURN MERGE(n, {{ mentionCount: e.mentionCount, blockIndexes: e.blockIndexes }})
"""

RECORD_ENTITIES_CYPHER = f"""
MATCH (r:{_RECORD_LABEL} {{id: $recordId}})-[e:{_REL}]->(n:{_LABEL})
RETURN n {{.*, mentionCount: e.mentionCount, blockIndexes: e.blockIndexes}} AS entity
"""

COPY_AQL = f"""
FOR e IN {_EDGE}
  FILTER e._from == CONCAT("{_RECORDS}/", @source)
  LET target = CONCAT("{_RECORDS}/", @target)
  FILTER DOCUMENT(target).orgId == e.orgId
  UPSERT {{ _from: target, _to: e._to }}
  INSERT MERGE(UNSET(e, "_key", "_id", "_rev", "_from"), {{ _from: target, _to: e._to }})
  UPDATE UNSET(e, "_key", "_id", "_rev", "_from", "_to")
  IN {_EDGE}
  RETURN 1
"""

COPY_CYPHER = f"""
MATCH (src:{_RECORD_LABEL} {{id: $source}})-[e:{_REL}]->(n:{_LABEL})
MATCH (dst:{_RECORD_LABEL} {{id: $target}})
WHERE dst.orgId = e.orgId
MERGE (dst)-[c:{_REL}]->(n)
ON CREATE SET c = properties(e)
ON MATCH SET c += properties(e)
RETURN count(c) AS copied
"""

# A null cutoff matches any orphan at once; a cutoff matches only nodes that were
# marked orphaned at or before it and still have no mention. ``@ids`` narrows the
# delete to candidates found earlier; each one is checked again here. ``@skipKinds``
# leaves out kinds the caller cannot clean up after. A merged node has no mention
# by design and stays: it is the redirect an old key or stored id still follows.
_ORPHAN_FILTER_AQL = f"""
FOR n IN {_COLLECTION}
  FILTER n.orgId == @orgId
  FILTER @ids == null OR n._key IN @ids
  FILTER @skipKinds == null OR n.kind NOT IN @skipKinds
  FILTER n.mergedInto == null
  FILTER @cutoff == null OR (n.orphanedAt != null AND n.orphanedAt <= @cutoff)
  FILTER LENGTH(FOR e IN {_EDGE} FILTER e._to == n._id LIMIT 1 RETURN 1) == 0
  LIMIT @batch"""

FIND_ORPHANS_AQL = _ORPHAN_FILTER_AQL + """
  RETURN n._key
"""

ORPHAN_AQL = _ORPHAN_FILTER_AQL + f"""
  REMOVE n IN {_COLLECTION}
  RETURN OLD._key
"""

_ORPHAN_FILTER_CYPHER = f"""
MATCH (n:{_LABEL} {{orgId: $orgId}})
WHERE ($ids IS NULL OR n.id IN $ids)
  AND ($skipKinds IS NULL OR NOT n.kind IN $skipKinds)
  AND n.mergedInto IS NULL
  AND ($cutoff IS NULL OR (n.orphanedAt IS NOT NULL AND n.orphanedAt <= $cutoff))
  AND NOT (n)<-[:{_REL}]-()
WITH n LIMIT $batch"""

FIND_ORPHANS_CYPHER = _ORPHAN_FILTER_CYPHER + """
RETURN n.id AS id
"""

# Neo4j reads committed data without a snapshot and does not check a match again
# once it holds a lock. So the delete locks each candidate first and then checks
# it again: a writer's claim or link that committed meanwhile is visible under the
# lock, and the node stays. An alias node can name several entities; only this
# node's links go, and the alias only once nothing else uses it.
ORPHAN_CYPHER = _ORPHAN_FILTER_CYPHER + f"""
SET n._sweepLock = true
WITH n, ($cutoff IS NULL OR (n.orphanedAt IS NOT NULL AND n.orphanedAt <= $cutoff))
  AND NOT (n)<-[:{_REL}]-() AS orphaned
REMOVE n._sweepLock
WITH n WHERE orphaned
OPTIONAL MATCH (a:TaxonomyAlias {{orgId: $orgId}})-[link:ALIAS_OF]->(n)
WHERE a.collection STARTS WITH $aliasPrefix
WITH n, n.id AS id, collect(link) AS links, collect(a) AS aliases
FOREACH (link IN links | DELETE link)
DETACH DELETE n
WITH id, aliases
CALL {{
  WITH aliases
  UNWIND aliases AS alias
  WITH DISTINCT alias
  WHERE NOT (alias)-[:ALIAS_OF]->()
  DETACH DELETE alias
}}
RETURN id
"""

MARK_ORPHANS_AQL = f"""
FOR n IN {_COLLECTION}
  FILTER n.orgId == @orgId AND n.orphanedAt == null AND n.mergedInto == null
  FILTER LENGTH(FOR e IN {_EDGE} FILTER e._to == n._id LIMIT 1 RETURN 1) == 0
  LIMIT @batch
  UPDATE n WITH {{ orphanedAt: @now }} IN {_COLLECTION}
  RETURN 1
"""

MARK_ORPHANS_CYPHER = f"""
MATCH (n:{_LABEL} {{orgId: $orgId}})
WHERE n.orphanedAt IS NULL AND n.mergedInto IS NULL AND NOT (n)<-[:{_REL}]-()
WITH n LIMIT $batch
SET n.orphanedAt = $now
RETURN count(n) AS marked
"""

CLEAR_ORPHAN_MARKS_AQL = f"""
FOR n IN {_COLLECTION}
  FILTER n.orgId == @orgId AND n.orphanedAt != null
  FILTER LENGTH(FOR e IN {_EDGE} FILTER e._to == n._id LIMIT 1 RETURN 1) > 0
  LIMIT @batch
  UPDATE n WITH {{ orphanedAt: null }} IN {_COLLECTION}
  RETURN 1
"""

CLEAR_ORPHAN_MARKS_CYPHER = f"""
MATCH (n:{_LABEL} {{orgId: $orgId}})
WHERE n.orphanedAt IS NOT NULL AND (n)<-[:{_REL}]-()
WITH n LIMIT $batch
SET n.orphanedAt = null
RETURN count(n) AS cleared
"""

# ArangoDB removes a node without its edges, so an edge committed while the node
# was swept points at nothing. The target is checked again: a writer may have
# created the node anew, and its edge is then a live mention. A writer commits the
# node before the edge, so a snapshot holding the new edge also holds the node.
DANGLING_MENTIONS_AQL = f"""
FOR e IN {_EDGE}
  FILTER e._to IN @targets
  FILTER DOCUMENT(e._to) == null
  REMOVE e IN {_EDGE}
  RETURN 1
"""

DEDUPE_MENTIONS_AQL = f"""
FOR e IN {_EDGE}
  COLLECT source = e._from, target = e._to INTO group = e
  FILTER LENGTH(group) > 1
  LET keep = FIRST(FOR g IN group SORT g.updatedAtTimestamp DESC, g._key DESC LIMIT 1 RETURN g._key)
  FOR g IN group
    FILTER g._key != keep
    REMOVE g IN {_EDGE}
"""

# A persist that failed is retried with exponential backoff: each failure doubles
# the wait from the base, up to the cap.
SCHEDULE_RETRY_AQL = f"""
LET old = DOCUMENT("{_RETRIES}", @recordId)
LET before = MAX([old == null ? 0 : old.attempts, @attemptsSoFar])
LET due = @now + MIN([@maxBackoffMs, @baseBackoffMs * POW(2, before)])
UPSERT {{ _key: @recordId }}
INSERT {{ _key: @recordId, orgId: @orgId, recordId: @recordId, attempts: before + 1, dueAt: due,
         lastError: @error, createdAtTimestamp: @now, updatedAtTimestamp: @now }}
UPDATE {{ attempts: before + 1, dueAt: due, lastError: @error, updatedAtTimestamp: @now }}
IN {_RETRIES}
RETURN NEW.attempts
"""

SCHEDULE_RETRY_CYPHER = f"""
MERGE (r:{_RETRY_LABEL} {{id: $recordId}})
ON CREATE SET r.orgId = $orgId, r.recordId = $recordId, r.attempts = 0, r.createdAtTimestamp = $now
WITH r, CASE WHEN r.attempts > $attemptsSoFar THEN r.attempts ELSE $attemptsSoFar END AS before
SET r.attempts = before + 1,
    r.dueAt = $now + toInteger(CASE WHEN $baseBackoffMs * (2.0 ^ before) > $maxBackoffMs
                                     THEN $maxBackoffMs ELSE $baseBackoffMs * (2.0 ^ before) END),
    r.lastError = $error,
    r.updatedAtTimestamp = $now
RETURN r.attempts AS attempts
"""

DUE_RETRIES_AQL = f"""
FOR r IN {_RETRIES}
  FILTER r.dueAt <= @now
  SORT r.dueAt
  LIMIT @limit
  RETURN {{ recordId: r._key, orgId: r.orgId, attempts: r.attempts, dueAt: r.dueAt }}
"""

DUE_RETRIES_CYPHER = f"""
MATCH (r:{_RETRY_LABEL})
WHERE r.dueAt <= $now
RETURN r.id AS recordId, r.orgId AS orgId, r.attempts AS attempts, r.dueAt AS dueAt
ORDER BY r.dueAt
LIMIT $limit
"""

# Only while the due time is the one the caller read: a failure rescheduled
# meanwhile keeps its retry.
CLEAR_RETRY_AQL = f"""
FOR r IN {_RETRIES}
  FILTER r._key == @recordId AND (@dueAt == null OR r.dueAt == @dueAt)
  REMOVE r IN {_RETRIES}
  RETURN 1
"""

CLEAR_RETRY_CYPHER = f"""
MATCH (r:{_RETRY_LABEL} {{id: $recordId}})
WHERE $dueAt IS NULL OR r.dueAt = $dueAt
DELETE r
RETURN 1 AS removed
"""

PERSIST_RETRY_BASE_MS = 60_000
PERSIST_RETRY_MAX_MS = 6 * 60 * 60 * 1000

REDIRECTS_AQL = f"""
FOR id IN @ids
  LET n = DOCUMENT("{_COLLECTION}", id)
  FILTER n != null AND n.orgId == @orgId AND n.{MERGED_INTO_FIELD} != null
  RETURN {{ id: n._key, mergedInto: n.{MERGED_INTO_FIELD} }}
"""

REDIRECTS_CYPHER = f"""
MATCH (n:{_LABEL})
WHERE n.id IN $ids AND n.orgId = $orgId AND n.{MERGED_INTO_FIELD} IS NOT NULL
RETURN n.id AS id, n.{MERGED_INTO_FIELD} AS mergedInto
"""

MATCHED_KEYS_AQL = f"""
FOR n IN {_COLLECTION}
  FILTER n.orgId == @orgId
  FILTER LENGTH(@ids) == 0 OR n._key IN @ids
{_NODE_FILTERS_AQL}
  LIMIT @limit
  RETURN n._key
"""

MATCHED_KEYS_CYPHER = f"""
MATCH (n:{_LABEL} {{orgId: $orgId}})
{_NODE_FILTERS_CYPHER}
  AND (size($ids) = 0 OR n.id IN $ids)
RETURN n.id AS id
LIMIT $limit
"""


def named_entity_neo4j_indexes() -> list[str]:
    label = _LABEL
    rel = EDGE_COLLECTION_TO_RELATIONSHIP[CollectionNames.MENTIONS_ENTITY.value]
    return [
        f"CREATE INDEX named_entity_org_kind_norm IF NOT EXISTS FOR (n:{label}) ON (n.orgId, n.kind, n.normKey)",
        f"CREATE INDEX named_entity_org_orphaned IF NOT EXISTS FOR (n:{label}) ON (n.orgId, n.orphanedAt)",
        f"CREATE INDEX mentions_entity_org IF NOT EXISTS FOR ()-[r:{rel}]-() ON (r.orgId)",
        f"CREATE INDEX value_mention_record IF NOT EXISTS FOR (v:{_VALUE_LABEL}) ON (v.recordId)",
        f"CREATE INDEX value_mention_org_kind_start IF NOT EXISTS FOR (v:{_VALUE_LABEL}) ON (v.orgId, v.kind, v.startMs)",
        f"CREATE INDEX value_mention_org_currency_amount IF NOT EXISTS FOR (v:{_VALUE_LABEL}) ON (v.orgId, v.currency, v.amountFloat)",
        f"CREATE INDEX value_mention_org_dimension_si IF NOT EXISTS FOR (v:{_VALUE_LABEL}) ON (v.orgId, v.dimension, v.siValue)",
        f"CREATE INDEX value_mention_org_kind_numeric IF NOT EXISTS FOR (v:{_VALUE_LABEL}) ON (v.orgId, v.kind, v.numericValue)",
        f"CREATE INDEX named_entity_persist_retry_due IF NOT EXISTS FOR (r:{_RETRY_LABEL}) ON (r.dueAt)",
    ]


async def ensure_named_entity_indexes(http_client) -> None:
    col = _COLLECTION
    await http_client.ensure_persistent_index(col, ["orgId", "kind", "normKey"])
    await http_client.ensure_persistent_index(col, ["orgId", "kind", "normalizedAliases[*]"])
    await http_client.ensure_persistent_index(col, ["orgId", "orphanedAt"], sparse=True)
    await http_client.ensure_persistent_index(_VALUES, ["recordId"])
    await http_client.ensure_persistent_index(_VALUES, ["orgId", "kind", "startMs"], sparse=True)
    await http_client.ensure_persistent_index(_VALUES, ["orgId", "currency", "amountFloat"], sparse=True)
    await http_client.ensure_persistent_index(_VALUES, ["orgId", "dimension", "siValue"], sparse=True)
    await http_client.ensure_persistent_index(_VALUES, ["orgId", "kind", "numericValue"], sparse=True)
    await http_client.ensure_persistent_index(_RETRIES, ["dueAt"])
    await ensure_unique_mentions(http_client)


async def ensure_unique_mentions(http_client) -> bool:
    """One edge per (record, entity). UPSERT is not atomic, so only a unique index
    stops two concurrent writers from each inserting. A collection that already
    holds duplicates cannot take the index, so those are collapsed once and the
    index is retried."""
    fields = ["_from", "_to"]
    if await http_client.ensure_persistent_index(_EDGE, fields, True):
        return True
    await http_client.execute_aql(DEDUPE_MENTIONS_AQL)
    return bool(await http_client.ensure_persistent_index(_EDGE, fields, True))


def _bind(query: NamedEntityQueryLike, *, skip: int, limit: int) -> dict[str, Any]:
    return {
        "kinds": list(query.kinds or []),
        "prefix": (query.name_prefix or "").strip().lower(),
        "dateStart": query.date_start_ms,
        "dateEnd": query.date_end_ms,
        "amountMin": query.amount_min,
        "amountMax": query.amount_max,
        "currency": query.currency or "",
        "quantityMin": query.quantity_min,
        "quantityMax": query.quantity_max,
        "dimension": query.dimension or "",
        "percentMin": query.percent_min,
        "percentMax": query.percent_max,
        "skip": skip,
        "limit": limit,
    }


@dataclass
class NamedEntityQuery:
    kinds: list[str] | None = None
    name_prefix: str = ""
    date_start_ms: int | None = None
    date_end_ms: int | None = None
    amount_min: float | None = None
    amount_max: float | None = None
    currency: str = ""
    quantity_min: float | None = None
    quantity_max: float | None = None
    dimension: str = ""
    percent_min: float | None = None
    percent_max: float | None = None
    limit: int = 50
    cursor: str = ""


NamedEntityQueryLike = NamedEntityQuery


def arango_node(doc: dict[str, Any]) -> dict[str, Any]:
    stored = dict(doc)
    stored["_key"] = doc["id"]
    stored.pop("id", None)
    return stored


def neo4j_node(doc: dict[str, Any]) -> dict[str, Any]:
    return dict(doc)


async def _no_retry(write):
    return await write()


_UNIQUE_VIOLATION = re.compile(r'"errorNum":\s*1210|\[1210\]')
# Neo4j raises this when the sweep deleted a node this transaction had matched.
_NODE_GONE = re.compile(r"Neo\.ClientError\.Statement\.EntityNotFound")
_AQL_BIND = re.compile(r"@(\w+)")


class NamedEntityGraph:
    def __init__(self, execute, dialect: str, retry=None) -> None:
        self._raw_execute = execute
        self._dialect = dialect
        # ArangoDB fails concurrent writes to one key with errorNum 1200 instead of waiting.
        self._retry = retry or _no_retry

    async def _execute(self, statement: str, binds: dict[str, Any], transaction: str | None = None):
        if self._dialect == "arango":
            # AQL rejects a bind variable the statement does not declare (errorNum 1552).
            declared = set(_AQL_BIND.findall(statement))
            binds = {name: value for name, value in binds.items() if name in declared}
        if transaction is None:
            return await self._raw_execute(statement, binds)
        return await self._raw_execute(statement, binds, transaction=transaction)

    async def _write(self, statement: str, binds: dict[str, Any], transaction: str | None = None):
        if transaction is not None:
            # A conflict aborts the whole transaction, so only its owner can retry it.
            return await self._execute(statement, binds, transaction)
        return await self._retry(lambda: self._execute(statement, binds))

    async def create_if_absent(self, docs: list[dict[str, Any]]) -> None:
        if not docs:
            return
        if self._dialect == "arango":
            payload = [arango_node(doc) for doc in docs]
            await self._write(RECLAIM_AQL, {"keys": [doc["_key"] for doc in payload]})
            await self._write(CREATE_AQL, {"docs": payload})
            return
        await self._write(CREATE_CYPHER, {"docs": [neo4j_node(doc) for doc in docs]})

    async def find(self, org_id: str, kind: str, norm_keys: list[str]) -> list[dict]:
        if not norm_keys:
            return []
        if self._dialect == "arango":
            rows = await self._execute(FIND_AQL, {"orgId": org_id, "kind": kind, "keys": norm_keys})
            return rows or []
        rows = await self._execute(
            FIND_CYPHER,
            {"orgId": org_id, "kind": kind, "keys": norm_keys, "partition": alias_partition(kind)},
        )
        return _unwrap(rows)

    async def add_aliases(
        self, org_id: str, kind: str, key: str, aliases: list[str], normalized: list[str], now: int
    ) -> None:
        if not aliases:
            return
        binds = {
            "orgId": org_id,
            "kind": kind,
            "key": key,
            "aliases": aliases[:_ALIAS_CAP],
            "normalized": normalized[:_ALIAS_CAP],
            "maxAliases": _ALIAS_CAP,
            "now": now,
            "partition": alias_partition(kind),
        }
        query = ALIAS_AQL if self._dialect == "arango" else ALIAS_CYPHER
        await self._write(query, binds)

    async def query(self, org_id: str, query: NamedEntityQueryLike) -> dict[str, Any]:
        skip = int(query.cursor or 0)
        limit = max(1, min(int(query.limit or 50), 200))
        binds = _bind(query, skip=skip, limit=limit)
        binds["orgId"] = org_id
        statement = QUERY_AQL if self._dialect == "arango" else QUERY_CYPHER
        rows = await self._execute(statement, binds) or []
        entities = rows if self._dialect == "arango" else _unwrap(rows)
        cursor = str(skip + limit) if len(entities) == limit else ""
        return {"entities": entities, "cursor": cursor}

    async def records_for(
        self,
        org_id: str,
        entity_ids: list[str] | None,
        query: NamedEntityQueryLike | None,
        limit: int,
        within: list[str] | None = None,
    ) -> dict[str, Any]:
        """Records that satisfy every constraint of the filter. Each constraint is
        matched on its own and the record sets intersected, so "an amount and a date"
        is a record mentioning both, not one entity that is both. A constraint too
        broad to list is checked within the narrowest one that could be listed; only
        when none can be listed is the filter too broad."""
        cap = min(max(1, limit), _SCAN_CAP)
        if entity_ids:
            # A stored id keeps working after a merge: its survivor's mentions count too.
            survivors = await self.resolve_redirects(org_id, list(entity_ids))
            entity_ids = list(dict.fromkeys([*entity_ids, *survivors.values()]))
        predicates = _predicates(entity_ids, query)
        if not predicates:
            return {"hits": [], "truncated": False}
        if within is not None:
            return await self._records_within(org_id, predicates, within, cap)
        found = [await self._match(org_id, parts, cap) for parts in predicates]
        listed = [ids for ids in found if ids is not None]
        if not listed:
            return {"hits": [], "truncated": True}
        selected = set(min(listed, key=len))
        for parts, ids in zip(predicates, found):
            if not selected:
                break
            matched = ids if ids is not None else await self._match_within(org_id, parts, sorted(selected))
            selected &= set(matched)
        if not selected:
            return {"hits": [], "truncated": False}
        statement = RECORD_HITS_AQL if self._dialect == "arango" else RECORD_HITS_CYPHER
        rows = await self._execute(statement, {"orgId": org_id, "recordIds": sorted(selected)}) or []
        return {"hits": rows, "truncated": False}

    async def resolve_redirects(self, org_id: str, ids: list[str]) -> dict[str, str]:
        """Each id mapped to the node it was finally merged into; an id with no
        redirect maps to itself. A chain stops at a cycle or after the hop cap,
        and never leaves the org."""
        final = {node_id: node_id for node_id in ids}
        current = dict(final)
        visited = {node_id: {node_id} for node_id in ids}
        for _ in range(MAX_MERGE_REDIRECT_HOPS):
            pending = sorted(set(current.values()))
            statement = REDIRECTS_AQL if self._dialect == "arango" else REDIRECTS_CYPHER
            rows = await self._execute(statement, {"orgId": org_id, "ids": pending}) or []
            hops = {str(row["id"]): str(row["mergedInto"]) for row in rows if row.get("mergedInto")}
            moved = False
            for origin, node in current.items():
                target = hops.get(node)
                if target and target not in visited[origin]:
                    visited[origin].add(target)
                    current[origin] = target
                    moved = True
            if not moved:
                break
        final.update(current)
        return final

    async def _records_within(
        self, org_id: str, predicates: list[list[_Part]], within: list[str], cap: int
    ) -> dict[str, Any]:
        """The filter evaluated only over ``within`` (the records a caller may read),
        so neither the hits nor "too broad" depend on records outside it."""
        selected = list(dict.fromkeys(within))
        for parts in predicates:
            matched: set[str] = set()
            for start in range(0, len(selected), _WITHIN_CHUNK):
                matched.update(await self._match_within(org_id, parts, selected[start:start + _WITHIN_CHUNK]))
            selected = [record_id for record_id in selected if record_id in matched]
            if not selected:
                return {"hits": [], "truncated": False}
        if len(selected) > cap:
            return {"hits": [], "truncated": True}
        statement = RECORD_HITS_AQL if self._dialect == "arango" else RECORD_HITS_CYPHER
        rows = await self._execute(statement, {"orgId": org_id, "recordIds": selected}) or []
        return {"hits": rows, "truncated": False}

    async def _match(self, org_id: str, parts: list[_Part], cap: int) -> list[str] | None:
        """Record ids for one constraint, or None when there are more than ``cap``."""
        matched: set[str] = set()
        for part in parts:
            ids = await (self._node_records(org_id, part, cap) if part.source == "node" else self._value_records(org_id, part, cap))
            if ids is None:
                return None
            matched.update(ids)
            if len(matched) > cap:
                return None
        return sorted(matched)

    async def _node_records(self, org_id: str, part: _Part, cap: int) -> list[str] | None:
        keys = list(part.binds["ids"])
        if part.binds["kinds"] or part.binds["prefix"] or not keys:
            binds = {**part.binds, "orgId": org_id, "limit": cap + 1}
            statement = MATCHED_KEYS_AQL if self._dialect == "arango" else MATCHED_KEYS_CYPHER
            rows = await self._execute(statement, binds) or []
            keys = [row if isinstance(row, str) else (row.get("_key") or row.get("id") or "") for row in rows]
            keys = [key for key in keys if key]
            if len(keys) > cap:
                return None
        if not keys:
            return []
        binds = {"orgId": org_id, "keys": keys, "targets": [f"{_COLLECTION}/{key}" for key in keys], "limit": cap + 1}
        statement = NODE_RECORDS_AQL if self._dialect == "arango" else NODE_RECORDS_CYPHER
        ids = _record_ids(await self._execute(statement, binds))
        return None if len(ids) > cap else ids

    async def _value_records(self, org_id: str, part: _Part, cap: int) -> list[str] | None:
        binds = {**part.binds, "orgId": org_id, "within": False, "candidates": [], "limit": cap + 1}
        statement = VALUE_RECORDS_AQL if self._dialect == "arango" else VALUE_RECORDS_CYPHER
        ids = _record_ids(await self._execute(statement, binds))
        return None if len(ids) > cap else ids

    async def _match_within(self, org_id: str, parts: list[_Part], candidates: list[str]) -> list[str]:
        matched: set[str] = set()
        for part in parts:
            if part.source == "node":
                binds = {**part.binds, "orgId": org_id, "candidates": candidates}
                statement = NODE_RECORDS_WITHIN_AQL if self._dialect == "arango" else NODE_RECORDS_WITHIN_CYPHER
            else:
                binds = {**part.binds, "orgId": org_id, "within": True, "candidates": candidates, "limit": len(candidates) + 1}
                statement = VALUE_RECORDS_AQL if self._dialect == "arango" else VALUE_RECORDS_CYPHER
            matched.update(_record_ids(await self._execute(statement, binds)))
        return sorted(matched)

    async def entities_for_record(self, record_id: str) -> list[dict]:
        binds = {"recordId": record_id}
        if self._dialect == "arango":
            nodes = await self._execute(RECORD_ENTITIES_AQL, binds) or []
            values = await self._execute(RECORD_VALUES_AQL, binds) or []
            return [*nodes, *values]
        nodes = await self._execute(RECORD_ENTITIES_CYPHER, binds) or []
        values = await self._execute(RECORD_VALUES_CYPHER, binds) or []
        return [row.get("entity", row) for row in (*nodes, *values)]

    async def replace_values(self, record_id: str, docs: list[dict[str, Any]], transaction: str | None = None) -> None:
        """Make ``docs`` the record's value mentions. Rows belong to one record, so
        this never waits on another record's write."""
        keys = [doc["id"] for doc in docs]
        if self._dialect == "arango":
            await self._write(DELETE_STALE_VALUES_AQL, {"recordId": record_id, "keys": keys}, transaction)
            if docs:
                await self._write(UPSERT_VALUES_AQL, {"docs": [arango_node(doc) for doc in docs]}, transaction)
            return
        await self._write(DELETE_STALE_VALUES_CYPHER, {"recordId": record_id, "keys": keys}, transaction)
        if docs:
            await self._write(UPSERT_VALUES_CYPHER, {"docs": [neo4j_node(doc) for doc in docs]}, transaction)

    async def copy_mentions(self, source_key: str, target_key: str) -> int:
        if self._dialect == "arango":
            copied = len(await self._execute(COPY_AQL, {"source": source_key, "target": target_key}) or [])
            rows = await self._execute(RECORD_VALUES_AQL, {"recordId": source_key}) or []
        else:
            edges = await self._execute(COPY_CYPHER, {"source": source_key, "target": target_key}) or []
            copied = int(edges[0].get("copied") or 0) if edges and isinstance(edges[0], dict) else len(edges)
            rows = [row.get("entity", row) for row in await self._execute(RECORD_VALUES_CYPHER, {"recordId": source_key}) or []]
        org_id = next((row.get("orgId") for row in rows if row.get("orgId")), None)
        if not org_id:
            return copied
        statement = RECORD_HITS_AQL if self._dialect == "arango" else RECORD_HITS_CYPHER
        if not await self._execute(statement, {"orgId": org_id, "recordIds": [target_key]}):
            return copied
        docs = [_copied_value(row, target_key) for row in rows if row.get("orgId") == org_id]
        await self.replace_values(target_key, docs)
        return copied + len(docs)

    async def schedule_persist_retry(
        self, org_id: str, record_id: str, error: str, now: int, base_backoff_ms: int, max_backoff_ms: int,
        attempts_so_far: int = 0,
    ) -> int:
        binds = {
            "orgId": org_id, "recordId": record_id, "error": error[:200], "now": now,
            "baseBackoffMs": base_backoff_ms, "maxBackoffMs": max_backoff_ms, "attemptsSoFar": attempts_so_far,
        }
        if self._dialect == "arango":
            rows = await self._write(SCHEDULE_RETRY_AQL, binds) or []
            return int(rows[0]) if rows else 0
        rows = await self._write(SCHEDULE_RETRY_CYPHER, binds) or []
        return int(rows[0].get("attempts") or 0) if rows else 0

    async def due_persist_retries(self, now: int, limit: int) -> list[dict[str, Any]]:
        binds = {"now": now, "limit": max(1, limit)}
        statement = DUE_RETRIES_AQL if self._dialect == "arango" else DUE_RETRIES_CYPHER
        return list(await self._execute(statement, binds) or [])

    async def clear_persist_retry(self, record_id: str, due_at: int | None, transaction: str | None = None) -> bool:
        """With ``due_at``, only while the retry is still the one due then; without,
        whatever is pending (a newer write supersedes it)."""
        binds = {"recordId": record_id, "dueAt": due_at}
        statement = CLEAR_RETRY_AQL if self._dialect == "arango" else CLEAR_RETRY_CYPHER
        return bool(await self._write(statement, binds, transaction))

    async def delete_dangling_values(self, org_id: str, batch_size: int) -> int:
        binds = {"orgId": org_id, "batch": max(1, batch_size)}
        return await self._count(DANGLING_VALUES_AQL, DANGLING_VALUES_CYPHER, "removed", binds)

    def _orphan_binds(
        self,
        org_id: str,
        batch_size: int,
        marked_before_ms: int | None,
        entity_ids: list[str] | None,
        skip_kinds: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "orgId": org_id,
            "ids": entity_ids,
            "skipKinds": skip_kinds,
            "batch": max(1, batch_size),
            "cutoff": marked_before_ms,
            "aliasPrefix": alias_partition(""),
        }

    async def find_orphans(
        self, org_id: str, batch_size: int, marked_before_ms: int | None, skip_kinds: list[str] | None = None
    ) -> list[str]:
        binds = self._orphan_binds(org_id, batch_size, marked_before_ms, None, skip_kinds)
        if self._dialect == "arango":
            rows = await self._execute(FIND_ORPHANS_AQL, binds) or []
            return [row if isinstance(row, str) else str(row) for row in rows]
        rows = await self._execute(FIND_ORPHANS_CYPHER, binds) or []
        return [row if isinstance(row, str) else str(row.get("id") or "") for row in rows]

    async def delete_orphans(
        self,
        org_id: str,
        batch_size: int,
        marked_before_ms: int | None = None,
        entity_ids: list[str] | None = None,
    ) -> list[str]:
        if entity_ids is not None and not entity_ids:
            return []
        binds = self._orphan_binds(org_id, batch_size, marked_before_ms, entity_ids)
        if self._dialect == "arango":
            rows = await self._write(ORPHAN_AQL, binds) or []
            return [row if isinstance(row, str) else str(row) for row in rows]
        rows = await self._write(ORPHAN_CYPHER, binds) or []
        return [row if isinstance(row, str) else str(row.get("id") or "") for row in rows]

    async def mark_orphans(self, org_id: str, now_ms: int, batch_size: int) -> int:
        binds = {"orgId": org_id, "now": now_ms, "batch": max(1, batch_size)}
        return await self._count(MARK_ORPHANS_AQL, MARK_ORPHANS_CYPHER, "marked", binds)

    async def clear_orphan_marks(self, org_id: str, batch_size: int) -> int:
        binds = {"orgId": org_id, "batch": max(1, batch_size)}
        return await self._count(CLEAR_ORPHAN_MARKS_AQL, CLEAR_ORPHAN_MARKS_CYPHER, "cleared", binds)

    async def delete_dangling_mentions(self, entity_ids: list[str]) -> int:
        if self._dialect != "arango" or not entity_ids:
            return 0
        targets = [f"{_COLLECTION}/{key}" for key in entity_ids]
        return len(await self._write(DANGLING_MENTIONS_AQL, {"targets": targets}) or [])

    async def _count(self, aql: str, cypher: str, column: str, binds: dict[str, Any]) -> int:
        if self._dialect == "arango":
            return len(await self._write(aql, binds) or [])
        rows = await self._write(cypher, binds) or []
        if rows and isinstance(rows[0], dict):
            return int(rows[0].get(column) or 0)
        return 0


class NamedEntityGraphMixin:
    """Provider methods. ``_ner_dialect`` is ``arango`` or ``neo4j``."""

    _ner_dialect: str = ""

    def _named_entity_graph(self) -> NamedEntityGraph:
        execute = self.execute_query  # type: ignore[attr-defined]
        retry_conflicts = getattr(self, "_retry_write_conflicts", None)
        retry = (lambda write: retry_conflicts(write, None)) if retry_conflicts else None
        return NamedEntityGraph(execute, self._ner_dialect, retry)

    def is_named_entity_write_retryable(self, error: BaseException) -> bool:
        """A deadlock, lock timeout or write conflict; on ArangoDB a unique-index
        violation from two writers inserting the same edge, where the loser retries
        and finds the edge; on Neo4j a node the sweep deleted under the write,
        which the retry creates again."""
        if self.is_write_conflict(error) or self.is_transient_error(error):  # type: ignore[attr-defined]
            return True
        pattern = _UNIQUE_VIOLATION if self._ner_dialect == "arango" else _NODE_GONE
        return bool(pattern.search(str(error)))

    async def create_named_entities_if_absent(self, nodes: list[dict]) -> None:
        await self._named_entity_graph().create_if_absent(nodes)

    async def find_named_entities(self, org_id: str, kind: str, norm_keys: list[str]) -> list[dict]:
        return await self._named_entity_graph().find(org_id, kind, norm_keys)

    async def add_named_entity_aliases(
        self, org_id: str, kind: str, key: str, aliases: list[str], normalized: list[str]
    ) -> None:
        from app.utils.time_conversion import get_epoch_timestamp_in_ms

        await self._named_entity_graph().add_aliases(
            org_id, kind, key, aliases, normalized, get_epoch_timestamp_in_ms()
        )

    async def query_named_entities(self, org_id: str, query) -> dict:
        return await self._named_entity_graph().query(org_id, query)

    async def get_records_for_named_entities(
        self,
        org_id: str,
        entity_ids: list[str] | None = None,
        query=None,
        limit: int = 5000,
        within: list[str] | None = None,
    ) -> dict:
        return await self._named_entity_graph().records_for(org_id, entity_ids, query, limit, within)

    async def get_named_entities_for_record(self, record_id: str) -> list[dict]:
        return await self._named_entity_graph().entities_for_record(record_id)

    async def copy_named_entity_mentions(self, source_key: str, target_key: str) -> int:
        return await self._named_entity_graph().copy_mentions(source_key, target_key)

    async def resolve_named_entity_redirects(self, org_id: str, ids: list[str]) -> dict[str, str]:
        return await self._named_entity_graph().resolve_redirects(org_id, ids)

    async def schedule_named_entity_persist_retry(
        self, org_id: str, record_id: str, error: str, attempts_so_far: int = 0,
    ) -> int:
        from app.utils.time_conversion import get_epoch_timestamp_in_ms

        return await self._named_entity_graph().schedule_persist_retry(
            org_id, record_id, error, get_epoch_timestamp_in_ms(), PERSIST_RETRY_BASE_MS, PERSIST_RETRY_MAX_MS,
            attempts_so_far,
        )

    async def get_due_named_entity_persist_retries(self, limit: int) -> list[dict]:
        from app.utils.time_conversion import get_epoch_timestamp_in_ms

        return await self._named_entity_graph().due_persist_retries(get_epoch_timestamp_in_ms(), limit)

    async def clear_named_entity_persist_retry(
        self, record_id: str, due_at: int | None, transaction: str | None = None,
    ) -> bool:
        return await self._named_entity_graph().clear_persist_retry(record_id, due_at, transaction)

    async def replace_named_entity_values(
        self, record_id: str, docs: list[dict], transaction: str | None = None,
    ) -> None:
        await self._named_entity_graph().replace_values(record_id, docs, transaction)

    async def delete_dangling_named_entity_values(self, org_id: str, batch_size: int = 200) -> int:
        return await self._named_entity_graph().delete_dangling_values(org_id, batch_size)

    async def find_orphan_named_entities(
        self,
        org_id: str,
        batch_size: int = 200,
        marked_before_ms: int | None = None,
        skip_kinds: list[str] | None = None,
    ) -> list[str]:
        return await self._named_entity_graph().find_orphans(org_id, batch_size, marked_before_ms, skip_kinds)

    async def delete_orphan_named_entities(
        self,
        org_id: str,
        batch_size: int = 200,
        marked_before_ms: int | None = None,
        entity_ids: list[str] | None = None,
    ) -> list[str]:
        return await self._named_entity_graph().delete_orphans(org_id, batch_size, marked_before_ms, entity_ids)

    async def mark_orphan_named_entities(self, org_id: str, now_ms: int, batch_size: int = 200) -> int:
        return await self._named_entity_graph().mark_orphans(org_id, now_ms, batch_size)

    async def clear_orphan_named_entity_marks(self, org_id: str, batch_size: int = 200) -> int:
        return await self._named_entity_graph().clear_orphan_marks(org_id, batch_size)

    async def delete_dangling_named_entity_mentions(self, entity_ids: list[str]) -> int:
        return await self._named_entity_graph().delete_dangling_mentions(entity_ids)


_DATE_KINDS = frozenset({EntityKind.DATE.value, EntityKind.DATE_RANGE.value, EntityKind.DATE_TIME.value})
_VALUE_KIND_NAMES = frozenset(kind.value for kind in VALUE_KINDS)
# A kind that names a typed constraint's own kind narrows that constraint; any
# other kind in the filter is a constraint of its own ("mentions some such entity").
_TYPED_CONSTRAINT_KINDS = {
    "date": _DATE_KINDS,
    "amount": frozenset({EntityKind.CURRENCY.value}),
    "quantity": frozenset({EntityKind.DIMENSION.value, EntityKind.AGE.value}),
    "percent": frozenset({EntityKind.PERCENTAGE.value}),
}
_NO_VALUE = {
    "kinds": [], "dateStart": None, "dateEnd": None, "amountMin": None, "amountMax": None, "currency": "",
    "quantityMin": None, "quantityMax": None, "dimension": "", "percentMin": None, "percentMax": None,
}


@dataclass(frozen=True)
class _Part:
    """One way to satisfy a constraint: entity nodes or value rows."""

    source: str
    binds: dict[str, Any]


def _predicates(entity_ids: list[str] | None, query: NamedEntityQueryLike | None) -> list[list[_Part]]:
    """The filter's constraints, each a union of parts; every constraint must hold."""
    kinds = [str(kind) for kind in ((query.kinds if query else None) or [])]
    prefix = ((query.name_prefix if query else "") or "").strip().lower()
    ids = [str(item) for item in (entity_ids or []) if item]
    node_kinds = [kind for kind in kinds if kind not in _VALUE_KIND_NAMES]
    consumed: set[str] = set()
    constraints: list[list[_Part]] = []
    if ids or prefix:
        consumed.update(node_kinds)
        # A name or an id only ever matches an entity node, so a filter whose kinds
        # are all values leaves this constraint unsatisfiable.
        possible = not kinds or bool(node_kinds)
        constraints.append([_Part("node", {"ids": ids, "kinds": node_kinds, "prefix": prefix})] if possible else [])
    if query is not None:
        typed = {
            "date": query.date_start_ms is not None and {"dateStart": query.date_start_ms, "dateEnd": query.date_end_ms},
            "amount": query.amount_min is not None and {
                "amountMin": query.amount_min, "amountMax": query.amount_max, "currency": query.currency or "",
            },
            "quantity": query.quantity_min is not None and {
                "quantityMin": query.quantity_min, "quantityMax": query.quantity_max, "dimension": query.dimension or "",
            },
            "percent": query.percent_min is not None and {"percentMin": query.percent_min, "percentMax": query.percent_max},
        }
        for name, binds in typed.items():
            if not binds:
                continue
            own = [kind for kind in kinds if kind in _TYPED_CONSTRAINT_KINDS[name]]
            consumed.update(own)
            constraints.append([_Part("value", {**_NO_VALUE, **binds, "kinds": own})])
    rest = [kind for kind in kinds if kind not in consumed]
    if rest:
        parts = []
        rest_nodes = [kind for kind in rest if kind not in _VALUE_KIND_NAMES]
        rest_values = [kind for kind in rest if kind in _VALUE_KIND_NAMES]
        if rest_nodes:
            parts.append(_Part("node", {"ids": [], "kinds": rest_nodes, "prefix": ""}))
        if rest_values:
            parts.append(_Part("value", {**_NO_VALUE, "kinds": rest_values}))
        constraints.append(parts)
    return constraints


def _record_ids(rows: list | None) -> list[str]:
    ids = []
    for row in rows or []:
        value = row if isinstance(row, str) else (row.get("recordId") or row.get("matched") if isinstance(row, dict) else None)
        if value:
            ids.append(str(value))
    return list(dict.fromkeys(ids))


def _copied_value(row: dict[str, Any], target_key: str) -> dict[str, Any]:
    doc = {key: value for key, value in row.items() if key not in ("_key", "_id", "_rev", "id")}
    doc["recordId"] = target_key
    doc["id"] = value_mention_key(target_key, str(row.get("kind") or ""), str(row.get("normKey") or ""))
    return doc


def _unwrap(rows: list | None) -> list[dict]:
    out = []
    for row in rows or []:
        if isinstance(row, dict) and "n" in row and isinstance(row["n"], dict):
            out.append(row["n"])
        elif isinstance(row, dict):
            out.append(row)
    return out
