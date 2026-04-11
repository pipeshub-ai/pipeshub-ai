"""
Unit tests for VectorDBFactory.create_vector_db_service dispatching.

Tests cover:
- Dispatching to qdrant async (default)
- Dispatching to qdrant sync
- Case-insensitive service type matching
- Dispatching to opensearch
- Unsupported service type raises ValueError
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.vector_db.vector_db_factory import VectorDBFactory


class TestVectorDBFactoryCreateVectorDbService:

    @pytest.mark.asyncio
    @patch(
        "app.services.vector_db.vector_db_factory.QdrantService.create_async",
        new_callable=AsyncMock,
    )
    async def test_qdrant_async_default(self, mock_create_async):
        mock_service = MagicMock()
        mock_create_async.return_value = mock_service
        config = MagicMock()

        result = await VectorDBFactory.create_vector_db_service(
            service_type="qdrant",
            config=config,
        )
        assert result is mock_service
        mock_create_async.assert_awaited_once_with(config)

    @pytest.mark.asyncio
    @patch(
        "app.services.vector_db.vector_db_factory.QdrantService.create_async",
        new_callable=AsyncMock,
    )
    async def test_qdrant_async_explicit(self, mock_create_async):
        mock_service = MagicMock()
        mock_create_async.return_value = mock_service
        config = MagicMock()

        result = await VectorDBFactory.create_vector_db_service(
            service_type="qdrant",
            config=config,
            is_async=True,
        )
        assert result is mock_service
        mock_create_async.assert_awaited_once_with(config)

    @pytest.mark.asyncio
    @patch(
        "app.services.vector_db.vector_db_factory.QdrantService.create_sync",
        new_callable=AsyncMock,
    )
    async def test_qdrant_sync(self, mock_create_sync):
        mock_service = MagicMock()
        mock_create_sync.return_value = mock_service
        config = MagicMock()

        result = await VectorDBFactory.create_vector_db_service(
            service_type="qdrant",
            config=config,
            is_async=False,
        )
        assert result is mock_service
        mock_create_sync.assert_awaited_once_with(config)

    @pytest.mark.asyncio
    @patch(
        "app.services.vector_db.vector_db_factory.QdrantService.create_async",
        new_callable=AsyncMock,
    )
    async def test_case_insensitive_qdrant(self, mock_create_async):
        mock_service = MagicMock()
        mock_create_async.return_value = mock_service
        config = MagicMock()

        result = await VectorDBFactory.create_vector_db_service(
            service_type="Qdrant",
            config=config,
        )
        assert result is mock_service

    @pytest.mark.asyncio
    @patch(
        "app.services.vector_db.vector_db_factory.QdrantService.create_async",
        new_callable=AsyncMock,
    )
    async def test_uppercase_qdrant(self, mock_create_async):
        mock_service = MagicMock()
        mock_create_async.return_value = mock_service
        config = MagicMock()

        result = await VectorDBFactory.create_vector_db_service(
            service_type="QDRANT",
            config=config,
        )
        assert result is mock_service

    @pytest.mark.asyncio
    @patch(
        "app.services.vector_db.opensearch.opensearch.OpenSearchService.create",
        new_callable=AsyncMock,
    )
    async def test_opensearch_dispatching(self, mock_create):
        mock_service = MagicMock()
        mock_create.return_value = mock_service
        config = MagicMock()

        result = await VectorDBFactory.create_vector_db_service(
            service_type="opensearch",
            config=config,
        )
        assert result is mock_service
        mock_create.assert_awaited_once_with(config, is_async=True)

    @pytest.mark.asyncio
    @patch(
        "app.services.vector_db.opensearch.opensearch.OpenSearchService.create",
        new_callable=AsyncMock,
    )
    async def test_opensearch_case_insensitive(self, mock_create):
        mock_service = MagicMock()
        mock_create.return_value = mock_service
        config = MagicMock()

        result = await VectorDBFactory.create_vector_db_service(
            service_type="OpenSearch",
            config=config,
        )
        assert result is mock_service

    @pytest.mark.asyncio
    async def test_unsupported_service_type_raises(self):
        config = MagicMock()
        with pytest.raises(ValueError, match="Unsupported vector database service type"):
            await VectorDBFactory.create_vector_db_service(
                service_type="pinecone",
                config=config,
            )

    @pytest.mark.asyncio
    async def test_unsupported_service_type_empty_string(self):
        config = MagicMock()
        with pytest.raises(ValueError, match="Unsupported vector database service type"):
            await VectorDBFactory.create_vector_db_service(
                service_type="",
                config=config,
            )


class TestVectorDBFactoryCreateQdrantServiceSync:

    @pytest.mark.asyncio
    @patch(
        "app.services.vector_db.vector_db_factory.QdrantService.create_sync",
        new_callable=AsyncMock,
    )
    async def test_delegates_to_qdrant_create_sync(self, mock_create_sync):
        mock_service = MagicMock()
        mock_create_sync.return_value = mock_service
        config = MagicMock()

        result = await VectorDBFactory.create_qdrant_service_sync(config)
        assert result is mock_service
        mock_create_sync.assert_awaited_once_with(config)


class TestVectorDBFactoryCreateQdrantServiceAsync:

    @pytest.mark.asyncio
    @patch(
        "app.services.vector_db.vector_db_factory.QdrantService.create_async",
        new_callable=AsyncMock,
    )
    async def test_delegates_to_qdrant_create_async(self, mock_create_async):
        mock_service = MagicMock()
        mock_create_async.return_value = mock_service
        config = MagicMock()

        result = await VectorDBFactory.create_qdrant_service_async(config)
        assert result is mock_service
        mock_create_async.assert_awaited_once_with(config)
