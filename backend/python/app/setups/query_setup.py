from arango import ArangoClient
from dependency_injector import containers, providers
from qdrant_client import QdrantClient

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    QdrantCollectionNames,
)
from app.config.constants.service import config_node_constants
from app.config.providers.etcd.etcd3_encrypted_store import Etcd3EncryptedKeyValueStore
from app.modules.reranker.reranker import RerankerService
from app.modules.retrieval.retrieval_arango import ArangoService
from app.modules.retrieval.retrieval_service import RetrievalService
from app.services.ai_config_handler import RetrievalAiConfigHandler
from app.utils.logger import create_logger


class QueryAppContainer(containers.DeclarativeContainer):
    """Dependency injection container for the application."""

    # Log when container is initialized
    logger = providers.Singleton(create_logger, "query_service")

    logger().info("🚀 Initializing AppContainer")
    key_value_store = providers.Singleton(Etcd3EncryptedKeyValueStore, logger=logger)
    config_service = providers.Singleton(ConfigurationService, logger=logger, key_value_store=key_value_store)
    logger().info("🚀 Initializing QueryAppContainer")

    async def _fetch_arango_host(config_service: ConfigurationService) -> str:
        """Fetch ArangoDB host URL from etcd asynchronously."""
        arango_config = await config_service.get_config(
            config_node_constants.ARANGODB.value
        )
        return arango_config["url"]

    async def _create_arango_client(config_service: ConfigurationService) -> ArangoClient:
        """Async factory method to initialize ArangoClient."""
        # TODO: Remove this QueryAppContainer usage
        hosts = await QueryAppContainer._fetch_arango_host(config_service)
        return ArangoClient(hosts=hosts)

    arango_client = providers.Resource(
        _create_arango_client, config_service=config_service
    )

    # First create an async factory for the connected ArangoService
    async def _create_arango_service(logger, arango_client, config_service: ConfigurationService) -> ArangoService:
        """Async factory to create and connect ArangoService"""
        service = ArangoService(logger, arango_client, config_service)
        await service.connect()
        return service

    arango_service = providers.Resource(
        _create_arango_service,
        logger=logger,
        arango_client=arango_client,
        config_service=config_service,
    )

    # Vector search service
    async def _get_qdrant_config(config_service: ConfigurationService) -> dict:
        """Async factory method to get Qdrant configuration."""
        qdrant_config = await config_service.get_config(
            config_node_constants.QDRANT.value
        )
        return {
            "apiKey": qdrant_config["apiKey"],
            "host": qdrant_config["host"],
            "grpcPort": qdrant_config["grpcPort"],
        }

    qdrant_config = providers.Resource(
        _get_qdrant_config, config_service=config_service
    )

    async def _get_qdrant_client(qdrant_config) -> QdrantClient:
        """Async factory method to get Qdrant client."""
        return QdrantClient(
            host=qdrant_config["host"],
            grpc_port=qdrant_config["grpcPort"],
            api_key=qdrant_config["apiKey"],
            prefer_grpc=True,
            https=False,
            timeout=180,
        )


    qdrant_client =  providers.Resource(
        _get_qdrant_client,
        qdrant_config=qdrant_config,
    )

    # Vector search service
    async def _create_retrieval_service(config_service: ConfigurationService, logger, qdrant_client) -> RetrievalService:
        """Async factory for RetrievalService"""
        service = RetrievalService(
            logger=logger,
            config_service=config_service,
            collection_name=QdrantCollectionNames.RECORDS.value,
            qdrant_client=qdrant_client,
        )
        return service

    retrieval_service = providers.Resource(
        _create_retrieval_service,
        config_service=config_service,
        logger=logger,
        qdrant_client=qdrant_client,
    )

    llm_config_handler = providers.Singleton(
        RetrievalAiConfigHandler,
        logger=logger,
        config_service=config_service,
        retrieval_service=retrieval_service,
    )

    reranker_service = providers.Singleton(
        RerankerService,
        model_name="BAAI/bge-reranker-base",  # Choose model based on speed/accuracy needs
    )
