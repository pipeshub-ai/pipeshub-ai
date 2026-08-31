from dependency_injector import containers, providers

from app.config.configuration_service import ConfigurationService
from app.config.providers.encrypted_store import EncryptedKeyValueStore
from app.containers.container import BaseAppContainer
from app.containers.utils.utils import ContainerUtils
from app.modules.reranker.reranker import RerankerService
from app.utils.logger import create_logger


class QueryAppContainer(BaseAppContainer):
    """Dependency injection container for the query application."""

    # Override logger with service-specific name
    logger = providers.Singleton(create_logger, "query_service")
    container_utils = ContainerUtils()
    key_value_store = providers.Singleton(EncryptedKeyValueStore, logger=logger)

    # Override config_service to use the service-specific logger
    config_service = providers.Singleton(ConfigurationService, logger=logger, key_value_store=key_value_store)

    # Caches the accessible-record maps every search resolves; falls back to
    # live queries when Redis is unavailable or the kill-switch is set.
    accessible_records_cache = providers.Resource(
        container_utils.create_accessible_records_cache,
        logger=logger,
        config_service=config_service,
    )

    # Graph Database Provider via Factory (HTTP mode - fully async)
    graph_provider = providers.Resource(
        container_utils.create_graph_provider,
        logger=logger,
        config_service=config_service,
        accessible_records_cache=accessible_records_cache,
    )

    vector_db_service =  providers.Resource(
        container_utils.get_vector_db_service,
        config_service=config_service,
    )

    blob_store = providers.Resource(
        container_utils.create_blob_storage,
        logger=logger,
        config_service=config_service,
        graph_provider=graph_provider,
    )

    collection_registry = providers.Resource(
        container_utils.create_collection_registry,
        logger=logger,
        config_service=config_service,
        vector_db_service=vector_db_service,
    )

    retrieval_service = providers.Resource(
        container_utils.create_retrieval_service,
        config_service=config_service,
        logger=logger,
        vector_db_service=vector_db_service,
        graph_provider=graph_provider,
        blob_store=blob_store,
        collection_registry=collection_registry,
    )
    reranker_service = providers.Singleton(
        RerankerService,
        model_name="BAAI/bge-reranker-base",  # Choose model based on speed/accuracy needs
    )

    # Query-specific wiring configuration
    wiring_config = containers.WiringConfiguration(
        modules=[
            "app.api.routes.search",
            "app.api.routes.chatbot",
            "app.modules.retrieval.retrieval_service"
        ]
    )
