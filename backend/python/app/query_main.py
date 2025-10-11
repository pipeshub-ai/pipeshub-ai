from contextlib import asynccontextmanager
from typing import AsyncGenerator, List

import httpx
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.agents.db.tools_db import ToolsDBManager
from app.agents.router.router import router as tools_router
from app.agents.tools.registry import _global_tools_registry
from app.api.middlewares.auth import authMiddleware
from app.api.routes.agent import router as agent_router
from app.api.routes.chatbot import router as chatbot_router
from app.api.routes.health import router as health_router
from app.api.routes.search import router as search_router
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import DefaultEndpoints, config_node_constants
from app.containers.query import QueryAppContainer
from app.services.graph_db.arango.config import ArangoConfig
from app.services.messaging.kafka.utils.utils import KafkaUtils
from app.services.messaging.messaging_factory import MessagingFactory
from app.utils.time_conversion import get_epoch_timestamp_in_ms

container = QueryAppContainer.init("query_service")


async def initialize_container(container: QueryAppContainer) -> bool:
    """Initialize container resources"""
    logger = container.logger()
    logger.info("🚀 Initializing application resources")

    try:
        # Connect to ArangoDB and Redis
        logger.info("Connecting to ArangoDB")
        arango_service = await container.arango_service()
        if arango_service:
            arango_connected = await arango_service.connect()
            if not arango_connected:
                raise Exception("Failed to connect to ArangoDB")
            logger.info("✅ Connected to ArangoDB")
        else:
            raise Exception("Failed to connect to ArangoDB")

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

async def start_kafka_consumers(app_container: QueryAppContainer) -> List:
    """Start all Kafka consumers at application level"""
    logger = app_container.logger()
    consumers = []

    try:
    # 1. Create AI Config Consumer
        logger.info("🚀 Starting AI Config Kafka Consumer...")
        aiconfig_kafka_config = await KafkaUtils.create_aiconfig_kafka_consumer_config(app_container)
        aiconfig_kafka_consumer = MessagingFactory.create_consumer(
            broker_type="kafka",
            logger=logger,
            config=aiconfig_kafka_config
        )
        aiconfig_message_handler = await KafkaUtils.create_aiconfig_message_handler(app_container)
        await aiconfig_kafka_consumer.start(aiconfig_message_handler)
        consumers.append(("aiconfig", aiconfig_kafka_consumer))
        logger.info("✅ AI Config Kafka consumer started")

        logger.info(f"✅ All {len(consumers)} Kafka consumers started successfully")
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

async def stop_kafka_consumers(container: QueryAppContainer) -> bool:
    """Stop all Kafka consumers"""
    logger = container.logger()
    consumers = getattr(container, 'kafka_consumers', [])
    for name, consumer in consumers:
        try:
            await consumer.stop()
            logger.info(f"✅ {name.title()} Kafka consumer stopped")
            return True
        except Exception as e:
            logger.error(f"❌ Error stopping {name} consumer: {str(e)}")
            return False
        finally:
            # Clear the consumers list
            if hasattr(container, 'kafka_consumers'):
                container.kafka_consumers = []
            return True

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan context manager for FastAPI"""

    # Initialize container
    app_container = await get_initialized_container()
    # Store container in app state for access in dependencies
    app.container = app_container

    logger = app.container.logger()
    logger.debug("🚀 Starting retrieval application")

    # Start all Kafka consumers centrally
    try:
        consumers = await start_kafka_consumers(app_container)
        app_container.kafka_consumers = consumers
        logger.info("✅ All Kafka consumers started successfully")
    except Exception as e:
        logger.error(f"❌ Failed to start Kafka consumers: {str(e)}")
        raise

    arango_service = await app_container.arango_service()

    # Get all organizations
    orgs = await arango_service.get_all_orgs()
    if not orgs:
        logger.info("No organizations found in the system")
    else:
        logger.info("Found organizations in the system")
        retrieval_service = await container.retrieval_service()
        await retrieval_service.get_embedding_model_instance()

    arango_config_dict = await container.config_service().get_config(
        config_node_constants.ARANGODB.value
    )
    arango_config = ArangoConfig(**arango_config_dict)

    # Create ToolsDBManager
    logger.info("Creating ToolsDBManager...")
    tools_db = await ToolsDBManager.create(logger, arango_config)

    # Connect to ArangoDB
    logger.info("Connecting to ArangoDB...")
    await tools_db.graph_service.connect()

    # initialize collections
    await tools_db.graph_service.create_collection("tools")
    await tools_db.graph_service.create_collection("tools_ctags")

    # Use the warmup class to import all tools automatically
    logger.info("Using tools warmup to register all available tools...")
    from app.agents.tools.discovery import discover_tools

    discovery_results = discover_tools(logger)
    logger.info(f"Discovery completed: {discovery_results['total_tools']} tools registered")

    # Create a sample tool registry (this would normally come from your actual registry)
    logger.info("Setting up sample tool registry...")
    tool_registry = _global_tools_registry


    # Sync tools from registry to ArangoDB
    logger.info("Syncing tools from registry to ArangoDB...")
    await tools_db.sync_tools_from_registry(tool_registry)

    # List all tools in the registry
    registry_tools = tool_registry.list_tools()
    logger.info(f"Tools in registry: {registry_tools}")

    yield
    # Shutdown
    logger.info("🔄 Shutting down application")
    # Stop all Kafka consumers
    try:
        await stop_kafka_consumers(app_container)
        logger.info("✅ All Kafka consumers stopped")
    except Exception as e:
        logger.error(f"❌ Error stopping Kafka consumers: {str(e)}")


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
        response = await call_next(authenticated_request)
        return response

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

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """
    Custom handler to log Pydantic validation errors.
    This will log the detailed error and the body of the failed request.
    """
    # Log the full error details from the exception
    print(f"Pydantic validation error for {request.method} {request.url}: {exc.errors()}")

    try:
        # Try to log the request body
        body = await request.json()
        print(f"Failing request body: {body}")
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
app.include_router(agent_router, prefix="/api/v1/agent")
app.include_router(health_router, prefix="/api/v1")
app.include_router(tools_router, prefix="/api/v1")


def run(host: str = "0.0.0.0", port: int = 8000, reload: bool = True) -> None:
    """Run the application"""
    uvicorn.run(
        "app.query_main:app", host=host, port=port, log_level="info", reload=reload
    )


if __name__ == "__main__":
    run(reload=False)
