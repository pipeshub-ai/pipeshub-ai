import app.utils.runtime_threads  # noqa: E402 - must precede all ML library imports

import asyncio
import os
from uuid import uuid4
from collections.abc import AsyncGenerator, Awaitable
from contextlib import asynccontextmanager
from typing import Any, Protocol, TypeVar

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    EventTypes,
    OriginTypes,
    ProgressStatus,
)
from app.containers.indexing import IndexingAppContainer, initialize_container
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.services.messaging.config import (
    ConsumerType,
    Topic,
    get_message_broker_type,
    messaging_env,
)
from app.services.messaging.distributed_concurrency import (
    DistributedConcurrencyManager,
)
from app.services.messaging.kafka.utils.utils import KafkaUtils
from app.services.messaging.messaging_factory import MessagingFactory
from app.services.messaging.utils import MessagingUtils
from app.telemetry.setup import setup_telemetry
from app.utils.time_conversion import get_epoch_timestamp_in_ms

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
            recovery_lock_held = await run_coordination(
                concurrency_manager.try_acquire(
                    "recovery",
                    recovery_owner,
                    1,
                    recovery_lease_seconds,
                )
            )
            if not recovery_lock_held:
                logger.debug(
                    "Another indexing replica is running stale-record recovery"
                )
                return

            async def renew_recovery_lock() -> None:
                while True:
                    await asyncio.sleep(min(30.0, recovery_lease_seconds / 3))
                    renewed = await run_coordination(
                        concurrency_manager.renew(
                            "recovery",
                            recovery_owner,
                            recovery_lease_seconds,
                        )
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
                        record_lock_held = await run_coordination(
                            concurrency_manager.try_acquire(
                                record_pool,
                                record_owner,
                                1,
                                recovery_lease_seconds,
                            )
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
                            await run_coordination(
                                concurrency_manager.release(
                                    record_pool,
                                    record_owner,
                                )
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
                await run_coordination(
                    concurrency_manager.release("recovery", recovery_owner)
                )
            except Exception as release_exc:
                logger.warning(
                    "Failed to release stale-record recovery lease: %s",
                    release_exc,
                )


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


async def start_kafka_consumers(app_container: IndexingAppContainer) -> list[Any]:
    """Start all message consumers at application level"""
    logger = app_container.logger()
    consumers = []
    broker_type = get_message_broker_type()
    retry_manager = None
    retry_producer = None
    concurrency_manager = None

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
            )
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

        record_kafka_consumer = MessagingFactory.create_consumer(
            broker_type=broker_type,
            logger=logger,
            config=record_consumer_config,
            consumer_type=ConsumerType.INDEXING,
            retry_manager=retry_manager,
            producer=retry_producer,
            concurrency_manager=concurrency_manager,
        )
        consumers.append(("record", record_kafka_consumer, retry_producer))

        # TODO: Remove this once the graph provider is fixed
        # This is a temporary hack to reconnect the graph provider in worker thread event loop
        # because it is in main event loop, but indexing in in worker thread loop

        data_store = os.getenv("DATA_STORE", "arangodb").lower()
        if data_store == "neo4j":
            graph_provider = getattr(app_container, '_graph_provider', None)
            if not graph_provider or not hasattr(graph_provider, 'client') or not graph_provider.client:
                raise Exception("Neo4j Graph provider not initialized")

            await record_kafka_consumer.initialize()
            worker_loop = getattr(record_kafka_consumer, 'worker_loop', None)
            if not worker_loop or not worker_loop.is_running():
                raise Exception("Worker loop not initialized")

            async def _reconnect() -> None:
                if graph_provider.client.driver:
                    try:
                        await graph_provider.client.driver.close()
                    except Exception as e:
                        logger.warning("Failed to close existing Neo4j driver, proceeding with reconnection: %s", e)
                    graph_provider.client.driver = None
                await graph_provider.client.connect()

            reconnect_coro = _reconnect()
            try:
                future = asyncio.run_coroutine_threadsafe(
                    reconnect_coro,
                    worker_loop,
                )
            except BaseException:
                reconnect_coro.close()
                raise
            try:
                await asyncio.wrap_future(future)
            except BaseException:
                future.cancel()
                raise
            logger.info("✅Neo4j Graph provider reconnected in worker thread event loop")

        record_message_handler = await KafkaUtils.create_record_message_handler(
            app_container, producer=retry_producer
        )
        await record_kafka_consumer.start(record_message_handler)  # type: ignore[arg-type]
        logger.info("✅ Record message consumer started")

        return consumers
    except Exception as e:
        logger.error(f"❌ Error starting message consumers: {str(e)}")
        # Cleanup any started consumers and producers
        consumer_cleanup_failed = False
        for item in consumers:
            name = item[0]
            consumer = item[1]
            producer = item[2] if len(item) > 2 else None
            try:
                await consumer.stop()
                logger.info(f"Stopped {name} consumer during cleanup")
            except Exception as cleanup_error:
                consumer_cleanup_failed = True
                logger.error(f"Error stopping {name} consumer during cleanup: {cleanup_error}")
            if producer:
                try:
                    await producer.cleanup()
                    logger.info(f"Stopped {name} retry producer during cleanup")
                except Exception as cleanup_error:
                    logger.error(f"Error stopping {name} retry producer during cleanup: {cleanup_error}")
        if not consumers or consumer_cleanup_failed:
            if concurrency_manager:
                try:
                    await concurrency_manager.cleanup()
                except Exception as cleanup_error:
                    logger.error(
                        "Error closing distributed concurrency during cleanup: %s",
                        cleanup_error,
                    )
            if retry_manager:
                try:
                    await retry_manager.cleanup()
                except Exception as cleanup_error:
                    logger.error(
                        "Error closing retry manager during cleanup: %s",
                        cleanup_error,
                    )
            if retry_producer:
                try:
                    await retry_producer.cleanup()
                except Exception as cleanup_error:
                    logger.error(
                        "Error closing retry producer during cleanup: %s",
                        cleanup_error,
                    )
        raise

async def stop_kafka_consumers(container: IndexingAppContainer) -> None:
    """Stop all Kafka consumers and their associated producers"""

    logger = container.logger()
    consumers = getattr(container, 'kafka_consumers', [])
    for item in consumers:
        name = item[0]
        consumer = item[1]
        producer = item[2] if len(item) > 2 else None

        try:
            await consumer.stop()
            logger.info(f"✅ {name.title()} message consumer stopped")
        except Exception as e:
            logger.error(f"❌ Error stopping {name} consumer: {str(e)}")

        # concurrency_manager/retry_manager are injected into the consumer but
        # owned here (created in start_kafka_consumers) — consumer.stop() no
        # longer closes them (that broke restart), so close them unconditionally,
        # regardless of whether consumer.stop() above succeeded.
        for resource_name in ("concurrency_manager", "retry_manager"):
            resource = getattr(consumer, resource_name, None)
            if resource is not None:
                try:
                    await resource.cleanup()
                except Exception as cleanup_error:
                    logger.error(
                        "Error closing %s for %s consumer: %s",
                        resource_name,
                        name,
                        cleanup_error,
                    )

        if producer:
            try:
                await producer.cleanup()
                logger.info(f"✅ {name.title()} retry producer stopped")
            except Exception as e:
                logger.error(f"❌ Error stopping {name} retry producer: {str(e)}")

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

    # Start all message consumers centrally
    try:
        consumers = await start_kafka_consumers(app_container)
        app_container.kafka_consumers = consumers
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
    else:
        app.state.recovery_task = asyncio.create_task(
            run_stale_recovery_loop(app_container, graph_provider)
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

    # Stop message consumers
    try:
        await stop_kafka_consumers(app_container)
    except Exception as e:
        logger.error(f"❌ Error during application shutdown: {str(e)}")

    # Close configuration service (stops Redis Pub/Sub subscription)
    try:
        config_service = app_container.config_service()
        await config_service.close()
    except Exception as e:
        logger.error(f"❌ Error closing configuration service: {e}")

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
async def health_check() -> JSONResponse:
    """Health check endpoint for the indexing service itself"""
    try:
        return JSONResponse(
            status_code=200,
            content={
                "status": "healthy",
                "timestamp": get_epoch_timestamp_in_ms(),
            },
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