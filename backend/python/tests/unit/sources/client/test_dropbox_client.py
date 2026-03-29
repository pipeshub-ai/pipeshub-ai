"""Unit tests for Dropbox client module."""

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sources.client.dropbox.dropbox_ import (
    DropboxAppKeySecretConfig,
    DropboxClient,
    DropboxRESTClientViaToken,
    DropboxRESTClientWithAppKeySecret,
    DropboxResponse,
    DropboxTokenConfig,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def logger():
    return logging.getLogger("test_dropbox_client")


@pytest.fixture
def mock_config_service():
    return AsyncMock()


# ---------------------------------------------------------------------------
# DropboxResponse
# ---------------------------------------------------------------------------


class TestDropboxResponse:
    def test_to_dict(self):
        resp = DropboxResponse(success=True, data={"key": "val"})
        d = resp.to_dict()
        assert d["success"] is True
        assert d["data"]["key"] == "val"

    def test_to_json(self):
        resp = DropboxResponse(success=False, error="oops")
        j = resp.to_json()
        parsed = json.loads(j)
        assert parsed["success"] is False

    def test_defaults(self):
        resp = DropboxResponse(success=True)
        assert resp.data is None
        assert resp.error is None
        assert resp.message is None


# ---------------------------------------------------------------------------
# DropboxRESTClientViaToken
# ---------------------------------------------------------------------------


class TestDropboxRESTClientViaToken:
    def test_init(self):
        client = DropboxRESTClientViaToken("at")
        assert client.access_token == "at"
        assert client.refresh_token is None
        assert client.is_team is False
        assert client.dropbox_client is None

    def test_init_with_all_options(self):
        client = DropboxRESTClientViaToken(
            "at", refresh_token="rt", app_key="ak", app_secret="as",
            timeout=60.0, is_team=True
        )
        assert client.refresh_token == "rt"
        assert client.app_key == "ak"
        assert client.is_team is True

    def test_get_dropbox_client_before_create_raises(self):
        client = DropboxRESTClientViaToken("at")
        with pytest.raises(RuntimeError, match="not initialized"):
            client.get_dropbox_client()

    @patch("app.sources.client.dropbox.dropbox_.Dropbox")
    def test_create_client_individual(self, mock_dropbox):
        client = DropboxRESTClientViaToken("at")
        result = client.create_client()
        mock_dropbox.assert_called_once()
        assert result is mock_dropbox.return_value

    @patch("app.sources.client.dropbox.dropbox_.DropboxTeam")
    def test_create_client_team(self, mock_dropbox_team):
        client = DropboxRESTClientViaToken("at", is_team=True)
        result = client.create_client()
        mock_dropbox_team.assert_called_once()
        assert result is mock_dropbox_team.return_value

    @patch("app.sources.client.dropbox.dropbox_.Dropbox")
    def test_get_dropbox_client_after_create(self, mock_dropbox):
        client = DropboxRESTClientViaToken("at")
        client.create_client()
        assert client.get_dropbox_client() is mock_dropbox.return_value


# ---------------------------------------------------------------------------
# DropboxRESTClientWithAppKeySecret
# ---------------------------------------------------------------------------


class TestDropboxRESTClientWithAppKeySecret:
    def test_init(self):
        client = DropboxRESTClientWithAppKeySecret("ak", "as", "tok")
        assert client.app_key == "ak"
        assert client.app_secret == "as"
        assert client.token == "tok"
        assert client.is_team is False

    def test_get_dropbox_client_before_create_raises(self):
        client = DropboxRESTClientWithAppKeySecret("ak", "as", "tok")
        with pytest.raises(RuntimeError, match="not initialized"):
            client.get_dropbox_client()

    @patch("app.sources.client.dropbox.dropbox_.Dropbox")
    def test_create_client_individual(self, mock_dropbox):
        client = DropboxRESTClientWithAppKeySecret("ak", "as", "tok")
        result = client.create_client()
        mock_dropbox.assert_called_once()
        assert result is mock_dropbox.return_value

    @patch("app.sources.client.dropbox.dropbox_.DropboxTeam")
    def test_create_client_team(self, mock_dropbox_team):
        client = DropboxRESTClientWithAppKeySecret("ak", "as", "tok", is_team=True)
        result = client.create_client()
        mock_dropbox_team.assert_called_once()
        assert result is mock_dropbox_team.return_value


# ---------------------------------------------------------------------------
# DropboxTokenConfig
# ---------------------------------------------------------------------------


class TestDropboxTokenConfig:
    @patch("app.sources.client.dropbox.dropbox_.Dropbox")
    @pytest.mark.asyncio
    async def test_create_client(self, mock_dropbox):
        cfg = DropboxTokenConfig(token="at")
        client = await cfg.create_client()
        assert isinstance(client, DropboxRESTClientViaToken)

    @patch("app.sources.client.dropbox.dropbox_.DropboxTeam")
    @pytest.mark.asyncio
    async def test_create_client_team(self, mock_dropbox_team):
        cfg = DropboxTokenConfig(token="at")
        client = await cfg.create_client(is_team=True)
        assert isinstance(client, DropboxRESTClientViaToken)
        assert client.is_team is True

    def test_to_dict(self):
        cfg = DropboxTokenConfig(token="at")
        d = cfg.to_dict()
        assert d["token"] == "at"
        assert d["ssl"] is True

    def test_defaults(self):
        cfg = DropboxTokenConfig(token="at")
        assert cfg.refresh_token is None
        assert cfg.app_key is None
        assert cfg.base_url == "https://api.dropboxapi.com"


# ---------------------------------------------------------------------------
# DropboxAppKeySecretConfig
# ---------------------------------------------------------------------------


class TestDropboxAppKeySecretConfig:
    def test_to_dict(self):
        cfg = DropboxAppKeySecretConfig(app_key="ak", app_secret="as")
        d = cfg.to_dict()
        assert d["app_key"] == "ak"
        assert d["ssl"] is True

    @patch("app.sources.client.dropbox.dropbox_.HTTPClient")
    @patch("app.sources.client.dropbox.dropbox_.Dropbox")
    @pytest.mark.asyncio
    async def test_create_client(self, mock_dropbox, mock_http_cls):
        mock_response = MagicMock()
        mock_response.json.return_value = {"access_token": "fetched-tok"}
        mock_http_instance = AsyncMock()
        mock_http_instance.execute = AsyncMock(return_value=mock_response)
        mock_http_cls.return_value = mock_http_instance

        cfg = DropboxAppKeySecretConfig(app_key="ak", app_secret="as")
        client = await cfg.create_client()
        assert isinstance(client, DropboxRESTClientWithAppKeySecret)

    @patch("app.sources.client.dropbox.dropbox_.HTTPClient")
    @patch("app.sources.client.dropbox.dropbox_.DropboxTeam")
    @pytest.mark.asyncio
    async def test_create_client_team(self, mock_dropbox_team, mock_http_cls):
        mock_response = MagicMock()
        mock_response.json.return_value = {"access_token": "fetched-tok"}
        mock_http_instance = AsyncMock()
        mock_http_instance.execute = AsyncMock(return_value=mock_response)
        mock_http_cls.return_value = mock_http_instance

        cfg = DropboxAppKeySecretConfig(app_key="ak", app_secret="as")
        client = await cfg.create_client(is_team=True)
        assert isinstance(client, DropboxRESTClientWithAppKeySecret)


# ---------------------------------------------------------------------------
# DropboxClient init / get_client
# ---------------------------------------------------------------------------


class TestDropboxClientInit:
    def test_init(self):
        mock_client = MagicMock()
        dc = DropboxClient(mock_client)
        assert dc.get_client() is mock_client


# ---------------------------------------------------------------------------
# build_with_config
# ---------------------------------------------------------------------------


class TestBuildWithConfig:
    @patch("app.sources.client.dropbox.dropbox_.Dropbox")
    @pytest.mark.asyncio
    async def test_token_config(self, _):
        cfg = DropboxTokenConfig(token="at")
        dc = await DropboxClient.build_with_config(cfg)
        assert isinstance(dc, DropboxClient)

    @patch("app.sources.client.dropbox.dropbox_.HTTPClient")
    @patch("app.sources.client.dropbox.dropbox_.Dropbox")
    @pytest.mark.asyncio
    async def test_app_key_secret_config(self, mock_dropbox, mock_http_cls):
        mock_response = MagicMock()
        mock_response.json.return_value = {"access_token": "tok"}
        mock_http_instance = AsyncMock()
        mock_http_instance.execute = AsyncMock(return_value=mock_response)
        mock_http_cls.return_value = mock_http_instance

        cfg = DropboxAppKeySecretConfig(app_key="ak", app_secret="as")
        dc = await DropboxClient.build_with_config(cfg)
        assert isinstance(dc, DropboxClient)


# ---------------------------------------------------------------------------
# _get_connector_config
# ---------------------------------------------------------------------------


class TestGetConnectorConfig:
    @pytest.mark.asyncio
    async def test_returns_config(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value={"auth": {}})
        result = await DropboxClient._get_connector_config(logger, mock_config_service, "inst-1")
        assert result == {"auth": {}}

    @pytest.mark.asyncio
    async def test_empty_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Failed to get Dropbox"):
            await DropboxClient._get_connector_config(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_exception_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(side_effect=RuntimeError("boom"))
        with pytest.raises(ValueError, match="Failed to get Dropbox"):
            await DropboxClient._get_connector_config(logger, mock_config_service, "inst-1")


# ---------------------------------------------------------------------------
# build_from_services
# ---------------------------------------------------------------------------


class TestBuildFromServices:
    @patch("app.sources.client.dropbox.dropbox_.HTTPClient")
    @patch("app.sources.client.dropbox.dropbox_.Dropbox")
    @pytest.mark.asyncio
    async def test_app_key_secret(self, mock_dropbox, mock_http_cls, logger, mock_config_service):
        mock_response = MagicMock()
        mock_response.json.return_value = {"access_token": "tok"}
        mock_http_instance = AsyncMock()
        mock_http_instance.execute = AsyncMock(return_value=mock_response)
        mock_http_cls.return_value = mock_http_instance

        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "APP_KEY_SECRET", "appKey": "ak", "appSecret": "as"},
            }
        )
        dc = await DropboxClient.build_from_services(logger, mock_config_service, "inst-1")
        assert isinstance(dc, DropboxClient)

    @patch("app.sources.client.dropbox.dropbox_.Dropbox")
    @pytest.mark.asyncio
    async def test_oauth(self, mock_dropbox, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "OAUTH", "credentials": {"accessToken": "tok"}},
            }
        )
        dc = await DropboxClient.build_from_services(logger, mock_config_service, "inst-1")
        assert isinstance(dc, DropboxClient)

    @pytest.mark.asyncio
    async def test_no_config_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError):
            await DropboxClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_missing_app_key_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={"auth": {"authType": "APP_KEY_SECRET"}}
        )
        with pytest.raises(ValueError, match="App key and app secret"):
            await DropboxClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_oauth_missing_token_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={"auth": {"authType": "OAUTH", "credentials": {}}}
        )
        with pytest.raises(ValueError, match="Access token required"):
            await DropboxClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_unsupported_auth_type_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={"auth": {"authType": "UNSUPPORTED"}}
        )
        with pytest.raises(ValueError, match="Unsupported auth type"):
            await DropboxClient.build_from_services(logger, mock_config_service, "inst-1")


# ---------------------------------------------------------------------------
# build_from_toolset
# ---------------------------------------------------------------------------


class TestBuildFromToolset:
    @pytest.mark.asyncio
    async def test_empty_config_raises(self, logger):
        with pytest.raises(ValueError, match="Toolset config is required"):
            await DropboxClient.build_from_toolset({}, logger)

    @patch("app.sources.client.dropbox.dropbox_.HTTPClient")
    @patch("app.sources.client.dropbox.dropbox_.Dropbox")
    @pytest.mark.asyncio
    async def test_app_key_secret_success(self, mock_dropbox, mock_http_cls, logger):
        mock_response = MagicMock()
        mock_response.json.return_value = {"access_token": "tok"}
        mock_http_instance = AsyncMock()
        mock_http_instance.execute = AsyncMock(return_value=mock_response)
        mock_http_cls.return_value = mock_http_instance

        dc = await DropboxClient.build_from_toolset(
            {"auth": {"type": "APP_KEY_SECRET", "appKey": "ak", "appSecret": "as"}},
            logger,
        )
        assert isinstance(dc, DropboxClient)

    @pytest.mark.asyncio
    async def test_app_key_secret_missing_raises(self, logger):
        with pytest.raises(ValueError, match="App key and app secret"):
            await DropboxClient.build_from_toolset(
                {"auth": {"type": "APP_KEY_SECRET"}}, logger
            )

    @patch("app.sources.client.dropbox.dropbox_.Dropbox")
    @pytest.mark.asyncio
    async def test_oauth_success(self, _, logger):
        dc = await DropboxClient.build_from_toolset(
            {"auth": {"type": "OAUTH", "accessToken": "tok"}},
            logger,
        )
        assert isinstance(dc, DropboxClient)

    @pytest.mark.asyncio
    async def test_oauth_missing_token_raises(self, logger):
        with pytest.raises(ValueError, match="Access token required"):
            await DropboxClient.build_from_toolset(
                {"auth": {"type": "OAUTH"}}, logger
            )

    @pytest.mark.asyncio
    async def test_unsupported_auth_type_raises(self, logger):
        with pytest.raises(ValueError, match="Unsupported auth type"):
            await DropboxClient.build_from_toolset(
                {"auth": {"type": "UNSUPPORTED"}}, logger
            )
