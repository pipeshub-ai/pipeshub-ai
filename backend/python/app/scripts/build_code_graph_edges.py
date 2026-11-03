"""Build the code knowledge graph's cross-file edges for one repository.

Blocks are written to the graph as each file is indexed, carrying their
unresolved cross-file references. This script runs the corpus-wide pass that
turns those references into edges, once every file of a repo is indexed.

How to run
----------
    cd backend/python
    source .venv/bin/activate
    python -m app.scripts.build_code_graph_edges \
        --org-id <org> --connector-id <connector> --record-group-id <repo>

Options
    --dry-run   resolve and print the histogram without writing anything (takes no lock)
    --force     run even while files are still queued or in progress
    --full      ignore lastEdgeBuildAt and re-resolve every file
    --list      list every repo's graph state (built, owed, retrying, failed...) and exit
    --explain   explain one repo's state and what a build would do now, and exit

    python -m app.scripts.build_code_graph_edges --list [--org-id <org>]
    python -m app.scripts.build_code_graph_edges --explain --org-id <org> --record-group-id <repo>

A build takes the same Redis lock as the automatic build and exits 3 if one is
already running for the repo. Requires backend/python/.env, same as the other scripts.
"""
# ruff: noqa: T201 - a CLI script; print is its output
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

from dotenv import load_dotenv

load_dotenv()

from app.config.configuration_service import ConfigurationService  # noqa: E402
from app.config.providers.encrypted_store import EncryptedKeyValueStore  # noqa: E402
from app.modules.code_graph import deleted_files, edge_build_trigger  # noqa: E402
from app.modules.code_graph.build_state import (  # noqa: E402
    BuildStateStore,
    graph_status,
    needs_full_build,
)
from app.modules.code_graph.edge_builder import (  # noqa: E402
    EdgeBuildResult,
    build_code_graph_edges,
)
from app.modules.code_graph.facts_source import BlobCodeFactsSource  # noqa: E402
from app.modules.transformers.blob_storage import BlobStorage  # noqa: E402
from app.services.graph_db.graph_db_provider_factory import (  # noqa: E402
    GraphDBProviderFactory,
)
from app.services.vector_db.rebuild_state import (  # noqa: E402
    redis_from_config_service,
)
from app.utils.logger import create_logger  # noqa: E402

logger = create_logger("build_code_graph_edges")


async def run(args: argparse.Namespace) -> int:
    # The real store, not the in-memory one the other scripts use: the facts of
    # every re-resolved file are read from blob storage, whose config lives there.
    config_service = ConfigurationService(logger, EncryptedKeyValueStore(logger))
    provider = await GraphDBProviderFactory.create_provider(logger, config_service)
    if args.list:
        return await _list(provider, args.org_id)
    if args.explain:
        return await _explain(provider, args.org_id, args.record_group_id)
    facts_source = BlobCodeFactsSource(
        BlobStorage(logger, config_service, provider), provider, logger
    )
    redis = await redis_from_config_service(config_service)
    lock: tuple[str, str] | None = None
    renewal: asyncio.Task | None = None

    try:
        # A dry run writes nothing, so it neither needs nor blocks the lock.
        if not args.dry_run:
            lock = await edge_build_trigger.acquire_build_lock(
                redis, args.org_id, args.record_group_id
            )
            if lock is None:
                logger.error(
                    "Another code edge build already holds the lock for this repo; "
                    "wait for it to finish and run again."
                )
                return 3
            renewal = asyncio.create_task(
                edge_build_trigger.renew_build_lock_until_cancelled(
                    redis, lock[0], lock[1], logger
                )
            )

        if await edge_build_trigger.group_has_unfinished_records(
            provider, args.org_id, args.record_group_id
        ):
            if not args.force:
                logger.error(
                    "Files in this repo are still NOT_STARTED/QUEUED/IN_PROGRESS. "
                    "Resolving now would build edges against an incomplete symbol table. "
                    "Wait for indexing to drain, or pass --force."
                )
                return 2
            logger.warning("Proceeding while files are still indexing because --force was given")

        # Read before the watermark query, as the automatic build does: a record
        # updated in between would otherwise sit below the next run's `since`.
        started_at_ms = int(time.time() * 1000)
        store = BuildStateStore(provider, logger)
        # A dry run writes nothing, its own state included.
        start_state = (
            await store.read_or_empty(args.org_id, args.record_group_id) if args.dry_run
            else await store.begin(args.org_id, args.connector_id, args.record_group_id, lock[1])
        )

        deleted = await deleted_files.pending(provider, args.org_id, args.record_group_id)
        touched: set[str] | None = None
        if not args.full:
            last_build = start_state.last_build
            if not needs_full_build(start_state):
                touched = await edge_build_trigger.records_updated_since(
                    provider, args.org_id, args.record_group_id, last_build
                )
                if not touched and not deleted.keys:
                    logger.info("No file changed since the last edge build; nothing to do.")
                    if not args.dry_run:
                        await store.settle_built(
                            args.org_id, args.record_group_id, lease_token=lock[1],
                            start=start_state, started_at_ms=started_at_ms,
                            advance_watermark=False, full=False,
                        )
                    return 0
                logger.info("Incremental run: %d file(s) changed and %d deleted since last build",
                            len(touched), len(deleted.keys))
            else:
                logger.info("Full build: owed, never built, or built by another code graph version")

        result = await build_code_graph_edges(
            graph_provider=provider,
            org_id=args.org_id,
            record_group_id=args.record_group_id,
            touched_record_ids=touched,
            deleted_names=deleted.names,
            facts_source=facts_source,
            dry_run=args.dry_run,
            log=logger,
            fence=(lambda: edge_build_trigger.assert_lease_held(redis, *lock)) if lock else None,
        )

        logger.info("code_graph_edge_build %s", json.dumps(result.as_log_fields()))
        _print_summary(result, dry_run=args.dry_run)

        if not args.dry_run:
            if result.facts_missing:
                logger.warning(
                    "%d file(s) had no readable facts and kept their old edges; the "
                    "build marker is not advanced so the next run retries them",
                    result.facts_missing,
                )
            # Advance the marker only on success -- a failed run must not skip files.
            settled = await store.settle_built(
                args.org_id, args.record_group_id, lease_token=lock[1], start=start_state,
                started_at_ms=started_at_ms, advance_watermark=not result.facts_missing,
                full=touched is None, result=result,
            )
            if settled and not result.facts_missing:
                await deleted_files.forget(provider, deleted.keys, logger)
        return 0
    finally:
        if renewal is not None:
            renewal.cancel()
            await asyncio.wait({renewal})
        if lock is not None:
            try:
                await edge_build_trigger.release_build_lock(redis, lock[0], lock[1])
            except Exception:
                logger.exception("Failed to release code edge build lock %s", lock[0])
        await redis.aclose()
        disconnect = getattr(provider, "disconnect", None)
        if disconnect:
            await disconnect()


def _print_summary(result: EdgeBuildResult, *, dry_run: bool) -> None:
    header = "DRY RUN — nothing written" if dry_run else "Edge build complete"
    print(f"\n{header}")
    print(f"  files (total/touched/re-resolved): "
          f"{result.files_total}/{result.files_touched}/{result.files_reresolved}")
    print(f"  blocks scanned                   : {result.blocks}")
    print(f"  pending edges considered         : {result.pending_edges_seen}")
    print(f"  edges written                    : {result.edges_written}")
    print(f"  edges removed                    : {result.edges_removed}")
    print(f"  external refs (3rd-party/stdlib) : {result.external_refs}")
    print(f"  unresolved / ambiguous-skipped   : {result.unresolved} / {result.ambiguous_skipped}")
    print(f"  duration                         : {result.duration_s:.2f}s")
    if result.edges_by_type:
        print("  by relation:")
        for relation, count in sorted(result.edges_by_type.items(), key=lambda kv: -kv[1]):
            print(f"    {relation:16} {count}")


async def _list(provider: object, org_id: str | None) -> int:
    """Every repo's graph state, one line each: what is built, owed, failing."""
    builds = await BuildStateStore(provider).all()
    rows = [b for b in builds if not org_id or b.org_id == org_id]
    print(f"{'record group':40} {'org':24} {'status':20} {'last build':>14} {'tries':>5}  error")
    for b in sorted(rows, key=lambda b: (b.org_id, b.record_group_id)):
        s = b.state
        print(f"{b.record_group_id:40} {b.org_id:24} {graph_status(s).value:20} "
              f"{s.last_build or '-':>14} {s.attempts:>5}  {s.last_error or ''}")
    print(f"\n{len(rows)} repo(s)")
    return 0


async def _explain(provider: object, org_id: str, record_group_id: str) -> int:
    """Why one repo's graph is in the state it is in, and what a build would do now."""
    state = await BuildStateStore(provider).read(org_id, record_group_id)
    if state is None:
        print("No build was ever asked for this repo.")
        return 0
    for field, value in state._asdict().items():
        print(f"  {field:16} {value}")
    print(f"  {'status':16} {graph_status(state).value}")
    if await edge_build_trigger.group_has_unfinished_records(provider, org_id, record_group_id):
        print("A build now would wait: files in this repo are still indexing.")
    elif needs_full_build(state):
        print("A build now would be full (a full build is owed, the repo was never built, "
              "or it was last built by another code graph version).")
    else:
        touched = await edge_build_trigger.records_updated_since(
            provider, org_id, record_group_id, state.last_build or 0)
        print(f"A build now would re-check {len(touched)} changed file(s) and what depends on them.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resolve cross-file code edges for one repository, or report on them."
    )
    parser.add_argument("--org-id")
    parser.add_argument("--connector-id")
    parser.add_argument("--record-group-id",
                        help="The repo's recordGroupId (a GitLab project's code repository).")
    parser.add_argument("--list", action="store_true",
                        help="List every repo's graph state (optionally for --org-id) and exit.")
    parser.add_argument("--explain", action="store_true",
                        help="Explain one repo's graph state and what a build would do, and exit.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Resolve and report without writing.")
    parser.add_argument("--force", action="store_true",
                        help="Run even while files are still indexing.")
    parser.add_argument("--full", action="store_true",
                        help="Ignore lastEdgeBuildAt and re-resolve every file.")
    args = parser.parse_args()
    if not args.list:
        needed = ["org_id", "record_group_id"] + ([] if args.explain else ["connector_id"])
        missing = [f"--{n.replace('_', '-')}" for n in needed if not getattr(args, n)]
        if missing:
            parser.error(f"{', '.join(missing)} required")
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
