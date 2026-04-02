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
import base64
from app.sources.external.common.atlassian import AtlassianCloudResource


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

# =============================================================================
# Merged from test_jira_client_coverage.py
# =============================================================================

@pytest.fixture
def log():
    lg = logging.getLogger("test_jira")
    lg.setLevel(logging.CRITICAL)
    return lg


# ============================================================================
# REST Client Classes
# ============================================================================
class TestJiraRESTClientViaUsernamePasswordCoverage:
    def test_init(self):
        client = JiraRESTClientViaUsernamePassword("https://jira.example.com", "user", "pass")
        assert client.base_url == "https://jira.example.com"

    def test_get_base_url(self):
        client = JiraRESTClientViaUsernamePassword("https://jira.example.com", "user", "pass")
        assert client.get_base_url() == "https://jira.example.com"


class TestJiraRESTClientViaApiKeyCoverage:
    def test_init(self):
        client = JiraRESTClientViaApiKey("https://jira.example.com", "user@test.com", "api_key")
        assert client.base_url == "https://jira.example.com"
        assert client.email == "user@test.com"
        expected = base64.b64encode(b"user@test.com:api_key").decode()
        assert client.headers["Authorization"] == f"Basic {expected}"

    def test_get_base_url(self):
        client = JiraRESTClientViaApiKey("https://jira.example.com", "user@test.com", "key")
        assert client.get_base_url() == "https://jira.example.com"


class TestJiraRESTClientViaTokenCoverage:
    def test_init(self):
        client = JiraRESTClientViaToken("https://jira.example.com", "token123")
        assert client.base_url == "https://jira.example.com"
        assert client.token == "token123"

    def test_get_base_url(self):
        client = JiraRESTClientViaToken("https://jira.example.com", "token123")
        assert client.get_base_url() == "https://jira.example.com"

    def test_get_token(self):
        client = JiraRESTClientViaToken("https://jira.example.com", "token123")
        assert client.get_token() == "token123"

    def test_set_token(self):
        client = JiraRESTClientViaToken("https://jira.example.com", "old")
        client.set_token("new")
        assert client.token == "new"
        assert client.headers["Authorization"] == "Bearer new"

    def test_custom_token_type(self):
        client = JiraRESTClientViaToken("https://jira.example.com", "tok", "CustomType")
        assert client.token_type == "CustomType"
        client.set_token("new_tok")
        assert client.headers["Authorization"] == "CustomType new_tok"


# ============================================================================
# Config Dataclasses
# ============================================================================
class TestJiraUsernamePasswordConfigCoverage:
    def test_create_client(self):
        config = JiraUsernamePasswordConfig(base_url="https://j.com", username="u", password="p")
        client = config.create_client()
        assert isinstance(client, JiraRESTClientViaUsernamePassword)

    def test_to_dict(self):
        config = JiraUsernamePasswordConfig(base_url="https://j.com", username="u", password="p")
        d = config.to_dict()
        assert d["base_url"] == "https://j.com"
        assert d["ssl"] is False


class TestJiraTokenConfigCoverage:
    def test_create_client(self):
        config = JiraTokenConfig(base_url="https://j.com", token="tok")
        client = config.create_client()
        assert isinstance(client, JiraRESTClientViaToken)

    def test_to_dict(self):
        config = JiraTokenConfig(base_url="https://j.com", token="tok")
        d = config.to_dict()
        assert d["token"] == "tok"


class TestJiraApiKeyConfigCoverage:
    def test_create_client(self):
        config = JiraApiKeyConfig(base_url="https://j.com", email="u@t.com", api_key="k")
        client = config.create_client()
        assert isinstance(client, JiraRESTClientViaApiKey)

    def test_to_dict(self):
        config = JiraApiKeyConfig(base_url="https://j.com", email="u@t.com", api_key="k")
        d = config.to_dict()
        assert d["email"] == "u@t.com"


# ============================================================================
# JiraClient
# ============================================================================
class TestJiraClient:
    def test_init_and_get_client(self):
        mock = MagicMock()
        jc = JiraClient(mock)
        assert jc.get_client() is mock

    def test_build_with_config(self):
        config = JiraTokenConfig(base_url="https://j.com", token="tok")
        jc = JiraClient.build_with_config(config)
        assert isinstance(jc, JiraClient)
        assert isinstance(jc.get_client(), JiraRESTClientViaToken)


class TestJiraGetAccessibleResources:
    @pytest.mark.asyncio
    async def test_no_token(self):
        with pytest.raises(ValueError, match="No token provided"):
            await JiraClient.get_accessible_resources("")

    @pytest.mark.asyncio
    async def test_success(self):
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json.return_value = [
            {"id": "c1", "name": "Site", "url": "https://site.atlassian.net", "scopes": []},
        ]
        with patch("app.sources.client.jira.jira.HTTPClient") as MockHTTP:
            instance = MockHTTP.return_value
            instance.execute = AsyncMock(return_value=mock_response)
            instance.close = AsyncMock()
            resources = await JiraClient.get_accessible_resources("tok")
            assert len(resources) == 1
            assert resources[0].id == "c1"
            instance.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_non_200(self):
        mock_response = MagicMock()
        mock_response.status = 403
        mock_response.text.return_value = "Forbidden"
        with patch("app.sources.client.jira.jira.HTTPClient") as MockHTTP:
            instance = MockHTTP.return_value
            instance.execute = AsyncMock(return_value=mock_response)
            instance.close = AsyncMock()
            with pytest.raises(Exception, match="Failed to fetch"):
                await JiraClient.get_accessible_resources("tok")

    @pytest.mark.asyncio
    async def test_invalid_json(self):
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json.side_effect = Exception("bad json")
        mock_response.text.return_value = "not json"
        with patch("app.sources.client.jira.jira.HTTPClient") as MockHTTP:
            instance = MockHTTP.return_value
            instance.execute = AsyncMock(return_value=mock_response)
            instance.close = AsyncMock()
            with pytest.raises(Exception, match="Failed to fetch"):
                await JiraClient.get_accessible_resources("tok")

    @pytest.mark.asyncio
    async def test_not_a_list(self):
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json.return_value = {"error": "something"}
        with patch("app.sources.client.jira.jira.HTTPClient") as MockHTTP:
            instance = MockHTTP.return_value
            instance.execute = AsyncMock(return_value=mock_response)
            instance.close = AsyncMock()
            with pytest.raises(Exception, match="Failed to fetch"):
                await JiraClient.get_accessible_resources("tok")


class TestJiraGetCloudId:
    @pytest.mark.asyncio
    async def test_success(self):
        with patch.object(JiraClient, "get_accessible_resources", return_value=[
            AtlassianCloudResource(id="c1", name="Site", url="https://s.com", scopes=[]),
        ]):
            assert await JiraClient.get_cloud_id("tok") == "c1"

    @pytest.mark.asyncio
    async def test_no_resources(self):
        with patch.object(JiraClient, "get_accessible_resources", return_value=[]):
            with pytest.raises(Exception, match="No accessible resources"):
                await JiraClient.get_cloud_id("tok")


class TestJiraGetBaseUrl:
    @pytest.mark.asyncio
    async def test_success(self):
        with patch.object(JiraClient, "get_cloud_id", return_value="c1"):
            url = await JiraClient.get_jira_base_url("tok")
            assert url == "https://api.atlassian.com/ex/jira/c1"


class TestJiraBuildFromServices:
    @pytest.mark.asyncio
    async def test_api_token(self, log):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={
            "auth": {"authType": "API_TOKEN", "baseUrl": "https://jira.example.com/", "email": "u@t.com", "apiToken": "tok"},
        })
        jc = await JiraClient.build_from_services(log, cs, "inst1")
        assert isinstance(jc.get_client(), JiraRESTClientViaApiKey)
        # trailing slash stripped
        assert jc.get_client().base_url == "https://jira.example.com"

    @pytest.mark.asyncio
    async def test_bearer_token(self, log):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={
            "auth": {"authType": "BEARER_TOKEN", "bearerToken": "bearer_tok"},
        })
        with patch.object(JiraClient, "get_jira_base_url", return_value="https://api.atlassian.com/ex/jira/c1"):
            jc = await JiraClient.build_from_services(log, cs, "inst1")
            assert isinstance(jc.get_client(), JiraRESTClientViaToken)

    @pytest.mark.asyncio
    async def test_oauth(self, log):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={
            "auth": {"authType": "OAUTH"},
            "credentials": {"access_token": "oauth_tok"},
        })
        with patch.object(JiraClient, "get_jira_base_url", return_value="https://api.atlassian.com/ex/jira/c1"):
            jc = await JiraClient.build_from_services(log, cs, "inst1")
            assert isinstance(jc.get_client(), JiraRESTClientViaToken)

    @pytest.mark.asyncio
    async def test_invalid_auth_type(self, log):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={"auth": {"authType": "UNKNOWN"}})
        with pytest.raises(ValueError, match="Invalid auth type"):
            await JiraClient.build_from_services(log, cs, "inst1")

    @pytest.mark.asyncio
    async def test_no_config(self, log):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError):
            await JiraClient.build_from_services(log, cs, "inst1")

    @pytest.mark.asyncio
    async def test_no_auth(self, log):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={"auth": None})
        with pytest.raises(ValueError, match="Auth configuration not found"):
            await JiraClient.build_from_services(log, cs, "inst1")

    @pytest.mark.asyncio
    async def test_api_token_missing_base_url(self, log):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={
            "auth": {"authType": "API_TOKEN", "baseUrl": "", "email": "u@t.com", "apiToken": "tok"},
        })
        with pytest.raises(ValueError, match="Base URL is required"):
            await JiraClient.build_from_services(log, cs, "inst1")

    @pytest.mark.asyncio
    async def test_api_token_missing_email(self, log):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={
            "auth": {"authType": "API_TOKEN", "baseUrl": "https://j.com", "email": "", "apiToken": "tok"},
        })
        with pytest.raises(ValueError, match="Email and API token"):
            await JiraClient.build_from_services(log, cs, "inst1")

    @pytest.mark.asyncio
    async def test_bearer_token_missing(self, log):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={
            "auth": {"authType": "BEARER_TOKEN", "bearerToken": ""},
        })
        with pytest.raises(ValueError, match="Token required"):
            await JiraClient.build_from_services(log, cs, "inst1")

    @pytest.mark.asyncio
    async def test_oauth_missing_credentials(self, log):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={
            "auth": {"authType": "OAUTH"}, "credentials": None,
        })
        with pytest.raises(ValueError, match="Credentials configuration not found"):
            await JiraClient.build_from_services(log, cs, "inst1")

    @pytest.mark.asyncio
    async def test_oauth_missing_access_token(self, log):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={
            "auth": {"authType": "OAUTH"}, "credentials": {"access_token": ""},
        })
        with pytest.raises(ValueError, match="Access token required"):
            await JiraClient.build_from_services(log, cs, "inst1")


class TestJiraBuildFromToolset:
    @pytest.mark.asyncio
    async def test_empty_config_raises(self, log):
        with pytest.raises(ValueError, match="Toolset configuration is required"):
            await JiraClient.build_from_toolset({}, log)

    @pytest.mark.asyncio
    async def test_none_config_raises(self, log):
        with pytest.raises(ValueError, match="Toolset configuration is required"):
            await JiraClient.build_from_toolset(None, log)

    @pytest.mark.asyncio
    async def test_not_authenticated(self, log):
        config = {"isAuthenticated": False, "authType": "OAUTH"}
        with pytest.raises(ValueError, match="not authenticated"):
            await JiraClient.build_from_toolset(config, log)

    @pytest.mark.asyncio
    async def test_oauth(self, log):
        config = {
            "isAuthenticated": True,
            "authType": "OAUTH",
            "credentials": {"access_token": "oauth_tok"},
        }
        with patch.object(JiraClient, "get_jira_base_url", return_value="https://api.atlassian.com/ex/jira/c1"):
            jc = await JiraClient.build_from_toolset(config, log)
            assert isinstance(jc.get_client(), JiraRESTClientViaToken)

    @pytest.mark.asyncio
    async def test_oauth_no_token(self, log):
        config = {
            "isAuthenticated": True,
            "authType": "OAUTH",
            "credentials": {"access_token": ""},
        }
        with pytest.raises(ValueError, match="Access token not found"):
            await JiraClient.build_from_toolset(config, log)

    @pytest.mark.asyncio
    async def test_api_token(self, log):
        cs = AsyncMock()
        with patch("app.sources.client.jira.jira.get_toolset_by_id", return_value={
            "auth": {"baseUrl": "https://jira.example.com"},
        }):
            config = {
                "isAuthenticated": True,
                "authType": "API_TOKEN",
                "instanceId": "inst1",
                "auth": {"email": "u@t.com", "apiToken": "tok"},
            }
            jc = await JiraClient.build_from_toolset(config, log, cs)
            assert isinstance(jc.get_client(), JiraRESTClientViaApiKey)

    @pytest.mark.asyncio
    async def test_api_token_no_instance_id(self, log):
        config = {"isAuthenticated": True, "authType": "API_TOKEN"}
        with pytest.raises(ValueError, match="instanceId is required"):
            await JiraClient.build_from_toolset(config, log)

    @pytest.mark.asyncio
    async def test_api_token_no_config_service(self, log):
        config = {"isAuthenticated": True, "authType": "API_TOKEN", "instanceId": "inst1"}
        with pytest.raises(ValueError, match="config_service is required"):
            await JiraClient.build_from_toolset(config, log, None)

    @pytest.mark.asyncio
    async def test_api_token_instance_not_found(self, log):
        cs = AsyncMock()
        with patch("app.sources.client.jira.jira.get_toolset_by_id", return_value=None):
            config = {"isAuthenticated": True, "authType": "API_TOKEN", "instanceId": "inst1"}
            with pytest.raises(ValueError, match="not found"):
                await JiraClient.build_from_toolset(config, log, cs)

    @pytest.mark.asyncio
    async def test_unsupported_auth_type(self, log):
        config = {"isAuthenticated": True, "authType": "SAML"}
        with pytest.raises(ValueError, match="Unsupported auth type"):
            await JiraClient.build_from_toolset(config, log)

    @pytest.mark.asyncio
    async def test_api_token_missing_base_url(self, log):
        cs = AsyncMock()
        with patch("app.sources.client.jira.jira.get_toolset_by_id", return_value={
            "auth": {"baseUrl": ""},
        }):
            config = {
                "isAuthenticated": True,
                "authType": "API_TOKEN",
                "instanceId": "inst1",
                "auth": {"email": "u@t.com", "apiToken": "tok"},
            }
            with pytest.raises(ValueError, match="Base URL is required"):
                await JiraClient.build_from_toolset(config, log, cs)

    @pytest.mark.asyncio
    async def test_api_token_missing_email(self, log):
        cs = AsyncMock()
        with patch("app.sources.client.jira.jira.get_toolset_by_id", return_value={
            "auth": {"baseUrl": "https://jira.com"},
        }):
            config = {
                "isAuthenticated": True,
                "authType": "API_TOKEN",
                "instanceId": "inst1",
                "auth": {"email": "", "apiToken": "tok"},
            }
            with pytest.raises(ValueError, match="Email and API token"):
                await JiraClient.build_from_toolset(config, log, cs)


class TestJiraGetConnectorConfig:
    @pytest.mark.asyncio
    async def test_success(self, log):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={"auth": {}})
        result = await JiraClient._get_connector_config(log, cs, "inst1")
        assert "auth" in result

    @pytest.mark.asyncio
    async def test_empty_config(self, log):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Failed to get Jira"):
            await JiraClient._get_connector_config(log, cs, "inst1")

    @pytest.mark.asyncio
    async def test_exception(self, log):
        cs = AsyncMock()
        cs.get_config = AsyncMock(side_effect=Exception("etcd down"))
        with pytest.raises(ValueError, match="Failed to get Jira"):
            await JiraClient._get_connector_config(log, cs, "inst1")
