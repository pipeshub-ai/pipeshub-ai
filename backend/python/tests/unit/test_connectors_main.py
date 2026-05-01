"""Comprehensive unit tests for app.connectors_main module."""

import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse

from app.services.messaging.config import MessageBrokerType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_container():
    """Build a mock ConnectorAppContainer with common providers."""
    container = MagicMock()
    container.logger.return_value = MagicMock()
    mock_config_service = MagicMock()
    mock_config_service.get_config = AsyncMock(return_value={})
    mock_config_service.close = AsyncMock()
    container.config_service.return_value = mock_config_service
    container.data_store = AsyncMock()
    container.arango_service = AsyncMock()
    return container


def _make_graph_provider():
    """Build a mock graph_provider."""
    gp = MagicMock()
    gp.get_all_orgs = AsyncMock(return_value=[])
    gp.get_org_apps = AsyncMock(return_value=[])
    gp.get_users = AsyncMock(return_value=[])
    return gp


def _make_data_store(graph_provider=None):
    """Build a mock data_store with graph_provider."""
    ds = MagicMock()
    ds.graph_provider = graph_provider or _make_graph_provider()
    return ds


# ---------------------------------------------------------------------------
# get_initialized_container
# ---------------------------------------------------------------------------
class TestGetInitializedContainer:
    """Tests for get_initialized_container()."""

    async def test_first_call_initializes_container(self):
        """First call should invoke initialize_container and wire."""
        mock_container = _make_container()

        with (
            patch("app.connectors_main.container", mock_container),
            patch("app.connectors_main.initialize_container", new_callable=AsyncMock) as mock_init,
        ):
            # Remove the _initialized flag to simulate first call
            func = self._get_fresh_function()
            if hasattr(func, "_initialized"):
                delattr(func, "_initialized")

            result = await func()
            mock_init.assert_awaited_once_with(mock_container)
            mock_container.wire.assert_called_once()
            assert result is mock_container

    async def test_subsequent_calls_skip_initialization(self):
        """Second call should not re-initialize."""
        mock_container = _make_container()

        with (
            patch("app.connectors_main.container", mock_container),
            patch("app.connectors_main.initialize_container", new_callable=AsyncMock) as mock_init,
        ):
            func = self._get_fresh_function()
            if hasattr(func, "_initialized"):
                delattr(func, "_initialized")

            await func()
            await func()
            # initialize_container should be called only once
            mock_init.assert_awaited_once()

    def _get_fresh_function(self):
        """Import the function fresh each time."""
        from app.connectors_main import get_initialized_container
        return get_initialized_container


# ---------------------------------------------------------------------------
# refresh_toolset_tokens
# ---------------------------------------------------------------------------
class TestRefreshToolsetTokens:
    """Tests for refresh_toolset_tokens()."""

    async def test_success(self):
        """Successful token refresh returns True."""
        mock_container = _make_container()
        mock_refresh_service = MagicMock()
        mock_refresh_service._refresh_all_tokens = AsyncMock()
        mock_startup = MagicMock()
        mock_startup.get_toolset_token_refresh_service.return_value = mock_refresh_service

        with patch(
            "app.connectors.core.base.token_service.startup_service.startup_service",
            mock_startup,
        ):
            from app.connectors_main import refresh_toolset_tokens
            result = await refresh_toolset_tokens(mock_container)

        assert result is True
        mock_refresh_service._refresh_all_tokens.assert_awaited_once()

    async def test_no_refresh_service_returns_false(self):
        """When refresh service is not initialized, returns False."""
        mock_container = _make_container()
        mock_startup = MagicMock()
        mock_startup.get_toolset_token_refresh_service.return_value = None

        with patch(
            "app.connectors.core.base.token_service.startup_service.startup_service",
            mock_startup,
        ):
            from app.connectors_main import refresh_toolset_tokens
            result = await refresh_toolset_tokens(mock_container)

        assert result is False

    async def test_exception_returns_false(self):
        """Exception during refresh returns False."""
        mock_container = _make_container()
        mock_startup = MagicMock()
        mock_startup.get_toolset_token_refresh_service.side_effect = RuntimeError("boom")

        with patch(
            "app.connectors.core.base.token_service.startup_service.startup_service",
            mock_startup,
        ):
            from app.connectors_main import refresh_toolset_tokens
            result = await refresh_toolset_tokens(mock_container)

        assert result is False


# ---------------------------------------------------------------------------
# resume_sync_services
# ---------------------------------------------------------------------------
class TestResumeSyncServices:
    """Tests for resume_sync_services()."""

    async def test_no_orgs_returns_true(self):
        """No organizations found returns True."""
        from app.connectors_main import resume_sync_services

        mock_container = _make_container()
        gp = _make_graph_provider()
        gp.get_all_orgs = AsyncMock(return_value=[])
        ds = _make_data_store(gp)

        result = await resume_sync_services(mock_container, ds)
        assert result is True

    async def test_no_users_for_org_continues(self):
        """If no users for an org, it continues to the next org."""
        from app.connectors_main import resume_sync_services

        mock_container = _make_container()
        gp = _make_graph_provider()
        gp.get_all_orgs = AsyncMock(return_value=[{"_key": "org1", "accountType": "individual"}])
        gp.get_org_apps = AsyncMock(return_value=[])
        gp.get_users = AsyncMock(return_value=[])
        ds = _make_data_store(gp)

        result = await resume_sync_services(mock_container, ds)
        assert result is True

    async def test_connector_created_for_each_app(self):
        """Connectors created and stored in connectors_map for enabled apps."""
        from app.connectors_main import resume_sync_services

        mock_container = _make_container()
        # Remove connectors_map so the function creates it
        if hasattr(mock_container, 'connectors_map'):
            del mock_container.connectors_map

        mock_connector = MagicMock()
        gp = _make_graph_provider()
        gp.get_all_orgs = AsyncMock(return_value=[{"_key": "org1", "accountType": "individual"}])
        gp.get_org_apps = AsyncMock(return_value=[
            {"_key": "app1", "type": "Google Drive"},
            {"_key": "app2", "type": "Slack"},
        ])
        gp.get_users = AsyncMock(return_value=[{"_key": "user1"}])
        ds = _make_data_store(gp)

        with patch(
            "app.connectors_main.ConnectorFactory.create_and_start_sync",
            new_callable=AsyncMock,
            return_value=mock_connector,
        ):
            result = await resume_sync_services(mock_container, ds)

        assert result is True
        assert mock_container.connectors_map["app1"] is mock_connector
        assert mock_container.connectors_map["app2"] is mock_connector

    async def test_connector_none_not_stored(self):
        """If ConnectorFactory returns None, it should not be stored."""
        from app.connectors_main import resume_sync_services

        mock_container = _make_container()
        if hasattr(mock_container, 'connectors_map'):
            del mock_container.connectors_map

        gp = _make_graph_provider()
        gp.get_all_orgs = AsyncMock(return_value=[{"_key": "org1"}])
        gp.get_org_apps = AsyncMock(return_value=[{"_key": "app1", "type": "Slack"}])
        gp.get_users = AsyncMock(return_value=[{"_key": "user1"}])
        ds = _make_data_store(gp)

        with patch(
            "app.connectors_main.ConnectorFactory.create_and_start_sync",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await resume_sync_services(mock_container, ds)

        assert result is True
        assert "app1" not in mock_container.connectors_map

    async def test_exception_returns_false(self):
        """Exception during sync returns False."""
        from app.connectors_main import resume_sync_services

        mock_container = _make_container()
        ds = MagicMock()
        ds.graph_provider = MagicMock()
        ds.graph_provider.get_all_orgs = AsyncMock(side_effect=RuntimeError("db error"))

        result = await resume_sync_services(mock_container, ds)
        assert result is False

    async def test_org_with_id_key_fallback(self):
        """Org uses 'id' key when '_key' is not present."""
        from app.connectors_main import resume_sync_services

        mock_container = _make_container()
        gp = _make_graph_provider()
        gp.get_all_orgs = AsyncMock(return_value=[{"id": "org1"}])
        gp.get_org_apps = AsyncMock(return_value=[])
        gp.get_users = AsyncMock(return_value=[])
        ds = _make_data_store(gp)

        result = await resume_sync_services(mock_container, ds)
        assert result is True

    async def test_connectors_map_already_exists(self):
        """If connectors_map already exists, it should be reused."""
        from app.connectors_main import resume_sync_services

        mock_container = _make_container()
        mock_container.connectors_map = {"existing": MagicMock()}

        gp = _make_graph_provider()
        gp.get_all_orgs = AsyncMock(return_value=[{"_key": "org1"}])
        gp.get_org_apps = AsyncMock(return_value=[{"_key": "app1", "type": "Slack"}])
        gp.get_users = AsyncMock(return_value=[{"_key": "user1"}])
        ds = _make_data_store(gp)

        mock_connector = MagicMock()
        with patch(
            "app.connectors_main.ConnectorFactory.create_and_start_sync",
            new_callable=AsyncMock,
            return_value=mock_connector,
        ):
            result = await resume_sync_services(mock_container, ds)

        assert result is True
        # Both old and new entries should exist
        assert "existing" in mock_container.connectors_map
        assert "app1" in mock_container.connectors_map


# ---------------------------------------------------------------------------
# initialize_connector_registry
# ---------------------------------------------------------------------------
class TestInitializeConnectorRegistry:
    """Tests for initialize_connector_registry()."""

    async def test_success(self):
        """Successful registry initialization."""
        from app.connectors_main import initialize_connector_registry

        mock_container = _make_container()
        mock_registry = MagicMock()
        mock_registry._connectors = {"a": 1, "b": 2}
        mock_registry.sync_with_database = AsyncMock()

        with (
            patch("app.connectors_main.ConnectorRegistry", return_value=mock_registry),
            patch("app.connectors_main.ConnectorFactory.initialize_beta_connector_registry"),
            patch("app.connectors_main.ConnectorFactory.list_connectors", return_value={"drive": MagicMock(), "slack": MagicMock()}),
        ):
            result = await initialize_connector_registry(mock_container)

        assert result is mock_registry
        assert mock_registry.register_connector.call_count == 2
        mock_registry.sync_with_database.assert_awaited_once()

    async def test_exception_is_raised(self):
        """Exception during registry init is propagated."""
        from app.connectors_main import initialize_connector_registry

        mock_container = _make_container()

        with (
            patch("app.connectors_main.ConnectorRegistry", side_effect=RuntimeError("fail")),
        ):
            with pytest.raises(RuntimeError, match="fail"):
                await initialize_connector_registry(mock_container)


# ---------------------------------------------------------------------------
# start_messaging_producer
# ---------------------------------------------------------------------------
class TestStartMessagingProducer:
    """Tests for start_messaging_producer()."""

    async def test_success(self):
        """Producer is created, initialized, and attached to container."""
        from app.connectors_main import start_messaging_producer

        mock_container = _make_container()
        mock_producer = MagicMock()
        mock_producer.initialize = AsyncMock()
        mock_kafka_service = MagicMock()
        mock_container.kafka_service.return_value = mock_kafka_service

        with (
            patch("app.connectors_main.get_message_broker_type", return_value=MessageBrokerType.KAFKA),
            patch("app.connectors_main.MessagingUtils.create_producer_config", new_callable=AsyncMock, return_value={}),
            patch("app.connectors_main.MessagingFactory.create_producer", return_value=mock_producer),
        ):
            await start_messaging_producer(mock_container)

        mock_producer.initialize.assert_awaited_once()
        assert mock_container.messaging_producer is mock_producer
        mock_kafka_service.set_producer.assert_called_once_with(mock_producer)

    async def test_exception_is_raised(self):
        """Exception during producer start is propagated."""
        from app.connectors_main import start_messaging_producer

        mock_container = _make_container()

        with (
            patch("app.connectors_main.get_message_broker_type", return_value=MessageBrokerType.KAFKA),
            patch("app.connectors_main.MessagingUtils.create_producer_config", new_callable=AsyncMock, side_effect=RuntimeError("kafka down")),
        ):
            with pytest.raises(RuntimeError, match="kafka down"):
                await start_messaging_producer(mock_container)


# ---------------------------------------------------------------------------
# start_kafka_consumers (connectors)
# ---------------------------------------------------------------------------
class TestStartKafkaConsumers:
    """Tests for start_kafka_consumers()."""

    async def test_success_both_consumers(self):
        """Both entity and sync consumers are started successfully."""
        from app.connectors_main import start_kafka_consumers

        mock_container = _make_container()
        gp = _make_graph_provider()

        mock_entity_consumer = MagicMock()
        mock_entity_consumer.start = AsyncMock()
        mock_sync_consumer = MagicMock()
        mock_sync_consumer.start = AsyncMock()

        with (
            patch("app.connectors_main.get_message_broker_type", return_value=MessageBrokerType.KAFKA),
            patch("app.connectors_main.MessagingUtils.create_entity_consumer_config", new_callable=AsyncMock, return_value={}),
            patch("app.connectors_main.MessagingUtils.create_sync_consumer_config", new_callable=AsyncMock, return_value={}),
            patch("app.connectors_main.KafkaUtils.create_entity_message_handler", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.connectors_main.KafkaUtils.create_sync_message_handler", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.connectors_main.MessagingFactory.create_consumer", side_effect=[mock_entity_consumer, mock_sync_consumer]),
        ):
            consumers = await start_kafka_consumers(mock_container, gp)

        assert len(consumers) == 2
        assert consumers[0] == ("entity", mock_entity_consumer)
        assert consumers[1] == ("sync", mock_sync_consumer)

    async def test_error_on_second_consumer_cleans_up_first(self):
        """If second consumer fails, first consumer is stopped for cleanup."""
        from app.connectors_main import start_kafka_consumers

        mock_container = _make_container()
        gp = _make_graph_provider()

        mock_entity_consumer = MagicMock()
        mock_entity_consumer.start = AsyncMock()
        mock_entity_consumer.stop = AsyncMock()

        with (
            patch("app.connectors_main.get_message_broker_type", return_value=MessageBrokerType.KAFKA),
            patch("app.connectors_main.MessagingUtils.create_entity_consumer_config", new_callable=AsyncMock, return_value={}),
            patch("app.connectors_main.MessagingUtils.create_sync_consumer_config", new_callable=AsyncMock, side_effect=RuntimeError("sync config fail")),
            patch("app.connectors_main.KafkaUtils.create_entity_message_handler", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.connectors_main.MessagingFactory.create_consumer", return_value=mock_entity_consumer),
        ):
            with pytest.raises(RuntimeError, match="sync config fail"):
                await start_kafka_consumers(mock_container, gp)

        mock_entity_consumer.stop.assert_awaited_once()

    async def test_cleanup_error_during_cleanup(self):
        """If cleanup itself fails, it logs but still raises the original error."""
        from app.connectors_main import start_kafka_consumers

        mock_container = _make_container()
        gp = _make_graph_provider()

        mock_entity_consumer = MagicMock()
        mock_entity_consumer.start = AsyncMock()
        mock_entity_consumer.stop = AsyncMock(side_effect=RuntimeError("cleanup fail"))

        with (
            patch("app.connectors_main.get_message_broker_type", return_value=MessageBrokerType.KAFKA),
            patch("app.connectors_main.MessagingUtils.create_entity_consumer_config", new_callable=AsyncMock, return_value={}),
            patch("app.connectors_main.MessagingUtils.create_sync_consumer_config", new_callable=AsyncMock, side_effect=RuntimeError("config fail")),
            patch("app.connectors_main.KafkaUtils.create_entity_message_handler", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.connectors_main.MessagingFactory.create_consumer", return_value=mock_entity_consumer),
        ):
            with pytest.raises(RuntimeError, match="config fail"):
                await start_kafka_consumers(mock_container, gp)


# ---------------------------------------------------------------------------
# stop_kafka_consumers
# ---------------------------------------------------------------------------
class TestStopKafkaConsumers:
    """Tests for stop_kafka_consumers()."""

    async def test_stops_all_consumers(self):
        """All consumers are stopped and list is cleared."""
        from app.connectors_main import stop_kafka_consumers

        mock_container = _make_container()
        c1 = MagicMock()
        c1.stop = AsyncMock()
        c2 = MagicMock()
        c2.stop = AsyncMock()
        mock_container.kafka_consumers = [("entity", c1), ("sync", c2)]

        await stop_kafka_consumers(mock_container)

        c1.stop.assert_awaited_once()
        c2.stop.assert_awaited_once()
        assert mock_container.kafka_consumers == []

    async def test_empty_consumers_list(self):
        """No error when consumers list is empty."""
        from app.connectors_main import stop_kafka_consumers

        mock_container = _make_container()
        mock_container.kafka_consumers = []

        await stop_kafka_consumers(mock_container)

    async def test_no_kafka_consumers_attr(self):
        """No error when kafka_consumers attribute does not exist."""
        from app.connectors_main import stop_kafka_consumers

        mock_container = _make_container()
        # Use a real object so delattr works
        class Container:
            pass
        c = Container()
        c.logger = MagicMock(return_value=MagicMock())

        await stop_kafka_consumers(c)

    async def test_error_stopping_consumer_continues(self):
        """Error stopping one consumer does not prevent stopping others."""
        from app.connectors_main import stop_kafka_consumers

        mock_container = _make_container()
        c1 = MagicMock()
        c1.stop = AsyncMock(side_effect=RuntimeError("stop fail"))
        c2 = MagicMock()
        c2.stop = AsyncMock()
        mock_container.kafka_consumers = [("entity", c1), ("sync", c2)]

        await stop_kafka_consumers(mock_container)
        c2.stop.assert_awaited_once()
        assert mock_container.kafka_consumers == []


# ---------------------------------------------------------------------------
# stop_messaging_producer
# ---------------------------------------------------------------------------
class TestStopMessagingProducer:
    """Tests for stop_messaging_producer()."""

    async def test_success(self):
        """Producer is cleaned up successfully."""
        from app.connectors_main import stop_messaging_producer

        mock_container = _make_container()
        mock_producer = MagicMock()
        mock_producer.cleanup = AsyncMock()
        mock_container.messaging_producer = mock_producer

        await stop_messaging_producer(mock_container)
        mock_producer.cleanup.assert_awaited_once()

    async def test_no_producer(self):
        """No error when there is no messaging producer."""
        from app.connectors_main import stop_messaging_producer

        mock_container = _make_container()
        # messaging_producer is not set (getattr returns None by default for MagicMock)
        # Explicitly set it to None
        mock_container.messaging_producer = None

        await stop_messaging_producer(mock_container)

    async def test_cleanup_exception(self):
        """Exception during cleanup is caught and logged."""
        from app.connectors_main import stop_messaging_producer

        mock_container = _make_container()
        mock_producer = MagicMock()
        mock_producer.cleanup = AsyncMock(side_effect=RuntimeError("cleanup boom"))
        mock_container.messaging_producer = mock_producer

        # Should not raise
        await stop_messaging_producer(mock_container)


# ---------------------------------------------------------------------------
# shutdown_container_resources
# ---------------------------------------------------------------------------
class TestShutdownContainerResources:
    """Tests for shutdown_container_resources()."""

    async def test_success_full_shutdown(self):
        """All resources are shut down in order."""
        from app.connectors_main import shutdown_container_resources

        mock_container = _make_container()
        mock_container.kafka_consumers = []
        mock_container.messaging_producer = None

        with (
            patch("app.connectors_main.sync_task_manager.cancel_all", new_callable=AsyncMock),
            patch("app.connectors_main.stop_kafka_consumers", new_callable=AsyncMock) as mock_stop_kafka,
            patch("app.connectors_main.stop_messaging_producer", new_callable=AsyncMock) as mock_stop_producer,
            patch("app.connectors_main.startup_service.shutdown", new_callable=AsyncMock) as mock_startup_shutdown,
        ):
            await shutdown_container_resources(mock_container)

        mock_stop_kafka.assert_awaited_once()
        mock_stop_producer.assert_awaited_once()
        mock_startup_shutdown.assert_awaited_once()

    async def test_cancel_all_error_continues(self):
        """Error in cancel_all does not prevent other shutdown steps."""
        from app.connectors_main import shutdown_container_resources

        mock_container = _make_container()
        mock_container.kafka_consumers = []
        mock_container.messaging_producer = None

        with (
            patch("app.connectors_main.sync_task_manager.cancel_all", new_callable=AsyncMock, side_effect=RuntimeError("cancel fail")),
            patch("app.connectors_main.stop_kafka_consumers", new_callable=AsyncMock) as mock_stop_kafka,
            patch("app.connectors_main.stop_messaging_producer", new_callable=AsyncMock),
            patch("app.connectors_main.startup_service.shutdown", new_callable=AsyncMock),
        ):
            await shutdown_container_resources(mock_container)

        mock_stop_kafka.assert_awaited_once()

    async def test_startup_service_shutdown_error_continues(self):
        """Error in startup_service.shutdown does not prevent config service close."""
        from app.connectors_main import shutdown_container_resources

        mock_container = _make_container()
        mock_container.kafka_consumers = []
        mock_container.messaging_producer = None

        with (
            patch("app.connectors_main.sync_task_manager.cancel_all", new_callable=AsyncMock),
            patch("app.connectors_main.stop_kafka_consumers", new_callable=AsyncMock),
            patch("app.connectors_main.stop_messaging_producer", new_callable=AsyncMock),
            patch("app.connectors_main.startup_service.shutdown", new_callable=AsyncMock, side_effect=RuntimeError("shutdown fail")),
        ):
            await shutdown_container_resources(mock_container)

        # config_service().close() should still be called
        mock_container.config_service().close.assert_awaited()

    async def test_config_service_close_error(self):
        """Error closing config service is caught."""
        from app.connectors_main import shutdown_container_resources

        mock_container = _make_container()
        mock_container.kafka_consumers = []
        mock_container.messaging_producer = None
        mock_container.config_service.return_value.close = AsyncMock(side_effect=RuntimeError("close fail"))

        with (
            patch("app.connectors_main.sync_task_manager.cancel_all", new_callable=AsyncMock),
            patch("app.connectors_main.stop_kafka_consumers", new_callable=AsyncMock),
            patch("app.connectors_main.stop_messaging_producer", new_callable=AsyncMock),
            patch("app.connectors_main.startup_service.shutdown", new_callable=AsyncMock),
        ):
            # Should not raise
            await shutdown_container_resources(mock_container)


# ---------------------------------------------------------------------------
# lifespan
# ---------------------------------------------------------------------------
class TestLifespan:
    """Tests for lifespan() context manager."""

    async def test_startup_and_shutdown_arangodb(self):
        """Full lifespan cycle with arangodb data store."""
        from app.connectors_main import lifespan

        mock_container = _make_container()
        gp = _make_graph_provider()
        ds = _make_data_store(gp)
        mock_container.data_store = AsyncMock(return_value=ds)
        mock_container.arango_service = AsyncMock(return_value=MagicMock())

        mock_app = MagicMock()
        mock_app.state = MagicMock()
        mock_registry = MagicMock()
        mock_registry._connectors = {}

        mock_toolset_registry = MagicMock()
        mock_toolset_registry.list_toolsets.return_value = []
        mock_tools_registry = MagicMock()
        mock_tools_registry.list_tools.return_value = []
        mock_oauth_registry = MagicMock()

        with (
            patch("app.connectors_main.get_initialized_container", new_callable=AsyncMock, return_value=mock_container),
            patch("app.connectors_main.initialize_connector_registry", new_callable=AsyncMock, return_value=mock_registry),
            patch("app.connectors_main.startup_service.initialize", new_callable=AsyncMock),
            patch("app.connectors_main.start_messaging_producer", new_callable=AsyncMock),
            patch("app.connectors_main.resume_sync_services", new_callable=AsyncMock),
            patch("app.connectors_main.start_kafka_consumers", new_callable=AsyncMock, return_value=[]),
            patch("app.connectors_main.shutdown_container_resources", new_callable=AsyncMock) as mock_shutdown,
            patch("os.getenv", return_value="arangodb"),
            patch.dict("sys.modules", {
                "app.agents.registry.toolset_registry": MagicMock(get_toolset_registry=MagicMock(return_value=mock_toolset_registry)),
                "app.agents.tools.registry": MagicMock(_global_tools_registry=mock_tools_registry),
                "app.connectors.core.registry.oauth_config_registry": MagicMock(get_oauth_config_registry=MagicMock(return_value=mock_oauth_registry)),
            }),
        ):
            async with lifespan(mock_app):
                assert mock_app.container is mock_container
                assert mock_app.state.connector_registry is mock_registry

            mock_shutdown.assert_awaited_once()

    async def test_startup_non_arangodb_skips_migration(self):
        """Non-arangodb data store skips arango service and migration."""
        from app.connectors_main import lifespan

        mock_container = _make_container()
        gp = _make_graph_provider()
        ds = _make_data_store(gp)
        mock_container.data_store = AsyncMock(return_value=ds)

        mock_app = MagicMock()
        mock_app.state = MagicMock()
        mock_registry = MagicMock()
        mock_registry._connectors = {}

        mock_toolset_registry = MagicMock()
        mock_toolset_registry.list_toolsets.return_value = []
        mock_tools_registry = MagicMock()
        mock_tools_registry.list_tools.return_value = []
        mock_oauth_registry = MagicMock()

        with (
            patch("app.connectors_main.get_initialized_container", new_callable=AsyncMock, return_value=mock_container),
            patch("app.connectors_main.initialize_connector_registry", new_callable=AsyncMock, return_value=mock_registry),
            patch("app.connectors_main.startup_service.initialize", new_callable=AsyncMock),
            patch("app.connectors_main.start_messaging_producer", new_callable=AsyncMock),
            patch("app.connectors_main.resume_sync_services", new_callable=AsyncMock),
            patch("app.connectors_main.start_kafka_consumers", new_callable=AsyncMock, return_value=[]),
            patch("app.connectors_main.shutdown_container_resources", new_callable=AsyncMock),
            patch("os.getenv", return_value="neo4j"),
            patch.dict("sys.modules", {
                "app.agents.registry.toolset_registry": MagicMock(get_toolset_registry=MagicMock(return_value=mock_toolset_registry)),
                "app.agents.tools.registry": MagicMock(_global_tools_registry=mock_tools_registry),
                "app.connectors.core.registry.oauth_config_registry": MagicMock(get_oauth_config_registry=MagicMock(return_value=mock_oauth_registry)),
            }),
        ):
            async with lifespan(mock_app):
                assert mock_app.state.arango_service is None

    async def test_startup_service_init_failure_continues(self):
        """Startup service init failure does not prevent startup."""
        from app.connectors_main import lifespan

        mock_container = _make_container()
        gp = _make_graph_provider()
        ds = _make_data_store(gp)
        mock_container.data_store = AsyncMock(return_value=ds)

        mock_app = MagicMock()
        mock_app.state = MagicMock()
        mock_registry = MagicMock()
        mock_registry._connectors = {}

        mock_toolset_registry = MagicMock()
        mock_toolset_registry.list_toolsets.return_value = []
        mock_tools_registry = MagicMock()
        mock_tools_registry.list_tools.return_value = []
        mock_oauth_registry = MagicMock()

        with (
            patch("app.connectors_main.get_initialized_container", new_callable=AsyncMock, return_value=mock_container),
            patch("app.connectors_main.initialize_connector_registry", new_callable=AsyncMock, return_value=mock_registry),
            patch("app.connectors_main.startup_service.initialize", new_callable=AsyncMock, side_effect=RuntimeError("init fail")),
            patch("app.connectors_main.start_messaging_producer", new_callable=AsyncMock),
            patch("app.connectors_main.resume_sync_services", new_callable=AsyncMock),
            patch("app.connectors_main.start_kafka_consumers", new_callable=AsyncMock, return_value=[]),
            patch("app.connectors_main.shutdown_container_resources", new_callable=AsyncMock),
            patch("os.getenv", return_value="neo4j"),
            patch.dict("sys.modules", {
                "app.agents.registry.toolset_registry": MagicMock(get_toolset_registry=MagicMock(return_value=mock_toolset_registry)),
                "app.agents.tools.registry": MagicMock(_global_tools_registry=mock_tools_registry),
                "app.connectors.core.registry.oauth_config_registry": MagicMock(get_oauth_config_registry=MagicMock(return_value=mock_oauth_registry)),
            }),
        ):
            async with lifespan(mock_app):
                pass  # Startup succeeded despite init failure

    async def test_kafka_consumer_failure_raises(self):
        """If Kafka consumers fail to start, the lifespan raises."""
        from app.connectors_main import lifespan

        mock_container = _make_container()
        gp = _make_graph_provider()
        ds = _make_data_store(gp)
        mock_container.data_store = AsyncMock(return_value=ds)

        mock_app = MagicMock()
        mock_app.state = MagicMock()
        mock_registry = MagicMock()
        mock_registry._connectors = {}

        mock_toolset_registry = MagicMock()
        mock_toolset_registry.list_toolsets.return_value = []
        mock_tools_registry = MagicMock()
        mock_tools_registry.list_tools.return_value = []
        mock_oauth_registry = MagicMock()

        with (
            patch("app.connectors_main.get_initialized_container", new_callable=AsyncMock, return_value=mock_container),
            patch("app.connectors_main.initialize_connector_registry", new_callable=AsyncMock, return_value=mock_registry),
            patch("app.connectors_main.startup_service.initialize", new_callable=AsyncMock),
            patch("app.connectors_main.start_messaging_producer", new_callable=AsyncMock),
            patch("app.connectors_main.resume_sync_services", new_callable=AsyncMock),
            patch("app.connectors_main.start_kafka_consumers", new_callable=AsyncMock, side_effect=RuntimeError("kafka fail")),
            patch("os.getenv", return_value="neo4j"),
            patch.dict("sys.modules", {
                "app.agents.registry.toolset_registry": MagicMock(get_toolset_registry=MagicMock(return_value=mock_toolset_registry)),
                "app.agents.tools.registry": MagicMock(_global_tools_registry=mock_tools_registry),
                "app.connectors.core.registry.oauth_config_registry": MagicMock(get_oauth_config_registry=MagicMock(return_value=mock_oauth_registry)),
            }),
        ):
            with pytest.raises(RuntimeError, match="kafka fail"):
                async with lifespan(mock_app):
                    pass

    async def test_messaging_producer_failure_raises(self):
        """If messaging producer fails to start, the lifespan raises."""
        from app.connectors_main import lifespan

        mock_container = _make_container()
        gp = _make_graph_provider()
        ds = _make_data_store(gp)
        mock_container.data_store = AsyncMock(return_value=ds)

        mock_app = MagicMock()
        mock_app.state = MagicMock()
        mock_registry = MagicMock()
        mock_registry._connectors = {}

        mock_toolset_registry = MagicMock()
        mock_toolset_registry.list_toolsets.return_value = []
        mock_tools_registry = MagicMock()
        mock_tools_registry.list_tools.return_value = []
        mock_oauth_registry = MagicMock()

        with (
            patch("app.connectors_main.get_initialized_container", new_callable=AsyncMock, return_value=mock_container),
            patch("app.connectors_main.initialize_connector_registry", new_callable=AsyncMock, return_value=mock_registry),
            patch("app.connectors_main.startup_service.initialize", new_callable=AsyncMock),
            patch("app.connectors_main.start_messaging_producer", new_callable=AsyncMock, side_effect=RuntimeError("producer fail")),
            patch("os.getenv", return_value="neo4j"),
            patch.dict("sys.modules", {
                "app.agents.registry.toolset_registry": MagicMock(get_toolset_registry=MagicMock(return_value=mock_toolset_registry)),
                "app.agents.tools.registry": MagicMock(_global_tools_registry=mock_tools_registry),
                "app.connectors.core.registry.oauth_config_registry": MagicMock(get_oauth_config_registry=MagicMock(return_value=mock_oauth_registry)),
            }),
        ):
            with pytest.raises(RuntimeError, match="producer fail"):
                async with lifespan(mock_app):
                    pass

    async def test_resume_sync_failure_does_not_raise(self):
        """Resume sync failure is logged but does not prevent startup."""
        from app.connectors_main import lifespan

        mock_container = _make_container()
        gp = _make_graph_provider()
        ds = _make_data_store(gp)
        mock_container.data_store = AsyncMock(return_value=ds)

        mock_app = MagicMock()
        mock_app.state = MagicMock()
        mock_registry = MagicMock()
        mock_registry._connectors = {}

        mock_toolset_registry = MagicMock()
        mock_toolset_registry.list_toolsets.return_value = []
        mock_tools_registry = MagicMock()
        mock_tools_registry.list_tools.return_value = []
        mock_oauth_registry = MagicMock()

        with (
            patch("app.connectors_main.get_initialized_container", new_callable=AsyncMock, return_value=mock_container),
            patch("app.connectors_main.initialize_connector_registry", new_callable=AsyncMock, return_value=mock_registry),
            patch("app.connectors_main.startup_service.initialize", new_callable=AsyncMock),
            patch("app.connectors_main.start_messaging_producer", new_callable=AsyncMock),
            patch("app.connectors_main.resume_sync_services", new_callable=AsyncMock, side_effect=RuntimeError("sync fail")),
            patch("app.connectors_main.start_kafka_consumers", new_callable=AsyncMock, return_value=[]),
            patch("app.connectors_main.shutdown_container_resources", new_callable=AsyncMock),
            patch("os.getenv", return_value="neo4j"),
            patch.dict("sys.modules", {
                "app.agents.registry.toolset_registry": MagicMock(get_toolset_registry=MagicMock(return_value=mock_toolset_registry)),
                "app.agents.tools.registry": MagicMock(_global_tools_registry=mock_tools_registry),
                "app.connectors.core.registry.oauth_config_registry": MagicMock(get_oauth_config_registry=MagicMock(return_value=mock_oauth_registry)),
            }),
        ):
            async with lifespan(mock_app):
                pass  # Should not raise

    async def test_shutdown_error_is_caught(self):
        """Error during shutdown is caught and does not propagate."""
        from app.connectors_main import lifespan

        mock_container = _make_container()
        gp = _make_graph_provider()
        ds = _make_data_store(gp)
        mock_container.data_store = AsyncMock(return_value=ds)

        mock_app = MagicMock()
        mock_app.state = MagicMock()
        mock_registry = MagicMock()
        mock_registry._connectors = {}

        mock_toolset_registry = MagicMock()
        mock_toolset_registry.list_toolsets.return_value = []
        mock_tools_registry = MagicMock()
        mock_tools_registry.list_tools.return_value = []
        mock_oauth_registry = MagicMock()

        with (
            patch("app.connectors_main.get_initialized_container", new_callable=AsyncMock, return_value=mock_container),
            patch("app.connectors_main.initialize_connector_registry", new_callable=AsyncMock, return_value=mock_registry),
            patch("app.connectors_main.startup_service.initialize", new_callable=AsyncMock),
            patch("app.connectors_main.start_messaging_producer", new_callable=AsyncMock),
            patch("app.connectors_main.resume_sync_services", new_callable=AsyncMock),
            patch("app.connectors_main.start_kafka_consumers", new_callable=AsyncMock, return_value=[]),
            patch("app.connectors_main.shutdown_container_resources", new_callable=AsyncMock, side_effect=RuntimeError("shutdown fail")),
            patch("os.getenv", return_value="neo4j"),
            patch.dict("sys.modules", {
                "app.agents.registry.toolset_registry": MagicMock(get_toolset_registry=MagicMock(return_value=mock_toolset_registry)),
                "app.agents.tools.registry": MagicMock(_global_tools_registry=mock_tools_registry),
                "app.connectors.core.registry.oauth_config_registry": MagicMock(get_oauth_config_registry=MagicMock(return_value=mock_oauth_registry)),
            }),
        ):
            async with lifespan(mock_app):
                pass  # Should not raise on shutdown error


# ---------------------------------------------------------------------------
# authenticate_requests middleware
# ---------------------------------------------------------------------------
class TestAuthenticateRequestsMiddleware:
    """Tests for the authenticate_requests middleware."""

    async def test_excluded_path_health(self):
        """Health check path is excluded from auth."""
        from app.connectors_main import authenticate_requests, app

        mock_request = MagicMock()
        mock_request.url.path = "/health"

        mock_response = MagicMock(spec=JSONResponse)
        mock_call_next = AsyncMock(return_value=mock_response)

        mock_logger = MagicMock()
        app.container = MagicMock()
        app.container.logger.return_value = mock_logger

        result = await authenticate_requests(mock_request, mock_call_next)
        mock_call_next.assert_awaited_once_with(mock_request)
        assert result is mock_response

    async def test_excluded_path_drive_webhook(self):
        """Drive webhook path is excluded from auth."""
        from app.connectors_main import authenticate_requests, app

        mock_request = MagicMock()
        mock_request.url.path = "/drive/webhook"

        mock_response = MagicMock(spec=JSONResponse)
        mock_call_next = AsyncMock(return_value=mock_response)

        mock_logger = MagicMock()
        app.container = MagicMock()
        app.container.logger.return_value = mock_logger

        result = await authenticate_requests(mock_request, mock_call_next)
        mock_call_next.assert_awaited_once_with(mock_request)
        assert result is mock_response

    async def test_excluded_path_gmail_webhook(self):
        """Gmail webhook path is excluded from auth."""
        from app.connectors_main import authenticate_requests, app

        mock_request = MagicMock()
        mock_request.url.path = "/gmail/webhook"

        mock_response = MagicMock(spec=JSONResponse)
        mock_call_next = AsyncMock(return_value=mock_response)

        mock_logger = MagicMock()
        app.container = MagicMock()
        app.container.logger.return_value = mock_logger

        result = await authenticate_requests(mock_request, mock_call_next)
        mock_call_next.assert_awaited_once_with(mock_request)

    async def test_excluded_path_admin_webhook(self):
        """Admin webhook path is excluded from auth."""
        from app.connectors_main import authenticate_requests, app

        mock_request = MagicMock()
        mock_request.url.path = "/admin/webhook"

        mock_response = MagicMock(spec=JSONResponse)
        mock_call_next = AsyncMock(return_value=mock_response)

        mock_logger = MagicMock()
        app.container = MagicMock()
        app.container.logger.return_value = mock_logger

        result = await authenticate_requests(mock_request, mock_call_next)
        mock_call_next.assert_awaited_once_with(mock_request)

    async def test_auth_success(self):
        """Authenticated request is forwarded to call_next."""
        from app.connectors_main import authenticate_requests, app

        mock_request = MagicMock()
        mock_request.url.path = "/api/connectors"

        mock_authenticated_request = MagicMock()
        mock_response = MagicMock(spec=JSONResponse)
        mock_call_next = AsyncMock(return_value=mock_response)

        mock_logger = MagicMock()
        app.container = MagicMock()
        app.container.logger.return_value = mock_logger

        with patch("app.connectors_main.authMiddleware", new_callable=AsyncMock, return_value=mock_authenticated_request):
            result = await authenticate_requests(mock_request, mock_call_next)

        mock_call_next.assert_awaited_once_with(mock_authenticated_request)
        assert result is mock_response

    async def test_auth_http_exception(self):
        """HTTPException during auth returns JSON error response."""
        from app.connectors_main import authenticate_requests, app

        mock_request = MagicMock()
        mock_request.url.path = "/api/connectors"

        mock_call_next = AsyncMock()

        mock_logger = MagicMock()
        app.container = MagicMock()
        app.container.logger.return_value = mock_logger

        with patch("app.connectors_main.authMiddleware", new_callable=AsyncMock, side_effect=HTTPException(status_code=401, detail="Unauthorized")):
            result = await authenticate_requests(mock_request, mock_call_next)

        assert result.status_code == 401
        mock_call_next.assert_not_awaited()

    async def test_auth_unexpected_exception(self):
        """Unexpected exception during auth returns 500 error."""
        from app.connectors_main import authenticate_requests, app

        mock_request = MagicMock()
        mock_request.url.path = "/api/connectors"

        mock_call_next = AsyncMock()

        mock_logger = MagicMock()
        app.container = MagicMock()
        app.container.logger.return_value = mock_logger

        with patch("app.connectors_main.authMiddleware", new_callable=AsyncMock, side_effect=RuntimeError("unexpected")):
            result = await authenticate_requests(mock_request, mock_call_next)

        assert result.status_code == 500
        mock_call_next.assert_not_awaited()

    async def test_non_excluded_path_requires_auth(self):
        """Non-excluded paths go through auth middleware."""
        from app.connectors_main import authenticate_requests, app

        mock_request = MagicMock()
        mock_request.url.path = "/api/some/endpoint"

        mock_call_next = AsyncMock(return_value=MagicMock(spec=JSONResponse))

        mock_logger = MagicMock()
        app.container = MagicMock()
        app.container.logger.return_value = mock_logger

        with patch("app.connectors_main.authMiddleware", new_callable=AsyncMock, return_value=mock_request) as mock_auth:
            await authenticate_requests(mock_request, mock_call_next)

        mock_auth.assert_awaited_once_with(mock_request)


# ---------------------------------------------------------------------------
# health_check (connector)
# ---------------------------------------------------------------------------
class TestConnectorHealthCheck:
    """Tests for health_check() endpoint."""

    async def test_health_check_success(self):
        """Health check returns healthy status."""
        from app.connectors_main import health_check

        with patch("app.connectors_main.get_epoch_timestamp_in_ms", return_value=1234567890):
            result = await health_check()

        assert result.status_code == 200

    async def test_health_check_exception(self):
        """Health check returns fail status on exception."""
        from app.connectors_main import health_check

        # First call in the try block raises, second call in except block succeeds
        with patch("app.connectors_main.get_epoch_timestamp_in_ms", side_effect=[RuntimeError("time error"), 1234567890]):
            result = await health_check()

        assert result.status_code == 500


# ---------------------------------------------------------------------------
# global_exception_handler
# ---------------------------------------------------------------------------
class TestGlobalExceptionHandler:
    """Tests for global_exception_handler()."""

    async def test_returns_500_with_error_details(self):
        """Global handler returns 500 with error message and path."""
        from app.connectors_main import global_exception_handler, app

        mock_request = MagicMock()
        mock_request.url.path = "/api/test"

        mock_logger = MagicMock()
        app.container = MagicMock()
        app.container.logger.return_value = mock_logger

        exc = RuntimeError("something went wrong")
        result = await global_exception_handler(mock_request, exc)

        assert result.status_code == 500


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------
class TestRun:
    """Tests for run() function."""

    def test_run_default_args(self):
        """run() invokes uvicorn with default arguments."""
        from app.connectors_main import run

        with patch("app.connectors_main.uvicorn.run") as mock_uvicorn:
            run()

        mock_uvicorn.assert_called_once_with(
            "app.connectors_main:app",
            host="0.0.0.0",
            port=8088,
            log_level="info",
            reload=True,
            workers=1,
        )

    def test_run_custom_args(self):
        """run() passes custom arguments to uvicorn."""
        from app.connectors_main import run

        with patch("app.connectors_main.uvicorn.run") as mock_uvicorn:
            run(host="127.0.0.1", port=9000, workers=4, reload=False)

        mock_uvicorn.assert_called_once_with(
            "app.connectors_main:app",
            host="127.0.0.1",
            port=9000,
            log_level="info",
            reload=False,
            workers=4,
        )


# ---------------------------------------------------------------------------
# EXCLUDE_PATHS
# ---------------------------------------------------------------------------
class TestExcludePaths:
    """Tests for EXCLUDE_PATHS configuration."""

    def test_exclude_paths_contains_expected_paths(self):
        """EXCLUDE_PATHS includes all expected paths."""
        from app.connectors_main import EXCLUDE_PATHS

        assert "/health" in EXCLUDE_PATHS
        assert "/drive/webhook" in EXCLUDE_PATHS
        assert "/gmail/webhook" in EXCLUDE_PATHS
        assert "/admin/webhook" in EXCLUDE_PATHS

    def test_exclude_paths_length(self):
        """EXCLUDE_PATHS has exactly 4 entries."""
        from app.connectors_main import EXCLUDE_PATHS

        assert len(EXCLUDE_PATHS) == 4
