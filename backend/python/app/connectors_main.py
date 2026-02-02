import asyncio
import traceback
from contextlib import asynccontextmanager
from typing import AsyncGenerator, List

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.middlewares.auth import authMiddleware
from app.api.routes.entity import router as entity_router
from app.config.constants.arangodb import AccountType
from app.connectors.api.router import router
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.connectors.core.base.token_service.startup_service import startup_service
from app.connectors.core.factory.connector_factory import ConnectorFactory
from app.connectors.core.registry.connector_registry import (
    ConnectorRegistry,
)
from app.connectors.sources.localKB.api.kb_router import kb_router
from app.connectors.sources.localKB.api.knowledge_hub_router import knowledge_hub_router
from app.containers.connector import (
    ConnectorAppContainer,
    initialize_container,
)
from app.services.messaging.kafka.utils.utils import KafkaUtils
from app.services.messaging.messaging_factory import MessagingFactory
from app.utils.time_conversion import get_epoch_timestamp_in_ms

container = ConnectorAppContainer.init("connector_service")

async def get_initialized_container() -> ConnectorAppContainer:
    """Dependency provider for initialized container"""
    # Create container instance
    if not hasattr(get_initialized_container, "_initialized"):
        await initialize_container(container)
        # Wire the container after initialization
        container.wire(
            modules=[
                "app.core.celery_app",
                "app.connectors.sources.google.common.sync_tasks",
                "app.connectors.api.router",
                "app.connectors.sources.localKB.api.kb_router",
                "app.connectors.sources.localKB.api.knowledge_hub_router",
                "app.api.routes.entity",
                "app.connectors.api.middleware",
                "app.core.signed_url",
            ]
        )
        setattr(get_initialized_container, "_initialized", True)
        # Start token refresh service at app startup (use graph_provider from data_store)
        try:
            data_store = await container.data_store()
            await startup_service.initialize(container.key_value_store(), data_store.graph_provider)
        except Exception as e:
            container.logger().warning(f"Startup token refresh service failed to initialize: {e}")
    return container


async def resume_sync_services(app_container: ConnectorAppContainer, graph_provider, data_store_provider) -> bool:
    """Resume sync services for users with active sync states"""
    logger = app_container.logger()
    logger.debug("🔄 Checking for sync services to resume")

    try:
        graph_provider = data_store.graph_provider if data_store else (await app_container.data_store()).graph_provider

        # Get all organizations
        orgs = await graph_provider.get_all_orgs(active=True)
        if not orgs:
            logger.info("No organizations found in the system")
            return True

        logger.info("Found %d organizations in the system", len(orgs))

        # Use config_service and data_store_provider passed as parameters (already resolved)
        config_service = app_container.config_service()

        # Process each organization
        for org in orgs:
            org_id = org.get("_key") or org.get("id")
            accountType = org.get("accountType", AccountType.INDIVIDUAL.value)
            enabled_apps = await graph_provider.get_org_apps(org_id)
            app_names = [app["type"].replace(" ", "").lower() for app in enabled_apps]
            logger.info(f"App names: {app_names}")

            logger.info(
                "Processing organization %s with account type %s", org_id, accountType
            )

            # Get users for this organization
            users = await graph_provider.get_users(org_id, active=True)
            logger.info(f"User: {users}")
            if not users:
                logger.info("No users found for organization %s", org_id)
                continue

            logger.info("Found %d users for organization %s", len(users), org_id)

            config_service = app_container.config_service()
            # Use pre-resolved data_store passed from lifespan to avoid coroutine reuse
            # data_store_provider = data_store if data_store else await app_container.data_store()

            # Initialize connectors_map if not already initialized
            if not hasattr(app_container, 'connectors_map'):
                app_container.connectors_map = {}

            for app in enabled_apps:
                connector_id = app.get("_key")

                connector_name = app["type"].lower().replace(" ", "")
                connector = await ConnectorFactory.create_and_start_sync(
                    name=connector_name,
                    logger=logger,
                    data_store_provider=data_store_provider,
                    config_service=config_service,
                    connector_id=connector_id
                )
                if connector:
                    # Store using connector_id as the unique key (not connector_name to avoid conflicts with multiple instances)
                    app_container.connectors_map[connector_id] = connector
                    logger.info(f"{connector_name} connector (id: {connector_id}) initialized for org %s", org_id)

            logger.info("✅ Sync services resumed for org %s", org_id)
        logger.info("✅ Sync services resumed for all orgs")
        return True
    except Exception as e:
        logger.error("❌ Error during sync service resumption: %s", str(e))
        logger.error("❌ Detailed error traceback:\n%s", traceback.format_exc())
        return False

async def initialize_connector_registry(app_container: ConnectorAppContainer, graph_provider) -> ConnectorRegistry:
    """Initialize and sync connector registry with database"""
    logger = app_container.logger()
    logger.info("🔧 Initializing Connector Registry...")

    try:
        registry = ConnectorRegistry(app_container, graph_provider)

        ConnectorFactory.initialize_beta_connector_registry()
        # Register connectors using generic factory
        available_connectors = ConnectorFactory.list_connectors()
        for name, connector_class in available_connectors.items():
            registry.register_connector(connector_class)
        logger.info("✅ Connectors registered")
        logger.info(f"Registered {len(registry._connectors)} connectors")

        # Sync with database
        await registry.sync_with_database()
        logger.info("✅ Connector registry synchronized with database")

        return registry

    except Exception as e:
        logger.error(f"❌ Error initializing connector registry: {str(e)}")
        raise

async def start_messaging_producer(app_container: ConnectorAppContainer) -> None:
    """Start messaging producer and attach it to container"""
    logger = app_container.logger()

    try:
        logger.info("🚀 Starting Messaging Producer...")

        producer_config = await KafkaUtils.create_producer_config(app_container)

        # Create and initialize producer
        messaging_producer = MessagingFactory.create_producer(
            broker_type="kafka",
            logger=logger,
            config=producer_config
        )
        await messaging_producer.initialize()

        # Attach producer to container
        app_container.messaging_producer = messaging_producer

        logger.info("✅ Messaging producer started and attached to container")

    except Exception as e:
        logger.error(f"❌ Error starting messaging producer: {str(e)}")
        raise

async def start_kafka_consumers(app_container: ConnectorAppContainer, graph_provider) -> List:
    """Start all Kafka consumers at application level"""
    logger = app_container.logger()
    consumers = []

    try:
        # 1. Create Entity Consumer
        logger.info("🚀 Starting Entity Kafka Consumer...")
        entity_kafka_config = await KafkaUtils.create_entity_kafka_consumer_config(app_container)
        entity_kafka_consumer = MessagingFactory.create_consumer(
            broker_type="kafka",
            logger=logger,
            config=entity_kafka_config
        )
        entity_message_handler = await KafkaUtils.create_entity_message_handler(app_container, graph_provider)
        await entity_kafka_consumer.start(entity_message_handler)
        consumers.append(("entity", entity_kafka_consumer))
        logger.info("✅ Entity Kafka consumer started")

        # 2. Create Sync Consumer
        logger.info("🚀 Starting Sync Kafka Consumer...")
        sync_kafka_config = await KafkaUtils.create_sync_kafka_consumer_config(app_container)
        sync_kafka_consumer = MessagingFactory.create_consumer(
            broker_type="kafka",
            logger=logger,
            config=sync_kafka_config
        )
        sync_message_handler = await KafkaUtils.create_sync_message_handler(app_container)
        await sync_kafka_consumer.start(sync_message_handler)
        consumers.append(("sync", sync_kafka_consumer))
        logger.info("✅ Sync Kafka consumer started")

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

async def stop_kafka_consumers(container: ConnectorAppContainer) -> None:
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

async def stop_messaging_producer(container: ConnectorAppContainer) -> None:
    """Stop the messaging producer"""
    logger = container.logger()

    try:
        # Get the messaging producer from container
        messaging_producer = getattr(container, 'messaging_producer', None)
        if messaging_producer:
            await messaging_producer.cleanup()
            logger.info("✅ Messaging producer stopped successfully")
        else:
            logger.info("No messaging producer to stop")
    except Exception as e:
        logger.error(f"❌ Error stopping messaging producer: {str(e)}")

async def shutdown_container_resources(container: ConnectorAppContainer) -> None:
    """Shutdown all container resources properly"""
    logger = container.logger()

    try:
        # Stop Kafka consumers
        await stop_kafka_consumers(container)

        # Stop messaging producer
        await stop_messaging_producer(container)

        # Stop startup services (token refresh)
        try:
            await startup_service.shutdown()
        except Exception as e:
            logger.warning(f"Error shutting down startup services: {e}")

        logger.info("✅ All container resources shut down successfully")

    except Exception as e:
        logger.error(f"❌ Error during container resource shutdown: {str(e)}")

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan context manager for FastAPI"""
    # Initialize container
    app_container = await get_initialized_container()
    app.container = app_container  # type: ignore

    app.state.config_service = app_container.config_service()

    # Resolve data_store first - this internally resolves graph_provider
    data_store = await app_container.data_store()
    app.state.graph_provider = data_store.graph_provider

    # Initialize connector registry
    # Use the already-resolved graph_provider from data_store to avoid coroutine reuse
    logger = app_container.logger()
    graph_provider = data_store.graph_provider

    # Start token refresh service at app startup (database-agnostic)
    try:
        await startup_service.initialize(app_container.key_value_store(), graph_provider)
        logger.info("✅ Startup services initialized successfully")
    except Exception as e:
        logger.warning(f"⚠️ Startup token refresh service failed to initialize: {e}")

    # Initialize connector registry - pass already-resolved graph_provider
    registry = await initialize_connector_registry(app_container, graph_provider)
    app.state.connector_registry = registry
    logger.info("✅ Connector registry initialized and synchronized with database")

    # Initialize OAuth config registry (completely independent, no connector registry needed)
    # Note: OAuth registry is populated when connectors are registered above
    from app.connectors.core.registry.oauth_config_registry import (
        get_oauth_config_registry,
    )
    oauth_registry = get_oauth_config_registry()
    app.state.oauth_config_registry = oauth_registry
    logger.info("✅ OAuth config registry initialized")

    # Run OAuth credentials migration (AFTER connector and OAuth registries are initialized)
    # This migration needs OAuth registry to be populated to get OAuth infrastructure fields
    try:
        logger.info("🔄 Running OAuth credentials migration...")
        from app.migrations.oauth_credentials_migration import (
            run_oauth_credentials_migration,
        )

        migration_result = await run_oauth_credentials_migration(
            config_service=app_container.config_service(),
            graph_provider=app.state.graph_provider,
            logger=logger,
            dry_run=False
        )

        if migration_result.get("success"):
            if migration_result.get("skipped"):
                logger.info("✅ OAuth credentials migration already completed")
            else:
                connectors_migrated = migration_result.get("connectors_migrated", 0)
                oauth_configs_created = migration_result.get("oauth_configs_created", 0)
                logger.info(
                    f"✅ OAuth credentials migration completed: "
                    f"{connectors_migrated} connectors migrated, {oauth_configs_created} OAuth configs created"
                )
        else:
            error_msg = migration_result.get("error", "Unknown error")
            logger.error(f"❌ OAuth credentials migration failed: {error_msg}")
    except Exception as e:
        logger.error(f"❌ OAuth credentials migration error: {e}")
        # Don't fail startup - migration is idempotent and can be retried

    logger.debug("🚀 Starting application")

    # Start messaging producer first
    try:
        await start_messaging_producer(app_container)
        logger.info("✅ Messaging producer started successfully")
    except Exception as e:
        logger.error(f"❌ Failed to start messaging producer: {str(e)}")
        raise

    # Start all Kafka consumers centrally - pass already resolved graph_provider
    try:
        consumers = await start_kafka_consumers(app_container, graph_provider)
        app_container.kafka_consumers = consumers
        logger.info("✅ All Kafka consumers started successfully")
    except Exception as e:
        logger.error(f"❌ Failed to start Kafka consumers: {str(e)}")
        raise

    # Resume sync services - pass already resolved graph_provider and data_store
    asyncio.create_task(resume_sync_services(app_container, graph_provider, data_store))

    yield
    logger.info("🔄 Shut down application started")
    # Shutdown all container resources
    try:
        await shutdown_container_resources(app_container)
    except Exception as e:
        logger.error(f"❌ Error during application shutdown: {str(e)}")


# Create FastAPI app with lifespan
app = FastAPI(
    title="Connectors Sync Service",
    description="Service for syncing content from connectors to GraphDB",
    version="1.0.0",
    lifespan=lifespan,
    dependencies=[Depends(get_initialized_container)],
)

# List of paths to exclude from authentication (public endpoints)
# All other paths will require authentication by default
EXCLUDE_PATHS = [
    "/health",  # Health check endpoint
    "/drive/webhook",  # Google Drive webhook (has its own WebhookAuthVerifier)
    "/gmail/webhook",  # Gmail webhook (uses Google Pub/Sub authentication)
    "/admin/webhook",  # Admin webhook (has its own WebhookAuthVerifier)
]

@app.middleware("http")
async def authenticate_requests(request: Request, call_next) -> JSONResponse:
    """
    Authentication middleware that authenticates all requests by default,
    except for paths explicitly excluded (webhooks, health checks, OAuth callbacks).
    """
    logger = app.container.logger()  # type: ignore
    request_path = request.url.path
    logger.debug(f"Middleware processing request: {request_path}")

    # Check if path should be excluded from authentication
    should_exclude = False

    # Check exact path matches for webhooks and health
    if request_path in EXCLUDE_PATHS:
        should_exclude = True
        logger.debug(f"Excluding exact path match: {request_path}")

    # Check for OAuth callback paths (pattern-based exclusion)
    # if "/oauth/callback" in request_path:
    #     should_exclude = True
    #     logger.debug(f"Excluding OAuth callback path: {request_path}")



    # If path should be excluded, skip authentication
    if should_exclude:
        logger.debug(f"Skipping authentication for excluded path: {request_path}")
        return await call_next(request)

    # All other paths require authentication
    try:
        logger.debug(f"Applying authentication for path: {request_path}")
        authenticated_request = await authMiddleware(request)
        response = await call_next(authenticated_request)
        return response

    except HTTPException as exc:
        # Handle authentication errors
        logger.warning(f"Authentication failed for {request_path}: {exc.detail}")
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    except Exception as e:
        # Handle unexpected errors
        logger.error(f"Unexpected error during authentication for {request_path}: {str(e)}", exc_info=True)
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


@router.get("/health")
async def health_check() -> JSONResponse:
    """Basic health check endpoint"""
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
                "status": "fail",
                "error": str(e),
                "timestamp": get_epoch_timestamp_in_ms(),
            },
        )


# Include routes - more specific routes first
app.include_router(entity_router)
app.include_router(kb_router)
app.include_router(knowledge_hub_router)
app.include_router(router)



# Global error handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger = app.container.logger()  # type: ignore
    logger.error("Global error: %s", str(exc), exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"status": "error", "message": str(exc), "path": request.url.path},
    )


def run(host: str = "0.0.0.0", port: int = 8088, workers: int = 1, reload: bool = True) -> None:
    """Run the application"""
    uvicorn.run(
        "app.connectors_main:app",
        host=host,
        port=port,
        log_level="info",
        reload=reload,
        workers=workers,
    )


if __name__ == "__main__":
    run(reload=False)
