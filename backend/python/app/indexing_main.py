import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import DefaultEndpoints, config_node_constants
from app.containers.indexing import IndexingAppContainer, initialize_container
from app.utils.time_conversion import get_epoch_timestamp_in_ms

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

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan context manager for FastAPI"""

    container = await get_initialized_container()
    app.container = container
    logger = app.container.logger()
    logger.info("🚀 Starting application")
    consumer = await container.kafka_consumer()
    consume_task = asyncio.create_task(consumer.consume_messages())

    yield
    # Shutdown
    logger.info("🔄 Shutting down application")
    consumer.stop()
    # Cancel the consume task
    consume_task.cancel()
    try:
        await consume_task
    except asyncio.CancelledError:
        logger.info("Kafka consumer task cancelled")


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
