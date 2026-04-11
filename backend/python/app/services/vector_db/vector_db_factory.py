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
        return await QdrantService.create_sync(config)

    @staticmethod
    async def create_qdrant_service_async(
        config: ConfigurationService | QdrantConfig,
        ) -> QdrantService:
        return await QdrantService.create_async(config)

    @staticmethod
    async def create_vector_db_service(
        service_type: str,
        config: ConfigurationService,
        is_async: bool = True
    ) -> IVectorDBService:
        """
        Create a vector database service based on the service type.
        Args:
            service_type: Type of service to create ('qdrant', 'opensearch', etc.)
            config: ConfigurationService or backend-specific config
        Returns:
            IVectorDBService: Initialized vector database service instance
        """
        service_type_lower = service_type.lower()

        if service_type_lower == "qdrant":
            if is_async:
                return await VectorDBFactory.create_qdrant_service_async(config)
            else:
                return await VectorDBFactory.create_qdrant_service_sync(config)

        elif service_type_lower == "opensearch":
            from app.services.vector_db.opensearch.opensearch import OpenSearchService
            return await OpenSearchService.create(config, is_async=is_async)

        else:
            logger.error(f"Unsupported vector database service type: {service_type}")
            raise ValueError(f"Unsupported vector database service type: {service_type}")
