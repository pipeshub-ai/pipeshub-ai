import asyncio
from typing import Type, TypeVar

from arango import ArangoClient  # type: ignore
from dependency_injector import containers, providers  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.config.constants.service import config_node_constants
from app.utils.logger import create_logger

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
    async def _create_arango_client(config_service) -> ArangoClient:
        """Async factory method to initialize ArangoClient."""
        arangodb_config = await config_service.get_config(
            config_node_constants.ARANGODB.value
        )
        hosts = arangodb_config["url"]
        return ArangoClient(hosts=hosts)

    # Note: Each service container should define its own wiring_config
    # based on its specific module dependencies

    @classmethod
    def init(cls: Type[T], service_name: str) -> T:
        """Initialize the container with the given service name."""
        container = cls()
        container.logger().info(f"🚀 Initializing {cls.__name__} for {service_name}")
        return container
