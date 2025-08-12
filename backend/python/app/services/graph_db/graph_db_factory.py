"""
Factory for creating graph database service instances.
This provides a centralized way to create different graph database services.
"""

from logging import Logger
from typing import Optional, Union

from app.config.configuration_service import ConfigurationService
from app.services.graph_db.arango.arango import ArangoService
from app.services.graph_db.arango.config import ArangoConfig
from app.services.graph_db.interface.graph_db import IGraphService


class GraphDBFactory:
    """Factory for creating graph database service instances"""

    @staticmethod
    async def create_arango_service(
        logger: Logger,
        config_service: Union[ArangoConfig, ConfigurationService]
    ) -> ArangoService:
        """
        Create an ArangoService instance using the factory method.
        Args:
            logger: Logger instance
            arango_config: ArangoConfig instance
            config_service: ConfigurationService instance
        Returns:
            ArangoService: Initialized ArangoService instance
        """
        return await ArangoService.create(logger, config_service)

    @staticmethod
    async def create_service(
        service_type: str,
        logger: Logger,
        config_service: Union[ArangoConfig, ConfigurationService]
    ) -> Optional[IGraphService]:
        """
        Create a graph database service based on the service type.
        Args:
            service_type: Type of service to create ('arango', etc.)
            logger: Logger instance
            config_service: Union[ArangoConfig, ConfigurationService] instance
        Returns:
            IGraphService: Initialized graph database service instance
        """
        if service_type.lower() == "arango":
            return await GraphDBFactory.create_arango_service(logger, config_service)
        else:
            logger.error(f"Unsupported graph database service type: {service_type}")
            return None
