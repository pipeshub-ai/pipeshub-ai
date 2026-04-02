"""
Extended tests for app.sources.client.box.box covering missing lines:
- BoxRESTClientWithJWT.get_box_client not initialized
- BoxRESTClientWithOAuth2.get_box_client not initialized
- BoxRESTClientWithOAuthCode (create_client, _fetch_token flow)
- BoxRESTClientWithCCG.get_box_client not initialized
- BoxClient.build_from_services OAUTH_CODE and unsupported types
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sources.client.box.box import (
    BoxCCGConfig,
    BoxClient,
    BoxJWTConfig,
    BoxOAuth2Config,
    BoxOAuthCodeConfig,
    BoxRESTClientViaToken,
    BoxRESTClientWithCCG,
    BoxRESTClientWithJWT,
    BoxRESTClientWithOAuth2,
    BoxRESTClientWithOAuthCode,
    BoxResponse,
    BoxTokenConfig,
)


# ============================================================================
# get_box_client not initialized
# ============================================================================


class TestGetBoxClientNotInitialized:
    def test_jwt_client_not_initialized(self):
        client = BoxRESTClientWithJWT(
            client_id="cid",
            client_secret="csecret",
            enterprise_id="eid",
            jwt_key_id="kid",
            rsa_private_key_data="key_data",
        )
        with pytest.raises(RuntimeError, match="Client not initialized"):
            client.get_box_client()

    def test_oauth2_client_not_initialized(self):
        client = BoxRESTClientWithOAuth2(
            client_id="cid",
            client_secret="csecret",
            access_token="token",
        )
        with pytest.raises(RuntimeError, match="Client not initialized"):
            client.get_box_client()

    def test_oauth_code_client_not_initialized(self):
        client = BoxRESTClientWithOAuthCode(
            client_id="cid",
            client_secret="csecret",
            code="code123",
        )
        with pytest.raises(RuntimeError, match="Client not initialized"):
            client.get_box_client()

    def test_ccg_client_not_initialized(self):
        client = BoxRESTClientWithCCG(
            client_id="cid",
            client_secret="csecret",
            enterprise_id="eid",
        )
        with pytest.raises(RuntimeError, match="Client not initialized"):
            client.get_box_client()


# ============================================================================
# BoxRESTClientWithOAuthCode._fetch_token and create_client
# ============================================================================


class TestBoxRESTClientWithOAuthCode:
    @pytest.mark.asyncio
    async def test_fetch_token_with_redirect_uri(self):
        client = BoxRESTClientWithOAuthCode(
            client_id="cid",
            client_secret="csecret",
            code="code123",
            redirect_uri="http://localhost/callback",
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "access_token": "at123",
            "refresh_token": "rt123",
        }

        mock_http_client = MagicMock()
        mock_http_client.execute = AsyncMock(return_value=mock_response)

        with patch("app.sources.client.box.box.HTTPClient", return_value=mock_http_client):
            await client._fetch_token()

        assert client.access_token == "at123"
        assert client.refresh_token == "rt123"

    @pytest.mark.asyncio
    async def test_create_client_calls_fetch_token(self):
        client = BoxRESTClientWithOAuthCode(
            client_id="cid",
            client_secret="csecret",
            code="code123",
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "access_token": "at123",
        }

        mock_http_client = MagicMock()
        mock_http_client.execute = AsyncMock(return_value=mock_response)

        with (
            patch("app.sources.client.box.box.HTTPClient", return_value=mock_http_client),
            patch("app.sources.client.box.box.BoxOAuth"),
            patch("app.sources.client.box.box.BoxSDKClient"),
        ):
            await client.create_client()
            assert client.access_token == "at123"


# ============================================================================
# build_from_services - OAUTH_CODE and unsupported auth
# ============================================================================


class TestBuildFromServicesExtended:
    @pytest.mark.asyncio
    async def test_oauth_code_auth_bug_in_source(self):
        """The source code has a bug: OAUTH_CODE references credentials_config
        which is only defined inside the OAUTH branch. This test documents the bug."""
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {
                "authType": "OAUTH_CODE",
                "clientId": "cid",
                "clientSecret": "csecret",
                "redirectUri": "http://localhost/callback",
                "credentials": {"code": "code123"},
            }
        })
        logger = logging.getLogger("test")

        # This will raise because credentials_config is referenced before assignment
        with pytest.raises(ValueError, match="Failed to build Box client"):
            await BoxClient.build_from_services(logger, config_service, "inst1")

    @pytest.mark.asyncio
    async def test_unsupported_auth_type(self):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {"authType": "UNKNOWN"},
        })
        logger = logging.getLogger("test")
        with pytest.raises(ValueError, match="Unsupported auth_type"):
            await BoxClient.build_from_services(logger, config_service, "inst1")
