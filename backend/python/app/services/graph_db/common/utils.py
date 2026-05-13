from typing import Any, Dict, List, Optional


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
        "origin": "CONNECTOR",
        "stats": {
            "total": total,
            "indexingStatus": indexing_status_counts,
        },
        "byRecordType": list(record_type_counts.values()),
    }
