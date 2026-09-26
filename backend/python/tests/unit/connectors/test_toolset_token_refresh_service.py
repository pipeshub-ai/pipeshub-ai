"""Tests for app.connectors.core.base.token_service.toolset_token_refresh_service"""

from copy import deepcopy
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.core.base.token_service import oauth_service
from app.connectors.core.base.token_service.oauth_service import (
    RefreshTokenInvalidError,
)
from app.connectors.core.base.token_service.toolset_token_refresh_service import (
    MAX_REFRESH_TOKEN_INVALID_FAILURES,
    ToolsetTokenRefreshService,
)
from tests.support.slack_oauth import TOKEN_URL as SLACK_TOKEN_URL
from tests.support.slack_oauth import FakeSlackOAuth

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


class TestSlackToolsetRotation:
    """A Slack toolset signed in to an app with token rotation, refreshed through Slack's endpoint."""

    @pytest.fixture
    def store(self) -> dict:
        return {
            "isAuthenticated": True,
            "auth": {"type": "OAUTH"},
            "credentials": {
                "access_token": "xoxe.xoxp-1-old",
                "refresh_token": "xoxe-1-old",
                "expires_in": 43200,
                "created_at": (datetime.now() - timedelta(hours=13)).isoformat(),  # noqa: DTZ005 - OAuthToken is naive
            },
        }

    @pytest.fixture
    def slack_service(self, store: dict, monkeypatch: pytest.MonkeyPatch) -> ToolsetTokenRefreshService:
        async def get_config(_path: str, *_: object, **__: object) -> dict:
            return deepcopy(store)

        async def set_config(_path: str, value: dict) -> bool:
            store.clear()
            store.update(deepcopy(value))
            return True

        config_service = MagicMock()
        config_service.get_config = AsyncMock(side_effect=get_config)
        config_service.set_config = AsyncMock(side_effect=set_config)
        service = ToolsetTokenRefreshService(config_service)
        monkeypatch.setattr(service, "_build_complete_oauth_config", AsyncMock(return_value={
            "clientId": "client-1",
            "clientSecret": "secret-1",
            "authorizeUrl": "https://slack.com/oauth/v2/authorize",
            "tokenUrl": SLACK_TOKEN_URL,
            "redirectUri": "https://pipeshub.example/toolsets/oauth/callback/slack",
            "scopes": [],
            "scopeParameterName": "user_scope",
            "tokenResponsePath": "authed_user",
        }))
        monkeypatch.setattr(service, "schedule_token_refresh", AsyncMock())
        return service

    @pytest.mark.asyncio
    async def test_an_expired_token_is_refreshed_and_both_new_tokens_saved(
        self, slack_service: ToolsetTokenRefreshService, store: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slack = FakeSlackOAuth("xoxe-1-old")
        monkeypatch.setattr(oauth_service, "ClientSession", slack.client_session)

        await slack_service._refresh_toolset_token(CONFIG_PATH, "slack")

        assert [r["refresh_token"] for r in slack.requests] == ["xoxe-1-old"]
        new_access, new_refresh = slack.issued[0]
        assert (store["credentials"]["access_token"], store["credentials"]["refresh_token"]) == (new_access, new_refresh)

    @pytest.mark.asyncio
    async def test_a_revoked_refresh_token_counts_towards_deactivation(
        self, slack_service: ToolsetTokenRefreshService, store: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slack = FakeSlackOAuth("xoxe-1-issued-after-a-reinstall")
        monkeypatch.setattr(oauth_service, "ClientSession", slack.client_session)

        await slack_service._refresh_toolset_token(CONFIG_PATH, "slack")

        assert len(slack.requests) == 1
        assert slack_service._invalid_refresh_failures[CONFIG_PATH] == 1
        assert store["credentials"]["refresh_token"] == "xoxe-1-old"
