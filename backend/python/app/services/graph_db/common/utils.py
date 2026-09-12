from typing import Any, Dict, List, Optional

from app.config.constants.arangodb import Connectors

# Connectors whose record groups are scoped by their root instead of by the full
# descendant closure. Slack qualifies because grants sit on the channel and every
# thread under it inherits, so carrying one group id per thread buys nothing —
# a busy workspace has ~50x more threads than channels. Records from these
# connectors must set `rootRecordGroupId`; see ROOT_RECORD_GROUP_IDS_FIELD.
ROOT_SCOPED_CONNECTOR_TYPES = frozenset(
    value.upper()
    for value in (Connectors.SLACK.value, Connectors.SLACK_WORKSPACE.value)
)

# A virtualRecordId is a content identity, so several records can share one —
# the same file synced from two connectors, say. Adjudicating a VRID only needs
# enough candidates to find one the user may read; a pathological VRID
# (boilerplate duplicated across a corpus) must not turn one search into an
# unbounded scan. Shared so both providers cap identically — a difference here
# would show up as one backend returning results the other does not.
MAX_RECORD_CANDIDATES_PER_VRID = 20

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
