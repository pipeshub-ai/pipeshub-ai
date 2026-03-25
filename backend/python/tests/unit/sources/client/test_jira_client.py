"""Unit tests for Jira client module."""

import logging
from dataclasses import asdict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sources.client.jira.jira import (
    JiraApiKeyConfig,
    JiraClient,
    JiraRESTClientViaApiKey,
    JiraRESTClientViaToken,
    JiraRESTClientViaUsernamePassword,
    JiraTokenConfig,
    JiraUsernamePasswordConfig,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def logger():
    return logging.getLogger("test_jira_client")


@pytest.fixture
def mock_config_service():
    return AsyncMock()


# ---------------------------------------------------------------------------
# REST client classes
# ---------------------------------------------------------------------------


class TestJiraRESTClientViaUsernamePassword:
    def test_init_stores_base_url(self):
        client = JiraRESTClientViaUsernamePassword("http://jira.local", "user", "pass")
        assert client.base_url == "http://jira.local"

    def test_get_base_url(self):
        client = JiraRESTClientViaUsernamePassword("http://jira.local", "u", "p")
        assert client.get_base_url() == "http://jira.local"


class TestJiraRESTClientViaApiKey:
    def test_init_stores_base_url(self):
        client = JiraRESTClientViaApiKey("http://jira.local", "e@e.com", "key")
        assert client.base_url == "http://jira.local"

    def test_get_base_url(self):
        client = JiraRESTClientViaApiKey("http://jira.local", "e@e.com", "key")
        assert client.get_base_url() == "http://jira.local"


class TestJiraRESTClientViaToken:
    def test_init(self):
        client = JiraRESTClientViaToken("http://jira.local", "tok")
        assert client.base_url == "http://jira.local"
        assert client.token == "tok"
        assert client.token_type == "Bearer"

    def test_get_base_url(self):
        client = JiraRESTClientViaToken("http://jira.local", "tok")
        assert client.get_base_url() == "http://jira.local"

    def test_get_token(self):
        client = JiraRESTClientViaToken("http://jira.local", "tok")
        assert client.get_token() == "tok"

    def test_set_token(self):
        client = JiraRESTClientViaToken("http://jira.local", "tok")
        client.set_token("new-tok")
        assert client.token == "new-tok"
        assert client.headers["Authorization"] == "Bearer new-tok"

    def test_custom_token_type(self):
        client = JiraRESTClientViaToken("http://jira.local", "tok", "Custom")
        assert client.token_type == "Custom"


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------


class TestJiraUsernamePasswordConfig:
    def test_create_client(self):
        cfg = JiraUsernamePasswordConfig("http://jira.local", "u", "p")
        client = cfg.create_client()
        assert isinstance(client, JiraRESTClientViaUsernamePassword)

    def test_to_dict(self):
        cfg = JiraUsernamePasswordConfig("http://jira.local", "u", "p")
        d = cfg.to_dict()
        assert d == {"base_url": "http://jira.local", "username": "u", "password": "p", "ssl": False}

    def test_ssl_default(self):
        cfg = JiraUsernamePasswordConfig("http://jira.local", "u", "p")
        assert cfg.ssl is False


class TestJiraTokenConfig:
    def test_create_client(self):
        cfg = JiraTokenConfig("http://jira.local", "tok")
        client = cfg.create_client()
        assert isinstance(client, JiraRESTClientViaToken)

    def test_to_dict(self):
        cfg = JiraTokenConfig("http://jira.local", "tok")
        d = cfg.to_dict()
        assert d["token"] == "tok"
        assert d["ssl"] is False


class TestJiraApiKeyConfig:
    def test_create_client(self):
        cfg = JiraApiKeyConfig("http://jira.local", "e@e.com", "key")
        client = cfg.create_client()
        assert isinstance(client, JiraRESTClientViaApiKey)

    def test_to_dict(self):
        cfg = JiraApiKeyConfig("http://jira.local", "e@e.com", "key")
        d = cfg.to_dict()
        assert d["email"] == "e@e.com"
        assert d["api_key"] == "key"


# ---------------------------------------------------------------------------
# JiraClient init / get_client
# ---------------------------------------------------------------------------


class TestJiraClientInit:
    def test_init_and_get_client(self):
        mock_client = MagicMock()
        jc = JiraClient(mock_client)
        assert jc.get_client() is mock_client


# ---------------------------------------------------------------------------
# build_with_config
# ---------------------------------------------------------------------------


class TestBuildWithConfig:
    def test_token_config(self):
        cfg = JiraTokenConfig("http://jira.local", "tok")
        jc = JiraClient.build_with_config(cfg)
        assert isinstance(jc, JiraClient)
        assert isinstance(jc.get_client(), JiraRESTClientViaToken)

    def test_username_password_config(self):
        cfg = JiraUsernamePasswordConfig("http://jira.local", "u", "p")
        jc = JiraClient.build_with_config(cfg)
        assert isinstance(jc.get_client(), JiraRESTClientViaUsernamePassword)

    def test_api_key_config(self):
        cfg = JiraApiKeyConfig("http://jira.local", "e@e.com", "key")
        jc = JiraClient.build_with_config(cfg)
        assert isinstance(jc.get_client(), JiraRESTClientViaApiKey)


# ---------------------------------------------------------------------------
# get_accessible_resources
# ---------------------------------------------------------------------------


class TestGetAccessibleResources:
    @pytest.mark.asyncio
    async def test_empty_token_raises(self):
        with pytest.raises(ValueError, match="No token provided"):
            await JiraClient.get_accessible_resources("")

    @pytest.mark.asyncio
    @patch("app.sources.client.jira.jira.HTTPClient")
    async def test_success(self, mock_http_cls):
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json.return_value = [
            {"id": "cloud-1", "name": "Site 1", "url": "https://site1.atlassian.net", "scopes": ["read"], "avatarUrl": "http://avatar.png"},
        ]
        mock_instance = AsyncMock()
        mock_instance.execute = AsyncMock(return_value=mock_response)
        mock_instance.close = AsyncMock()
        mock_http_cls.return_value = mock_instance

        resources = await JiraClient.get_accessible_resources("valid-token")
        assert len(resources) == 1
        assert resources[0].id == "cloud-1"
        assert resources[0].name == "Site 1"

    @pytest.mark.asyncio
    @patch("app.sources.client.jira.jira.HTTPClient")
    async def test_non_200_raises(self, mock_http_cls):
        mock_response = MagicMock()
        mock_response.status = 401
        mock_response.text.return_value = "Unauthorized"
        mock_instance = AsyncMock()
        mock_instance.execute = AsyncMock(return_value=mock_response)
        mock_instance.close = AsyncMock()
        mock_http_cls.return_value = mock_instance

        with pytest.raises(Exception, match="Failed to fetch accessible resources"):
            await JiraClient.get_accessible_resources("bad-token")

    @pytest.mark.asyncio
    @patch("app.sources.client.jira.jira.HTTPClient")
    async def test_non_list_response_raises(self, mock_http_cls):
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json.return_value = {"error": "bad"}
        mock_instance = AsyncMock()
        mock_instance.execute = AsyncMock(return_value=mock_response)
        mock_instance.close = AsyncMock()
        mock_http_cls.return_value = mock_instance

        with pytest.raises(Exception, match="Expected list of resources"):
            await JiraClient.get_accessible_resources("token")

    @pytest.mark.asyncio
    @patch("app.sources.client.jira.jira.HTTPClient")
    async def test_json_parse_error(self, mock_http_cls):
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json.side_effect = Exception("parse error")
        mock_response.text.return_value = "not json"
        mock_instance = AsyncMock()
        mock_instance.execute = AsyncMock(return_value=mock_response)
        mock_instance.close = AsyncMock()
        mock_http_cls.return_value = mock_instance

        with pytest.raises(Exception, match="Failed to parse JSON"):
            await JiraClient.get_accessible_resources("token")


# ---------------------------------------------------------------------------
# get_cloud_id
# ---------------------------------------------------------------------------


class TestGetCloudId:
    @pytest.mark.asyncio
    @patch.object(JiraClient, "get_accessible_resources")
    async def test_returns_first_id(self, mock_resources):
        resource = MagicMock()
        resource.id = "cloud-1"
        mock_resources.return_value = [resource]
        result = await JiraClient.get_cloud_id("tok")
        assert result == "cloud-1"

    @pytest.mark.asyncio
    @patch.object(JiraClient, "get_accessible_resources")
    async def test_no_resources_raises(self, mock_resources):
        mock_resources.return_value = []
        with pytest.raises(Exception, match="No accessible resources"):
            await JiraClient.get_cloud_id("tok")


# ---------------------------------------------------------------------------
# get_jira_base_url
# ---------------------------------------------------------------------------


class TestGetJiraBaseUrl:
    @pytest.mark.asyncio
    @patch.object(JiraClient, "get_cloud_id", new_callable=AsyncMock, return_value="cloud-1")
    async def test_returns_correct_url(self, _):
        url = await JiraClient.get_jira_base_url("tok")
        assert url == "https://api.atlassian.com/ex/jira/cloud-1"


# ---------------------------------------------------------------------------
# _get_connector_config
# ---------------------------------------------------------------------------


class TestGetConnectorConfig:
    @pytest.mark.asyncio
    async def test_returns_config(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value={"auth": {}})
        result = await JiraClient._get_connector_config(logger, mock_config_service, "inst-1")
        assert result == {"auth": {}}

    @pytest.mark.asyncio
    async def test_empty_config_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Failed to get Jira connector"):
            await JiraClient._get_connector_config(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_exception_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(side_effect=RuntimeError("boom"))
        with pytest.raises(ValueError, match="Failed to get Jira connector"):
            await JiraClient._get_connector_config(logger, mock_config_service, "inst-1")


# ---------------------------------------------------------------------------
# build_from_services
# ---------------------------------------------------------------------------


class TestBuildFromServices:
    @pytest.mark.asyncio
    @patch.object(JiraClient, "get_jira_base_url", new_callable=AsyncMock, return_value="http://jira-base")
    async def test_bearer_token(self, _, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "BEARER_TOKEN", "bearerToken": "tok"},
                "credentials": {"something": "x"},
            }
        )
        jc = await JiraClient.build_from_services(logger, mock_config_service, "inst-1")
        assert isinstance(jc, JiraClient)

    @pytest.mark.asyncio
    @patch.object(JiraClient, "get_jira_base_url", new_callable=AsyncMock, return_value="http://jira-base")
    async def test_oauth(self, _, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "OAUTH"},
                "credentials": {"access_token": "oauth-tok"},
            }
        )
        jc = await JiraClient.build_from_services(logger, mock_config_service, "inst-1")
        assert isinstance(jc, JiraClient)

    @pytest.mark.asyncio
    async def test_no_config_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError):
            await JiraClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_no_auth_config_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={"auth": None, "credentials": {"x": "y"}}
        )
        with pytest.raises(ValueError, match="Auth configuration not found"):
            await JiraClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_no_credentials_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={"auth": {"authType": "BEARER_TOKEN"}, "credentials": None}
        )
        with pytest.raises(ValueError, match="Token required for token auth type"):
            await JiraClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_missing_bearer_token_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "BEARER_TOKEN"},
                "credentials": {"x": "y"},
            }
        )
        with pytest.raises(ValueError, match="Token required"):
            await JiraClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_invalid_auth_type_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "UNSUPPORTED"},
                "credentials": {"x": "y"},
            }
        )
        with pytest.raises(ValueError, match="Invalid auth type"):
            await JiraClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_oauth_missing_token_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "OAUTH"},
                "credentials": {"some_key": "some_val"},
            }
        )
        with pytest.raises(ValueError, match="Access token required"):
            await JiraClient.build_from_services(logger, mock_config_service, "inst-1")


# ---------------------------------------------------------------------------
# build_from_toolset
# ---------------------------------------------------------------------------


class TestBuildFromToolset:
    @pytest.mark.asyncio
    async def test_empty_config_raises(self, logger):
        with pytest.raises(ValueError, match="Toolset configuration is required"):
            await JiraClient.build_from_toolset({}, logger)

    @pytest.mark.asyncio
    async def test_not_authenticated_raises(self, logger):
        with pytest.raises(ValueError, match="not authenticated"):
            await JiraClient.build_from_toolset(
                {"isAuthenticated": False, "authType": "OAUTH"}, logger
            )

    @pytest.mark.asyncio
    @patch.object(JiraClient, "get_jira_base_url", new_callable=AsyncMock, return_value="http://jira-base")
    async def test_oauth_success(self, _, logger):
        jc = await JiraClient.build_from_toolset(
            {"isAuthenticated": True, "authType": "OAUTH", "credentials": {"access_token": "tok"}},
            logger,
        )
        assert isinstance(jc, JiraClient)

    @pytest.mark.asyncio
    async def test_oauth_missing_token_raises(self, logger):
        with pytest.raises(ValueError, match="Access token not found"):
            await JiraClient.build_from_toolset(
                {"isAuthenticated": True, "authType": "OAUTH", "credentials": {}},
                logger,
            )

    @pytest.mark.asyncio
    async def test_unsupported_auth_type_raises(self, logger):
        with pytest.raises(ValueError, match="Unsupported auth type"):
            await JiraClient.build_from_toolset(
                {"isAuthenticated": True, "authType": "UNSUPPORTED", "credentials": {}},
                logger,
            )
