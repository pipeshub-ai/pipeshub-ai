"""Legacy VectorDBFactory — thin wrapper kept for backward compatibility.

New code should use VectorDBProviderFactory directly.
"""

from app.config.configuration_service import ConfigurationService
from app.services.vector_db.interface.vector_db import IVectorDBService
from app.services.vector_db.vector_db_provider_factory import VectorDBProviderFactory
from app.utils.logger import create_logger

logger = create_logger("vector_db_factory")


class VectorDBFactory:
    """Factory shim — delegates to VectorDBProviderFactory private creators."""

    @staticmethod
    async def create_vector_db_service(
        service_type: str,
        config: ConfigurationService,
        is_async: bool = True,  # kept for backward compat; always async now
    ) -> IVectorDBService:
        """Create an IVectorDBService by explicit service_type string."""
        _type = service_type.lower()
        if _type == "qdrant":
            return await VectorDBProviderFactory._create_qdrant(logger, config)
        if _type == "opensearch":
            return await VectorDBProviderFactory._create_opensearch(logger, config)
        if _type == "redis":
            return await VectorDBProviderFactory._create_redis(logger, config)
        raise ValueError(f"Unsupported vector DB service type: {service_type}")

    @staticmethod
    async def create_qdrant_service_sync(config: ConfigurationService) -> IVectorDBService:
        return await VectorDBFactory.create_vector_db_service("qdrant", config)

    @staticmethod
    async def create_qdrant_service_async(config: ConfigurationService) -> IVectorDBService:
        return await VectorDBFactory.create_vector_db_service("qdrant", config)
