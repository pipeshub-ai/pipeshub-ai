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

Requires backend/python/.env, same as the other scripts here.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Any

from dotenv import load_dotenv

load_dotenv()

from app.config.configuration_service import ConfigurationService  # noqa: E402
from app.config.constants.arangodb import (  # noqa: E402
    CollectionNames,
    ProgressStatus,
)
from app.config.providers.in_memory_store import InMemoryKeyValueStore  # noqa: E402
from app.modules.code_graph.edge_builder import (  # noqa: E402
    build_code_graph_edges,
)
from app.services.graph_db.graph_db_provider_factory import (  # noqa: E402
    GraphDBProviderFactory,
)
from app.services.graph_db.interface.graph_db_provider import (  # noqa: E402
    IGraphDBProvider,
)
from app.utils.logger import create_logger  # noqa: E402

logger = create_logger("build_code_graph_edges")

# A file still being indexed means an incomplete symbol table, and an incomplete
# symbol table produces wrong edges rather than missing ones.
_BLOCKING_STATUSES = (
    ProgressStatus.NOT_STARTED.value,
    ProgressStatus.QUEUED.value,
    ProgressStatus.IN_PROGRESS.value,
)

_SYNC_POINT_KEY_SUFFIX = "code-edge-build"


class _KeyValueStore(InMemoryKeyValueStore):
    @property
    def client(self) -> Any:  # pragma: no cover - mirrors the other scripts
        return None


async def _count_unfinished(provider: IGraphDBProvider, org_id: str,
                            record_group_id: str) -> int:
    return await provider.count_nodes_by_filters(
        collection=CollectionNames.RECORDS.value,
        filters={"orgId": org_id, "recordGroupId": record_group_id},
        in_filters={"indexingStatus": list(_BLOCKING_STATUSES)},
    )


async def _load_last_build(provider: IGraphDBProvider, org_id: str,
                           record_group_id: str) -> int | None:
    docs = await provider.get_nodes_by_filters(
        collection=CollectionNames.SYNC_POINTS.value,
        filters={"orgId": org_id, "syncPointKey": f"{record_group_id}/{_SYNC_POINT_KEY_SUFFIX}"},
        return_fields=["lastEdgeBuildAt"],
    )
    if not docs:
        return None
    val = docs[0].get("lastEdgeBuildAt")
    return val if isinstance(val, (int, float)) else None


async def _stamp_last_build(provider: IGraphDBProvider, org_id: str,
                            connector_id: str, record_group_id: str, at_ms: int) -> None:
    """Advance the marker only on success -- a failed run must not skip files."""
    key = f"{record_group_id}/{_SYNC_POINT_KEY_SUFFIX}"
    await provider.upsert_sync_point(
        sync_point_key=key,
        sync_point_data={
            "orgId": org_id,
            "connectorId": connector_id,
            "syncDataPointType": "codeEdgeBuild",
            "lastEdgeBuildAt": at_ms,
        },
        collection=CollectionNames.SYNC_POINTS.value,
    )


async def _touched_records(provider: IGraphDBProvider, org_id: str,
                           record_group_id: str, since_ms: int) -> set[str]:
    rows = await provider.get_nodes_updated_since(
        collection=CollectionNames.RECORDS.value,
        timestamp_field="updatedAtTimestamp",
        since=since_ms,
        filters={"orgId": org_id, "recordGroupId": record_group_id},
        return_fields=["_key"],
    )
    return {
        row["_key"]
        for row in rows
        if isinstance(row.get("_key"), str)
    }


async def run(args: argparse.Namespace) -> int:
    key_value_store = _KeyValueStore(logger, "app/config/default_config.json")
    config_service = ConfigurationService(logger, key_value_store)
    provider = await GraphDBProviderFactory.create_provider(logger, config_service)

    try:
        unfinished = await _count_unfinished(provider, args.org_id, args.record_group_id)
        if unfinished and not args.force:
            logger.error(
                "%d file(s) in this repo are still NOT_STARTED/QUEUED/IN_PROGRESS. "
                "Resolving now would build edges against an incomplete symbol table. "
                "Wait for indexing to drain, or pass --force.",
                unfinished,
            )
            return 2
        if unfinished:
            logger.warning("Proceeding with %d unfinished file(s) because --force was given", unfinished)

        touched: set[str] | None = None
        if not args.full:
            last_build = await _load_last_build(provider, args.org_id, args.record_group_id)
            if last_build:
                touched = await _touched_records(
                    provider, args.org_id, args.record_group_id, int(last_build)
                )
                if not touched:
                    logger.info("No file changed since the last edge build; nothing to do.")
                    return 0
                logger.info("Incremental run: %d file(s) changed since last build", len(touched))
            else:
                logger.info("No previous edge build recorded; running a full build")

        started_at_ms = int(time.time() * 1000)
        result = await build_code_graph_edges(
            graph_provider=provider,
            org_id=args.org_id,
            record_group_id=args.record_group_id,
            touched_record_ids=touched,
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
