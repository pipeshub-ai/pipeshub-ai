import asyncio

# Only for development/debugging
import signal
import sys
from contextlib import asynccontextmanager
from typing import AsyncGenerator, List

import httpx
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
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import DefaultEndpoints, config_node_constants
from app.containers.indexing import IndexingAppContainer, initialize_container
from app.services.messaging.kafka.handlers.record import RecordEventHandler
from app.services.messaging.kafka.utils.utils import KafkaUtils
from app.services.messaging.messaging_factory import MessagingFactory
from app.utils.time_conversion import get_epoch_timestamp_in_ms


def handle_sigterm(signum, frame) -> None:
    print(f"Received signal {signum}, {frame} shutting down gracefully")
    sys.exit(0)

signal.signal(signal.SIGTERM, handle_sigterm)
signal.signal(signal.SIGINT, handle_sigterm)

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
                get_initialized_container.initialized = True
    return container

async def recover_in_progress_records(app_container: IndexingAppContainer, graph_provider) -> None:
    """
    Recover and process records that were in progress when the service crashed.
    This ensures that any incomplete indexing operations are completed before
    processing new events from Kafka. Records are processed in parallel (5 at a time).
    """
    logger = app_container.logger()
    logger.info("🔄 Checking for in-progress records to recover...")

    # Semaphore to limit concurrent processing to 5 records
    semaphore = asyncio.Semaphore(5)
    # Track results for final summary
    results = {"success": 0, "partial": 0, "incomplete": 0, "skipped": 0, "error": 0}

    try:
        # Query for records that are in IN_PROGRESS status
        in_progress_records = await graph_provider.get_nodes_by_filters(
            CollectionNames.RECORDS.value,
            {"indexingStatus": ProgressStatus.IN_PROGRESS.value}
        )
        queued_records = await arango_service.get_documents_by_status(
                CollectionNames.RECORDS.value,
                ProgressStatus.QUEUED.value
            )
        # Create combined list and store length for clarity and efficiency
        all_records_to_recover = in_progress_records + queued_records
        total_records = len(all_records_to_recover)

        if not total_records:
            logger.info("✅ No in-progress or queued records found. Starting fresh.")
            return

        logger.info(f"📋 Found {total_records} in-progress or queued records to recover")
        # Create the message handler that will process these records
        record_message_handler: RecordEventHandler = await KafkaUtils.create_record_message_handler(app_container)

        async def process_single_record(idx: int, record: dict) -> None:
            """Process a single record with semaphore control."""
            async with semaphore:
                record_id = record.get("_key")
                record_name = record.get("recordName", "Unknown")
                try:
                    logger.info(
                        f"🔄 [{idx}/{total_records}] Recovering record: {record_name} (ID: {record_id})"
                    )

                    # Check if connector is disabled or deleted
                    connector_id = record.get("connectorId")
                    origin = record.get("origin")
                    if connector_id and origin == OriginTypes.CONNECTOR.value:
                        connector_instance = await arango_service.get_document(
                            connector_id, CollectionNames.APPS.value
                        )
                        if not connector_instance:
                            logger.info(
                                f"⏭️ [{idx}/{total_records}] Skipping recovery for record {record_id}: "
                                f"connector instance {connector_id} not found (possibly deleted)."
                            )
                            results["skipped"] += 1
                            return
                        if not connector_instance.get("isActive", False):
                            logger.info(
                                f"⏭️ [{idx}/{total_records}] Skipping recovery for record {record_id}: "
                                f"connector instance {connector_id} is inactive."
                            )
                            # Update status to CONNECTOR_DISABLED
                            await arango_service.update_document(
                                record_id,
                                CollectionNames.RECORDS.value,
                                {
                                    "indexingStatus": ProgressStatus.CONNECTOR_DISABLED.value,
                                }
                            )
                            results["skipped"] += 1
                            return

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
                        "virtualRecordId": record.get("virtualRecordId", None),
                    }

                    # Determine event type - default to NEW_RECORD for recovery
                    # Only treat as REINDEX if version > 0 AND virtualRecordId exists
                    # Otherwise, treat as NEW_RECORD (even if version > 0, the initial indexing might have failed)
                    version = payload.get("version", 0)
                    virtual_record_id = payload.get("virtualRecordId")

                    if version > 0 and virtual_record_id is not None:
                        event_type = EventTypes.REINDEX_RECORD.value
                        logger.info(f"   Treating as REINDEX_RECORD (version={version}, virtualRecordId={virtual_record_id})")
                    else:
                        event_type = EventTypes.NEW_RECORD.value
                        logger.info(f"   Treating as NEW_RECORD (version={version}, virtualRecordId={virtual_record_id})")

                    # Process the record using the same handler that processes Kafka messages
                    # record_message_handler returns an async generator, so we need to consume it
                    # Track whether we received the indexing_complete event to verify full recovery
                    parsing_complete = False
                    indexing_complete = False

                    async for event in record_message_handler({
                        "eventType": event_type,
                        "payload": payload
                    }):
                        event_name = event.get("event", "unknown")
                        logger.debug(f"   Recovery event: {event_name}")

                        if event_name == "parsing_complete":
                            parsing_complete = True
                        elif event_name == "indexing_complete":
                            indexing_complete = True

                    # Only report success if indexing actually completed
                    if indexing_complete:
                        logger.info(
                            f"✅ [{idx}/{total_records}] Successfully recovered record: {record_name}"
                        )
                        results["success"] += 1
                    elif parsing_complete:
                        logger.warning(
                            f"⚠️ [{idx}/{total_records}] Partial recovery for record: {record_name} "
                            f"(parsing completed but indexing did not complete)"
                        )
                        results["partial"] += 1
                    else:
                        logger.warning(
                            f"⚠️ [{idx}/{total_records}] Recovery incomplete for record: {record_name} "
                            f"(no completion events received)"
                        )
                        results["incomplete"] += 1

                except Exception as e:
                    logger.error(
                        f"❌ Error recovering record {record_id}: {str(e)}"
                    )
                    results["error"] += 1

        # Create tasks for all records and process them in parallel (limited by semaphore)
        tasks = [
            process_single_record(idx, record)
            for idx, record in enumerate(all_records_to_recover, 1)
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

        logger.info(
            f"✅ Recovery complete. Processed {total_records} records: "
            f"{results['success']} success, {results['skipped']} skipped, "
            f"{results['partial']} partial, {results['incomplete']} incomplete, "
            f"{results['error']} errors"
        )

    except Exception as e:
        logger.error(f"❌ Error during record recovery: {str(e)}")
        # Don't raise - we want to continue starting the service even if recovery fails
        logger.warning("⚠️ Continuing to start Kafka consumers despite recovery errors")

async def start_kafka_consumers(app_container: IndexingAppContainer) -> List:
    """Start all Kafka consumers at application level"""
    logger = app_container.logger()
    consumers = []

    try:
        # 1. Create Entity Consumer
        logger.info("🚀 Starting Entity Kafka Consumer...")
        record_kafka_consumer_config = await KafkaUtils.create_record_kafka_consumer_config(app_container)

        record_kafka_consumer = MessagingFactory.create_consumer(
            broker_type="kafka",
            logger=logger,
            config=record_kafka_consumer_config,
            consumer_type="indexing"
        )
        record_message_handler = await KafkaUtils.create_record_message_handler(app_container)
        await record_kafka_consumer.start(record_message_handler)
        consumers.append(("record", record_kafka_consumer))
        logger.info("✅ Record Kafka consumer started")

        return consumers
    except Exception as e:
        logger.error(f"❌ Error starting Kafka consumers: {str(e)}")
        # Cleanup any started consumers
        for name, consumer in consumers:
            try:
                await consumer.stop()
                logger.info(f"Stopped {name} consumer during cleanup")
            except Exception as cleanup_error:
                logger.error(f"Error stopping {name} consumer during cleanup: {cleanup_error}")
        raise

async def stop_kafka_consumers(container) -> None:
    """Stop all Kafka consumers"""

    logger = container.logger()
    consumers = getattr(container, 'kafka_consumers', [])
    for name, consumer in consumers:
        try:
            await consumer.stop()
            logger.info(f"✅ {name.title()} Kafka consumer stopped")
        except Exception as e:
            logger.error(f"❌ Error stopping {name} consumer: {str(e)}")

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

    # Get the already-resolved graph_provider from container (set during initialization)
    # This avoids coroutine reuse error
    graph_provider = getattr(app_container, '_graph_provider', None)
    if not graph_provider:
        # Fallback: if not set during initialization, resolve it now
        graph_provider = await app_container.graph_provider()
    app.state.graph_provider = graph_provider

    # Recover in-progress records before starting Kafka consumers
    # Pass already-resolved graph_provider to avoid coroutine reuse
    try:
        await recover_in_progress_records(app_container, graph_provider)
    except Exception as e:
        logger.error(f"❌ Error during record recovery: {str(e)}")
        # Continue even if recovery fails

    # Start all Kafka consumers centrally
    try:
        consumers = await start_kafka_consumers(app_container)
        app_container.kafka_consumers = consumers
        logger.info("✅ All Kafka consumers started successfully")
    except Exception as e:
        logger.error(f"❌ Failed to start Kafka consumers: {str(e)}")
        raise

    yield
    # Shutdown
    logger.info("🔄 Shutting down application")
    # Stop Kafka consumers
    try:
        await stop_kafka_consumers(app_container)
    except Exception as e:
        logger.error(f"❌ Error during application shutdown: {str(e)}")


app = FastAPI(
    lifespan=lifespan,
    title="Vector Search API",
    description="API for semantic search and document retrieval with Kafka consumer",
    version="1.0.0",
)


@app.get("/health")
async def health_check() -> JSONResponse:
    """Health check endpoint that also verifies connector service health"""
    try:
        endpoints = await app.container.config_service().get_config(
            config_node_constants.ENDPOINTS.value
        )
        connector_endpoint = endpoints.get("connectors").get("endpoint", DefaultEndpoints.CONNECTOR_ENDPOINT.value)
        connector_url = f"{connector_endpoint}/health"
        async with httpx.AsyncClient() as client:
            connector_response = await client.get(connector_url, timeout=5.0)

            if connector_response.status_code != HttpStatusCode.SUCCESS.value:
                return JSONResponse(
                    status_code=500,
                    content={
                        "status": "fail",
                        "error": f"Connector service unhealthy: {connector_response.text}",
                        "timestamp": get_epoch_timestamp_in_ms(),
                    },
                )

            return JSONResponse(
                status_code=200,
                content={
                    "status": "healthy",
                    "timestamp": get_epoch_timestamp_in_ms(),
                },
            )
    except httpx.RequestError as e:
        return JSONResponse(
            status_code=500,
            content={
                "status": "fail",
                "error": f"Failed to connect to connector service: {str(e)}",
                "timestamp": get_epoch_timestamp_in_ms(),
            },
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "status": "fail",
                "error": str(e),
                "timestamp": get_epoch_timestamp_in_ms(),
            },
        )


def run(host: str = "0.0.0.0", port: int = 8091, reload: bool = True) -> None:
    """Run the application"""
    uvicorn.run(
        "app.indexing_main:app", host=host, port=port, log_level="info", reload=reload
    )


if __name__ == "__main__":
    run(reload=False)
