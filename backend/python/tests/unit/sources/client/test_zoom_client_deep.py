"""
Additional tests for app.sources.client.zoom.zoom targeting uncovered lines:
- ZoomClient.build_from_services: all auth types (S2S, OAuth, Token)
- ZoomClient.build_from_toolset
- ZoomClient._find_shared_oauth_config
- ZoomClient._get_connector_config
- Shared OAuth config resolution
"""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.sources.client.zoom.zoom import (
    ZoomAuthType,
    ZoomClient,
    ZoomOAuthConfig,
    ZoomRESTClientViaOAuth,
    ZoomRESTClientViaServerToServer,
    ZoomRESTClientViaToken,
    ZoomServerToServerConfig,
    ZoomSharedOAuthConfigEntry,
    ZoomTokenConfig,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_config_service(config_data=None, oauth_data=None):
    """Create a mock config service."""
    svc = AsyncMock()
    async def get_config_side_effect(key, **kwargs):
        if "oauth" in key:
            return oauth_data if oauth_data is not None else []
        return config_data
    svc.get_config = AsyncMock(side_effect=get_config_side_effect)
    return svc


# ============================================================================
# build_from_services - SERVER_TO_SERVER
# ============================================================================


class TestBuildFromServicesS2S:
    @pytest.mark.asyncio
    async def test_s2s_with_direct_credentials(self):
        config = {
            "auth": {
                "authType": "SERVER_TO_SERVER",
                "accountId": "acc-123",
                "clientId": "cid-123",
                "clientSecret": "secret-123",
            },
            "credentials": {}
        }
        cs = _mock_config_service(config)
        logger = MagicMock(spec=logging.Logger)
        client = await ZoomClient.build_from_services(logger, cs, "inst-1")
        assert isinstance(client.client, ZoomRESTClientViaServerToServer)

    @pytest.mark.asyncio
    async def test_s2s_missing_credentials_raises(self):
        config = {
            "auth": {
                "authType": "SERVER_TO_SERVER",
                "accountId": "",
                "clientId": "",
                "clientSecret": "",
            },
            "credentials": {}
        }
        cs = _mock_config_service(config)
        logger = MagicMock(spec=logging.Logger)
        with pytest.raises(ValueError, match="account_id, client_id, and client_secret"):
            await ZoomClient.build_from_services(logger, cs, "inst-1")

    @pytest.mark.asyncio
    async def test_s2s_with_shared_oauth_config(self):
        config = {
            "auth": {
                "authType": "SERVER_TO_SERVER",
                "accountId": "",
                "clientId": "",
                "clientSecret": "",
                "oauthConfigId": "shared-1",
            },
            "credentials": {}
        }
        oauth_entries = [
            {
                "_id": "shared-1",
                "config": {
                    "accountId": "shared-acc",
                    "clientId": "shared-cid",
                    "clientSecret": "shared-secret",
                }
            }
        ]
        cs = _mock_config_service(config, oauth_entries)
        logger = MagicMock(spec=logging.Logger)
        client = await ZoomClient.build_from_services(logger, cs, "inst-1")
        assert isinstance(client.client, ZoomRESTClientViaServerToServer)


# ============================================================================
# build_from_services - OAUTH
# ============================================================================


class TestBuildFromServicesOAuth:
    @pytest.mark.asyncio
    async def test_oauth_with_access_token(self):
        config = {
            "auth": {
                "authType": "OAUTH",
                "clientId": "cid",
                "clientSecret": "csec",
                "redirectUri": "https://example.com/callback",
            },
            "credentials": {"access_token": "tok-123"}
        }
        cs = _mock_config_service(config)
        logger = MagicMock(spec=logging.Logger)
        client = await ZoomClient.build_from_services(logger, cs, "inst-1")
        assert isinstance(client.client, ZoomRESTClientViaOAuth)

    @pytest.mark.asyncio
    async def test_oauth_missing_access_token_raises(self):
        config = {
            "auth": {"authType": "OAUTH"},
            "credentials": {"access_token": ""}
        }
        cs = _mock_config_service(config)
        logger = MagicMock(spec=logging.Logger)
        with pytest.raises(ValueError, match="Access token required"):
            await ZoomClient.build_from_services(logger, cs, "inst-1")

    @pytest.mark.asyncio
    async def test_oauth_with_shared_config(self):
        config = {
            "auth": {
                "authType": "OAUTH",
                "oauthConfigId": "shared-2",
                "clientId": "",
                "clientSecret": "",
            },
            "credentials": {"access_token": "tok-abc"}
        }
        oauth_entries = [
            {
                "_id": "shared-2",
                "config": {
                    "clientId": "shared-cid",
                    "clientSecret": "shared-sec",
                    "redirectUri": "https://redir.com",
                }
            }
        ]
        cs = _mock_config_service(config, oauth_entries)
        logger = MagicMock(spec=logging.Logger)
        client = await ZoomClient.build_from_services(logger, cs, "inst-1")
        assert isinstance(client.client, ZoomRESTClientViaOAuth)


# ============================================================================
# build_from_services - TOKEN
# ============================================================================


class TestBuildFromServicesToken:
    @pytest.mark.asyncio
    async def test_token_auth(self):
        config = {
            "auth": {"authType": "TOKEN", "token": "my-token"},
            "credentials": {}
        }
        cs = _mock_config_service(config)
        logger = MagicMock(spec=logging.Logger)
        client = await ZoomClient.build_from_services(logger, cs, "inst-1")
        assert isinstance(client.client, ZoomRESTClientViaToken)

    @pytest.mark.asyncio
    async def test_token_missing_raises(self):
        config = {
            "auth": {"authType": "TOKEN", "token": ""},
            "credentials": {}
        }
        cs = _mock_config_service(config)
        logger = MagicMock(spec=logging.Logger)
        with pytest.raises(ValueError, match="Token required"):
            await ZoomClient.build_from_services(logger, cs, "inst-1")


# ============================================================================
# build_from_services - Invalid auth type
# ============================================================================


class TestBuildFromServicesInvalid:
    @pytest.mark.asyncio
    async def test_invalid_auth_type(self):
        config = {
            "auth": {"authType": "INVALID"},
            "credentials": {}
        }
        cs = _mock_config_service(config)
        logger = MagicMock(spec=logging.Logger)
        with pytest.raises((ValueError, Exception)):
            await ZoomClient.build_from_services(logger, cs, "inst-1")

    @pytest.mark.asyncio
    async def test_empty_config_raises(self):
        cs = _mock_config_service(None)
        logger = MagicMock(spec=logging.Logger)
        with pytest.raises(ValueError):
            await ZoomClient.build_from_services(logger, cs, "inst-1")


# ============================================================================
# build_from_toolset
# ============================================================================


class TestBuildFromToolset:
    @pytest.mark.asyncio
    async def test_basic_toolset(self):
        toolset_config = {
            "credentials": {"access_token": "tok-xyz"},
            "auth": {"clientId": "cid", "clientSecret": "sec", "redirectUri": "https://redir"},
        }
        logger = MagicMock(spec=logging.Logger)
        client = await ZoomClient.build_from_toolset(toolset_config, logger)
        assert isinstance(client.client, ZoomRESTClientViaOAuth)

    @pytest.mark.asyncio
    async def test_missing_access_token_raises(self):
        toolset_config = {
            "credentials": {},
            "auth": {},
        }
        logger = MagicMock(spec=logging.Logger)
        with pytest.raises(ValueError, match="Access token not found"):
            await ZoomClient.build_from_toolset(toolset_config, logger)

    @pytest.mark.asyncio
    async def test_with_shared_oauth_config(self):
        toolset_config = {
            "credentials": {"access_token": "tok-123"},
            "auth": {"oauthConfigId": "shared-3", "clientId": "", "clientSecret": ""},
        }
        oauth_entries = [
            {
                "_id": "shared-3",
                "config": {
                    "clientId": "sc-cid",
                    "clientSecret": "sc-sec",
                    "redirectUri": "https://r.com",
                }
            }
        ]
        cs = _mock_config_service(None, oauth_entries)
        logger = MagicMock(spec=logging.Logger)
        client = await ZoomClient.build_from_toolset(toolset_config, logger, cs)
        assert isinstance(client.client, ZoomRESTClientViaOAuth)


# ============================================================================
# _find_shared_oauth_config
# ============================================================================


class TestFindSharedOAuthConfig:
    @pytest.mark.asyncio
    async def test_found(self):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=[
            {"_id": "cfg-1", "config": {"clientId": "cid-1"}},
            {"_id": "cfg-2", "config": {"clientId": "cid-2"}},
        ])
        logger = MagicMock()
        result = await ZoomClient._find_shared_oauth_config(cs, "cfg-2", logger)
        assert result is not None
        assert result.resolved_client_id() == "cid-2"

    @pytest.mark.asyncio
    async def test_not_found(self):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=[
            {"_id": "cfg-1", "config": {"clientId": "cid-1"}},
        ])
        logger = MagicMock()
        result = await ZoomClient._find_shared_oauth_config(cs, "cfg-missing", logger)
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        cs = AsyncMock()
        cs.get_config = AsyncMock(side_effect=Exception("fail"))
        logger = MagicMock()
        result = await ZoomClient._find_shared_oauth_config(cs, "x", logger)
        assert result is None

    @pytest.mark.asyncio
    async def test_non_list_returns_none(self):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value="not-a-list")
        logger = MagicMock()
        result = await ZoomClient._find_shared_oauth_config(cs, "x", logger)
        assert result is None


# ============================================================================
# _get_connector_config
# ============================================================================


class TestGetConnectorConfig:
    @pytest.mark.asyncio
    async def test_success(self):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={"auth": {"authType": "TOKEN"}})
        logger = MagicMock()
        result = await ZoomClient._get_connector_config(logger, cs, "inst-1")
        assert result == {"auth": {"authType": "TOKEN"}}

    @pytest.mark.asyncio
    async def test_empty_config_raises(self):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=None)
        logger = MagicMock()
        with pytest.raises(ValueError, match="Failed to get Zoom"):
            await ZoomClient._get_connector_config(logger, cs, "inst-1")

    @pytest.mark.asyncio
    async def test_exception_raises_value_error(self):
        cs = AsyncMock()
        cs.get_config = AsyncMock(side_effect=Exception("network error"))
        logger = MagicMock()
        with pytest.raises(ValueError, match="Failed to get Zoom"):
            await ZoomClient._get_connector_config(logger, cs, "inst-1")


# ============================================================================
# ZoomSharedOAuthConfigEntry resolvers
# ============================================================================


class TestZoomSharedOAuthConfigEntry:
    def test_resolved_account_id_camel(self):
        entry = ZoomSharedOAuthConfigEntry(accountId="acc-1")
        assert entry.resolved_account_id() == "acc-1"

    def test_resolved_account_id_snake(self):
        entry = ZoomSharedOAuthConfigEntry(account_id="acc-2")
        assert entry.resolved_account_id() == "acc-2"

    def test_resolved_account_id_fallback(self):
        entry = ZoomSharedOAuthConfigEntry()
        assert entry.resolved_account_id("default") == "default"

    def test_resolved_client_id(self):
        entry = ZoomSharedOAuthConfigEntry(client_id="cid")
        assert entry.resolved_client_id() == "cid"

    def test_resolved_client_secret(self):
        entry = ZoomSharedOAuthConfigEntry(client_secret="sec")
        assert entry.resolved_client_secret() == "sec"

    def test_resolved_redirect_uri(self):
        entry = ZoomSharedOAuthConfigEntry(redirect_uri="https://r.com")
        assert entry.resolved_redirect_uri() == "https://r.com"
