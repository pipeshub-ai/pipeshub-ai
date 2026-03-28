"""Unit tests for app.connectors.core.base.token_service.startup_service."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.core.base.token_service.startup_service import (
    StartupService,
    startup_service,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service():
    """Create a fresh StartupService."""
    return StartupService()


def _make_mock_config_service():
    return AsyncMock()


def _make_mock_graph_provider():
    return AsyncMock()


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------


class TestStartupServiceInit:
    def test_init(self):
        svc = _make_service()
        assert svc._token_refresh_service is None
        assert svc._toolset_token_refresh_service is None
        assert svc._initialized is False
        assert isinstance(svc._initialize_lock, asyncio.Lock)

    def test_global_instance(self):
        """Module-level startup_service is a StartupService."""
        assert isinstance(startup_service, StartupService)


# ---------------------------------------------------------------------------
# Initialize
# ---------------------------------------------------------------------------


class TestInitialize:
    async def test_successful_initialization(self):
        svc = _make_service()
        config_service = _make_mock_config_service()
        graph_provider = _make_mock_graph_provider()

        mock_token_refresh = AsyncMock()
        mock_token_refresh.start = AsyncMock()
        mock_toolset_refresh = AsyncMock()
        mock_toolset_refresh.start = AsyncMock()

        with patch(
            "app.connectors.core.base.token_service.startup_service.TokenRefreshService",
            return_value=mock_token_refresh
        ), patch(
            "app.connectors.core.base.token_service.startup_service.ToolsetTokenRefreshService",
            return_value=mock_toolset_refresh
        ):
            await svc.initialize(config_service, graph_provider)

        assert svc._initialized is True
        assert svc._token_refresh_service is mock_token_refresh
        assert svc._toolset_token_refresh_service is mock_toolset_refresh
        mock_token_refresh.start.assert_awaited_once()
        mock_toolset_refresh.start.assert_awaited_once()

    async def test_initialize_twice_is_noop(self):
        svc = _make_service()
        config_service = _make_mock_config_service()
        graph_provider = _make_mock_graph_provider()

        mock_token_refresh = AsyncMock()
        mock_token_refresh.start = AsyncMock()
        mock_toolset_refresh = AsyncMock()
        mock_toolset_refresh.start = AsyncMock()

        with patch(
            "app.connectors.core.base.token_service.startup_service.TokenRefreshService",
            return_value=mock_token_refresh
        ), patch(
            "app.connectors.core.base.token_service.startup_service.ToolsetTokenRefreshService",
            return_value=mock_toolset_refresh
        ):
            await svc.initialize(config_service, graph_provider)
            # Second call should be a noop
            await svc.initialize(config_service, graph_provider)

        # start should only be called once each
        assert mock_token_refresh.start.await_count == 1
        assert mock_toolset_refresh.start.await_count == 1

    async def test_initialize_failure_in_token_refresh(self):
        svc = _make_service()
        config_service = _make_mock_config_service()
        graph_provider = _make_mock_graph_provider()

        mock_token_refresh = AsyncMock()
        mock_token_refresh.start = AsyncMock(side_effect=Exception("Token refresh failed"))

        with patch(
            "app.connectors.core.base.token_service.startup_service.TokenRefreshService",
            return_value=mock_token_refresh
        ):
            with pytest.raises(Exception, match="Token refresh failed"):
                await svc.initialize(config_service, graph_provider)

        assert svc._initialized is False
        assert svc._token_refresh_service is None
        assert svc._toolset_token_refresh_service is None

    async def test_initialize_failure_in_toolset_refresh(self):
        svc = _make_service()
        config_service = _make_mock_config_service()
        graph_provider = _make_mock_graph_provider()

        mock_token_refresh = AsyncMock()
        mock_token_refresh.start = AsyncMock()
        mock_token_refresh.stop = AsyncMock()

        mock_toolset_refresh = AsyncMock()
        mock_toolset_refresh.start = AsyncMock(side_effect=Exception("Toolset refresh failed"))

        with patch(
            "app.connectors.core.base.token_service.startup_service.TokenRefreshService",
            return_value=mock_token_refresh
        ), patch(
            "app.connectors.core.base.token_service.startup_service.ToolsetTokenRefreshService",
            return_value=mock_toolset_refresh
        ):
            with pytest.raises(Exception, match="Toolset refresh failed"):
                await svc.initialize(config_service, graph_provider)

        assert svc._initialized is False
        # Token refresh service was set, then should be cleaned up
        assert svc._token_refresh_service is None
        mock_token_refresh.stop.assert_awaited_once()

    async def test_initialize_failure_cleanup_stop_exception(self):
        """Cleanup stop() exceptions are silently caught."""
        svc = _make_service()
        config_service = _make_mock_config_service()
        graph_provider = _make_mock_graph_provider()

        mock_token_refresh = AsyncMock()
        mock_token_refresh.start = AsyncMock()
        mock_token_refresh.stop = AsyncMock(side_effect=Exception("stop failed"))

        mock_toolset_refresh = AsyncMock()
        mock_toolset_refresh.start = AsyncMock(side_effect=Exception("Toolset refresh failed"))

        with patch(
            "app.connectors.core.base.token_service.startup_service.TokenRefreshService",
            return_value=mock_token_refresh
        ), patch(
            "app.connectors.core.base.token_service.startup_service.ToolsetTokenRefreshService",
            return_value=mock_toolset_refresh
        ):
            with pytest.raises(Exception, match="Toolset refresh failed"):
                await svc.initialize(config_service, graph_provider)

        assert svc._initialized is False
        assert svc._token_refresh_service is None

    async def test_initialize_failure_toolset_cleanup(self):
        """If toolset refresh was set before failure, it's cleaned up too."""
        svc = _make_service()
        config_service = _make_mock_config_service()
        graph_provider = _make_mock_graph_provider()

        mock_token_refresh = AsyncMock()
        mock_token_refresh.start = AsyncMock()
        mock_token_refresh.stop = AsyncMock()

        mock_toolset_refresh = AsyncMock()
        mock_toolset_refresh.start = AsyncMock()
        mock_toolset_refresh.stop = AsyncMock()

        # Simulate: both services start fine, but then something after
        # toolset_token_refresh_service.start() fails before _initialized is set
        # Actually the only way _toolset_token_refresh_service is set AND an exception
        # occurs is if it's set but then something else fails.
        # Let's test by making the service succeed but then fail right after
        # We need to cause an exception after the toolset service is assigned

        # Simulate: token refresh starts, toolset starts, but something after fails
        # The code sets _toolset_token_refresh_service after start(), so we simulate
        # a failure that somehow has both services set.
        # Manually set both for cleanup testing
        svc._token_refresh_service = mock_token_refresh
        svc._toolset_token_refresh_service = mock_toolset_refresh

        # Simulate exception scenario where both need cleanup
        with patch(
            "app.connectors.core.base.token_service.startup_service.TokenRefreshService",
            side_effect=Exception("creation failed")
        ):
            with pytest.raises(Exception, match="creation failed"):
                await svc.initialize(config_service, graph_provider)

        # Both should have been cleaned up
        assert svc._token_refresh_service is None
        assert svc._toolset_token_refresh_service is None
        mock_token_refresh.stop.assert_awaited_once()
        mock_toolset_refresh.stop.assert_awaited_once()

    async def test_initialize_failure_toolset_stop_exception(self):
        """Toolset stop() exception during cleanup is silently caught."""
        svc = _make_service()
        config_service = _make_mock_config_service()
        graph_provider = _make_mock_graph_provider()

        mock_toolset_refresh = AsyncMock()
        mock_toolset_refresh.stop = AsyncMock(side_effect=Exception("toolset stop failed"))

        # Pre-set the toolset service to trigger cleanup path
        svc._toolset_token_refresh_service = mock_toolset_refresh

        with patch(
            "app.connectors.core.base.token_service.startup_service.TokenRefreshService",
            side_effect=Exception("creation failed")
        ):
            with pytest.raises(Exception, match="creation failed"):
                await svc.initialize(config_service, graph_provider)

        assert svc._toolset_token_refresh_service is None


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------


class TestShutdown:
    async def test_shutdown_both_services(self):
        svc = _make_service()
        mock_token = AsyncMock()
        mock_token.stop = AsyncMock()
        mock_toolset = AsyncMock()
        mock_toolset.stop = AsyncMock()

        svc._token_refresh_service = mock_token
        svc._toolset_token_refresh_service = mock_toolset
        svc._initialized = True

        await svc.shutdown()

        assert svc._initialized is False
        assert svc._token_refresh_service is None
        assert svc._toolset_token_refresh_service is None
        mock_token.stop.assert_awaited_once()
        mock_toolset.stop.assert_awaited_once()

    async def test_shutdown_no_services(self):
        svc = _make_service()
        # Should not raise even with no services
        await svc.shutdown()
        assert svc._initialized is False

    async def test_shutdown_exception(self):
        svc = _make_service()
        mock_token = AsyncMock()
        mock_token.stop = AsyncMock(side_effect=Exception("stop error"))
        svc._token_refresh_service = mock_token
        svc._initialized = True

        # Should not raise
        await svc.shutdown()

    async def test_shutdown_only_token_service(self):
        svc = _make_service()
        mock_token = AsyncMock()
        mock_token.stop = AsyncMock()
        svc._token_refresh_service = mock_token
        svc._initialized = True

        await svc.shutdown()
        assert svc._token_refresh_service is None
        mock_token.stop.assert_awaited_once()

    async def test_shutdown_only_toolset_service(self):
        svc = _make_service()
        mock_toolset = AsyncMock()
        mock_toolset.stop = AsyncMock()
        svc._toolset_token_refresh_service = mock_toolset
        svc._initialized = True

        await svc.shutdown()
        assert svc._toolset_token_refresh_service is None
        mock_toolset.stop.assert_awaited_once()


# ---------------------------------------------------------------------------
# Getters
# ---------------------------------------------------------------------------


class TestGetters:
    def test_get_token_refresh_service_none(self):
        svc = _make_service()
        assert svc.get_token_refresh_service() is None

    def test_get_token_refresh_service_set(self):
        svc = _make_service()
        mock = MagicMock()
        svc._token_refresh_service = mock
        assert svc.get_token_refresh_service() is mock

    def test_get_toolset_token_refresh_service_none(self):
        svc = _make_service()
        assert svc.get_toolset_token_refresh_service() is None

    def test_get_toolset_token_refresh_service_set(self):
        svc = _make_service()
        mock = MagicMock()
        svc._toolset_token_refresh_service = mock
        assert svc.get_toolset_token_refresh_service() is mock
