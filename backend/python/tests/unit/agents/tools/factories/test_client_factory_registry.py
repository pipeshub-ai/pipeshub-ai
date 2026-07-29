"""Tests for `app.agents.tools.factories.registry.ClientFactoryRegistry` and
`app.agents.tools.factories.base.ClientFactory`."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest


class TestClientFactoryRegistry:
    """Tests for ClientFactoryRegistry."""

    @pytest.fixture(autouse=True)
    def reset_registry(self):
        """Reset registry state before/after each test."""
        from app.agents.tools.factories.registry import ClientFactoryRegistry
        ClientFactoryRegistry.reset()
        yield
        ClientFactoryRegistry.reset()

    def test_register_and_get_factory(self):
        from app.agents.tools.factories.registry import ClientFactoryRegistry
        mock_factory = MagicMock()
        ClientFactoryRegistry.register("test_app", mock_factory)
        result = ClientFactoryRegistry.get_factory("test_app")
        assert result is mock_factory

    def test_get_factory_not_found(self):
        from app.agents.tools.factories.registry import ClientFactoryRegistry
        # Force initialization to avoid triggering default factories for a missing key
        ClientFactoryRegistry._initialized = True
        result = ClientFactoryRegistry.get_factory("nonexistent_app")
        assert result is None

    def test_unregister_factory(self):
        from app.agents.tools.factories.registry import ClientFactoryRegistry
        mock_factory = MagicMock()
        ClientFactoryRegistry.register("test_app", mock_factory)
        ClientFactoryRegistry.unregister("test_app")
        ClientFactoryRegistry._initialized = True
        result = ClientFactoryRegistry.get_factory("test_app")
        assert result is None

    def test_unregister_nonexistent_is_noop(self):
        from app.agents.tools.factories.registry import ClientFactoryRegistry
        # Should not raise
        ClientFactoryRegistry.unregister("does_not_exist")

    def test_list_factories(self):
        from app.agents.tools.factories.registry import ClientFactoryRegistry
        mock_factory = MagicMock()
        ClientFactoryRegistry._initialized = True
        ClientFactoryRegistry.register("app_a", mock_factory)
        ClientFactoryRegistry.register("app_b", mock_factory)
        names = ClientFactoryRegistry.list_factories()
        assert "app_a" in names
        assert "app_b" in names

    def test_reset_clears_all(self):
        from app.agents.tools.factories.registry import ClientFactoryRegistry
        ClientFactoryRegistry.register("test_app", MagicMock())
        ClientFactoryRegistry._initialized = True
        ClientFactoryRegistry.reset()
        assert ClientFactoryRegistry._factories == {}
        assert ClientFactoryRegistry._initialized is False

    def test_get_factory_triggers_initialization(self):
        """get_factory should trigger initialize_default_factories if not initialized."""
        from app.agents.tools.factories.registry import ClientFactoryRegistry
        ClientFactoryRegistry._initialized = False
        with patch.object(ClientFactoryRegistry, "initialize_default_factories") as mock_init:
            mock_init.side_effect = lambda: setattr(ClientFactoryRegistry, "_initialized", True)
            ClientFactoryRegistry.get_factory("anything")
            mock_init.assert_called_once()

    def test_list_factories_triggers_initialization(self):
        """list_factories should trigger initialize_default_factories if not initialized."""
        from app.agents.tools.factories.registry import ClientFactoryRegistry
        ClientFactoryRegistry._initialized = False
        with patch.object(ClientFactoryRegistry, "initialize_default_factories") as mock_init:
            mock_init.side_effect = lambda: setattr(ClientFactoryRegistry, "_initialized", True)
            ClientFactoryRegistry.list_factories()
            mock_init.assert_called_once()

    def test_initialize_default_factories_idempotent(self):
        """Calling initialize_default_factories twice does not duplicate entries."""
        from app.agents.tools.factories.registry import ClientFactoryRegistry
        ClientFactoryRegistry.initialize_default_factories()
        first_count = len(ClientFactoryRegistry._factories)
        ClientFactoryRegistry.initialize_default_factories()
        second_count = len(ClientFactoryRegistry._factories)
        assert first_count == second_count


class TestClientFactoryBase:
    """Tests for the abstract ClientFactory base class."""

    def test_cannot_instantiate_directly(self):
        from app.agents.tools.factories.base import ClientFactory
        with pytest.raises(TypeError):
            ClientFactory()

    def test_create_client_sync_no_event_loop(self):
        """create_client_sync calls asyncio.run when no event loop is running."""
        from app.agents.tools.factories.base import ClientFactory

        class ConcreteFactory(ClientFactory):
            async def create_client(self, config_service, logger, toolset_config, state=None):
                return "test_client"

        factory = ConcreteFactory()
        result = factory.create_client_sync(MagicMock(), MagicMock(), {"key": "val"})
        assert result == "test_client"

    def test_create_client_sync_in_async_context(self):
        """create_client_sync uses thread pool when already in async context."""
        from app.agents.tools.factories.base import ClientFactory

        class ConcreteFactory(ClientFactory):
            async def create_client(self, config_service, logger, toolset_config, state=None):
                return "thread_client"

        factory = ConcreteFactory()

        async def run_test():
            return factory.create_client_sync(MagicMock(), MagicMock(), {"key": "val"})

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(run_test())
            assert result == "thread_client"
        finally:
            loop.close()
