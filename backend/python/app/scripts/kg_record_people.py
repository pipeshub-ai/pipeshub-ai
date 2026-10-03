"""Operator command: link existing records to the members they name and the
CRM accounts they belong to (KG-13).

New and updated records are linked as they sync (``record_people``,
``record_organizations``). This backfills the records synced before that,
from what their type documents and record groups already hold, with no
connector re-sync. With ``--apply`` it first stamps the tenant on CRM
accounts written before accounts recorded one. A dry run unless
``--apply``; re-running is safe, since each record's edges are replaced.

    python -m app.scripts.kg_record_people backfill --org ORG [--apply]

Output is one JSON object per line: one per page, then a total.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import TYPE_CHECKING, TextIO

from app.config.constants.arangodb import (
    RECORD_TYPE_COLLECTION_MAPPING,
    CollectionNames,
)
from app.connectors.core.base.data_processor.record_organizations import (
    link_record_organization,
)
from app.connectors.core.base.data_processor.record_people import link_record_people
from app.modules.indexing.entity_index_rebuild import EntityIndexState

if TYPE_CHECKING:
    from logging import Logger

    from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
    from app.models.entities import Record, User
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

# Every record type stored in a type collection whose records record_people
# links, so a new ticket-like type (Salesforce CASE, TASK) is not missed.
_LINKED_COLLECTIONS = {
    CollectionNames.TICKETS.value, CollectionNames.PROJECTS.value, CollectionNames.MAILS.value,
    CollectionNames.COMMENTS.value, CollectionNames.PULLREQUESTS.value, CollectionNames.DEALS.value,
}
LINKED_TYPES = sorted(t for t, c in RECORD_TYPE_COLLECTION_MAPPING.items() if c in _LINKED_COLLECTIONS)
PAGE_SIZE = 200
EXIT_PARTIAL = 1
EXIT_INVALID = 2


class _DryRunStore:
    """Real user lookups; edge writes counted, not made."""

    def __init__(self, graph: IGraphDBProvider) -> None:
        self._graph = graph
        self.edges = 0

    async def get_user_by_email(self, email: str) -> User | None:
        return await self._graph.get_user_by_email(email)

    async def get_user_by_source_id(self, source_user_id: str, connector_id: str) -> User | None:
        return await self._graph.get_user_by_source_id(source_user_id, connector_id)

    async def get_record_group_organization(self, record_group_id: str, org_id: str) -> str | None:
        return await self._graph.get_record_group_organization(record_group_id, org_id)

    async def delete_edges_between_collections(
        self, from_id: str, from_collection: str, edge_collection: str, to_collection: str,
    ) -> None:
        return None

    async def batch_create_entity_relations(self, edges: list[dict]) -> None:
        self.edges += len(edges)


async def backfill(
    graph: IGraphDBProvider,
    data_store: GraphDataStore,
    org_id: str,
    *,
    apply: bool,
    logger: Logger,
    out: TextIO,
    page_size: int = PAGE_SIZE,
) -> int:
    """Link every person-naming or account record of ``org_id``; returns the
    exit code. A failed page is reported and skipped, so a re-run finishes it."""
    after: str | None = None
    totals = {"records": 0, "edges": 0, "skipped": 0, "failed_pages": 0}
    stamped: int | None = None
    if apply:
        try:
            stamped = await graph.stamp_external_org_parents(org_id)
        except Exception as exc:  # accounts left unstamped are not linked; reported below
            out.write(json.dumps({"stamp_accounts": "failed", "error": type(exc).__name__}) + "\n")
    else:
        # Stamping is a write, so the dry run cannot count the account edges
        # of accounts it would stamp.
        out.write(json.dumps({"stamp_accounts": "skipped in a dry run; their records count no account edge"}) + "\n")
    while True:
        ids = await graph.page_record_ids_by_type(org_id, LINKED_TYPES, after_key=after, limit=page_size)
        if not ids:
            break
        after = ids[-1]
        try:
            records = list((await graph.get_typed_records_batch(ids)).values())
            if apply:
                async def _write(tx_store: object, page: list = records) -> int:
                    return sum([await _link(r, tx_store, logger) for r in page])

                edges = await data_store.execute_idempotent_in_transaction(_write)
            else:
                dry = _DryRunStore(graph)
                for record in records:
                    await _link(record, dry, logger)
                edges = dry.edges
        except Exception as exc:  # a page that fails is reported; the rest carry on
            totals["failed_pages"] += 1
            out.write(json.dumps({"after": after, "error": type(exc).__name__}) + "\n")
            continue
        # The typed read drops records it cannot rebuild; count them so a
        # partial page is not reported as complete.
        skipped = len(ids) - len(records)
        totals["records"] += len(records)
        totals["edges"] += edges
        totals["skipped"] += skipped
        out.write(json.dumps({
            "after": after, "records": len(records), "skipped": skipped, "edges": edges, "applied": apply,
        }) + "\n")
    reprojected = None
    if apply:
        # The entity index projects people in its org pass, which may have run
        # before these edges existed; clearing its state re-runs it.
        try:
            reprojected = bool(await graph.update_node(org_id, CollectionNames.ORGS.value, {
                EntityIndexState.STATE: None, EntityIndexState.TARGET: None,
            }))
        except Exception:
            reprojected = False
    out.write(json.dumps({
        "org": org_id, "total": totals, "applied": apply, "accounts_stamped": stamped,
        "entity_index_rerun": reprojected,
    }) + "\n")
    partial = totals["failed_pages"] or totals["skipped"] or reprojected is False or (apply and stamped is None)
    return EXIT_PARTIAL if partial else 0


async def _link(record: Record, store: object, logger: Logger) -> int:
    return (
        await link_record_people(record, store, logger)
        + await link_record_organization(record, store, logger)
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kg_record_people", description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("backfill")
    run.add_argument("--org", required=True)
    run.add_argument("--apply", action="store_true", help="write the edges (default: dry run)")
    return parser


async def _main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
    from app.containers.indexing import IndexingAppContainer

    container = IndexingAppContainer.init("kg_record_people")
    logger = container.logger()
    graph = await container.graph_provider()
    try:
        if args.apply:
            # The new edge types are rejected by ArangoDB's strict edge
            # schema until the current schema is applied; a dry run writes
            # nothing, schema included.
            await graph.ensure_schema()
        return await backfill(
            graph, GraphDataStore(logger, graph), args.org, apply=args.apply, logger=logger, out=sys.stdout,
        )
    except ValueError as exc:
        sys.stderr.write(json.dumps({"error": str(exc)}) + "\n")
        return EXIT_INVALID
    finally:
        await graph.disconnect()


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(sys.argv[1:])))
