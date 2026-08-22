"""Admin jobs: drop the records vector collection, or re-embed from blob."""

from __future__ import annotations

import asyncio
import os
from logging import Logger
from typing import Any
from uuid import uuid4

from redis.asyncio import Redis

from app.config.constants.arangodb import EventTypes, MimeTypes, ProgressStatus
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.sync.task_manager import (
    reindex_task_manager,
    sync_task_manager,
)
from app.connectors.services.kafka_service import KafkaService
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.services.messaging.config import Topic
from app.services.vector_db.rebuild_state import (
    JOB_LOCK_RENEW_INTERVAL_SECONDS,
    JOB_LOCK_TTL_SECONDS,
    PHASE_DROPPING,
    PHASE_FAILED,
    PHASE_READY,
    RebuildJobLock,
    get_cleanup_phase,
    redis_from_config_service,
    set_cleanup_phase,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms

TASK_KEY = "vector-store-rebuild"
PAGE_SIZE = 100
DEFAULT_DELETE_WAIT_SECONDS = 600
POLL_INTERVAL_SECONDS = 1.0
APP_STATUS_DELETING = "DELETING"

# Gate scan bounds: enough to look past a run of folder records without turning
# an admin request into a full table scan.
BUSY_SCAN_PAGE_SIZE = 100
BUSY_SCAN_MAX_PAGES = 20


class VectorStoreRebuildBusyError(Exception):
    """Cleanup or reindex is already running."""


def _delete_wait_seconds() -> int:
    raw = os.getenv("VECTOR_STORE_REBUILD_DELETE_WAIT_SECONDS")
    if raw is None:
        return DEFAULT_DELETE_WAIT_SECONDS
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_DELETE_WAIT_SECONDS


def _page_size() -> int:
    raw = os.getenv("VECTOR_STORE_REBUILD_PAGE_SIZE")
    if raw is None:
        return PAGE_SIZE
    try:
        return max(1, int(raw))
    except ValueError:
        return PAGE_SIZE


def _doc_key(doc: dict[str, Any] | None) -> str | None:
    if not doc:
        return None
    key = doc.get("_key") or doc.get("id")
    return str(key) if key else None


def _is_deleting(app: dict[str, Any]) -> bool:
    return app.get("status") == APP_STATUS_DELETING


async def list_rebuild_apps(graph_provider: IGraphDBProvider) -> list[tuple[str, str]]:
    """Return (org_id, connector_id) for every non-DELETING app."""
    pairs: list[tuple[str, str]] = []
    orgs = await graph_provider.get_all_orgs(active=False)
    for org in orgs or []:
        org_id = _doc_key(org)
        if not org_id:
            continue
        apps = await graph_provider.get_org_apps(org_id, active_only=False)
        for app in apps or []:
            if _is_deleting(app):
                continue
            connector_id = _doc_key(app)
            if connector_id:
                pairs.append((org_id, connector_id))
    return pairs


async def _reset_app(
    graph_provider: IGraphDBProvider,
    connector_id: str,
) -> None:
    await graph_provider.reset_indexing_status_for_connector(
        connector_id,
        ProgressStatus.NOT_STARTED.value,
        exclude_statuses=[ProgressStatus.IN_PROGRESS.value],
    )


async def _reset_apps(
    graph_provider: IGraphDBProvider,
    apps: list[tuple[str, str]],
) -> None:
    for _org_id, connector_id in apps:
        await _reset_app(graph_provider, connector_id)


class VectorStoreRebuildConflictError(Exception):
    """Indexing or sync work is still outstanding, so a rebuild would race it."""


# Records in these states have work in flight or queued behind them. Dropping the
# collection under them wipes points those runs already wrote, and they finish as
# COMPLETED with nothing indexed — a state nothing later repairs, because the
# status reset deliberately skips IN_PROGRESS.
PENDING_INDEXING_STATUSES = (
    ProgressStatus.IN_PROGRESS.value,
    ProgressStatus.QUEUED.value,
)


def _is_folder_record(record: Any) -> bool:
    """Folder records are graph scaffolding, never indexed.

    Tree-aware connectors (S3, Drive, Azure Blob) create them so children have a
    parent. They are persisted with the default QUEUED status and no indexing
    event ever follows, so they sit in QUEUED permanently — and they carry no
    embeddings at all. Counting them as outstanding work would block a rebuild
    for ever on the one category with nothing at stake.

    Mirrors the folder test in RecordEventHandler so both agree on what a folder
    is.
    """
    return getattr(record, "mime_type", None) in (
        MimeTypes.FOLDER.value,
        MimeTypes.GOOGLE_DRIVE_FOLDER.value,
    )


async def find_busy_connectors(
    graph_provider: IGraphDBProvider,
    apps: list[tuple[str, str]],
) -> list[str]:
    """Connector ids with real indexing work still queued or running.

    Pages rather than probing a single row, because folder records must be
    skipped and an unlucky first row would otherwise decide the answer. Bounded
    by BUSY_SCAN_MAX_PAGES: this is a gate, so it only needs to find *one*
    genuine record, and a connector whose first few hundred pending rows are all
    folders is not draining anything.
    """
    busy: list[str] = []
    for org_id, connector_id in apps:
        after_key: str | None = None
        for _ in range(BUSY_SCAN_MAX_PAGES):
            records = await graph_provider.get_records_by_status(
                org_id=org_id,
                connector_id=connector_id,
                status_filters=list(PENDING_INDEXING_STATUSES),
                limit=BUSY_SCAN_PAGE_SIZE,
                after_key=after_key,
            )
            if not records:
                break
            if any(not _is_folder_record(r) for r in records):
                busy.append(connector_id)
                break
            last_id = records[-1].id
            if not last_id or len(records) < BUSY_SCAN_PAGE_SIZE:
                break
            after_key = last_id
    return busy


async def assert_no_indexing_in_flight(
    graph_provider: IGraphDBProvider,
    apps: list[tuple[str, str]],
) -> None:
    """Refuse a rebuild while indexing or a sync is still in flight.

    Both jobs mutate the vector store underneath the indexing pipeline. A cleanup
    can wipe points a concurrent run just wrote; a reindex and a live index of the
    same VRID can interleave their delete-then-upsert and leave duplicates. There
    is no ordering between the two paths, so the only safe answer is to decline.
    """
    running_syncs = sync_task_manager.active_keys()
    if running_syncs:
        raise VectorStoreRebuildConflictError(
            "Connector sync is running "
            f"({', '.join(sorted(running_syncs)[:5])}). "
            "Wait for it to finish before rebuilding the vector store."
        )

    busy = await find_busy_connectors(graph_provider, apps)
    if busy:
        raise VectorStoreRebuildConflictError(
            "Records are still queued or being indexed for "
            f"{len(busy)} connector(s) ({', '.join(busy[:5])}). "
            "Wait for indexing to drain before rebuilding the vector store."
        )


async def _renew_lock_until_cancelled(
    lock: RebuildJobLock, logger: Logger
) -> None:
    """Hold the lease open while the job runs.

    The lease is deliberately short so a crashed job frees it quickly; that only
    works if a live job keeps renewing. Losing ownership means another replica
    took over, so stop renewing rather than fighting it.
    """
    while True:
        await asyncio.sleep(JOB_LOCK_RENEW_INTERVAL_SECONDS)
        try:
            if not await lock.refresh():
                logger.error(
                    "Vector-store job lost its lock; another replica may have "
                    "taken over. Stopping renewal."
                )
                return
        except Exception:
            logger.exception("Failed to renew vector-store job lock")


async def acquire_rebuild_lock(config_service: Any) -> tuple[RebuildJobLock, Redis]:
    redis = await redis_from_config_service(config_service)
    lock = RebuildJobLock(redis, ttl_seconds=JOB_LOCK_TTL_SECONDS, token=str(uuid4()))
    if not await lock.try_acquire():
        await redis.aclose()
        raise VectorStoreRebuildBusyError(
            "A vector-store cleanup or reindex job is already running"
        )
    return lock, redis


async def release_rebuild_lock(lock: RebuildJobLock, redis: Redis) -> None:
    try:
        await lock.release()
    finally:
        await redis.aclose()


async def start_vector_store_cleanup(
    *,
    logger: Logger,
    graph_provider: IGraphDBProvider,
    kafka_service: KafkaService,
    lock: RebuildJobLock,
    redis: Redis,
    org_id: str | None,
    user_id: str | None,
    apps: list[tuple[str, str]],
) -> None:
    renewal = asyncio.create_task(_renew_lock_until_cancelled(lock, logger))
    try:
        # Re-check immediately before the destructive step: the route's gate ran
        # before this job was scheduled, and a sync can start in between.
        await assert_no_indexing_in_flight(graph_provider, apps)
        await set_cleanup_phase(redis, PHASE_DROPPING)
        await _reset_apps(graph_provider, apps)
        published = await kafka_service.publish_event(
            Topic.RECORD_EVENTS.value,
            {
                "eventType": EventTypes.DELETE_VECTOR_COLLECTION.value,
                "timestamp": get_epoch_timestamp_in_ms(),
                "payload": {
                    "requestedByOrgId": org_id,
                    "requestedByUserId": user_id,
                },
            },
        )
        if not published:
            await set_cleanup_phase(redis, PHASE_FAILED)
            raise RuntimeError("Failed to publish deleteVectorCollection")
        deadline = asyncio.get_running_loop().time() + _delete_wait_seconds()
        while asyncio.get_running_loop().time() < deadline:
            phase = await get_cleanup_phase(redis)
            if phase == PHASE_READY:
                logger.info("Vector-store collection recreate acknowledged")
                return
            if phase == PHASE_FAILED:
                raise RuntimeError(
                    "Indexing could not recreate the records collection"
                )
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
        await set_cleanup_phase(redis, PHASE_FAILED)
        raise TimeoutError(
            "Timed out waiting for indexing to recreate the records collection"
        )
    except Exception:
        logger.exception("Vector-store cleanup failed")
        try:
            if await get_cleanup_phase(redis) == PHASE_DROPPING:
                await set_cleanup_phase(redis, PHASE_FAILED)
        except Exception:
            logger.exception("Failed to mark vector-store cleanup failed")
        raise
    finally:
        renewal.cancel()
        await release_rebuild_lock(lock, redis)


async def start_vector_store_reindex(
    *,
    logger: Logger,
    graph_provider: IGraphDBProvider,
    data_store_provider: Any,
    config_service: Any,
    lock: RebuildJobLock,
    redis: Redis,
    apps: list[tuple[str, str]],
) -> None:
    processor = DataSourceEntitiesProcessor(logger, data_store_provider, config_service)
    renewal = asyncio.create_task(_renew_lock_until_cancelled(lock, logger))
    try:
        await processor.initialize()
        page_size = _page_size()
        exclude_statuses = [ProgressStatus.IN_PROGRESS.value]
        for org_id, connector_id in apps:
            # Reset this app as we reach it rather than resetting every org
            # upfront: nothing consumes NOT_STARTED on its own, so a job that
            # dies partway would otherwise leave every un-published record
            # parked in that state with no path back.
            await _reset_app(graph_provider, connector_id)
            after_key: str | None = None
            while True:
                records = await graph_provider.get_records_by_status(
                    org_id=org_id,
                    connector_id=connector_id,
                    status_filters=None,
                    limit=page_size,
                    after_key=after_key,
                    exclude_statuses=exclude_statuses,
                    is_placeholder=False,
                )
                fetched_count = len(records)
                last_id = records[-1].id if records else None
                records = [r for r in records if not r.is_placeholder]
                if not records:
                    if not last_id or fetched_count < page_size:
                        break
                    after_key = last_id
                    continue
                if not last_id:
                    logger.error(
                        "Last record of vector-store reindex page has no id; "
                        "stopping connector %s",
                        connector_id,
                    )
                    break
                after_key = last_id
                await processor.reindex_existing_records(records, vector_db_only=True)
                if fetched_count < page_size:
                    break
    except Exception:
        logger.exception("Vector-store reindex failed")
        raise
    finally:
        renewal.cancel()
        await release_rebuild_lock(lock, redis)


async def schedule_vector_store_job_async(coro) -> bool:
    task = await reindex_task_manager.start_if_idle(TASK_KEY, coro)
    return task is not None
