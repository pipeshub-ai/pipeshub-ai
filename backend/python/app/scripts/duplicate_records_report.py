"""Operator command: count records that share a (connectorId, externalRecordId) pair.

    python -m app.scripts.duplicate_records_report [--org ORG] [--timeout SECONDS]

A connector's record is meant to be unique by its source id within one connector
instance, but the graph only indexes that pair, it does not enforce it. This
reads how many pairs hold more than one record, per connector, so a cleanup can
be sized before a unique constraint is added. It writes nothing.

The scan reads every record (or every record of one org), so run it against a
copy or off-peak on a large graph.

Output is one JSON object: totals and the connectors with duplicates, worst first.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Any

from app.config.constants.arangodb import CollectionNames

EXIT_FAILED = 2

_NEO4J_QUERY = """
MATCH (n:Record)
WHERE n.externalRecordId IS NOT NULL AND n.connectorId IS NOT NULL
  AND ($org_id IS NULL OR n.orgId = $org_id)
WITH n.connectorId AS connectorId, n.externalRecordId AS externalRecordId, count(*) AS copies
WHERE copies > 1
RETURN connectorId, count(*) AS pairs, sum(copies) AS records
ORDER BY records DESC
"""

_ARANGO_QUERY = f"""
FOR r IN {CollectionNames.RECORDS.value}
  FILTER r.externalRecordId != null AND r.connectorId != null
  FILTER @org_id == null OR r.orgId == @org_id
  COLLECT connectorId = r.connectorId, externalRecordId = r.externalRecordId WITH COUNT INTO copies
  FILTER copies > 1
  COLLECT c = connectorId AGGREGATE pairs = COUNT(1), records = SUM(copies)
  SORT records DESC
  RETURN {{connectorId: c, pairs: pairs, records: records}}
"""


def duplicate_query(data_store: str, org_id: str | None) -> tuple[str, dict[str, Any]]:
    """The read-only query for this graph backend, and its bind variables."""
    if data_store == "neo4j":
        return _NEO4J_QUERY, {"org_id": org_id}
    if data_store == "arangodb":
        return _ARANGO_QUERY, {"org_id": org_id}
    raise ValueError(f"Unsupported DATA_STORE: {data_store}")


def summarize(rows: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Totals over the per-connector rows; ``extraRecords`` is what a cleanup would remove."""
    connectors = [
        {
            "connectorId": row["connectorId"],
            "pairs": int(row["pairs"]),
            "records": int(row["records"]),
            "extraRecords": int(row["records"]) - int(row["pairs"]),
        }
        for row in rows or []
    ]
    return {
        "connectorsWithDuplicates": len(connectors),
        "duplicatePairs": sum(c["pairs"] for c in connectors),
        "extraRecords": sum(c["extraRecords"] for c in connectors),
        "connectors": connectors,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.scripts.duplicate_records_report",
        description="Count records sharing a (connectorId, externalRecordId) pair. Read-only.",
    )
    parser.add_argument("--org", default=None, help="Only this org's records")
    parser.add_argument(
        "--timeout", type=float, default=600.0, help="Server-side query limit in seconds"
    )
    return parser


async def _main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    from app.containers.indexing import IndexingAppContainer

    data_store = os.getenv("DATA_STORE", "arangodb").lower()
    try:
        query, bind_vars = duplicate_query(data_store, args.org)
        container = IndexingAppContainer.init("duplicate_records_report")
        logger = container.logger()
        graph_provider = await container.graph_provider()
    except Exception as exc:
        # The container's logger may be what failed.
        logging.getLogger(__name__).exception("duplicate_records_report: setup failed")
        sys.stderr.write(json.dumps({"error": f"{type(exc).__name__}: {exc}"}) + "\n")
        return EXIT_FAILED
    try:
        rows = await graph_provider.execute_query(
            query, bind_vars=bind_vars, timeout_seconds=args.timeout
        )
        if rows is None:
            raise RuntimeError("the query returned no result; see the provider log")
        report = {"dataStore": data_store, "org": args.org, **summarize(rows)}
        sys.stdout.write(json.dumps(report) + "\n")
        return 0
    except Exception as exc:
        logger.exception("duplicate_records_report failed")
        sys.stderr.write(json.dumps({"error": f"{type(exc).__name__}: {exc}"}) + "\n")
        return EXIT_FAILED
    finally:
        await graph_provider.disconnect()


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(sys.argv[1:])))
