"""
Unit tests for GraphDBFactory and GraphDBProviderFactory dispatching.

Tests cover:
- GraphDBFactory.create_service: arango dispatch, unsupported type returns None
- GraphDBFactory.create_arango_service: delegates to ArangoService.create
- GraphDBProviderFactory.create_provider: arangodb/neo4j dispatch, unsupported type raises
- GraphDBProviderFactory._create_arango_http_provider: connect success/failure
- GraphDBProviderFactory._create_neo4j_provider: connect success/failure
- create_graph_db_provider convenience function
"""

import logging
import os

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.graph_db.graph_db_factory import GraphDBFactory
from app.services.graph_db.graph_db_provider_factory import (
    GraphDBProviderFactory,
    create_graph_db_provider,
)


# ---------------------------------------------------------------------------
# GraphDBFactory
# ---------------------------------------------------------------------------


class TestGraphDBFactoryCreateService:
    """Tests for GraphDBFactory.create_service dispatching."""

    @pytest.mark.asyncio
    @patch(
        "app.services.graph_db.graph_db_factory.ArangoService.create",
        new_callable=AsyncMock,
    )
    async def test_arango_type_dispatches(self, mock_create):
        mock_service = MagicMock()
        mock_create.return_value = mock_service
        mock_logger = MagicMock()
        config = MagicMock()

        result = await GraphDBFactory.create_service("arango", mock_logger, config)
        assert result is mock_service
        mock_create.assert_awaited_once_with(mock_logger, config)

    @pytest.mark.asyncio
    @patch(
        "app.services.graph_db.graph_db_factory.ArangoService.create",
        new_callable=AsyncMock,
    )
    async def test_arango_type_case_insensitive(self, mock_create):
        mock_service = MagicMock()
        mock_create.return_value = mock_service
        mock_logger = MagicMock()
        config = MagicMock()

        result = await GraphDBFactory.create_service("Arango", mock_logger, config)
        assert result is mock_service

    @pytest.mark.asyncio
    @patch(
        "app.services.graph_db.graph_db_factory.ArangoService.create",
        new_callable=AsyncMock,
    )
    async def test_arango_type_uppercase(self, mock_create):
        mock_service = MagicMock()
        mock_create.return_value = mock_service
        mock_logger = MagicMock()
        config = MagicMock()

        result = await GraphDBFactory.create_service("ARANGO", mock_logger, config)
        assert result is mock_service

    @pytest.mark.asyncio
    async def test_unsupported_type_returns_none(self):
        mock_logger = MagicMock()
        config = MagicMock()

        result = await GraphDBFactory.create_service("neo4j", mock_logger, config)
        assert result is None
        mock_logger.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_unsupported_type_empty_string_returns_none(self):
        mock_logger = MagicMock()
        config = MagicMock()

        result = await GraphDBFactory.create_service("", mock_logger, config)
        assert result is None


class TestGraphDBFactoryCreateArangoService:
    """Tests for GraphDBFactory.create_arango_service."""

    @pytest.mark.asyncio
    @patch(
        "app.services.graph_db.graph_db_factory.ArangoService.create",
        new_callable=AsyncMock,
    )
    async def test_delegates_to_arango_service_create(self, mock_create):
        mock_service = MagicMock()
        mock_create.return_value = mock_service
        mock_logger = MagicMock()
        config = MagicMock()

        result = await GraphDBFactory.create_arango_service(mock_logger, config)
        assert result is mock_service
        mock_create.assert_awaited_once_with(mock_logger, config)


# ---------------------------------------------------------------------------
# GraphDBProviderFactory
# ---------------------------------------------------------------------------


class TestGraphDBProviderFactoryCreateProvider:
    """Tests for GraphDBProviderFactory.create_provider dispatching via DATA_STORE env."""

    @pytest.mark.asyncio
    @patch(
        "app.services.graph_db.graph_db_provider_factory.GraphDBProviderFactory._create_arango_http_provider",
        new_callable=AsyncMock,
    )
    async def test_arangodb_provider_default(self, mock_create_arango):
        """When DATA_STORE is not set, defaults to arangodb."""
        mock_provider = MagicMock()
        mock_create_arango.return_value = mock_provider
        mock_logger = MagicMock()
        config = MagicMock()

        with patch.dict(os.environ, {}, clear=False):
            # Remove DATA_STORE if present
            os.environ.pop("DATA_STORE", None)
            result = await GraphDBProviderFactory.create_provider(mock_logger, config)

        assert result is mock_provider
        mock_create_arango.assert_awaited_once_with(
            logger=mock_logger,
            config_service=config,
        )

    @pytest.mark.asyncio
    @patch(
        "app.services.graph_db.graph_db_provider_factory.GraphDBProviderFactory._create_arango_http_provider",
        new_callable=AsyncMock,
    )
    async def test_arangodb_provider_explicit(self, mock_create_arango):
        mock_provider = MagicMock()
        mock_create_arango.return_value = mock_provider
        mock_logger = MagicMock()
        config = MagicMock()

        with patch.dict(os.environ, {"DATA_STORE": "arangodb"}):
            result = await GraphDBProviderFactory.create_provider(mock_logger, config)

        assert result is mock_provider

    @pytest.mark.asyncio
    @patch(
        "app.services.graph_db.graph_db_provider_factory.GraphDBProviderFactory._create_arango_http_provider",
        new_callable=AsyncMock,
    )
    async def test_arangodb_provider_case_insensitive(self, mock_create_arango):
        mock_provider = MagicMock()
        mock_create_arango.return_value = mock_provider
        mock_logger = MagicMock()
        config = MagicMock()

        with patch.dict(os.environ, {"DATA_STORE": "ArangoDB"}):
            result = await GraphDBProviderFactory.create_provider(mock_logger, config)

        assert result is mock_provider

    @pytest.mark.asyncio
    @patch(
        "app.services.graph_db.graph_db_provider_factory.GraphDBProviderFactory._create_neo4j_provider",
        new_callable=AsyncMock,
    )
    async def test_neo4j_provider(self, mock_create_neo4j):
        mock_provider = MagicMock()
        mock_create_neo4j.return_value = mock_provider
        mock_logger = MagicMock()
        config = MagicMock()

        with patch.dict(os.environ, {"DATA_STORE": "neo4j"}):
            result = await GraphDBProviderFactory.create_provider(mock_logger, config)

        assert result is mock_provider
        mock_create_neo4j.assert_awaited_once_with(
            logger=mock_logger,
            config_service=config,
        )

    @pytest.mark.asyncio
    async def test_unsupported_provider_raises_value_error(self):
        mock_logger = MagicMock()
        config = MagicMock()

        with patch.dict(os.environ, {"DATA_STORE": "cassandra"}):
            with pytest.raises(ValueError, match="Unsupported graph database provider"):
                await GraphDBProviderFactory.create_provider(mock_logger, config)

    @pytest.mark.asyncio
    async def test_unsupported_provider_logs_error(self):
        mock_logger = MagicMock()
        config = MagicMock()

        with patch.dict(os.environ, {"DATA_STORE": "cassandra"}):
            with pytest.raises(ValueError):
                await GraphDBProviderFactory.create_provider(mock_logger, config)

        mock_logger.error.assert_called()


class TestGraphDBProviderFactoryCreateArangoHttpProvider:
    """Tests for GraphDBProviderFactory._create_arango_http_provider."""

    @pytest.mark.asyncio
    @patch("app.services.graph_db.graph_db_provider_factory.ArangoHTTPProvider")
    async def test_success(self, MockProvider):
        mock_instance = MagicMock()
        mock_instance.connect = AsyncMock(return_value=True)
        MockProvider.return_value = mock_instance
        mock_logger = MagicMock()
        config = MagicMock()

        result = await GraphDBProviderFactory._create_arango_http_provider(
            logger=mock_logger,
            config_service=config,
        )
        assert result is mock_instance
        MockProvider.assert_called_once_with(logger=mock_logger, config_service=config)
        mock_instance.connect.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("app.services.graph_db.graph_db_provider_factory.ArangoHTTPProvider")
    async def test_connect_returns_false_raises_connection_error(self, MockProvider):
        mock_instance = MagicMock()
        mock_instance.connect = AsyncMock(return_value=False)
        MockProvider.return_value = mock_instance
        mock_logger = MagicMock()
        config = MagicMock()

        with pytest.raises(ConnectionError, match="Failed to connect ArangoDB HTTP provider"):
            await GraphDBProviderFactory._create_arango_http_provider(
                logger=mock_logger,
                config_service=config,
            )

    @pytest.mark.asyncio
    @patch("app.services.graph_db.graph_db_provider_factory.ArangoHTTPProvider")
    async def test_connect_raises_exception(self, MockProvider):
        mock_instance = MagicMock()
        mock_instance.connect = AsyncMock(side_effect=Exception("connection refused"))
        MockProvider.return_value = mock_instance
        mock_logger = MagicMock()
        config = MagicMock()

        with pytest.raises(Exception, match="connection refused"):
            await GraphDBProviderFactory._create_arango_http_provider(
                logger=mock_logger,
                config_service=config,
            )
        mock_logger.error.assert_called()


class TestGraphDBProviderFactoryCreateNeo4jProvider:
    """Tests for GraphDBProviderFactory._create_neo4j_provider."""

    @pytest.mark.asyncio
    @patch("app.services.graph_db.graph_db_provider_factory.Neo4jProvider")
    async def test_success(self, MockProvider):
        mock_instance = MagicMock()
        mock_instance.connect = AsyncMock(return_value=True)
        MockProvider.return_value = mock_instance
        mock_logger = MagicMock()
        config = MagicMock()

        result = await GraphDBProviderFactory._create_neo4j_provider(
            logger=mock_logger,
            config_service=config,
        )
        assert result is mock_instance
        MockProvider.assert_called_once_with(logger=mock_logger, config_service=config)
        mock_instance.connect.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("app.services.graph_db.graph_db_provider_factory.Neo4jProvider")
    async def test_connect_returns_false_raises_connection_error(self, MockProvider):
        mock_instance = MagicMock()
        mock_instance.connect = AsyncMock(return_value=False)
        MockProvider.return_value = mock_instance
        mock_logger = MagicMock()
        config = MagicMock()

        with pytest.raises(ConnectionError, match="Failed to connect Neo4j provider"):
            await GraphDBProviderFactory._create_neo4j_provider(
                logger=mock_logger,
                config_service=config,
            )

    @pytest.mark.asyncio
    @patch("app.services.graph_db.graph_db_provider_factory.Neo4jProvider")
    async def test_connect_raises_exception(self, MockProvider):
        mock_instance = MagicMock()
        mock_instance.connect = AsyncMock(side_effect=Exception("driver error"))
        MockProvider.return_value = mock_instance
        mock_logger = MagicMock()
        config = MagicMock()

        with pytest.raises(Exception, match="driver error"):
            await GraphDBProviderFactory._create_neo4j_provider(
                logger=mock_logger,
                config_service=config,
            )
        mock_logger.error.assert_called()


class TestCreateGraphDbProviderConvenience:
    """Tests for the create_graph_db_provider convenience function."""

    @pytest.mark.asyncio
    @patch(
        "app.services.graph_db.graph_db_provider_factory.GraphDBProviderFactory.create_provider",
        new_callable=AsyncMock,
    )
    async def test_delegates_to_factory(self, mock_create_provider):
        mock_provider = MagicMock()
        mock_create_provider.return_value = mock_provider
        mock_logger = MagicMock()
        config = MagicMock()

        result = await create_graph_db_provider(mock_logger, config)
        assert result is mock_provider
        mock_create_provider.assert_awaited_once_with(
            logger=mock_logger,
            config_service=config,
        )
