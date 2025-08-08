"""
Factory for creating vector database service instances.
This provides a centralized way to create different vector database services.
"""

import logging
from typing import Optional

from app.config.configuration_service import ConfigurationService
from app.services.vector_db.interface.vector_db import IVectorDBService
from app.services.vector_db.qdrant.qdrant import QdrantService


class VectorDBFactory:
    """Factory for creating vector database service instances"""

    @staticmethod
    async def create_qdrant_service(
        logger: logging.Logger,
        config_service: ConfigurationService
    ) -> QdrantService:
        """
        Create a QdrantService instance using the factory method.
        Args:
            logger: Logger instance
            config_service: ConfigurationService instance
        Returns:
            QdrantService: Initialized QdrantService instance
        """
        return await QdrantService.create(logger, config_service)

    @staticmethod
    async def create_service(
        service_type: str,
        logger: logging.Logger,
        config_service: ConfigurationService
    ) -> Optional[IVectorDBService]:
        """
        Create a vector database service based on the service type.
        Args:
            service_type: Type of service to create ('qdrant', etc.)
            logger: Logger instance
            config_service: ConfigurationService instance

        Returns:
            IVectorDBService: Initialized vector database service instance
        """
        if service_type.lower() == "qdrant":
            return await VectorDBFactory.create_qdrant_service(logger, config_service)
        else:
            logger.error(f"Unsupported vector database service type: {service_type}")
            return None
