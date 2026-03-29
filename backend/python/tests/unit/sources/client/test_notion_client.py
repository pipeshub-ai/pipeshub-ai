"""Unit tests for Notion client module."""

import base64
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sources.client.notion.notion import (
    NotionClient,
    NotionRESTClientViaOAuth,
    NotionRESTClientViaToken,
    NotionResponse,
    NotionTokenConfig,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def logger():
    return logging.getLogger("test_notion_client")


@pytest.fixture
def mock_config_service():
    return AsyncMock()


# ---------------------------------------------------------------------------
# NotionResponse
# ---------------------------------------------------------------------------


class TestNotionResponse:
    def test_to_dict(self):
        resp = NotionResponse(success=True, data={"key": "val"})
        d = resp.to_dict()
        assert d["success"] is True
        assert d["data"]["key"] == "val"

    def test_to_json(self):
        resp = NotionResponse(success=False, error="oops")
        j = resp.to_json()
        parsed = json.loads(j)
        assert parsed["success"] is False

    def test_default_none_fields(self):
        resp = NotionResponse(success=True)
        assert resp.data is None
        assert resp.error is None
        assert resp.message is None


# ---------------------------------------------------------------------------
# NotionRESTClientViaOAuth
# ---------------------------------------------------------------------------


class TestNotionRESTClientViaOAuth:
    def test_init_with_access_token(self):
        client = NotionRESTClientViaOAuth("cid", "csec", "http://redirect", access_token="at")
        assert client.base_url == "https://api.notion.com/v1"
        assert client.client_id == "cid"
        assert client.client_secret == "csec"
        assert client.access_token == "at"
        assert client.is_oauth_completed() is True
        assert client.headers["Notion-Version"] == "2025-09-03"

    def test_init_without_access_token(self):
        client = NotionRESTClientViaOAuth("cid", "csec", "http://redirect")
        assert client.access_token is None
        assert client.is_oauth_completed() is False

    def test_get_base_url(self):
        client = NotionRESTClientViaOAuth("cid", "csec", "http://redirect")
        assert client.get_base_url() == "https://api.notion.com/v1"

    def test_custom_version(self):
        client = NotionRESTClientViaOAuth("cid", "csec", "http://redirect", version="2023-08-01")
        assert client.version == "2023-08-01"
        assert client.headers["Notion-Version"] == "2023-08-01"

    def test_get_authorization_url_without_state(self):
        client = NotionRESTClientViaOAuth("cid", "csec", "http://redirect")
        url = client.get_authorization_url()
        assert "client_id=cid" in url
        assert "response_type=code" in url
        assert "redirect_uri=http" in url
        assert "state" not in url

    def test_get_authorization_url_with_state(self):
        client = NotionRESTClientViaOAuth("cid", "csec", "http://redirect")
        url = client.get_authorization_url(state="my-state")
        assert "state=my-state" in url

    @pytest.mark.asyncio
    async def test_initiate_oauth_flow_delegates(self):
        client = NotionRESTClientViaOAuth("cid", "csec", "http://redirect")
        with patch.object(client, "_exchange_code_for_token", new_callable=AsyncMock, return_value="at") as mock_exchange:
            result = await client.initiate_oauth_flow("auth-code")
            mock_exchange.assert_called_once_with("auth-code")
            assert result == "at"

    @pytest.mark.asyncio
    async def test_exchange_code_for_token_success(self):
        client = NotionRESTClientViaOAuth("cid", "csec", "http://redirect")
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json.return_value = {"access_token": "new-at"}
        with patch.object(client, "execute", new_callable=AsyncMock, return_value=mock_response):
            result = await client._exchange_code_for_token("code")
            assert result == "new-at"
            assert client.access_token == "new-at"
            assert client._oauth_completed is True
            assert "Bearer new-at" in client.headers["Authorization"]

    @pytest.mark.asyncio
    async def test_exchange_code_for_token_failure(self):
        client = NotionRESTClientViaOAuth("cid", "csec", "http://redirect")
        mock_response = MagicMock()
        mock_response.status = 400
        mock_response.text.return_value = "Bad Request"
        with patch.object(client, "execute", new_callable=AsyncMock, return_value=mock_response):
            with pytest.raises(Exception, match="Token request failed"):
                await client._exchange_code_for_token("code")

    @pytest.mark.asyncio
    async def test_exchange_code_no_access_token(self):
        client = NotionRESTClientViaOAuth("cid", "csec", "http://redirect")
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json.return_value = {"token_type": "bearer"}
        with patch.object(client, "execute", new_callable=AsyncMock, return_value=mock_response):
            result = await client._exchange_code_for_token("code")
            assert result is None

    @pytest.mark.asyncio
    async def test_refresh_token_success(self):
        client = NotionRESTClientViaOAuth("cid", "csec", "http://redirect")
        mock_response = MagicMock()
        mock_response.json = AsyncMock(return_value={"access_token": "refreshed-at"})

        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.execute = AsyncMock(return_value=mock_response)

        with patch("app.sources.client.notion.notion.HTTPClient", return_value=mock_http):
            result = await client.refresh_token("rt")
            assert result == "refreshed-at"
            assert client.access_token == "refreshed-at"

    @pytest.mark.asyncio
    async def test_refresh_token_no_token_returned(self):
        client = NotionRESTClientViaOAuth("cid", "csec", "http://redirect")
        mock_response = MagicMock()
        mock_response.json = AsyncMock(return_value={"error": "invalid"})

        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.execute = AsyncMock(return_value=mock_response)

        with patch("app.sources.client.notion.notion.HTTPClient", return_value=mock_http):
            result = await client.refresh_token("rt")
            assert result is None


# ---------------------------------------------------------------------------
# NotionRESTClientViaToken
# ---------------------------------------------------------------------------


class TestNotionRESTClientViaToken:
    def test_init(self):
        client = NotionRESTClientViaToken("tok")
        assert client.base_url == "https://api.notion.com/v1"
        assert client.version == "2025-09-03"
        assert client.headers["Notion-Version"] == "2025-09-03"
        assert client.headers["Content-Type"] == "application/json"

    def test_get_base_url(self):
        client = NotionRESTClientViaToken("tok")
        assert client.get_base_url() == "https://api.notion.com/v1"

    def test_custom_version(self):
        client = NotionRESTClientViaToken("tok", version="2023-08-01")
        assert client.version == "2023-08-01"


# ---------------------------------------------------------------------------
# NotionTokenConfig
# ---------------------------------------------------------------------------


class TestNotionTokenConfig:
    def test_create_client(self):
        cfg = NotionTokenConfig("tok")
        client = cfg.create_client()
        assert isinstance(client, NotionRESTClientViaToken)

    def test_to_dict(self):
        cfg = NotionTokenConfig("tok")
        d = cfg.to_dict()
        assert d["token"] == "tok"
        assert d["ssl"] is True

    def test_default_version(self):
        cfg = NotionTokenConfig("tok")
        assert cfg.version == "2025-09-03"


# ---------------------------------------------------------------------------
# NotionClient init / get_client
# ---------------------------------------------------------------------------


class TestNotionClientInit:
    def test_init_with_token_client(self):
        client = NotionRESTClientViaToken("tok")
        nc = NotionClient(client)
        assert nc.get_client() is client

    def test_init_with_oauth_client(self):
        client = NotionRESTClientViaOAuth("cid", "csec", "http://redirect")
        nc = NotionClient(client)
        assert nc.get_client() is client


# ---------------------------------------------------------------------------
# build_with_config
# ---------------------------------------------------------------------------


class TestBuildWithConfig:
    def test_token_config(self):
        cfg = NotionTokenConfig("tok")
        nc = NotionClient.build_with_config(cfg)
        assert isinstance(nc, NotionClient)
        assert isinstance(nc.get_client(), NotionRESTClientViaToken)


# ---------------------------------------------------------------------------
# _get_connector_config
# ---------------------------------------------------------------------------


class TestGetConnectorConfig:
    @pytest.mark.asyncio
    async def test_returns_config(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value={"auth": {}})
        result = await NotionClient._get_connector_config(logger, mock_config_service, "inst-1")
        assert result == {"auth": {}}

    @pytest.mark.asyncio
    async def test_empty_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Failed to get Notion"):
            await NotionClient._get_connector_config(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_exception_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(side_effect=RuntimeError("boom"))
        with pytest.raises(ValueError, match="Failed to get Notion"):
            await NotionClient._get_connector_config(logger, mock_config_service, "inst-1")


# ---------------------------------------------------------------------------
# build_from_services
# ---------------------------------------------------------------------------


class TestBuildFromServices:
    @pytest.mark.asyncio
    async def test_api_token_success(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "API_TOKEN", "apiToken": "tok"},
            }
        )
        nc = await NotionClient.build_from_services(logger, mock_config_service, "inst-1")
        assert isinstance(nc, NotionClient)
        assert isinstance(nc.get_client(), NotionRESTClientViaToken)

    @pytest.mark.asyncio
    async def test_api_token_missing_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={"auth": {"authType": "API_TOKEN"}}
        )
        with pytest.raises(ValueError, match="Token required"):
            await NotionClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_oauth_success(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "authType": "OAUTH",
                    "clientId": "cid",
                    "clientSecret": "csec",
                    "redirectUri": "http://redirect",
                },
                "credentials": {"access_token": "at"},
            }
        )
        nc = await NotionClient.build_from_services(logger, mock_config_service, "inst-1")
        assert isinstance(nc.get_client(), NotionRESTClientViaOAuth)

    @pytest.mark.asyncio
    async def test_oauth_missing_client_credentials_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "OAUTH"},
                "credentials": {"access_token": "at"},
            }
        )
        with pytest.raises(ValueError, match="Client ID, client secret"):
            await NotionClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_oauth_shared_config(self, logger, mock_config_service):
        """Should fetch from shared OAuth config when oauthConfigId present."""

        async def fake_get_config(path, default=None):
            if "connectors" in path:
                return {
                    "auth": {
                        "authType": "OAUTH",
                        "oauthConfigId": "oauth-123",
                        "redirectUri": "http://redirect",
                    },
                    "credentials": {"access_token": "at"},
                }
            if "oauth" in path:
                return [
                    {
                        "_id": "oauth-123",
                        "config": {"clientId": "shared-cid", "clientSecret": "shared-csec"},
                    }
                ]
            return default

        mock_config_service.get_config = AsyncMock(side_effect=fake_get_config)
        nc = await NotionClient.build_from_services(logger, mock_config_service, "inst-1")
        assert isinstance(nc.get_client(), NotionRESTClientViaOAuth)

    @pytest.mark.asyncio
    async def test_oauth_shared_config_not_found(self, logger, mock_config_service):
        """Missing shared OAuth config should still fail with missing credentials."""

        async def fake_get_config(path, default=None):
            if "connectors" in path:
                return {
                    "auth": {
                        "authType": "OAUTH",
                        "oauthConfigId": "oauth-123",
                    },
                    "credentials": {"access_token": "at"},
                }
            if "oauth" in path:
                return []
            return default

        mock_config_service.get_config = AsyncMock(side_effect=fake_get_config)
        with pytest.raises(ValueError, match="Client ID, client secret"):
            await NotionClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_no_config_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError):
            await NotionClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_invalid_auth_type_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={"auth": {"authType": "UNSUPPORTED"}}
        )
        with pytest.raises(ValueError, match="Invalid auth type"):
            await NotionClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_custom_version(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "API_TOKEN", "apiToken": "tok"},
                "version": "2023-08-01",
            }
        )
        nc = await NotionClient.build_from_services(logger, mock_config_service, "inst-1")
        client = nc.get_client()
        assert isinstance(client, NotionRESTClientViaToken)
        assert client.version == "2023-08-01"
