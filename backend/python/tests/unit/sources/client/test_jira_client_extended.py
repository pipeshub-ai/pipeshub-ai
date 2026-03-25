"""
Extended tests for app.sources.client.jira.jira covering missing lines:
- build_from_services with API_TOKEN auth (lines 268-280)
- build_from_services with BEARER_TOKEN auth (line 290)
- build_from_services with OAUTH auth (lines 296, 304)
- build_from_toolset with OAUTH auth (line 394)
- build_from_toolset with API_TOKEN auth (lines 401-430)
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sources.client.jira.jira import (
    JiraClient,
    JiraRESTClientViaApiKey,
    JiraRESTClientViaToken,
)


# ============================================================================
# build_from_services
# ============================================================================


class TestBuildFromServicesApiToken:
    @pytest.mark.asyncio
    async def test_api_token_auth(self):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {
                "authType": "API_TOKEN",
                "baseUrl": "https://mysite.atlassian.net/",
                "email": "user@example.com",
                "apiToken": "jira_token_123",
            }
        })
        logger = logging.getLogger("test")
        client = await JiraClient.build_from_services(logger, config_service, "inst1")
        assert isinstance(client.get_client(), JiraRESTClientViaApiKey)
        assert not client.get_client().get_base_url().endswith("/")

    @pytest.mark.asyncio
    async def test_api_token_missing_base_url(self):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {
                "authType": "API_TOKEN",
                "baseUrl": "",
                "email": "user@example.com",
                "apiToken": "token",
            }
        })
        logger = logging.getLogger("test")
        with pytest.raises(ValueError, match="Base URL is required"):
            await JiraClient.build_from_services(logger, config_service, "inst1")

    @pytest.mark.asyncio
    async def test_api_token_missing_email(self):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {
                "authType": "API_TOKEN",
                "baseUrl": "https://mysite.atlassian.net",
                "email": "",
                "apiToken": "token",
            }
        })
        logger = logging.getLogger("test")
        with pytest.raises(ValueError, match="Email and API token are required"):
            await JiraClient.build_from_services(logger, config_service, "inst1")


class TestBuildFromServicesBearerToken:
    @pytest.mark.asyncio
    async def test_bearer_token(self):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {
                "authType": "BEARER_TOKEN",
                "bearerToken": "bearer123",
            }
        })
        logger = logging.getLogger("test")
        with patch.object(
            JiraClient, "get_jira_base_url",
            new_callable=AsyncMock,
            return_value="https://api.atlassian.com/ex/jira/cloud1"
        ):
            client = await JiraClient.build_from_services(logger, config_service, "inst1")
            assert isinstance(client.get_client(), JiraRESTClientViaToken)


class TestBuildFromServicesOAuth:
    @pytest.mark.asyncio
    async def test_oauth(self):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {"authType": "OAUTH"},
            "credentials": {"access_token": "oauth_token"},
        })
        logger = logging.getLogger("test")
        with patch.object(
            JiraClient, "get_jira_base_url",
            new_callable=AsyncMock,
            return_value="https://api.atlassian.com/ex/jira/cloud1"
        ):
            client = await JiraClient.build_from_services(logger, config_service, "inst1")
            assert isinstance(client.get_client(), JiraRESTClientViaToken)

    @pytest.mark.asyncio
    async def test_oauth_no_credentials(self):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {"authType": "OAUTH"},
            "credentials": {},
        })
        logger = logging.getLogger("test")
        with pytest.raises(ValueError, match="Credentials configuration not found"):
            await JiraClient.build_from_services(logger, config_service, "inst1")


# ============================================================================
# build_from_toolset
# ============================================================================


class TestBuildFromToolsetOAuth:
    @pytest.mark.asyncio
    async def test_oauth_success(self):
        toolset_config = {
            "authType": "OAUTH",
            "isAuthenticated": True,
            "credentials": {"access_token": "oauth_tok"},
        }
        logger = logging.getLogger("test")
        with patch.object(
            JiraClient, "get_jira_base_url",
            new_callable=AsyncMock,
            return_value="https://api.atlassian.com/ex/jira/cloud1"
        ):
            client = await JiraClient.build_from_toolset(toolset_config, logger)
            assert isinstance(client.get_client(), JiraRESTClientViaToken)


class TestBuildFromToolsetApiToken:
    @pytest.mark.asyncio
    async def test_api_token_success(self):
        toolset_config = {
            "authType": "API_TOKEN",
            "isAuthenticated": True,
            "instanceId": "inst-123",
            "auth": {
                "email": "user@example.com",
                "apiToken": "api_token_123",
            },
        }
        config_service = AsyncMock()
        logger = logging.getLogger("test")

        with patch(
            "app.sources.client.jira.jira.get_toolset_by_id",
            new_callable=AsyncMock,
            return_value={
                "auth": {"baseUrl": "https://mysite.atlassian.net"},
            },
        ):
            client = await JiraClient.build_from_toolset(toolset_config, logger, config_service)
            assert isinstance(client.get_client(), JiraRESTClientViaApiKey)

    @pytest.mark.asyncio
    async def test_api_token_no_instance_id(self):
        toolset_config = {
            "authType": "API_TOKEN",
            "isAuthenticated": True,
            "auth": {"email": "user@example.com", "apiToken": "tok"},
        }
        logger = logging.getLogger("test")
        with pytest.raises(ValueError, match="instanceId is required"):
            await JiraClient.build_from_toolset(toolset_config, logger)

    @pytest.mark.asyncio
    async def test_api_token_no_config_service(self):
        toolset_config = {
            "authType": "API_TOKEN",
            "isAuthenticated": True,
            "instanceId": "inst-123",
            "auth": {"email": "user@example.com", "apiToken": "tok"},
        }
        logger = logging.getLogger("test")
        with pytest.raises(ValueError, match="config_service is required"):
            await JiraClient.build_from_toolset(toolset_config, logger, None)


class TestBuildFromToolsetInvalid:
    @pytest.mark.asyncio
    async def test_unsupported_auth_type(self):
        toolset_config = {
            "authType": "UNKNOWN",
            "isAuthenticated": True,
        }
        logger = logging.getLogger("test")
        with pytest.raises(ValueError, match="Unsupported auth type"):
            await JiraClient.build_from_toolset(toolset_config, logger)

    @pytest.mark.asyncio
    async def test_empty_config(self):
        logger = logging.getLogger("test")
        with pytest.raises(ValueError, match="Toolset configuration is required"):
            await JiraClient.build_from_toolset({}, logger)

    @pytest.mark.asyncio
    async def test_not_authenticated(self):
        toolset_config = {"authType": "OAUTH", "isAuthenticated": False}
        logger = logging.getLogger("test")
        with pytest.raises(ValueError, match="not authenticated"):
            await JiraClient.build_from_toolset(toolset_config, logger)
