"""Operator command: remove the record processing diagnostics fields before going back to an older release.

Records carry four optional internal fields (``firstQueuedAt``, ``failedAt``, ``indexingAttempts``, ``pageCount``).
An image older than the release that added them has a record schema that does not list them, and on ArangoDB a
record that still holds one is refused on every write. Rolling back to the release that accepts the fields needs
nothing. Rolling back below it needs this:

1. stop every service that runs the release that writes the fields (or any later one);
2. run ``--apply`` for each organisation;
3. start the older image.

    python -m app.scripts.record_diagnostics_contract --org ORG --check
    python -m app.scripts.record_diagnostics_contract --org ORG --apply

``--check`` counts the records that carry a field. ``--apply`` removes the fields in batches, can be stopped and
run again, and ends with a check: it exits 1 when any record still carries one.
Output is one JSON object per line.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import TYPE_CHECKING, Any, TextIO

from app.config.constants.arangodb import CollectionNames
from app.modules.indexing.record_diagnostics import ALL_FIELDS

if TYPE_CHECKING:
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

BATCH_SIZE = 10_000
EXIT_LEFT_OVER = 1
EXIT_INVALID = 2


def _store_of(graph: object) -> str:
    module = type(graph).__module__
    for name in ("arango", "neo4j", "postgres"):
        if name in module:
            return name
    raise ValueError(f"unsupported graph provider {type(graph).__name__}")


async def _arango(graph: Any, org: str, limit: int | None) -> int:  # noqa: ANN401
    has_any = " OR ".join(f"HAS(r, '{name}')" for name in ALL_FIELDS)
    if limit is None:
        rows = await graph.http_client.execute_aql(
            f"RETURN LENGTH(FOR r IN @@c FILTER r.orgId == @org AND ({has_any}) RETURN 1)",
            {"@c": CollectionNames.RECORDS.value, "org": org},
        )
        return int(rows[0])
    cleared = ", ".join(f"{name}: null" for name in ALL_FIELDS)
    rows = await graph.http_client.execute_aql(
        f"FOR r IN @@c FILTER r.orgId == @org AND ({has_any}) LIMIT @n "
        f"UPDATE r WITH {{ {cleared} }} IN @@c OPTIONS {{ keepNull: false }} RETURN 1",
        {"@c": CollectionNames.RECORDS.value, "org": org, "n": limit},
    )
    return len(rows)


async def _neo4j(graph: Any, org: str, limit: int | None) -> int:  # noqa: ANN401
    from app.services.graph_db.neo4j.neo4j_provider import collection_to_label

    label = collection_to_label(CollectionNames.RECORDS.value)
    has_any = " OR ".join(f"r.{name} IS NOT NULL" for name in ALL_FIELDS)
    if limit is None:
        rows = await graph.client.execute_query(
            f"MATCH (r:{label}) WHERE r.orgId = $org AND ({has_any}) RETURN count(r) AS n", parameters={"org": org},
        )
        return int(rows[0]["n"])
    removed = ", ".join(f"r.{name}" for name in ALL_FIELDS)
    rows = await graph.client.execute_query(
        f"MATCH (r:{label}) WHERE r.orgId = $org AND ({has_any}) WITH r LIMIT $n REMOVE {removed} RETURN count(r) AS n",
        parameters={"org": org, "n": limit},
    )
    return int(rows[0]["n"]) if rows else 0


async def _postgres(graph: Any, org: str, limit: int | None) -> int:  # noqa: ANN401
    names = list(ALL_FIELDS)
    if limit is None:
        return int(await graph._q(lambda tx: tx.fetchval(
            "/* pipeshub:graph.record_diagnostics_contract_check */ SELECT count(*) FROM graph.nodes "
            "WHERE org_id = $1 AND coll = 'records' AND doc ?| $2::text[]", org, names), None) or 0)
    return int(await graph._q(lambda tx: tx.fetchval(
        "/* pipeshub:graph.record_diagnostics_contract_apply */ WITH u AS (UPDATE graph.nodes SET doc = doc - $2::text[] "
        "WHERE org_id = $1 AND coll = 'records' AND id IN (SELECT id FROM graph.nodes WHERE org_id = $1 AND coll = 'records' "
        "AND doc ?| $2::text[] LIMIT $3) RETURNING 1) SELECT count(*) FROM u", org, names, limit), None, write=True) or 0)


_STORES = {"arango": _arango, "neo4j": _neo4j, "postgres": _postgres}


async def check(graph: IGraphDBProvider, org: str) -> int:
    """How many records of ``org`` carry any of the fields (JSON null and a non-number included)."""
    return await _STORES[_store_of(graph)](graph, org, None)


async def apply(graph: IGraphDBProvider, org: str, *, batch_size: int = BATCH_SIZE, out: TextIO = sys.stdout) -> int:
    """Remove the fields from every record of ``org``, ``batch_size`` records at a time; returns the records left."""
    remove = _STORES[_store_of(graph)]
    total = 0
    remaining = await check(graph, org)
    while remaining:
        removed = await remove(graph, org, batch_size)
        total += removed
        out.write(json.dumps({"removed": removed, "total": total}) + "\n")
        now = await check(graph, org)
        if now >= remaining:
            # a batch that takes nothing away would be run for ever (a store that keeps the keys it was told to clear)
            out.write(json.dumps({"stalled": True, "left": now}) + "\n")
            return now
        remaining = now
        if removed < batch_size:
            break
    left = await check(graph, org)
    out.write(json.dumps({"left": left}) + "\n")
    return left


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="record_diagnostics_contract", description=__doc__.split("\n\n")[0])
    parser.add_argument("--org", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="count the records that carry a field")
    mode.add_argument("--apply", action="store_true", help="remove the fields")
    return parser


async def _main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    from app.containers.indexing import IndexingAppContainer
    from app.core.org_context import for_org

    container = IndexingAppContainer.init("record_diagnostics_contract")
    graph = await container.graph_provider()
    try:
        # The graph runs every statement under one org (PostgreSQL refuses an unbound one).
        with for_org(args.org, job="record_diagnostics_contract"):
            if args.check:
                count = await check(graph, args.org)
                sys.stdout.write(json.dumps({"carrying": count}) + "\n")
                return 0 if count == 0 else EXIT_LEFT_OVER
            return EXIT_LEFT_OVER if await apply(graph, args.org) else 0
    except ValueError as exc:
        sys.stderr.write(json.dumps({"error": str(exc)}) + "\n")
        return EXIT_INVALID
    finally:
        await graph.disconnect()


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(sys.argv[1:])))
