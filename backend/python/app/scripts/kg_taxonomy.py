"""Operator command for taxonomy consolidation (KG-33) and legacy-node
migration (B7). Every command is a dry run unless ``--apply`` is given, and
every write can be undone (``unmerge``, ``unmigrate-legacy``).

    python -m app.scripts.kg_taxonomy duplicates --org ORG [--collection topics]
    python -m app.scripts.kg_taxonomy consolidate --org ORG [--collection topics] [--apply]
    python -m app.scripts.kg_taxonomy merge --org ORG --collection topics --winner W --loser L [--apply]
    python -m app.scripts.kg_taxonomy unmerge --org ORG --collection topics --loser L [--apply]
    python -m app.scripts.kg_taxonomy legacy --org ORG [--collection topics]
    python -m app.scripts.kg_taxonomy migrate-legacy --org ORG [--collection topics] [--apply]
    python -m app.scripts.kg_taxonomy unmigrate-legacy --org ORG --collection topics \\
        --legacy L --target T [--apply]

Without ``--collection``, the listing and bulk commands cover every taxonomy
collection. Output is one JSON object per line.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import TYPE_CHECKING, TextIO, TypeVar

from app.services.graph_db.taxonomy import TAXONOMY_COLLECTIONS

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from logging import Logger

    from app.modules.entity_resolution.consolidation import TaxonomyConsolidator
    from app.modules.transformers.entity_vectorstore import EntityVectorStore
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

_COLLECTIONS = sorted(TAXONOMY_COLLECTIONS)
_T = TypeVar("_T")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kg_taxonomy",
        description="Merge duplicate taxonomy nodes and migrate legacy ones; dry run unless --apply.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    def command(name: str, *, needs_collection: bool = False, writes: bool = False) -> argparse.ArgumentParser:
        sub = commands.add_parser(name)
        sub.add_argument("--org", required=True)
        sub.add_argument("--collection", choices=_COLLECTIONS, required=needs_collection)
        if writes:
            sub.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
        return sub

    command("duplicates")
    command("consolidate", writes=True)
    merge = command("merge", needs_collection=True, writes=True)
    merge.add_argument("--winner", required=True)
    merge.add_argument("--loser", required=True)
    command("unmerge", needs_collection=True, writes=True).add_argument("--loser", required=True)
    command("legacy")
    command("migrate-legacy", writes=True)
    undo = command("unmigrate-legacy", needs_collection=True, writes=True)
    undo.add_argument("--legacy", required=True)
    undo.add_argument("--target", required=True)
    return parser


# Exit codes: 0 all done, 1 some items failed or left the index unrefreshed,
# 2 the command itself was invalid, 3 a single-item command failed or the
# graph or its schema was unavailable.
EXIT_PARTIAL = 1
EXIT_INVALID = 2
EXIT_FAILED = 3


async def run(args: argparse.Namespace, consolidator: TaxonomyConsolidator, out: TextIO) -> int:
    """Execute one parsed command; returns the process exit code.

    Bulk commands carry on past a failed item, report it as a JSON line with
    ``error``, and exit 1; each item is idempotent, so a re-run finishes it.
    """
    failures = 0

    def emit(**fields: object) -> None:
        out.write(json.dumps(fields, sort_keys=True, default=str) + "\n")

    async def step(action: str, coro: Awaitable[_T], **fields: object) -> _T | None:
        nonlocal failures
        try:
            return await coro
        except Exception as exc:
            failures += 1
            emit(action=action, error=f"{type(exc).__name__}: {exc}", **fields)
            return None

    def report(action: str, result: object, **fields: object) -> None:
        nonlocal failures
        if result is None:
            return
        if getattr(result, "index_refreshed", True) is False:
            failures += 1
        emit(action=action, **fields)

    collections = [args.collection] if args.collection else _COLLECTIONS
    dry_run = not getattr(args, "apply", False)
    org = args.org

    if args.command in ("duplicates", "consolidate"):
        for collection in collections:
            groups = await step("duplicates", consolidator.duplicate_groups(collection, org), collection=collection)
            for group in groups or []:
                for loser in group.losers:
                    if args.command == "duplicates":
                        emit(collection=collection, winner=group.winner.key, winner_name=group.winner.name,
                             loser=loser.key, loser_name=loser.name)
                        continue
                    where = {"collection": collection, "winner": group.winner.key, "loser": loser.key}
                    result = await step(
                        "merge", consolidator.merge(collection, org, group.winner.key, loser.key, dry_run=dry_run),
                        **where,
                    )
                    if result is not None:
                        report("merge", result, edges=result.edges_moved, dry_run=result.dry_run,
                               index_refreshed=result.index_refreshed, **where)
    elif args.command == "merge":
        # Single-item commands let a failure through, so the process exits 2
        # (invalid request) or 3; only bulk commands carry on past an item.
        where = {"collection": args.collection, "winner": args.winner, "loser": args.loser}
        result = await consolidator.merge(args.collection, org, args.winner, args.loser, dry_run=dry_run)
        report("merge", result, edges=result.edges_moved, dry_run=result.dry_run,
               index_refreshed=result.index_refreshed, **where)
    elif args.command == "unmerge":
        where = {"collection": args.collection, "loser": args.loser}
        undone = await consolidator.unmerge(args.collection, org, args.loser, dry_run=dry_run)
        report("unmerge", undone, edges=undone.edges_moved, dry_run=undone.dry_run,
               index_refreshed=undone.index_refreshed, **where)
    elif args.command in ("legacy", "migrate-legacy"):
        for collection in collections:
            nodes = await step("legacy", consolidator.legacy_nodes(collection, org), collection=collection)
            for node in nodes or []:
                if args.command == "legacy":
                    emit(collection=collection, legacy=node.key, name=node.name, records=node.records)
                    continue
                where = {"collection": collection, "legacy": node.key}
                result = await step(
                    "migrate-legacy", consolidator.migrate_legacy(collection, org, node.key, dry_run=dry_run), **where,
                )
                if result is not None:
                    report("migrate-legacy", result, target=result.target_key, edges=result.edges_moved,
                           dry_run=result.dry_run, skipped=result.skipped_reason,
                           index_refreshed=result.index_refreshed, **where)
    elif args.command == "unmigrate-legacy":
        where = {"collection": args.collection, "legacy": args.legacy, "target": args.target}
        restored = await consolidator.unmigrate_legacy(
            args.collection, org, args.legacy, args.target, dry_run=dry_run,
        )
        report("unmigrate-legacy", restored, edges=restored.edges_moved, dry_run=restored.dry_run,
               index_refreshed=restored.index_refreshed, **where)
    else:
        raise ValueError(f"unknown command {args.command!r}")
    return EXIT_PARTIAL if failures else 0


async def execute(
    args: argparse.Namespace,
    graph_provider: IGraphDBProvider,
    make_consolidator: Callable[[EntityVectorStore | None], TaxonomyConsolidator],
    open_store: Callable[[], Awaitable[EntityVectorStore | None]],
    logger: Logger,
    out: TextIO,
    err: TextIO,
) -> int:
    """Run ``args`` against an open graph and disconnect it; returns the
    exit code."""
    try:
        # Merges write mergedFrom on edges; ArangoDB's strict edge schema
        # rejects it until the current schema is applied. A dry run only
        # reads, so it works with a read-only graph user.
        if getattr(args, "apply", False) and await graph_provider.ensure_schema() is False:
            err.write(json.dumps({"error": "graph schema could not be applied; nothing was written"}) + "\n")
            return EXIT_FAILED
        try:
            entity_store = await open_store()
        except Exception:
            logger.warning("kg_taxonomy: entity store unavailable; changes are reported as index not refreshed")
            entity_store = None
        return await run(args, make_consolidator(entity_store), out)
    except ValueError as exc:
        err.write(json.dumps({"error": str(exc)}) + "\n")
        return EXIT_INVALID
    except Exception as exc:
        logger.exception("kg_taxonomy: %s failed", args.command)
        err.write(json.dumps({"error": f"{type(exc).__name__}: {exc}"}) + "\n")
        return EXIT_FAILED
    finally:
        await graph_provider.disconnect()


async def _main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    from app.containers.indexing import IndexingAppContainer
    from app.modules.entity_resolution.consolidation import TaxonomyConsolidator

    container = IndexingAppContainer.init("kg_taxonomy")
    logger = container.logger()
    try:
        graph_provider = await container.graph_provider()
    except Exception as exc:
        logger.exception("kg_taxonomy: graph unavailable")
        sys.stderr.write(json.dumps({"error": f"{type(exc).__name__}: {exc}"}) + "\n")
        return EXIT_FAILED
    return await execute(
        args, graph_provider,
        lambda store: TaxonomyConsolidator(graph_provider=graph_provider, entity_store=store, logger=logger),
        container.entity_vector_store, logger, sys.stdout, sys.stderr,
    )


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(sys.argv[1:])))
