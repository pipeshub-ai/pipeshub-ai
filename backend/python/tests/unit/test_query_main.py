"""Unit tests for app.query_main — Query service FastAPI entrypoint."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import httpx
import pytest
from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.services.messaging.config import MessageBrokerType


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
            patch("app.query_main.MessagingUtils.create_aiconfig_consumer_config", new_callable=AsyncMock, return_value={}),
            patch("app.query_main.KafkaUtils") as MockKU,
            patch("app.query_main.MessagingFactory") as MockMF,
        ):
            MockKU.create_aiconfig_message_handler = AsyncMock(return_value=MagicMock())
            MockMF.create_consumer.return_value = mock_consumer

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
            patch("app.query_main.MessagingUtils.create_aiconfig_consumer_config", new_callable=AsyncMock, return_value={}),
            patch("app.query_main.KafkaUtils") as MockKU,
            patch("app.query_main.MessagingFactory") as MockMF,
        ):
            MockKU.create_aiconfig_message_handler = AsyncMock(return_value=MagicMock())
            MockMF.create_consumer.return_value = mock_consumer

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

        call_count = 0

        def info_side_effect(msg):
            nonlocal call_count
            call_count += 1
            # Raise on the second info call which is the final log after append
            if call_count == 2:
                raise RuntimeError("log explosion")

        logger.info = MagicMock(side_effect=info_side_effect)

        with (
            patch("app.query_main.get_message_broker_type", return_value=MessageBrokerType.KAFKA),
            patch("app.query_main.MessagingUtils.create_aiconfig_consumer_config", new_callable=AsyncMock, return_value={}),
            patch("app.query_main.KafkaUtils") as MockKU,
            patch("app.query_main.MessagingFactory") as MockMF,
        ):
            MockKU.create_aiconfig_message_handler = AsyncMock(return_value=MagicMock())
            MockMF.create_consumer.return_value = mock_consumer

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

        call_count = 0

        def info_side_effect(msg):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("post-append error")

        logger.info = MagicMock(side_effect=info_side_effect)

        with (
            patch("app.query_main.get_message_broker_type", return_value=MessageBrokerType.KAFKA),
            patch("app.query_main.MessagingUtils.create_aiconfig_consumer_config", new_callable=AsyncMock, return_value={}),
            patch("app.query_main.KafkaUtils") as MockKU,
            patch("app.query_main.MessagingFactory") as MockMF,
        ):
            MockKU.create_aiconfig_message_handler = AsyncMock(return_value=MagicMock())
            MockMF.create_consumer.return_value = mock_consumer

            with pytest.raises(RuntimeError, match="post-append error"):
                await start_kafka_consumers(container)

        # Stop was attempted but failed — error was logged
        mock_consumer.stop.assert_awaited_once()
        # The cleanup error should be logged
        logger.error.assert_any_call(
            "Error stopping aiconfig consumer during cleanup: stop failed"
        )


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
        """Even if consumer.stop raises, the finally block returns True."""
        from app.query_main import stop_kafka_consumers

        consumer = AsyncMock()
        consumer.stop = AsyncMock(side_effect=Exception("stop fail"))
        container = _make_container()
        container.kafka_consumers = [("aiconfig", consumer)]

        result = await stop_kafka_consumers(container)
        # The finally block returns True regardless
        assert result is True
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

            # After shutdown, config_service.close should be called
            mock_config_service.close.assert_awaited_once()

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

        with patch(
            "app.query_main.authMiddleware",
            new_callable=AsyncMock,
            side_effect=HTTPException(status_code=401, detail="Unauthorized"),
        ):
            result = await authenticate_requests(mock_request, call_next)

        assert isinstance(result, JSONResponse)
        assert result.status_code == 401

    async def test_unexpected_exception_returns_500(self):
        """Unexpected exception returns 500 Internal Server Error."""
        from app.query_main import authenticate_requests

        mock_request = MagicMock()
        mock_request.url.path = "/api/v1/search"
        call_next = AsyncMock()

        with patch(
            "app.query_main.authMiddleware",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ):
            result = await authenticate_requests(mock_request, call_next)

        assert isinstance(result, JSONResponse)
        assert result.status_code == 500


# ===========================================================================
# health_check
# ===========================================================================


class TestHealthCheck:
    """Tests for the /health endpoint handler."""

    def _set_app_container(self, query_app, mock_config):
        """Helper: set app.container with mock config_service on the FastAPI instance."""
        mock_container = MagicMock()
        mock_container.config_service.return_value = mock_config
        query_app.container = mock_container
        return mock_container

    async def test_healthy_connector(self):
        """Connector service returns 200 -> healthy response."""
        from app.query_main import health_check, app as query_app

        mock_config = MagicMock()
        mock_config.get_config = AsyncMock(return_value={
            "connectors": {"endpoint": "http://connector:8088"}
        })

        mock_http_response = MagicMock()
        mock_http_response.status_code = 200
        mock_http_response.text = "OK"

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_http_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        self._set_app_container(query_app, mock_config)
        try:
            with (
                patch("app.query_main.httpx.AsyncClient", return_value=mock_client),
                patch("app.query_main.get_epoch_timestamp_in_ms", return_value=123456),
            ):
                result = await health_check()
            assert result.status_code == 200
        finally:
            if hasattr(query_app, "container"):
                del query_app.container

    async def test_unhealthy_connector(self):
        """Connector service returns non-200 -> fail response."""
        from app.query_main import health_check, app as query_app

        mock_config = MagicMock()
        mock_config.get_config = AsyncMock(return_value={
            "connectors": {"endpoint": "http://connector:8088"}
        })

        mock_http_response = MagicMock()
        mock_http_response.status_code = 503
        mock_http_response.text = "Service Unavailable"

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_http_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        self._set_app_container(query_app, mock_config)
        try:
            with (
                patch("app.query_main.httpx.AsyncClient", return_value=mock_client),
                patch("app.query_main.get_epoch_timestamp_in_ms", return_value=123456),
            ):
                result = await health_check()
            assert result.status_code == 500
        finally:
            if hasattr(query_app, "container"):
                del query_app.container

    async def test_request_error(self):
        """httpx.RequestError -> fail response with connection error."""
        from app.query_main import health_check, app as query_app

        mock_config = MagicMock()
        mock_config.get_config = AsyncMock(return_value={
            "connectors": {"endpoint": "http://connector:8088"}
        })

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=httpx.RequestError("connection refused", request=MagicMock())
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        self._set_app_container(query_app, mock_config)
        try:
            with (
                patch("app.query_main.httpx.AsyncClient", return_value=mock_client),
                patch("app.query_main.get_epoch_timestamp_in_ms", return_value=123456),
            ):
                result = await health_check()
            assert result.status_code == 500
        finally:
            if hasattr(query_app, "container"):
                del query_app.container

    async def test_generic_exception(self):
        """Generic exception -> fail response."""
        from app.query_main import health_check, app as query_app

        mock_config = MagicMock()
        mock_config.get_config = AsyncMock(side_effect=RuntimeError("config error"))

        self._set_app_container(query_app, mock_config)
        try:
            with patch("app.query_main.get_epoch_timestamp_in_ms", return_value=123456):
                result = await health_check()
            assert result.status_code == 500
        finally:
            if hasattr(query_app, "container"):
                del query_app.container

    async def test_default_endpoint_used(self):
        """When endpoint key is missing, DefaultEndpoints.CONNECTOR_ENDPOINT is used."""
        from app.query_main import health_check, app as query_app

        mock_config = MagicMock()
        # connectors dict exists but no "endpoint" key -> .get returns default
        connectors_dict = MagicMock()
        connectors_dict.get.return_value = "http://localhost:8088"
        endpoints_dict = {"connectors": connectors_dict}
        mock_config.get_config = AsyncMock(return_value=endpoints_dict)

        mock_http_response = MagicMock()
        mock_http_response.status_code = 200
        mock_http_response.text = "OK"

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_http_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        self._set_app_container(query_app, mock_config)
        try:
            with (
                patch("app.query_main.httpx.AsyncClient", return_value=mock_client),
                patch("app.query_main.get_epoch_timestamp_in_ms", return_value=123456),
            ):
                result = await health_check()
            assert result.status_code == 200
        finally:
            if hasattr(query_app, "container"):
                del query_app.container


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

    def test_run_calls_uvicorn(self):
        """run() delegates to uvicorn.run with correct arguments."""
        from app.query_main import run

        with patch("app.query_main.uvicorn.run") as mock_uvicorn:
            run(host="127.0.0.1", port=9000, reload=False)

        mock_uvicorn.assert_called_once_with(
            "app.query_main:app",
            host="127.0.0.1",
            port=9000,
            log_level="info",
            reload=False,
        )

    def test_run_defaults(self):
        """run() uses default arguments."""
        from app.query_main import run

        with patch("app.query_main.uvicorn.run") as mock_uvicorn:
            run()

        mock_uvicorn.assert_called_once_with(
            "app.query_main:app",
            host="0.0.0.0",
            port=8000,
            log_level="info",
            reload=True,
        )


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
