"""Unit tests for VectorDBProviderFactory.

Focused on the vector-Redis mode guard (R20): FT.HYBRID has no Redis Cluster
/ MemoryDB support, so selecting VECTOR_DB_TYPE=redis against a cluster must
fail fast at startup rather than on the first search. The guard reads
VECTOR_REDIS_MODE (defaulting to REDIS_MODE) because the vector store keeps
its own REDIS_VECTOR_* connection -- app-on-MemoryDB with the vector index on
a standalone Redis 8.4 is a valid deployment.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.vector_db.vector_db_provider_factory import (
    VectorDBProviderFactory,
)


@pytest.fixture
def logger():
    return MagicMock()


@pytest.fixture
def config_service():
    return MagicMock()


class TestRedisModeGuard:
    @pytest.mark.asyncio
    async def test_redis_vector_db_rejected_on_cluster_mode(
        self, logger, config_service, monkeypatch
    ):
        monkeypatch.setenv("VECTOR_DB_TYPE", "redis")
        monkeypatch.delenv("VECTOR_REDIS_MODE", raising=False)
        monkeypatch.setenv("REDIS_MODE", "cluster")

        with pytest.raises(ValueError, match="incompatible with VECTOR_REDIS_MODE"):
            await VectorDBProviderFactory.create_provider(
                logger=logger, config_service=config_service
            )

    @pytest.mark.asyncio
    async def test_redis_vector_db_rejected_on_non_standalone_mode(
        self, logger, config_service, monkeypatch
    ):
        monkeypatch.setenv("VECTOR_DB_TYPE", "redis")
        monkeypatch.delenv("VECTOR_REDIS_MODE", raising=False)
        monkeypatch.setenv("REDIS_MODE", "memorydb")

        with pytest.raises(ValueError, match="incompatible with VECTOR_REDIS_MODE"):
            await VectorDBProviderFactory.create_provider(
                logger=logger, config_service=config_service
            )

    @pytest.mark.asyncio
    async def test_redis_vector_db_allowed_when_the_vector_redis_is_standalone(
        self, logger, config_service, monkeypatch
    ):
        """The vector store has its own REDIS_VECTOR_* connection, so an app
        on MemoryDB with the vector index on a standalone Redis 8.4 must be
        allowed -- the error message recommends exactly this arrangement."""
        monkeypatch.setenv("VECTOR_DB_TYPE", "redis")
        monkeypatch.setenv("REDIS_MODE", "cluster")
        monkeypatch.setenv("VECTOR_REDIS_MODE", "standalone")

        fake_provider = AsyncMock()
        with patch(
            "app.services.vector_db.redis.redis_vector.RedisVectorService.create",
            AsyncMock(return_value=fake_provider),
        ):
            provider = await VectorDBProviderFactory.create_provider(
                logger=logger, config_service=config_service
            )
        assert provider is fake_provider

    @pytest.mark.asyncio
    async def test_redis_vector_db_allowed_on_standalone_mode(
        self, logger, config_service, monkeypatch
    ):
        monkeypatch.setenv("VECTOR_DB_TYPE", "redis")
        monkeypatch.delenv("VECTOR_REDIS_MODE", raising=False)
        monkeypatch.setenv("REDIS_MODE", "standalone")

        fake_provider = AsyncMock()
        with patch(
            "app.services.vector_db.redis.redis_vector.RedisVectorService.create",
            AsyncMock(return_value=fake_provider),
        ):
            provider = await VectorDBProviderFactory.create_provider(
                logger=logger, config_service=config_service
            )

        assert provider is fake_provider

    @pytest.mark.asyncio
    async def test_redis_vector_db_allowed_when_redis_mode_unset(
        self, logger, config_service, monkeypatch
    ):
        monkeypatch.setenv("VECTOR_DB_TYPE", "redis")
        monkeypatch.delenv("VECTOR_REDIS_MODE", raising=False)
        monkeypatch.delenv("REDIS_MODE", raising=False)

        fake_provider = AsyncMock()
        with patch(
            "app.services.vector_db.redis.redis_vector.RedisVectorService.create",
            AsyncMock(return_value=fake_provider),
        ):
            provider = await VectorDBProviderFactory.create_provider(
                logger=logger, config_service=config_service
            )

        assert provider is fake_provider


class TestProviderDispatch:
    @pytest.mark.asyncio
    async def test_defaults_to_qdrant(self, logger, config_service, monkeypatch):
        monkeypatch.delenv("VECTOR_DB_TYPE", raising=False)
        fake_provider = AsyncMock()
        with patch(
            "app.services.vector_db.qdrant.qdrant.QdrantService.create",
            AsyncMock(return_value=fake_provider),
        ):
            provider = await VectorDBProviderFactory.create_provider(
                logger=logger, config_service=config_service
            )
        assert provider is fake_provider

    @pytest.mark.asyncio
    async def test_unsupported_type_raises(self, logger, config_service, monkeypatch):
        monkeypatch.setenv("VECTOR_DB_TYPE", "not-a-real-db")
        with pytest.raises(ValueError, match="Unsupported VECTOR_DB_TYPE"):
            await VectorDBProviderFactory.create_provider(
                logger=logger, config_service=config_service
            )
