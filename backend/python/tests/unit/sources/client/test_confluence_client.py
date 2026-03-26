"""Unit tests for Confluence client module."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sources.client.confluence.confluence import (
    ConfluenceApiKeyConfig,
    ConfluenceClient,
    ConfluenceRESTClientViaApiKey,
    ConfluenceRESTClientViaToken,
    ConfluenceRESTClientViaUsernamePassword,
    ConfluenceTokenConfig,
    ConfluenceUsernamePasswordConfig,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def logger():
    return logging.getLogger("test_confluence_client")


@pytest.fixture
def mock_config_service():
    return AsyncMock()


# ---------------------------------------------------------------------------
# REST client classes
# ---------------------------------------------------------------------------


class TestConfluenceRESTClientViaUsernamePassword:
    def test_init_stores_base_url(self):
        client = ConfluenceRESTClientViaUsernamePassword("http://confluence.local", "user", "pass")
        assert client.base_url == "http://confluence.local"

    def test_get_base_url(self):
        client = ConfluenceRESTClientViaUsernamePassword("http://confluence.local", "u", "p")
        assert client.get_base_url() == "http://confluence.local"


class TestConfluenceRESTClientViaApiKey:
    def test_init_stores_base_url(self):
        client = ConfluenceRESTClientViaApiKey("http://confluence.local", "e@e.com", "key")
        assert client.base_url == "http://confluence.local"

    def test_get_base_url(self):
        client = ConfluenceRESTClientViaApiKey("http://confluence.local", "e@e.com", "key")
        assert client.get_base_url() == "http://confluence.local"


class TestConfluenceRESTClientViaToken:
    def test_init(self):
        client = ConfluenceRESTClientViaToken("http://confluence.local", "tok")
        assert client.base_url == "http://confluence.local"
        assert client.token == "tok"

    def test_get_base_url(self):
        client = ConfluenceRESTClientViaToken("http://confluence.local", "tok")
        assert client.get_base_url() == "http://confluence.local"

    def test_get_token(self):
        client = ConfluenceRESTClientViaToken("http://confluence.local", "tok")
        assert client.get_token() == "tok"

    def test_set_token(self):
        client = ConfluenceRESTClientViaToken("http://confluence.local", "tok")
        client.set_token("new-tok")
        assert client.token == "new-tok"
        assert client.headers["Authorization"] == "Bearer new-tok"


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------


class TestConfluenceUsernamePasswordConfig:
    def test_create_client(self):
        cfg = ConfluenceUsernamePasswordConfig("http://c.local", "u", "p")
        client = cfg.create_client()
        assert isinstance(client, ConfluenceRESTClientViaUsernamePassword)

    def test_to_dict(self):
        cfg = ConfluenceUsernamePasswordConfig("http://c.local", "u", "p")
        d = cfg.to_dict()
        assert d["base_url"] == "http://c.local"
        assert d["ssl"] is False


class TestConfluenceTokenConfig:
    def test_create_client(self):
        cfg = ConfluenceTokenConfig("http://c.local", "tok")
        client = cfg.create_client()
        assert isinstance(client, ConfluenceRESTClientViaToken)

    def test_to_dict(self):
        cfg = ConfluenceTokenConfig("http://c.local", "tok")
        d = cfg.to_dict()
        assert d["token"] == "tok"


class TestConfluenceApiKeyConfig:
    def test_create_client(self):
        cfg = ConfluenceApiKeyConfig("http://c.local", "e@e.com", "key")
        client = cfg.create_client()
        assert isinstance(client, ConfluenceRESTClientViaApiKey)

    def test_to_dict(self):
        cfg = ConfluenceApiKeyConfig("http://c.local", "e@e.com", "key")
        d = cfg.to_dict()
        assert d["email"] == "e@e.com"


# ---------------------------------------------------------------------------
# ConfluenceClient init / get_client
# ---------------------------------------------------------------------------


class TestConfluenceClientInit:
    def test_init_and_get_client(self):
        mock_client = MagicMock()
        cc = ConfluenceClient(mock_client)
        assert cc.get_client() is mock_client


# ---------------------------------------------------------------------------
# build_with_config
# ---------------------------------------------------------------------------


class TestBuildWithConfig:
    def test_token_config(self):
        cfg = ConfluenceTokenConfig("http://c.local", "tok")
        cc = ConfluenceClient.build_with_config(cfg)
        assert isinstance(cc, ConfluenceClient)
        assert isinstance(cc.get_client(), ConfluenceRESTClientViaToken)

    def test_username_password_config(self):
        cfg = ConfluenceUsernamePasswordConfig("http://c.local", "u", "p")
        cc = ConfluenceClient.build_with_config(cfg)
        assert isinstance(cc.get_client(), ConfluenceRESTClientViaUsernamePassword)

    def test_api_key_config(self):
        cfg = ConfluenceApiKeyConfig("http://c.local", "e@e.com", "key")
        cc = ConfluenceClient.build_with_config(cfg)
        assert isinstance(cc.get_client(), ConfluenceRESTClientViaApiKey)


# ---------------------------------------------------------------------------
# get_accessible_resources
# ---------------------------------------------------------------------------


class TestGetAccessibleResources:
    @pytest.mark.asyncio
    async def test_empty_token_raises(self):
        with pytest.raises(ValueError, match="No token provided"):
            await ConfluenceClient.get_accessible_resources("")

    @pytest.mark.asyncio
    @patch("app.sources.client.confluence.confluence.HTTPClient")
    async def test_success(self, mock_http_cls):
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json.return_value = [
            {"id": "cloud-1", "name": "Site 1", "url": "https://site1.atlassian.net", "scopes": ["read"]},
        ]
        mock_instance = AsyncMock()
        mock_instance.execute = AsyncMock(return_value=mock_response)
        mock_http_cls.return_value = mock_instance

        resources = await ConfluenceClient.get_accessible_resources("valid-token")
        assert len(resources) == 1
        assert resources[0].id == "cloud-1"

    @pytest.mark.asyncio
    @patch("app.sources.client.confluence.confluence.HTTPClient")
    async def test_non_200_raises(self, mock_http_cls):
        mock_response = MagicMock()
        mock_response.status = 401
        mock_response.text.return_value = "Unauthorized"
        mock_instance = AsyncMock()
        mock_instance.execute = AsyncMock(return_value=mock_response)
        mock_http_cls.return_value = mock_instance

        with pytest.raises(Exception, match="Failed to fetch accessible resources"):
            await ConfluenceClient.get_accessible_resources("bad-token")

    @pytest.mark.asyncio
    @patch("app.sources.client.confluence.confluence.HTTPClient")
    async def test_non_list_response_raises(self, mock_http_cls):
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json.return_value = {"error": "bad"}
        mock_instance = AsyncMock()
        mock_instance.execute = AsyncMock(return_value=mock_response)
        mock_http_cls.return_value = mock_instance

        with pytest.raises(Exception, match="Expected list"):
            await ConfluenceClient.get_accessible_resources("token")

    @pytest.mark.asyncio
    @patch("app.sources.client.confluence.confluence.HTTPClient")
    async def test_json_parse_error(self, mock_http_cls):
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json.side_effect = Exception("parse error")
        mock_response.text.return_value = "not json"
        mock_instance = AsyncMock()
        mock_instance.execute = AsyncMock(return_value=mock_response)
        mock_http_cls.return_value = mock_instance

        with pytest.raises(Exception, match="Failed to parse JSON"):
            await ConfluenceClient.get_accessible_resources("token")


# ---------------------------------------------------------------------------
# get_cloud_id
# ---------------------------------------------------------------------------


class TestGetCloudId:
    @pytest.mark.asyncio
    @patch.object(ConfluenceClient, "get_accessible_resources")
    async def test_returns_first_id(self, mock_res):
        resource = MagicMock()
        resource.id = "c-1"
        mock_res.return_value = [resource]
        assert await ConfluenceClient.get_cloud_id("tok") == "c-1"

    @pytest.mark.asyncio
    @patch.object(ConfluenceClient, "get_accessible_resources")
    async def test_no_resources_raises(self, mock_res):
        mock_res.return_value = []
        with pytest.raises(Exception, match="No accessible resources"):
            await ConfluenceClient.get_cloud_id("tok")


# ---------------------------------------------------------------------------
# get_confluence_base_url
# ---------------------------------------------------------------------------


class TestGetConfluenceBaseUrl:
    @pytest.mark.asyncio
    @patch.object(ConfluenceClient, "get_cloud_id", new_callable=AsyncMock, return_value="c-1")
    async def test_returns_correct_url(self, _):
        url = await ConfluenceClient.get_confluence_base_url("tok")
        assert url == "https://api.atlassian.com/ex/confluence/c-1/wiki/api/v2"


# ---------------------------------------------------------------------------
# _get_connector_config
# ---------------------------------------------------------------------------


class TestGetConnectorConfig:
    @pytest.mark.asyncio
    async def test_returns_config(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value={"auth": {}})
        result = await ConfluenceClient._get_connector_config(logger, mock_config_service, "inst-1")
        assert result == {"auth": {}}

    @pytest.mark.asyncio
    async def test_empty_config_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Failed to get Confluence"):
            await ConfluenceClient._get_connector_config(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_exception_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(side_effect=RuntimeError("boom"))
        with pytest.raises(ValueError, match="Failed to get Confluence"):
            await ConfluenceClient._get_connector_config(logger, mock_config_service, "inst-1")


# ---------------------------------------------------------------------------
# build_from_services
# ---------------------------------------------------------------------------


class TestBuildFromServices:
    @pytest.mark.asyncio
    @patch.object(ConfluenceClient, "get_confluence_base_url", new_callable=AsyncMock, return_value="http://base")
    async def test_bearer_token(self, _, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "BEARER_TOKEN", "bearerToken": "tok"},
                "credentials": {"x": "y"},
            }
        )
        cc = await ConfluenceClient.build_from_services(logger, mock_config_service, "inst-1")
        assert isinstance(cc, ConfluenceClient)

    @pytest.mark.asyncio
    @patch.object(ConfluenceClient, "get_confluence_base_url", new_callable=AsyncMock, return_value="http://base")
    async def test_oauth(self, _, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "OAUTH"},
                "credentials": {"access_token": "oauth-tok"},
            }
        )
        cc = await ConfluenceClient.build_from_services(logger, mock_config_service, "inst-1")
        assert isinstance(cc, ConfluenceClient)

    @pytest.mark.asyncio
    async def test_no_config_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError):
            await ConfluenceClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_no_auth_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={"auth": None, "credentials": {"x": "y"}}
        )
        with pytest.raises(ValueError, match="Auth configuration not found"):
            await ConfluenceClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_no_credentials_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={"auth": {"authType": "BEARER_TOKEN"}, "credentials": None}
        )
        with pytest.raises(ValueError, match="Token required for token auth type"):
            await ConfluenceClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_missing_bearer_token_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "BEARER_TOKEN"},
                "credentials": {"x": "y"},
            }
        )
        with pytest.raises(ValueError, match="Token required"):
            await ConfluenceClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_invalid_auth_type_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "UNSUPPORTED"},
                "credentials": {"x": "y"},
            }
        )
        with pytest.raises(ValueError, match="Invalid auth type"):
            await ConfluenceClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_oauth_missing_token_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "OAUTH"},
                "credentials": {"some_key": "some_val"},
            }
        )
        with pytest.raises(ValueError, match="Access token required"):
            await ConfluenceClient.build_from_services(logger, mock_config_service, "inst-1")


# ---------------------------------------------------------------------------
# build_from_toolset
# ---------------------------------------------------------------------------


class TestBuildFromToolset:
    @pytest.mark.asyncio
    async def test_empty_config_raises(self, logger):
        with pytest.raises(ValueError, match="Toolset config is required"):
            await ConfluenceClient.build_from_toolset({}, logger)

    @pytest.mark.asyncio
    @patch.object(ConfluenceClient, "get_confluence_base_url", new_callable=AsyncMock, return_value="http://base")
    async def test_bearer_token_success(self, _, logger):
        cc = await ConfluenceClient.build_from_toolset(
            {"authType": "BEARER_TOKEN", "bearerToken": "tok", "credentials": {}},
            logger,
        )
        assert isinstance(cc, ConfluenceClient)

    @pytest.mark.asyncio
    async def test_bearer_token_missing_raises(self, logger):
        with pytest.raises(ValueError, match="Token required"):
            await ConfluenceClient.build_from_toolset(
                {"authType": "BEARER_TOKEN", "credentials": {}}, logger
            )

    @pytest.mark.asyncio
    @patch.object(ConfluenceClient, "get_confluence_base_url", new_callable=AsyncMock, return_value="http://base")
    async def test_oauth_success(self, _, logger):
        cc = await ConfluenceClient.build_from_toolset(
            {"authType": "OAUTH", "credentials": {"access_token": "tok"}},
            logger,
        )
        assert isinstance(cc, ConfluenceClient)

    @pytest.mark.asyncio
    async def test_oauth_missing_token_raises(self, logger):
        with pytest.raises(ValueError, match="Access token required"):
            await ConfluenceClient.build_from_toolset(
                {"authType": "OAUTH", "credentials": {}}, logger
            )

    @pytest.mark.asyncio
    async def test_invalid_auth_type_raises(self, logger):
        with pytest.raises(ValueError, match="Invalid auth type"):
            await ConfluenceClient.build_from_toolset(
                {"authType": "UNSUPPORTED", "credentials": {}}, logger
            )
