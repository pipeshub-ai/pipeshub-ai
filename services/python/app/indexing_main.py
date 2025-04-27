import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator

import httpx
import uvicorn
from fastapi import Body, FastAPI
from fastapi.responses import JSONResponse

from app.config.configuration_service import config_node_constants
from app.setups.indexing_setup import AppContainer, initialize_container
from app.utils.llm import get_llm

container = AppContainer()
container_lock = asyncio.Lock()


async def get_initialized_container() -> AppContainer:
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
async def health_check():
    """Health check endpoint that also verifies connector service health"""
    try:
        endpoints = await app.container.config_service().get_config(
            config_node_constants.ENDPOINTS.value
        )
        connector_endpoint = endpoints.get("connectors").get("endpoint")
        connector_url = f"{connector_endpoint}/health"
        async with httpx.AsyncClient() as client:
            connector_response = await client.get(connector_url, timeout=5.0)

            if connector_response.status_code != 200:
                return JSONResponse(
                    status_code=500,
                    content={
                        "status": "fail",
                        "error": f"Connector service unhealthy: {connector_response.text}",
                        "timestamp": datetime.now(
                            timezone(timedelta(hours=5, minutes=30))
                        ).isoformat(),
                    },
                )

            return JSONResponse(
                status_code=200,
                content={
                    "status": "healthy",
                    "timestamp": datetime.now(
                        timezone(timedelta(hours=5, minutes=30))
                    ).isoformat(),
                },
            )
    except httpx.RequestError as e:
        return JSONResponse(
            status_code=500,
            content={
                "status": "fail",
                "error": f"Failed to connect to connector service: {str(e)}",
                "timestamp": datetime.now(
                    timezone(timedelta(hours=5, minutes=30))
                ).isoformat(),
            },
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "status": "fail",
                "error": str(e),
                "timestamp": datetime.now(
                    timezone(timedelta(hours=5, minutes=30))
                ).isoformat(),
            },
        )

@app.post("/llm-health-check")
async def llm_health_check(llm_configs: list[dict] = Body(...)):
    """Health check endpoint to validate user-provided LLM configurations"""
    try:
        llm = await get_llm(app.container.logger(), app.container.config_service(), llm_configs)
        # Make a simple test call to the LLM with the provided configurations
        await llm.ainvoke("Test message to verify LLM health.")

        return JSONResponse(
            status_code=200,
            content={
                "status": "healthy",
                "message": "LLM service is responding",
                "timestamp": datetime.now(timezone(timedelta(hours=5, minutes=30))).isoformat(),
            },
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "status": "not healthy",
                "error": f"LLM service health check failed: {str(e)}",
                "timestamp": datetime.now(timezone(timedelta(hours=5, minutes=30))).isoformat(),
            },
        )

@app.post("/embedding-health-check")
async def embedding_health_check(embedding_configs: list[dict] = Body(...)):
    try:
        indexing_pipeline = await app.container.indexing_pipeline()
        embedding_model_created = await indexing_pipeline.get_embedding_model_instance(embedding_configs)
        # Make a simple test call to the embedding model
        if embedding_model_created:
            sample_embedding = indexing_pipeline.dense_embeddings.embed_query("Test message to verify embedding model health.")
            if sample_embedding:
                return JSONResponse(
                    status_code=200,
                    content={
                "status": "healthy",
                "message": "Embedding model is responding. Sample embedding size: " + str(len(sample_embedding)),
                "timestamp": datetime.now(timezone(timedelta(hours=5, minutes=30))).isoformat(),
            },
                )
            else:
                raise Exception("Embedding model failed to respond")
        else:
            raise Exception("Embedding model failed to create")
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "status": "not healthy",
                "error": f"Embedding model health check failed: {str(e)}",
                "timestamp": datetime.now(timezone(timedelta(hours=5, minutes=30))).isoformat(),
            },
        )

def run(host: str = "0.0.0.0", port: int = 8091, reload: bool = True):
    """Run the application"""
    uvicorn.run(
        "app.indexing_main:app", host=host, port=port, log_level="info", reload=reload
    )


if __name__ == "__main__":
    run(reload=False)
