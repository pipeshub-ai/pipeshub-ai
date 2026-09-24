"""Extended tests for StartupService covering MCP token refresh paths.

Targets uncovered lines from the coverage report:
- Line 31: _mcp_token_refresh_service initialization
- Line 54: wait_for_initial_refresh=False for connector token refresh
- Line 63: log message after connector token refresh init
- Lines 95-99: MCP token refresh cleanup on exception
- Lines 120-122: MCP token refresh shutdown
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.core.base.token_service.startup_service import (
    StartupService,
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


# ===========================================================================
# MCP token refresh service initialization (line 31)
# ===========================================================================


class TestMCPTokenRefreshInit:
    def test_init_has_mcp_field(self):
        """_mcp_token_refresh_service is None after construction."""
        svc = _make_service()
        assert svc._mcp_token_refresh_service is None

    @pytest.mark.asyncio
    async def test_successful_initialization_includes_mcp(self):
        """All three services are initialized including MCP."""
        svc = _make_service()
        config = _make_mock_config_service()
        graph = _make_mock_graph_provider()

        mock_token = AsyncMock()
        mock_token.start = AsyncMock()
        mock_toolset = AsyncMock()
        mock_toolset.start = AsyncMock()
        mock_mcp = AsyncMock()
        mock_mcp.start = AsyncMock()

        with patch(
            "app.connectors.core.base.token_service.startup_service.TokenRefreshService",
            return_value=mock_token,
        ), patch(
            "app.connectors.core.base.token_service.startup_service.ToolsetTokenRefreshService",
            return_value=mock_toolset,
        ), patch(
            "app.connectors.core.base.token_service.startup_service.MCPTokenRefreshService",
            return_value=mock_mcp,
        ):
            await svc.initialize(config, graph)

        assert svc._initialized is True
        assert svc._token_refresh_service is mock_token
        assert svc._toolset_token_refresh_service is mock_toolset
        assert svc._mcp_token_refresh_service is mock_mcp
        mock_token.start.assert_awaited_once_with(wait_for_initial_refresh=False)
        mock_toolset.start.assert_awaited_once_with(wait_for_initial_refresh=False)
        mock_mcp.start.assert_awaited_once_with(wait_for_initial_refresh=False)


# ===========================================================================
# Already-initialized guard (second call skips)
# ===========================================================================


class TestAlreadyInitializedGuard:
    @pytest.mark.asyncio
    async def test_second_call_is_noop(self):
        """Second initialize() call after successful init is a noop."""
        svc = _make_service()
        config = _make_mock_config_service()
        graph = _make_mock_graph_provider()

        mock_token = AsyncMock()
        mock_token.start = AsyncMock()
        mock_toolset = AsyncMock()
        mock_toolset.start = AsyncMock()
        mock_mcp = AsyncMock()
        mock_mcp.start = AsyncMock()

        with patch(
            "app.connectors.core.base.token_service.startup_service.TokenRefreshService",
            return_value=mock_token,
        ), patch(
            "app.connectors.core.base.token_service.startup_service.ToolsetTokenRefreshService",
            return_value=mock_toolset,
        ), patch(
            "app.connectors.core.base.token_service.startup_service.MCPTokenRefreshService",
            return_value=mock_mcp,
        ):
            await svc.initialize(config, graph)
            await svc.initialize(config, graph)

        assert mock_token.start.await_count == 1
        assert mock_toolset.start.await_count == 1
        assert mock_mcp.start.await_count == 1


# ===========================================================================
# Partial initialization cleanup: MCP failure (lines 95-99)
# ===========================================================================


class TestMCPFailureCleanup:
    @pytest.mark.asyncio
    async def test_mcp_start_failure_cleans_up_all(self):
        """If MCP start() fails, token and toolset services are cleaned up."""
        svc = _make_service()
        config = _make_mock_config_service()
        graph = _make_mock_graph_provider()

        mock_token = AsyncMock()
        mock_token.start = AsyncMock()
        mock_token.stop = AsyncMock()
        mock_toolset = AsyncMock()
        mock_toolset.start = AsyncMock()
        mock_toolset.stop = AsyncMock()
        mock_mcp = AsyncMock()
        mock_mcp.start = AsyncMock(side_effect=Exception("MCP failed"))

        with patch(
            "app.connectors.core.base.token_service.startup_service.TokenRefreshService",
            return_value=mock_token,
        ), patch(
            "app.connectors.core.base.token_service.startup_service.ToolsetTokenRefreshService",
            return_value=mock_toolset,
        ), patch(
            "app.connectors.core.base.token_service.startup_service.MCPTokenRefreshService",
            return_value=mock_mcp,
        ):
            with pytest.raises(Exception, match="MCP failed"):
                await svc.initialize(config, graph)

        assert svc._initialized is False
        assert svc._token_refresh_service is None
        assert svc._toolset_token_refresh_service is None
        assert svc._mcp_token_refresh_service is None
        mock_token.stop.assert_awaited_once()
        mock_toolset.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mcp_cleanup_stop_exception_is_swallowed(self):
        """If pre-set MCP service stop() throws during cleanup, it is silently caught."""
        svc = _make_service()
        config = _make_mock_config_service()
        graph = _make_mock_graph_provider()

        mock_mcp = AsyncMock()
        mock_mcp.stop = AsyncMock(side_effect=Exception("mcp stop failed"))

        # Pre-set the MCP service to trigger cleanup path
        svc._mcp_token_refresh_service = mock_mcp

        with patch(
            "app.connectors.core.base.token_service.startup_service.TokenRefreshService",
            side_effect=Exception("creation failed"),
        ):
            with pytest.raises(Exception, match="creation failed"):
                await svc.initialize(config, graph)

        assert svc._mcp_token_refresh_service is None

    @pytest.mark.asyncio
    async def test_all_three_services_cleaned_up_on_failure(self):
        """If all three services were set before failure, all are cleaned up."""
        svc = _make_service()
        config = _make_mock_config_service()
        graph = _make_mock_graph_provider()

        mock_token = AsyncMock()
        mock_token.stop = AsyncMock()
        mock_toolset = AsyncMock()
        mock_toolset.stop = AsyncMock()
        mock_mcp = AsyncMock()
        mock_mcp.stop = AsyncMock()

        svc._token_refresh_service = mock_token
        svc._toolset_token_refresh_service = mock_toolset
        svc._mcp_token_refresh_service = mock_mcp

        with patch(
            "app.connectors.core.base.token_service.startup_service.TokenRefreshService",
            side_effect=Exception("creation failed"),
        ):
            with pytest.raises(Exception, match="creation failed"):
                await svc.initialize(config, graph)

        assert svc._token_refresh_service is None
        assert svc._toolset_token_refresh_service is None
        assert svc._mcp_token_refresh_service is None
        mock_token.stop.assert_awaited_once()
        mock_toolset.stop.assert_awaited_once()
        mock_mcp.stop.assert_awaited_once()


# ===========================================================================
# Shutdown with MCP service (lines 120-122)
# ===========================================================================


class TestShutdownMCP:
    @pytest.mark.asyncio
    async def test_shutdown_all_three_services(self):
        svc = _make_service()
        mock_token = AsyncMock()
        mock_token.stop = AsyncMock()
        mock_toolset = AsyncMock()
        mock_toolset.stop = AsyncMock()
        mock_mcp = AsyncMock()
        mock_mcp.stop = AsyncMock()

        svc._token_refresh_service = mock_token
        svc._toolset_token_refresh_service = mock_toolset
        svc._mcp_token_refresh_service = mock_mcp
        svc._initialized = True

        await svc.shutdown()

        assert svc._initialized is False
        assert svc._token_refresh_service is None
        assert svc._toolset_token_refresh_service is None
        assert svc._mcp_token_refresh_service is None
        mock_token.stop.assert_awaited_once()
        mock_toolset.stop.assert_awaited_once()
        mock_mcp.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_shutdown_only_mcp_service(self):
        svc = _make_service()
        mock_mcp = AsyncMock()
        mock_mcp.stop = AsyncMock()
        svc._mcp_token_refresh_service = mock_mcp
        svc._initialized = True

        await svc.shutdown()

        assert svc._mcp_token_refresh_service is None
        mock_mcp.stop.assert_awaited_once()


# ===========================================================================
# set_messaging_producer forwarding
# ===========================================================================


class TestSetMessagingProducer:
    def test_forwards_to_token_refresh_service(self):
        svc = _make_service()
        mock_token = MagicMock()
        svc._token_refresh_service = mock_token
        producer = MagicMock()

        svc.set_messaging_producer(producer)
        mock_token.set_messaging_producer.assert_called_once_with(producer)

    def test_noop_when_no_token_refresh_service(self):
        svc = _make_service()
        producer = MagicMock()
        # Should not raise
        svc.set_messaging_producer(producer)


# ===========================================================================
# Getter for MCP service
# ===========================================================================


class TestGetMCPService:
    def test_get_mcp_token_refresh_service_none(self):
        svc = _make_service()
        assert svc.get_mcp_token_refresh_service() is None

    def test_get_mcp_token_refresh_service_set(self):
        svc = _make_service()
        mock = MagicMock()
        svc._mcp_token_refresh_service = mock
        assert svc.get_mcp_token_refresh_service() is mock
