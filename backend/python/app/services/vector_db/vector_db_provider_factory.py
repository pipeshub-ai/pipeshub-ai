"""Vector Database Provider Factory.

Creates the correct IVectorDBService implementation based on the
``VECTOR_DB_TYPE`` environment variable.  Config credentials are fetched
from the ConfigurationService (KV store), with environment-variable fallbacks,
mirroring the GraphDBProviderFactory pattern.

Supported providers (VECTOR_DB_TYPE values)
--------------------------------------------
``qdrant``     – Qdrant (default) — sparse + dense, native RRF
``opensearch`` – OpenSearch >= 2.19 — dense KNN + BM25, RRF pipeline
``redis``      – Redis >= 8.4 — dense KNN + BM25, native FT.HYBRID RRF
"""

import os
from logging import Logger

from app.config.configuration_service import ConfigurationService
from app.services.vector_db.interface.vector_db import IVectorDBService


class VectorDBProviderFactory:
    """Factory for creating vector database provider instances.

    Usage::

        provider = await VectorDBProviderFactory.create_provider(
            logger=logger,
            config_service=config_service,
        )
    """

    @staticmethod
    async def create_provider(
        logger: Logger,
        config_service: ConfigurationService,
    ) -> IVectorDBService:
        """Create and return a connected IVectorDBService.

        The provider type is read from the ``VECTOR_DB_TYPE`` environment
        variable.  Defaults to ``qdrant``.

        Raises:
            ValueError: If VECTOR_DB_TYPE is an unsupported value.
            ConnectionError: If the provider cannot connect.
        """
        provider_type = os.getenv("VECTOR_DB_TYPE", "qdrant").lower().strip()
        logger.info(
            f"VectorDBProviderFactory: creating '{provider_type}' provider "
            "(from VECTOR_DB_TYPE env)"
        )

        if provider_type == "qdrant":
            return await VectorDBProviderFactory._create_qdrant(logger, config_service)
        elif provider_type == "opensearch":
            return await VectorDBProviderFactory._create_opensearch(logger, config_service)
        elif provider_type == "redis":
            return await VectorDBProviderFactory._create_redis(logger, config_service)
        else:
            raise ValueError(
                f"Unsupported VECTOR_DB_TYPE='{provider_type}'. "
                "Supported values: qdrant, opensearch, redis"
            )

    # ------------------------------------------------------------------
    # Provider-specific creators
    # ------------------------------------------------------------------

    @staticmethod
    async def _create_qdrant(
        logger: Logger,
        config_service: ConfigurationService,
    ) -> IVectorDBService:
        from app.services.vector_db.qdrant.qdrant import QdrantService

        try:
            logger.debug("Creating Qdrant provider (fully async)…")
            provider = await QdrantService.create(config_service)
            logger.info("✅ Qdrant provider created and connected")
            return provider
        except Exception as e:
            logger.error(f"❌ Failed to create Qdrant provider: {e}")
            raise

    @staticmethod
    async def _create_opensearch(
        logger: Logger,
        config_service: ConfigurationService,
    ) -> IVectorDBService:
        from app.services.vector_db.opensearch.opensearch import OpenSearchService

        try:
            logger.debug("Creating OpenSearch provider (async)…")
            provider = await OpenSearchService.create(config_service)
            logger.info("✅ OpenSearch provider created and connected")
            return provider
        except Exception as e:
            logger.error(f"❌ Failed to create OpenSearch provider: {e}")
            raise

    @staticmethod
    async def _create_redis(
        logger: Logger,
        config_service: ConfigurationService,
    ) -> IVectorDBService:
        from app.services.vector_db.redis.redis_vector import RedisVectorService

        try:
            logger.debug("Creating Redis vector provider…")
            provider = await RedisVectorService.create(config_service)
            logger.info("✅ Redis vector provider created and connected")
            return provider
        except Exception as e:
            logger.error(f"❌ Failed to create Redis vector provider: {e}")
            raise


# ---------------------------------------------------------------------------
# Convenience function (mirrors create_graph_db_provider)
# ---------------------------------------------------------------------------

async def create_vector_db_provider(
    logger: Logger,
    config_service: ConfigurationService,
) -> IVectorDBService:
    """Convenience wrapper around VectorDBProviderFactory.create_provider()."""
    return await VectorDBProviderFactory.create_provider(
        logger=logger,
        config_service=config_service,
    )
