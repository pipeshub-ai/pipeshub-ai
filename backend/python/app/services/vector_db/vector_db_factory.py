"""
Factory for creating vector database service instances.
This provides a centralized way to create different vector database services.
"""

from app.config.configuration_service import ConfigurationService
from app.services.vector_db.interface.vector_db import IVectorDBService
from app.services.vector_db.qdrant.config import QdrantConfig
from app.services.vector_db.qdrant.qdrant import QdrantService
from app.utils.logger import create_logger

logger = create_logger("vector_db_factory")

class VectorDBFactory:
    """Factory for creating vector database service instances"""

    @staticmethod
    async def create_qdrant_service_sync(
        config: ConfigurationService | QdrantConfig,
    ) -> QdrantService:
        """
        Create a QdrantService instance using the factory method.
        Args:
            config: ConfigurationService or QdrantConfig
        Returns:
            QdrantService: Initialized QdrantService instance
        """
        return await QdrantService.create_sync(config)

    @staticmethod
    async def create_qdrant_service_async(
        config: ConfigurationService | QdrantConfig,
        ) -> QdrantService:
        """
        Create a QdrantService instance with async client using the factory method.
        Args:
            config: ConfigurationService or QdrantConfig
        Returns:
            QdrantService: Initialized QdrantService instance with async client
        """
        return await QdrantService.create_async(config)

    @staticmethod
    async def create_vector_db_service(
        service_type: str,
        config: ConfigurationService | QdrantConfig,
        is_async: bool = True
    ) -> IVectorDBService:
        """
        Create a vector database service based on the service type.
        Args:
            service_type: Type of service to create ('qdrant', etc.)
            config: ConfigurationService or QdrantConfig

        Returns:
            IVectorDBService: Initialized vector database service instance
        """
        if service_type.lower() == "qdrant":
            if is_async:
                return await VectorDBFactory.create_qdrant_service_async(config)
            else:
                return await VectorDBFactory.create_qdrant_service_sync(config)
        else:
            logger.error(f"Unsupported vector database service type: {service_type}")
            raise ValueError(f"Unsupported vector database service type: {service_type}")
