"""Unit tests for Slack client module."""

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sources.client.slack.slack import (
    SlackApiKeyConfig,
    SlackClient,
    SlackRESTClientViaApiKey,
    SlackRESTClientViaToken,
    SlackRESTClientViaUsernamePassword,
    SlackResponse,
    SlackTokenConfig,
    SlackUsernamePasswordConfig,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def logger():
    return logging.getLogger("test_slack_client")


@pytest.fixture
def mock_config_service():
    return AsyncMock()


# ---------------------------------------------------------------------------
# SlackResponse
# ---------------------------------------------------------------------------


class TestSlackResponse:
    def test_to_dict(self):
        resp = SlackResponse(success=True, data={"key": "val"})
        d = resp.to_dict()
        assert d["success"] is True
        assert d["data"] == {"key": "val"}

    def test_to_json(self):
        resp = SlackResponse(success=False, error="oops")
        j = resp.to_json()
        parsed = json.loads(j)
        assert parsed["success"] is False
        assert parsed["error"] == "oops"

    def test_default_none_fields(self):
        resp = SlackResponse(success=True)
        assert resp.data is None
        assert resp.error is None
        assert resp.message is None


# ---------------------------------------------------------------------------
# REST client classes
# ---------------------------------------------------------------------------


class TestSlackRESTClientViaUsernamePassword:
    def test_raises_not_implemented(self):
        with pytest.raises(NotImplementedError):
            SlackRESTClientViaUsernamePassword("user", "pass")

    def test_get_web_client_raises(self):
        # Can't construct, so test the method on a class level mock
        with pytest.raises(NotImplementedError):
            SlackRESTClientViaUsernamePassword("user", "pass")


class TestSlackRESTClientViaApiKey:
    def test_raises_not_implemented(self):
        with pytest.raises(NotImplementedError):
            SlackRESTClientViaApiKey("e@e.com", "key")


class TestSlackRESTClientViaToken:
    def test_empty_token_raises(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            SlackRESTClientViaToken("")

    def test_invalid_prefix_raises(self):
        with pytest.raises(ValueError, match="Invalid Slack token format"):
            SlackRESTClientViaToken("invalid-token")

    @patch("app.sources.client.slack.slack.WebClient")
    def test_bot_token(self, mock_wc):
        client = SlackRESTClientViaToken("xoxb-test-token")
        assert client.get_web_client() is mock_wc.return_value

    @patch("app.sources.client.slack.slack.WebClient")
    def test_user_token(self, mock_wc):
        client = SlackRESTClientViaToken("xoxp-test-token")
        assert client.get_web_client() is mock_wc.return_value


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------


class TestSlackUsernamePasswordConfig:
    def test_to_dict(self):
        cfg = SlackUsernamePasswordConfig("user", "pass")
        d = cfg.to_dict()
        assert d["username"] == "user"
        assert d["ssl"] is False

    def test_create_client_raises(self):
        cfg = SlackUsernamePasswordConfig("user", "pass")
        with pytest.raises(NotImplementedError):
            cfg.create_client()

    def test_get_web_client_raises(self):
        cfg = SlackUsernamePasswordConfig("user", "pass")
        with pytest.raises(NotImplementedError):
            cfg.get_web_client()


class TestSlackTokenConfig:
    @patch("app.sources.client.slack.slack.WebClient")
    def test_create_client(self, mock_wc):
        cfg = SlackTokenConfig("xoxb-test-token")
        client = cfg.create_client()
        assert isinstance(client, SlackRESTClientViaToken)

    def test_to_dict(self):
        cfg = SlackTokenConfig("xoxb-tok")
        d = cfg.to_dict()
        assert d["token"] == "xoxb-tok"


class TestSlackApiKeyConfig:
    def test_to_dict(self):
        cfg = SlackApiKeyConfig("e@e.com", "key")
        d = cfg.to_dict()
        assert d["email"] == "e@e.com"
        assert d["api_key"] == "key"

    def test_create_client_raises(self):
        cfg = SlackApiKeyConfig("e@e.com", "key")
        with pytest.raises(NotImplementedError):
            cfg.create_client()


# ---------------------------------------------------------------------------
# SlackClient init / get_client
# ---------------------------------------------------------------------------


class TestSlackClientInit:
    def test_init_and_get_client(self):
        mock_client = MagicMock()
        sc = SlackClient(mock_client)
        assert sc.get_client() is mock_client

    def test_get_web_client(self):
        mock_client = MagicMock()
        mock_client.get_web_client.return_value = MagicMock()
        sc = SlackClient(mock_client)
        assert sc.get_web_client() is mock_client.get_web_client.return_value


# ---------------------------------------------------------------------------
# build_with_config
# ---------------------------------------------------------------------------


class TestBuildWithConfig:
    @patch("app.sources.client.slack.slack.WebClient")
    def test_token_config(self, _):
        cfg = SlackTokenConfig("xoxb-test-token")
        sc = SlackClient.build_with_config(cfg)
        assert isinstance(sc, SlackClient)


# ---------------------------------------------------------------------------
# _get_connector_config
# ---------------------------------------------------------------------------


class TestGetConnectorConfig:
    @pytest.mark.asyncio
    async def test_returns_config(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value={"auth": {}})
        result = await SlackClient._get_connector_config(logger, mock_config_service, "inst-1")
        assert result == {"auth": {}}

    @pytest.mark.asyncio
    async def test_empty_config_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Failed to get Slack"):
            await SlackClient._get_connector_config(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_exception_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(side_effect=RuntimeError("boom"))
        with pytest.raises(ValueError, match="Failed to get Slack"):
            await SlackClient._get_connector_config(logger, mock_config_service, "inst-1")


# ---------------------------------------------------------------------------
# build_from_services
# ---------------------------------------------------------------------------


class TestBuildFromServices:
    @pytest.mark.asyncio
    @patch("app.sources.client.slack.slack.WebClient")
    async def test_api_token(self, _, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "API_TOKEN", "userOAuthAccessToken": "xoxb-tok"},
            }
        )
        sc = await SlackClient.build_from_services(logger, mock_config_service, "inst-1")
        assert isinstance(sc, SlackClient)

    @pytest.mark.asyncio
    @patch("app.sources.client.slack.slack.WebClient")
    async def test_user_oauth_default(self, _, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "userOAuthAccessToken", "userOAuthAccessToken": "xoxp-tok"},
            }
        )
        sc = await SlackClient.build_from_services(logger, mock_config_service, "inst-1")
        assert isinstance(sc, SlackClient)

    @pytest.mark.asyncio
    async def test_no_config_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError):
            await SlackClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_missing_token_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "API_TOKEN"},
            }
        )
        with pytest.raises(ValueError, match="Token required"):
            await SlackClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_invalid_auth_type_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "UNSUPPORTED"},
            }
        )
        with pytest.raises(ValueError, match="Invalid auth type"):
            await SlackClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_username_password_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "USERNAME_PASSWORD", "username": "u", "password": "p"},
            }
        )
        with pytest.raises(NotImplementedError):
            await SlackClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_username_password_missing_creds_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "USERNAME_PASSWORD"},
            }
        )
        with pytest.raises(ValueError, match="Username and password required"):
            await SlackClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_api_key_missing_creds_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "API_KEY"},
            }
        )
        with pytest.raises(ValueError, match="Email and API key required"):
            await SlackClient.build_from_services(logger, mock_config_service, "inst-1")


# ---------------------------------------------------------------------------
# build_from_toolset
# ---------------------------------------------------------------------------


class TestBuildFromToolset:
    @pytest.mark.asyncio
    async def test_empty_config_raises(self, logger):
        with pytest.raises(ValueError, match="Toolset config is required"):
            await SlackClient.build_from_toolset({}, logger)

    @pytest.mark.asyncio
    @patch("app.sources.client.slack.slack.WebClient")
    async def test_oauth_success(self, _, logger):
        sc = await SlackClient.build_from_toolset(
            {"authType": "OAUTH", "credentials": {"access_token": "xoxb-tok"}},
            logger,
        )
        assert isinstance(sc, SlackClient)

    @pytest.mark.asyncio
    async def test_oauth_missing_token_raises(self, logger):
        with pytest.raises(ValueError, match="Token required"):
            await SlackClient.build_from_toolset(
                {"authType": "OAUTH", "credentials": {}}, logger
            )

    @pytest.mark.asyncio
    @patch("app.sources.client.slack.slack.WebClient")
    async def test_api_token_success(self, _, logger):
        sc = await SlackClient.build_from_toolset(
            {"authType": "API_TOKEN", "apiToken": "xoxb-tok", "credentials": {}},
            logger,
        )
        assert isinstance(sc, SlackClient)

    @pytest.mark.asyncio
    async def test_api_token_missing_raises(self, logger):
        with pytest.raises(ValueError, match="API token required"):
            await SlackClient.build_from_toolset(
                {"authType": "API_TOKEN", "credentials": {}}, logger
            )

    @pytest.mark.asyncio
    async def test_invalid_auth_type_raises(self, logger):
        with pytest.raises(ValueError, match="Invalid auth type"):
            await SlackClient.build_from_toolset(
                {"authType": "UNSUPPORTED", "credentials": {}}, logger
            )
