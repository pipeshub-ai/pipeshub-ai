"""Comprehensive unit tests for app.connectors_main module."""

import asyncio
import json
import logging
import sys
import types
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse

from app.services.messaging.config import MessageBrokerType
from tests.support.host_header import POISONED_HOSTS, request_with_host

@pytest.fixture(autouse=True)
def _no_inherited_worker_healthcheck_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UVICORN_WORKER_HEALTHCHECK_TIMEOUT_SECONDS", raising=False)



# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_component_registry() -> Iterator[None]:
    """start_kafka_consumers and the retries share module-level state with /health."""
    import app.connectors_main as cm

    yield
    for task in cm._retry_tasks:
        task.cancel()
    cm._retry_tasks.clear()
    cm._started_consumers.clear()
    cm._component_status.clear()


def _client_with_open_driver(run=None):
    """A Neo4j client whose driver is already open. ``run`` is session.run."""
    result = MagicMock()
    result.consume = AsyncMock()
    session = AsyncMock()
    session.run = run if run is not None else AsyncMock(return_value=result)
    ctx = AsyncMock()
    ctx.__aenter__.return_value = session
    ctx.__aexit__.return_value = False
    driver = MagicMock()
    driver.session.return_value = ctx
    client = MagicMock()
    client.driver = driver
    client.database = "neo4j"
    client.connect = MagicMock()
    return client, session


def _make_container():
    """Build a mock ConnectorAppContainer with common providers."""
    container = MagicMock()
    container.logger.return_value = MagicMock()
    mock_config_service = MagicMock()
    mock_config_service.get_config = AsyncMock(return_value={})
    mock_config_service.close = AsyncMock()
    container.config_service.return_value = mock_config_service
    container.data_store = AsyncMock()
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


def _mock_os_getenv(data_store="arangodb"):
    """Return getenv side_effect that preserves DATA_STORE and MESSAGE_BROKER."""
    def _getenv(key, default=None):
        if key == "DATA_STORE":
            return data_store
        if key == "MESSAGE_BROKER":
            return MessageBrokerType.KAFKA.value
        return default
    return _getenv


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

        import app.connectors_main as connectors_main

        with patch.object(connectors_main, "startup_service", mock_startup):
            result = await connectors_main.refresh_toolset_tokens(mock_container)

        assert result is True
        mock_refresh_service._refresh_all_tokens.assert_awaited_once()

    async def test_no_refresh_service_returns_false(self):
        """When refresh service is not initialized, returns False."""
        mock_container = _make_container()
        mock_startup = MagicMock()
        mock_startup.get_toolset_token_refresh_service.return_value = None

        import app.connectors_main as connectors_main

        with patch.object(connectors_main, "startup_service", mock_startup):
            result = await connectors_main.refresh_toolset_tokens(mock_container)

        assert result is False

    async def test_exception_returns_false(self):
        """Exception during refresh returns False."""
        mock_container = _make_container()
        mock_startup = MagicMock()
        mock_startup.get_toolset_token_refresh_service.side_effect = RuntimeError("boom")

        import app.connectors_main as connectors_main

        with patch.object(connectors_main, "startup_service", mock_startup):
            result = await connectors_main.refresh_toolset_tokens(mock_container)

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

    async def test_connector_owed_a_full_sync_is_published_not_started(self) -> None:
        """Started here it would sync incrementally; only the event path runs
        the full sync its pendingFullSync flag asks for."""
        from app.connectors_main import resume_sync_services

        mock_container = _make_container()
        mock_container.connectors_map = {}
        gp = _make_graph_provider()
        gp.get_all_orgs = AsyncMock(return_value=[{"_key": "org1"}])
        gp.get_org_apps = AsyncMock(return_value=[
            {"_key": "owed", "type": "Slack", "pendingFullSync": True},
            {"_key": "plain", "type": "Slack"},
        ])
        gp.get_users = AsyncMock(return_value=[{"_key": "user1"}])
        ds = _make_data_store(gp)

        with (
            patch("app.connectors_main.sync_executor_enabled", return_value=False),
            patch(
                "app.connectors_main.ConnectorFactory.create_and_start_sync",
                new_callable=AsyncMock,
                return_value=MagicMock(),
            ) as create,
            patch("app.connectors_main._publish_startup_resync", new_callable=AsyncMock) as publish,
        ):
            assert await resume_sync_services(mock_container, ds) is True

        started = {c.kwargs["connector_id"]: c.kwargs["start_sync"] for c in create.await_args_list}
        assert started == {"owed": False, "plain": True}
        assert [c.kwargs["connector_id"] for c in publish.await_args_list] == ["owed"]

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
            patch("app.connectors_main.get_connector_registry_cls", return_value=MagicMock(return_value=mock_registry)),
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
            patch(
                "app.connectors_main.get_connector_registry_cls",
                return_value=MagicMock(side_effect=RuntimeError("fail")),
            ),
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

    async def test_success_all_consumers(self) -> None:
        from app.connectors_main import start_kafka_consumers

        mock_container = _make_container()
        gp = _make_graph_provider()

        mock_entity_consumer = MagicMock()
        mock_entity_consumer.start = AsyncMock()
        mock_sync_consumer = MagicMock()
        mock_sync_consumer.start = AsyncMock()
        mock_code_graph_consumer = MagicMock()
        mock_code_graph_consumer.start = AsyncMock()

        with (
            patch("app.connectors_main.get_message_broker_type", return_value=MessageBrokerType.KAFKA),
            patch("app.connectors_main.MessagingUtils._get_redis_config", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.connectors_main.MessagingFactory.create_retry_manager", return_value=MagicMock(initialize=AsyncMock())),
            patch("app.connectors_main.MessagingUtils.create_entity_consumer_config", new_callable=AsyncMock, return_value={}),
            patch("app.connectors_main.MessagingUtils.create_sync_consumer_config", new_callable=AsyncMock, return_value={}),
            patch("app.connectors_main.MessagingUtils.create_code_graph_consumer_config", new_callable=AsyncMock, return_value={}),
            patch("app.connectors_main.KafkaUtils.create_entity_message_handler", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.connectors_main.KafkaUtils.create_sync_message_handler", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.connectors_main.KafkaUtils.create_code_graph_message_handler", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.connectors_main.MessagingFactory.create_consumer", side_effect=[mock_entity_consumer, mock_sync_consumer, mock_code_graph_consumer]),
        ):
            consumers = await start_kafka_consumers(mock_container, gp)

        assert len(consumers) == 3
        assert consumers[0] == ("entity", mock_entity_consumer)
        assert consumers[1] == ("sync", mock_sync_consumer)
        assert consumers[2] == ("code_graph", mock_code_graph_consumer)

    async def test_a_code_graph_consumer_failure_keeps_the_sync_consumers(self) -> None:
        """The code graph is optional; it must not take entity and sync events down."""
        from app.connectors_main import start_kafka_consumers

        mock_container = _make_container()
        gp = _make_graph_provider()
        mock_entity_consumer = MagicMock(start=AsyncMock(), stop=AsyncMock())
        mock_sync_consumer = MagicMock(start=AsyncMock(), stop=AsyncMock())

        with (
            patch("app.connectors_main.get_message_broker_type", return_value=MessageBrokerType.KAFKA),
            patch("app.connectors_main.MessagingUtils._get_redis_config", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.connectors_main.MessagingFactory.create_retry_manager", return_value=MagicMock(initialize=AsyncMock())),
            patch("app.connectors_main.MessagingUtils.create_entity_consumer_config", new_callable=AsyncMock, return_value={}),
            patch("app.connectors_main.MessagingUtils.create_sync_consumer_config", new_callable=AsyncMock, return_value={}),
            patch("app.connectors_main.MessagingUtils.create_code_graph_consumer_config", new_callable=AsyncMock, side_effect=RuntimeError("redis down")),
            patch("app.connectors_main.KafkaUtils.create_entity_message_handler", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.connectors_main.KafkaUtils.create_sync_message_handler", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.connectors_main.MessagingFactory.create_consumer", side_effect=[mock_entity_consumer, mock_sync_consumer]),
        ):
            consumers = await start_kafka_consumers(mock_container, gp)

        assert consumers == [("entity", mock_entity_consumer), ("sync", mock_sync_consumer)]
        mock_entity_consumer.stop.assert_not_awaited()
        mock_sync_consumer.stop.assert_not_awaited()

    async def test_a_failed_code_graph_consumer_is_retried_until_it_runs(self) -> None:
        """A boot-time broker hiccup used to leave the code graph without a
        consumer until the next restart."""
        import asyncio

        import app.connectors_main as cm

        mock_container = _make_container()
        gp = _make_graph_provider()
        entity = MagicMock(start=AsyncMock(), stop=AsyncMock(), is_running=MagicMock(return_value=True))
        sync = MagicMock(start=AsyncMock(), stop=AsyncMock(), is_running=MagicMock(return_value=True))
        code_graph = MagicMock(start=AsyncMock(), stop=AsyncMock(), is_running=MagicMock(return_value=True))

        with (
            patch("app.connectors_main._RETRY_FIRST_DELAY_SECONDS", 0),
            patch("app.connectors_main.get_message_broker_type", return_value=MessageBrokerType.KAFKA),
            patch("app.connectors_main.MessagingUtils._get_redis_config", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.connectors_main.MessagingFactory.create_retry_manager", return_value=MagicMock(initialize=AsyncMock())),
            patch("app.connectors_main.MessagingUtils.create_entity_consumer_config", new_callable=AsyncMock, return_value={}),
            patch("app.connectors_main.MessagingUtils.create_sync_consumer_config", new_callable=AsyncMock, return_value={}),
            patch("app.connectors_main.MessagingUtils.create_code_graph_consumer_config", new_callable=AsyncMock,
                  side_effect=[RuntimeError("redis down"), RuntimeError("still down"), {}]),
            patch("app.connectors_main.KafkaUtils.create_entity_message_handler", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.connectors_main.KafkaUtils.create_sync_message_handler", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.connectors_main.KafkaUtils.create_code_graph_message_handler", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.connectors_main.MessagingFactory.create_consumer", side_effect=[entity, sync, code_graph]),
        ):
            try:
                consumers = await cm.start_kafka_consumers(mock_container, gp)
                assert cm.component_health()["code_graph"]["state"] == "retrying"
                await asyncio.wait_for(asyncio.gather(*cm._retry_tasks), timeout=5)
                health = cm.component_health()
                started = list(consumers)
            finally:
                for task in cm._retry_tasks:
                    task.cancel()

        assert ("code_graph", code_graph) in started
        assert health["code_graph"] == {"state": "running", "required": False}

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
            patch("app.connectors_main.MessagingUtils._get_redis_config", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.connectors_main.MessagingFactory.create_retry_manager", return_value=MagicMock(initialize=AsyncMock())),
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
            patch("app.connectors_main.MessagingUtils._get_redis_config", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.connectors_main.MessagingFactory.create_retry_manager", return_value=MagicMock(initialize=AsyncMock())),
            patch("app.connectors_main.MessagingUtils.create_entity_consumer_config", new_callable=AsyncMock, return_value={}),
            patch("app.connectors_main.MessagingUtils.create_sync_consumer_config", new_callable=AsyncMock, side_effect=RuntimeError("config fail")),
            patch("app.connectors_main.KafkaUtils.create_entity_message_handler", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.connectors_main.MessagingFactory.create_consumer", return_value=mock_entity_consumer),
        ):
            with pytest.raises(RuntimeError, match="config fail"):
                await start_kafka_consumers(mock_container, gp)

    async def test_retry_manager_created_for_redis_broker(self):
        """RetryManager is created and initialized for Redis broker."""
        from app.connectors_main import start_kafka_consumers

        mock_container = _make_container()
        gp = _make_graph_provider()
        mock_entity_consumer = MagicMock()
        mock_entity_consumer.start = AsyncMock()
        mock_sync_consumer = MagicMock()
        mock_sync_consumer.start = AsyncMock()
        mock_code_graph_consumer = MagicMock()
        mock_code_graph_consumer.start = AsyncMock()
        mock_retry_manager = AsyncMock()
        mock_retry_manager.initialize = AsyncMock()

        with (
            patch("app.connectors_main.get_message_broker_type", return_value=MessageBrokerType.REDIS),
            patch("app.connectors_main.MessagingUtils._get_redis_config", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.connectors_main.MessagingFactory.create_retry_manager", return_value=mock_retry_manager) as mock_create_rm,
            patch("app.connectors_main.MessagingUtils.create_entity_consumer_config", new_callable=AsyncMock, return_value={}),
            patch("app.connectors_main.MessagingUtils.create_sync_consumer_config", new_callable=AsyncMock, return_value={}),
            patch("app.connectors_main.MessagingUtils.create_code_graph_consumer_config", new_callable=AsyncMock, return_value={}),
            patch("app.connectors_main.KafkaUtils.create_entity_message_handler", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.connectors_main.KafkaUtils.create_sync_message_handler", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.connectors_main.KafkaUtils.create_code_graph_message_handler", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.connectors_main.MessagingFactory.create_consumer", side_effect=[mock_entity_consumer, mock_sync_consumer, mock_code_graph_consumer]) as mock_create_consumer,
        ):
            consumers = await start_kafka_consumers(mock_container, gp)

        # Assert RetryManager was created and initialized
        mock_create_rm.assert_called_once()
        mock_retry_manager.initialize.assert_awaited_once()

        assert mock_create_consumer.call_count == 3
        for call in mock_create_consumer.call_args_list:
            assert call.kwargs["retry_manager"] is mock_retry_manager

        assert len(consumers) == 3
        assert consumers[0] == ("entity", mock_entity_consumer)
        assert consumers[1] == ("sync", mock_sync_consumer)
        assert consumers[2] == ("code_graph", mock_code_graph_consumer)


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
class TestOptionalComponentRetry:
    """The reconciler and code-graph consumer retry with backoff instead of
    staying down until a restart."""

    async def test_the_delay_doubles_and_status_clears_on_success(self) -> None:
        import app.connectors_main as cm

        attempt = AsyncMock(side_effect=[RuntimeError("a"), RuntimeError("b"), None])
        sleeps = []

        async def fake_sleep(delay) -> None:
            sleeps.append(delay)

        with patch("app.connectors_main.asyncio.sleep", side_effect=fake_sleep):
            await cm._keep_trying("code_edge_reconciler", attempt, logging.getLogger("t"), RuntimeError("first"))

        assert sleeps == [5.0, 10.0, 20.0]
        assert attempt.await_count == 3
        assert "code_edge_reconciler" not in cm._component_status

    async def test_the_delay_is_capped(self) -> None:
        import app.connectors_main as cm

        attempt = AsyncMock(side_effect=[RuntimeError("x")] * 8 + [None])
        sleeps = []

        async def fake_sleep(delay) -> None:
            sleeps.append(delay)

        with patch("app.connectors_main.asyncio.sleep", side_effect=fake_sleep):
            await cm._keep_trying("code_graph", attempt, logging.getLogger("t"), RuntimeError("first"))

        assert max(sleeps) == cm._RETRY_MAX_DELAY_SECONDS

    async def test_shutdown_cancels_a_pending_retry(self) -> None:
        import asyncio

        import app.connectors_main as cm
        from app.connectors_main import stop_kafka_consumers

        cm._start_retrying("code_graph", AsyncMock(side_effect=RuntimeError("down")),
                           logging.getLogger("t"), RuntimeError("down"))
        task = cm._retry_tasks[0]
        container = _make_container()
        container.kafka_consumers = []
        try:
            await stop_kafka_consumers(container)
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            cm._component_status.clear()

        assert cm._retry_tasks == []


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
            patch(
                "app.connectors_main.get_coordinator",
                return_value=MagicMock(cancel_all=AsyncMock(), stop=AsyncMock()),
            ) as get_coordinator,
            patch("app.connectors_main.stop_kafka_consumers", new_callable=AsyncMock) as mock_stop_kafka,
            patch("app.connectors_main.stop_messaging_producer", new_callable=AsyncMock) as mock_stop_producer,
            patch("app.connectors_main.startup_service.shutdown", new_callable=AsyncMock) as mock_startup_shutdown,
        ):
            await shutdown_container_resources(mock_container)

        get_coordinator.return_value.cancel_all.assert_awaited_once()
        get_coordinator.return_value.stop.assert_awaited_once()
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
            patch("app.connectors_main.get_coordinator", return_value=MagicMock(cancel_all=AsyncMock(side_effect=RuntimeError("cancel fail")), stop=AsyncMock())),
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
            patch("app.connectors_main.get_coordinator", return_value=MagicMock(cancel_all=AsyncMock(), stop=AsyncMock())),
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
            patch("app.connectors_main.get_coordinator", return_value=MagicMock(cancel_all=AsyncMock(), stop=AsyncMock())),
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
            patch("os.getenv", side_effect=_mock_os_getenv("arangodb")),
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
            patch("os.getenv", side_effect=_mock_os_getenv("neo4j")),
            patch.dict("sys.modules", {
                "app.agents.registry.toolset_registry": MagicMock(get_toolset_registry=MagicMock(return_value=mock_toolset_registry)),
                "app.agents.tools.registry": MagicMock(_global_tools_registry=mock_tools_registry),
                "app.connectors.core.registry.oauth_config_registry": MagicMock(get_oauth_config_registry=MagicMock(return_value=mock_oauth_registry)),
            }),
        ):
            async with lifespan(mock_app):
                assert mock_app.container is mock_container
                assert mock_app.state.connector_registry is mock_registry

    async def test_connector_oauth_repair_runs_after_the_registry_and_its_failure_does_not_stop_startup(self) -> None:
        from app.connectors_main import lifespan

        mock_container = _make_container()
        mock_container.data_store = AsyncMock(return_value=_make_data_store())
        mock_app = MagicMock()
        mock_registry = MagicMock()
        mock_registry._connectors = {}
        calls: list[str] = []

        async def _registry(*_args: object) -> MagicMock:
            calls.append("registry")
            return mock_registry

        async def _repair(*_args: object) -> dict:
            calls.append("repair")
            raise RuntimeError("store did not answer")

        with (
            patch("app.connectors_main.get_initialized_container", new_callable=AsyncMock, return_value=mock_container),
            patch("app.connectors_main.initialize_connector_registry", side_effect=_registry),
            patch(
                "app.migrations.connector_oauth_toolset_fields_migration.run_connector_oauth_toolset_fields_repair",
                side_effect=_repair,
            ) as mock_repair,
            patch("app.connectors_main.startup_service.initialize", new_callable=AsyncMock),
            patch("app.connectors_main.start_messaging_producer", new_callable=AsyncMock),
            patch("app.connectors_main.resume_sync_services", new_callable=AsyncMock),
            patch("app.connectors_main.start_kafka_consumers", new_callable=AsyncMock, return_value=[]),
            patch("app.connectors_main.shutdown_container_resources", new_callable=AsyncMock),
            patch("os.getenv", side_effect=_mock_os_getenv("neo4j")),
        ):
            async with lifespan(mock_app):
                assert mock_app.state.connector_registry is mock_registry

        assert calls == ["registry", "repair"]
        assert mock_repair.call_args.args[0] is mock_container.config_service.return_value

    async def test_toolset_registry_is_loaded_before_token_refresh_starts(self) -> None:
        """The first refresh scan runs in the background as soon as startup services start,
        and a personal action's token URL comes only from the toolset registry."""
        from app.connectors_main import lifespan

        mock_container = _make_container()
        mock_container.data_store = AsyncMock(return_value=_make_data_store())
        mock_app = MagicMock()
        mock_registry = MagicMock()
        mock_registry._connectors = {}
        calls: list[str] = []

        mock_toolset_registry = MagicMock()
        mock_toolset_registry.list_toolsets.return_value = []
        mock_toolset_registry.auto_discover_toolsets.side_effect = lambda: calls.append("toolsets")

        async def _startup(*_args: object, **_kwargs: object) -> None:
            calls.append("refresh services")

        with (
            patch("app.connectors_main.get_initialized_container", new_callable=AsyncMock, return_value=mock_container),
            patch("app.connectors_main.initialize_connector_registry", new_callable=AsyncMock, return_value=mock_registry),
            patch("app.connectors_main.get_toolset_registry", return_value=mock_toolset_registry),
            patch("app.connectors_main.startup_service.initialize", side_effect=_startup),
            patch("app.connectors_main.start_messaging_producer", new_callable=AsyncMock),
            patch("app.connectors_main.resume_sync_services", new_callable=AsyncMock),
            patch("app.connectors_main.start_kafka_consumers", new_callable=AsyncMock, return_value=[]),
            patch("app.connectors_main.shutdown_container_resources", new_callable=AsyncMock),
            patch("os.getenv", side_effect=_mock_os_getenv("neo4j")),
        ):
            async with lifespan(mock_app):
                assert mock_app.state.toolset_registry is mock_toolset_registry

        assert calls == ["toolsets", "refresh services"]

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
            patch("os.getenv", side_effect=_mock_os_getenv("neo4j")),
            patch.dict("sys.modules", {
                "app.agents.registry.toolset_registry": MagicMock(get_toolset_registry=MagicMock(return_value=mock_toolset_registry)),
                "app.agents.tools.registry": MagicMock(_global_tools_registry=mock_tools_registry),
                "app.connectors.core.registry.oauth_config_registry": MagicMock(get_oauth_config_registry=MagicMock(return_value=mock_oauth_registry)),
            }),
        ):
            async with lifespan(mock_app):
                pass  # Startup succeeded despite init failure

    async def test_kafka_consumer_failure_does_not_raise(self):
        """Kafka consumer startup failure is logged but does not prevent startup."""
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
            patch("app.connectors_main.shutdown_container_resources", new_callable=AsyncMock),
            patch("os.getenv", side_effect=_mock_os_getenv("neo4j")),
            patch.dict("sys.modules", {
                "app.agents.registry.toolset_registry": MagicMock(get_toolset_registry=MagicMock(return_value=mock_toolset_registry)),
                "app.agents.tools.registry": MagicMock(_global_tools_registry=mock_tools_registry),
                "app.connectors.core.registry.oauth_config_registry": MagicMock(get_oauth_config_registry=MagicMock(return_value=mock_oauth_registry)),
            }),
        ):
            async with lifespan(mock_app):
                await mock_container.post_startup_task

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
            patch("os.getenv", side_effect=_mock_os_getenv("neo4j")),
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
            patch("os.getenv", side_effect=_mock_os_getenv("neo4j")),
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
            patch("os.getenv", side_effect=_mock_os_getenv("neo4j")),
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

    @pytest.mark.parametrize("host", POISONED_HOSTS)
    async def test_poisoned_host_header_does_not_skip_auth(self, host):
        """A Host header naming an excluded path does not replace the request path."""
        from app.connectors_main import authenticate_requests, app

        request = request_with_host("/api/v1/x", host)
        mock_call_next = AsyncMock(return_value=MagicMock(spec=JSONResponse))
        app.container = MagicMock()

        with patch("app.connectors_main.authMiddleware", new_callable=AsyncMock, return_value=request) as mock_auth:
            await authenticate_requests(request, mock_call_next)

        mock_auth.assert_awaited_once_with(request)

    async def test_health_path_of_a_real_request_skips_auth(self):
        """The exclusion still applies to a real request for /health."""
        from app.connectors_main import authenticate_requests, app

        request = request_with_host("/health")
        mock_call_next = AsyncMock(return_value=MagicMock(spec=JSONResponse))
        app.container = MagicMock()

        with patch("app.connectors_main.authMiddleware", new_callable=AsyncMock) as mock_auth:
            await authenticate_requests(request, mock_call_next)

        mock_auth.assert_not_awaited()
        mock_call_next.assert_awaited_once_with(request)


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

    @pytest.mark.parametrize(
        "running, status_code, status",
        [
            ({"entity": True, "sync": True, "code_graph": True}, 200, "healthy"),
            ({"entity": True, "sync": True, "code_graph": False}, 200, "degraded"),
            ({"entity": True, "sync": False, "code_graph": True}, 503, "fail"),
        ],
    )
    async def test_health_tells_required_from_optional_consumers(self, running, status_code, status) -> None:
        import app.connectors_main as cm

        cm._started_consumers[:] = [
            (name, MagicMock(is_running=MagicMock(return_value=up))) for name, up in running.items()
        ]
        try:
            result = await cm.health_check()
        finally:
            cm._started_consumers.clear()

        body = json.loads(result.body)
        assert result.status_code == status_code
        assert body["status"] == status

    async def test_a_retrying_reconciler_is_degraded_not_down(self) -> None:
        import app.connectors_main as cm

        cm._component_status["code_edge_reconciler"] = {"state": "retrying", "attempts": 2, "error": "boom"}
        try:
            result = await cm.health_check()
        finally:
            cm._component_status.clear()

        body = json.loads(result.body)
        assert result.status_code == 200
        assert body["status"] == "degraded"
        assert body["components"]["code_edge_reconciler"]["attempts"] == 2

    async def test_consumers_that_failed_to_start_fail_health(self) -> None:
        import app.connectors_main as cm

        cm._component_status["message_consumers"] = {"state": "failed", "error": "kafka down"}
        try:
            result = await cm.health_check()
        finally:
            cm._component_status.clear()

        assert result.status_code == 503

    async def test_a_failed_startup_is_unhealthy(self):
        """Coordinator init failing in the background startup task used to leave
        the service answering 200 while it consumed no events at all."""
        from app.connectors_main import app, health_check

        app.state.startup_error = "sync coordinator init failed: boom"
        try:
            result = await health_check()
        finally:
            app.state.startup_error = None

        assert result.status_code == 503


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
            timeout_worker_healthcheck=60,
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
            timeout_worker_healthcheck=60,
        )

    def test_run_defaults_to_the_edition_worker_count(self):
        """workers=None asks the edition seam, not the env var directly.

        The seam may pin the count at one whatever is set, because more than
        one worker needs a cross-process lease the process may not have.
        """
        from app.connectors_main import run
        from app.edition_services import max_connector_workers

        with (
            patch("app.connectors_main.uvicorn.run") as mock_uvicorn,
            patch.dict("os.environ", {"CONNECTOR_UVICORN_WORKERS": "3"}),
        ):
            run(reload=False)
            # Asked inside the patch: the seam may read CONNECTOR_UVICORN_WORKERS,
            # and reading it afterwards would see the restored environment
            # instead of the value run() just used.
            workers = max_connector_workers()

        mock_uvicorn.assert_called_once_with(
            "app.connectors_main:app",
            host="0.0.0.0",
            port=8088,
            log_level="info",
            reload=False,
            workers=workers,
            timeout_worker_healthcheck=60,
        )

    @staticmethod
    def _uvicorn_workers(*, seam, **run_kwargs) -> int:
        from app.connectors_main import run

        with (
            patch("app.connectors_main.uvicorn.run") as mock_uvicorn,
            patch("app.connectors_main.max_connector_workers", **seam),
        ):
            run(**run_kwargs)
        return mock_uvicorn.call_args.kwargs["workers"]

    def test_run_uses_what_the_edition_seam_answers(self):
        assert self._uvicorn_workers(seam={"return_value": 3}, reload=False) == 3

    def test_run_falls_back_to_one_worker_when_the_seam_cannot_parse_its_setting(self):
        assert self._uvicorn_workers(seam={"side_effect": ValueError("abc")}, reload=False) == 1

    def test_run_reload_forces_a_single_worker(self):
        """Matching docling/indexing/parsing's own reload-safety clamp."""
        assert self._uvicorn_workers(seam={"return_value": 4}, reload=True) == 1

    def test_run_explicit_workers_argument_skips_the_seam(self):
        assert self._uvicorn_workers(seam={"side_effect": AssertionError("asked")}, workers=2, reload=False) == 2


# ---------------------------------------------------------------------------
# EXCLUDE_PATHS
# ---------------------------------------------------------------------------
class TestExcludePaths:
    """Tests for EXCLUDE_PATHS configuration."""

    def test_exclude_paths_contains_expected_paths(self):
        """EXCLUDE_PATHS includes all expected paths."""
        from app.connectors_main import EXCLUDE_PATHS

        assert "/health" in EXCLUDE_PATHS
        assert "/health/graph-db" in EXCLUDE_PATHS
        assert "/health/vector-db" in EXCLUDE_PATHS
        assert "/drive/webhook" in EXCLUDE_PATHS
        assert "/gmail/webhook" in EXCLUDE_PATHS
        assert "/admin/webhook" in EXCLUDE_PATHS

    def test_exclude_paths_length(self):
        """EXCLUDE_PATHS has exactly 6 entries."""
        from app.connectors_main import EXCLUDE_PATHS

        assert len(EXCLUDE_PATHS) == 6


# ---------------------------------------------------------------------------
# Health endpoints & metrics (KB refactor adjacent)
# ---------------------------------------------------------------------------
class TestGraphDbHealthCheck:
    @pytest.mark.asyncio
    async def test_graph_db_health_arangodb_healthy(self):
        """Arango health probe returns 200 when DB version call succeeds."""
        from app.connectors_main import graph_db_health_check

        request = MagicMock()
        container = _make_container()
        container.config_service.return_value.get_config = AsyncMock(
            return_value={"username": "root", "password": "secret"}
        )
        container.arango_client = AsyncMock(return_value=MagicMock())
        request.app.container = container

        mock_sys_db = MagicMock()
        with patch("app.connectors_main.os.getenv", return_value="arangodb"), \
             patch("app.connectors_main.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
            mock_thread.side_effect = [mock_sys_db, None]
            response = await graph_db_health_check(request)

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_graph_db_health_arangodb_unhealthy(self):
        """Arango connection failure returns 503."""
        from app.connectors_main import graph_db_health_check

        request = MagicMock()
        container = _make_container()
        container.config_service.return_value.get_config = AsyncMock(
            side_effect=RuntimeError("config down")
        )
        request.app.container = container

        with patch("app.connectors_main.os.getenv", return_value="arangodb"):
            response = await graph_db_health_check(request)

        assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_graph_db_health_neo4j_healthy(self):
        """Neo4j health probe returns 200 when the open driver answers RETURN 1."""
        from app.connectors_main import graph_db_health_check

        request = MagicMock()
        client, session = _client_with_open_driver()
        request.app.state.graph_provider = MagicMock(client=client)

        with patch("app.connectors_main.os.getenv", return_value="neo4j"):
            response = await graph_db_health_check(request)

        assert response.status_code == 200
        session.run.assert_awaited_once_with("RETURN 1 AS n")
        client.connect.assert_not_called()
        client.driver.session.assert_called_once_with(
            database="neo4j",
            connection_acquisition_timeout=2.0,
        )

    @pytest.mark.asyncio
    async def test_graph_db_health_neo4j_no_client(self):
        """Neo4j provider without a client returns 503."""
        from app.connectors_main import graph_db_health_check

        request, _records = _request_with_service_logger()
        request.app.state.graph_provider = MagicMock(client=None)

        with patch("app.connectors_main.os.getenv", return_value="neo4j"):
            response = await graph_db_health_check(request)

        assert response.status_code == 503
        body = json.loads(response.body)
        assert body["error"] == "Neo4j unavailable"

    @pytest.mark.asyncio
    async def test_graph_db_health_neo4j_no_driver_does_not_connect(self):
        """A client with no driver returns 503 and does not open a new one."""
        from app.connectors_main import graph_db_health_check

        request, _records = _request_with_service_logger()
        client = MagicMock()
        client.driver = None
        client.connect = AsyncMock()
        request.app.state.graph_provider = MagicMock(client=client)

        with patch("app.connectors_main.os.getenv", return_value="neo4j"):
            response = await graph_db_health_check(request)

        assert response.status_code == 503
        body = json.loads(response.body)
        assert body["error"] == "Neo4j unavailable"
        client.connect.assert_not_called()

    @pytest.mark.asyncio
    async def test_graph_db_health_neo4j_probe_timeout(self):
        """A probe that exceeds the wait_for budget returns 503, not a hang."""
        from app.connectors_main import graph_db_health_check

        request, records = _request_with_service_logger()
        client, _session = _client_with_open_driver()
        request.app.state.graph_provider = MagicMock(client=client)

        def _expire(awaitable, timeout):
            awaitable.close()
            raise TimeoutError()

        with patch("app.connectors_main.os.getenv", return_value="neo4j"), \
             patch("app.connectors_main.asyncio.wait_for", side_effect=_expire):
            response = await graph_db_health_check(request)

        assert response.status_code == 503
        body = json.loads(response.body)
        assert body["error"] == "Neo4j health check timed out"
        assert "timed out" in records[0].getMessage()

    @pytest.mark.asyncio
    async def test_graph_db_health_unknown_store(self):
        """Unknown DATA_STORE returns healthy with explanatory message."""
        from app.connectors_main import graph_db_health_check

        request = MagicMock()
        with patch("app.connectors_main.os.getenv", return_value="unknown_db"):
            response = await graph_db_health_check(request)

        assert response.status_code == 200
        assert b"not implemented" in response.body


class TestVectorDbHealthCheck:
    @pytest.mark.asyncio
    async def test_vector_db_health_healthy(self):
        """Vector DB health returns 200 when provider reports healthy."""
        from types import SimpleNamespace

        from app.connectors_main import vector_db_health_check
        from app.services.vector_db.models import HealthStatus, VectorDBHealth

        request = MagicMock()
        container = _make_container()
        request.app.container = container
        request.app.state = SimpleNamespace()
        mock_provider = AsyncMock()
        mock_provider.health_check = AsyncMock(return_value=VectorDBHealth(
            status=HealthStatus.HEALTHY,
            server_version="1.0",
            latency_ms=5,
            message="ok",
        ))

        with patch("app.connectors_main.os.getenv", return_value="qdrant"), \
             patch(
                 "app.services.vector_db.vector_db_provider_factory.VectorDBProviderFactory.create_provider",
                 new_callable=AsyncMock,
                 return_value=mock_provider,
             ):
            response = await vector_db_health_check(request)

        assert response.status_code == 200
        assert request.app.state._vector_db_health_provider is mock_provider

    @pytest.mark.asyncio
    async def test_vector_db_health_unhealthy(self):
        """Vector DB unhealthy status returns 503."""
        from types import SimpleNamespace

        from app.connectors_main import vector_db_health_check
        from app.services.vector_db.models import HealthStatus, VectorDBHealth

        request = MagicMock()
        container = _make_container()
        request.app.container = container
        request.app.state = SimpleNamespace()
        mock_provider = AsyncMock()
        mock_provider.health_check = AsyncMock(return_value=VectorDBHealth(
            status=HealthStatus.UNHEALTHY,
            message="down",
        ))
        request.app.state._vector_db_health_provider = mock_provider

        with patch("app.connectors_main.os.getenv", return_value="qdrant"):
            response = await vector_db_health_check(request)

        assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_create_provider_exception(self):
        from types import SimpleNamespace

        from app.connectors_main import vector_db_health_check

        request = MagicMock()
        request.app.container = _make_container()
        request.app.state = SimpleNamespace()

        with patch("app.connectors_main.os.getenv", return_value="qdrant"), \
             patch(
                 "app.services.vector_db.vector_db_provider_factory.VectorDBProviderFactory.create_provider",
                 new_callable=AsyncMock,
                 side_effect=RuntimeError("connect failed"),
             ):
            response = await vector_db_health_check(request)

        assert response.status_code == 503
        assert request.app.state._vector_db_health_provider is None

    @pytest.mark.asyncio
    async def test_reuses_cached_provider(self):
        from types import SimpleNamespace

        from app.connectors_main import vector_db_health_check
        from app.services.vector_db.models import HealthStatus, VectorDBHealth

        request = MagicMock()
        request.app.state = SimpleNamespace()
        mock_provider = AsyncMock()
        mock_provider.health_check = AsyncMock(return_value=VectorDBHealth(
            status=HealthStatus.HEALTHY,
            server_version="1.0",
            latency_ms=1,
            message="ok",
        ))
        request.app.state._vector_db_health_provider = mock_provider

        with patch("app.connectors_main.os.getenv", return_value="qdrant"):
            response = await vector_db_health_check(request)

        assert response.status_code == 200


class TestVectorDbMemoryCheck:
    """/health/vector-db/memory: advisory, authenticated, never fails the probe."""

    def test_route_requires_authentication(self) -> None:
        from app.connectors_main import EXCLUDE_PATHS

        assert "/health/vector-db/memory" not in EXCLUDE_PATHS

    @pytest.mark.asyncio
    async def test_returns_the_report(self) -> None:
        from types import SimpleNamespace

        from app.connectors_main import vector_db_memory_check
        from app.services.vector_db.models import VectorDBMemoryReport

        request = MagicMock()
        request.app.state = SimpleNamespace()
        request.state = SimpleNamespace(user={"role": "admin"})
        provider = MagicMock()
        provider.memory_report = AsyncMock(return_value=VectorDBMemoryReport(
            status="insufficient",
            needed_bytes=12 * 1024**3,
            index_bytes=10 * 1024**3,
            base_bytes=2 * 1024**3,
            available_bytes=4 * 1024**3,
            available_source="configured",
            points=5_000_000,
            nodes=1,
        ))
        request.app.state._vector_db_health_provider = provider

        response = await vector_db_memory_check(request)

        assert response.status_code == 200
        body = json.loads(response.body)
        assert body["status"] == "insufficient"
        assert body["neededBytes"] == 12 * 1024**3
        assert body["availableBytes"] == 4 * 1024**3
        assert body["availableSource"] == "configured"

    @pytest.mark.asyncio
    async def test_creates_and_caches_the_provider(self) -> None:
        from types import SimpleNamespace

        from app.connectors_main import vector_db_memory_check

        request = MagicMock()
        request.app.container = _make_container()
        request.app.state = SimpleNamespace()
        request.state = SimpleNamespace(user={"role": "admin"})
        provider = MagicMock()
        provider.memory_report = AsyncMock(return_value=None)

        with patch(
            "app.services.vector_db.vector_db_provider_factory.VectorDBProviderFactory.create_provider",
            new_callable=AsyncMock,
            return_value=provider,
        ):
            response = await vector_db_memory_check(request)

        assert response.status_code == 200
        assert json.loads(response.body) == {"status": "unsupported"}
        assert request.app.state._vector_db_health_provider is provider

    @pytest.mark.asyncio
    async def test_failure_is_503_and_keeps_the_provider(self) -> None:
        from types import SimpleNamespace

        from app.connectors_main import vector_db_memory_check

        request = MagicMock()
        request.app.container = _make_container()
        request.app.state = SimpleNamespace()
        request.state = SimpleNamespace(user={"role": "admin"})
        provider = MagicMock()
        provider.memory_report = AsyncMock(side_effect=RuntimeError("qdrant down"))
        request.app.state._vector_db_health_provider = provider

        response = await vector_db_memory_check(request)

        assert response.status_code == 503
        assert json.loads(response.body)["status"] == "unknown"
        assert request.app.state._vector_db_health_provider is provider

    @pytest.mark.asyncio
    @pytest.mark.parametrize("user", [None, {}, {"role": "member"}, {"role": "Member"}])
    async def test_non_admins_are_refused_before_the_report(self, user: dict | None) -> None:
        from types import SimpleNamespace

        from app.connectors_main import vector_db_memory_check

        request = MagicMock()
        request.app.state = SimpleNamespace()
        request.state = SimpleNamespace(user=user)
        provider = MagicMock()
        provider.memory_report = AsyncMock()
        request.app.state._vector_db_health_provider = provider

        response = await vector_db_memory_check(request)

        assert response.status_code == 403
        assert json.loads(response.body) == {"detail": "Admin access required"}
        provider.memory_report.assert_not_awaited()


class TestGraphDbHealthNeo4jUnhealthy:
    @pytest.mark.asyncio
    async def test_neo4j_connectivity_failure(self):
        from app.connectors_main import graph_db_health_check

        request = MagicMock()
        client, _session = _client_with_open_driver(
            AsyncMock(side_effect=RuntimeError("neo4j down"))
        )
        request.app.state.graph_provider = MagicMock(client=client)

        with patch("app.connectors_main.os.getenv", return_value="neo4j"):
            response = await graph_db_health_check(request)

        assert response.status_code == 503


SENTINEL = "SENTINEL bolt://neo4j:hunter2@10.0.0.5:7687"


class _FakeNeo4jAuthError(Exception):
    pass


class _FakeNeo4jServiceUnavailable(Exception):
    pass


class _Records(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _request_with_service_logger() -> tuple[MagicMock, list[logging.LogRecord]]:
    """A request whose container logger is built like ``create_logger``'s: its own
    handler and nothing propagated, so a line sent to any other logger is not counted."""
    handler = _Records()
    service_logger = logging.Logger("connector_service")
    service_logger.propagate = False
    service_logger.addHandler(handler)
    request = MagicMock()
    request.app.container = _make_container()
    request.app.container.logger.return_value = service_logger
    return request, handler.records


def _traceback_text(records: list[logging.LogRecord]) -> str:
    """Text of the exception the one ERROR line carries as a traceback."""
    assert [record.levelno for record in records] == [logging.ERROR]
    return str(records[0].exc_info[1])


def _line_without_traceback(records: list[logging.LogRecord], level: int) -> str:
    assert [record.levelno for record in records] == [level]
    assert records[0].exc_info is None
    return records[0].getMessage()


class TestHealthProbesReturnFixedText:
    """These routes skip authentication, so what the driver said goes to the service log only."""

    async def test_arangodb_failure(self):
        from app.connectors_main import graph_db_health_check

        request, records = _request_with_service_logger()
        request.app.container.config_service.return_value.get_config = AsyncMock(
            side_effect=RuntimeError(SENTINEL)
        )

        with patch("app.connectors_main.os.getenv", return_value="arangodb"):
            response = await graph_db_health_check(request)

        assert response.status_code == 503
        body = json.loads(response.body)
        assert body["status"] == "unhealthy"
        assert body["error"] == "ArangoDB health check failed"
        assert "SENTINEL" not in response.body.decode()
        assert _traceback_text(records) == SENTINEL

    @pytest.mark.parametrize(
        ("error_type", "expected"),
        [
            (_FakeNeo4jAuthError, "Neo4j auth failed"),
            (_FakeNeo4jServiceUnavailable, "Neo4j unavailable"),
            (RuntimeError, "Neo4j health check failed"),
        ],
    )
    async def test_neo4j_failure(self, error_type: type[Exception], expected: str):
        """An outage is polled on every /health call: one line for it, a traceback only for the unexpected."""
        from app.connectors_main import graph_db_health_check

        request, records = _request_with_service_logger()
        client, _session = _client_with_open_driver(
            AsyncMock(side_effect=error_type(SENTINEL))
        )
        driver = client.driver
        request.app.state.graph_provider = MagicMock(client=client)
        neo4j_exceptions = types.ModuleType("neo4j.exceptions")
        neo4j_exceptions.AuthError = _FakeNeo4jAuthError
        neo4j_exceptions.ServiceUnavailable = _FakeNeo4jServiceUnavailable

        with patch("app.connectors_main.os.getenv", return_value="neo4j"), \
             patch.dict("sys.modules", {"neo4j.exceptions": neo4j_exceptions}):
            response = await graph_db_health_check(request)

        assert response.status_code == 503
        body = json.loads(response.body)
        assert body["status"] == "unhealthy"
        assert body["error"] == expected
        assert "SENTINEL" not in response.body.decode()
        if error_type is RuntimeError:
            assert _traceback_text(records) == SENTINEL
        else:
            assert _line_without_traceback(records, logging.ERROR) == f"{expected}: {SENTINEL}"
        assert client.driver is driver
        client.connect.assert_not_called()

    async def test_vector_db_exception(self):
        from types import SimpleNamespace

        from app.connectors_main import vector_db_health_check

        request, records = _request_with_service_logger()
        request.app.state = SimpleNamespace()

        with patch("app.connectors_main.os.getenv", return_value="qdrant"), \
             patch(
                 "app.services.vector_db.vector_db_provider_factory.VectorDBProviderFactory.create_provider",
                 new_callable=AsyncMock,
                 side_effect=RuntimeError(SENTINEL),
             ):
            response = await vector_db_health_check(request)

        assert response.status_code == 503
        body = json.loads(response.body)
        assert body["provider"] == "qdrant"
        assert body["error"] == "Vector DB (qdrant) health check failed"
        assert "SENTINEL" not in response.body.decode()
        assert _traceback_text(records) == SENTINEL

    async def test_vector_db_unhealthy_result_message(self):
        """Providers put ``str(e)`` in ``VectorDBHealth.message``."""
        from types import SimpleNamespace

        from app.connectors_main import vector_db_health_check
        from app.services.vector_db.models import HealthStatus, VectorDBHealth

        request, records = _request_with_service_logger()
        request.app.state = SimpleNamespace()
        mock_provider = AsyncMock()
        mock_provider.health_check = AsyncMock(return_value=VectorDBHealth(
            status=HealthStatus.UNHEALTHY,
            message=SENTINEL,
        ))
        request.app.state._vector_db_health_provider = mock_provider

        with patch("app.connectors_main.os.getenv", return_value="qdrant"):
            response = await vector_db_health_check(request)

        assert response.status_code == 503
        body = json.loads(response.body)
        assert body["provider"] == "qdrant"
        assert body["error"] == "qdrant health check failed"
        assert "SENTINEL" not in response.body.decode()
        assert SENTINEL in _line_without_traceback(records, logging.WARNING)


class TestRefreshConnectorMetrics:
    @pytest.mark.asyncio
    async def test_refresh_connector_metrics_updates_gauge(self):
        """Metrics refresh counts only active connector apps by type."""
        from app.connectors_main import refresh_connector_metrics
        from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

        apps = [
            {"isActive": True, "type": "KB"},
            {"isActive": False, "type": "KB"},
            {"isActive": True, "type": "DRIVE"},
        ]

        async def nodes_by_filters(collection, filters, return_fields=None) -> list[dict]:
            return [a for a in apps if all(a.get(k) == v for k, v in filters.items())]

        # The count is the provider's: this one counts the way a backend without
        # a query of its own does.
        gp = AsyncMock()
        gp.get_nodes_by_filters = AsyncMock(side_effect=nodes_by_filters)
        gp.count_active_apps_by_type = lambda: IGraphDBProvider.count_active_apps_by_type(gp)
        logger = MagicMock()

        with patch("app.connectors_main.set_connector_active") as mock_set, \
             patch("app.connectors_main.asyncio.sleep", new_callable=AsyncMock, side_effect=asyncio.CancelledError):
            with pytest.raises(asyncio.CancelledError):
                await refresh_connector_metrics(gp, logger, interval_s=60)

        mock_set.assert_called_once_with({"KB": 1, "DRIVE": 1})
        gp.get_all_documents.assert_not_called()

    @pytest.mark.asyncio
    async def test_logs_warning_when_gauge_refresh_fails(self):
        from app.connectors_main import refresh_connector_metrics

        gp = AsyncMock()
        gp.count_active_apps_by_type = AsyncMock(side_effect=RuntimeError("graph down"))
        logger = MagicMock()

        with patch("app.connectors_main.set_connector_active") as mock_set, \
             patch("app.connectors_main.asyncio.sleep", new_callable=AsyncMock, side_effect=asyncio.CancelledError):
            with pytest.raises(asyncio.CancelledError):
                await refresh_connector_metrics(gp, logger, interval_s=60)

        logger.warning.assert_called()
        mock_set.assert_not_called()


class TestCorsPolicy:
    """Browsers reach this service only through the Node API, so it must not grant CORS."""

    def test_no_cors_middleware(self) -> None:
        from starlette.middleware.cors import CORSMiddleware

        from app.connectors_main import app
        assert all(m.cls is not CORSMiddleware for m in app.user_middleware)

    def test_cross_origin_preflight_is_not_granted(self) -> None:
        from fastapi.testclient import TestClient

        from app.connectors_main import app
        evil = "https://evil.example"
        response = TestClient(app, raise_server_exceptions=False).options(
            "/api/v1/chat",
            headers={
                "Origin": evil,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert response.headers.get("access-control-allow-origin") is None
        assert response.headers.get("access-control-allow-credentials") is None
