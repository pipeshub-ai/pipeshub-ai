import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.middlewares.auth import authMiddleware
from app.api.routes.agent import router as agent_router
from app.api.routes.chatbot import router as chatbot_router
from app.api.routes.health import router as health_router
from app.api.routes.search import router as search_router
from app.api.routes.ai_models_registry import router as ai_models_registry_router
from app.api.routes.speech import router as speech_router
from app.api.routes.toolsets import router as toolsets_router
from app.containers.query import QueryAppContainer
from app.health.health import Health
from app.services.messaging.config import get_message_broker_type
from app.services.messaging.kafka.utils.utils import KafkaUtils
from app.services.messaging.messaging_factory import MessagingFactory
from app.services.messaging.utils import MessagingUtils
from app.utils.time_conversion import get_epoch_timestamp_in_ms

container = QueryAppContainer.init("query_service")


async def initialize_container(container: QueryAppContainer) -> bool:
    """Initialize container resources"""
    logger = container.logger()
    logger.info("🚀 Initializing application resources")

    try:
        # Ensure connector service is healthy before starting query service
        logger.info("Checking Connector service health before startup")
        await Health.health_check_connector_service(container)

        # Ensure Graph Database Provider is initialized (connection is handled in the resource factory)
        logger.info("Ensuring Graph Database Provider is initialized")
        graph_provider = await container.graph_provider()
        if not graph_provider:
            raise Exception("Failed to initialize Graph Database Provider")

        # Store the resolved graph_provider in the container to avoid coroutine reuse
        container._graph_provider = graph_provider
        logger.info("✅ Graph Database Provider initialized and connected")

        return True

    except Exception as e:
        logger.error(f"❌ Failed to initialize resources: {str(e)}")
        raise


async def get_initialized_container() -> QueryAppContainer:
    """Dependency provider for initialized container"""
    if not hasattr(get_initialized_container, "initialized"):
        await initialize_container(container)
        container.wire(
            modules=[
                "app.api.routes.search",
                "app.api.routes.chatbot",
                "app.modules.retrieval.retrieval_service"
            ]
        )
        get_initialized_container.initialized = True
    return container

async def start_kafka_consumers(app_container: QueryAppContainer) -> list:
    """Start all message consumers at application level"""
    logger = app_container.logger()
    consumers = []
    broker_type = get_message_broker_type()

    try:
        logger.info(f"🚀 Starting AI Config Consumer (broker: {broker_type})...")
        aiconfig_config = await MessagingUtils.create_aiconfig_consumer_config(app_container)
        aiconfig_consumer = MessagingFactory.create_consumer(
            broker_type=broker_type,
            logger=logger,
            config=aiconfig_config
        )
        aiconfig_message_handler = await KafkaUtils.create_aiconfig_message_handler(app_container)
        await aiconfig_consumer.start(aiconfig_message_handler)
        consumers.append(("aiconfig", aiconfig_consumer))
        logger.info("✅ AI Config consumer started")

        logger.info(f"✅ All {len(consumers)} message consumers started successfully")
        return consumers

    except Exception as e:
        logger.error(f"❌ Error starting message consumers: {str(e)}")
        # Cleanup any started consumers
        for name, consumer in consumers:
            try:
                await consumer.stop()
                logger.info(f"Stopped {name} consumer during cleanup")
            except Exception as cleanup_error:
                logger.error(f"Error stopping {name} consumer during cleanup: {cleanup_error}")
        raise

async def stop_kafka_consumers(container: QueryAppContainer) -> bool|None:
    """Stop all Kafka consumers"""
    logger = container.logger()
    consumers = getattr(container, 'kafka_consumers', [])
    for name, consumer in consumers:
        try:
            await consumer.stop()
            logger.info(f"✅ {name.title()} message consumer stopped")
            return True
        except Exception as e:
            logger.error(f"❌ Error stopping {name} consumer: {str(e)}")
            return False
        finally:
            # Clear the consumers list
            if hasattr(container, 'kafka_consumers'):
                container.kafka_consumers = []
    return None

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan context manager for FastAPI"""

    # Initialize container
    app_container = await get_initialized_container()
    # Store container in app state for access in dependencies
    app.container = app_container

    logger = app.container.logger()
    logger.debug("🚀 Starting retrieval application")

    # Get the already-resolved graph_provider from container (set during initialization)
    # This avoids coroutine reuse error
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

    # Get all organizations
    orgs = await graph_provider.get_all_orgs()
    if not orgs:
        logger.info("No organizations found in the system")
    else:
        logger.info("Found organizations in the system")
        retrieval_service = await container.retrieval_service()

        # Warm up the embedding model in the background. The default
        # HuggingFace model (BAAI/bge-large-en-v1.5) can take minutes to
        # download and load on a cold cache, which would otherwise block the
        # FastAPI lifespan and prevent the service from accepting traffic
        # (including health checks) until it finishes. Kick it off as a
        # background task; callers of `get_embedding_model_instance()` will
        # await/coordinate via the internal lock and lazy-load on first use
        # if the warmup hasn't completed yet.
        async def _warmup_embedding_model() -> None:
            try:
                logger.info("🔥 Warming up embedding model in background")
                await retrieval_service.get_embedding_model_instance()
                logger.info("✅ Embedding model warmup complete")
            except Exception as warmup_error:
                logger.error(
                    f"❌ Embedding model warmup failed (will retry on first use): {warmup_error}"
                )

        app.state.embedding_warmup_task = asyncio.create_task(_warmup_embedding_model())

    # Initialize toolset registry for agent tool execution
    # This imports toolset modules which register tools in the global registry
    logger.info("🔄 Initializing in-memory toolset registry for agents...")
    from app.agents.registry.toolset_registry import get_toolset_registry
    from app.agents.tools.registry import _global_tools_registry

    toolset_registry = get_toolset_registry()
    toolset_registry.auto_discover_toolsets()
    app.state.toolset_registry = toolset_registry
    logger.info(f"✅ Loaded {len(toolset_registry.list_toolsets())} toolsets in memory")

    # Log tool count from in-memory registry
    tool_count = len(_global_tools_registry.list_tools())
    logger.info(f"✅ {tool_count} tools available from in-memory registry")

    yield
    # Shutdown
    logger.info("🔄 Shutting down application")

    # Cancel embedding warmup task if it is still running.
    warmup_task: asyncio.Task | None = getattr(app.state, "embedding_warmup_task", None)
    if warmup_task is not None and not warmup_task.done():
        warmup_task.cancel()
        try:
            await warmup_task
        except (asyncio.CancelledError, Exception):
            pass

    # Stop all message consumers
    try:
        await stop_kafka_consumers(app_container)
        logger.info("✅ All message consumers stopped")
    except Exception as e:
        logger.error(f"❌ Error stopping message consumers: {str(e)}")

    # Close configuration service (stops Redis Pub/Sub subscription)
    try:
        config_service = app_container.config_service()
        await config_service.close()
    except Exception as e:
        logger.error(f"❌ Error closing configuration service: {e}")


# Create FastAPI app with lifespan
app = FastAPI(
    title="Retrieval API",
    description="API for retrieving information from vector store",
    version="1.0.0",
    lifespan=lifespan,
    redirect_slashes=False,
    dependencies=[Depends(get_initialized_container)],
)

EXCLUDE_PATHS = ["/health"]  # Exclude health endpoint from authentication for monitoring purposes


@app.middleware("http")
async def authenticate_requests(request: Request, call_next) -> JSONResponse:
    # Check if path should be excluded from authentication
    if any(request.url.path.startswith(path) for path in EXCLUDE_PATHS):
        return await call_next(request)

    try:
        # Apply authentication
        authenticated_request = await authMiddleware(request)
        # Continue with the request
        return await call_next(authenticated_request)

    except HTTPException as exc:
        # Handle authentication errors
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    except Exception:
        # Handle unexpected errors
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal server error"},
        )


# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check() -> JSONResponse:
    """Health check endpoint for the query service itself"""
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

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """
    Custom handler to log Pydantic validation errors.
    This will log the detailed error and the body of the failed request.
    """
    # Log the full error details from the exception

    try:
        # Try to log the request body
        await request.json()
    except Exception:
        print("Could not parse request body as JSON.")

    # You can customize the response, but for now, we'll just re-raise
    # or return the default FastAPI response structure.
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()},
    )


# Include routes from routes.py
app.include_router(search_router, prefix="/api/v1")
app.include_router(chatbot_router, prefix="/api/v1")
app.include_router(speech_router, prefix="/api/v1")
app.include_router(agent_router, prefix="/api/v1/agent")
app.include_router(toolsets_router)
app.include_router(health_router, prefix="/api/v1")
app.include_router(ai_models_registry_router, prefix="/api/v1")


def run(host: str = "0.0.0.0", port: int = 8000, reload: bool = True) -> None:
    """Run the application"""
    uvicorn.run(
        "app.query_main:app", host=host, port=port, log_level="info", reload=reload
    )

if __name__ == "__main__":
    run(reload=False)
