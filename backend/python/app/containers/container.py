import asyncio
import os
from collections.abc import AsyncIterator
from typing import Optional, Type, TypeVar

from arango import ArangoClient  # type: ignore
from dependency_injector import containers, providers  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.config.constants.service import config_node_constants
from app.services.redis.config import RedisConnectionConfig
from app.services.redis.connection_provider import IRedisConnectionProvider
from app.services.redis.connection_provider_factory import (
    get_prepared_redis_provider,
)
from app.utils.logger import create_logger

T = TypeVar("T", bound="BaseAppContainer")


async def _build_redis_provider(
    config_service: ConfigurationService,
) -> AsyncIterator[IRedisConnectionProvider]:
    """Resolve the shared connection provider from the stored Redis config."""
    redis_config = await config_service.get_redis_config()
    # `get_prepared_redis_provider`, not `get_redis_provider`: a provider with
    # rotating credentials (an EE MemoryDB provider resolving IAM tokens)
    # needs its one-time async `prepare()` before any client is built.
    provider = await get_prepared_redis_provider(
        RedisConnectionConfig.from_host_port(
            host=redis_config.host,
            port=redis_config.port,
            password=redis_config.password,
            db=redis_config.db,
            tls=redis_config.tls,
        )
    )
    yield provider
    await provider.close()


class BaseAppContainer(containers.DeclarativeContainer):
    """Base container with common providers and factory methods for all services."""

    # Common locks for cache access
    service_creds_lock = providers.Singleton(asyncio.Lock)
    user_creds_lock = providers.Singleton(asyncio.Lock)

    # Common logger provider - will be overridden by child containers
    logger = providers.Singleton(create_logger, "base_service")

    # Common configuration service
    config_service = providers.Singleton(ConfigurationService, logger=logger)

    # Shared `IRedisConnectionProvider` (Phase 5, R11). Resolved from the
    # stored Redis config, not `RedisConnectionConfig.from_env()`: every real
    # call site builds its config from `ConfigurationService.get_redis_config()`
    # (host/port/credentials live in the encrypted KV store, not the env), and
    # a provider built from a different fingerprint would be a *second*
    # provider -- on cluster, a second connection to every node.
    # `get_redis_provider()` caches by fingerprint, so this returns the same
    # instance those call sites already share.
    redis_provider = providers.Resource(_build_redis_provider, config_service)

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

    # Note: Each service container should define its own wiring_config
    # based on its specific module dependencies

    @classmethod
    def init(cls: Type[T], service_name: str) -> T:
        """Initialize the container with the given service name."""
        container = cls()
        container.logger().info(f"🚀 Initializing {cls.__name__} for {service_name}")
        return container
