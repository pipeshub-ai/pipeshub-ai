from typing import Any


def build_connector_stats_response(
    rows: list[dict[str, Any]],
    statuses: list[str],
    org_id: str,
    connector_id: str,
) -> dict[str, Any]:
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
    indexing_status_counts = dict.fromkeys(statuses, 0)
    record_type_counts: dict[str, dict[str, Any]] = {}
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
                    "indexingStatus": dict.fromkeys(statuses, 0),
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
