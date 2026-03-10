from dependency_injector import containers, providers  # type: ignore
from dotenv import load_dotenv  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.config.providers.encrypted_store import EncryptedKeyValueStore
from app.connectors.services.kafka_service import KafkaService
from app.containers.container import BaseAppContainer
from app.containers.utils.utils import ContainerUtils
from app.health.health import Health
from app.services.vector_db.const.const import VECTOR_DB_COLLECTION_NAME
from app.utils.logger import create_logger

load_dotenv(override=True)


class IndexingAppContainer(BaseAppContainer):
    """Dependency injection container for the indexing application."""

    # Override logger with service-specific name
    logger = providers.Singleton(create_logger, "indexing_service")
    container_utils = ContainerUtils()
    # Override config_service to use the service-specific logger
    key_value_store = providers.Singleton(EncryptedKeyValueStore, logger=logger)
    config_service = providers.Singleton(ConfigurationService, logger=logger, key_value_store=key_value_store)

    # Override arango_client to use the service-specific config_service
    arango_client = providers.Resource(
        BaseAppContainer._create_arango_client, config_service=config_service
    )
    kafka_service = providers.Singleton(
        KafkaService, logger=logger, config_service=config_service
    )

    # Graph Database Provider via Factory (HTTP mode - fully async)
    graph_provider = providers.Resource(
        container_utils.create_graph_provider,
        logger=logger,
        config_service=config_service,
    )

    vector_db_service = providers.Resource(
        container_utils.get_vector_db_service,
        config_service=config_service,
    )
    indexing_pipeline = providers.Resource(
        container_utils.create_indexing_pipeline,
        logger=logger,
        config_service=config_service,
        graph_provider=graph_provider,
        vector_db_service=vector_db_service,
    )

    document_extractor = providers.Resource(
        container_utils.create_document_extractor,
        logger=logger,
        graph_provider=graph_provider,
        config_service=config_service,
    )

    blob_storage = providers.Resource(
        container_utils.create_blob_storage,
        logger=logger,
        config_service=config_service,
        graph_provider=graph_provider,
    )

    graphdb = providers.Resource(
        container_utils.create_graphdb,
        graph_provider=graph_provider,
        logger=logger,
    )

    vector_store = providers.Resource(
        container_utils.create_vector_store,
        logger=logger,
        graph_provider=graph_provider,
        config_service=config_service,
        vector_db_service=vector_db_service,
        collection_name=VECTOR_DB_COLLECTION_NAME,
    )

    sink_orchestrator = providers.Resource(
        container_utils.create_sink_orchestrator,
        logger=logger,
        graphdb=graphdb,
        blob_storage=blob_storage,
        vector_store=vector_store,
        graph_provider=graph_provider,
    )

    # Parsers
    parsers = providers.Resource(container_utils.create_parsers, logger=logger)

    # Processor - depends on indexing_pipeline and graph_provider
    processor = providers.Resource(
        container_utils.create_processor,
        logger=logger,
        config_service=config_service,
        indexing_pipeline=indexing_pipeline,
        graph_provider=graph_provider,
        parsers=parsers,
        document_extractor=document_extractor,
        sink_orchestrator=sink_orchestrator,
    )

    event_processor = providers.Resource(
        container_utils.create_event_processor,
        logger=logger,
        processor=processor,
        graph_provider=graph_provider,
        config_service=config_service,
    )

    # Indexing-specific wiring configuration
    wiring_config = containers.WiringConfiguration(
        modules=[
            "app.indexing_main"
        ]
    )

async def initialize_container(container: IndexingAppContainer) -> bool:
    """Initialize container resources"""
    logger = container.logger()
    logger.info("🚀 Initializing application resources")

    try:
        # Ensure connector service is healthy before starting indexing service
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

        await Health.system_health_check(container)
        return True

    except Exception as e:
        logger.error(f"❌ Failed to initialize resources: {str(e)}")
        raise
