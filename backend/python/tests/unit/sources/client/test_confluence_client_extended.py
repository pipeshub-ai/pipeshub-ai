"""
Extended tests for app.sources.client.confluence.confluence covering missing lines:
- build_from_services with API_TOKEN auth (lines 274-288)
- build_from_services with BEARER_TOKEN auth (line 299)
- build_from_services with OAUTH auth (line 305, 314)
- build_from_toolset with BEARER_TOKEN auth (line 365)
- build_from_toolset with OAUTH auth (line 377)
- build_from_toolset with API_TOKEN auth (lines 384-415)
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sources.client.confluence.confluence import (
    ConfluenceClient,
    ConfluenceRESTClientViaApiKey,
    ConfluenceRESTClientViaToken,
)


# ============================================================================
# build_from_services - API_TOKEN auth
# ============================================================================


class TestBuildFromServicesApiToken:
    @pytest.mark.asyncio
    async def test_api_token_auth(self):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {
                "authType": "API_TOKEN",
                "baseUrl": "https://mysite.atlassian.net",
                "email": "user@example.com",
                "apiToken": "token123",
            }
        })
        logger = logging.getLogger("test")
        client = await ConfluenceClient.build_from_services(logger, config_service, "inst1")
        assert isinstance(client.get_client(), ConfluenceRESTClientViaApiKey)
        assert client.get_client().get_base_url().endswith("/wiki/api/v2")

    @pytest.mark.asyncio
    async def test_api_token_already_has_path(self):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {
                "authType": "API_TOKEN",
                "baseUrl": "https://mysite.atlassian.net/wiki/api/v2",
                "email": "user@example.com",
                "apiToken": "token123",
            }
        })
        logger = logging.getLogger("test")
        client = await ConfluenceClient.build_from_services(logger, config_service, "inst1")
        base_url = client.get_client().get_base_url()
        assert base_url == "https://mysite.atlassian.net/wiki/api/v2"

    @pytest.mark.asyncio
    async def test_api_token_missing_base_url(self):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {
                "authType": "API_TOKEN",
                "baseUrl": "",
                "email": "user@example.com",
                "apiToken": "token123",
            }
        })
        logger = logging.getLogger("test")
        with pytest.raises(ValueError, match="Base URL is required"):
            await ConfluenceClient.build_from_services(logger, config_service, "inst1")

    @pytest.mark.asyncio
    async def test_api_token_missing_email_uses_pat_client(self):
        """Empty email with API token selects Bearer/PAT client (Data Center style)."""
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {
                "authType": "API_TOKEN",
                "baseUrl": "https://mysite.atlassian.net",
                "email": "",
                "apiToken": "token123",
            }
        })
        logger = logging.getLogger("test")
        cc = await ConfluenceClient.build_from_services(logger, config_service, "inst1")
        assert isinstance(cc.get_client(), ConfluenceRESTClientViaToken)


class TestBuildFromServicesBearerToken:
    @pytest.mark.asyncio
    async def test_bearer_token_auth(self):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {
                "authType": "BEARER_TOKEN",
                "bearerToken": "bearer_token_123",
                "baseUrl": "https://acme.atlassian.net",
            }
        })
        logger = logging.getLogger("test")
        with patch.object(
            ConfluenceClient, "get_confluence_base_url",
            new_callable=AsyncMock,
            return_value="https://api.atlassian.com/ex/confluence/cloud1/wiki/api/v2"
        ):
            client = await ConfluenceClient.build_from_services(logger, config_service, "inst1")
            assert isinstance(client.get_client(), ConfluenceRESTClientViaToken)

    @pytest.mark.asyncio
    async def test_bearer_token_missing(self):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {
                "authType": "BEARER_TOKEN",
                "bearerToken": "",
            }
        })
        logger = logging.getLogger("test")
        with pytest.raises(ValueError, match="Token required"):
            await ConfluenceClient.build_from_services(logger, config_service, "inst1")


class TestBuildFromServicesOAuth:
    @pytest.mark.asyncio
    async def test_oauth_auth(self):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {"authType": "OAUTH", "baseUrl": "https://acme.atlassian.net"},
            "credentials": {"access_token": "oauth_token"},
        })
        logger = logging.getLogger("test")
        with patch.object(
            ConfluenceClient, "get_confluence_base_url",
            new_callable=AsyncMock,
            return_value="https://api.atlassian.com/ex/confluence/cloud1/wiki/api/v2"
        ):
            client = await ConfluenceClient.build_from_services(logger, config_service, "inst1")
            assert isinstance(client.get_client(), ConfluenceRESTClientViaToken)

    @pytest.mark.asyncio
    async def test_oauth_no_credentials(self):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {"authType": "OAUTH"},
            "credentials": {},
        })
        logger = logging.getLogger("test")
        with pytest.raises(ValueError, match="Credentials configuration not found"):
            await ConfluenceClient.build_from_services(logger, config_service, "inst1")

    @pytest.mark.asyncio
    async def test_oauth_no_access_token(self):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {"authType": "OAUTH"},
            "credentials": {"access_token": ""},
        })
        logger = logging.getLogger("test")
        with pytest.raises(ValueError, match="Access token required"):
            await ConfluenceClient.build_from_services(logger, config_service, "inst1")


class TestBuildFromServicesInvalidAuth:
    @pytest.mark.asyncio
    async def test_invalid_auth_type(self):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {"authType": "UNKNOWN"},
        })
        logger = logging.getLogger("test")
        with pytest.raises(ValueError, match="Invalid auth type"):
            await ConfluenceClient.build_from_services(logger, config_service, "inst1")


# ============================================================================
# build_from_toolset
# ============================================================================


class TestBuildFromToolsetBearer:
    @pytest.mark.asyncio
    async def test_bearer_token(self):
        toolset_config = {
            "authType": "BEARER_TOKEN",
            "bearerToken": "toolset_token",
            "auth": {"baseUrl": "https://acme.atlassian.net"},
        }
        logger = logging.getLogger("test")
        with patch.object(
            ConfluenceClient, "get_confluence_base_url",
            new_callable=AsyncMock,
            return_value="https://api.atlassian.com/ex/confluence/cloud1/wiki/api/v2"
        ):
            client = await ConfluenceClient.build_from_toolset(toolset_config, logger)
            assert isinstance(client.get_client(), ConfluenceRESTClientViaToken)


class TestBuildFromToolsetOAuth:
    @pytest.mark.asyncio
    async def test_oauth(self):
        toolset_config = {
            "authType": "OAUTH",
            "auth": {"baseUrl": "https://acme.atlassian.net"},
            "credentials": {"access_token": "oauth_token"},
        }
        logger = logging.getLogger("test")
        with patch.object(
            ConfluenceClient, "get_confluence_base_url",
            new_callable=AsyncMock,
            return_value="https://api.atlassian.com/ex/confluence/cloud1/wiki/api/v2"
        ):
            client = await ConfluenceClient.build_from_toolset(toolset_config, logger)
            assert isinstance(client.get_client(), ConfluenceRESTClientViaToken)


class TestBuildFromToolsetApiToken:
    @pytest.mark.asyncio
    async def test_api_token_success(self):
        toolset_config = {
            "authType": "API_TOKEN",
            "instanceId": "inst-123",
            "auth": {
                "email": "user@example.com",
                "apiToken": "api_token_123",
            },
        }
        config_service = AsyncMock()
        logger = logging.getLogger("test")

        with patch(
            "app.sources.client.confluence.confluence.get_toolset_by_id",
            new_callable=AsyncMock,
            return_value={
                "auth": {"baseUrl": "https://mysite.atlassian.net"},
            },
        ):
            client = await ConfluenceClient.build_from_toolset(toolset_config, logger, config_service)
            assert isinstance(client.get_client(), ConfluenceRESTClientViaApiKey)

    @pytest.mark.asyncio
    async def test_api_token_no_instance_id(self):
        toolset_config = {
            "authType": "API_TOKEN",
            "auth": {"email": "user@example.com", "apiToken": "tok"},
        }
        logger = logging.getLogger("test")
        with pytest.raises(ValueError, match="instanceId is required"):
            await ConfluenceClient.build_from_toolset(toolset_config, logger)

    @pytest.mark.asyncio
    async def test_api_token_no_config_service(self):
        toolset_config = {
            "authType": "API_TOKEN",
            "instanceId": "inst-123",
            "auth": {"email": "user@example.com", "apiToken": "tok"},
        }
        logger = logging.getLogger("test")
        with pytest.raises(ValueError, match="config_service is required"):
            await ConfluenceClient.build_from_toolset(toolset_config, logger, None)

    @pytest.mark.asyncio
    async def test_api_token_instance_not_found(self):
        toolset_config = {
            "authType": "API_TOKEN",
            "instanceId": "inst-123",
            "auth": {"email": "user@example.com", "apiToken": "tok"},
        }
        config_service = AsyncMock()
        logger = logging.getLogger("test")

        with patch(
            "app.sources.client.confluence.confluence.get_toolset_by_id",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with pytest.raises(ValueError, match="not found"):
                await ConfluenceClient.build_from_toolset(toolset_config, logger, config_service)


class TestBuildFromToolsetInvalid:
    @pytest.mark.asyncio
    async def test_invalid_auth_type(self):
        toolset_config = {"authType": "UNKNOWN"}
        logger = logging.getLogger("test")
        with pytest.raises(ValueError, match="Invalid auth type"):
            await ConfluenceClient.build_from_toolset(toolset_config, logger)

    @pytest.mark.asyncio
    async def test_empty_config(self):
        logger = logging.getLogger("test")
        with pytest.raises(ValueError, match="Toolset config is required"):
            await ConfluenceClient.build_from_toolset({}, logger)
