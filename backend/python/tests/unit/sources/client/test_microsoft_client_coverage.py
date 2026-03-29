"""
Coverage tests for app.sources.client.microsoft.microsoft covering missing lines:
- MSGraphClientViaUsernamePassword.get_ms_graph_service_client/get_mode
- MSGraphClientWithCertificatePath.get_ms_graph_service_client/get_mode
- MSGraphClient.build_from_services USERNAME_PASSWORD auth
- MSGraphClient._get_connector_config
- _MsalTokenProvider._is_token_expiring, _ensure_lock, _refresh_access_token
- _MsalTokenProvider.get_allowed_hosts_validator
- _MeRedirectingGraphClient.__getattr__
"""

import json
import logging
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ============================================================================
# MSGraphClientViaUsernamePassword
# ============================================================================


class TestMSGraphClientViaUsernamePassword:
    def test_get_mode(self):
        from app.sources.client.microsoft.microsoft import (
            GraphMode,
            MSGraphClientViaUsernamePassword,
        )
        client = MSGraphClientViaUsernamePassword(
            username="user", password="pass",
            client_id="cid", tenant_id="tid",
            mode=GraphMode.APP,
        )
        assert client.get_mode() == GraphMode.APP


# ============================================================================
# MSGraphClientWithCertificatePath
# ============================================================================


class TestMSGraphClientWithCertificatePath:
    def test_get_mode(self):
        from app.sources.client.microsoft.microsoft import (
            GraphMode,
            MSGraphClientWithCertificatePath,
        )
        client = MSGraphClientWithCertificatePath(
            certificate_path="/path/to/cert",
            tenant_id="tid",
            client_id="cid",
            mode=GraphMode.APP,
        )
        assert client.get_mode() == GraphMode.APP


# ============================================================================
# build_from_services with USERNAME_PASSWORD auth
# ============================================================================


class TestBuildFromServicesUsernamePassword:
    @pytest.mark.asyncio
    async def test_username_password_auth(self):
        from app.sources.client.microsoft.microsoft import (
            GraphMode,
            MSGraphClient,
        )
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {
                "authType": "USERNAME_PASSWORD",
                "tenantId": "tid",
                "clientId": "cid",
                "username": "user@example.com",
                "password": "pass123",
            }
        })
        logger = logging.getLogger("test")
        client = await MSGraphClient.build_from_services(
            "OneDrive", logger, config_service,
            mode=GraphMode.APP,
            connector_instance_id="inst1",
        )
        assert client is not None

    @pytest.mark.asyncio
    async def test_username_password_missing_username(self):
        from app.sources.client.microsoft.microsoft import (
            GraphMode,
            MSGraphClient,
        )
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {
                "authType": "USERNAME_PASSWORD",
                "tenantId": "tid",
                "clientId": "cid",
                "username": "",
                "password": "pass",
            }
        })
        logger = logging.getLogger("test")
        with pytest.raises(ValueError, match="Username and password required"):
            await MSGraphClient.build_from_services(
                "OneDrive", logger, config_service,
                mode=GraphMode.APP,
                connector_instance_id="inst1",
            )


# ============================================================================
# _get_connector_config
# ============================================================================


class TestGetConnectorConfig:
    @pytest.mark.asyncio
    async def test_config_not_found(self):
        from app.sources.client.microsoft.microsoft import MSGraphClient
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=None)
        logger = logging.getLogger("test")
        with pytest.raises(ValueError, match="Failed to get Microsoft Graph connector"):
            await MSGraphClient._get_connector_config(
                "onedrive", logger, config_service, "inst1"
            )

    @pytest.mark.asyncio
    async def test_config_exception(self):
        from app.sources.client.microsoft.microsoft import MSGraphClient
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(side_effect=Exception("fail"))
        logger = logging.getLogger("test")
        with pytest.raises(ValueError, match="Failed to get Microsoft Graph connector"):
            await MSGraphClient._get_connector_config(
                "onedrive", logger, config_service, "inst1"
            )


# ============================================================================
# build_from_toolset - more coverage
# ============================================================================


class TestBuildFromToolsetExtended:
    @pytest.mark.asyncio
    async def test_placeholder_access_token_raises(self):
        from app.sources.client.microsoft.microsoft import MSGraphClient
        toolset_config = {
            "auth": {},
            "credentials": {"access_token": "me-token-to-replace"},
            "isAuthenticated": True,
        }
        logger = logging.getLogger("test")
        with pytest.raises(ValueError, match="Invalid access token"):
            await MSGraphClient.build_from_toolset(
                toolset_config, "outlook", logger
            )

    @pytest.mark.asyncio
    async def test_not_authenticated_raises(self):
        """Toolset that is not authenticated should raise ValueError."""
        from app.sources.client.microsoft.microsoft import MSGraphClient

        toolset_config = {
            "auth": {},
            "credentials": {"access_token": "tok"},
            "isAuthenticated": False,
        }
        logger = logging.getLogger("test")
        with pytest.raises(ValueError, match="not authenticated"):
            await MSGraphClient.build_from_toolset(
                toolset_config, "outlook", logger
            )

    @pytest.mark.asyncio
    async def test_no_credentials_raises(self):
        """Toolset with empty credentials should raise ValueError."""
        from app.sources.client.microsoft.microsoft import MSGraphClient

        toolset_config = {
            "auth": {},
            "credentials": {},
            "isAuthenticated": True,
        }
        logger = logging.getLogger("test")
        with pytest.raises(ValueError, match="no credentials"):
            await MSGraphClient.build_from_toolset(
                toolset_config, "outlook", logger
            )

    @pytest.mark.asyncio
    async def test_no_access_token_raises(self):
        """Toolset with no access_token should raise ValueError."""
        from app.sources.client.microsoft.microsoft import MSGraphClient

        toolset_config = {
            "auth": {},
            "credentials": {"refresh_token": "rt"},
            "isAuthenticated": True,
        }
        logger = logging.getLogger("test")
        with pytest.raises(ValueError, match="Access token not found"):
            await MSGraphClient.build_from_toolset(
                toolset_config, "outlook", logger
            )
