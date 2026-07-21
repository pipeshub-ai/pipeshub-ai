"""Tests for app.connectors.core.base.token_service.toolset_token_refresh_service"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.core.base.token_service.oauth_service import (
    RefreshTokenInvalidError,
)
from app.connectors.core.base.token_service.toolset_token_refresh_service import (
    MAX_REFRESH_TOKEN_INVALID_FAILURES,
    ToolsetTokenRefreshService,
)

CONFIG_PATH = "/services/toolsets/inst-1/user-1"


@pytest.fixture
def mock_config_service() -> MagicMock:
    """Mock ConfigurationService with async get_config/set_config."""
    svc = MagicMock()
    svc.get_config = AsyncMock(return_value={"isAuthenticated": True, "toolsetType": "confluence"})
    svc.set_config = AsyncMock(return_value=True)
    return svc


@pytest.fixture
def service(mock_config_service: MagicMock) -> ToolsetTokenRefreshService:
    return ToolsetTokenRefreshService(mock_config_service)


class TestToolsetRefreshTokenInvalidThreshold:
    """Tests for _handle_refresh_token_invalid() deactivation threshold."""

    @pytest.mark.asyncio
    async def test_deactivates_only_on_threshold_rejection(
        self, service: ToolsetTokenRefreshService, mock_config_service: MagicMock
    ) -> None:
        """First N-1 rejections leave the toolset untouched; the Nth deauthenticates it."""
        error = RefreshTokenInvalidError("refresh_token is invalid")

        for _ in range(MAX_REFRESH_TOKEN_INVALID_FAILURES - 1):
            await service._handle_refresh_token_invalid(CONFIG_PATH, error)

        mock_config_service.set_config.assert_not_awaited()
        assert service._invalid_refresh_failures[CONFIG_PATH] == MAX_REFRESH_TOKEN_INVALID_FAILURES - 1

        await service._handle_refresh_token_invalid(CONFIG_PATH, error)

        mock_config_service.set_config.assert_awaited_once()
        path, config = mock_config_service.set_config.await_args.args
        assert path == CONFIG_PATH
        assert config["isAuthenticated"] is False
        assert config["deauthReason"] == "refresh_token_invalid"
        assert CONFIG_PATH not in service._invalid_refresh_failures

    @pytest.mark.asyncio
    async def test_mark_unauthenticated_tolerates_missing_config(
        self, service: ToolsetTokenRefreshService, mock_config_service: MagicMock
    ) -> None:
        """A deleted toolset config aborts the write without raising."""
        mock_config_service.get_config = AsyncMock(return_value=None)

        await service._mark_toolset_unauthenticated(CONFIG_PATH)

        mock_config_service.set_config.assert_not_awaited()
