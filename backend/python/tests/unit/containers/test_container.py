"""Tests for container module: BaseAppContainer initialization and provider registration."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from dependency_injector import providers

from app.containers.container import BaseAppContainer


# ============================================================================
# BaseAppContainer tests
# ============================================================================


class TestBaseAppContainer:
    """Test BaseAppContainer initialization and provider registration."""

    def test_container_instantiation(self):
        """Test that the container can be instantiated."""
        container = BaseAppContainer()
        assert container is not None

    def test_service_creds_lock_provider(self):
        """Test that service_creds_lock provider returns an asyncio.Lock."""
        container = BaseAppContainer()
        lock = container.service_creds_lock()
        assert isinstance(lock, asyncio.Lock)

    def test_user_creds_lock_provider(self):
        """Test that user_creds_lock provider returns an asyncio.Lock."""
        container = BaseAppContainer()
        lock = container.user_creds_lock()
        assert isinstance(lock, asyncio.Lock)

    def test_service_creds_lock_is_singleton(self):
        """Test that service_creds_lock returns the same instance."""
        container = BaseAppContainer()
        lock1 = container.service_creds_lock()
        lock2 = container.service_creds_lock()
        assert lock1 is lock2

    def test_user_creds_lock_is_singleton(self):
        """Test that user_creds_lock returns the same instance."""
        container = BaseAppContainer()
        lock1 = container.user_creds_lock()
        lock2 = container.user_creds_lock()
        assert lock1 is lock2

    def test_logger_provider(self):
        """Test that logger provider creates a logger."""
        container = BaseAppContainer()
        logger = container.logger()
        assert logger is not None

    def test_logger_is_singleton(self):
        """Test that logger returns the same instance on repeated calls."""
        container = BaseAppContainer()
        logger1 = container.logger()
        logger2 = container.logger()
        assert logger1 is logger2

    def test_logger_provider_is_singleton_type(self):
        """Test that the logger provider is a Singleton provider."""
        assert isinstance(BaseAppContainer.logger, providers.Singleton)

    def test_config_service_provider_is_singleton_type(self):
        """Test that config_service provider is a Singleton provider."""
        assert isinstance(BaseAppContainer.config_service, providers.Singleton)

    def test_config_service_provider_override(self):
        """Test that config_service provider can be overridden (DI pattern)."""
        container = BaseAppContainer()
        mock_config = MagicMock()
        container.config_service.override(providers.Object(mock_config))
        result = container.config_service()
        assert result is mock_config
        container.config_service.reset_override()

    def test_container_has_expected_providers(self):
        """Test that the container exposes all expected provider names."""
        container = BaseAppContainer()
        assert hasattr(container, "service_creds_lock")
        assert hasattr(container, "user_creds_lock")
        assert hasattr(container, "logger")
        assert hasattr(container, "config_service")

    def test_init_class_method(self):
        """Test the init class method creates a container."""
        container = BaseAppContainer.init("test_service")
        assert container is not None
        # init() returns the container; verify it has the expected providers
        assert hasattr(container, "logger")
        assert hasattr(container, "config_service")
        assert hasattr(container, "service_creds_lock")

    def test_init_returns_working_container(self):
        """Test that init returns a container whose providers work."""
        container = BaseAppContainer.init("test_service")
        logger = container.logger()
        assert logger is not None
        lock = container.service_creds_lock()
        assert isinstance(lock, asyncio.Lock)

    @pytest.mark.asyncio
    @patch("app.containers.container.os.getenv", return_value="neo4j")
    async def test_create_arango_client_neo4j_returns_none(self, mock_getenv):
        """Test that _create_arango_client returns None when DATA_STORE is neo4j."""
        mock_config_service = MagicMock()
        result = await BaseAppContainer._create_arango_client(mock_config_service)
        assert result is None

    @pytest.mark.asyncio
    @patch("app.containers.container.os.getenv", return_value="arangodb")
    async def test_create_arango_client_no_url_returns_none(self, mock_getenv):
        """Test that _create_arango_client returns None when no URL is configured."""
        mock_config_service = AsyncMock()
        mock_config_service.get_config.return_value = {"url": None}
        result = await BaseAppContainer._create_arango_client(mock_config_service)
        assert result is None

    @pytest.mark.asyncio
    @patch("app.containers.container.os.getenv", return_value="arangodb")
    @patch("app.containers.container.ArangoClient")
    async def test_create_arango_client_with_url(self, mock_arango_cls, mock_getenv):
        """Test that _create_arango_client creates client when URL is provided."""
        mock_config_service = AsyncMock()
        mock_config_service.get_config.return_value = {"url": "http://localhost:8529"}
        mock_client = MagicMock()
        mock_arango_cls.return_value = mock_client

        result = await BaseAppContainer._create_arango_client(mock_config_service)
        assert result is mock_client
        mock_arango_cls.assert_called_once_with(hosts="http://localhost:8529")

    @pytest.mark.asyncio
    @patch("app.containers.container.os.getenv", return_value="arangodb")
    async def test_create_arango_client_empty_url_returns_none(self, mock_getenv):
        """Test _create_arango_client returns None for empty string URL."""
        mock_config_service = AsyncMock()
        mock_config_service.get_config.return_value = {"url": ""}
        result = await BaseAppContainer._create_arango_client(mock_config_service)
        assert result is None

    def test_multiple_containers_independent(self):
        """Test that multiple container instances have independent singletons."""
        container1 = BaseAppContainer()
        container2 = BaseAppContainer()
        lock1 = container1.service_creds_lock()
        lock2 = container2.service_creds_lock()
        # Each container creates its own singleton
        assert lock1 is not lock2
