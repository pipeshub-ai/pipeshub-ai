import asyncio
import inspect
import logging
import os
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from functools import partial
from typing import TYPE_CHECKING, Any, Protocol, TypeVar, cast
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

import app.utils.runtime_threads  # must precede all ML library imports
from app.api.middlewares.request_context import RequestContextMiddleware
from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    EventTypes,
    OriginTypes,
    ProgressStatus,
)
from app.containers.indexing import initialize_container
from app.core.envelope import configure_from_config as configure_envelope
from app.core.org_context import spawn_detached
from app.edition_containers import IndexingAppContainer
from app.edition_services import configure_app
from app.modules.artifacts.retention import RetentionReport, RevisionRetentionSweep
from app.modules.artifacts.store import NodeArtifactStore
from app.modules.indexing.duplicate_reconcile import (
    DuplicateReconciler,
    retry_pending_duplicate_reconciles,
)
from app.modules.indexing.entity_index_rebuild import run_entity_index_rebuild_loop
from app.modules.indexing.lane_upkeep import last_lane_report, run_lane_upkeep
from app.modules.indexing.named_entity_completion import (
    record_consumer_loop,
    run_named_entity_completion_loop,
)
from app.modules.indexing.named_entity_persist_retry import (
    retry_pending_named_entity_persists,
)
from app.modules.indexing.named_entity_sweep import run_named_entity_sweep_loop
from app.modules.indexing.rate_limit_deferral import deferral_due
from app.modules.indexing.record_label_repair import run_record_label_repair_loop
from app.modules.indexing.record_republish import (
    is_parked_duplicate,
    record_event,
    republish_eligible,
)
from app.modules.indexing.recovery_budget import (
    ExhaustedRecordSink,
    record_handback,
    recovery_exhausted,
    recovery_tracking_id,
)
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
from app.services.graph_db.common.record_visibility import (
    RecordVisibility,
    is_live_record,
)
from app.services.graph_db.entity_index_queries import APP_STATUS_DELETING
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
from app.services.messaging.lanes.assignment import lane_assignments_in_use
from app.services.messaging.lanes.backlog import LaneBacklog
from app.services.messaging.messaging_factory import MessagingFactory
from app.services.messaging.utils import MessagingUtils
from app.services.messaging.worker_loop import WorkerLoop
from app.services.redis.connection_provider_factory import uses_pg_state
from app.services.resource_governor import ResourceGovernor
from app.services.resource_governor.shared_slots import shared_slots_directory
from app.telemetry.setup import setup_telemetry
from app.utils.concurrency import indexing_llm_stats
from app.utils.env_utils import uvicorn_worker_healthcheck_timeout
from app.utils.llm import is_local_cpu_embedding_configured
from app.utils.logger import install_log_redaction
from app.utils.loop_lag_monitor import LoopLagMonitor
from app.utils.loop_share import LoopShare
from app.utils.process_hardening import mark_process_non_dumpable
from app.utils.request_context import set_service_suffix
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from app.utils.user_errors import (
    CONNECTOR_OFF,
    CONNECTOR_REMOVED,
    RECOVERY_REQUEUED,
    RECOVERY_RETRY,
)

if TYPE_CHECKING:
    from app.modules.indexing.run import IndexingPipeline
    from app.modules.pipeline.runtime import PipelineRuntime
    from app.services.messaging.interface.producer import IMessagingProducer

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

def _enrichment_cut_short(record: dict[str, Any]) -> bool:
    """Indexed, with an enrichment that has not ended (and nothing else in flight)."""
    return (
        record.get("indexingStatus") == ProgressStatus.COMPLETED.value
        and record.get("extractionStatus") == ProgressStatus.IN_PROGRESS.value
        and record.get("parsingStatus") != ProgressStatus.IN_PROGRESS.value
    )


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
    results = {"requeued": 0, "skipped": 0, "exhausted": 0, "error": 0}
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
        retry_tracker = None
        exhausted_sink: ExhaustedRecordSink | None = None
        read_backlog: Callable[[], Awaitable[LaneBacklog]] | None = None
        consumers = getattr(app_container, "kafka_consumers", [])
        if consumers and len(consumers[0]) > 2:
            retry_producer = consumers[0][2]
            record_consumer = consumers[0][1]
            retry_tracker = getattr(record_consumer, "retry_manager", None)
            # The record handler, which the consumer reports abandoned messages to.
            sink = getattr(record_consumer, "disposition_sink", None)
            if callable(getattr(sink, "on_recovery_exhausted", None)):
                exhausted_sink = sink
            lane_backlog = getattr(record_consumer, "lane_backlog", None)
            if lane_backlog is not None:
                # Not through run_coordination: the consumer hops to the loop
                # that owns its broker client by itself, with its own timeout.
                # Read at most once per pass, shared by the sweep and the lane
                # upkeep below.
                read_backlog = _read_once(partial(lane_backlog, Topic.RECORD_EVENTS.value))
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

        async def process_single_record(
            record: dict[str, Any], *, resume_enrichment: bool = False
        ) -> bool | None:
            """Reset one stuck record and re-queue it, with semaphore control.

            ``resume_enrichment``: the record is indexed and only its enrichment
            was cut short. It stays searchable; the republished event re-runs it
            (the handler does not skip an indexed record whose enrichment is
            IN_PROGRESS), and its completion promotes its queued duplicates.
            """
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
                    if latest_record is None:
                        still_stuck = False
                    elif resume_enrichment:
                        # Stale again, not merely cut short: a redelivery that
                        # is enriching it now has refreshed processingStartedAt.
                        still_stuck = _enrichment_cut_short(latest_record) and is_stale(latest_record)
                    else:
                        still_stuck = (
                            latest_record.get("indexingStatus") == ProgressStatus.IN_PROGRESS.value
                            or latest_record.get("parsingStatus") == ProgressStatus.IN_PROGRESS.value
                        )
                    if not still_stuck:
                        results["skipped"] += 1
                        return True
                    record = latest_record
                    record_name = record.get("recordName", "Unknown")

                    logger.debug(
                        f"🔄 Recovering stale record: {record_name} (ID: {record_id})"
                    )

                    # Check if connector is disabled or deleted. Not for a
                    # resumed enrichment: the record is searchable, and these
                    # branches would turn it AUTO_INDEX_OFF and strand its
                    # QUEUED copies. The republished event reaches the
                    # handler, which ends the enrichment and releases them.
                    connector_id = record.get("connectorId")
                    origin = record.get("origin")
                    if connector_id and origin == OriginTypes.CONNECTOR.value and not resume_enrichment:
                        # A failed read must not look like a deleted connector;
                        # raising leaves the row for the next pass.
                        connector_instance = await graph_provider.get_document(
                            connector_id, CollectionNames.APPS.value, raise_on_error=True
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
                                    "reason": CONNECTOR_REMOVED,
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
                                    "reason": CONNECTOR_OFF,
                                },
                            )
                            results["skipped"] += 1
                            return True

                    tracking_id = recovery_tracking_id(record_id, record.get("version", 0))
                    # Without a sink to end it (and move its queued copies on),
                    # the record is recovered with no budget, as before.
                    if exhausted_sink is not None and await recovery_exhausted(
                        retry_tracker, tracking_id, logger
                    ):
                        await exhausted_sink.on_recovery_exhausted(
                            record_id, resume_enrichment=resume_enrichment
                        )
                        logger.warning(
                            "Gave up recovering record %s (%s): it kept failing or "
                            "being cut off after it was re-queued",
                            record_id,
                            record_name,
                        )
                        results["exhausted"] += 1
                        return True

                    # Reconstruct the payload from the record data. With the
                    # connector id the event goes to its connector's own lane;
                    # without it every recovered record shared the default lane.
                    payload = {
                        "recordId": record_id,
                        "recordName": record.get("recordName"),
                        "orgId": record.get("orgId"),
                        "version": record.get("version", 0),
                        "connectorName": record.get("connectorName", Connectors.KNOWLEDGE_BASE.value),
                        "connectorId": record.get("connectorId"),
                        "extension": record.get("extension"),
                        "mimeType": record.get("mimeType"),
                        "origin": record.get("origin"),
                        "recordType": record.get("recordType"),
                        "virtualRecordId": record.get("virtualRecordId"),
                        # Failures of every recovery of this version share one budget.
                        "_retry_tracking_id": tracking_id,
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

                    if resume_enrichment:
                        # A fresh start time keeps the next scans off it while
                        # the event waits; if the event is lost it ages out again.
                        reset_fields = {
                            "processingStartedAt": get_epoch_timestamp_in_ms(),
                            "reason": RECOVERY_REQUEUED,
                        }
                    else:
                        reset_fields = {
                            "parsingStatus": ProgressStatus.NOT_STARTED.value,
                            "indexingStatus": ProgressStatus.QUEUED.value,
                            "queuedAtTimestamp": get_epoch_timestamp_in_ms(),
                            "extractionStatus": ProgressStatus.NOT_STARTED.value,
                            "processingStartedAt": None,
                            "reason": RECOVERY_REQUEUED,
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
                        # Counted here, not after the status write: an event
                        # in the broker is a hand-back even if that write fails.
                        await record_handback(retry_tracker, tracking_id, logger)

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
                                    "reason": RECOVERY_RETRY,
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

        for status_field in ("indexingStatus", "parsingStatus", "extractionStatus"):
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

                resume_enrichment = status_field == "extractionStatus"
                candidates = [
                    record
                    for record in page
                    if is_stale(record)
                    and not (
                        status_field == "parsingStatus"
                        and record.get("indexingStatus")
                        == ProgressStatus.IN_PROGRESS.value
                    )
                    and (not resume_enrichment or _enrichment_cut_short(record))
                ]

                outcomes = await asyncio.gather(
                    *(
                        process_single_record(record, resume_enrichment=resume_enrichment)
                        for record in candidates
                    )
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
            read_backlog=read_backlog,
        )

        await _upkeep_lanes(
            graph_provider=graph_provider,
            read_backlog=read_backlog,
            logger=logger,
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

        # A primary whose handler could not copy its taxonomy to promoted
        # duplicates keeps duplicateReconcilePending; a redelivery finds
        # nothing QUEUED, so nothing else would retry it.
        try:
            event_processor = app_container.event_processor()
            if inspect.isawaitable(event_processor):
                event_processor = await event_processor
            total_records += await retry_pending_duplicate_reconciles(
                graph_provider=graph_provider,
                reconciler=DuplicateReconciler(
                    graph_provider=graph_provider,
                    sink=getattr(event_processor, "sink_orchestrator", None),
                    sync_vector_membership=event_processor.sync_vector_membership,
                    logger=logger,
                ),
                logger=logger,
                page_size=page_size,
            )
        except Exception as exc:
            logger.warning(f"Duplicate reconcile retry skipped this pass: {exc}")

        # A named-entity graph write that failed after its own retries left the
        # record's last good entities in place and a retry marker for this pass.
        try:
            event_processor = app_container.event_processor()
            if inspect.isawaitable(event_processor):
                event_processor = await event_processor
            total_records += await retry_pending_named_entity_persists(
                graph_provider=graph_provider,
                sink=getattr(event_processor, "sink_orchestrator", None),
                logger=logger,
                page_size=page_size,
                concurrency_manager=concurrency_manager,
            )
        except Exception as exc:
            logger.warning(f"Named-entity persist retry skipped this pass: {exc}")
        try:
            total_records += await _sweep_pipeline_stages(app_container, concurrency_manager, logger)
        except Exception as exc:
            logger.warning(f"Pipeline stage sweep skipped this pass: {exc}")
        total_records += await _sweep_retired_revisions(
            app_container=app_container,
            graph_provider=graph_provider,
            logger=logger,
            lease_held=lambda: recovery_renewal_task is None or not recovery_renewal_task.done(),
            concurrency_manager=concurrency_manager,
        )
        total_records += await _sweep_stored_records(
            app_container=app_container,
            graph_provider=graph_provider,
            logger=logger,
            lease_held=lambda: recovery_renewal_task is None or not recovery_renewal_task.done(),
        )

        if total_records == 0:
            logger.debug("No stale in-progress records to recover")
            return

        logger.info(
            f"✅ Recovery complete. Processed {total_records} stale record(s): "
            f"{results['requeued']} re-queued, {results['skipped']} skipped, "
            f"{results['exhausted']} given up, {results['error']} errors"
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


def _read_once(read: Callable[[], Awaitable[_T]]) -> Callable[[], Awaitable[_T]]:
    """``read``, run on first call only; later calls get the same answer or error."""
    outcome: list[tuple[_T | None, BaseException | None]] = []

    async def once() -> _T:
        if not outcome:
            try:
                outcome.append((await read(), None))
            except Exception as e:
                outcome.append((None, e))
        value, error = outcome[0]
        if error is not None:
            raise error
        return value  # type: ignore[return-value]

    return once


async def _upkeep_lanes(
    *,
    graph_provider: IGraphDBProvider,
    read_backlog: Callable[[], Awaitable[LaneBacklog]] | None,
    logger: logging.Logger,
) -> None:
    """Keep the Redis Streams lane map in step, when producers place by it.

    Never fails the recovery pass: the map only steers placement, and the
    next pass tries again.
    """
    assignments = lane_assignments_in_use(Topic.RECORD_EVENTS.value)
    if assignments is None:
        return
    backlog: LaneBacklog | None = None
    if read_backlog is not None:
        try:
            backlog = await read_backlog()
        except Exception as e:
            # The sweep has already said so; upkeep only skips what needs it.
            logger.debug("Lane upkeep runs without the backlog this pass: %s", e)
    try:
        await run_lane_upkeep(
            assignments=assignments,
            graph_provider=graph_provider,
            backlog=backlog,
            logger=logger,
        )
    except Exception as e:
        logger.warning(
            "Queue lane upkeep failed this pass; it runs again next minute: %s: %s",
            type(e).__name__,
            e,
        )


# How much of virtualRecordToDocIdMapping one recovery tick will walk. Bounded
# because orphans are rare and every page costs a graph read; the cursor below
# carries the position forward so successive ticks cover the rest.
ORPHAN_SCAN_MAX_PAGES_PER_TICK = 4
# Between pages, while the indexing loop is busy (see LoopShare).
ORPHAN_SCAN_MAX_PAUSE_SECONDS = 5.0
_orphan_sweep_cursor = 0



# Stage sweep thresholds: a claim unpublished this long is re-sent; a running job untouched
# this long (past the longest stage budget plus a lease term) is reclaimed once its lease is
# found free, or on age alone without distributed concurrency.
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
        in_progress_older_than_ms=_STAGE_IN_PROGRESS_SWEEP_MS,
        is_job_active=is_job_active,
    )
    if report.republished or report.reclaimed:
        logger.info(
            "Pipeline stage sweep: %d re-published, %d reclaimed of %d scanned",
            report.republished, report.reclaimed, report.scanned,
        )
    return report.republished + report.reclaimed

# Pages of virtualRecordToDocIdMapping one recovery tick retires revisions over.
# Nothing here is urgent (the grace alone is a day), so a pass is spread over
# many ticks; the cursor carries it forward and restarts at the top at the end.
RETENTION_MAX_PAGES_PER_TICK = 4
_retention_cursor: str | None = None


async def _sweep_retired_revisions(
    *,
    app_container: IndexingAppContainer,
    graph_provider: IGraphDBProvider,
    logger: logging.Logger,
    lease_held: Callable[[], bool],
    concurrency_manager: IDistributedLeaseManager | None = None,
) -> int:
    """Retire artifact sets and stage states of revisions nothing reads any more.

    Under the recovery lease: stops at the next page or delete once it is lost,
    leaving the cursor on the page it had not finished. Each virtual record is
    retired under its holders' record leases, which a re-index holds.
    """
    global _retention_cursor  # noqa: PLW0603 - the cursor is per process by design

    grace_seconds = messaging_env.revision_retention_grace_seconds
    if grace_seconds <= 0:
        return 0
    try:
        blob_storage = app_container.blob_storage()
        if inspect.isawaitable(blob_storage):
            blob_storage = await blob_storage
    except Exception as exc:
        logger.warning("Blob storage unavailable for revision retention: %s", exc)
        return 0
    sweep = RevisionRetentionSweep(
        graph_provider,
        NodeArtifactStore(blob_storage, logger),
        logger,
        grace_ms=int(grace_seconds * 1000),
        leases=concurrency_manager,
    )
    total = RetentionReport()
    budget = messaging_env.revision_retention_tick_budget_seconds
    deadline = time.monotonic() + budget if budget > 0 else None
    pacer = LoopShare(max_pause_seconds=ORPHAN_SCAN_MAX_PAUSE_SECONDS)
    for _ in range(RETENTION_MAX_PAGES_PER_TICK):
        if not lease_held() or (deadline is not None and time.monotonic() >= deadline):
            break
        try:
            next_key, report = await sweep.run_page(
                _retention_cursor, still_leader=lease_held, deadline=deadline
            )
        except Exception as exc:
            logger.warning("Revision retention skipped the rest of this pass: %s: %s", type(exc).__name__, exc)
            break
        total.add(report)
        if report.interrupted:
            break
        _retention_cursor = next_key
        if next_key is None or report.cut_short:
            break
        # A pause can last ORPHAN_SCAN_MAX_PAUSE_SECONDS; do not sleep through the deadline.
        if deadline is not None and deadline - time.monotonic() <= ORPHAN_SCAN_MAX_PAUSE_SECONDS:
            break
        await pacer.pause()
    if total.cut_short:
        logger.info(
            "Revision retention stopped early this pass (time budget or failures in a row); "
            "the next pass goes on from there"
        )
    if total.removed or total.failed:
        logger.info(
            "Revision retention: %d revision(s) and %d stage state(s) removed, %d revision(s) kept, "
            "%d virtual record(s) left whole for the trash, %d left for the next pass while a record "
            "was busy, %d failure(s), of %d scanned",
            total.revisions_deleted, total.stage_states_deleted, total.revisions_kept,
            total.skipped_trashed, total.skipped_busy, total.failed, total.scanned,
        )
    return total.removed


_stored_retention_cursor: str | None = None


async def _sweep_stored_records(
    *,
    app_container: IndexingAppContainer,
    graph_provider: IGraphDBProvider,
    logger: logging.Logger,
    lease_held: Callable[[], bool],
) -> int:
    """Purge the documents stored writes replaced and the strays nothing names (see ``stored_retention``).

    Runs whether the flags are on or off, and regardless of revision retention. Pauses while the
    membership result is stale or lists a member that does not follow the stored-write protocol.
    """
    global _stored_retention_cursor  # noqa: PLW0603 - the cursor is per process by design

    from app.modules.artifacts.stored_retention import (
        StoredRecordRetention,
        StoredRetentionReport,
    )
    from app.services.messaging.membership import MembershipVerdict, reader_for

    try:
        blob_storage = app_container.blob_storage()
        if inspect.isawaitable(blob_storage):
            blob_storage = await blob_storage
    except Exception as exc:
        logger.warning("Blob storage unavailable for stored-record retention: %s", exc)
        return 0
    reader = reader_for(app_container.config_service())

    async def membership() -> MembershipVerdict:
        return await reader.verdict() if reader is not None else MembershipVerdict(False, (), "stale")

    sweep = StoredRecordRetention(graph_provider, blob_storage, logger, membership=membership)
    total = StoredRetentionReport()
    for _ in range(RETENTION_MAX_PAGES_PER_TICK):
        if not lease_held():
            break
        try:
            next_key, report = await sweep.run_page(_stored_retention_cursor, still_leader=lease_held)
        except Exception as exc:
            logger.warning("Stored-record retention skipped the rest of this pass: %s: %s", type(exc).__name__, exc)
            break
        total.add(report)
        if report.interrupted or report.paused:
            break
        _stored_retention_cursor = next_key
        if next_key is None:
            break
    if total.paused:
        logger.warning(
            "Stored-record retention paused: an indexing member does not follow the stored-write protocol, "
            "or the membership check is not available",
        )
    if total.purged or total.strays_purged or total.failed:
        logger.info(
            "Stored-record retention: %d replaced document(s) and %d stray(s) purged, %d marked, %d held for "
            "want of a retire route, %d mapping(s) left for a recent older-writer write, %d failure(s), of %d scanned",
            total.purged, total.strays_purged, total.marked, total.held_no_fence, total.fingerprints,
            total.failed, total.scanned,
        )
    return total.purged + total.strays_purged


async def _sweep_orphaned_virtual_record_mappings(
    *,
    graph_provider: IGraphDBProvider,
    pipeline: "IndexingPipeline",
    logger: logging.Logger,
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

    Orphans are rare but the mapping collection is large, and each page costs a
    graph read, so a full scan per tick would be standing work for almost no
    yield. Instead each tick walks a bounded slice and leaves the cursor where it
    stopped, wrapping at the end: the collection is covered over many ticks
    rather than all at once. The cursor is per-process and resets on restart —
    acceptable, since nothing here is required to be timely.
    """
    global _orphan_sweep_cursor  # noqa: PLW0603 - the cursor is per process by design

    swept = 0
    offset = _orphan_sweep_cursor
    pages_scanned = 0
    pacer = LoopShare(max_pause_seconds=ORPHAN_SCAN_MAX_PAUSE_SECONDS)
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
        vrids = [
            vrid
            for mapping in page
            if isinstance(vrid := mapping.get("_key") or mapping.get("id"), str) and vrid
        ]
        try:
            # One read per page, not one per mapping: the answer is the same,
            # and on Neo4j each read is a millisecond of the indexing loop.
            # Raising, not answering empty: an empty answer would read as "no
            # records reference these" and clean them all up.
            in_use = await graph_provider.get_virtual_record_ids_in_use(vrids)
            unreferenced = [vrid for vrid in vrids if vrid not in in_use]
            # Content still held by a record in the trash is the purge's to
            # remove; releasing it here would delete a restorable record's
            # stored content ahead of time.
            trashed = (
                await graph_provider.get_virtual_record_ids_in_use(
                    unreferenced, visibility=RecordVisibility.DELETED
                )
                if unreferenced
                else set()
            )
        except Exception as exc:
            logger.warning(
                "Could not check %d virtual record(s) for orphaned vectors: %s",
                len(vrids),
                exc,
            )
            unreferenced, trashed = [], set()
        for vrid in unreferenced:
            if vrid in trashed:
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
        await pacer.pause()

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
    graph_provider: IGraphDBProvider,
    logger: logging.Logger,
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
    for `is_stale`'s cutoff — one lease interval with a concurrency manager,
    else stale_recovery_after_seconds (~122 min) — which is right for a
    pipeline that might still finish and wrong here: the connector has already
    been popped from connectors_map, so nothing in flight can succeed. One lease
    interval is enough to be sure no worker still owns the row.
    """
    swept = 0
    # None means "could not read this pass" -- recorded so the lookup is not
    # repeated, but never mistaken for "gone". Rebuilt on the next tick.
    connector_active: dict[str, bool | None] = {}
    in_progress_cutoff_ms = get_epoch_timestamp_in_ms() - int(
        messaging_env.concurrency_lease_seconds * 1000
    )

    async def _is_inactive(connector_id: str) -> bool:
        if connector_id not in connector_active:
            try:
                # `raise_on_error`, because saying yes here parks the record as
                # AUTO_INDEX_OFF. Without it an unreadable graph answers None,
                # which is the same answer a deleted connector gives -- so a
                # restart would take healthy records out of indexing for good,
                # under a status that reads as somebody's deliberate setting.
                instance = await graph_provider.get_document(
                    connector_id, CollectionNames.APPS.value, raise_on_error=True
                )
            except Exception as e:
                # Remembered as unreadable for the rest of this pass, not
                # retried per record: on Neo4j a restart surfaces only after the
                # 30s connection-acquisition timeout, so asking again for each
                # of a hundred records would hold recovery for the best part of
                # an hour while hammering the database trying to come back.
                logger.warning(
                    "Could not read connector %s, so leaving its records alone "
                    "this pass rather than parking them: %s", connector_id, e
                )
                connector_active[connector_id] = None
            else:
                # A missing instance counts as inactive: its records can never
                # be indexed again.
                connector_active[connector_id] = bool(
                    instance and instance.get("isActive", False)
                )
        # Unreadable is not inactive, so nothing is parked on a failed read.
        return connector_active[connector_id] is False

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
                            "reason": CONNECTOR_OFF,
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


# After each re-send of the same record the wait before the next one doubles,
# up to this (or the configured minimum age, if that is longer).
STRANDED_REPUBLISH_BACKOFF_CAP_SECONDS = 24 * 3600.0
# How far a record's queue time and the broker's timestamp for its event may
# disagree: the two are stamped by different hosts, and the record is stamped
# before its event is sent.
STRANDED_QUEUE_CLOCK_ALLOWANCE_MS = 5 * 60 * 1000


def _republishes_since_queued(record: dict[str, Any], enabled_at: object = None) -> int:
    """How many times the sweep has re-sent this record since it was last put in line.

    ``enabled_at`` is when the connector of a record parked while it was off
    was turned on again, which puts the record in line again without moving
    its queuedAtTimestamp.
    """
    try:
        last_republished_at = float(record.get("lastRepublishedAt"))
    except (TypeError, ValueError):
        return 0
    for put_in_line_at in (record.get("queuedAtTimestamp"), enabled_at):
        try:
            if float(put_in_line_at) > last_republished_at:
                # Put in line again since: an earlier wait's re-sends say
                # nothing about this one.
                return 0
        except (TypeError, ValueError):
            continue
    try:
        count = int(record.get("republishCount") or 0)
    except (TypeError, ValueError):
        count = 0
    # A row re-sent before the counter existed has still been re-sent once.
    return max(1, count)


def _republish_wait_ms(after_seconds: float, republishes: int) -> float:
    cap = max(after_seconds, STRANDED_REPUBLISH_BACKOFF_CAP_SECONDS)
    # The exponent is clamped only so a corrupt counter cannot overflow.
    return min(after_seconds * 2 ** min(republishes, 32), cap) * 1000


async def _republish_stranded_records(
    *,
    graph_provider: IGraphDBProvider,
    logger: logging.Logger,
    producer: "IMessagingProducer",
    run_coordination: CoordinationRunner,
    concurrency_manager: DistributedConcurrencyManager | None,
    page_size: int,
    read_backlog: Callable[[], Awaitable[LaneBacklog]] | None = None,
) -> int:
    """Re-publish records that have been waiting on an event that never came.

    The sweep above only rescues rows on connectors that are gone; the main
    stale scan only looks at IN_PROGRESS. A row on a *live* connector whose
    event was lost — dropped by the broker, discarded by a consumer, or never
    published because the send failed after the transaction committed — is
    reachable by neither, so it waits for ever.

    The status cannot say which rows those are: QUEUED is written both by the
    upsert that precedes publishing and by the publish itself. Age alone
    cannot either, because a record queued behind a long backlog is just as
    old as one whose event was lost, and re-sending it lengthens the very
    backlog it is waiting behind. So the broker is asked (``read_backlog``,
    once per pass, and only if some record is old enough to need it): a record
    is left alone while a lane its event could be on still holds work at least
    as old as the record's own queue time, and re-sent once that lane has moved
    past it with the record still waiting.

    STRANDED_RECORD_REPUBLISH_AFTER_SECONDS is the minimum age before a record
    is looked at at all (one hour by default; 0 disables the sweep). After each
    re-send the wait before the next doubles, so a record is never re-sent
    every period for ever -- which is also the whole of the rule on a pass
    where the broker cannot be read.

    Re-publishing is safe to repeat: the handler skips a record that is already
    COMPLETED, and the per-record exclusivity lease stops a republished event
    racing one already in flight.

    Also walked: AUTO_INDEX_OFF with reason CONNECTOR_OFF, parked while its
    connector was off, on a connector that is on again. Turning it on re-queues
    these at once (EntityEventService._requeue_records_parked_while_off); this
    is the net for what that single pass missed -- a refused send, a pass that
    died -- and for a record parked just after it ran. Such a record keeps its
    status here, as the others do, so the same age and back-off bound it, and
    its age also counts from the connector's enabledAtTimestamp, so nothing is
    sent from here while that re-queue may still be sending it.

    It also ends the wait of a record the handler put back in line because its
    source rate-limited it (``retryNotBefore``, see
    ``modules/indexing/rate_limit_deferral.py``): that record is sent again as
    soon as the time has passed, and not before.
    """
    after_seconds = messaging_env.stranded_record_republish_after_seconds
    if after_seconds <= 0:
        return 0

    pass_started_ms = get_epoch_timestamp_in_ms()
    cutoff_ms = pass_started_ms - int(after_seconds * 1000)
    # None means "could not read this pass": see the sibling sweep above.
    connector_active: dict[str, bool | None] = {}
    republished = 0
    considered = 0
    still_queued = 0
    backed_off = 0
    rate_limited_waiting = 0
    rate_limited_resent = 0
    backlog: LaneBacklog | None = None
    backlog_read = False

    async def _lane_backlog() -> LaneBacklog | None:
        """The broker's view, read on first use; None if it cannot be read this pass."""
        nonlocal backlog, backlog_read
        if not backlog_read:
            backlog_read = True
            if read_backlog is not None:
                try:
                    backlog = await read_backlog()
                except Exception as e:
                    # The type as well: a timeout's own message is empty.
                    logger.warning(
                        "Could not read the indexing queue's backlog, so this "
                        "pass decides on age and back-off alone: %s: %s",
                        type(e).__name__, e,
                    )
        return backlog

    async def _is_active(connector_id: str) -> bool:
        if connector_id not in connector_active:
            try:
                instance = await graph_provider.get_document(
                    connector_id, CollectionNames.APPS.value, raise_on_error=True
                )
            except Exception as e:
                # Remembered for this pass rather than retried per record, for
                # the same reason as the sweep above.
                logger.warning(
                    "Could not read connector %s, so leaving its records for the "
                    "next pass: %s", connector_id, e
                )
                connector_active[connector_id] = None
            else:
                # A connector or knowledge base being deleted owns its records' fate: a
                # re-sent event would index content the delete is about to remove.
                connector_active[connector_id] = bool(
                    instance
                    and instance.get("isActive", False)
                    and instance.get("status") != APP_STATUS_DELETING
                )
        # Unreadable is not active either: the record waits for the next pass.
        return connector_active[connector_id] is True

    async def _resend(
        record: dict[str, Any],
        record_key: str,
        event_type: str,
        payload: dict[str, object],
        *,
        claim: dict[str, Any],
        undo: dict[str, Any],
    ) -> bool:
        """Send the record's event again under its lease; True once it is sent."""
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
                    return False

            # The marker is a durable claim written BEFORE the send, not
            # a receipt written after it. Written after, a Neo4j failure
            # following a successful Redis send left the record eligible
            # again next tick -- one duplicate event per minute, per
            # record, for as long as the consumer had not yet moved it
            # out of QUEUED/NOT_STARTED. Under a backlog that is a
            # feedback loop: every tick inflates the very backlog that
            # is delaying the consumer. Claiming first bounds it: if the
            # claim cannot be persisted, nothing is sent this tick.
            # Deliberately not updatedAtTimestamp: that field means
            # "when the record last changed" and connectors write it, so
            # a recovery sweep must not move it.
            claimed = await graph_provider.update_node(
                record_key, CollectionNames.RECORDS.value, claim
            )
            if not claimed:
                logger.error(
                    "Could not record a republish claim for stranded "
                    "record %s; skipping it this tick rather than risk "
                    "re-sending it every tick",
                    record_key,
                )
                return False

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
                # The claim is already persisted, so without this the
                # record would wait out a back-off for a send that
                # never happened. Putting back what was there (best
                # effort) lets the next tick retry; if even that fails
                # the record still only waits one back-off -- bounded
                # either way.
                try:
                    await graph_provider.update_node(
                        record_key, CollectionNames.RECORDS.value, undo
                    )
                except Exception as clear_exc:
                    logger.warning(
                        "Could not undo republish claim for %s after a "
                        "failed send; it will retry after its back-off: %s",
                        record_key,
                        clear_exc,
                    )
                raise
            return True
        except Exception as exc:
            logger.error(
                "Failed to re-publish stranded record %s: %s",
                record_key,
                exc,
            )
            return False
        finally:
            if lock_held and concurrency_manager is not None:
                try:
                    await concurrency_manager.release(record_pool, record_owner)
                except Exception as release_exc:
                    logger.warning(
                        "Failed to release stranded-record lease for %s: %s",
                        record_key,
                        release_exc,
                    )

    async def _pages_in_status(status_value: str) -> AsyncGenerator[list[dict], None]:
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
                return
            yield page
            if len(page) < page_size:
                return
            # Republished rows keep their status, so the filtered result does
            # not shrink under the cursor — advance over the whole page.
            offset += len(page)

    # Per connector that is on: when it was last turned on.
    enabled_at: dict[str, object] = {}

    async def _pages_parked_while_off() -> AsyncGenerator[list[dict], None]:
        """Per connector that is on, so neither a paused connector's parked
        backlog nor a manual-indexing connector's AUTO_INDEX_OFF records
        (no reason) is read on every pass; each read is a seek in the
        (connectorId, indexingStatus, reason, key) index."""
        offset = 0
        while True:
            apps = await graph_provider.get_documents_paginated(
                CollectionNames.APPS.value,
                skip=offset,
                limit=page_size,
                filters={"isActive": True},
                sort_field="_key",
                raise_on_error=False,
            )
            for app in apps:
                if key := app.get("_key") or app.get("id"):
                    enabled_at[key] = app.get("enabledAtTimestamp")
            if len(apps) < page_size:
                break
            offset += len(apps)
        for connector_id in list(enabled_at):
            # "" rather than None, so the first page has the key predicate
            # too: Neo4j uses a composite index only when every property in
            # it has one.
            after_key = ""
            while True:
                page = await graph_provider.get_documents_paginated(
                    CollectionNames.RECORDS.value,
                    limit=page_size,
                    # Only the turn-off's reason: AUTO_INDEX_OFF from a
                    # connector's indexing filters has none.
                    filters={
                        "connectorId": connector_id,
                        "indexingStatus": ProgressStatus.AUTO_INDEX_OFF.value,
                        "reason": CONNECTOR_OFF,
                    },
                    sort_field="_key",
                    raise_on_error=False,
                    after_key=after_key,
                )
                if not page:
                    break
                yield page
                after_key = page[-1].get("_key") or page[-1].get("id")
                if len(page) < page_size or not after_key:
                    break

    for status_value, pages in (
        (ProgressStatus.QUEUED.value, _pages_in_status(ProgressStatus.QUEUED.value)),
        (ProgressStatus.NOT_STARTED.value, _pages_in_status(ProgressStatus.NOT_STARTED.value)),
        (ProgressStatus.AUTO_INDEX_OFF.value, _pages_parked_while_off()),
    ):
        parked_while_off = status_value == ProgressStatus.AUTO_INDEX_OFF.value
        async for page in pages:
            for record in page:
                record_key = record.get("_key") or record.get("id")
                connector_id = record.get("connectorId")
                if parked_while_off and not is_live_record(record):
                    continue
                # Uploads are otherwise left alone: a new one waits on its file
                # reaching storage. A restored file's content is already there,
                # and its restore was the only thing that would have queued it;
                # so is a pushed record's (see republish_eligible).
                eligible, restored_upload = republish_eligible(record, status_value)
                if (
                    not record_key
                    or not connector_id
                    or not eligible
                    # A stub for an ancestor that was never synced: nothing to
                    # index, and the connector refuses to stream it.
                    or record.get("isPlaceholder")
                ):
                    continue

                # Put back in line by the handler because its source
                # rate-limited it. Its event was acknowledged then, so neither
                # the age, the back-off nor the queue's backlog says anything
                # about it: the not-before time is the whole rule. A record
                # parked while its connector was off since then keeps the
                # parked rules below (its turn-on clock), even if the wait's
                # fields are still on it.
                due = None if parked_while_off else deferral_due(record, pass_started_ms)
                if due is not None:
                    if not due:
                        rate_limited_waiting += 1
                    elif await _is_active(connector_id):
                        event_type, payload = record_event(
                            record,
                            record_key=record_key,
                            connector_id=connector_id,
                            restored_upload=restored_upload,
                        )
                        if await _resend(
                            record,
                            record_key,
                            event_type,
                            payload,
                            claim={"lastRepublishedAt": get_epoch_timestamp_in_ms()},
                            undo={"lastRepublishedAt": record.get("lastRepublishedAt")},
                        ):
                            rate_limited_resent += 1
                    continue

                # Aged from the newest of three clocks, so any one being fresh
                # keeps the row out. queuedAtTimestamp is the platform's own
                # "put in line for indexing" time. updatedAt cannot carry this
                # alone: connectors may fill it with source-system time (a Jira
                # issue last edited a year ago), which made every freshly synced
                # row look stranded and sent it twice; it stays in the max for
                # rows written before queuedAtTimestamp existed.
                # lastRepublishedAt is our own: publishing changes nothing about
                # the row, so without it a record stays eligible and every tick
                # sends another copy of the same event -- worst precisely when
                # the consumer is backlogged, which is the case this sweep
                # exists for.
                # A record parked while its connector was off is also aged
                # from when the connector was turned on again: until then the
                # appEnabled re-queue is sending it, and a copy from here
                # would reach the consumer twice. Its own clocks are of the
                # park, often hours older.
                clocks: list[float] = []
                for value in (
                    record.get("queuedAtTimestamp"),
                    record.get("updatedAtTimestamp") or record.get("createdAtTimestamp"),
                    record.get("lastRepublishedAt"),
                    enabled_at.get(connector_id) if parked_while_off else None,
                ):
                    try:
                        clocks.append(float(value))
                    except (TypeError, ValueError):
                        # Unset or malformed: one bad clock must not hide the others.
                        continue
                if not clocks:
                    continue
                last_touched_at = max(clocks)
                if last_touched_at > cutoff_ms:
                    continue

                if not await _is_active(connector_id):
                    # The sweep above owns these; moving them here would race it.
                    continue

                # A parked copy is released by its twin's completion only while
                # QUEUED; one parked while its connector was off never is.
                if not parked_while_off and is_parked_duplicate(record, restored_upload=restored_upload):
                    continue

                considered += 1
                republishes = _republishes_since_queued(
                    record, enabled_at.get(connector_id) if parked_while_off else None
                )
                if pass_started_ms - last_touched_at < _republish_wait_ms(after_seconds, republishes):
                    backed_off += 1
                    continue

                event_type, payload = record_event(
                    record,
                    record_key=record_key,
                    connector_id=connector_id,
                    restored_upload=restored_upload,
                )

                # Older work still on a lane this record's event could be on
                # means the consumer has not got as far as that event yet. A
                # lane the consumer has paused, or whose head is buffered or in
                # flight, counts: the broker reports what is not finished, not
                # what is not delivered.
                lanes = await _lane_backlog()
                if lanes is not None:
                    oldest_waiting = lanes.oldest_waiting_for(payload)
                    if (
                        oldest_waiting is not None
                        and oldest_waiting <= last_touched_at + STRANDED_QUEUE_CLOCK_ALLOWANCE_MS
                    ):
                        still_queued += 1
                        continue

                if await _resend(
                    record,
                    record_key,
                    event_type,
                    payload,
                    claim={
                        "lastRepublishedAt": get_epoch_timestamp_in_ms(),
                        "republishCount": republishes + 1,
                    },
                    undo={
                        "lastRepublishedAt": record.get("lastRepublishedAt"),
                        "republishCount": republishes,
                    },
                ):
                    republished += 1
                    logger.warning(
                        "Re-published stranded record %s (%s, status %s, "
                        "no consumer picked it up for %.0fs)",
                        record_key,
                        record.get("recordName"),
                        status_value,
                        (get_epoch_timestamp_in_ms() - last_touched_at) / 1000,
                    )

    decided_without_the_queue = backlog_read and backlog is None
    logger.log(
        logging.INFO if considered else logging.DEBUG,
        "Stranded-record sweep: %d considered, %d left alone because their "
        "queue still holds older work, %d re-published, %d waiting out a "
        "back-off%s",
        considered,
        still_queued,
        republished,
        backed_off,
        " (queue not readable; decided on age alone)" if decided_without_the_queue else "",
    )
    if rate_limited_waiting or rate_limited_resent:
        logger.info(
            "Stranded-record sweep: %d record(s) waiting out their source's rate "
            "limit, %d re-sent now that it has passed",
            rate_limited_waiting,
            rate_limited_resent,
        )
    return republished + rate_limited_resent


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

    # On its own timer, so a slow lap or a paused sweep never makes the published result stale.
    membership_task = spawn_detached(run_membership_publisher(app_container))
    try:
        while True:
            await recover_in_progress_records(app_container, graph_provider)
            await asyncio.sleep(
                max(1.0, messaging_env.stale_recovery_interval_seconds)
            )
    finally:
        membership_task.cancel()
        await asyncio.gather(membership_task, return_exceptions=True)


async def run_membership_publisher(app_container: IndexingAppContainer) -> None:
    """Publish which members of this service's consumer groups follow the stored-write protocol.

    The check runs on its own 30 s timer, apart from the recovery lap and every sweep, under the
    cluster-wide ``artifact-membership`` lease (one checker at a time); each writer has its own
    key, so a briefly doubled leader or an ex-leader cannot hide a finding.
    """
    from app.services.messaging import membership as membership_module
    from app.services.messaging.config import MessageBrokerType
    from app.services.redis.config import RedisConnectionConfig
    from app.services.redis.connection_provider_factory import get_redis_provider

    logger = app_container.logger()
    config_service = app_container.config_service()
    broker_type = get_message_broker_type()
    if broker_type == MessageBrokerType.POSTGRES:
        # Its consumers register no group, so there is nothing to check. Nothing is published, which
        # is the "stale" result: artifact readers stay on the stored record and retention stays paused.
        logger.info(
            "Indexing membership is not published for the %s broker: artifact reads stay on the stored record",
            broker_type.value,
        )
        return
    owner = f"membership:{uuid4().hex[:12]}"
    lease_seconds = max(90.0, 3 * membership_module.CHECK_PERIOD_SECONDS)

    def manager() -> Any:  # noqa: ANN401
        consumers = getattr(app_container, "kafka_consumers", [])
        return getattr(consumers[0][1], "concurrency_manager", None) if consumers else None

    async def is_leader() -> bool:
        leases = manager()
        if leases is None:
            return True
        if await leases.renew("artifact-membership", owner, lease_seconds):
            return True
        return bool(await leases.try_acquire("artifact-membership", owner, 1, lease_seconds))

    async def check(groups: dict[str, list[str]]) -> list[str]:
        if broker_type == MessageBrokerType.KAFKA:
            from aiokafka.admin import AIOKafkaAdminClient

            from app.services.messaging.kafka.config.kafka_config import (
                kafka_security_kwargs,
            )

            cfg = await MessagingUtils.create_producer_config_from_service(config_service, client_id="membership_check")

            def factory() -> Any:  # noqa: ANN401
                return AIOKafkaAdminClient(
                    bootstrap_servers=",".join(cfg.bootstrap_servers),
                    client_id=f"{cfg.client_id}-admin",
                    **kafka_security_kwargs(ssl_enabled=cfg.ssl, sasl=cfg.sasl),
                )

            return await membership_module.kafka_untagged(factory, list(groups))
        redis_config = await MessagingUtils._get_redis_config(app_container)
        provider = get_redis_provider(RedisConnectionConfig.from_redis_config(redis_config))
        redis = provider.get_client()
        streams: dict[str, list[str]] = {}
        for group, topics in groups.items():
            found = set(topics)
            for topic in topics:
                async for key in redis.scan_iter(match=f"{topic}.*"):
                    found.add(key.decode() if isinstance(key, bytes) else str(key))
            streams[group] = sorted(found)
        return await membership_module.redis_untagged(redis, streams)

    publisher = membership_module.MembershipPublisher(
        logger,
        check,
        lambda key, entry, ttl: membership_module.publish_entry(config_service, key, entry, ttl),
        holder=owner,
        is_leader=is_leader,
    )
    await publisher.run()


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


def _indexing_worker_count() -> int:
    """Uvicorn worker processes running this service side by side (each with its own consumers)."""
    return max(1, int(os.getenv("INDEXING_UVICORN_WORKERS", "1")))


def _distributed_leases_wanted() -> bool:
    """On PostgreSQL state the leases are rows in the database, so they are always kept: a stage and a re-index of one record
    exclude each other (``record:<id>``) whatever the capacity switch says."""
    return messaging_env.distributed_concurrency_enabled or uses_pg_state()


async def _start_stages_when_topics_exist(
    admin: IMessageAdmin,
    topics: list[str],
    start_stages: Callable[[], Awaitable[None]],
    logger: logging.Logger,
) -> None:
    # ``start_stages`` stops what it started when it fails, so each attempt starts every stage.
    delay = _TOPIC_RETRY_FIRST_S
    while True:
        await asyncio.sleep(delay)
        try:
            await admin.ensure_topics_exist(topics)
        except Exception as e:
            delay = min(delay * 2, _TOPIC_RETRY_MAX_S)
            logger.error("❌ Pipeline stage topics %s are still not available (%s); retrying in %.0fs", topics, e, delay)
            continue
        try:
            await start_stages()
            logger.info("✅ Pipeline stage topics %s are available; stage consumers started", topics)
            return
        except Exception as e:
            delay = min(delay * 2, _TOPIC_RETRY_MAX_S)
            logger.error("❌ Starting the pipeline stage consumers failed (%s); retrying in %.0fs", e, delay)


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

        if _distributed_leases_wanted():
            concurrency_manager = MessagingFactory.create_lease_manager(logger, redis_config)
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
        connector_off_filter = await app_container.connector_off_filter()
        # Kept on the container so shutdown can release its Redis client.
        app_container.record_event_handler = record_event_handler

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
            connector_off_filter=connector_off_filter,
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
        # cluster lease pool, so stage work (classification, entities, ...) never holds an indexing permit.
        pipeline_runtime = await _resolve_pipeline_runtime(app_container)
        # A stage writes the stored record under the record's exclusivity lease, as its re-index does.
        pipeline_runtime.record_leases.bind(concurrency_manager)
        record_event_handler.record_leases = pipeline_runtime.record_leases  # lets the abandon sink tell a dead holder from a live one
        registry = pipeline_runtime.registry
        stage_topics = [registry.topic_for(name) for name in registry.names()]
        stage_producer = retry_producer
        main_loop = asyncio.get_running_loop()

        async def start_stage_consumers() -> None:
            # Bound only once the topics exist: Kafka holds a send to a missing topic for its
            # metadata timeout, and the record handler's hand-off would wait with it.
            pipeline_runtime.publisher.bind(stage_producer, main_loop)
            started: list[tuple[str, Any, Any]] = []
            try:
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
                        # Its cluster lease pool is sized for every worker process, as the record
                        # leases are.
                        stage_admission=StageAdmission(
                            stage=stage_name,
                            limit=pipeline_runtime.stage_limits[stage_name],
                            workers=_indexing_worker_count(),
                        ),
                        worker=worker,
                    )
                    # The retry producer is owned by the record consumer's entry, so it is closed once.
                    entry = (topic, stage_consumer, None)
                    consumers.append(entry)
                    started.append(entry)
                    await stage_consumer.start(pipeline_runtime.handler)  # type: ignore[arg-type]
                    logger.info("✅ Pipeline stage consumer started: %s", topic)
            except Exception:
                # Stopped and dropped, so a retry starts each stage once.
                for entry in started:
                    consumers.remove(entry)
                    try:
                        await entry[1].stop()
                    except Exception as exc:
                        logger.error("Error stopping %s consumer after a failed start: %s", entry[0], exc)
                raise

        admin = MessagingFactory.create_admin(logger, producer_config, broker_type)
        try:
            await admin.ensure_topics_exist(stage_topics)
        except Exception as e:
            # Records stay indexable without the stage topics; only the stages wait.
            logger.error(
                "❌ Pipeline stage topics %s are not available (%s). Records are still indexed and "
                "searchable; the pipeline stages wait until the topics exist. Create them, or allow this "
                "client to create topics. Retrying in the background.",
                stage_topics,
                e,
            )
            setattr(
                app_container,
                "stage_startup_task",
                spawn_detached(
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

    handler = getattr(container, "record_event_handler", None)
    aclose = getattr(handler, "aclose", None)
    if inspect.iscoroutinefunction(aclose):
        try:
            await aclose()
        except Exception as e:
            logger.error(f"❌ Error closing record event handler: {str(e)}")

    # Clear the consumers list
    if hasattr(container, 'kafka_consumers'):
        container.kafka_consumers = []

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan context manager for FastAPI"""
    mark_process_non_dumpable()
    install_log_redaction()

    app_container = await get_initialized_container()
    app.container = app_container
    logger = app.container.logger()
    logger.info("🚀 Starting application")

    await configure_envelope(app_container.config_service())

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
        worker_count=_indexing_worker_count(),
        heavy_parse_slots_dir=shared_slots_directory(
            "pipeshub-indexing-heavy-parse-slots"
        ),
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
    # Large text, code and CSV files are parsed in worker processes
    # sized from this governor, so they cannot stall the consumer's loop.
    from app.modules.parsers import parse_pool
    parse_pool.set_resource_governor(governor)

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
        app.state.entity_index_future = asyncio.run_coroutine_threadsafe(
            run_entity_index_rebuild_loop(app_container, graph_provider),
            worker_loop,
        )
        app.state.label_repair_future = asyncio.run_coroutine_threadsafe(
            run_record_label_repair_loop(app_container, graph_provider),
            worker_loop,
        )
        app.state.named_entity_sweep_future = asyncio.run_coroutine_threadsafe(
            run_named_entity_sweep_loop(app_container, graph_provider),
            worker_loop,
        )
    else:
        app.state.recovery_task = spawn_detached(
            run_stale_recovery_loop(app_container, graph_provider)
        )
        app.state.backfill_task = spawn_detached(
            run_vector_membership_backfill_loop(app_container, graph_provider)
        )
        app.state.entity_index_task = spawn_detached(
            run_entity_index_rebuild_loop(app_container, graph_provider)
        )
        app.state.label_repair_task = spawn_detached(
            run_record_label_repair_loop(app_container, graph_provider)
        )
        app.state.named_entity_sweep_task = spawn_detached(
            run_named_entity_sweep_loop(app_container, graph_provider)
        )

    # On every store, not only Neo4j: on the loop records are indexed on, beside the
    # consumers whose slot use (process-wide, see LLMGateway) it yields to.
    completion_loop = record_consumer_loop(consumers)
    if completion_loop is not None:
        app.state.named_entity_completion_future = asyncio.run_coroutine_threadsafe(
            run_named_entity_completion_loop(app_container, graph_provider),
            completion_loop,
        )
    else:
        app.state.named_entity_completion_task = spawn_detached(
            run_named_entity_completion_loop(app_container, graph_provider)
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

    entity_index_task = getattr(app.state, "entity_index_task", None)
    if entity_index_task:
        if not entity_index_task.done():
            entity_index_task.cancel()
        try:
            await entity_index_task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"❌ Error during entity index rebuild shutdown: {str(e)}")

    entity_index_future = getattr(app.state, "entity_index_future", None)
    if entity_index_future:
        if not entity_index_future.done():
            entity_index_future.cancel()
        try:
            await asyncio.wrap_future(entity_index_future)
        except (asyncio.CancelledError, RuntimeError):
            pass
        except Exception as e:
            logger.error(f"❌ Error during entity index rebuild future shutdown: {str(e)}")

    label_repair_task = getattr(app.state, "label_repair_task", None)
    if label_repair_task:
        if not label_repair_task.done():
            label_repair_task.cancel()
        try:
            await label_repair_task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"❌ Error during record label repair shutdown: {str(e)}")

    label_repair_future = getattr(app.state, "label_repair_future", None)
    if label_repair_future:
        if not label_repair_future.done():
            label_repair_future.cancel()
        try:
            await asyncio.wrap_future(label_repair_future)
        except (asyncio.CancelledError, RuntimeError):
            pass
        except Exception as e:
            logger.error(f"❌ Error during record label repair future shutdown: {str(e)}")

    sweep_task = getattr(app.state, "named_entity_sweep_task", None)
    if sweep_task:
        if not sweep_task.done():
            sweep_task.cancel()
        try:
            await sweep_task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"❌ Error during named entity sweep shutdown: {str(e)}")

    sweep_future = getattr(app.state, "named_entity_sweep_future", None)
    if sweep_future:
        if not sweep_future.done():
            sweep_future.cancel()
        try:
            await asyncio.wrap_future(sweep_future)
        except (asyncio.CancelledError, RuntimeError):
            pass
        except Exception as e:
            logger.error(f"❌ Error during named entity sweep future shutdown: {str(e)}")

    completion_task = getattr(app.state, "named_entity_completion_task", None)
    if completion_task:
        if not completion_task.done():
            completion_task.cancel()
        try:
            await completion_task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"❌ Error during named entity completion shutdown: {str(e)}")

    completion_future = getattr(app.state, "named_entity_completion_future", None)
    if completion_future:
        if not completion_future.done():
            completion_future.cancel()
        try:
            await asyncio.wrap_future(completion_future)
        except (asyncio.CancelledError, RuntimeError):
            pass
        except Exception as e:
            logger.error(f"❌ Error during named entity completion future shutdown: {str(e)}")

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

    try:
        from app.modules.parsers.pdf.pdf_rasterizer import shutdown_pdf_raster_pool
        if shutdown_pdf_raster_pool():
            logger.info("✅ PDF rasterization process pool shut down")
    except Exception as e:
        logger.error(f"❌ Error shutting down PDF rasterization pool: {e}")

    try:
        from app.modules.parsers.parse_pool import shutdown_parse_pool
        if shutdown_parse_pool():
            logger.info("✅ Parse worker pool shut down")
    except Exception as e:
        logger.error(f"❌ Error shutting down parse worker pool: {e}")


set_service_suffix("-is")

app = FastAPI(
    lifespan=lifespan,
    title="Vector Search API",
    description="API for semantic search and document retrieval with message consumer",
    version="1.0.0",
)
configure_app(app, "indexing")

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
                container.logger().warning("Resource governor stats failed: %s", stats_error)
                content["resource_governor"] = {"error": "unavailable"}
        # Per-tier dispatch admission: a heavy tier pinned at its ceiling with
        # light idle is attachments queueing on heavy parse, which is fine as
        # long as light keeps moving; both pinned means the node is full.
        dispatch: dict[str, Any] = {}
        loop_lag: dict[str, Any] = {}
        for entry in getattr(container, "kafka_consumers", None) or []:
            consumer = entry[1] if len(entry) > 1 else None
            # Late wake-ups on the worker loop: every record on it was stalled
            # that long, which reads as slow I/O everywhere else.
            lag_monitor = getattr(consumer, "loop_lag_monitor", None)
            if isinstance(lag_monitor, LoopLagMonitor):
                loop_lag[str(entry[0])] = lag_monitor.stats()
            stats = getattr(consumer, "dispatch_stats", None)
            if not callable(stats):
                continue
            try:
                dispatch[str(entry[0])] = stats()
            except Exception as stats_error:
                container.logger().warning("Dispatch stats failed for %s: %s", entry[0], stats_error)
                dispatch[str(entry[0])] = {"error": "unavailable"}
        if dispatch:
            content["dispatch"] = dispatch
        if loop_lag:
            content["worker_loop_lag"] = loop_lag
        # The gateway's cap: its use, wait and hold time, rate-limit retries.
        content["indexing_llm"] = indexing_llm_stats()
        # Which connector is on which Redis Streams lane, from the last lane
        # upkeep this replica ran (the one holding the recovery lock).
        lane_report = last_lane_report(Topic.RECORD_EVENTS.value)
        if lane_report is not None:
            content["lanes"] = lane_report.as_dict()
        runtime: PipelineRuntime | None = getattr(request.app.state, "pipeline_runtime", None)
        if runtime is not None:
            try:
                content["stages"] = runtime.stats()
            except Exception as stats_error:
                container.logger().warning("Pipeline stage stats failed: %s", stats_error)
                content["stages"] = {"error": "unavailable"}
        try:
            content["llm_gateway"] = get_llm_gateway().stats()
        except Exception as stats_error:
            container.logger().warning("LLM gateway stats failed: %s", stats_error)
            content["llm_gateway"] = {"error": "unavailable"}
        worker = getattr(container, "worker_loop", None)
        failure = worker.failure if isinstance(worker, WorkerLoop) else None
        if failure is not None:
            # Its consumers stopped themselves; nothing on this node indexes until the process
            # restarts. The type only: the traceback is in the CRITICAL log line.
            content["status"] = "unhealthy"
            content["worker_loop_failure"] = type(failure).__name__
        return JSONResponse(
            status_code=503 if failure is not None else 200,
            content=content,
        )
    except Exception:
        container.logger().exception("Health check failed")
        return JSONResponse(
            status_code=500,
            content={
                "status": "unhealthy",
                "error": "Health check failed",
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
        timeout_worker_healthcheck=uvicorn_worker_healthcheck_timeout(),
    )


if __name__ == "__main__":
    run(reload=False)
