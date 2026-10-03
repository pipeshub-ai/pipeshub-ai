import re
from collections.abc import Iterable
from typing import Any, Dict, List, Optional

from app.config.constants.arangodb import Connectors, OriginTypes, RecordRelations

# Connectors whose record groups are scoped by their root instead of by the full
# descendant closure. Slack qualifies because grants sit on the channel and every
# thread under it inherits, so carrying one group id per thread buys nothing —
# a busy workspace has ~50x more threads than channels. Records from these
# connectors must set `rootRecordGroupId`; see ROOT_RECORD_GROUP_IDS_FIELD.
ROOT_SCOPED_CONNECTOR_TYPES = frozenset(
    value.upper()
    for value in (Connectors.SLACK.value, Connectors.SLACK_WORKSPACE.value)
)

# Records granted directly to a user with no container covering them. Expected
# near-empty: app-level connectors write a blanket ORG grant and land in
# app_ids, Drive shared-with-me files get a synthetic group, KB uploads land in
# app_ids. What is left is a record-level connector granting per record while
# creating no record group.
#
# On overflow the whole request falls back to the record-id path. Truncating
# instead would be a permanent, silent, error-free hole — exactly the failure
# mode container filtering is supposed to avoid.
MAX_DIRECT_GRANT_RECORDS = 2_000

# Ceiling on |app_ids| + |record_group_ids| + |direct_records| in one filter.
# Filter size is what actually breaks: OpenSearch rejects a terms clause above
# index.max_terms_count (65,536 by default) and degrades well before it, Redis
# parses the query string on its main thread outside search-timeout, and Qdrant
# probes the payload index once per value per segment. Far below all three, so
# that hitting it means something is wrong upstream rather than that the tenant
# is merely large.
CONTAINER_FILTER_MAX_TERMS = 25_000

# Depth for the INHERIT_PERMISSIONS closure when expanding accessible record
# groups. Not a taste call — an invariant: the closure must be at least as deep
# as the per-record verifier, which walks `1..20`. A shallower closure omits
# containers whose records the verifier would have admitted, and a container
# omitted is unrecoverable recall loss with nothing to notice.
CONTAINER_INHERIT_MAX_DEPTH = 20

# A user reaching one KB through several grants (direct, or more than one team)
# acts with the strongest of them: the ranking both providers already use to pick
# the highest permission on a record. Roles not listed rank below all of these.
KB_ROLE_PRIORITY: dict[str, int] = {
    "OWNER": 6,
    "ORGANIZER": 5,
    "FILEORGANIZER": 4,
    "WRITER": 3,
    "COMMENTER": 2,
    "READER": 1,
}

# How deep a delete follows containment (PARENT_CHILD / ATTACHMENT) from a
# folder or record. Folder nesting has no enforced limit, so this is a guard
# against a cycle, not a product limit: a cascade that stopped at 20 left
# anything deeper behind.
CONTAINMENT_MAX_DEPTH = 1000

# Records considered per entity when listing an entity's records, taken
# before the newest-first sort. Without a bound, a language or broad category
# linked to most of an org's records is sorted in full on every page. Past the
# cap the order is newest among the first records found, and paging ends; the
# provider reports that through ``EntityCandidateRows.capped``.
ENTITY_CANDIDATE_SCAN_CAP = 10_000

# Edge types that make one record the storage/display parent of another.
CANONICAL_PARENT_RELATION_TYPES = (
    RecordRelations.PARENT_CHILD.value,
    RecordRelations.ATTACHMENT.value,
)

# Chains only branch on graph anomalies (duplicate parent edges, two nodes with
# the same externalRecordId, several BELONGS_TO parents), so this caps result
# size; it is not a tuning knob.
PATH_MAX_CANDIDATES = 64

# Deepest a knowledge-base folder may sit; a folder directly in the collection is
# depth 1. Enforced on folder create, upload and move.
KB_MAX_FOLDER_DEPTH = 20


def select_canonical_chain_names(
    rows: list[Any] | None,
    name_fields: tuple[str, ...],
) -> list[str]:
    """Pick one ancestor chain from path-query candidates; return names root-first.

    Each row is ``{"ids": [start, parent, ..., ancestor], <name_field>: [...]}``
    with lists aligned by position. Arango and Neo4j both return every
    canonical chain and this picks the same one for both: the longest, ties
    broken by the lexicographically smallest id sequence. A node's name is the
    first non-empty string among *name_fields*; nodes without one are dropped.
    """
    best_key: tuple[int, list[str]] | None = None
    best_row: dict[str, Any] | None = None
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        ids = row.get("ids")
        if not isinstance(ids, list) or not ids:
            continue
        key = (-len(ids), [str(i) for i in ids])
        if best_key is None or key < best_key:
            best_key, best_row = key, row
    if best_row is None:
        return []
    names: list[str] = []
    for index in range(len(best_row["ids"])):
        for name_field in name_fields:
            values = best_row.get(name_field)
            value = values[index] if isinstance(values, list) and index < len(values) else None
            if isinstance(value, str) and value:
                names.append(value)
                break
    names.reverse()
    return names


class EntityCandidateRows(list):
    """One entity's candidate record rows, plus whether the provider's scan
    stopped at ``ENTITY_CANDIDATE_SCAN_CAP``.

    When ``capped`` the rows are the newest within an arbitrary bounded subset
    of the entity's records, so neither "newest first" nor "no more records"
    holds for the entity as a whole. A list subclass so existing callers that
    treat the value as a plain list keep working.
    """

    def __init__(self, rows: Iterable[dict[str, Any]] = (), *, capped: bool = False) -> None:
        super().__init__(rows)
        self.capped = capped


def dedupe_agents_by_id(rows: Optional[List[Dict[str, Any]]]) -> List[str]:
    """
    Collapse a list of ``{agentId, agentName}`` rows into a list of agent names,
    deduped by ``agentId``.

    Used by ``check_toolset_instance_in_use`` and ``check_connector_in_use`` on
    both Arango and Neo4j providers. Dedupe must happen by id, not by name —
    two distinct agents can legitimately share a display name, and collapsing
    them would under-report the true blocker count in 409 messages.

    Order is preserved: the first occurrence of each ``agentId`` keeps its
    position in the output.

    Args:
        rows: Result rows, each expected to have ``agentId`` and ``agentName``.
              ``None``, empty list, and non-dict entries are tolerated.

    Returns:
        List of agent names (with duplicates allowed for distinct ids).
    """
    if not rows:
        return []

    seen_ids: set = set()
    names: List[str] = []
    for r in rows:
        if not r or not isinstance(r, dict):
            continue
        aid = r.get("agentId")
        if aid and aid not in seen_ids:
            seen_ids.add(aid)
            names.append(r.get("agentName", "Unknown"))
    return names


def build_connector_stats_response(
    rows: List[Dict[str, Any]],
    statuses: List[str],
    org_id: str,
    connector_id: str,
    origin: str = "CONNECTOR",
) -> Dict[str, Any]:
    """
    Build connector stats response from aggregated query rows.

    Used by both ArangoDB and Neo4j providers to process
    get_connector_stats query results.

    Args:
        rows: Query results with recordType, indexingStatus, cnt
        statuses: List of valid indexing status values
        org_id: Organization ID
        connector_id: Connector ID
        origin: Origin type ("CONNECTOR" or "COLLECTION"), defaults to "CONNECTOR"

    Returns:
        Formatted stats response dictionary
    """
    indexing_status_counts = {s: 0 for s in statuses}
    record_type_counts: Dict[str, Dict[str, Any]] = {}
    total = 0

    for row in rows:
        cnt = row.get("cnt", 0)
        total += cnt
        st = row.get("indexingStatus")
        if st in indexing_status_counts:
            indexing_status_counts[st] += cnt
        rt = row.get("recordType")
        if rt:
            if rt not in record_type_counts:
                record_type_counts[rt] = {
                    "recordType": rt,
                    "total": 0,
                    "indexingStatus": {s: 0 for s in statuses},
                }
            record_type_counts[rt]["total"] += cnt
            if st in statuses:
                record_type_counts[rt]["indexingStatus"][st] += cnt

    return {
        "orgId": org_id,
        "connectorId": connector_id,
        "origin": origin,
        "stats": {
            "total": total,
            "indexingStatus": indexing_status_counts,
        },
        "byRecordType": list(record_type_counts.values()),
    }


_STORAGE_DOCUMENT_ID = re.compile(r"^[0-9a-f]{24}$", re.IGNORECASE)


def uploaded_document_id(record: Dict[str, Any], type_doc: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """The storage document holding an uploaded file's original bytes, or None.

    A knowledge-base upload keeps its file in a storage document of its own,
    named by the record's externalRecordId. Folders are uploads too but hold no
    file, and other origins use externalRecordId for the source system's id.
    """
    if record.get("origin") != OriginTypes.UPLOAD.value:
        return None
    if (type_doc or {}).get("isFile") is False:
        return None
    document_id = record.get("externalRecordId")
    if isinstance(document_id, str) and _STORAGE_DOCUMENT_ID.match(document_id):
        return document_id
    return None
