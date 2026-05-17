"""Deep coverage tests for app.sources.client.confluence.confluence.

Missing lines from coverage report (94.4%):
- 263: build_from_services - empty auth_config after `config.get("auth",{}) or {}`
- 299: build_from_services BEARER_TOKEN - base_url is empty (after get_confluence_base_url)
- 314: build_from_services OAUTH - base_url is empty
- 365: build_from_toolset BEARER_TOKEN - base_url is empty
- 377: build_from_toolset OAUTH - base_url is empty
- 406: build_from_toolset API_TOKEN - missing base_url from instance config
- 408: build_from_toolset API_TOKEN - empty email uses PAT client; empty apiToken raises
- 412->415: build_from_toolset API_TOKEN - base_url normalization (append /wiki/api/v2)
"""

import logging
from unittest.mock import AsyncMock, patch

import pytest

from app.sources.client.confluence.confluence import (
    ConfluenceClient,
    ConfluenceRESTClientViaApiKey,
    ConfluenceRESTClientViaToken,
)


@pytest.fixture
def log():
    return logging.getLogger("test_confluence_deep")


@pytest.fixture
def mock_config_service():
    return AsyncMock()


# ============================================================================
# build_from_services - Line 263: auth_config is empty/falsy after extraction
# ============================================================================

class TestBuildFromServicesEmptyAuth:
    @pytest.mark.asyncio
    async def test_auth_config_empty_dict(self, log, mock_config_service):
        """When auth config is empty dict {}, raise ValueError about auth config."""
        mock_config_service.get_config = AsyncMock(return_value={"auth": {}})
        with pytest.raises(ValueError, match="Auth configuration not found"):
            await ConfluenceClient.build_from_services(log, mock_config_service, "inst1")

    @pytest.mark.asyncio
    async def test_auth_config_none_value(self, log, mock_config_service):
        """When 'auth' key maps to None, raise ValueError."""
        mock_config_service.get_config = AsyncMock(return_value={"auth": None})
        with pytest.raises(ValueError, match="Auth configuration not found"):
            await ConfluenceClient.build_from_services(log, mock_config_service, "inst1")


class TestBuildFromServicesConfigFalsy:
    @pytest.mark.asyncio
    async def test_config_returns_falsy_value(self, log, mock_config_service):
        """When _get_connector_config is mocked to return falsy, line 263 is hit."""
        with patch.object(
            ConfluenceClient, "_get_connector_config",
            new_callable=AsyncMock,
            return_value=None
        ):
            with pytest.raises(ValueError, match="Failed to get Confluence connector"):
                await ConfluenceClient.build_from_services(log, mock_config_service, "inst1")

    @pytest.mark.asyncio
    async def test_config_returns_empty_dict(self, log, mock_config_service):
        """When _get_connector_config is mocked to return empty dict, line 263 is hit."""
        with patch.object(
            ConfluenceClient, "_get_connector_config",
            new_callable=AsyncMock,
            return_value={}
        ):
            with pytest.raises(ValueError, match="Failed to get Confluence connector"):
                await ConfluenceClient.build_from_services(log, mock_config_service, "inst1")


# ============================================================================
# build_from_services - Line 299: BEARER_TOKEN base_url is empty/None
# ============================================================================

class TestBuildFromServicesBearerTokenBaseUrlEmpty:
    @pytest.mark.asyncio
    async def test_bearer_token_empty_base_url(self, log, mock_config_service):
        """When get_confluence_base_url returns empty string, raise ValueError."""
        mock_config_service.get_config = AsyncMock(return_value={
            "auth": {
                "authType": "BEARER_TOKEN",
                "bearerToken": "tok123",
                "baseUrl": "https://acme.atlassian.net",
            },
        })
        with patch.object(
            ConfluenceClient, "get_confluence_base_url",
            new_callable=AsyncMock,
            return_value=""
        ):
            with pytest.raises(ValueError, match="base_url not found"):
                await ConfluenceClient.build_from_services(log, mock_config_service, "inst1")

    @pytest.mark.asyncio
    async def test_bearer_token_none_base_url(self, log, mock_config_service):
        """When get_confluence_base_url returns None, raise ValueError."""
        mock_config_service.get_config = AsyncMock(return_value={
            "auth": {
                "authType": "BEARER_TOKEN",
                "bearerToken": "tok123",
                "baseUrl": "https://acme.atlassian.net",
            },
        })
        with patch.object(
            ConfluenceClient, "get_confluence_base_url",
            new_callable=AsyncMock,
            return_value=None
        ):
            with pytest.raises(ValueError, match="base_url not found"):
                await ConfluenceClient.build_from_services(log, mock_config_service, "inst1")


# ============================================================================
# build_from_services - Line 314: OAUTH base_url is empty/None
# ============================================================================

class TestBuildFromServicesOAuthBaseUrlEmpty:
    @pytest.mark.asyncio
    async def test_oauth_empty_base_url(self, log, mock_config_service):
        """When get_confluence_base_url returns empty string for OAuth, raise ValueError."""
        mock_config_service.get_config = AsyncMock(return_value={
            "auth": {"authType": "OAUTH", "baseUrl": "https://acme.atlassian.net"},
            "credentials": {"access_token": "at"},
        })
        with patch.object(
            ConfluenceClient, "get_confluence_base_url",
            new_callable=AsyncMock,
            return_value=""
        ):
            with pytest.raises(ValueError, match="base_url not found"):
                await ConfluenceClient.build_from_services(log, mock_config_service, "inst1")

    @pytest.mark.asyncio
    async def test_oauth_none_base_url(self, log, mock_config_service):
        """When get_confluence_base_url returns None for OAuth, raise ValueError."""
        mock_config_service.get_config = AsyncMock(return_value={
            "auth": {"authType": "OAUTH", "baseUrl": "https://acme.atlassian.net"},
            "credentials": {"access_token": "at"},
        })
        with patch.object(
            ConfluenceClient, "get_confluence_base_url",
            new_callable=AsyncMock,
            return_value=None
        ):
            with pytest.raises(ValueError, match="base_url not found"):
                await ConfluenceClient.build_from_services(log, mock_config_service, "inst1")


# ============================================================================
# build_from_toolset - Line 365: BEARER_TOKEN base_url empty
# ============================================================================

class TestBuildFromToolsetBearerTokenBaseUrlEmpty:
    @pytest.mark.asyncio
    async def test_bearer_base_url_empty(self, log):
        """When get_confluence_base_url returns empty for toolset BEARER_TOKEN."""
        toolset_config = {
            "authType": "BEARER_TOKEN",
            "bearerToken": "tok123",
            "auth": {"baseUrl": "https://acme.atlassian.net"},
        }
        with patch.object(
            ConfluenceClient, "get_confluence_base_url",
            new_callable=AsyncMock,
            return_value=""
        ):
            with pytest.raises(ValueError, match="base_url not found"):
                await ConfluenceClient.build_from_toolset(toolset_config, log)

    @pytest.mark.asyncio
    async def test_bearer_base_url_none(self, log):
        """When get_confluence_base_url returns None for toolset BEARER_TOKEN."""
        toolset_config = {
            "authType": "BEARER_TOKEN",
            "bearerToken": "tok123",
            "auth": {"baseUrl": "https://acme.atlassian.net"},
        }
        with patch.object(
            ConfluenceClient, "get_confluence_base_url",
            new_callable=AsyncMock,
            return_value=None
        ):
            with pytest.raises(ValueError, match="base_url not found"):
                await ConfluenceClient.build_from_toolset(toolset_config, log)


# ============================================================================
# build_from_toolset - Line 377: OAUTH base_url empty
# ============================================================================

class TestBuildFromToolsetOAuthBaseUrlEmpty:
    @pytest.mark.asyncio
    async def test_oauth_base_url_empty(self, log):
        """When get_confluence_base_url returns empty for toolset OAUTH."""
        toolset_config = {
            "authType": "OAUTH",
            "auth": {"baseUrl": "https://acme.atlassian.net"},
            "credentials": {"access_token": "at"},
        }
        with patch.object(
            ConfluenceClient, "get_confluence_base_url",
            new_callable=AsyncMock,
            return_value=""
        ):
            with pytest.raises(ValueError, match="base_url not found"):
                await ConfluenceClient.build_from_toolset(toolset_config, log)

    @pytest.mark.asyncio
    async def test_oauth_base_url_none(self, log):
        """When get_confluence_base_url returns None for toolset OAUTH."""
        toolset_config = {
            "authType": "OAUTH",
            "auth": {"baseUrl": "https://acme.atlassian.net"},
            "credentials": {"access_token": "at"},
        }
        with patch.object(
            ConfluenceClient, "get_confluence_base_url",
            new_callable=AsyncMock,
            return_value=None
        ):
            with pytest.raises(ValueError, match="base_url not found"):
                await ConfluenceClient.build_from_toolset(toolset_config, log)


# ============================================================================
# build_from_toolset - Lines 406, 408: API_TOKEN missing base_url / email
# ============================================================================

class TestBuildFromToolsetApiTokenMissingFields:
    @pytest.mark.asyncio
    async def test_api_token_missing_base_url_from_instance(self, log, mock_config_service):
        """When instance config has empty baseUrl, raise ValueError."""
        toolset_config = {
            "authType": "API_TOKEN",
            "instanceId": "inst-123",
            "auth": {"email": "user@example.com", "apiToken": "tok"},
        }
        with patch(
            "app.sources.client.confluence.confluence.get_toolset_by_id",
            new_callable=AsyncMock,
            return_value={"auth": {"baseUrl": ""}},
        ):
            with pytest.raises(ValueError, match="Base URL is required"):
                await ConfluenceClient.build_from_toolset(toolset_config, log, mock_config_service)

    @pytest.mark.asyncio
    async def test_api_token_missing_email_from_user_auth(self, log, mock_config_service):
        """When user auth has empty email but token present, use PAT (Bearer) client."""
        toolset_config = {
            "authType": "API_TOKEN",
            "instanceId": "inst-123",
            "auth": {"email": "", "apiToken": "tok"},
        }
        with patch(
            "app.sources.client.confluence.confluence.get_toolset_by_id",
            new_callable=AsyncMock,
            return_value={"auth": {"baseUrl": "https://mysite.atlassian.net"}},
        ):
            client = await ConfluenceClient.build_from_toolset(toolset_config, log, mock_config_service)
            assert isinstance(client.get_client(), ConfluenceRESTClientViaToken)

    @pytest.mark.asyncio
    async def test_api_token_missing_api_token_from_user_auth(self, log, mock_config_service):
        """When user auth has empty apiToken, raise ValueError."""
        toolset_config = {
            "authType": "API_TOKEN",
            "instanceId": "inst-123",
            "auth": {"email": "user@example.com", "apiToken": ""},
        }
        with patch(
            "app.sources.client.confluence.confluence.get_toolset_by_id",
            new_callable=AsyncMock,
            return_value={"auth": {"baseUrl": "https://mysite.atlassian.net"}},
        ):
            with pytest.raises(ValueError, match="API token is required for API_TOKEN auth"):
                await ConfluenceClient.build_from_toolset(toolset_config, log, mock_config_service)


# ============================================================================
# build_from_toolset - Line 412->415: API_TOKEN base_url normalization
# ============================================================================

class TestBuildFromToolsetApiTokenBaseUrlNormalization:
    @pytest.mark.asyncio
    async def test_api_token_appends_wiki_path(self, log, mock_config_service):
        """When base_url does not end with /wiki/api/v2, it gets appended."""
        toolset_config = {
            "authType": "API_TOKEN",
            "instanceId": "inst-123",
            "auth": {"email": "user@example.com", "apiToken": "tok"},
        }
        with patch(
            "app.sources.client.confluence.confluence.get_toolset_by_id",
            new_callable=AsyncMock,
            return_value={"auth": {"baseUrl": "https://mysite.atlassian.net/"}},
        ):
            client = await ConfluenceClient.build_from_toolset(toolset_config, log, mock_config_service)
            assert isinstance(client.get_client(), ConfluenceRESTClientViaApiKey)
            assert client.get_client().get_base_url().endswith("/wiki/api/v2")
            # Verify no double slashes
            assert "//wiki" not in client.get_client().get_base_url()

    @pytest.mark.asyncio
    async def test_api_token_already_has_wiki_path(self, log, mock_config_service):
        """When base_url already ends with /wiki/api/v2, don't append again."""
        toolset_config = {
            "authType": "API_TOKEN",
            "instanceId": "inst-123",
            "auth": {"email": "user@example.com", "apiToken": "tok"},
        }
        with patch(
            "app.sources.client.confluence.confluence.get_toolset_by_id",
            new_callable=AsyncMock,
            return_value={"auth": {"baseUrl": "https://mysite.atlassian.net/wiki/api/v2"}},
        ):
            client = await ConfluenceClient.build_from_toolset(toolset_config, log, mock_config_service)
            assert client.get_client().get_base_url() == "https://mysite.atlassian.net/wiki/api/v2"

    @pytest.mark.asyncio
    async def test_api_token_no_user_auth_dict(self, log, mock_config_service):
        """When 'auth' is missing from toolset_config, user auth is empty → missing api token."""
        toolset_config = {
            "authType": "API_TOKEN",
            "instanceId": "inst-123",
            # No 'auth' key
        }
        with patch(
            "app.sources.client.confluence.confluence.get_toolset_by_id",
            new_callable=AsyncMock,
            return_value={"auth": {"baseUrl": "https://mysite.atlassian.net"}},
        ):
            with pytest.raises(ValueError, match="API token is required for API_TOKEN auth"):
                await ConfluenceClient.build_from_toolset(toolset_config, log, mock_config_service)


# ============================================================================
# build_from_toolset - additional BEARER_TOKEN missing token
# ============================================================================

class TestBuildFromToolsetBearerTokenMissing:
    @pytest.mark.asyncio
    async def test_bearer_token_missing_raises(self, log):
        """When bearerToken is empty, raise ValueError."""
        toolset_config = {"authType": "BEARER_TOKEN", "bearerToken": ""}
        with pytest.raises(ValueError, match="Token required"):
            await ConfluenceClient.build_from_toolset(toolset_config, log)

    @pytest.mark.asyncio
    async def test_bearer_token_not_present(self, log):
        """When bearerToken key is not present, uses default empty string."""
        toolset_config = {"authType": "BEARER_TOKEN"}
        with pytest.raises(ValueError, match="Token required"):
            await ConfluenceClient.build_from_toolset(toolset_config, log)


# ============================================================================
# build_from_toolset - OAUTH missing access_token
# ============================================================================

class TestBuildFromToolsetOAuthMissingToken:
    @pytest.mark.asyncio
    async def test_oauth_no_access_token(self, log):
        """When OAuth credentials has empty access_token, raise ValueError."""
        toolset_config = {"authType": "OAUTH", "credentials": {"access_token": ""}}
        with pytest.raises(ValueError, match="Access token required"):
            await ConfluenceClient.build_from_toolset(toolset_config, log)

    @pytest.mark.asyncio
    async def test_oauth_credentials_empty(self, log):
        """When OAuth credentials dict is empty, raise ValueError."""
        toolset_config = {"authType": "OAUTH", "credentials": {}}
        with pytest.raises(ValueError, match="Access token required"):
            await ConfluenceClient.build_from_toolset(toolset_config, log)

    @pytest.mark.asyncio
    async def test_oauth_credentials_none(self, log):
        """When OAuth credentials is None, raise ValueError."""
        toolset_config = {"authType": "OAUTH", "credentials": None}
        with pytest.raises(ValueError, match="Access token required"):
            await ConfluenceClient.build_from_toolset(toolset_config, log)


# ============================================================================
# build_from_services - API_TOKEN with missing apiToken
# ============================================================================

class TestBuildFromServicesApiTokenMissingApiToken:
    @pytest.mark.asyncio
    async def test_api_token_missing_api_token_value(self, log, mock_config_service):
        """When apiToken is empty, raise ValueError."""
        mock_config_service.get_config = AsyncMock(return_value={
            "auth": {
                "authType": "API_TOKEN",
                "baseUrl": "https://mysite.atlassian.net",
                "email": "user@example.com",
                "apiToken": "",
            }
        })
        with pytest.raises(ValueError, match="API token is required for API_TOKEN auth"):
            await ConfluenceClient.build_from_services(log, mock_config_service, "inst1")
