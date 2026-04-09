"""Coverage tests for app.sources.client.notion.notion targeting uncovered lines.

Missing lines from coverage report (96.1%):
- 277: build_from_services - config is None after _get_connector_config
- 302->309: OAuth shared config - oauth_configs is not a list (isinstance check fails)
- 314-316: OAuth shared config fetch failure fallback (exception in fetching shared config)
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sources.client.notion.notion import (
    NotionClient,
    NotionRESTClientViaOAuth,
    NotionRESTClientViaToken,
)


@pytest.fixture
def logger():
    return logging.getLogger("test_notion_coverage")


@pytest.fixture
def mock_config_service():
    return AsyncMock()


# ============================================================================
# Line 277: build_from_services - config is None
# ============================================================================

class TestBuildFromServicesConfigNone:
    @pytest.mark.asyncio
    async def test_config_none_raises_value_error(self, logger, mock_config_service):
        """When _get_connector_config returns None, raise ValueError."""
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Failed to get Notion"):
            await NotionClient.build_from_services(logger, mock_config_service, "inst-missing")

    @pytest.mark.asyncio
    async def test_config_returns_falsy_directly(self, logger, mock_config_service):
        """When _get_connector_config is mocked to return None, line 277 is hit."""
        with patch.object(
            NotionClient, "_get_connector_config",
            new_callable=AsyncMock,
            return_value=None
        ):
            with pytest.raises(ValueError, match="Failed to get Notion connector"):
                await NotionClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_config_returns_empty_dict_directly(self, logger, mock_config_service):
        """When _get_connector_config is mocked to return empty dict, line 277 is hit."""
        with patch.object(
            NotionClient, "_get_connector_config",
            new_callable=AsyncMock,
            return_value={}
        ):
            with pytest.raises(ValueError, match="Failed to get Notion connector"):
                await NotionClient.build_from_services(logger, mock_config_service, "inst-1")


# ============================================================================
# Line 302->309: OAuth shared config - not a list
# ============================================================================

class TestBuildFromServicesOAuthSharedConfigNotList:
    @pytest.mark.asyncio
    async def test_oauth_configs_is_dict_not_list(self, logger, mock_config_service):
        """When oauth_configs is a dict instead of list, skip iteration, fail with missing creds."""

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
                return {"some": "dict"}  # Not a list!
            return default

        mock_config_service.get_config = AsyncMock(side_effect=fake_get_config)
        with pytest.raises(ValueError, match="Client ID, client secret"):
            await NotionClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_oauth_configs_is_string(self, logger, mock_config_service):
        """When oauth_configs is a string instead of list, skip iteration."""

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
                return "not a list"
            return default

        mock_config_service.get_config = AsyncMock(side_effect=fake_get_config)
        with pytest.raises(ValueError, match="Client ID, client secret"):
            await NotionClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_oauth_configs_is_none(self, logger, mock_config_service):
        """When oauth_configs returns None (default), skip iteration."""

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
                return None
            return default

        mock_config_service.get_config = AsyncMock(side_effect=fake_get_config)
        with pytest.raises(ValueError, match="Client ID, client secret"):
            await NotionClient.build_from_services(logger, mock_config_service, "inst-1")


# ============================================================================
# Lines 314-316: OAuth shared config fetch failure fallback
# ============================================================================

class TestBuildFromServicesOAuthSharedConfigFetchFailure:
    @pytest.mark.asyncio
    async def test_shared_config_fetch_exception_falls_back(self, logger, mock_config_service):
        """When fetching shared OAuth config raises exception, fall back to connector auth."""
        call_count = 0

        async def fake_get_config(path, default=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:  # First call: connector config
                return {
                    "auth": {
                        "authType": "OAUTH",
                        "oauthConfigId": "oauth-123",
                        # No clientId/clientSecret here
                    },
                    "credentials": {"access_token": "at"},
                }
            # Second call: shared OAuth config - raises
            raise RuntimeError("etcd unreachable")

        mock_config_service.get_config = AsyncMock(side_effect=fake_get_config)
        # Should fall back and then fail because no clientId/clientSecret
        with pytest.raises(ValueError, match="Client ID, client secret"):
            await NotionClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_shared_config_fetch_exception_with_existing_creds(self, logger, mock_config_service):
        """When shared config fetch fails but connector auth already has creds, use those."""
        call_count = 0

        async def fake_get_config(path, default=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:  # First call: connector config
                return {
                    "auth": {
                        "authType": "OAUTH",
                        "oauthConfigId": "oauth-123",
                        "clientId": "fallback-cid",
                        "clientSecret": "fallback-csec",
                        "redirectUri": "http://redirect",
                    },
                    "credentials": {"access_token": "at"},
                }
            # Second call: shared OAuth config - raises
            raise RuntimeError("etcd unreachable")

        mock_config_service.get_config = AsyncMock(side_effect=fake_get_config)
        # Should succeed using the fallback credentials
        nc = await NotionClient.build_from_services(logger, mock_config_service, "inst-1")
        assert isinstance(nc, NotionClient)
        assert isinstance(nc.get_client(), NotionRESTClientViaOAuth)


# ============================================================================
# Additional edge cases for OAuth shared config
# ============================================================================

class TestBuildFromServicesOAuthSharedConfigEdgeCases:
    @pytest.mark.asyncio
    async def test_shared_config_matching_but_no_config_key(self, logger, mock_config_service):
        """When matching OAuth config has no 'config' key, don't extract credentials."""

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
                return [
                    {"_id": "oauth-123"},  # No 'config' key
                ]
            return default

        mock_config_service.get_config = AsyncMock(side_effect=fake_get_config)
        with pytest.raises(ValueError, match="Client ID, client secret"):
            await NotionClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_shared_config_with_redirect_uri(self, logger, mock_config_service):
        """Shared config provides redirectUri when connector auth doesn't have it."""

        async def fake_get_config(path, default=None):
            if "connectors" in path:
                return {
                    "auth": {
                        "authType": "OAUTH",
                        "oauthConfigId": "oauth-123",
                        # No redirectUri here
                    },
                    "credentials": {"access_token": "at"},
                }
            if "oauth" in path:
                return [
                    {
                        "_id": "oauth-123",
                        "config": {
                            "clientId": "shared-cid",
                            "clientSecret": "shared-csec",
                            "redirectUri": "https://shared-redirect.com/callback",
                        },
                    }
                ]
            return default

        mock_config_service.get_config = AsyncMock(side_effect=fake_get_config)
        nc = await NotionClient.build_from_services(logger, mock_config_service, "inst-1")
        assert isinstance(nc.get_client(), NotionRESTClientViaOAuth)

    @pytest.mark.asyncio
    async def test_shared_config_snake_case_keys(self, logger, mock_config_service):
        """Shared config uses snake_case (client_id, client_secret, redirect_uri)."""

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
                return [
                    {
                        "_id": "oauth-123",
                        "config": {
                            "client_id": "snake-cid",
                            "client_secret": "snake-csec",
                            "redirect_uri": "https://redirect.com",
                        },
                    }
                ]
            return default

        mock_config_service.get_config = AsyncMock(side_effect=fake_get_config)
        nc = await NotionClient.build_from_services(logger, mock_config_service, "inst-1")
        assert isinstance(nc.get_client(), NotionRESTClientViaOAuth)

    @pytest.mark.asyncio
    async def test_shared_config_empty_list(self, logger, mock_config_service):
        """When shared config returns empty list, no match found."""

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
    async def test_oauth_with_all_fields_from_connector_auth(self, logger, mock_config_service):
        """When connector auth already has all OAuth fields, no shared config lookup needed."""
        mock_config_service.get_config = AsyncMock(return_value={
            "auth": {
                "authType": "OAUTH",
                "clientId": "direct-cid",
                "clientSecret": "direct-csec",
                "redirectUri": "http://redirect",
            },
            "credentials": {"access_token": "at"},
        })
        nc = await NotionClient.build_from_services(logger, mock_config_service, "inst-1")
        assert isinstance(nc.get_client(), NotionRESTClientViaOAuth)

    @pytest.mark.asyncio
    async def test_oauth_needs_shared_config_false_when_both_present(self, logger, mock_config_service):
        """When clientId and clientSecret are present, needs_shared_config is False."""
        mock_config_service.get_config = AsyncMock(return_value={
            "auth": {
                "authType": "OAUTH",
                "oauthConfigId": "oauth-123",
                "clientId": "cid",
                "clientSecret": "csec",
                "redirectUri": "http://redirect",
            },
            "credentials": {"access_token": "at"},
        })
        nc = await NotionClient.build_from_services(logger, mock_config_service, "inst-1")
        assert isinstance(nc.get_client(), NotionRESTClientViaOAuth)
        # Only one call to get_config (for connector config), no shared config call
        mock_config_service.get_config.assert_called_once()


# ============================================================================
# build_from_services - empty auth section
# ============================================================================

class TestBuildFromServicesAuthEdgeCases:
    @pytest.mark.asyncio
    async def test_auth_none_defaults_to_empty_dict(self, logger, mock_config_service):
        """When auth is None, defaults to empty dict, authType defaults to API_TOKEN."""
        mock_config_service.get_config = AsyncMock(return_value={
            "auth": None,
        })
        # authType defaults to "API_TOKEN" but no apiToken, so raises
        with pytest.raises(ValueError, match="Token required"):
            await NotionClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_auth_missing_key(self, logger, mock_config_service):
        """When config returns empty dict {}, _get_connector_config treats it as falsy and raises."""
        mock_config_service.get_config = AsyncMock(return_value={})
        # Empty dict is falsy in _get_connector_config: `if not config: raise`
        with pytest.raises(ValueError, match="Failed to get Notion"):
            await NotionClient.build_from_services(logger, mock_config_service, "inst-1")
