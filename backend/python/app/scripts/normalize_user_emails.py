"""Operator command: normalize stored user emails to lowercase in ArangoDB and Neo4j.

Batch-updates existing user records whose email field contains uppercase letters or
surrounding whitespace, making all stored user emails strictly lowercase and
index-backed under the composite (orgId, email) index.

A dry run by default; pass ``--apply`` to execute the updates:

    python -m app.scripts.normalize_user_emails --org ORG [--apply]
    python -m app.scripts.normalize_user_emails --all-orgs [--apply]

Output is structured JSON per line for monitoring and auditing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import TYPE_CHECKING, TextIO

from app.config.constants.arangodb import CollectionNames
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from logging import Logger

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

DEFAULT_BATCH_SIZE = 200
EXIT_SUCCESS = 0
EXIT_PARTIAL = 1
EXIT_INVALID = 2


async def fetch_unnormalized_users(
    graph: IGraphDBProvider,
    org_id: str | None,
    limit: int,
) -> list[dict]:
    """Find user nodes whose email is not lowercase and stripped.

    Returns dicts with keys: ``id``, ``orgId``, ``email``, ``newEmail``.
    """
    if hasattr(graph, "fetch_unnormalized_users"):
        return await graph.fetch_unnormalized_users(org_id, limit)

    from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
    from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
    from app.services.graph_db.postgres.identity import IdentityMixin

    if isinstance(graph, ArangoHTTPProvider):
        query = f"""
        FOR u IN {CollectionNames.USERS.value}
            {"FILTER u.orgId == @org_id" if org_id else ""}
            FILTER u.email != null AND u.email != "" AND u.email != LOWER(TRIM(u.email))
            LIMIT @limit
            RETURN {{ id: u._key, orgId: u.orgId, email: u.email, newEmail: LOWER(TRIM(u.email)) }}
        """
        bind_vars: dict = {"limit": limit, **({"org_id": org_id} if org_id else {})}
        return await graph.http_client.execute_aql(query, bind_vars=bind_vars)

    if isinstance(graph, Neo4jProvider):
        where_clause = "WHERE u.orgId = $org_id" if org_id else "WHERE true"
        query = f"""
        MATCH (u:User)
        {where_clause}
          AND u.email IS NOT NULL AND u.email <> "" AND u.email <> toLower(trim(u.email))
        RETURN u.id AS id, u.orgId AS orgId, u.email AS email, toLower(trim(u.email)) AS newEmail
        LIMIT $limit
        """
        params: dict = {"limit": limit, **({"org_id": org_id} if org_id else {})}
        results = await graph.client.execute_query(query, parameters=params)
        return [
            {
                "id": row["id"],
                "orgId": row.get("orgId"),
                "email": row["email"],
                "newEmail": row["newEmail"],
            }
            for row in results or []
        ]

    if isinstance(graph, IdentityMixin) or hasattr(graph, "_id_rows"):
        query = """
        /* pipeshub:graph.unnormalized_user_emails */
        SELECT id, org_id, doc->>'email' AS email, lower(trim(doc->>'email')) AS new_email
        FROM graph.nodes
        WHERE coll = 'users'
          AND ($1::text IS NULL OR org_id = $1)
          AND doc->>'email' IS NOT NULL AND doc->>'email' <> ''
          AND doc->>'email' <> lower(trim(doc->>'email'))
        LIMIT $2
        """
        rows = await graph._id_rows(query, [org_id, limit])
        return [
            {
                "id": r["id"],
                "orgId": r.get("org_id"),
                "email": r["email"],
                "newEmail": r["new_email"],
            }
            for r in rows or []
        ]

    return []


async def normalize_emails_for_org(
    graph: IGraphDBProvider,
    org_id: str | None,
    *,
    apply: bool,
    logger: Logger,
    out: TextIO,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> dict[str, int]:
    """Process user email normalization in batches for a given organization (or global)."""
    stats = {"scanned": 0, "updated": 0, "failed_batches": 0}
    seen_ids: set[str] = set()

    while True:
        try:
            candidates = await fetch_unnormalized_users(graph, org_id, batch_size)
        except Exception as exc:
            logger.exception("Failed to query unnormalized users for org %s", org_id)
            stats["failed_batches"] += 1
            out.write(json.dumps({"org_id": org_id, "error": type(exc).__name__, "message": str(exc)}) + "\n")
            break

        if not candidates:
            break

        # In dry-run mode, candidates won't be modified in DB, so break out
        # once all candidates have been seen to avoid infinite loop.
        new_candidates = [c for c in candidates if c["id"] not in seen_ids]
        if not new_candidates:
            break

        seen_ids.update(c["id"] for c in new_candidates)
        stats["scanned"] += len(new_candidates)

        if apply:
            now = get_epoch_timestamp_in_ms()
            update_nodes = [
                {
                    "id": c["id"],
                    "email": c["newEmail"],
                    "updatedAtTimestamp": now,
                }
                for c in new_candidates
            ]
            try:
                await graph.batch_upsert_nodes(update_nodes, CollectionNames.USERS.value)
                stats["updated"] += len(update_nodes)
            except Exception as exc:
                logger.exception("Failed to write normalized email batch for org %s", org_id)
                stats["failed_batches"] += 1
                out.write(json.dumps({"org_id": org_id, "error": type(exc).__name__, "message": str(exc)}) + "\n")
                continue
        else:
            stats["updated"] += len(new_candidates)

        out.write(
            json.dumps({
                "status": "progress",
                "org_id": org_id or "all",
                "batch_size": len(new_candidates),
                "total_updated": stats["updated"],
                "applied": apply,
            })
            + "\n"
        )

    out.write(
        json.dumps({
            "status": "org_summary",
            "org_id": org_id or "all",
            "scanned": stats["scanned"],
            "updated": stats["updated"],
            "failed_batches": stats["failed_batches"],
            "applied": apply,
        })
        + "\n"
    )
    return stats


async def run_normalization(
    graph: IGraphDBProvider,
    *,
    org_id: str | None = None,
    all_orgs: bool = False,
    apply: bool = False,
    logger: Logger,
    out: TextIO,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> int:
    """Entrypoint to normalize user emails across one or all organizations."""
    if not org_id and not all_orgs:
        out.write(json.dumps({"error": "Either --org or --all-orgs must be provided."}) + "\n")
        return EXIT_INVALID

    total_stats = {"total_scanned": 0, "total_updated": 0, "total_failed_batches": 0}

    if org_id:
        target_orgs: list[str | None] = [org_id]
    else:
        try:
            org_docs = await graph.get_all_orgs()
            target_orgs = [o.get("id") or o.get("_key") for o in org_docs if o]
        except Exception:
            # If get_all_orgs fails or is unsupported, perform a global pass with org_id=None
            target_orgs = [None]

    for target in target_orgs:
        res = await normalize_emails_for_org(
            graph,
            target,
            apply=apply,
            logger=logger,
            out=out,
            batch_size=batch_size,
        )
        total_stats["total_scanned"] += res["scanned"]
        total_stats["total_updated"] += res["updated"]
        total_stats["total_failed_batches"] += res["failed_batches"]

    out.write(
        json.dumps({
            "status": "completed",
            "total_orgs": len(target_orgs),
            "total_scanned": total_stats["total_scanned"],
            "total_updated": total_stats["total_updated"],
            "failed_batches": total_stats["total_failed_batches"],
            "applied": apply,
        })
        + "\n"
    )

    return EXIT_PARTIAL if total_stats["total_failed_batches"] else EXIT_SUCCESS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="normalize_user_emails",
        description=__doc__.split("\n\n")[0],
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--org", help="Target a specific organization ID")
    group.add_argument("--all-orgs", action="store_true", help="Process all organizations")

    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write updates to the database (default: dry run)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Number of records per update batch (default: {DEFAULT_BATCH_SIZE})",
    )
    return parser


async def _main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    from app.containers.indexing import IndexingAppContainer

    container = IndexingAppContainer.init("normalize_user_emails")
    logger = container.logger()
    graph = await container.graph_provider()

    try:
        return await run_normalization(
            graph,
            org_id=args.org,
            all_orgs=args.all_orgs,
            apply=args.apply,
            logger=logger,
            out=sys.stdout,
            batch_size=args.batch_size,
        )
    finally:
        await graph.disconnect()


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(sys.argv[1:])))
