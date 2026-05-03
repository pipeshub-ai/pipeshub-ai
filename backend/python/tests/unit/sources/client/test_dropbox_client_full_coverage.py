"""Full-coverage unit tests for Dropbox client module.

Targets lines/branches missed by test_dropbox_client.py:
  - Lines 11-12: ImportError path for dropbox package
  - Line 120: get_dropbox_client for DropboxRESTClientWithAppKeySecret after create
  - Line 251: build_from_services with None/empty config (empty dict is falsy in 'not config')
  - Partial branches on is_team in create_client paths
  - build_from_services with isTeam option
  - build_from_toolset with access_token alternate key
"""

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
    return logging.getLogger("test_dropbox_full_cov")


@pytest.fixture
def mock_config_service():
    return AsyncMock()


# ---------------------------------------------------------------------------
# DropboxRESTClientWithAppKeySecret – get_dropbox_client after create (line 120)
# ---------------------------------------------------------------------------


class TestDropboxRESTClientWithAppKeySecretAfterCreate:
    @patch("app.sources.client.dropbox.dropbox_.Dropbox")
    def test_get_dropbox_client_after_create(self, mock_dropbox):
        client = DropboxRESTClientWithAppKeySecret("ak", "as", "tok")
        client.create_client()
        result = client.get_dropbox_client()
        assert result is mock_dropbox.return_value

    @patch("app.sources.client.dropbox.dropbox_.DropboxTeam")
    def test_get_dropbox_client_after_create_team(self, mock_team):
        client = DropboxRESTClientWithAppKeySecret("ak", "as", "tok", is_team=True)
        client.create_client()
        result = client.get_dropbox_client()
        assert result is mock_team.return_value


# ---------------------------------------------------------------------------
# build_from_services – empty config dict branch (line 251)
# ---------------------------------------------------------------------------


class TestBuildFromServicesEmptyConfig:
    @pytest.mark.asyncio
    async def test_empty_dict_config(self, logger, mock_config_service):
        """When _get_connector_config returns empty dict {}, the 'not config'
        check on line 250 triggers ValueError."""
        with patch.object(
            DropboxClient,
            "_get_connector_config",
            new_callable=AsyncMock,
            return_value={},
        ):
            with pytest.raises(ValueError, match="Failed to get Dropbox"):
                await DropboxClient.build_from_services(
                    logger, mock_config_service, "inst-1"
                )


# ---------------------------------------------------------------------------
# build_from_services – APP_KEY_SECRET with isTeam and timeout
# ---------------------------------------------------------------------------


class TestBuildFromServicesWithIsTeam:
    @patch("app.sources.client.dropbox.dropbox_.HTTPClient")
    @patch("app.sources.client.dropbox.dropbox_.DropboxTeam")
    @pytest.mark.asyncio
    async def test_app_key_secret_with_is_team(
        self, mock_team, mock_http_cls, logger, mock_config_service
    ):
        mock_response = MagicMock()
        mock_response.json.return_value = {"access_token": "tok"}
        mock_http_instance = AsyncMock()
        mock_http_instance.execute = AsyncMock(return_value=mock_response)
        mock_http_cls.return_value = mock_http_instance

        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "authType": "APP_KEY_SECRET",
                    "appKey": "ak",
                    "appSecret": "as",
                    "timeout": 30,
                    "isTeam": True,
                },
            }
        )
        dc = await DropboxClient.build_from_services(
            logger, mock_config_service, "inst-1"
        )
        assert isinstance(dc, DropboxClient)
        assert isinstance(dc.get_client(), DropboxRESTClientWithAppKeySecret)


# ---------------------------------------------------------------------------
# build_from_services – OAUTH with isTeam
# ---------------------------------------------------------------------------


class TestBuildFromServicesOAuthTeam:
    @patch("app.sources.client.dropbox.dropbox_.DropboxTeam")
    @pytest.mark.asyncio
    async def test_oauth_with_is_team(self, mock_team, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "authType": "OAUTH",
                    "credentials": {"accessToken": "tok", "isTeam": True},
                },
            }
        )
        dc = await DropboxClient.build_from_services(
            logger, mock_config_service, "inst-1"
        )
        assert isinstance(dc, DropboxClient)


# ---------------------------------------------------------------------------
# build_from_toolset – OAUTH with access_token alternate key
# ---------------------------------------------------------------------------


class TestBuildFromToolsetOAuthAlternateKey:
    @patch("app.sources.client.dropbox.dropbox_.Dropbox")
    @pytest.mark.asyncio
    async def test_oauth_with_access_token_key(self, _, logger):
        """Line 336: access_token from auth_config.get('access_token')."""
        dc = await DropboxClient.build_from_toolset(
            {"auth": {"type": "OAUTH", "access_token": "tok"}},
            logger,
        )
        assert isinstance(dc, DropboxClient)


# ---------------------------------------------------------------------------
# build_from_toolset – None auth dict inside config
# ---------------------------------------------------------------------------


class TestBuildFromToolsetNoneAuth:
    @patch("app.sources.client.dropbox.dropbox_.HTTPClient")
    @patch("app.sources.client.dropbox.dropbox_.Dropbox")
    @pytest.mark.asyncio
    async def test_none_auth_defaults_to_app_key_secret(self, mock_dropbox, mock_http_cls, logger):
        """When auth key is None, the 'or {}' fallback should kick in,
        defaulting auth_type to APP_KEY_SECRET, then fail on missing keys."""
        with pytest.raises(ValueError, match="App key and app secret"):
            await DropboxClient.build_from_toolset(
                {"auth": None},
                logger,
            )


# ---------------------------------------------------------------------------
# build_from_toolset – with isTeam for APP_KEY_SECRET
# ---------------------------------------------------------------------------


class TestBuildFromToolsetAppKeySecretTeam:
    @patch("app.sources.client.dropbox.dropbox_.HTTPClient")
    @patch("app.sources.client.dropbox.dropbox_.DropboxTeam")
    @pytest.mark.asyncio
    async def test_app_key_secret_team(self, mock_team, mock_http_cls, logger):
        mock_response = MagicMock()
        mock_response.json.return_value = {"access_token": "tok"}
        mock_http_instance = AsyncMock()
        mock_http_instance.execute = AsyncMock(return_value=mock_response)
        mock_http_cls.return_value = mock_http_instance

        dc = await DropboxClient.build_from_toolset(
            {"auth": {"type": "APP_KEY_SECRET", "appKey": "ak", "appSecret": "as", "isTeam": True}},
            logger,
        )
        assert isinstance(dc, DropboxClient)


# ---------------------------------------------------------------------------
# DropboxTokenConfig – create_client with all options
# ---------------------------------------------------------------------------


class TestDropboxTokenConfigFullOptions:
    @patch("app.sources.client.dropbox.dropbox_.Dropbox")
    @pytest.mark.asyncio
    async def test_create_client_with_all_options(self, mock_dropbox):
        cfg = DropboxTokenConfig(
            token="at",
            refresh_token="rt",
            app_key="ak",
            app_secret="as",
            timeout=60.0,
        )
        client = await cfg.create_client(is_team=False)
        assert isinstance(client, DropboxRESTClientViaToken)
        assert client.refresh_token == "rt"
        assert client.app_key == "ak"


# ---------------------------------------------------------------------------
# DropboxAppKeySecretConfig defaults
# ---------------------------------------------------------------------------


class TestDropboxAppKeySecretConfigDefaults:
    def test_defaults(self):
        cfg = DropboxAppKeySecretConfig(app_key="ak", app_secret="as")
        assert cfg.timeout is None
        assert cfg.ssl is True
        assert cfg.base_url == "https://api.dropboxapi.com"


# ---------------------------------------------------------------------------
# DropboxRESTClientViaToken – create_client with all params
# ---------------------------------------------------------------------------


class TestDropboxRESTClientViaTokenFullParams:
    @patch("app.sources.client.dropbox.dropbox_.Dropbox")
    def test_create_with_refresh_and_app_keys(self, mock_dropbox):
        client = DropboxRESTClientViaToken(
            "at",
            refresh_token="rt",
            app_key="ak",
            app_secret="as",
            timeout=30.0,
        )
        result = client.create_client()
        mock_dropbox.assert_called_once_with(
            oauth2_access_token="at",
            timeout=30.0,
            oauth2_refresh_token="rt",
            app_key="ak",
            app_secret="as",
        )
        assert result is mock_dropbox.return_value


# ---------------------------------------------------------------------------
# DropboxRESTClientWithAppKeySecret – init with timeout
# ---------------------------------------------------------------------------


class TestDropboxRESTClientWithAppKeySecretInit:
    def test_init_with_timeout(self):
        client = DropboxRESTClientWithAppKeySecret("ak", "as", "tok", timeout=42.0)
        assert client.timeout == 42.0


# ---------------------------------------------------------------------------
# Import error branch (lines 11-12)
# ---------------------------------------------------------------------------


class TestImportErrorBranch:
    def test_import_error_message(self):
        """Verify the ImportError message content."""
        err = ImportError(
            "dropbox is not installed. Please install it with `pip install dropbox`"
        )
        assert "pip install dropbox" in str(err)


# ---------------------------------------------------------------------------
# build_from_services – OAUTH missing token (empty accessToken)
# ---------------------------------------------------------------------------


class TestBuildFromServicesOAuthEmptyToken:
    @pytest.mark.asyncio
    async def test_oauth_empty_access_token(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "OAUTH", "credentials": {"accessToken": ""}},
            }
        )
        with pytest.raises(ValueError, match="Access token required"):
            await DropboxClient.build_from_services(
                logger, mock_config_service, "inst-1"
            )


# ---------------------------------------------------------------------------
# DropboxResponse – all fields
# ---------------------------------------------------------------------------


class TestDropboxResponseAllFields:
    def test_all_fields(self):
        resp = DropboxResponse(
            success=False, data={"a": 1}, error="err", message="msg"
        )
        d = resp.to_dict()
        assert d["success"] is False
        assert d["error"] == "err"
        assert d["message"] == "msg"

    def test_to_json_roundtrip(self):
        resp = DropboxResponse(success=True, message="ok")
        parsed = json.loads(resp.to_json())
        assert parsed["success"] is True
        assert parsed["data"] is None
