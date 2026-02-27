import asyncio
import os
from typing import Optional, Type, TypeVar, Union

from arango import ArangoClient  # type: ignore
from dependency_injector import containers, providers  # type: ignore
from redis.asyncio import Redis
from redis.asyncio.cluster import RedisCluster

from app.config.configuration_service import ConfigurationService
from app.config.constants.service import config_node_constants
from app.utils.logger import create_logger
from app.utils.redis_util import create_redis_client, is_cluster_mode

T = TypeVar("T", bound="BaseAppContainer")


class BaseAppContainer(containers.DeclarativeContainer):
    """Base container with common providers and factory methods for all services."""

    # Common locks for cache access
    service_creds_lock = providers.Singleton(asyncio.Lock)
    user_creds_lock = providers.Singleton(asyncio.Lock)

    # Common logger provider - will be overridden by child containers
    logger = providers.Singleton(create_logger, "base_service")

    # Common configuration service
    config_service = providers.Singleton(ConfigurationService, logger=logger)

    # Common factory methods for external services
    @staticmethod
    async def _create_arango_client(config_service) -> Optional[ArangoClient]:
        """Async factory method to initialize ArangoClient.

        Returns None if DATA_STORE is set to neo4j to avoid unnecessary connection.
        """
        data_store = os.getenv("DATA_STORE", "arangodb").lower()

        if data_store == "neo4j":
            logger = create_logger("base_service")
            logger.info("⏭️  Skipping ArangoDB client initialization (DATA_STORE=neo4j)")
            return None

        arangodb_config = await config_service.get_config(
            config_node_constants.ARANGODB.value
        )
        hosts = arangodb_config.get("url")

        if not hosts:
            logger = create_logger("base_service")
            logger.warning("⚠️  ArangoDB URL not found in config, skipping initialization")
            return None

        return ArangoClient(hosts=hosts)

    @staticmethod
    async def _create_redis_client(config_service) -> Union[Redis, RedisCluster]:
        """Async factory method to initialize Redis client.
        
        Supports both standalone Redis and Redis Cluster modes based on configuration.
        """
        logger = create_logger("base_service")
        redis_config = await config_service.get_config(
            config_node_constants.REDIS.value
        )
        
        cluster_mode = is_cluster_mode(redis_config)
        client = await create_redis_client(
            redis_config,
            decode_responses=True,
            encoding="utf-8",
        )
        
        mode_str = "Cluster" if cluster_mode else "Standalone"
        logger.info(f"✅ Redis client created in {mode_str} mode")
        return client

    # Common external service providers
    arango_client = providers.Resource(
        _create_arango_client, config_service=config_service
    )
    redis_client = providers.Resource(
        _create_redis_client, config_service=config_service
    )

    # Note: Each service container should define its own wiring_config
    # based on its specific module dependencies

    @classmethod
    def init(cls: Type[T], service_name: str) -> T:
        """Initialize the container with the given service name."""
        container = cls()
        container.logger().info(f"🚀 Initializing {cls.__name__} for {service_name}")
        return container

