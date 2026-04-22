"""Full-coverage unit tests for Jira client module.

Targets lines/branches missed by existing test files:
  - Line 257: build_from_services with empty-dict config (falsy)
  - Line 290: build_from_services BEARER_TOKEN with get_jira_base_url returning ""
  - Line 304: build_from_services OAUTH with get_jira_base_url returning ""
  - Line 394: build_from_toolset OAUTH with get_jira_base_url returning ""
  - Partial branches in build_from_services and build_from_toolset paths
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sources.client.jira.jira import (
    JiraClient,
    JiraRESTClientViaApiKey,
    JiraRESTClientViaToken,
)


@pytest.fixture
def logger():
    return logging.getLogger("test_jira_full_cov")


# ---------------------------------------------------------------------------
# build_from_services – line 257: empty dict config
# ---------------------------------------------------------------------------


class TestBuildFromServicesEmptyDictConfig:
    @pytest.mark.asyncio
    async def test_empty_dict_config_raises(self, logger):
        """When _get_connector_config returns {} (falsy), line 257 raises."""
        config_service = AsyncMock()
        with patch.object(
            JiraClient,
            "_get_connector_config",
            new_callable=AsyncMock,
            return_value={},
        ):
            with pytest.raises(ValueError, match="Failed to get Jira connector configuration"):
                await JiraClient.build_from_services(logger, config_service, "inst-1")


# ---------------------------------------------------------------------------
# build_from_services – line 290: BEARER_TOKEN empty base_url
# ---------------------------------------------------------------------------


class TestBuildFromServicesBearerTokenEmptyBaseUrl:
    @pytest.mark.asyncio
    async def test_bearer_token_empty_base_url(self, logger):
        """When get_jira_base_url returns '' for BEARER_TOKEN, line 290 raises."""
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "authType": "BEARER_TOKEN",
                    "bearerToken": "valid_token",
                    "baseUrl": "https://acme.atlassian.net",
                }
            }
        )
        with patch.object(
            JiraClient,
            "get_jira_base_url",
            new_callable=AsyncMock,
            return_value="",
        ):
            with pytest.raises(ValueError, match="Jira base_url not found"):
                await JiraClient.build_from_services(
                    logger, config_service, "inst-1"
                )

    @pytest.mark.asyncio
    async def test_bearer_token_none_base_url(self, logger):
        """When get_jira_base_url returns None for BEARER_TOKEN, line 290 raises."""
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "authType": "BEARER_TOKEN",
                    "bearerToken": "valid_token",
                    "baseUrl": "https://acme.atlassian.net",
                }
            }
        )
        with patch.object(
            JiraClient,
            "get_jira_base_url",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with pytest.raises(ValueError, match="Jira base_url not found"):
                await JiraClient.build_from_services(
                    logger, config_service, "inst-1"
                )


# ---------------------------------------------------------------------------
# build_from_services – line 304: OAUTH empty base_url
# ---------------------------------------------------------------------------


class TestBuildFromServicesOAuthEmptyBaseUrl:
    @pytest.mark.asyncio
    async def test_oauth_empty_base_url(self, logger):
        """When get_jira_base_url returns '' for OAUTH, line 304 raises."""
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "OAUTH", "baseUrl": "https://acme.atlassian.net"},
                "credentials": {"access_token": "oauth_tok"},
            }
        )
        with patch.object(
            JiraClient,
            "get_jira_base_url",
            new_callable=AsyncMock,
            return_value="",
        ):
            with pytest.raises(ValueError, match="Jira base_url not found"):
                await JiraClient.build_from_services(
                    logger, config_service, "inst-1"
                )

    @pytest.mark.asyncio
    async def test_oauth_none_base_url(self, logger):
        """When get_jira_base_url returns None for OAUTH, line 304 raises."""
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "OAUTH", "baseUrl": "https://acme.atlassian.net"},
                "credentials": {"access_token": "oauth_tok"},
            }
        )
        with patch.object(
            JiraClient,
            "get_jira_base_url",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with pytest.raises(ValueError, match="Jira base_url not found"):
                await JiraClient.build_from_services(
                    logger, config_service, "inst-1"
                )


# ---------------------------------------------------------------------------
# build_from_toolset – line 394: OAUTH with get_jira_base_url returning ""
# ---------------------------------------------------------------------------


class TestBuildFromToolsetOAuthEmptyBaseUrl:
    @pytest.mark.asyncio
    async def test_oauth_empty_base_url(self, logger):
        """When get_jira_base_url returns '' for OAUTH toolset path, line 394 raises."""
        toolset_config = {
            "authType": "OAUTH",
            "isAuthenticated": True,
            "auth": {"baseUrl": "https://acme.atlassian.net"},
            "credentials": {"access_token": "oauth_tok"},
        }
        with patch.object(
            JiraClient,
            "get_jira_base_url",
            new_callable=AsyncMock,
            return_value="",
        ):
            with pytest.raises(ValueError, match="Failed to get Jira base URL"):
                await JiraClient.build_from_toolset(toolset_config, logger)

    @pytest.mark.asyncio
    async def test_oauth_none_base_url(self, logger):
        """When get_jira_base_url returns None for OAUTH toolset path, line 394 raises."""
        toolset_config = {
            "authType": "OAUTH",
            "isAuthenticated": True,
            "auth": {"baseUrl": "https://acme.atlassian.net"},
            "credentials": {"access_token": "oauth_tok"},
        }
        with patch.object(
            JiraClient,
            "get_jira_base_url",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with pytest.raises(ValueError, match="Failed to get Jira base URL"):
                await JiraClient.build_from_toolset(toolset_config, logger)


# ---------------------------------------------------------------------------
# build_from_services – API_TOKEN with missing apiToken only
# ---------------------------------------------------------------------------


class TestBuildFromServicesApiTokenMissingApiToken:
    @pytest.mark.asyncio
    async def test_missing_api_token_only(self, logger):
        """Email is present but apiToken is empty."""
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "authType": "API_TOKEN",
                    "baseUrl": "https://jira.example.com",
                    "email": "user@test.com",
                    "apiToken": "",
                }
            }
        )
        with pytest.raises(ValueError, match="Email and API token"):
            await JiraClient.build_from_services(logger, config_service, "inst-1")


# ---------------------------------------------------------------------------
# build_from_toolset – API_TOKEN with missing apiToken
# ---------------------------------------------------------------------------


class TestBuildFromToolsetApiTokenMissingApiToken:
    @pytest.mark.asyncio
    async def test_missing_api_token_only(self, logger):
        """Email is present but apiToken is empty in toolset config."""
        cs = AsyncMock()
        with patch(
            "app.sources.client.jira.jira.get_toolset_by_id",
            return_value={"auth": {"baseUrl": "https://jira.com"}},
        ):
            config = {
                "isAuthenticated": True,
                "authType": "API_TOKEN",
                "instanceId": "inst1",
                "auth": {"email": "u@t.com", "apiToken": ""},
            }
            with pytest.raises(ValueError, match="Email and API token"):
                await JiraClient.build_from_toolset(config, logger, cs)


# ---------------------------------------------------------------------------
# build_from_services – OAUTH with empty credentials dict (not None)
# ---------------------------------------------------------------------------


class TestBuildFromServicesOAuthEmptyCredentialsDict:
    @pytest.mark.asyncio
    async def test_empty_credentials_dict(self, logger):
        """credentials={} is falsy - should raise 'Credentials configuration not found'."""
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "OAUTH"},
                "credentials": {},
            }
        )
        with pytest.raises(ValueError, match="Credentials configuration not found"):
            await JiraClient.build_from_services(logger, config_service, "inst-1")


# ---------------------------------------------------------------------------
# build_from_toolset – auth_type lowercase conversion (.upper())
# ---------------------------------------------------------------------------


class TestBuildFromToolsetAuthTypeCaseInsensitive:
    @pytest.mark.asyncio
    async def test_lowercase_oauth(self, logger):
        """authType='oauth' should be uppercased to 'OAUTH'."""
        toolset_config = {
            "authType": "oauth",
            "isAuthenticated": True,
            "auth": {"baseUrl": "https://acme.atlassian.net"},
            "credentials": {"access_token": "tok"},
        }
        with patch.object(
            JiraClient,
            "get_jira_base_url",
            new_callable=AsyncMock,
            return_value="https://api.atlassian.com/ex/jira/c1",
        ):
            jc = await JiraClient.build_from_toolset(toolset_config, logger)
            assert isinstance(jc.get_client(), JiraRESTClientViaToken)

    @pytest.mark.asyncio
    async def test_lowercase_api_token(self, logger):
        """authType='api_token' should be uppercased to 'API_TOKEN'."""
        cs = AsyncMock()
        with patch(
            "app.sources.client.jira.jira.get_toolset_by_id",
            return_value={"auth": {"baseUrl": "https://jira.com"}},
        ):
            config = {
                "isAuthenticated": True,
                "authType": "api_token",
                "instanceId": "inst1",
                "auth": {"email": "u@t.com", "apiToken": "tok"},
            }
            jc = await JiraClient.build_from_toolset(config, logger, cs)
            assert isinstance(jc.get_client(), JiraRESTClientViaApiKey)


# ---------------------------------------------------------------------------
# build_from_toolset – empty authType defaults to ""
# ---------------------------------------------------------------------------


class TestBuildFromToolsetEmptyAuthType:
    @pytest.mark.asyncio
    async def test_empty_auth_type_raises(self, logger):
        """Empty authType should be treated as unsupported."""
        toolset_config = {
            "isAuthenticated": True,
            "credentials": {"access_token": "tok"},
        }
        with pytest.raises(ValueError, match="Unsupported auth type"):
            await JiraClient.build_from_toolset(toolset_config, logger)


# ---------------------------------------------------------------------------
# build_from_services – auth_config with empty dict from .get("auth", {})
# ---------------------------------------------------------------------------


class TestBuildFromServicesAuthConfigBranch:
    @pytest.mark.asyncio
    async def test_auth_key_empty_dict(self, logger):
        """config.get('auth', {}) returns {} but is still truthy after 'or {}'.
        Since {} is falsy, 'not auth_config' check catches it."""
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(
            return_value={"auth": {}, "credentials": {}}
        )
        with pytest.raises(ValueError, match="Auth configuration not found"):
            await JiraClient.build_from_services(logger, config_service, "inst-1")
