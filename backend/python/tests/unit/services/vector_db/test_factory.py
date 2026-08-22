"""Unit tests for VectorDBProviderFactory."""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Skip Qdrant factory test if qdrant_client not installed
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


@pytest.fixture
def mock_config_service():
    """Return a mock ConfigurationService."""
    svc = MagicMock()
    svc.get_config = AsyncMock(return_value={
        "host": "localhost",
        "port": 6379,
        "password": None,
    })
    return svc


@pytest.fixture
def mock_logger():
    return MagicMock()


@pytest.mark.asyncio
async def test_factory_creates_redis_provider(mock_logger, mock_config_service):
    from app.services.vector_db.redis.redis_vector import RedisVectorService

    with patch.dict(os.environ, {"VECTOR_DB_TYPE": "redis"}):
        with patch.object(RedisVectorService, "create", new_callable=AsyncMock) as mock_create:
            mock_instance = MagicMock(spec=RedisVectorService)
            mock_create.return_value = mock_instance

            from app.services.vector_db.vector_db_provider_factory import VectorDBProviderFactory
            result = await VectorDBProviderFactory.create_provider(
                logger=mock_logger, config_service=mock_config_service
            )

            mock_create.assert_called_once_with(mock_config_service)
            assert result is mock_instance


@pytest.mark.asyncio
@pytest.mark.skipif(not _OPENSEARCH_AVAILABLE, reason="opensearch-py not installed")
async def test_factory_creates_opensearch_provider(mock_logger, mock_config_service):
    from app.services.vector_db.opensearch.opensearch import OpenSearchService

    with patch.dict(os.environ, {"VECTOR_DB_TYPE": "opensearch"}):
        with patch.object(OpenSearchService, "create", new_callable=AsyncMock) as mock_create:
            mock_instance = MagicMock(spec=OpenSearchService)
            mock_create.return_value = mock_instance

            from app.services.vector_db.vector_db_provider_factory import VectorDBProviderFactory
            result = await VectorDBProviderFactory.create_provider(
                logger=mock_logger, config_service=mock_config_service
            )

            mock_create.assert_called_once_with(mock_config_service)
            assert result is mock_instance


@pytest.mark.asyncio
@pytest.mark.skipif(not _QDRANT_AVAILABLE, reason="qdrant_client not installed")
async def test_factory_creates_qdrant_provider_by_default(mock_logger, mock_config_service):
    from app.services.vector_db.qdrant.qdrant import QdrantService

    with patch.dict(os.environ, {}, clear=True):
        os.environ.pop("VECTOR_DB_TYPE", None)
        with patch.object(QdrantService, "create", new_callable=AsyncMock) as mock_create:
            mock_instance = MagicMock(spec=QdrantService)
            mock_create.return_value = mock_instance

            from app.services.vector_db.vector_db_provider_factory import VectorDBProviderFactory
            result = await VectorDBProviderFactory.create_provider(
                logger=mock_logger, config_service=mock_config_service
            )

            mock_create.assert_called_once_with(mock_config_service)
            assert result is mock_instance


@pytest.mark.asyncio
async def test_factory_raises_on_unknown_type(mock_logger, mock_config_service):
    with patch.dict(os.environ, {"VECTOR_DB_TYPE": "unknown_db"}):
        from app.services.vector_db.vector_db_provider_factory import VectorDBProviderFactory
        with pytest.raises(ValueError, match="unknown_db"):
            await VectorDBProviderFactory.create_provider(
                logger=mock_logger, config_service=mock_config_service
            )
