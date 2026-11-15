"""Unit tests for app.query_main — Query service FastAPI entrypoint."""

import asyncio
import json
import logging
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.modules.parsers import parse_pool
from app.services.messaging.config import MessageBrokerType
from tests.support.host_header import POISONED_HOSTS, request_with_host

@pytest.fixture(autouse=True)
def _no_inherited_worker_healthcheck_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UVICORN_WORKER_HEALTHCHECK_TIMEOUT_SECONDS", raising=False)



# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_container():
    """Build a mock QueryAppContainer with commonly-used attributes."""
    c = MagicMock()
    c.logger.return_value = MagicMock()
    c.wire = MagicMock()
    mock_config = MagicMock()
    mock_config.get_config = AsyncMock(return_value={
        "connectors": {"endpoint": "http://connector:8088"}
    })
    mock_config.close = AsyncMock()
    c.config_service.return_value = mock_config
    return c


def _make_graph_provider(orgs=None):
    """Return an async-ready mock graph provider."""
    gp = AsyncMock()
    gp.get_all_orgs = AsyncMock(return_value=orgs or [])
    return gp


# ===========================================================================
# initialize_container
# ===========================================================================


class TestInitializeContainer:
    """Tests for the initialize_container coroutine."""

    async def test_success_returns_true(self):
        """Successful initialisation returns True."""
        from app.query_main import initialize_container

        container = _make_container()
        gp = _make_graph_provider()
        container.graph_provider = AsyncMock(return_value=gp)

        with patch("app.query_main.Health") as MockHealth:
            MockHealth.health_check_connector_service = AsyncMock()
            result = await initialize_container(container)

        assert result is True
        assert container._graph_provider is gp
        MockHealth.health_check_connector_service.assert_awaited_once_with(container)

    async def test_graph_provider_none_raises(self):
        """When graph_provider resolves to None, an Exception is raised."""
        from app.query_main import initialize_container

        container = _make_container()
        container.graph_provider = AsyncMock(return_value=None)

        with patch("app.query_main.Health") as MockHealth:
            MockHealth.health_check_connector_service = AsyncMock()
            with pytest.raises(Exception, match="Failed to initialize Graph Database Provider"):
                await initialize_container(container)

    async def test_health_check_failure_propagates(self):
        """If Health.health_check_connector_service raises, it propagates."""
        from app.query_main import initialize_container

        container = _make_container()

        with patch("app.query_main.Health") as MockHealth:
            MockHealth.health_check_connector_service = AsyncMock(
                side_effect=Exception("connector down")
            )
            with pytest.raises(Exception, match="connector down"):
                await initialize_container(container)


# ===========================================================================
# get_initialized_container
# ===========================================================================


class TestGetInitializedContainer:
    """Tests for the get_initialized_container dependency provider."""

    async def test_first_call_initializes(self):
        """On first call, initialize_container is invoked and .initialized is set."""
        from app.query_main import get_initialized_container

        # Clean up the attribute from any previous test run
        if hasattr(get_initialized_container, "initialized"):
            del get_initialized_container.initialized

        mock_container = _make_container()
        gp = _make_graph_provider()
        mock_container.graph_provider = AsyncMock(return_value=gp)

        with (
            patch("app.query_main.container", mock_container),
            patch("app.query_main.initialize_container", new_callable=AsyncMock) as mock_init,
        ):
            result = await get_initialized_container()

        assert result is mock_container
        mock_init.assert_awaited_once_with(mock_container)
        mock_container.wire.assert_called_once()
        assert get_initialized_container.initialized is True

        # Cleanup
        del get_initialized_container.initialized

    async def test_second_call_skips_init(self):
        """When .initialized is already set, initialize_container is NOT called."""
        from app.query_main import get_initialized_container

        if hasattr(get_initialized_container, "initialized"):
            del get_initialized_container.initialized

        mock_container = _make_container()
        gp = _make_graph_provider()
        mock_container.graph_provider = AsyncMock(return_value=gp)

        with (
            patch("app.query_main.container", mock_container),
            patch("app.query_main.initialize_container", new_callable=AsyncMock) as mock_init,
        ):
            # First call — initialises
            await get_initialized_container()
            mock_init.reset_mock()
            # Second call — skips init
            result = await get_initialized_container()

        mock_init.assert_not_awaited()
        assert result is mock_container

        # Cleanup
        del get_initialized_container.initialized


# ===========================================================================
# start_kafka_consumers
# ===========================================================================


class TestStartKafkaConsumers:
    """Tests for start_kafka_consumers."""

    async def test_success_starts_consumer(self):
        """Consumer is created, started, and returned in list."""
        from app.query_main import start_kafka_consumers

        container = _make_container()
        mock_consumer = AsyncMock()
        mock_consumer.start = AsyncMock()

        with (
            patch("app.query_main.get_message_broker_type", return_value=MessageBrokerType.KAFKA),
            patch("app.query_main.MessagingUtils._get_redis_config", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.query_main.MessagingFactory.create_retry_manager", return_value=MagicMock(initialize=AsyncMock())),
            patch("app.query_main.MessagingUtils.create_aiconfig_consumer_config", new_callable=AsyncMock, return_value={}),
            patch("app.query_main.KafkaUtils.create_aiconfig_message_handler", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.query_main.MessagingFactory.create_consumer", return_value=mock_consumer),
        ):
            consumers = await start_kafka_consumers(container)

        assert len(consumers) == 1
        assert consumers[0][0] == "aiconfig"
        assert consumers[0][1] is mock_consumer
        mock_consumer.start.assert_awaited_once()

    async def test_error_cleans_up_started_consumers(self):
        """On failure after partial start, already-started consumers are stopped."""
        from app.query_main import start_kafka_consumers

        container = _make_container()
        mock_consumer = AsyncMock()
        mock_consumer.start = AsyncMock(side_effect=Exception("kafka error"))

        with (
            patch("app.query_main.get_message_broker_type", return_value=MessageBrokerType.KAFKA),
            patch("app.query_main.MessagingUtils._get_redis_config", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.query_main.MessagingFactory.create_retry_manager", return_value=MagicMock(initialize=AsyncMock())),
            patch("app.query_main.MessagingUtils.create_aiconfig_consumer_config", new_callable=AsyncMock, return_value={}),
            patch("app.query_main.KafkaUtils.create_aiconfig_message_handler", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.query_main.MessagingFactory.create_consumer", return_value=mock_consumer),
        ):
            with pytest.raises(Exception, match="kafka error"):
                await start_kafka_consumers(container)

    async def test_error_after_append_triggers_cleanup(self):
        """When an error occurs after consumer is appended, cleanup stops it."""
        from app.query_main import start_kafka_consumers

        container = _make_container()
        logger = container.logger()

        mock_consumer = AsyncMock()
        mock_consumer.start = AsyncMock()
        mock_consumer.stop = AsyncMock()

        def info_side_effect(msg, *args, **kwargs):
            if "AI Config consumer started" in str(msg):
                raise RuntimeError("log explosion")

        logger.info = MagicMock(side_effect=info_side_effect)

        with (
            patch("app.query_main.get_message_broker_type", return_value=MessageBrokerType.KAFKA),
            patch("app.query_main.MessagingUtils._get_redis_config", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.query_main.MessagingFactory.create_retry_manager", return_value=MagicMock(initialize=AsyncMock())),
            patch("app.query_main.MessagingUtils.create_aiconfig_consumer_config", new_callable=AsyncMock, return_value={}),
            patch("app.query_main.KafkaUtils.create_aiconfig_message_handler", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.query_main.MessagingFactory.create_consumer", return_value=mock_consumer),
        ):
            with pytest.raises(RuntimeError, match="log explosion"):
                await start_kafka_consumers(container)

        # The consumer was appended before the error, so cleanup should have stopped it
        mock_consumer.stop.assert_awaited_once()

    async def test_cleanup_stop_error_logged(self):
        """If cleanup stop itself fails, the error is logged and original re-raised."""
        from app.query_main import start_kafka_consumers

        container = _make_container()
        logger = container.logger()

        mock_consumer = AsyncMock()
        mock_consumer.start = AsyncMock()
        mock_consumer.stop = AsyncMock(side_effect=Exception("stop failed"))

        def info_side_effect(msg, *args, **kwargs):
            if "AI Config consumer started" in str(msg):
                raise RuntimeError("post-append error")

        logger.info = MagicMock(side_effect=info_side_effect)

        with (
            patch("app.query_main.get_message_broker_type", return_value=MessageBrokerType.KAFKA),
            patch("app.query_main.MessagingUtils._get_redis_config", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.query_main.MessagingFactory.create_retry_manager", return_value=MagicMock(initialize=AsyncMock())),
            patch("app.query_main.MessagingUtils.create_aiconfig_consumer_config", new_callable=AsyncMock, return_value={}),
            patch("app.query_main.KafkaUtils.create_aiconfig_message_handler", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.query_main.MessagingFactory.create_consumer", return_value=mock_consumer),
        ):
            with pytest.raises(RuntimeError, match="post-append error"):
                await start_kafka_consumers(container)

        # Stop was attempted but failed — error was logged
        mock_consumer.stop.assert_awaited_once()
        # The cleanup error should be logged
        logger.error.assert_any_call(
            "Error stopping aiconfig consumer during cleanup: stop failed"
        )

    async def test_retry_manager_created_for_redis_broker(self):
        """RetryManager is created and initialized for Redis broker."""
        from app.query_main import start_kafka_consumers

        container = _make_container()
        mock_consumer = AsyncMock()
        mock_consumer.start = AsyncMock()
        mock_retry_manager = AsyncMock()
        mock_retry_manager.initialize = AsyncMock()

        with (
            patch("app.query_main.get_message_broker_type", return_value=MessageBrokerType.REDIS),
            patch("app.query_main.MessagingUtils._get_redis_config", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.query_main.MessagingFactory.create_retry_manager", return_value=mock_retry_manager) as mock_create_rm,
            patch("app.query_main.MessagingUtils.create_aiconfig_consumer_config", new_callable=AsyncMock, return_value={}),
            patch("app.query_main.KafkaUtils") as MockKU,
            patch("app.query_main.MessagingFactory.create_consumer", return_value=mock_consumer) as mock_create_consumer,
        ):
            MockKU.create_aiconfig_message_handler = AsyncMock(return_value=MagicMock())

            consumers = await start_kafka_consumers(container)

        # Assert RetryManager was created and initialized
        mock_create_rm.assert_called_once()
        mock_retry_manager.initialize.assert_awaited_once()

        # Assert consumer was created with retry_manager
        mock_create_consumer.assert_called_once()
        call_kwargs = mock_create_consumer.call_args.kwargs
        assert call_kwargs["retry_manager"] is mock_retry_manager

        assert len(consumers) == 1
        assert consumers[0][0] == "aiconfig"


# ===========================================================================
# stop_kafka_consumers
# ===========================================================================


class TestStopKafkaConsumers:
    """Tests for stop_kafka_consumers."""

    async def test_stop_success(self):
        """Consumer stops successfully, returns True."""
        from app.query_main import stop_kafka_consumers

        consumer = AsyncMock()
        consumer.stop = AsyncMock()
        container = _make_container()
        container.kafka_consumers = [("aiconfig", consumer)]

        result = await stop_kafka_consumers(container)
        assert result is True
        consumer.stop.assert_awaited_once()

    async def test_stop_error_returns_true_from_finally(self):
        """When consumer.stop raises, the except block returns False."""
        from app.query_main import stop_kafka_consumers

        consumer = AsyncMock()
        consumer.stop = AsyncMock(side_effect=Exception("stop fail"))
        container = _make_container()
        container.kafka_consumers = [("aiconfig", consumer)]

        result = await stop_kafka_consumers(container)
        # The except block returns False; the finally block clears the list
        assert result is False
        assert container.kafka_consumers == []

    async def test_no_consumers_attribute(self):
        """When container has no kafka_consumers attr, loop body never runs."""
        from app.query_main import stop_kafka_consumers

        container = _make_container()
        # Explicitly remove kafka_consumers if present
        if hasattr(container, "kafka_consumers"):
            delattr(container, "kafka_consumers")
        # getattr with default [] -> empty list
        result = await stop_kafka_consumers(container)
        assert result is None  # No iteration occurs, function returns None

    async def test_empty_consumers_list(self):
        """Empty consumers list — loop body never runs."""
        from app.query_main import stop_kafka_consumers

        container = _make_container()
        container.kafka_consumers = []

        result = await stop_kafka_consumers(container)
        assert result is None


# ===========================================================================
# lifespan
# ===========================================================================


class TestLifespan:
    """Tests for the lifespan async context manager."""

    async def test_startup_and_shutdown_normal(self):
        """Full startup-yield-shutdown with organisations found."""
        from app.query_main import lifespan

        mock_container = _make_container()
        gp = _make_graph_provider(orgs=["org1"])
        mock_container._graph_provider = gp

        mock_retrieval = AsyncMock()
        mock_retrieval.get_embedding_model_instance = AsyncMock()

        mock_toolset_registry = MagicMock()
        mock_toolset_registry.list_toolsets.return_value = ["t1"]
        mock_tools_registry = MagicMock()
        mock_tools_registry.list_tools.return_value = ["tool1", "tool2"]

        mock_app = MagicMock()
        mock_app.container = None
        mock_app.state = MagicMock()

        mock_config_service = MagicMock()
        mock_config_service.close = AsyncMock()

        with (
            patch("app.query_main.get_initialized_container", new_callable=AsyncMock, return_value=mock_container),
            patch("app.query_main.start_kafka_consumers", new_callable=AsyncMock, return_value=[("aiconfig", AsyncMock())]),
            patch("app.query_main.stop_kafka_consumers", new_callable=AsyncMock),
            patch("app.query_main.container", mock_container),
            patch("app.query_main.get_toolset_registry", return_value=mock_toolset_registry, create=True),
            patch("app.query_main._global_tools_registry", mock_tools_registry, create=True),
        ):
            mock_container.retrieval_service = AsyncMock(return_value=mock_retrieval)
            mock_container.config_service.return_value = mock_config_service

            # We need to mock the in-lifespan imports
            with (
                patch.dict("sys.modules", {
                    "app.agents.registry.toolset_registry": MagicMock(get_toolset_registry=MagicMock(return_value=mock_toolset_registry)),
                    "app.agents.tools.registry": MagicMock(_global_tools_registry=mock_tools_registry),
                }),
            ):
                async with lifespan(mock_app):
                    # Inside lifespan — app should have container set
                    assert mock_app.container is mock_container
                    # Chat attachment PDFs parse in killable worker processes.
                    assert parse_pool.parse_pool_workers() > 0

            # After shutdown, config_service.close should be called
            mock_config_service.close.assert_awaited_once()
            assert parse_pool.parse_pool_workers() == 0

    async def test_startup_no_orgs(self):
        """When no orgs found, retrieval_service is NOT invoked."""
        from app.query_main import lifespan

        mock_container = _make_container()
        gp = _make_graph_provider(orgs=[])
        mock_container._graph_provider = gp

        mock_toolset_registry = MagicMock()
        mock_toolset_registry.list_toolsets.return_value = []
        mock_tools_registry = MagicMock()
        mock_tools_registry.list_tools.return_value = []

        mock_app = MagicMock()
        mock_app.state = MagicMock()

        mock_config_service = MagicMock()
        mock_config_service.close = AsyncMock()

        with (
            patch("app.query_main.get_initialized_container", new_callable=AsyncMock, return_value=mock_container),
            patch("app.query_main.start_kafka_consumers", new_callable=AsyncMock, return_value=[]),
            patch("app.query_main.stop_kafka_consumers", new_callable=AsyncMock),
            patch("app.query_main.container", mock_container),
            patch.dict("sys.modules", {
                "app.agents.registry.toolset_registry": MagicMock(get_toolset_registry=MagicMock(return_value=mock_toolset_registry)),
                "app.agents.tools.registry": MagicMock(_global_tools_registry=mock_tools_registry),
            }),
        ):
            mock_container.config_service.return_value = mock_config_service
            mock_container.retrieval_service = AsyncMock()

            async with lifespan(mock_app):
                pass

            # retrieval_service should NOT have been awaited (no orgs)
            mock_container.retrieval_service.assert_not_awaited()

    async def test_startup_lists_orgs_in_a_system_scope_that_ends_after_the_list(self) -> None:
        """The PostgreSQL graph refuses an org list with no context (the service then could not start); the
        scope must end with the list, or every task started after it would inherit a cross-org context."""
        from app.core import org_context
        from app.query_main import lifespan

        mock_container = _make_container()
        seen: list = []

        async def _orgs(*_a: object, **_k: object) -> list:
            seen.append(org_context.peek())
            return []

        gp = _make_graph_provider()
        gp.get_all_orgs = AsyncMock(side_effect=_orgs)
        mock_container._graph_provider = gp
        mock_registry = MagicMock()
        mock_registry.list_toolsets.return_value = []
        mock_config_service = MagicMock()
        mock_config_service.close = AsyncMock()

        with (
            patch("app.query_main.get_initialized_container", new_callable=AsyncMock, return_value=mock_container),
            patch("app.query_main.start_kafka_consumers", new_callable=AsyncMock, return_value=[]),
            patch("app.query_main.stop_kafka_consumers", new_callable=AsyncMock),
            patch("app.query_main.container", mock_container),
            patch.dict("sys.modules", {
                "app.agents.registry.toolset_registry": MagicMock(get_toolset_registry=MagicMock(return_value=mock_registry)),
                "app.agents.tools.registry": MagicMock(_global_tools_registry=MagicMock()),
            }),
        ):
            mock_container.config_service.return_value = mock_config_service
            async with lifespan(MagicMock()):
                assert org_context.peek() is None

        assert len(seen) == 1 and seen[0] is not None
        assert (seen[0].kind, seen[0].source) == ("system", "system:query_startup")

    async def test_startup_seed_task_starts_with_no_org_context(self) -> None:
        """With orgs present the seeding task is started after the list; it must not inherit the list's cross-org scope."""
        from types import SimpleNamespace

        from app.core import org_context
        from app.query_main import lifespan

        mock_container = _make_container()
        mock_container._graph_provider = _make_graph_provider(orgs=[{"_key": "org-1"}])
        mock_container.retrieval_service = AsyncMock(return_value=AsyncMock())
        mock_registry = MagicMock()
        mock_registry.list_toolsets.return_value = []
        mock_config_service = MagicMock()
        mock_config_service.close = AsyncMock()
        mock_container.config_service.return_value = mock_config_service
        seen: list = []

        async def _seed(*_a: object, **_k: object) -> None:
            seen.append(org_context.peek())

        app = MagicMock()
        app.state = SimpleNamespace()
        with (
            patch("app.query_main.get_initialized_container", new_callable=AsyncMock, return_value=mock_container),
            patch("app.query_main.start_kafka_consumers", new_callable=AsyncMock, return_value=[]),
            patch("app.query_main.stop_kafka_consumers", new_callable=AsyncMock),
            patch("app.query_main.container", mock_container),
            patch("app.agents.agent_loop.skills_wiring.skills_enabled", return_value=True),
            patch("app.services.featureflag.platform_settings.is_skills_enabled", new_callable=AsyncMock, return_value=True),
            patch("app.agents.agent_loop.skills.manager_factory.reconcile_builtin_skills_for_orgs", side_effect=_seed),
            patch.dict("sys.modules", {
                "app.agents.registry.toolset_registry": MagicMock(get_toolset_registry=MagicMock(return_value=mock_registry)),
                "app.agents.tools.registry": MagicMock(_global_tools_registry=MagicMock()),
            }),
        ):
            async with lifespan(app):
                await app.state.skills_seed_task

        assert seen == [None]

    async def test_startup_graph_provider_fallback(self):
        """When _graph_provider is not set, lifespan falls back to await graph_provider()."""
        from app.query_main import lifespan

        mock_container = _make_container()
        gp = _make_graph_provider(orgs=[])
        # Remove _graph_provider so the fallback path is triggered
        mock_container._graph_provider = None
        mock_container.graph_provider = AsyncMock(return_value=gp)

        mock_toolset_registry = MagicMock()
        mock_toolset_registry.list_toolsets.return_value = []
        mock_tools_registry = MagicMock()
        mock_tools_registry.list_tools.return_value = []

        mock_app = MagicMock()
        mock_app.state = MagicMock()

        mock_config_service = MagicMock()
        mock_config_service.close = AsyncMock()

        with (
            patch("app.query_main.get_initialized_container", new_callable=AsyncMock, return_value=mock_container),
            patch("app.query_main.start_kafka_consumers", new_callable=AsyncMock, return_value=[]),
            patch("app.query_main.stop_kafka_consumers", new_callable=AsyncMock),
            patch("app.query_main.container", mock_container),
            patch.dict("sys.modules", {
                "app.agents.registry.toolset_registry": MagicMock(get_toolset_registry=MagicMock(return_value=mock_toolset_registry)),
                "app.agents.tools.registry": MagicMock(_global_tools_registry=mock_tools_registry),
            }),
        ):
            mock_container.config_service.return_value = mock_config_service

            async with lifespan(mock_app):
                assert mock_app.state.graph_provider is gp

    async def test_kafka_start_failure_raises(self):
        """If start_kafka_consumers raises, lifespan propagates the error."""
        from app.query_main import lifespan

        mock_container = _make_container()
        gp = _make_graph_provider()
        mock_container._graph_provider = gp

        mock_app = MagicMock()
        mock_app.state = MagicMock()

        with (
            patch("app.query_main.get_initialized_container", new_callable=AsyncMock, return_value=mock_container),
            patch("app.query_main.start_kafka_consumers", new_callable=AsyncMock, side_effect=Exception("kafka fail")),
        ):
            with pytest.raises(Exception, match="kafka fail"):
                async with lifespan(mock_app):
                    pass

    async def test_shutdown_stop_consumers_error_logged(self):
        """If stop_kafka_consumers raises during shutdown, error is logged but no re-raise."""
        from app.query_main import lifespan

        mock_container = _make_container()
        gp = _make_graph_provider(orgs=[])
        mock_container._graph_provider = gp

        mock_toolset_registry = MagicMock()
        mock_toolset_registry.list_toolsets.return_value = []
        mock_tools_registry = MagicMock()
        mock_tools_registry.list_tools.return_value = []

        mock_app = MagicMock()
        mock_app.state = MagicMock()

        mock_config_service = MagicMock()
        mock_config_service.close = AsyncMock()

        with (
            patch("app.query_main.get_initialized_container", new_callable=AsyncMock, return_value=mock_container),
            patch("app.query_main.start_kafka_consumers", new_callable=AsyncMock, return_value=[]),
            patch("app.query_main.stop_kafka_consumers", new_callable=AsyncMock, side_effect=Exception("stop err")),
            patch("app.query_main.container", mock_container),
            patch.dict("sys.modules", {
                "app.agents.registry.toolset_registry": MagicMock(get_toolset_registry=MagicMock(return_value=mock_toolset_registry)),
                "app.agents.tools.registry": MagicMock(_global_tools_registry=mock_tools_registry),
            }),
        ):
            mock_container.config_service.return_value = mock_config_service

            # Should NOT raise even though stop_kafka_consumers raises
            async with lifespan(mock_app):
                pass

    async def test_shutdown_config_close_error_logged(self):
        """If config_service.close raises during shutdown, error is logged but no re-raise."""
        from app.query_main import lifespan

        mock_container = _make_container()
        gp = _make_graph_provider(orgs=[])
        mock_container._graph_provider = gp

        mock_toolset_registry = MagicMock()
        mock_toolset_registry.list_toolsets.return_value = []
        mock_tools_registry = MagicMock()
        mock_tools_registry.list_tools.return_value = []

        mock_app = MagicMock()
        mock_app.state = MagicMock()

        mock_config_service = MagicMock()
        mock_config_service.close = AsyncMock(side_effect=Exception("config close err"))

        with (
            patch("app.query_main.get_initialized_container", new_callable=AsyncMock, return_value=mock_container),
            patch("app.query_main.start_kafka_consumers", new_callable=AsyncMock, return_value=[]),
            patch("app.query_main.stop_kafka_consumers", new_callable=AsyncMock),
            patch("app.query_main.container", mock_container),
            patch.dict("sys.modules", {
                "app.agents.registry.toolset_registry": MagicMock(get_toolset_registry=MagicMock(return_value=mock_toolset_registry)),
                "app.agents.tools.registry": MagicMock(_global_tools_registry=mock_tools_registry),
            }),
        ):
            mock_container.config_service.return_value = mock_config_service

            # Should NOT raise
            async with lifespan(mock_app):
                pass


# ===========================================================================
# authenticate_requests middleware
# ===========================================================================


class TestAuthenticateRequests:
    """Tests for the authenticate_requests HTTP middleware."""

    async def test_excluded_path_skips_auth(self):
        """Requests to /health bypass authentication entirely."""
        from app.query_main import authenticate_requests

        mock_request = MagicMock()
        mock_request.url.path = "/health"
        mock_response = MagicMock()
        call_next = AsyncMock(return_value=mock_response)

        result = await authenticate_requests(mock_request, call_next)
        assert result is mock_response
        call_next.assert_awaited_once_with(mock_request)

    async def test_authenticated_request_succeeds(self):
        """Successful authMiddleware passes request to call_next."""
        from app.query_main import authenticate_requests

        mock_request = MagicMock()
        mock_request.url.path = "/api/v1/search"
        authed_request = MagicMock()
        mock_response = MagicMock()
        call_next = AsyncMock(return_value=mock_response)

        with patch("app.query_main.authMiddleware", new_callable=AsyncMock, return_value=authed_request):
            result = await authenticate_requests(mock_request, call_next)

        assert result is mock_response
        call_next.assert_awaited_once_with(authed_request)

    async def test_http_exception_returns_error_json(self):
        """HTTPException from authMiddleware returns proper JSON error."""
        from app.query_main import authenticate_requests

        mock_request = MagicMock()
        mock_request.url.path = "/api/v1/search"
        call_next = AsyncMock()

        refusal = HTTPException(status_code=401, detail="Unauthorized")
        with (
            patch("app.query_main.authMiddleware", new_callable=AsyncMock, side_effect=refusal),
            patch("app.query_main.mark_request_failure") as hand_over,
        ):
            result = await authenticate_requests(mock_request, call_next)

        assert isinstance(result, JSONResponse)
        assert result.status_code == 401
        hand_over.assert_called_once_with(mock_request, refusal)
        call_next.assert_not_awaited()

    async def test_unexpected_exception_propagates_to_the_request_line(self) -> None:
        """Not caught here: the request's http.request.end logs it once and answers the 500."""
        from app.query_main import authenticate_requests

        mock_request = MagicMock()
        mock_request.url.path = "/api/v1/search"
        call_next = AsyncMock()

        with patch(
            "app.query_main.authMiddleware",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ):
            with pytest.raises(RuntimeError, match="boom"):
                await authenticate_requests(mock_request, call_next)

    async def test_route_exception_propagates_to_the_request_line(self) -> None:
        from app.query_main import authenticate_requests

        mock_request = MagicMock()
        mock_request.url.path = "/api/v1/search"
        call_next = AsyncMock(side_effect=RuntimeError("route exploded"))

        with patch("app.query_main.authMiddleware", new_callable=AsyncMock, return_value=mock_request):
            with pytest.raises(RuntimeError, match="route exploded"):
                await authenticate_requests(mock_request, call_next)

    @pytest.mark.parametrize("host", POISONED_HOSTS)
    async def test_poisoned_host_header_does_not_skip_auth(self, host):
        """A Host header naming an excluded path does not replace the request path."""
        from app.query_main import authenticate_requests

        request = request_with_host("/api/v1/x", host)
        call_next = AsyncMock()

        with patch("app.query_main.authMiddleware", new_callable=AsyncMock, return_value=request) as auth:
            await authenticate_requests(request, call_next)

        auth.assert_awaited_once_with(request)

    async def test_health_path_of_a_real_request_skips_auth(self):
        """The exclusion still applies to a real request for /health."""
        from app.query_main import authenticate_requests

        request = request_with_host("/health")
        call_next = AsyncMock()

        with patch("app.query_main.authMiddleware", new_callable=AsyncMock) as auth:
            await authenticate_requests(request, call_next)

        auth.assert_not_awaited()
        call_next.assert_awaited_once_with(request)


# ===========================================================================
# health_check
# ===========================================================================


class TestHealthCheck:
    """Tests for the /health endpoint handler."""

    async def test_health_check_success(self):
        """Health check returns healthy status."""
        from app.query_main import health_check

        with patch("app.query_main.get_epoch_timestamp_in_ms", return_value=1234567890):
            result = await health_check()

        assert result.status_code == 200
        assert result.body is not None

    async def test_health_check_includes_timestamp(self):
        """Health check response includes timestamp."""
        import json
        from app.query_main import health_check

        with patch("app.query_main.get_epoch_timestamp_in_ms", return_value=1234567890):
            result = await health_check()

        body = json.loads(result.body)
        assert body["status"] == "healthy"
        assert body["timestamp"] == 1234567890

    async def test_health_check_general_exception(self):
        """Health check returns 500 when get_epoch_timestamp_in_ms raises on first call."""
        from app.query_main import health_check

        mock_ts = MagicMock(side_effect=[RuntimeError("timestamp error"), 9999999])
        with patch("app.query_main.get_epoch_timestamp_in_ms", mock_ts):
            result = await health_check()

        assert result.status_code == 500


# ===========================================================================
# validation_exception_handler
# ===========================================================================


class TestValidationExceptionHandler:
    """Tests for the validation_exception_handler."""

    async def test_returns_422_with_errors(self):
        """Returns 422 with validation errors in content."""
        from app.query_main import validation_exception_handler

        mock_request = MagicMock()
        mock_request.method = "POST"
        mock_request.url = "http://test/api/v1/search"
        mock_request.json = AsyncMock(return_value={"bad": "data"})

        mock_exc = MagicMock(spec=RequestValidationError)
        mock_exc.errors.return_value = [{"loc": ["body", "query"], "msg": "field required"}]

        result = await validation_exception_handler(mock_request, mock_exc)

        assert isinstance(result, JSONResponse)
        assert result.status_code == 422

    async def test_answers_in_plain_words_and_keeps_the_detail_list(self):
        """The message names the field; each detail entry keeps loc/type with a reworded msg."""
        import json

        from app.query_main import validation_exception_handler

        mock_request = MagicMock()
        mock_request.method = "POST"
        mock_request.url = "http://test/api/v1/search"
        mock_exc = MagicMock(spec=RequestValidationError)
        mock_exc.errors.return_value = [
            {"type": "missing", "loc": ["body", "query"], "msg": "Field required"},
            {"type": "int_parsing", "loc": ["body", "limit"], "msg": "Input should be a valid integer"},
        ]

        result = await validation_exception_handler(mock_request, mock_exc)

        body = json.loads(result.body)
        assert body["message"] == "Query is required. Limit must be a whole number."
        assert [d["msg"] for d in body["detail"]] == ["Query is required.", "Limit must be a whole number."]
        assert body["detail"][0]["loc"] == ["body", "query"]
        assert body["detail"][1]["type"] == "int_parsing"

    async def test_logs_the_route_template_not_the_url(self, caplog: pytest.LogCaptureFixture) -> None:
        """The URL can carry an email or a token in its query; the route template names the endpoint."""
        import logging
        from types import SimpleNamespace

        from app.query_main import validation_exception_handler

        mock_request = MagicMock()
        mock_request.method = "GET"
        mock_request.url = "http://test/api/v1/search?token=fake-token-value&email=alice@example.com"
        mock_request.scope = {"route": SimpleNamespace(path_format="/api/v1/search", path="/api/v1/search")}
        mock_exc = MagicMock(spec=RequestValidationError)
        mock_exc.errors.return_value = [{"loc": ["query", "q"], "msg": "field required"}]

        with caplog.at_level(logging.WARNING):
            await validation_exception_handler(mock_request, mock_exc)

        (record,) = [r for r in caplog.records if "Request validation failed" in r.getMessage()]
        assert record.route == "/api/v1/search"
        assert "fake-token-value" not in caplog.text
        assert "alice" not in caplog.text and "http://" not in caplog.text
        assert "q" in record.getMessage()

    async def test_logged_errors_do_not_echo_the_rejected_input(self, caplog) -> None:
        """Each error carries the rejected value as ``input``, which for a
        password field is the password; the log keeps only where and why."""
        import logging

        from app.query_main import validation_exception_handler
        from app.utils.logger import RedactingFormatter

        mock_request = MagicMock()
        mock_request.method = "POST"
        mock_request.url = "http://test/api/v1/connect"
        mock_exc = MagicMock(spec=RequestValidationError)
        mock_exc.errors.return_value = [{
            "type": "string_too_long",
            "loc": ["body", "password"],
            "msg": "String should have at most 4 characters",
            "input": "SENTINEL-422-pw",
            "ctx": {"max_length": 4},
        }]

        with caplog.at_level(logging.WARNING, logger="app.query_main"):
            result = await validation_exception_handler(mock_request, mock_exc)

        assert result.status_code == 422
        # Nor does the body: Node logs it and relays it verbatim on the skills routes.
        assert b"SENTINEL-422" not in result.body
        assert "input" not in json.loads(result.body)["detail"][0]
        records = [r for r in caplog.records if "Request validation failed" in r.getMessage()]
        assert records
        formatter = RedactingFormatter("%(name)s - %(levelname)s - %(message)s")
        for text in (records[0].getMessage(), formatter.format(records[0])):
            assert "SENTINEL-422" not in text
            assert "password" in text
            assert "string_too_long" in text

    def test_a_real_422_keeps_its_wording_and_constraint_but_not_the_input(self) -> None:
        from fastapi import FastAPI
        from pydantic import BaseModel, Field

        from app.query_main import validation_exception_handler

        class _Body(BaseModel):
            name: str = Field(max_length=4)

        app = FastAPI()
        app.add_exception_handler(RequestValidationError, validation_exception_handler)

        @app.post("/x")
        async def _route(body: _Body) -> None:  # pragma: no cover - never reached
            return None

        response = TestClient(app).post("/x", json={"name": "F47SEED-ada@example.com"})
        assert response.status_code == 422
        assert response.json() == {
            "message": "Name must be at most 4 characters.",
            "detail": [{"type": "string_too_long", "loc": ["body", "name"], "msg": "Name must be at most 4 characters.",
                        "ctx": {"max_length": 4}}],
        }

    async def test_body_parse_failure_still_returns_422(self):
        """When request.json() raises, handler still returns 422."""
        from app.query_main import validation_exception_handler

        mock_request = MagicMock()
        mock_request.method = "POST"
        mock_request.url = "http://test/api/v1/search"
        mock_request.json = AsyncMock(side_effect=Exception("bad json"))

        mock_exc = MagicMock(spec=RequestValidationError)
        mock_exc.errors.return_value = [{"loc": ["body"], "msg": "invalid"}]

        result = await validation_exception_handler(mock_request, mock_exc)

        assert isinstance(result, JSONResponse)
        assert result.status_code == 422


# ===========================================================================
# run
# ===========================================================================


class TestRun:
    """Tests for the run function."""

    @pytest.fixture(autouse=True)
    def _never_really_exec(self):
        """run() execs the uvicorn CLI when workers>1, which REPLACES the pytest
        process. A stray WEB_CONCURRENCY in the environment is enough to reach that
        path, so no test here is allowed near the real call."""
        with patch("app.query_main.os.execvp") as m:
            yield m

    def test_run_calls_uvicorn(self):
        """run() delegates to uvicorn.run with correct arguments."""
        import os as _os

        from app.query_main import run

        env = _os.environ.copy()
        env.pop("QUERY_UVICORN_WORKERS", None)
        env.pop("WEB_CONCURRENCY", None)

        with (
            patch("app.query_main.uvicorn.run") as mock_uvicorn,
            patch.dict("os.environ", env, clear=True),
        ):
            run(host="127.0.0.1", port=9000, reload=False)

        mock_uvicorn.assert_called_once_with(
            "app.query_main:app",
            host="127.0.0.1",
            port=9000,
            log_level="info",
            access_log=False,
            reload=False,
            workers=1,
            timeout_worker_healthcheck=60,
        )

    def test_run_defaults(self):
        """run() uses default arguments."""
        import os as _os

        from app.query_main import run

        env = _os.environ.copy()
        env.pop("QUERY_UVICORN_WORKERS", None)
        env.pop("WEB_CONCURRENCY", None)

        with (
            patch("app.query_main.uvicorn.run") as mock_uvicorn,
            patch.dict("os.environ", env, clear=True),
        ):
            run()

        mock_uvicorn.assert_called_once_with(
            "app.query_main:app",
            host="0.0.0.0",
            port=8000,
            log_level="info",
            access_log=False,
            reload=True,
            workers=1,
            timeout_worker_healthcheck=60,
        )


class TestRunWorkersWarning:
    """QUERY_UVICORN_WORKERS handling, including the reload incompatibility."""

    @pytest.fixture(autouse=True)
    def _never_really_exec(self):
        with patch("app.query_main.os.execvp") as m:
            yield m

    def test_workers_gt_one_with_reload_warns(self) -> None:
        """reload=True with workers>1 warns and falls back to a single worker."""
        import warnings

        from app.query_main import run

        with (
            patch("app.query_main.uvicorn.run") as mock_uvicorn,
            patch.dict("os.environ", {"QUERY_UVICORN_WORKERS": "4"}),
        ):
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                run(reload=True)

            runtime_warnings = [x for x in w if issubclass(x.category, RuntimeWarning)]
            assert len(runtime_warnings) >= 1
            assert "not compatible with reload=True" in str(runtime_warnings[0].message)

            mock_uvicorn.assert_called_once_with(
                "app.query_main:app",
                host="0.0.0.0",
                port=8000,
                log_level="info",
                access_log=False,
                reload=True,
                workers=1,
                timeout_worker_healthcheck=60,
            )

    def test_falls_back_to_one_worker_when_exec_fails(self) -> None:
        """A failed exec must still serve, on one worker, rather than not serve."""
        import os as _os

        from app.query_main import run

        with (
            patch("app.query_main.uvicorn.run") as mock_uvicorn,
            patch("app.query_main.os.execvp", side_effect=OSError("boom")),
            patch.dict("os.environ", {"QUERY_UVICORN_WORKERS": "4"}),
        ):
            run(reload=False)
            # The sentinel must not survive, or a later retry would skip the exec.
            assert "QUERY_UVICORN_EXECED" not in _os.environ
            assert _os.environ["QUERY_UVICORN_WORKERS"] == "1"

        assert mock_uvicorn.call_args.kwargs["workers"] == 1

    def test_frozen_build_refuses_multiple_workers(self) -> None:
        """A PyInstaller build can do neither half of the multi-worker path.

        sys.executable is the app binary (its bootloader ignores "-m uvicorn") and
        uvicorn.run(workers=N) would need multiprocessing.freeze_support(). Refusing
        beats crash-looping. backend/python/Dockerfile produces such a build.
        """
        import os as _os

        from app.query_main import run

        with (
            patch("app.query_main.uvicorn.run") as mock_uvicorn,
            patch("app.query_main.os.execvp") as mock_execvp,
            patch("app.query_main.sys") as mock_sys,
            patch.dict("os.environ", {"QUERY_UVICORN_WORKERS": "4"}),
        ):
            mock_sys.frozen = True
            run(reload=False)

            mock_execvp.assert_not_called()
            assert mock_uvicorn.call_args.kwargs["workers"] == 1
            assert _os.environ["QUERY_UVICORN_WORKERS"] == "1"

    def test_exec_sentinel_prevents_a_second_exec(self) -> None:
        """Re-entering with the sentinel set must serve in-process, not exec again.

        In a frozen build sys.executable is the app binary, so an unguarded exec would
        re-enter run() forever.
        """
        from app.query_main import run

        with (
            patch("app.query_main.uvicorn.run") as mock_uvicorn,
            patch("app.query_main.os.execvp") as mock_execvp,
            patch.dict(
                "os.environ",
                {"QUERY_UVICORN_WORKERS": "4", "QUERY_UVICORN_EXECED": "1"},
            ),
        ):
            run(reload=False)

        mock_execvp.assert_not_called()
        mock_uvicorn.assert_called_once()
        assert mock_uvicorn.call_args.kwargs["workers"] == 4

    def test_multi_worker_hands_off_to_the_uvicorn_cli(self) -> None:
        """workers>1 must exec the uvicorn CLI instead of calling uvicorn.run here.

        Reached via `python -m app.query_main`, this module is __main__, and a spawned
        worker re-imports its parent's __main__ -- so each child would import the whole
        app twice and die before serving, leaving the supervisor respawning forever.
        Verified against a real container: without this, 4 workers crash-loop.
        """
        from app.query_main import run

        with (
            patch("app.query_main.uvicorn.run") as mock_uvicorn,
            patch("app.query_main.os.execvp") as mock_execvp,
            patch.dict("os.environ", {"QUERY_UVICORN_WORKERS": "4"}),
        ):
            run(reload=False)

        mock_uvicorn.assert_not_called()
        mock_execvp.assert_called_once()
        argv = mock_execvp.call_args[0][1]
        assert argv[1:4] == ["-m", "uvicorn", "app.query_main:app"]
        assert "--no-access-log" in argv
        assert argv[-4:] == ["--workers", "4", "--timeout-worker-healthcheck", "60"]

    def test_single_worker_does_not_exec(self) -> None:
        """The default path must stay in-process -- no exec, no behaviour change."""
        import os as _os

        from app.query_main import run

        env = _os.environ.copy()
        env.pop("QUERY_UVICORN_WORKERS", None)
        env.pop("WEB_CONCURRENCY", None)

        with (
            patch("app.query_main.uvicorn.run") as mock_uvicorn,
            patch("app.query_main.os.execvp") as mock_execvp,
            patch.dict("os.environ", env, clear=True),
        ):
            run(reload=False)

        mock_execvp.assert_not_called()
        mock_uvicorn.assert_called_once()
        assert mock_uvicorn.call_args.kwargs["workers"] == 1

    def test_effective_worker_count_is_published_to_the_environment(self) -> None:
        """Workers re-import this module in their own process and size budgets from the
        env var, so run() must publish the count it actually used."""
        import os as _os

        from app.query_main import run

        with (
            patch("app.query_main.uvicorn.run"),
            patch("app.query_main.os.execvp"),
            patch.dict("os.environ", {"QUERY_UVICORN_WORKERS": "4"}),
        ):
            # reload forces the count back to 1; children must see 1, not 4.
            run(reload=True)
            assert _os.environ["QUERY_UVICORN_WORKERS"] == "1"

        with (
            patch("app.query_main.uvicorn.run"),
            # Without this the exec is real and replaces the test runner.
            patch("app.query_main.os.execvp"),
            patch.dict("os.environ", {}, clear=False),
        ):
            run(workers=3, reload=False)
            assert _os.environ["QUERY_UVICORN_WORKERS"] == "3"

    def test_malformed_worker_count_does_not_raise(self) -> None:
        """A bad value must degrade to 1, not crash-loop the service.

        lifespan reads this too, so raising here would kill the app before it serves.
        Helm renders `value: ""` when the chart key is left blank.
        """
        import os as _os

        from app.query_main import configured_worker_count

        # WEB_CONCURRENCY must be out of the way: blank/absent QUERY_UVICORN_WORKERS
        # legitimately falls through to it, which would mask the degradation here.
        env = _os.environ.copy()
        env.pop("WEB_CONCURRENCY", None)

        for bad in ("", "   ", "abc", "4.5", "-", "1e3"):
            with patch.dict("os.environ", {**env, "QUERY_UVICORN_WORKERS": bad}, clear=True):
                assert configured_worker_count() == 1, f"{bad!r} should degrade to 1"

        for good, want in (("4", 4), (" 8 ", 8), ("0", 1), ("-3", 1)):
            with patch.dict("os.environ", {**env, "QUERY_UVICORN_WORKERS": good}, clear=True):
                assert configured_worker_count() == want

    def test_web_concurrency_is_still_honoured(self) -> None:
        """uvicorn reads WEB_CONCURRENCY when workers is unset; passing an explicit
        count would have silently dropped such deployments to a single process."""
        import os as _os

        from app.query_main import configured_worker_count

        env = _os.environ.copy()
        env.pop("QUERY_UVICORN_WORKERS", None)
        env.pop("WEB_CONCURRENCY", None)
        env["WEB_CONCURRENCY"] = "6"
        with patch.dict("os.environ", env, clear=True):
            assert configured_worker_count() == 6

        # An explicit QUERY_UVICORN_WORKERS wins over it.
        env["QUERY_UVICORN_WORKERS"] = "2"
        with patch.dict("os.environ", env, clear=True):
            assert configured_worker_count() == 2

    def test_workers_from_env_default(self) -> None:
        """An unset QUERY_UVICORN_WORKERS defaults to a single worker."""
        import os as _os

        from app.query_main import run

        env = _os.environ.copy()
        env.pop("QUERY_UVICORN_WORKERS", None)
        env.pop("WEB_CONCURRENCY", None)

        with (
            patch("app.query_main.uvicorn.run") as mock_uvicorn,
            patch.dict("os.environ", env, clear=True),
        ):
            run(reload=False)

        assert mock_uvicorn.call_args.kwargs["workers"] == 1


# ===========================================================================
# Module-level constants / app configuration
# ===========================================================================


class TestModuleConstants:
    """Verify module-level configuration."""

    def test_exclude_paths_contains_health(self):
        """EXCLUDE_PATHS includes /health."""
        from app.query_main import EXCLUDE_PATHS
        assert "/health" in EXCLUDE_PATHS

    def test_app_title(self):
        """FastAPI app has the expected title."""
        from app.query_main import app
        assert app.title == "Retrieval API"

    def test_app_redirect_slashes_disabled(self):
        """redirect_slashes is False on the app."""
        from app.query_main import app
        assert app.router.redirect_slashes is False


class TestCorsPolicy:
    """Browsers reach this service only through the Node API, so it must not grant CORS."""

    def test_no_cors_middleware(self) -> None:
        from starlette.middleware.cors import CORSMiddleware

        from app.query_main import app
        assert all(m.cls is not CORSMiddleware for m in app.user_middleware)

    def test_cross_origin_preflight_is_not_granted(self) -> None:
        from fastapi.testclient import TestClient

        from app.query_main import app
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


class TestRequestLogging:
    """query_main writes http.request.end through request_log, inside the request context."""

    def test_the_line_sits_just_inside_the_request_context(self) -> None:
        from app.api.middlewares.request_context import RequestContextMiddleware
        from app.api.middlewares.request_log import RequestLogMiddleware
        from app.query_main import app

        classes = [m.cls for m in app.user_middleware]
        assert classes.index(RequestContextMiddleware) + 1 == classes.index(RequestLogMiddleware)
        assert Exception not in app.exception_handlers

    @pytest.fixture
    def client(self, caplog: pytest.LogCaptureFixture) -> Iterator[TestClient]:
        import logging

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        import app.query_main as qm
        from app.api.middlewares.request_log import install_request_logging

        mini = FastAPI()
        mini.middleware("http")(qm.authenticate_requests)
        install_request_logging(mini)  # same order as query_main

        @mini.get("/api/v1/kb/{kb_id}")
        async def kb(kb_id: str) -> dict:
            return {"kb_id": kb_id}

        @mini.get("/health/boom")
        async def health_boom() -> None:
            raise RuntimeError("probe exploded")

        caplog.set_level(logging.DEBUG, logger="app.api.middlewares.request_log")
        with TestClient(mini, raise_server_exceptions=True) as test_client:
            yield test_client

    @staticmethod
    def _end(caplog: pytest.LogCaptureFixture) -> logging.LogRecord:
        (record,) = [r for r in caplog.records if r.getMessage() == "http.request.end"]
        return record

    def test_a_refusal_reaches_the_line_with_its_reason(self, client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        from app.api.middlewares.auth import AuthRejected

        refusal = AuthRejected(401, "Could not validate credentials", reason="expired",
                               headers={"WWW-Authenticate": "Bearer"})
        with patch("app.query_main.authMiddleware", new_callable=AsyncMock, side_effect=refusal):
            response = client.get("/api/v1/kb/65f0?email=alice@example.com")

        assert response.status_code == 401
        assert response.json() == {"detail": "Could not validate credentials"}
        record = self._end(caplog)
        assert record.levelno == logging.INFO
        assert (record.route, record.error_class, record.reason) == ("/api/v1/kb/{kb_id}", "auth_expired", "expired")
        assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []

    def test_an_exempt_path_crash_is_one_line_and_the_json_500(self, client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        response = client.get("/health/boom")

        assert response.status_code == 500
        assert response.json() == {"detail": "Internal server error"}
        record = self._end(caplog)
        assert record.levelno == logging.ERROR and "probe exploded" in record.exc_text
