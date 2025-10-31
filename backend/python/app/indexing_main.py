import asyncio

# Only for development/debugging
import signal
import sys
import time
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
    ProgressStatus,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import DefaultEndpoints, config_node_constants
from app.containers.indexing import IndexingAppContainer, initialize_container
from app.services.messaging.kafka.handlers.record import RecordEventHandler
from app.services.messaging.kafka.rate_limiter.rate_limiter import RateLimiter
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

MAX_CONCURRENT_TASKS = 5  # Maximum number of messages to process concurrently
RATE_LIMIT_PER_SECOND = 2  # Maximum number of new tasks to start per second

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

async def recover_in_progress_records(app_container: IndexingAppContainer) -> None:
    """
    Recover and process records that were in progress when the service crashed.
    This ensures that any incomplete indexing operations are completed before
    processing new events from Kafka.
    """
    logger = app_container.logger()
    logger.info("🔄 Checking for in-progress records to recover...")

    try:
        # Get the arango service and event processor
        arango_service = await app_container.arango_service()

        # Query for records that are in IN_PROGRESS status
        in_progress_records = await arango_service.get_documents_by_status(
            CollectionNames.RECORDS.value,
            ProgressStatus.IN_PROGRESS.value
        )

        if not in_progress_records:
            logger.info("✅ No in-progress records found. Starting fresh.")
            return

        logger.info(f"📋 Found {len(in_progress_records)} in-progress records to recover")
        # Todo: Fix properly. Wait for connector service to be ready. This is a temporary solution.
        time.sleep(60)
        # Create the message handler that will process these records
        record_message_handler: RecordEventHandler = await KafkaUtils.create_record_message_handler(app_container)

        # Process each in-progress record
        for idx, record in enumerate(in_progress_records, 1):
            try:
                record_id = record.get("_key")
                record_name = record.get("recordName", "Unknown")
                logger.info(
                    f"🔄 [{idx}/{len(in_progress_records)}] Recovering record: {record_name} (ID: {record_id})"
                )

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
                success = await record_message_handler({
                    "eventType": event_type,
                    "payload": payload
                })

                if success:
                    logger.info(
                        f"✅ [{idx}/{len(in_progress_records)}] Successfully recovered record: {record_name}"
                    )
                else:
                    logger.warning(
                        f"⚠️ [{idx}/{len(in_progress_records)}] Failed to recover record: {record_name}"
                    )

            except Exception as e:
                logger.error(
                    f"❌ Error recovering record {record.get('_key')}: {str(e)}"
                )
                # Continue with next record even if one fails
                continue

        logger.info(
            f"✅ Recovery complete. Processed {len(in_progress_records)} in-progress records"
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

        rate_limiter = RateLimiter(RATE_LIMIT_PER_SECOND)

        record_kafka_consumer = MessagingFactory.create_consumer(
            broker_type="kafka",
            logger=logger,
            config=record_kafka_consumer_config,
            rate_limiter=rate_limiter
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

    # Recover in-progress records before starting Kafka consumers
    try:
        await recover_in_progress_records(app_container)
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
