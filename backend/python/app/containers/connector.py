import os

from dependency_injector import containers, providers

from app.config.configuration_service import ConfigurationService
from app.config.constants.service import config_node_constants
from app.config.providers.encrypted_store import EncryptedKeyValueStore
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.connectors.services.base_arango_service import BaseArangoService
from app.connectors.services.kafka_service import KafkaService
from app.containers.container import BaseAppContainer
from app.containers.utils.utils import ContainerUtils
from app.core.celery_app import CeleryApp
from app.core.signed_url import SignedUrlConfig, SignedUrlHandler
from app.health.health import Health
from app.migrations.all_team_migration import run_all_team_migration
from app.services.graph_db.graph_db_provider_factory import GraphDBProviderFactory
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.logger import create_logger


class ConnectorAppContainer(BaseAppContainer):
    """Dependency injection container for the connector application."""

    # Override logger with service-specific name
    logger = providers.Singleton(create_logger, "connector_service")
    container_utils = ContainerUtils()
    key_value_store = providers.Singleton(EncryptedKeyValueStore, logger=logger)

    # Override config_service to use the service-specific logger
    config_service = providers.Singleton(ConfigurationService, logger=logger, key_value_store=key_value_store)

    # Override arango_client to use the service-specific config_service
    arango_client = providers.Resource(
        BaseAppContainer._create_arango_client, config_service=config_service
    )

    # Core Services
    kafka_service = providers.Singleton(
        KafkaService, logger=logger, config_service=config_service
    )

    # First create an async factory for the connected BaseArangoService
    @staticmethod
    async def _create_arango_service(logger, arango_client, kafka_service, config_service) -> BaseArangoService | None:
        """Async factory to create and connect BaseArangoService (with schema init allowed)"""
        # Skip ArangoDB service creation if using a different graph database
        data_store = os.getenv("DATA_STORE", "arangodb").lower()
        if data_store != "arangodb":
            logger.info(f"⏭️ Skipping ArangoDB service creation (DATA_STORE={data_store})")
            return None

        service = BaseArangoService(
            logger,
            arango_client,
            config_service,
            kafka_service,
            enable_schema_init=True,
        )
        await service.connect()
        return service


    arango_service = providers.Resource(
        _create_arango_service,
        logger=logger,
        arango_client=arango_client,
        kafka_service=kafka_service,
        config_service=config_service,
    )

    # Graph Database Provider via Factory (HTTP mode - fully async)
    @staticmethod
    async def _create_graphDB_provider(logger, config_service) -> IGraphDBProvider:
        """Async factory to create graph database provider"""
        return await GraphDBProviderFactory.create_provider(
            logger=logger,
            config_service=config_service,
        )

    graph_provider = providers.Resource(
        _create_graphDB_provider,
        logger=logger,
        config_service=config_service,
    )

    # Graph Data Store - Transaction-based data access layer
    @staticmethod
    async def _create_data_store(logger, graph_provider) -> GraphDataStore:
        """Async factory to create GraphDataStore with resolved graph_provider"""
        return GraphDataStore(logger, graph_provider)

    data_store = providers.Resource(
        _create_data_store,
        logger=logger,
        graph_provider=graph_provider,
    )

    # Note: KnowledgeBaseService is created in the router's get_kb_service() using
    # request.app.state.graph_provider and container.kafka_service (async Resource
    # does not inject well into Singleton). graph_provider no longer depends on kafka_service.
    # Note: KnowledgeHubService is created manually in the router's get_knowledge_hub_service()
    # helper function because it depends on async graph_provider which doesn't work well
    # with dependency_injector's Factory/Resource providers.

    # Celery and Tasks
    celery_app = providers.Singleton(
        CeleryApp, logger=logger, config_service=config_service
    )

    # Signed URL Handler
    signed_url_config = providers.Resource(
        SignedUrlConfig.create, config_service=config_service
    )
    signed_url_handler = providers.Singleton(
        SignedUrlHandler,
        logger=logger,
        config=signed_url_config,
        config_service=config_service,
    )

    feature_flag_service = providers.Singleton(container_utils.create_feature_flag_service, config_service=config_service)

    # Connector-specific wiring configuration
    wiring_config = containers.WiringConfiguration(
        modules=[
            "app.core.celery_app",
            "app.connectors.api.router",
            "app.connectors.sources.localKB.api.kb_router",
            "app.connectors.sources.localKB.api.knowledge_hub_router",
            "app.connectors.api.middleware",
            "app.core.signed_url",
        ]
    )

async def initialize_container(container) -> bool:
    """Initialize container resources with health checks."""

    logger = container.logger()
    config_service = container.config_service()

    logger.info("🚀 Initializing application resources")
    try:
        await Health.system_health_check(container)

        # Write deployment config to KV store so Node.js can read it
        data_store_type = os.getenv("DATA_STORE", "arangodb").lower()
        try:
            existing_deployment = await config_service.get_config(
                config_node_constants.DEPLOYMENT.value, default={}
            ) or {}
            existing_deployment["dataStoreType"] = data_store_type
            existing_deployment["vectorDbType"] = "qdrant"
            await config_service.set_config(
                config_node_constants.DEPLOYMENT.value, existing_deployment
            )
            logger.info(f"✅ Deployment config written to KV store (dataStoreType={data_store_type})")
        except Exception as e:
            logger.warning(f"⚠️ Failed to write deployment config to KV store: {e}")

        # Conditionally initialize ArangoDB service based on DATA_STORE
        if data_store_type == "arangodb":
            logger.info("Ensuring ArangoDB service is initialized")
            arango_service = await container.arango_service()
            if not arango_service:
                raise Exception("Failed to initialize ArangoDB service")
            logger.info("✅ ArangoDB service initialized")
        else:
            logger.info(f"⏭️ Skipping ArangoDB service init (DATA_STORE={data_store_type})")

        logger.info("Ensuring graph database provider is initialized")
        data_store = await container.data_store()
        if not data_store:
            raise Exception("Failed to initialize data store")
        logger.info("✅ Data store initialized")

        # Schema init: collections, graph, departments seed
        await data_store.graph_provider.ensure_schema()
        logger.info("✅ Schema ensured")

        logger.info("✅ Container initialization completed successfully")


        # Run All Team migration (DB-agnostic, runs for both ArangoDB and Neo4j)
        try:
            logger.info("🔄 Running All team migration...")
            
            migration_result = await run_all_team_migration(
                graph_provider=data_store.graph_provider,
                config_service=config_service,
                logger=logger
            )
            
            if migration_result.get("success"):
                if migration_result.get("skipped"):
                    logger.info("✅ All team migration already completed")
                else:
                    orgs_processed = migration_result.get("orgs_processed", 0)
                    teams_created = migration_result.get("teams_created", 0)
                    logger.info(
                        f"✅ All team migration completed: "
                        f"{orgs_processed} orgs processed, {teams_created} All teams ensured"
                    )
            else:
                error_msg = migration_result.get("error", "Unknown error")
                logger.error(f"❌ All team migration failed: {error_msg}")
        except Exception as e:
            logger.error(f"❌ All team migration error: {e}")

        return True

    except Exception as e:
        logger.error(f"❌ Container initialization failed: {str(e)}")
        raise
