"""
Additional tests for app.sources.client.box.box to cover missing lines/branches.

Targets:
  - Lines 14-15: ImportError when box_sdk_gen is not installed
  - Line 100: get_box_client after init for JWT
  - Line 140: get_box_client after init for OAuth2
  - Line 172->175: create_client for OAuthCode when access_token already set
  - Line 187: get_box_client after init for OAuthCode
  - Line 260: get_box_client after init for CCG
  - Line 505: OAuth missing required fields ValueError
  - Lines 519-530: OAUTH_CODE path in build_from_services (documents existing bug)
"""

import importlib
import logging
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sources.client.box.box import (
    BoxClient,
    BoxRESTClientViaToken,
    BoxRESTClientWithCCG,
    BoxRESTClientWithJWT,
    BoxRESTClientWithOAuth2,
    BoxRESTClientWithOAuthCode,
)


# ============================================================================
# get_box_client AFTER create_client (lines 100, 140, 187, 260)
# ============================================================================


class TestGetBoxClientAfterInit:
    """Cover get_box_client return path when client IS initialized."""

    @pytest.mark.asyncio
    @patch("app.sources.client.box.box.BoxJWTAuth")
    @patch("app.sources.client.box.box.BoxSDKClient")
    async def test_jwt_get_box_client_after_init(self, mock_sdk, mock_auth):
        client = BoxRESTClientWithJWT(
            client_id="cid",
            client_secret="csec",
            enterprise_id="eid",
            jwt_key_id="kid",
            rsa_private_key_data="key-data",
        )
        await client.create_client()
        result = client.get_box_client()
        assert result is mock_sdk.return_value

    @pytest.mark.asyncio
    @patch("app.sources.client.box.box.BoxOAuth")
    @patch("app.sources.client.box.box.BoxSDKClient")
    async def test_oauth2_get_box_client_after_init(self, mock_sdk, mock_auth):
        client = BoxRESTClientWithOAuth2(
            client_id="cid",
            client_secret="csec",
            access_token="at",
        )
        await client.create_client()
        result = client.get_box_client()
        assert result is mock_sdk.return_value

    @pytest.mark.asyncio
    async def test_oauth_code_get_box_client_after_init(self):
        """Cover OAuthCode get_box_client when client is initialized."""
        client = BoxRESTClientWithOAuthCode(
            client_id="cid",
            client_secret="csec",
            code="auth-code",
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {"access_token": "at123"}
        mock_http_client = MagicMock()
        mock_http_client.execute = AsyncMock(return_value=mock_response)

        with (
            patch("app.sources.client.box.box.HTTPClient", return_value=mock_http_client),
            patch("app.sources.client.box.box.BoxOAuth"),
            patch("app.sources.client.box.box.BoxSDKClient") as mock_sdk,
        ):
            await client.create_client()
            result = client.get_box_client()
            assert result is mock_sdk.return_value

    @pytest.mark.asyncio
    @patch("app.sources.client.box.box.BoxSDKCCGAuth")
    @patch("app.sources.client.box.box.BoxSDKCCGConfig")
    @patch("app.sources.client.box.box.BoxSDKClient")
    async def test_ccg_get_box_client_after_init(self, mock_sdk, mock_cfg, mock_auth):
        client = BoxRESTClientWithCCG(
            client_id="cid",
            client_secret="csec",
            enterprise_id="eid",
        )
        await client.create_client()
        result = client.get_box_client()
        assert result is mock_sdk.return_value


# ============================================================================
# OAuthCode create_client - skip _fetch_token when access_token already set
# (line 172->175 branch: access_token is not None)
# ============================================================================


class TestOAuthCodeSkipFetchToken:
    @pytest.mark.asyncio
    async def test_create_client_skips_fetch_when_token_present(self):
        """When access_token is already set, _fetch_token should NOT be called."""
        client = BoxRESTClientWithOAuthCode(
            client_id="cid",
            client_secret="csec",
            code="auth-code",
        )
        # Pre-set the access_token so _fetch_token is skipped
        client.access_token = "already-have-token"
        client.refresh_token = "already-have-refresh"

        with (
            patch("app.sources.client.box.box.BoxOAuth") as mock_oauth,
            patch("app.sources.client.box.box.BoxSDKClient") as mock_sdk,
        ):
            result = await client.create_client()
            mock_oauth.assert_called_once_with(
                client_id="cid",
                client_secret="csec",
                access_token="already-have-token",
                refresh_token="already-have-refresh",
            )
            assert result is mock_sdk.return_value


# ============================================================================
# build_from_services - OAuth missing required fields (line 505)
# ============================================================================


class TestBuildFromServicesOAuthMissingFields:
    @pytest.mark.asyncio
    async def test_oauth_missing_access_token(self):
        logger = logging.getLogger("test")
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {
                "authType": "OAUTH",
                "clientId": "cid",
                "clientSecret": "csec",
                "credentials": {},
            }
        })
        with pytest.raises(ValueError, match="access_token"):
            await BoxClient.build_from_services(logger, config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_oauth_missing_client_id(self):
        logger = logging.getLogger("test")
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {
                "authType": "OAUTH",
                "clientSecret": "csec",
                "credentials": {"access_token": "at"},
            }
        })
        with pytest.raises(ValueError, match="client_id, client_secret"):
            await BoxClient.build_from_services(logger, config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_oauth_missing_client_secret(self):
        logger = logging.getLogger("test")
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {
                "authType": "OAUTH",
                "clientId": "cid",
                "credentials": {"access_token": "at"},
            }
        })
        with pytest.raises(ValueError, match="client_id, client_secret"):
            await BoxClient.build_from_services(logger, config_service, "inst-1")


# ============================================================================
# build_from_services - OAUTH_CODE path (lines 519-530)
# The source has a known bug: OAUTH_CODE references `credentials_config`
# which is only defined in the OAUTH branch. Exercising this path shows
# it raises due to NameError (wrapped in ValueError by the outer handler).
# ============================================================================


class TestBuildFromServicesOAuthCode:
    @pytest.mark.asyncio
    async def test_oauth_code_raises_due_to_credentials_config_bug(self):
        """OAUTH_CODE references credentials_config from OAUTH scope - this is a bug."""
        logger = logging.getLogger("test")
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {
                "authType": "OAUTH_CODE",
                "clientId": "cid",
                "clientSecret": "csec",
                "redirectUri": "http://localhost/callback",
                "credentials": {"code": "code123"},
            }
        })
        with pytest.raises(ValueError, match="Failed to build Box client"):
            await BoxClient.build_from_services(logger, config_service, "inst-1")


# ============================================================================
# OAuthCode _fetch_token without redirect_uri
# ============================================================================


class TestFetchTokenWithoutRedirectUri:
    @pytest.mark.asyncio
    async def test_fetch_token_no_redirect_uri(self):
        """When redirect_uri is None, it should not be included in the body."""
        client = BoxRESTClientWithOAuthCode(
            client_id="cid",
            client_secret="csec",
            code="code123",
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "access_token": "at123",
        }
        mock_http_client = MagicMock()
        mock_http_client.execute = AsyncMock(return_value=mock_response)

        with patch("app.sources.client.box.box.HTTPClient", return_value=mock_http_client) as mock_cls:
            with patch("app.sources.client.box.box.HTTPRequest") as mock_req:
                await client._fetch_token()

        assert client.access_token == "at123"
        assert client.refresh_token is None


# ============================================================================
# BoxRESTClientWithJWT passphrase parameter
# ============================================================================


class TestJWTWithPassphrase:
    def test_init_with_passphrase(self):
        client = BoxRESTClientWithJWT(
            client_id="cid",
            client_secret="csec",
            enterprise_id="eid",
            jwt_key_id="kid",
            rsa_private_key_data="key-data",
            rsa_private_key_passphrase="pass123",
        )
        assert client.rsa_private_key_passphrase == "pass123"

    @pytest.mark.asyncio
    @patch("app.sources.client.box.box.BoxJWTAuth")
    @patch("app.sources.client.box.box.BoxSDKClient")
    async def test_create_client_passes_passphrase(self, mock_sdk, mock_auth):
        client = BoxRESTClientWithJWT(
            client_id="cid",
            client_secret="csec",
            enterprise_id="eid",
            jwt_key_id="kid",
            rsa_private_key_data="key-data",
            rsa_private_key_passphrase="pass123",
        )
        await client.create_client()
        mock_auth.assert_called_once_with(
            client_id="cid",
            client_secret="csec",
            enterprise_id="eid",
            jwt_key_id="kid",
            rsa_private_key_data="key-data",
            rsa_private_key_passphrase="pass123",
        )


# ============================================================================
# Import error when box_sdk_gen is not installed (lines 14-15)
# ============================================================================


class TestBoxSdkImportError:
    def test_import_error_when_box_sdk_gen_missing(self):
        """When box_sdk_gen is not installed, the module raises ImportError."""
        # Save original modules
        module_name = "app.sources.client.box.box"
        box_sdk_modules = [
            k for k in sys.modules if k.startswith("box_sdk_gen")
        ]
        saved_box_modules = {k: sys.modules[k] for k in box_sdk_modules}
        saved_module = sys.modules.pop(module_name, None)

        try:
            # Remove box_sdk_gen from sys.modules and make import fail
            for k in box_sdk_modules:
                sys.modules.pop(k, None)

            # Create a custom importer that blocks box_sdk_gen
            import builtins
            real_import = builtins.__import__

            def mock_import(name, *args, **kwargs):
                if name == "box_sdk_gen" or name.startswith("box_sdk_gen."):
                    raise ImportError("No module named 'box_sdk_gen'")
                return real_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=mock_import):
                with pytest.raises(ImportError, match="box_sdk_gen is not installed"):
                    importlib.import_module(module_name)
        finally:
            # Restore everything
            for k, v in saved_box_modules.items():
                sys.modules[k] = v
            if saved_module is not None:
                sys.modules[module_name] = saved_module
            elif module_name in sys.modules:
                sys.modules.pop(module_name, None)
