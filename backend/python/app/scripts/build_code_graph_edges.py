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
    --dry-run   resolve and print the histogram without writing anything
    --force     run even while files are still queued or in progress
    --full      ignore lastEdgeBuildAt and re-resolve every file

Takes the same Redis lock as the automatic build and exits 3 if one is already
running for the repo. Requires backend/python/.env, same as the other scripts.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

from dotenv import load_dotenv

load_dotenv()

from app.config.configuration_service import ConfigurationService  # noqa: E402
from app.config.constants.arangodb import CollectionNames  # noqa: E402
from app.config.providers.encrypted_store import EncryptedKeyValueStore  # noqa: E402
from app.modules.code_graph import edge_build_trigger  # noqa: E402
from app.modules.code_graph.edge_builder import (  # noqa: E402
    build_code_graph_edges,
)
from app.modules.code_graph.facts_source import BlobCodeFactsSource  # noqa: E402
from app.modules.transformers.blob_storage import BlobStorage  # noqa: E402
from app.services.graph_db.graph_db_provider_factory import (  # noqa: E402
    GraphDBProviderFactory,
)
from app.services.graph_db.interface.graph_db_provider import (  # noqa: E402
    IGraphDBProvider,
)
from app.services.vector_db.rebuild_state import (  # noqa: E402
    redis_from_config_service,
)
from app.utils.logger import create_logger  # noqa: E402

logger = create_logger("build_code_graph_edges")


async def _stamp_last_build(provider: IGraphDBProvider, org_id: str,
                            connector_id: str, record_group_id: str, at_ms: int) -> None:
    """Advance the marker only on success -- a failed run must not skip files.

    ``at_ms`` is when this run started; a request stamped after it stays owed.
    """
    state = await edge_build_trigger.read_build_state(provider, org_id, record_group_id)
    await provider.upsert_sync_point(
        sync_point_key=edge_build_trigger.sync_point_key_for(record_group_id),
        sync_point_data={
            "orgId": org_id,
            "connectorId": connector_id,
            "syncDataPointType": "codeEdgeBuild",
            "lastEdgeBuildAt": at_ms,
            "edgeBuildPending": edge_build_trigger.still_owed(state, at_ms),
        },
        collection=CollectionNames.SYNC_POINTS.value,
    )


async def run(args: argparse.Namespace) -> int:
    # The real store, not the in-memory one the other scripts use: the facts of
    # every re-resolved file are read from blob storage, whose config lives there.
    config_service = ConfigurationService(logger, EncryptedKeyValueStore(logger))
    provider = await GraphDBProviderFactory.create_provider(logger, config_service)
    facts_source = BlobCodeFactsSource(
        BlobStorage(logger, config_service, provider), provider, logger
    )
    redis = await redis_from_config_service(config_service)
    lock: tuple[str, str] | None = None
    renewal: asyncio.Task | None = None

    try:
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

        touched: set[str] | None = None
        if not args.full:
            last_build = (await edge_build_trigger.read_build_state(
                provider, args.org_id, args.record_group_id
            )).last_build
            if last_build is not None:
                touched = await edge_build_trigger.records_updated_since(
                    provider, args.org_id, args.record_group_id, last_build
                )
                if not touched:
                    logger.info("No file changed since the last edge build; nothing to do.")
                    return 0
                logger.info("Incremental run: %d file(s) changed since last build", len(touched))
            else:
                logger.info("No previous edge build recorded; running a full build")

        result = await build_code_graph_edges(
            graph_provider=provider,
            org_id=args.org_id,
            record_group_id=args.record_group_id,
            touched_record_ids=touched,
            facts_source=facts_source,
            dry_run=args.dry_run,
            log=logger,
        )

        logger.info("code_graph_edge_build %s", json.dumps(result.as_log_fields()))
        _print_summary(result, dry_run=args.dry_run)

        if not args.dry_run:
            await _stamp_last_build(
                provider, args.org_id, args.connector_id, args.record_group_id, started_at_ms
            )
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


def _print_summary(result, *, dry_run: bool) -> None:
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resolve cross-file code edges for one repository."
    )
    parser.add_argument("--org-id", required=True)
    parser.add_argument("--connector-id", required=True)
    parser.add_argument("--record-group-id", required=True,
                        help="The repo's recordGroupId (a GitLab project's code repository).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Resolve and report without writing.")
    parser.add_argument("--force", action="store_true",
                        help="Run even while files are still indexing.")
    parser.add_argument("--full", action="store_true",
                        help="Ignore lastEdgeBuildAt and re-resolve every file.")
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
