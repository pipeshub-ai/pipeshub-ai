import asyncio
import inspect
import logging
import os
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from typing import TYPE_CHECKING, Any, Protocol, TypeVar, cast
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

import app.utils.runtime_threads  # noqa: E402 - must precede all ML library imports
from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    EventTypes,
    OriginTypes,
    ProgressStatus,
)
from app.containers.indexing import initialize_container
from app.edition_containers import IndexingAppContainer
from app.modules.indexing.vector_membership_backfill import (
    run_vector_membership_backfill_loop,
)
from app.modules.parsers.pdf.docling_processor import (
    set_resource_governor as set_docling_processor_governor,
)
from app.modules.parsers.pdf.pdf_rasterizer import (
    set_resource_governor as set_pdf_rasterizer_governor,
)
from app.services.distributed.interface import IDistributedLeaseManager
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.services.llm_gateway.gateway import get_llm_gateway
from app.services.messaging.backpressure import (
    get_default_backpressure_coordinator,
)
from app.services.messaging.config import (
    ConsumerType,
    Topic,
    get_message_broker_type,
    messaging_env,
)
from app.services.messaging.consumer_concurrency import StageAdmission
from app.services.messaging.distributed_concurrency import (
    DistributedConcurrencyManager,
)
from app.services.messaging.interface.admin import IMessageAdmin
from app.services.messaging.kafka.utils.utils import KafkaUtils
from app.services.messaging.messaging_factory import MessagingFactory
from app.services.messaging.utils import MessagingUtils
from app.services.messaging.worker_loop import WorkerLoop
from app.services.resource_governor import ResourceGovernor
from app.telemetry.setup import setup_telemetry
from app.utils.llm import is_local_cpu_embedding_configured
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from app.modules.pipeline.runtime import PipelineRuntime

_T = TypeVar("_T")


class CoordinationRunner(Protocol):
    def __call__(self, coro: Awaitable[_T]) -> Awaitable[_T]: ...

# def handle_sigterm(signum, frame) -> None:
#     print(f"Received signal {signum}, {frame} shutting down gracefully")
#     sys.exit(0)

# signal.signal(signal.SIGTERM, handle_sigterm)
# signal.signal(signal.SIGINT, handle_sigterm)

container = IndexingAppContainer.init("indexing_service")
container_lock = asyncio.Lock()


async def get_initialized_container() -> IndexingAppContainer:
    """Dependency provider for initialized container"""
    if not hasattr(get_initialized_container, "initialized"):
        async with container_lock:
            if not hasattr(
                get_initialized_container, "initialized"
            ):  # Double-check inside lock
                await initialize_container(container)
                container.wire(modules=["app.modules.retrieval.retrieval_service"])
                setattr(get_initialized_container, "initialized", True)
    return container

async def recover_in_progress_records(
    app_container: IndexingAppContainer,
    graph_provider: IGraphDBProvider,
    concurrency_manager: DistributedConcurrencyManager | None = None,
    coordination_runner: CoordinationRunner | None = None,
) -> None:
    """
    Recover records left in IN_PROGRESS after a crash/restart.

    Recovery is intentionally lightweight: it republishes each abandoned
    record and resets it to QUEUED under a per-record lease, then lets the
    normal consumer flow (parsing/indexing semaphores, backpressure, retry
    classification, circuit breaker) process it exactly like any other
    incoming record.

    This deliberately does NOT invoke the indexing pipeline inline: doing so
    would bypass the parsing/indexing semaphores and backpressure entirely —
    doubling load on the exact resources a just-restarted, possibly
    resource-constrained instance can least afford — and can race with a
    re-queued Kafka message for the same record that the live consumer is
    processing concurrently, causing double-processing. Records to recover
    are processed in parallel (5 at a time).
    """
    logger = app_container.logger()
    logger.debug("Checking for in-progress records to recover")

    # Semaphore to limit concurrent processing to 5 records
    semaphore = asyncio.Semaphore(5)
    # Track results for final summary
    results = {"requeued": 0, "skipped": 0, "error": 0}
    total_records = 0
    recovery_owner = f"recovery:{uuid4().hex}"
    recovery_renewal_task: asyncio.Task[None] | None = None
    recovery_lock_held = False
    # Shared by both the single coordination lock ("recovery") and each
    # per-record lock ("record:<id>") acquired below — they don't need
    # different values, just distinct pool keys.
    recovery_lease_seconds = max(30.0, messaging_env.concurrency_lease_seconds)

    async def run_coordination(coro: Awaitable[_T]) -> _T:
        if coordination_runner is not None:
            return await coordination_runner(coro)
        return await coro

    try:
        # Reuse the retry producer set up in start_kafka_consumers (available on the
        # container by the time recovery runs) so recovery events go out on the
        # same producer as live retries.
        retry_producer = None
        consumers = getattr(app_container, "kafka_consumers", [])
        if consumers and len(consumers[0]) > 2:
            retry_producer = consumers[0][2]
            record_consumer = consumers[0][1]
            if concurrency_manager is None:
                concurrency_manager = getattr(
                    record_consumer, "concurrency_manager", None
                )
            if coordination_runner is None:
                coordination_runner = getattr(
                    record_consumer, "_run_on_main_loop", None
                )

        if retry_producer is None:
            logger.warning(
                "⚠️ No producer available; stale-record recovery is deferred"
            )
            return

        if concurrency_manager is not None:
            recovery_lock_held = await concurrency_manager.try_acquire(
                "recovery",
                recovery_owner,
                1,
                recovery_lease_seconds,
            )
            if not recovery_lock_held:
                logger.debug(
                    "Another indexing replica is running stale-record recovery"
                )
                return

            async def renew_recovery_lock() -> None:
                while True:
                    await asyncio.sleep(min(30.0, recovery_lease_seconds / 3))
                    renewed = await concurrency_manager.renew(
                        "recovery",
                        recovery_owner,
                        recovery_lease_seconds,
                    )
                    if not renewed:
                        raise RuntimeError("Lost stale-record recovery lease")

            recovery_renewal_task = asyncio.create_task(renew_recovery_lock())

        async def update_recovery_status(
            record_id: str,
            fields: dict[str, Any],
        ) -> None:
            updated = await graph_provider.update_node(
                record_id,
                CollectionNames.RECORDS.value,
                fields,
            )
            if not updated:
                raise RuntimeError(
                    f"Failed to persist recovery status for record {record_id}"
                )

        async def process_single_record(record: dict[str, Any]) -> bool | None:
            """Reset one stuck record and re-queue it, with semaphore control."""
            async with semaphore:
                record_id = record.get("_key")
                record_name = record.get("recordName", "Unknown")
                reset_for_requeue = False
                published = False
                record_lock_held = False
                record_pool: str | None = None
                record_owner = f"{recovery_owner}:record:{uuid4().hex}"
                try:
                    if not record_id:
                        raise ValueError("Cannot recover a record without _key")

                    if concurrency_manager is not None:
                        record_pool = f"record:{record_id}"
                        record_lock_held = await concurrency_manager.try_acquire(
                            record_pool,
                            record_owner,
                            1,
                            recovery_lease_seconds,
                        )
                        if not record_lock_held:
                            return None

                    # Re-check current status regardless of distributed mode: the
                    # record could have completed between the initial scan and now
                    # (this coroutine only runs 5-at-a-time, so the gap can be
                    # seconds), and without this a single-instance deployment would
                    # reset an already-finished record back to QUEUED and reindex it.
                    latest_record = await graph_provider.get_document(
                        record_id,
                        CollectionNames.RECORDS.value,
                    )
                    if latest_record is None or not (
                        latest_record.get("indexingStatus")
                        == ProgressStatus.IN_PROGRESS.value
                        or latest_record.get("parsingStatus")
                        == ProgressStatus.IN_PROGRESS.value
                    ):
                        results["skipped"] += 1
                        return True
                    record = latest_record
                    record_name = record.get("recordName", "Unknown")

                    logger.debug(
                        f"🔄 Recovering stale record: {record_name} (ID: {record_id})"
                    )

                    # Check if connector is disabled or deleted
                    connector_id = record.get("connectorId")
                    origin = record.get("origin")
                    if connector_id and origin == OriginTypes.CONNECTOR.value:
                        connector_instance = await graph_provider.get_document(
                            connector_id, CollectionNames.APPS.value
                        )
                        if not connector_instance:
                            logger.info(
                                f"⏭️ Skipping recovery for record {record_id}: "
                                f"connector instance {connector_id} not found (possibly deleted)."
                            )
                            await update_recovery_status(
                                record_id,
                                {
                                    "parsingStatus": ProgressStatus.AUTO_INDEX_OFF.value,
                                    "indexingStatus": ProgressStatus.AUTO_INDEX_OFF.value,
                                    "extractionStatus": ProgressStatus.AUTO_INDEX_OFF.value,
                                    "processingStartedAt": None,
                                    "reason": "Connector no longer exists",
                                },
                            )
                            results["skipped"] += 1
                            return True
                        if not connector_instance.get("isActive", False):
                            logger.info(
                                f"⏭️ Skipping recovery for record {record_id}: "
                                f"connector instance {connector_id} is inactive."
                            )
                            # Update status to AUTO_INDEX_OFF and reason to connector is inactive
                            await update_recovery_status(
                                record_id,
                                {
                                    "parsingStatus": ProgressStatus.AUTO_INDEX_OFF.value,
                                    "indexingStatus": ProgressStatus.AUTO_INDEX_OFF.value,
                                    "extractionStatus": ProgressStatus.AUTO_INDEX_OFF.value,
                                    "processingStartedAt": None,
                                    "reason": "Connector is inactive",
                                },
                            )
                            results["skipped"] += 1
                            return True

                    # Reconstruct the payload from the record data
                    payload = {
                        "recordId": record_id,
                        "recordName": record.get("recordName"),
                        "orgId": record.get("orgId"),
                        "version": record.get("version", 0),
                        "connectorName": record.get("connectorName", Connectors.KNOWLEDGE_BASE.value),
                        "extension": record.get("extension"),
                        "mimeType": record.get("mimeType"),
                        "origin": record.get("origin"),
                        "recordType": record.get("recordType"),
                        "virtualRecordId": record.get("virtualRecordId"),
                    }

                    # Determine event type - default to NEW_RECORD for recovery
                    # Only treat as REINDEX if version > 0 AND virtualRecordId exists
                    # Otherwise, treat as NEW_RECORD (even if version > 0, the initial indexing might have failed)
                    version = int(payload.get("version", 0) or 0)
                    virtual_record_id = payload.get("virtualRecordId")

                    if version > 0 and virtual_record_id is not None:
                        event_type = EventTypes.REINDEX_RECORD.value
                        logger.debug(f"Treating as REINDEX_RECORD (version={version}, virtualRecordId={virtual_record_id})")
                    else:
                        event_type = EventTypes.NEW_RECORD.value
                        logger.debug(f"Treating as NEW_RECORD (version={version}, virtualRecordId={virtual_record_id})")

                    reset_fields = {
                        "parsingStatus": ProgressStatus.NOT_STARTED.value,
                        "indexingStatus": ProgressStatus.QUEUED.value,
                        "extractionStatus": ProgressStatus.NOT_STARTED.value,
                        "processingStartedAt": None,
                        "reason": "Recovered after restart; re-queued for indexing",
                    }

                    async def publish_recovery_event() -> None:
                        nonlocal published
                        await run_coordination(
                            retry_producer.send_event(
                                topic=Topic.RECORD_EVENTS.value,
                                event_type=event_type,
                                payload=payload,
                                key=str(record_id),
                            )
                        )
                        published = True

                    if concurrency_manager is not None:
                        # The per-record lease keeps the consumer out until the
                        # reset completes. Publishing first avoids losing work
                        # if this process dies between the database write and send.
                        await publish_recovery_event()
                        await update_recovery_status(record_id, reset_fields)
                        reset_for_requeue = True
                    else:
                        await update_recovery_status(record_id, reset_fields)
                        reset_for_requeue = True
                        await publish_recovery_event()

                    logger.debug(
                        f"✅ Re-queued stale record: {record_name} "
                        f"(event={event_type})"
                    )
                    results["requeued"] += 1
                    return True

                except Exception as e:
                    if reset_for_requeue and not published and record_id:
                        try:
                            await update_recovery_status(
                                record_id,
                                {
                                    "parsingStatus": record.get(
                                        "parsingStatus",
                                        ProgressStatus.NOT_STARTED.value,
                                    ),
                                    "indexingStatus": record.get(
                                        "indexingStatus",
                                        ProgressStatus.IN_PROGRESS.value,
                                    ),
                                    "extractionStatus": record.get(
                                        "extractionStatus",
                                        ProgressStatus.NOT_STARTED.value,
                                    ),
                                    "processingStartedAt": 0,
                                    "reason": (
                                        "Stale-record recovery publish failed; "
                                        "will retry"
                                    ),
                                },
                            )
                        except Exception as restore_exc:
                            logger.error(
                                "Failed to restore stale status for %s after "
                                "recovery publish failure: %s",
                                record_id,
                                restore_exc,
                            )
                    logger.error(
                        f"❌ Error recovering record {record_id}: {str(e)}"
                    )
                    results["error"] += 1
                    return False
                finally:
                    if (
                        record_lock_held
                        and record_pool is not None
                        and concurrency_manager is not None
                    ):
                        try:
                            await concurrency_manager.release(
                                record_pool,
                                record_owner,
                            )
                        except Exception as release_exc:
                            logger.warning(
                                "Failed to release recovery lease for record %s: %s",
                                record_id,
                                release_exc,
                            )

        cutoff_ms = (
            get_epoch_timestamp_in_ms()
            - int(messaging_env.stale_recovery_after_seconds * 1000)
        )
        # In distributed mode the per-record lease is normally the
        # authoritative liveness check (process_single_record skips rows an
        # active worker still owns) — but a Redis flush/failover can wipe
        # every lease while a worker is still genuinely mid-processing, and
        # try_acquire on a lease-less record would then look identical to a
        # truly abandoned one. Requiring at least one lease interval to have
        # elapsed since processingStartedAt closes that double-processing
        # window without reintroducing the full stale_recovery_after_seconds
        # wait distributed mode exists to avoid.
        distributed_cutoff_ms = (
            get_epoch_timestamp_in_ms()
            - int(messaging_env.concurrency_lease_seconds * 1000)
        )
        page_size = max(1, messaging_env.stale_recovery_page_size)

        def is_stale(record: dict[str, Any]) -> bool:
            started_at = record.get("processingStartedAt")
            if started_at is None:
                # Rows written before processingStartedAt was introduced are
                # crash leftovers and are recovered once during rollout.
                return True
            effective_cutoff_ms = (
                distributed_cutoff_ms if concurrency_manager is not None else cutoff_ms
            )
            try:
                return float(started_at) <= effective_cutoff_ms
            except (TypeError, ValueError):
                return True

        for status_field in ("indexingStatus", "parsingStatus"):
            offset = 0
            while True:
                if (
                    recovery_renewal_task is not None
                    and recovery_renewal_task.done()
                ):
                    recovery_renewal_task.result()

                page = await graph_provider.get_documents_paginated(
                    CollectionNames.RECORDS.value,
                    skip=offset,
                    limit=page_size,
                    filters={
                        status_field: ProgressStatus.IN_PROGRESS.value,
                    },
                    sort_field="_key",
                    raise_on_error=True,
                )
                if not page:
                    break

                candidates = [
                    record
                    for record in page
                    if is_stale(record)
                    and not (
                        status_field == "parsingStatus"
                        and record.get("indexingStatus")
                        == ProgressStatus.IN_PROGRESS.value
                    )
                ]

                outcomes = await asyncio.gather(
                    *(process_single_record(record) for record in candidates)
                )
                total_records += sum(outcome is not None for outcome in outcomes)
                removed_from_result = sum(outcome is True for outcome in outcomes)

                if len(page) < page_size:
                    break
                # Successful recovery removes rows from this filtered result,
                # so only advance over rows that remain in front of the cursor.
                # Caveat: this only accounts for rows this page removed — a
                # concurrent status change on a row from an *earlier* page
                # (outside our control) can still shift the offset by one and
                # skip or repeat a row; harmless since the loop reruns on the
                # next stale_recovery_interval_seconds tick.
                offset += len(page) - removed_from_result

        # QUEUED records on a disabled connector are unreachable by the scan
        # above: it filters on IN_PROGRESS, and a QUEUED row has no
        # processingStartedAt to age out. Nothing else moves them either — the
        # event guard only fires for messages still in the broker — so without
        # this pass they sit in QUEUED for ever.
        #
        # Deliberately narrow: it only ever marks records whose connector is
        # gone or inactive. Re-queuing QUEUED rows for *live* connectors would
        # duplicate work, because a message for them may still be in the broker.
        queued_swept = await _sweep_queued_records_for_inactive_connectors(
            graph_provider=graph_provider,
            logger=logger,
            page_size=page_size,
        )
        total_records += queued_swept

        # The counterpart for *live* connectors: a row whose event was lost or
        # never published is invisible to both the scan above and the sweep.
        # STRANDED_RECORD_REPUBLISH_AFTER_SECONDS=0 disables it.
        total_records += await _republish_stranded_records(
            graph_provider=graph_provider,
            logger=logger,
            producer=retry_producer,
            run_coordination=run_coordination,
            concurrency_manager=concurrency_manager,
            page_size=page_size,
            is_backlogged=lambda: _record_consumer_backlogged(app_container),
        )

        # Vectors whose last referencing record was repointed elsewhere are
        # reachable from neither the record scan above nor the membership
        # backfill (both walk records, and this VRID has none). Left alone they
        # stay searchable for ever. See the sweep's docstring.
        try:
            pipeline = app_container.indexing_pipeline()
            if inspect.isawaitable(pipeline):
                pipeline = await pipeline
        except Exception as exc:
            pipeline = None
            logger.warning(f"Indexing pipeline unavailable for orphan sweep: {exc}")
        if pipeline is not None:
            total_records += await _sweep_orphaned_virtual_record_mappings(
                graph_provider=graph_provider,
                pipeline=pipeline,
                logger=logger,
                page_size=page_size,
            )

        total_records += await _sweep_pipeline_stages(app_container, concurrency_manager, logger)

        if total_records == 0:
            logger.debug("No stale in-progress records to recover")
            return

        logger.info(
            f"✅ Recovery complete. Processed {total_records} stale record(s): "
            f"{results['requeued']} re-queued, {results['skipped']} skipped, "
            f"{results['error']} errors"
        )

    except Exception as e:
        logger.error(f"❌ Error during record recovery: {str(e)}")
        # Don't raise - we want to continue starting the service even if recovery fails
        logger.warning("⚠️ Continuing to start message consumers despite recovery errors")
    finally:
        if recovery_renewal_task is not None:
            recovery_renewal_task.cancel()
            await asyncio.gather(
                recovery_renewal_task,
                return_exceptions=True,
            )
        if recovery_lock_held and concurrency_manager is not None:
            try:
                await concurrency_manager.release("recovery", recovery_owner)
            except Exception as release_exc:
                logger.warning(
                    "Failed to release stale-record recovery lease: %s",
                    release_exc,
                )


# How much of virtualRecordToDocIdMapping one recovery tick will walk. Bounded
# because orphans are rare and every row costs a graph lookup; the cursor below
# carries the position forward so successive ticks cover the rest.
ORPHAN_SCAN_MAX_PAGES_PER_TICK = 4
_orphan_sweep_cursor = 0



# Stage sweep thresholds: a claim unpublished this long is re-sent; a running job this far
# past the longest stage budget is reclaimed once its lease is found free.
_STAGE_QUEUED_SWEEP_MS = 120_000
_STAGE_IN_PROGRESS_SWEEP_MS = 1_800_000
_STAGE_SWEEP_LIMIT = 200
_STAGE_SWEEP_OWNER = "pipeline-stage-sweeper"


async def _resolve_pipeline_runtime(app_container: IndexingAppContainer) -> "PipelineRuntime":
    runtime = app_container.pipeline_runtime()
    if inspect.isawaitable(runtime):
        runtime = await runtime
    return runtime


async def _sweep_pipeline_stages(
    app_container: IndexingAppContainer,
    concurrency_manager: IDistributedLeaseManager | None,
    logger: logging.Logger,
) -> int:
    """Re-publish stage jobs whose claim never reached the broker; reclaim ones whose worker is gone."""
    try:
        runtime = await _resolve_pipeline_runtime(app_container)
    except Exception as exc:
        logger.warning("Pipeline runtime unavailable for the stage sweep: %s", exc)
        return 0

    is_job_active: Callable[[str], Awaitable[bool]] | None = None
    if concurrency_manager is not None:
        manager = concurrency_manager

        async def job_lease_held(job_id: str) -> bool:
            # The stage consumer holds this lease while a job runs; if the sweeper can
            # take it, nobody is running the job.
            pool = f"record:job:{job_id}"
            if await manager.try_acquire(pool, _STAGE_SWEEP_OWNER, 1, 5.0):
                await manager.release(pool, _STAGE_SWEEP_OWNER)
                return False
            return True

        is_job_active = job_lease_held

    report = await runtime.coordinator.sweep(
        queued_older_than_ms=_STAGE_QUEUED_SWEEP_MS,
        limit=_STAGE_SWEEP_LIMIT,
        in_progress_older_than_ms=_STAGE_IN_PROGRESS_SWEEP_MS if is_job_active is not None else None,
        is_job_active=is_job_active,
    )
    if report.republished or report.reclaimed:
        logger.info(
            "Pipeline stage sweep: %d re-published, %d reclaimed of %d scanned",
            report.republished, report.reclaimed, report.scanned,
        )
    return report.republished + report.reclaimed

async def _sweep_orphaned_virtual_record_mappings(
    *,
    graph_provider,
    pipeline,
    logger,
    page_size: int,
) -> int:
    """Clean up VRIDs whose vectors outlived every graph record referencing them.

    An abandoned VRID can be stranded: on an N:1 split the record is repointed at
    a fresh VRID *before* the old one is cleaned up, so if that cleanup fails the
    old VRID has no record left to find it by — and the membership backfill walks
    records, not VRIDs, so it can never reach it either.

    virtualRecordToDocIdMapping is the durable marker for this. It is written per
    VRID and removed only by a successful cleanup, so a mapping whose VRID has no
    records is exactly an orphan. Reusing rewrite_or_delete_virtual_record keeps
    the confirming re-read, so a lagging graph read cannot delete live vectors.

    Orphans are rare but the mapping collection is large, and each row costs a
    graph lookup, so a full scan per tick would be a standing N+1 for almost no
    yield. Instead each tick walks a bounded slice and leaves the cursor where it
    stopped, wrapping at the end: the collection is covered over many ticks
    rather than all at once. The cursor is per-process and resets on restart —
    acceptable, since nothing here is required to be timely.
    """
    global _orphan_sweep_cursor

    swept = 0
    offset = _orphan_sweep_cursor
    pages_scanned = 0
    while pages_scanned < ORPHAN_SCAN_MAX_PAGES_PER_TICK:
        pages_scanned += 1
        page = await graph_provider.get_documents_paginated(
            CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value,
            skip=offset,
            limit=page_size,
            sort_field="_key",
            raise_on_error=False,
        )
        if not page:
            offset = 0  # ran off the end; restart from the top next tick
            break

        swept_this_page = 0
        for mapping in page:
            vrid = mapping.get("_key") or mapping.get("id")
            if not isinstance(vrid, str) or not vrid:
                continue
            try:
                records = await graph_provider.get_records_by_virtual_record_id(vrid)
            except Exception as exc:
                logger.warning(
                    "Could not check virtual record %s for orphaned vectors: %s",
                    vrid,
                    exc,
                )
                continue
            if records:
                continue
            try:
                outcome = await pipeline.rewrite_or_delete_vector_membership(vrid)
            except Exception as exc:
                logger.error(
                    "Failed to clean up orphaned vectors for virtual record %s: %s",
                    vrid,
                    exc,
                )
                continue
            if outcome == "deleted":
                swept += 1
                swept_this_page += 1

        if len(page) < page_size:
            offset = 0
            break
        offset += len(page) - swept_this_page

    _orphan_sweep_cursor = offset

    if swept:
        logger.info(
            "Cleaned up %d orphaned virtual record(s) whose vectors outlived "
            "every referencing record",
            swept,
        )
    return swept


async def _sweep_queued_records_for_inactive_connectors(
    *,
    graph_provider,
    logger,
    page_size: int,
) -> int:
    """Move stranded records on gone/inactive connectors to AUTO_INDEX_OFF.

    Disabling a connector sweeps its backlog at the time of the toggle, but that
    cannot help rows queued during the toggle, a sweep that failed part-way, or a
    connector disabled before that sweep existed. This is the self-healing path
    for all three.

    Never re-publishes. A row on a *live* connector is left alone: its message may
    still be sitting in the broker, and re-queuing would duplicate the work.

    QUEUED rows are unreachable by the main stale scan — it filters on
    IN_PROGRESS, and a QUEUED row has no processingStartedAt to age out — so
    without this they sit for ever.

    IN_PROGRESS rows are included past a short grace period. The main scan waits
    stale_recovery_after_seconds (~32 min by default), which is right for a
    pipeline that might still finish and wrong here: the connector has already
    been popped from connectors_map, so nothing in flight can succeed. One lease
    interval is enough to be sure no worker still owns the row.
    """
    swept = 0
    connector_active: dict[str, bool] = {}
    in_progress_cutoff_ms = get_epoch_timestamp_in_ms() - int(
        messaging_env.concurrency_lease_seconds * 1000
    )

    async def _is_inactive(connector_id: str) -> bool:
        if connector_id not in connector_active:
            instance = await graph_provider.get_document(
                connector_id, CollectionNames.APPS.value
            )
            # A missing instance counts as inactive: its records can never be
            # indexed again.
            connector_active[connector_id] = bool(
                instance and instance.get("isActive", False)
            )
        return not connector_active[connector_id]

    for status_value in (
        ProgressStatus.QUEUED.value,
        ProgressStatus.IN_PROGRESS.value,
    ):
        offset = 0
        while True:
            page = await graph_provider.get_documents_paginated(
                CollectionNames.RECORDS.value,
                skip=offset,
                limit=page_size,
                filters={"indexingStatus": status_value},
                sort_field="_key",
                raise_on_error=False,
            )
            if not page:
                break

            swept_this_page = 0
            for record in page:
                connector_id = record.get("connectorId")
                if (
                    not connector_id
                    or record.get("origin") != OriginTypes.CONNECTOR.value
                ):
                    continue

                if status_value == ProgressStatus.IN_PROGRESS.value:
                    started_at = record.get("processingStartedAt")
                    try:
                        recently_started = (
                            started_at is not None
                            and float(started_at) > in_progress_cutoff_ms
                        )
                    except (TypeError, ValueError):
                        recently_started = False
                    if recently_started:
                        continue

                if not await _is_inactive(connector_id):
                    continue

                record_key = record.get("_key") or record.get("id")
                if not record_key:
                    continue
                try:
                    await graph_provider.update_node(
                        record_key,
                        CollectionNames.RECORDS.value,
                        {
                            "indexingStatus": ProgressStatus.AUTO_INDEX_OFF.value,
                            "processingStartedAt": None,
                            "reason": "Connector is inactive",
                        },
                    )
                    swept += 1
                    swept_this_page += 1
                except Exception as exc:
                    logger.error(
                        "Failed to move record %s to manual indexing: %s",
                        record_key,
                        exc,
                    )

            if len(page) < page_size:
                break
            # Swept rows leave this filtered result, so advance only over the
            # rows still in front of the cursor — this page's removals.
            offset += len(page) - swept_this_page

    if swept:
        logger.info(
            "Moved %d record(s) on inactive connectors to manual indexing", swept
        )
    return swept


def _as_epoch_ms(value: object) -> float | None:
    """A stored epoch-ms stamp as a number; None when absent or unreadable."""
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        return float(value)
    except ValueError:
        return None


# While the record consumer has a backlog, a stranded row waits up to this many intervals
# before it is re-sent anyway: late events are not re-sent, lost ones still are.
_STRANDED_BACKLOG_PATIENCE = 6


def _record_consumer_backlogged(app_container: object) -> bool:
    """Whether this process's record consumer holds more work than it can start.

    Read from its dispatch stats: tasks waiting for an index permit, or reads
    blocked by the dispatch budget. Only this process's consumer is visible;
    the sweep runs on one node, under the cluster-wide recovery lease.
    """
    consumers = cast("list[tuple[object, ...]]", getattr(app_container, "kafka_consumers", None) or [])
    for entry in consumers:
        consumer = entry[1] if len(entry) > 1 else None
        # Stage consumers admit per stage; the sweep re-sends record events.
        if consumer is None or getattr(consumer, "stage_admission", None) is not None:
            continue
        stats = getattr(consumer, "dispatch_stats", None)
        if not callable(stats):
            continue
        snapshot = cast("dict[str, Any]", stats())
        total = cast("dict[str, Any]", snapshot.get("total") or {})
        if snapshot.get("blocked") or (total.get("waiters") or 0) > 0:
            return True
    return False


async def _republish_stranded_records(
    *,
    graph_provider: IGraphDBProvider,
    logger: logging.Logger,
    producer,
    run_coordination,
    concurrency_manager,
    page_size: int,
    is_backlogged: Callable[[], bool] | None = None,
) -> int:
    """Re-publish records that have been waiting on an event that never came.

    The sweep above only rescues rows on connectors that are gone; the main
    stale scan only looks at IN_PROGRESS. A row on a *live* connector whose
    event was lost — dropped by the broker, discarded by a consumer, or never
    published because the send failed after the transaction committed — is
    reachable by neither, so it waits for ever.

    Keyed on how long the row has waited since this sweep first saw it, on a
    clock the sweep owns (``awaitingEventSince``). QUEUED is written both by the
    upsert that precedes publishing and by the publish itself, so the status
    cannot tell "the event is on the broker" from "it was never sent", and the
    row's own timestamps cannot either: connectors fill ``updatedAtTimestamp``
    with the source system's time, so a Jira issue last edited a year ago looks
    a year old the moment it is queued. The first sighting only starts the
    clock, a consumer picking the row up clears it, and a row still waiting a
    full interval later is re-sent and its clock restarted. While this
    process's record consumer has a backlog, re-sends wait (up to
    ``_STRANDED_BACKLOG_PATIENCE`` intervals): an event behind a backlog is
    late, not lost.

    On by default (one hour); STRANDED_RECORD_REPUBLISH_AFTER_SECONDS=0
    disables it.

    Re-publishing is safe to repeat: the handler skips a record that is already
    COMPLETED, and the per-record exclusivity lease stops a republished event
    racing one already in flight.
    """
    after_seconds = messaging_env.stranded_record_republish_after_seconds
    if after_seconds <= 0:
        return 0

    now_ms = get_epoch_timestamp_in_ms()
    cutoff_ms = now_ms - int(after_seconds * 1000)
    patience_cutoff_ms = now_ms - int(after_seconds * 1000 * _STRANDED_BACKLOG_PATIENCE)
    connector_active: dict[str, bool] = {}
    republished = 0
    deferred = 0
    try:
        backlogged = bool(is_backlogged is not None and is_backlogged())
    except Exception as exc:
        logger.debug("Could not read the record consumer's backlog: %s", exc)
        backlogged = False

    async def _is_active(connector_id: str) -> bool:
        if connector_id not in connector_active:
            instance = await graph_provider.get_document(
                connector_id, CollectionNames.APPS.value
            )
            connector_active[connector_id] = bool(
                instance and instance.get("isActive", False)
            )
        return connector_active[connector_id]

    for status_value in (
        ProgressStatus.QUEUED.value,
        ProgressStatus.NOT_STARTED.value,
    ):
        offset = 0
        while True:
            page = await graph_provider.get_documents_paginated(
                CollectionNames.RECORDS.value,
                skip=offset,
                limit=page_size,
                filters={"indexingStatus": status_value},
                sort_field="_key",
                raise_on_error=False,
            )
            if not page:
                break

            for record in page:
                record_key = record.get("_key") or record.get("id")
                connector_id = record.get("connectorId")
                if (
                    not record_key
                    or not connector_id
                    or record.get("origin") != OriginTypes.CONNECTOR.value
                ):
                    continue

                waiting_since = _as_epoch_ms(record.get("awaitingEventSince"))
                if waiting_since is None:
                    # First sighting: start the clock. Nothing is sent until the row
                    # has waited a full interval with no consumer picking it up.
                    try:
                        await graph_provider.update_node(
                            record_key,
                            CollectionNames.RECORDS.value,
                            {"awaitingEventSince": get_epoch_timestamp_in_ms()},
                        )
                    except Exception as exc:
                        logger.debug("Could not start the stranded clock for %s: %s", record_key, exc)
                    continue
                if waiting_since > cutoff_ms:
                    continue
                if backlogged and waiting_since > patience_cutoff_ms:
                    deferred += 1
                    continue

                if not await _is_active(connector_id):
                    # The sweep above owns these; moving them here would race it.
                    continue

                # A duplicate parked behind an in-flight twin is legitimately
                # QUEUED with its message already acked — it is released by the
                # twin's completion, not by us.
                if record.get("md5Checksum") and record.get("virtualRecordId"):
                    continue

                record_owner = f"stranded:{uuid4().hex}"
                record_pool = f"record:{record_key}"
                lock_held = False
                try:
                    if concurrency_manager is not None:
                        lock_held = await concurrency_manager.try_acquire(
                            record_pool,
                            record_owner,
                            1,
                            messaging_env.concurrency_lease_seconds,
                        )
                        if not lock_held:
                            # Someone is working on it after all.
                            continue

                    payload = {
                        "recordId": record_key,
                        "recordName": record.get("recordName"),
                        "orgId": record.get("orgId"),
                        "version": record.get("version", 0),
                        "connectorName": record.get("connectorName"),
                        "connectorId": connector_id,
                        "extension": record.get("extension"),
                        "mimeType": record.get("mimeType"),
                        "origin": record.get("origin"),
                        "recordType": record.get("recordType"),
                        "virtualRecordId": record.get("virtualRecordId"),
                    }
                    version = int(payload.get("version", 0) or 0)
                    event_type = (
                        EventTypes.REINDEX_RECORD.value
                        if version > 0 and payload.get("virtualRecordId")
                        else EventTypes.NEW_RECORD.value
                    )

                    # The claim restarts the row's clock and is written BEFORE the
                    # send, not after it. Written after, a graph failure following
                    # a successful send left the row eligible again next tick: one
                    # duplicate per tick, per record, inflating the very backlog
                    # that delays the consumer. If the claim cannot be persisted,
                    # nothing is sent this tick.
                    claimed = await graph_provider.update_node(
                        record_key,
                        CollectionNames.RECORDS.value,
                        {"awaitingEventSince": get_epoch_timestamp_in_ms()},
                    )
                    if not claimed:
                        logger.error(
                            "Could not record a republish claim for stranded "
                            "record %s; skipping it this tick rather than risk "
                            "re-sending it every tick",
                            record_key,
                        )
                        continue

                    try:
                        await run_coordination(
                            producer.send_event(
                                topic=Topic.RECORD_EVENTS.value,
                                event_type=event_type,
                                payload=payload,
                                key=str(record_key),
                            )
                        )
                    except Exception:
                        # The claim restarted the clock, so without this the
                        # record would wait a full interval before its next
                        # attempt. Putting the old stamp back (best effort) lets
                        # the next tick retry; if even that fails the record
                        # still only waits one interval -- bounded either way.
                        try:
                            await graph_provider.update_node(
                                record_key,
                                CollectionNames.RECORDS.value,
                                {"awaitingEventSince": int(waiting_since)},
                            )
                        except Exception as clear_exc:
                            logger.warning(
                                "Could not clear republish claim for %s after a "
                                "failed send; it will retry after the interval: %s",
                                record_key,
                                clear_exc,
                            )
                        raise
                    republished += 1
                    logger.warning(
                        "Re-published stranded record %s (%s, status %s, "
                        "no consumer picked it up for %.0fs)",
                        record_key,
                        record.get("recordName"),
                        status_value,
                        (get_epoch_timestamp_in_ms() - waiting_since) / 1000,
                    )
                except Exception as exc:
                    logger.error(
                        "Failed to re-publish stranded record %s: %s",
                        record_key,
                        exc,
                    )
                finally:
                    if lock_held and concurrency_manager is not None:
                        try:
                            await concurrency_manager.release(
                                record_pool, record_owner
                            )
                        except Exception as release_exc:
                            logger.warning(
                                "Failed to release stranded-record lease for %s: %s",
                                record_key,
                                release_exc,
                            )

            if len(page) < page_size:
                break
            # Republished rows keep their status, so the filtered result does
            # not shrink under the cursor — advance over the whole page.
            offset += len(page)

    if republished:
        logger.warning(
            "Re-published %d stranded record(s) whose events never arrived",
            republished,
        )
    if deferred:
        logger.info(
            "Deferred re-sending %d stranded record(s): the record consumer has a "
            "backlog, so their events may only be late",
            deferred,
        )
    return republished


async def run_stale_recovery_loop(
    app_container: IndexingAppContainer,
    graph_provider: IGraphDBProvider,
) -> None:
    """Continuously repair records abandoned by crashes or timed-out workers."""
    logger = app_container.logger()
    startup_grace = max(
        0.0,
        messaging_env.stale_recovery_startup_grace_seconds,
    )
    if startup_grace:
        logger.info(
            "Delaying stale-record recovery for %.0fs during rollout",
            startup_grace,
        )
        await asyncio.sleep(startup_grace)

    while True:
        await recover_in_progress_records(app_container, graph_provider)
        await asyncio.sleep(
            max(1.0, messaging_env.stale_recovery_interval_seconds)
        )


class _Closable(Protocol):
    async def cleanup(self) -> None: ...


async def _stop_consumers_then_shared_resources(
    consumers: list[tuple[Any, ...]],
    logger: logging.Logger,
    *,
    also_close: tuple[_Closable | None, ...] = (),
    during: str = "",
) -> None:
    """Stop every consumer, then close each resource they use, once.

    The record consumer and every stage consumer share one lease manager, retry
    tracker and retry producer. A consumer that is still stopping renews leases,
    counts deliveries and re-queues through them, so none may close before the
    last consumer has stopped.
    """
    for item in consumers:
        name, consumer = item[0], item[1]
        try:
            await consumer.stop()
            logger.info("✅ %s message consumer stopped%s", str(name).title(), during)
        except Exception as exc:
            logger.error("❌ Error stopping %s consumer%s: %s", name, during, exc)

    # Injected into the consumers but owned by start_kafka_consumers: consumer.stop()
    # does not close them (that broke restart).
    resources: list[tuple[str, _Closable | None]] = []
    for item in consumers:
        name, consumer = item[0], item[1]
        resources.extend(
            (f"{name} {attr}", cast("_Closable | None", getattr(consumer, attr, None)))
            for attr in ("concurrency_manager", "retry_manager")
        )
        if len(item) > 2:
            resources.append((f"{name} retry producer", cast("_Closable | None", item[2])))
    resources.extend(("shared consumer resource", resource) for resource in also_close)
    closed: set[int] = set()
    for label, resource in resources:
        if resource is None or id(resource) in closed:
            continue
        closed.add(id(resource))
        try:
            await resource.cleanup()
        except Exception as exc:
            logger.error("Error closing %s%s: %s", label, during, exc)


# The connector service creates the collections on a first start; indexing waits for it.
_SCHEMA_RETRY_FIRST_S = 2.0
_SCHEMA_RETRY_MAX_S = 60.0
# Missing stage topics usually wait on an operator (topic ACLs, or creating them by hand).
_TOPIC_RETRY_FIRST_S = 30.0
_TOPIC_RETRY_MAX_S = 300.0


async def _ensure_pipeline_schema(graph_provider: IGraphDBProvider, logger: logging.Logger) -> None:
    """Wait until the graph holds the schema indexing writes against.

    Retries instead of failing the startup: where nothing restarts the process, exiting would
    stop indexing for good, and where something does it would only repeat the wait.
    """
    delay = _SCHEMA_RETRY_FIRST_S
    while True:
        try:
            await graph_provider.ensure_pipeline_schema()
            logger.info("✅ Graph schema for the indexing pipeline is in place")
            return
        except Exception as e:
            logger.error("❌ Graph schema for the indexing pipeline is not ready (%s); retrying in %.0fs", e, delay)
        await asyncio.sleep(delay)
        delay = min(delay * 2, _SCHEMA_RETRY_MAX_S)


async def _start_stages_when_topics_exist(
    admin: IMessageAdmin,
    topics: list[str],
    start_stages: Callable[[], Awaitable[None]],
    logger: logging.Logger,
) -> None:
    delay = _TOPIC_RETRY_FIRST_S
    while True:
        await asyncio.sleep(delay)
        try:
            await admin.ensure_topics_exist(topics)
            break
        except Exception as e:
            delay = min(delay * 2, _TOPIC_RETRY_MAX_S)
            logger.error("❌ Pipeline stage topics %s are still not available (%s); retrying in %.0fs", topics, e, delay)
    try:
        await start_stages()
        logger.info("✅ Pipeline stage topics %s are available; stage consumers started", topics)
    except Exception:
        logger.exception("❌ Starting the pipeline stage consumers failed")


async def start_kafka_consumers(
    app_container: IndexingAppContainer,
    governor: ResourceGovernor | None = None,
) -> list[Any]:
    """Start all message consumers at application level"""
    logger = app_container.logger()
    consumers: list[tuple[str, Any, Any]] = []
    broker_type = get_message_broker_type()
    retry_manager = None
    retry_producer = None
    concurrency_manager = None
    # Every consumer below runs its handlers on this one loop (see WorkerLoop).
    worker = WorkerLoop(logger)
    setattr(app_container, "worker_loop", worker)

    try:
        logger.info(f"🚀 Starting Record Consumer (broker: {broker_type})...")
        record_consumer_config = await MessagingUtils.create_record_consumer_config(app_container)

        # Create RetryManager for persistent failure retry tracking
        redis_config = await MessagingUtils._get_redis_config(app_container)
        retry_manager = MessagingFactory.create_retry_manager(logger, redis_config)
        await retry_manager.initialize()
        logger.info("✅ RetryManager initialized for %s consumer", broker_type.value)

        if messaging_env.distributed_concurrency_enabled:
            concurrency_manager = DistributedConcurrencyManager(
                logger,
                redis_config,
                key_prefix=messaging_env.concurrency_key_prefix,
                operation_timeout_seconds=(
                    messaging_env.concurrency_redis_timeout_seconds
                ),
                max_connections=messaging_env.concurrency_redis_max_connections,
            )
            # Fails the startup, like the RetryManager ping above: Redis is a
            # hard requirement for this service on either broker, so degrading
            # here would only mask a Redis that the line above already proved
            # reachable. The in-flight fail-open (see LeaseKind) is unaffected —
            # a Redis that dies *after* startup still leaves capacity leases
            # running under node-local limits.
            await concurrency_manager.initialize()
            logger.info(
                "✅ Distributed indexing concurrency initialized "
                "(global parsing=%d, indexing=%d)",
                messaging_env.max_concurrent_parsing,
                messaging_env.max_concurrent_indexing,
            )

        # Create producer for re-queueing failed messages
        producer_config = await MessagingUtils.create_producer_config_from_service(
            app_container.config_service(),
            client_id="indexing_retry_producer",
        )
        retry_producer = MessagingFactory.create_producer(
            logger=logger,
            config=producer_config,
            broker_type=broker_type,
        )
        await retry_producer.initialize()
        logger.info("✅ Retry producer initialized for %s", broker_type.value)

        # Built before the consumer because it is both the message handler and
        # the consumer's abandonment sink: when a message is discarded without
        # being processed, this is what puts the record into a terminal status
        # instead of leaving it on one no recovery sweep revisits.
        record_event_handler = await KafkaUtils.create_record_event_handler(
            app_container, producer=retry_producer
        )

        # Same process-wide singleton the ParsingClient/DoclingClient/
        # EmbeddingServerEmbeddings instances used by this consumer's
        # pipeline default to (see app.services.messaging.backpressure) —
        # passing it explicitly here is what lets the consumer's read loop
        # actually see their 429 signals and pause new reads.
        record_kafka_consumer = MessagingFactory.create_consumer(
            broker_type=broker_type,
            logger=logger,
            config=record_consumer_config,
            consumer_type=ConsumerType.INDEXING,
            retry_manager=retry_manager,
            producer=retry_producer,
            concurrency_manager=concurrency_manager,
            governor=governor,
            backpressure_coordinator=get_default_backpressure_coordinator(),
            disposition_sink=record_event_handler,
            worker=worker,
        )
        consumers.append(("record", record_kafka_consumer, retry_producer))

        record_message_handler = await KafkaUtils.create_record_message_handler(
            app_container,
            producer=retry_producer,
            record_event_service=record_event_handler,
        )
        await record_kafka_consumer.start(record_message_handler)  # type: ignore[arg-type]
        logger.info("✅ Record message consumer started")

        # Pipeline stages: one consumer per stage topic, each on its own permits and
        # cluster lease pool, so stage work (classification) never holds an indexing permit.
        pipeline_runtime = await _resolve_pipeline_runtime(app_container)
        # A stage writes the stored record under the record's exclusivity lease, as its re-index does.
        pipeline_runtime.record_leases.bind(concurrency_manager)
        registry = pipeline_runtime.registry
        stage_topics = [registry.topic_for(name) for name in registry.names()]
        stage_producer = retry_producer
        main_loop = asyncio.get_running_loop()

        async def start_stage_consumers() -> None:
            # Bound only once the topics exist: Kafka holds a send to a missing topic for its
            # metadata timeout, and the record handler's hand-off would wait with it.
            pipeline_runtime.publisher.bind(stage_producer, main_loop)
            for stage_name in registry.names():
                topic = registry.topic_for(stage_name)
                stage_config = await MessagingUtils.create_consumer_config(
                    app_container,
                    f"pipeline_{stage_name}_client",
                    f"pipeline_{stage_name}_group",
                    [topic],
                    is_indexing=True,
                )
                stage_consumer = MessagingFactory.create_consumer(
                    broker_type=broker_type,
                    logger=logger,
                    config=stage_config,
                    consumer_type=ConsumerType.INDEXING,
                    retry_manager=retry_manager,
                    producer=stage_producer,
                    concurrency_manager=concurrency_manager,
                    backpressure_coordinator=get_default_backpressure_coordinator(),
                    disposition_sink=pipeline_runtime.handler,
                    stage_admission=StageAdmission(stage=stage_name, limit=pipeline_runtime.stage_limits[stage_name]),
                    worker=worker,
                )
                # The retry producer is owned by the record consumer's entry, so it is closed once.
                consumers.append((topic, stage_consumer, None))
                await stage_consumer.start(pipeline_runtime.handler)  # type: ignore[arg-type]
                logger.info("✅ Pipeline stage consumer started: %s", topic)

        admin = MessagingFactory.create_admin(logger, producer_config, broker_type)
        try:
            await admin.ensure_topics_exist(stage_topics)
        except Exception as e:
            # Records stay indexable without the stage topics; only classification waits.
            logger.error(
                "❌ Pipeline stage topics %s are not available (%s). Records are still indexed and "
                "searchable; classification waits until the topics exist. Create them, or allow this "
                "client to create topics. Retrying in the background.",
                stage_topics,
                e,
            )
            setattr(
                app_container,
                "stage_startup_task",
                asyncio.create_task(
                    _start_stages_when_topics_exist(admin, stage_topics, start_stage_consumers, logger)
                ),
            )
            return consumers
        await start_stage_consumers()

        return consumers
    except Exception as e:
        logger.error(f"❌ Error starting message consumers: {str(e)}")
        # The started consumers first, then what they share (owned here, whether or
        # not any consumer had started).
        await _stop_consumers_then_shared_resources(
            consumers,
            logger,
            also_close=(concurrency_manager, retry_manager, retry_producer),
            during=" during cleanup",
        )
        # After every consumer that ran on it.
        await worker.aclose()
        raise

async def _close_worker_loop(container: IndexingAppContainer) -> None:
    """Close the shared worker loop, once every consumer and loop-bound client on it is done."""
    worker = getattr(container, "worker_loop", None)
    if isinstance(worker, WorkerLoop):
        await worker.aclose()
        setattr(container, "worker_loop", None)


async def stop_kafka_consumers(container: IndexingAppContainer, *, close_worker_loop: bool = True) -> None:
    """Stop all Kafka consumers and their associated producers"""

    logger = container.logger()
    # A stage start still waiting for its topics must not start consumers once these stop.
    stage_startup = getattr(container, "stage_startup_task", None)
    if isinstance(stage_startup, asyncio.Task) and not stage_startup.done():
        _ = stage_startup.cancel()
        with suppress(asyncio.CancelledError):
            await stage_startup
    consumers = getattr(container, 'kafka_consumers', [])
    await _stop_consumers_then_shared_resources(consumers, logger)

    # Closed only once every consumer running on it has stopped; the lifespan closes it later,
    # after the clients bound to it.
    if close_worker_loop:
        await _close_worker_loop(container)

    # Clear the consumers list
    if hasattr(container, 'kafka_consumers'):
        container.kafka_consumers = []

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan context manager for FastAPI"""

    app_container = await get_initialized_container()
    app.container = app_container
    logger = app.container.logger()
    logger.info("🚀 Starting application")

    try:
        await telemetry.bind(app_container.config_service(), logger).start()
    except Exception as e:
        logger.warning(f"❌ Failed to start telemetry pusher: {e}")

    graph_provider = getattr(app_container, '_graph_provider', None)
    if not graph_provider:
        # Fallback: if not set during initialization, resolve it now
        graph_provider = await app_container.graph_provider()
    app.state.graph_provider = graph_provider

    # Indexing writes stage states and record fields declared by the connector service's schema
    # bootstrap, which may not have run yet (a first start, or an older connector image).
    await _ensure_pipeline_schema(cast(IGraphDBProvider, graph_provider), logger)

    # One governor per process: derives parse/index ceilings from cgroup/CPU
    # limits (falling back to the operator's MAX_CONCURRENT_* when set) and
    # adapts the effective limits within them as load changes. Shared by
    # both indexing consumers below (only one is actually active per broker
    # configuration, but construction is cheap either way).
    governor = ResourceGovernor(
        logger=logger,
        env_parse=messaging_env.env_max_concurrent_parsing,
        env_index=messaging_env.env_max_concurrent_indexing,
        worker_count=max(1, int(os.getenv("INDEXING_UVICORN_WORKERS", "1"))),
        reserve_embedding_cpus=await is_local_cpu_embedding_configured(
            app_container.config_service(), logger
        ),
    )
    app.state.governor = governor
    app_container.resource_governor = governor
    governor_task = asyncio.create_task(governor.run())
    # Both leaf modules run a worker-process OOM-kill (BrokenProcessPool)
    # straight into the governor's fast incident path instead of only the
    # periodic sampler noticing the pressure it already caused.
    set_docling_processor_governor(governor)
    set_pdf_rasterizer_governor(governor)

    # This service flips records to COMPLETED, which is when a KB record first
    # becomes searchable — the query service's cached map must be dropped then.
    try:
        from app.services.cache.accessible_records_cache import AccessibleRecordsCache
        from app.services.cache.invalidation_hooks import (
            init_accessible_records_invalidator,
        )
        cache = await AccessibleRecordsCache.create(logger, app_container.config_service())
        app.state.accessible_records_cache = cache
        init_accessible_records_invalidator(logger, cache, graph_provider)
    except Exception as e:
        logger.warning(f"❌ Failed to register accessible-records invalidator: {e}")


    # Start all message consumers centrally
    try:
        consumers = await start_kafka_consumers(app_container, governor)
        app_container.kafka_consumers = consumers
        app.state.pipeline_runtime = await _resolve_pipeline_runtime(app_container)
        logger.info("✅ All message consumers started successfully")
    except Exception as e:
        logger.error(f"❌ Failed to start message consumers: {str(e)}")
        raise

    # Continuously recover abandoned statuses. Distributed per-record leases
    # protect active handlers; processingStartedAt is the single-instance fallback.
    # Must happen AFTER consumers start: for Neo4j, start_kafka_consumers
    # reconnects the graph driver to the consumer's worker loop, so recovery
    # must run in that same loop to avoid cross-loop Future errors.
    data_store = os.getenv("DATA_STORE", "arangodb").lower()
    worker_loop = None
    if data_store == "neo4j" and consumers:
        record_consumer = consumers[0][1]
        worker_loop = getattr(record_consumer, "worker_loop", None)

    if worker_loop and worker_loop.is_running():
        app.state.recovery_future = asyncio.run_coroutine_threadsafe(
            run_stale_recovery_loop(app_container, graph_provider),
            worker_loop,
        )
        app.state.backfill_future = asyncio.run_coroutine_threadsafe(
            run_vector_membership_backfill_loop(app_container, graph_provider),
            worker_loop,
        )
    else:
        app.state.recovery_task = asyncio.create_task(
            run_stale_recovery_loop(app_container, graph_provider)
        )
        app.state.backfill_task = asyncio.create_task(
            run_vector_membership_backfill_loop(app_container, graph_provider)
        )

    yield
    # Shutdown
    logger.info("🔄 Shutting down application")
    if telemetry.pusher is not None:
        try:
            await telemetry.pusher.stop()
        except asyncio.CancelledError:
            # Let a genuine shutdown-timeout cancellation propagate instead
            # of swallowing it here — otherwise the caller (ASGI server)
            # can't tell shutdown didn't finish, and we'd still attempt the
            # awaits below on a cancelled task.
            raise
        except Exception as e:
            logger.warning(f"❌ Error stopping telemetry pusher: {e}")

    # Cancel background recovery if it's still running.
    recovery_task = getattr(app.state, "recovery_task", None)
    if recovery_task:
        if not recovery_task.done():
            recovery_task.cancel()
        try:
            await recovery_task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"❌ Error during recovery task shutdown: {str(e)}")

    recovery_future = getattr(app.state, "recovery_future", None)
    if recovery_future:
        if not recovery_future.done():
            recovery_future.cancel()
        try:
            await asyncio.wrap_future(recovery_future)
        except (asyncio.CancelledError, RuntimeError):
            pass
        except Exception as e:
            logger.error(f"❌ Error during recovery future shutdown: {str(e)}")

    backfill_task = getattr(app.state, "backfill_task", None)
    if backfill_task:
        if not backfill_task.done():
            backfill_task.cancel()
        try:
            await backfill_task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"❌ Error during vector membership backfill shutdown: {str(e)}")

    backfill_future = getattr(app.state, "backfill_future", None)
    if backfill_future:
        if not backfill_future.done():
            backfill_future.cancel()
        try:
            await asyncio.wrap_future(backfill_future)
        except (asyncio.CancelledError, RuntimeError):
            pass
        except Exception as e:
            logger.error(f"❌ Error during vector membership backfill future shutdown: {str(e)}")

    # Stop message consumers; the worker loop stays up for the clients bound to it, closed below.
    try:
        await stop_kafka_consumers(app_container, close_worker_loop=False)
    except Exception as e:
        logger.error(f"❌ Error during application shutdown: {str(e)}")

    # Stop the resource governor's sample loop after consumers (which hold
    # its gates) have drained, so nothing races a limit change mid-shutdown.
    governor.stop()
    governor_task.cancel()
    try:
        await governor_task
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"❌ Error during resource governor shutdown: {str(e)}")
    governor.close()

    try:
        accessible_records_cache = getattr(app.state, "accessible_records_cache", None)
        if accessible_records_cache is not None:
            await accessible_records_cache.close()
            logger.info("✅ Accessible-records cache closed")
    except Exception as e:
        logger.error(f"❌ Error closing accessible-records cache: {e}")

    # Close configuration service (stops Redis Pub/Sub subscription)
    try:
        config_service = app_container.config_service()
        await config_service.close()
    except Exception as e:
        logger.error(f"❌ Error closing configuration service: {e}")

    # After the clients bound to it (the accessible-records cache's, above): LoopLocal can
    # close a resource only on a loop that is still running.
    try:
        await _close_worker_loop(app_container)
    except Exception as e:
        logger.error(f"❌ Error closing the shared worker loop: {e}")

    # Shut down the PDF OCR process-pool (no-op if it was never initialised).
    # atexit registered inside the pool factory is the safety net for unclean
    # exits; this call handles the normal graceful shutdown path.
    try:
        from app.events.events import shutdown_pdf_ocr_pool
        if shutdown_pdf_ocr_pool():
            logger.info("✅ PDF OCR detection process pool shut down")
    except Exception as e:
        logger.error(f"❌ Error shutting down PDF OCR detection pool: {e}")

    try:
        from app.modules.parsers.pdf.pdf_rasterizer import shutdown_pdf_raster_pool
        if shutdown_pdf_raster_pool():
            logger.info("✅ PDF rasterization process pool shut down")
    except Exception as e:
        logger.error(f"❌ Error shutting down PDF rasterization pool: {e}")


from app.api.middlewares.request_context import RequestContextMiddleware
from app.utils.request_context import set_service_suffix

set_service_suffix("-is")

app = FastAPI(
    lifespan=lifespan,
    title="Vector Search API",
    description="API for semantic search and document retrieval with message consumer",
    version="1.0.0",
)

# Trace context — outermost.
app.add_middleware(RequestContextMiddleware)
# Telemetry: metrics middleware + pusher (started/stopped in lifespan).
telemetry = setup_telemetry(app, service_name="indexing_service")


@app.get("/health")
async def health_check(request: Request) -> JSONResponse:
    """Health check endpoint for the indexing service itself"""
    try:
        governor: ResourceGovernor | None = getattr(
            request.app.state, "governor", None
        )
        content: dict[str, Any] = {
            "status": "healthy",
            "timestamp": get_epoch_timestamp_in_ms(),
        }
        if governor is not None:
            try:
                content["resource_governor"] = governor.stats()
            except Exception as stats_error:
                # Observability failure must not fail the liveness probe —
                # the service itself is still healthy.
                content["resource_governor"] = {"error": str(stats_error)}
        # Per-tier dispatch admission: a heavy tier pinned at its ceiling with
        # light idle is attachments queueing on heavy parse, which is fine as
        # long as light keeps moving; both pinned means the node is full.
        dispatch: dict[str, Any] = {}
        for entry in getattr(container, "kafka_consumers", None) or []:
            consumer = entry[1] if len(entry) > 1 else None
            stats = getattr(consumer, "dispatch_stats", None)
            if not callable(stats):
                continue
            try:
                dispatch[str(entry[0])] = stats()
            except Exception as stats_error:
                dispatch[str(entry[0])] = {"error": str(stats_error)}
        if dispatch:
            content["dispatch"] = dispatch
        runtime: PipelineRuntime | None = getattr(request.app.state, "pipeline_runtime", None)
        if runtime is not None:
            try:
                content["stages"] = runtime.stats()
            except Exception as stats_error:
                content["stages"] = {"error": str(stats_error)}
        try:
            content["llm_gateway"] = get_llm_gateway().stats()
        except Exception as stats_error:
            content["llm_gateway"] = {"error": str(stats_error)}
        return JSONResponse(
            status_code=200,
            content=content,
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "status": "unhealthy",
                "error": str(e),
                "timestamp": get_epoch_timestamp_in_ms(),
            },
        )


def run(host: str = "0.0.0.0", port: int = 8091, workers: int | None = None, *, reload: bool = True) -> None:
    """Run the application"""
    import warnings
    workers = workers or max(1, int(os.getenv("INDEXING_UVICORN_WORKERS", "1")))
    if reload and workers > 1:
        warnings.warn(
            "INDEXING_UVICORN_WORKERS>1 is not compatible with reload=True; falling back to 1 worker.",
            RuntimeWarning,
            stacklevel=2,
        )
        workers = 1
    uvicorn.run(
        "app.indexing_main:app",
        host=host,
        port=port,
        log_level="info",
        reload=reload,
        workers=workers,
    )


if __name__ == "__main__":
    run(reload=False)