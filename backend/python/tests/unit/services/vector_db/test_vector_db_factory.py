"""
Unit tests for VectorDBFactory.create_vector_db_service dispatching.

Updated to use new API (QdrantService.create, no is_async param, redis support).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.vector_db.vector_db_factory import VectorDBFactory


# Check if optional providers are available
try:
    import qdrant_client  # noqa: F401
    _QDRANT_AVAILABLE = True
except ImportError:
    _QDRANT_AVAILABLE = False

try:
    import opensearchpy  # noqa: F401
    _OPENSEARCH_AVAILABLE = True
except ImportError:
    _OPENSEARCH_AVAILABLE = False


class TestVectorDBFactoryCreateVectorDbService:

    @pytest.mark.asyncio
    @pytest.mark.skipif(not _QDRANT_AVAILABLE, reason="qdrant_client not installed")
    @patch(
        "app.services.vector_db.qdrant.qdrant.QdrantService.create",
        new_callable=AsyncMock,
    )
    async def test_qdrant_default(self, mock_create):
        mock_service = MagicMock()
        mock_create.return_value = mock_service
        config = MagicMock()

        result = await VectorDBFactory.create_vector_db_service(
            service_type="qdrant",
            config=config,
        )
        assert result is mock_service
        mock_create.assert_awaited_once_with(config)

    @pytest.mark.asyncio
    @pytest.mark.skipif(not _QDRANT_AVAILABLE, reason="qdrant_client not installed")
    @patch(
        "app.services.vector_db.qdrant.qdrant.QdrantService.create",
        new_callable=AsyncMock,
    )
    async def test_case_insensitive_qdrant(self, mock_create):
        mock_service = MagicMock()
        mock_create.return_value = mock_service
        config = MagicMock()

        result = await VectorDBFactory.create_vector_db_service(
            service_type="Qdrant",
            config=config,
        )
        assert result is mock_service

    @pytest.mark.asyncio
    @pytest.mark.skipif(not _OPENSEARCH_AVAILABLE, reason="opensearch-py not installed")
    async def test_opensearch_dispatching(self):
        from app.services.vector_db.opensearch.opensearch import OpenSearchService
        mock_service = MagicMock()
        config = MagicMock()

        with patch.object(OpenSearchService, "create", new_callable=AsyncMock) as mock_create:
            mock_create.return_value = mock_service
            result = await VectorDBFactory.create_vector_db_service(
                service_type="opensearch",
                config=config,
            )
        assert result is mock_service
        mock_create.assert_awaited_once_with(config)

    @pytest.mark.asyncio
    @patch(
        "app.services.vector_db.redis.redis_vector.RedisVectorService.create",
        new_callable=AsyncMock,
    )
    async def test_redis_dispatching(self, mock_create):
        mock_service = MagicMock()
        mock_create.return_value = mock_service
        config = MagicMock()

        result = await VectorDBFactory.create_vector_db_service(
            service_type="redis",
            config=config,
        )
        assert result is mock_service
        mock_create.assert_awaited_once_with(config)

    @pytest.mark.asyncio
    async def test_unsupported_service_type_raises(self):
        config = MagicMock()
        with pytest.raises(ValueError, match="[Uu]nsupported"):
            await VectorDBFactory.create_vector_db_service(
                service_type="pinecone",
                config=config,
            )

    @pytest.mark.asyncio
    async def test_unsupported_service_type_empty_string(self):
        config = MagicMock()
        with pytest.raises(ValueError):
            await VectorDBFactory.create_vector_db_service(
                service_type="",
                config=config,
            )


class TestVectorDBFactoryLegacyHelpers:
    """Legacy create_qdrant_service_sync / _async helpers."""

    @pytest.mark.asyncio
    @pytest.mark.skipif(not _QDRANT_AVAILABLE, reason="qdrant_client not installed")
    @patch(
        "app.services.vector_db.qdrant.qdrant.QdrantService.create",
        new_callable=AsyncMock,
    )
    async def test_create_qdrant_service_sync_delegates(self, mock_create):
        mock_service = MagicMock()
        mock_create.return_value = mock_service
        config = MagicMock()

        result = await VectorDBFactory.create_qdrant_service_sync(config)
        assert result is mock_service
        mock_create.assert_awaited_once_with(config)

    @pytest.mark.asyncio
    @pytest.mark.skipif(not _QDRANT_AVAILABLE, reason="qdrant_client not installed")
    @patch(
        "app.services.vector_db.qdrant.qdrant.QdrantService.create",
        new_callable=AsyncMock,
    )
    async def test_create_qdrant_service_async_delegates(self, mock_create):
        mock_service = MagicMock()
        mock_create.return_value = mock_service
        config = MagicMock()

        result = await VectorDBFactory.create_qdrant_service_async(config)
        assert result is mock_service
        mock_create.assert_awaited_once_with(config)
