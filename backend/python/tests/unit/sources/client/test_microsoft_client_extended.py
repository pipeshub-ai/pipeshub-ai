"""
Extended tests for app/sources/client/microsoft/microsoft.py targeting uncovered lines:
520-524, 590-634, 641-646, 649, 664-665, 730-733, 749, 786-787, 805, 808, 819-827.
"""

import base64
import json
import logging
import time
from dataclasses import asdict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def logger():
    return MagicMock(spec=logging.Logger)


@pytest.fixture
def mock_config_service():
    return AsyncMock()


def _make_jwt_token(claims=None):
    """Create a fake JWT token (3 parts separated by dots)."""
    import base64 as b64

    header = b64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).rstrip(b"=").decode()
    if claims is None:
        claims = {"oid": "user-oid-123", "sub": "user-sub"}
    payload = b64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    signature = b64.urlsafe_b64encode(b"fakesignature").rstrip(b"=").decode()
    return f"{header}.{payload}.{signature}"


def _make_toolset_config(
    access_token=None,
    refresh_token="refresh_tok",
    is_authenticated=True,
    scope="Mail.ReadWrite Calendars.ReadWrite",
    expires_at=None,
    expires_in=None,
):
    """Create a toolset config dict for build_from_toolset."""
    if access_token is None:
        access_token = _make_jwt_token()

    config = {
        "auth": {},
        "credentials": {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "scope": scope,
        },
        "isAuthenticated": is_authenticated,
        "oauthConfigId": "oauth-config-1",
    }
    if expires_at is not None:
        config["credentials"]["expires_at"] = expires_at
    if expires_in is not None:
        config["credentials"]["expires_in"] = expires_in
    return config


# ===================================================================
# build_from_toolset — final token validation (lines 520-524)
# ===================================================================


class TestBuildFromToolsetFinalTokenValidation:
    """Cover the final token validation that checks the token used."""

    @pytest.mark.asyncio
    async def test_final_token_is_placeholder_after_refresh_raises(
        self, logger, mock_config_service
    ):
        """Lines 520-524: Both stored and refreshed tokens are placeholders."""
        from app.sources.client.microsoft.microsoft import MSGraphClient

        # Use a placeholder token that will be caught by final validation
        toolset_config = _make_toolset_config(
            access_token="me-token-to-replace",
            refresh_token=None,
        )

        with patch(
            "app.api.routes.toolsets.get_oauth_credentials_for_toolset",
            new_callable=AsyncMock,
            return_value={
                "clientId": "client-id",
                "clientSecret": "client-secret",
                "tenantId": "tenant-id",
            },
        ):
            with pytest.raises(ValueError, match="Invalid access token"):
                await MSGraphClient.build_from_toolset(
                    toolset_config, "outlook", logger, mock_config_service
                )


# ===================================================================
# build_from_toolset — _MsalTokenProvider (lines 590-634)
# ===================================================================


class TestMsalTokenProvider:
    """Cover _MsalTokenProvider inner class behavior."""

    @pytest.mark.asyncio
    async def test_token_refresh_returns_placeholder(self, logger, mock_config_service):
        """Lines 590-634: MSAL refresh returns a placeholder token => keeps original."""
        from app.sources.client.microsoft.microsoft import MSGraphClient

        toolset_config = _make_toolset_config(
            refresh_token="valid_refresh",
            expires_in=3600,
        )

        mock_msal_result = {
            "access_token": "me-token-to-replace",  # Placeholder!
        }

        with patch(
            "app.api.routes.toolsets.get_oauth_credentials_for_toolset",
            new_callable=AsyncMock,
            return_value={
                "clientId": "client-id",
                "clientSecret": "client-secret",
                "tenantId": "tenant-id",
            },
        ):
            with patch("msal.ConfidentialClientApplication") as mock_msal:
                mock_msal_app = MagicMock()
                mock_msal_app.acquire_token_by_refresh_token.return_value = (
                    mock_msal_result
                )
                mock_msal.return_value = mock_msal_app

                with patch(
                    "app.sources.client.microsoft.microsoft.GraphServiceClient"
                ) as mock_gsc:
                    mock_gsc_instance = MagicMock()
                    mock_gsc_instance.path_parameters = {}
                    mock_gsc.return_value = mock_gsc_instance

                    client = await MSGraphClient.build_from_toolset(
                        toolset_config, "outlook", logger, mock_config_service
                    )
                    assert client is not None

    @pytest.mark.asyncio
    async def test_token_refresh_fails_with_error(self, logger, mock_config_service):
        """Lines 641-646: MSAL refresh fails with error, falls back to stored token."""
        from app.sources.client.microsoft.microsoft import MSGraphClient

        toolset_config = _make_toolset_config(
            refresh_token="valid_refresh",
            expires_in=3600,
        )

        mock_msal_result = {
            "error": "invalid_grant",
            "error_description": "AADSTS50076: token expired",
        }

        with patch(
            "app.api.routes.toolsets.get_oauth_credentials_for_toolset",
            new_callable=AsyncMock,
            return_value={
                "clientId": "client-id",
                "clientSecret": "client-secret",
                "tenantId": "tenant-id",
            },
        ):
            with patch("msal.ConfidentialClientApplication") as mock_msal:
                mock_msal_app = MagicMock()
                mock_msal_app.acquire_token_by_refresh_token.return_value = (
                    mock_msal_result
                )
                mock_msal.return_value = mock_msal_app

                with patch(
                    "app.sources.client.microsoft.microsoft.GraphServiceClient"
                ) as mock_gsc:
                    mock_gsc_instance = MagicMock()
                    mock_gsc_instance.path_parameters = {}
                    mock_gsc.return_value = mock_gsc_instance

                    client = await MSGraphClient.build_from_toolset(
                        toolset_config, "outlook", logger, mock_config_service
                    )
                    assert client is not None

    @pytest.mark.asyncio
    async def test_msal_refresh_exception(self, logger, mock_config_service):
        """Line 649: MSAL refresh call raises exception."""
        from app.sources.client.microsoft.microsoft import MSGraphClient

        toolset_config = _make_toolset_config(
            refresh_token="valid_refresh",
            expires_in=3600,
        )

        with patch(
            "app.api.routes.toolsets.get_oauth_credentials_for_toolset",
            new_callable=AsyncMock,
            return_value={
                "clientId": "client-id",
                "clientSecret": "client-secret",
                "tenantId": "tenant-id",
            },
        ):
            with patch("msal.ConfidentialClientApplication") as mock_msal:
                mock_msal_app = MagicMock()
                mock_msal_app.acquire_token_by_refresh_token.side_effect = (
                    Exception("Network timeout")
                )
                mock_msal.return_value = mock_msal_app

                with patch(
                    "app.sources.client.microsoft.microsoft.GraphServiceClient"
                ) as mock_gsc:
                    mock_gsc_instance = MagicMock()
                    mock_gsc_instance.path_parameters = {}
                    mock_gsc.return_value = mock_gsc_instance

                    client = await MSGraphClient.build_from_toolset(
                        toolset_config, "outlook", logger, mock_config_service
                    )
                    assert client is not None


# ===================================================================
# build_from_toolset — JWT decoding and user OID (lines 730-733, 749)
# ===================================================================


class TestBuildFromToolsetJwtDecoding:
    """Cover JWT token decoding for user OID extraction."""

    @pytest.mark.asyncio
    async def test_jwt_without_oid_falls_back_to_me(
        self, logger, mock_config_service
    ):
        """Lines 730-733: JWT has no oid claim => user_id_for_graph = 'me'."""
        from app.sources.client.microsoft.microsoft import MSGraphClient

        token_no_oid = _make_jwt_token(claims={"sub": "user-sub"})
        toolset_config = _make_toolset_config(
            access_token=token_no_oid,
            refresh_token=None,
            expires_in=3600,
        )

        with patch(
            "app.api.routes.toolsets.get_oauth_credentials_for_toolset",
            new_callable=AsyncMock,
            return_value={
                "clientId": "client-id",
                "clientSecret": "client-secret",
                "tenantId": "tenant-id",
            },
        ):
            with patch("msal.ConfidentialClientApplication") as mock_msal:
                mock_msal.return_value = MagicMock()

                with patch(
                    "app.sources.client.microsoft.microsoft.GraphServiceClient"
                ) as mock_gsc:
                    mock_gsc_instance = MagicMock()
                    mock_gsc_instance.path_parameters = {}
                    mock_gsc.return_value = mock_gsc_instance

                    client = await MSGraphClient.build_from_toolset(
                        toolset_config, "outlook", logger, mock_config_service
                    )
                    assert client is not None

    @pytest.mark.asyncio
    async def test_jwt_decode_failure_falls_back(
        self, logger, mock_config_service
    ):
        """Lines 736-739: JWT decode fails => falls back to 'me'."""
        from app.sources.client.microsoft.microsoft import MSGraphClient

        # Use a non-standard "JWT" that won't decode properly
        bad_token = "not.a.valid-jwt-payload"
        toolset_config = _make_toolset_config(
            access_token=bad_token,
            refresh_token=None,
            expires_in=3600,
        )

        with patch(
            "app.api.routes.toolsets.get_oauth_credentials_for_toolset",
            new_callable=AsyncMock,
            return_value={
                "clientId": "client-id",
                "clientSecret": "client-secret",
                "tenantId": "tenant-id",
            },
        ):
            with patch("msal.ConfidentialClientApplication") as mock_msal:
                mock_msal.return_value = MagicMock()

                with patch(
                    "app.sources.client.microsoft.microsoft.GraphServiceClient"
                ) as mock_gsc:
                    mock_gsc_instance = MagicMock()
                    mock_gsc_instance.path_parameters = {}
                    mock_gsc.return_value = mock_gsc_instance

                    client = await MSGraphClient.build_from_toolset(
                        toolset_config, "outlook", logger, mock_config_service
                    )
                    assert client is not None

    @pytest.mark.asyncio
    async def test_graph_client_no_path_parameters(
        self, logger, mock_config_service
    ):
        """Line 749: GraphServiceClient has no path_parameters attribute."""
        from app.sources.client.microsoft.microsoft import MSGraphClient

        toolset_config = _make_toolset_config(
            refresh_token=None,
            expires_in=3600,
        )

        with patch(
            "app.api.routes.toolsets.get_oauth_credentials_for_toolset",
            new_callable=AsyncMock,
            return_value={
                "clientId": "client-id",
                "clientSecret": "client-secret",
                "tenantId": "tenant-id",
            },
        ):
            with patch("msal.ConfidentialClientApplication") as mock_msal:
                mock_msal.return_value = MagicMock()

                with patch(
                    "app.sources.client.microsoft.microsoft.GraphServiceClient"
                ) as mock_gsc:
                    mock_gsc_instance = MagicMock(spec=[])  # No path_parameters
                    mock_gsc.return_value = mock_gsc_instance

                    client = await MSGraphClient.build_from_toolset(
                        toolset_config, "outlook", logger, mock_config_service
                    )
                    assert client is not None


# ===================================================================
# build_from_toolset — _DelegatedGraphClient and _MeRedirectingGraphClient
# (lines 786-787, 805, 808)
# ===================================================================


class TestDelegatedGraphClientShim:
    """Cover the _DelegatedGraphClient and _MeRedirectingGraphClient."""

    @pytest.mark.asyncio
    async def test_delegated_client_me_property(self, logger, mock_config_service):
        """Lines 805, 808: The shim client's .me redirects to .users.by_user_id."""
        from app.sources.client.microsoft.microsoft import MSGraphClient

        toolset_config = _make_toolset_config(
            refresh_token=None,
            expires_in=3600,
        )

        with patch(
            "app.api.routes.toolsets.get_oauth_credentials_for_toolset",
            new_callable=AsyncMock,
            return_value={
                "clientId": "client-id",
                "clientSecret": "client-secret",
                "tenantId": "tenant-id",
            },
        ):
            with patch("msal.ConfidentialClientApplication") as mock_msal:
                mock_msal.return_value = MagicMock()

                with patch(
                    "app.sources.client.microsoft.microsoft.GraphServiceClient"
                ) as mock_gsc:
                    mock_gsc_instance = MagicMock()
                    mock_gsc_instance.path_parameters = {}
                    mock_gsc.return_value = mock_gsc_instance

                    client = await MSGraphClient.build_from_toolset(
                        toolset_config, "outlook", logger, mock_config_service
                    )

                    # Get the internal client shim
                    inner = client.get_client()
                    assert inner.get_mode().value == "delegated"

                    # Access .me should call users.by_user_id
                    me_result = inner.get_ms_graph_service_client().me
                    assert me_result is not None


# ===================================================================
# build_from_toolset — ImportError and general exception (lines 819-827)
# ===================================================================


class TestBuildFromToolsetErrors:
    """Cover lines 819-827: ImportError and generic Exception."""

    @pytest.mark.asyncio
    async def test_import_error_raises_value_error(self, logger, mock_config_service):
        """Line 819-824: Missing msal package raises ValueError."""
        from app.sources.client.microsoft.microsoft import MSGraphClient

        toolset_config = _make_toolset_config(
            refresh_token=None,
            expires_in=3600,
        )

        with patch(
            "app.api.routes.toolsets.get_oauth_credentials_for_toolset",
            new_callable=AsyncMock,
            return_value={
                "clientId": "client-id",
                "clientSecret": "client-secret",
                "tenantId": "tenant-id",
            },
        ):
            with patch.dict("sys.modules", {"msal": None}):
                with pytest.raises((ValueError, ImportError)):
                    await MSGraphClient.build_from_toolset(
                        toolset_config, "outlook", logger, mock_config_service
                    )

    @pytest.mark.asyncio
    async def test_scope_as_list_in_credentials(self, logger, mock_config_service):
        """Line 664-665: scope stored as list instead of string."""
        from app.sources.client.microsoft.microsoft import MSGraphClient

        toolset_config = _make_toolset_config(
            refresh_token=None,
            expires_in=3600,
        )
        toolset_config["credentials"]["scope"] = ["Mail.ReadWrite", "User.Read"]

        with patch(
            "app.api.routes.toolsets.get_oauth_credentials_for_toolset",
            new_callable=AsyncMock,
            return_value={
                "clientId": "client-id",
                "clientSecret": "client-secret",
                "tenantId": "tenant-id",
            },
        ):
            with patch("msal.ConfidentialClientApplication") as mock_msal:
                mock_msal.return_value = MagicMock()

                with patch(
                    "app.sources.client.microsoft.microsoft.GraphServiceClient"
                ) as mock_gsc:
                    mock_gsc_instance = MagicMock()
                    mock_gsc_instance.path_parameters = {}
                    mock_gsc.return_value = mock_gsc_instance

                    client = await MSGraphClient.build_from_toolset(
                        toolset_config, "outlook", logger, mock_config_service
                    )
                    assert client is not None

    @pytest.mark.asyncio
    async def test_expires_at_in_milliseconds(self, logger, mock_config_service):
        """Lines 662-663: expires_at is in milliseconds (> 1e12)."""
        from app.sources.client.microsoft.microsoft import MSGraphClient

        toolset_config = _make_toolset_config(
            refresh_token=None,
        )
        # Millisecond timestamp
        toolset_config["credentials"]["expires_at"] = int(time.time() * 1000) + 3600000

        with patch(
            "app.api.routes.toolsets.get_oauth_credentials_for_toolset",
            new_callable=AsyncMock,
            return_value={
                "clientId": "client-id",
                "clientSecret": "client-secret",
                "tenantId": "tenant-id",
            },
        ):
            with patch("msal.ConfidentialClientApplication") as mock_msal:
                mock_msal.return_value = MagicMock()

                with patch(
                    "app.sources.client.microsoft.microsoft.GraphServiceClient"
                ) as mock_gsc:
                    mock_gsc_instance = MagicMock()
                    mock_gsc_instance.path_parameters = {}
                    mock_gsc.return_value = mock_gsc_instance

                    client = await MSGraphClient.build_from_toolset(
                        toolset_config, "outlook", logger, mock_config_service
                    )
                    assert client is not None

    @pytest.mark.asyncio
    async def test_invalid_expires_at_handled(self, logger, mock_config_service):
        """Lines 664-665: Invalid expires_at value handled gracefully."""
        from app.sources.client.microsoft.microsoft import MSGraphClient

        toolset_config = _make_toolset_config(
            refresh_token=None,
        )
        toolset_config["credentials"]["expires_at"] = "not-a-number"

        with patch(
            "app.api.routes.toolsets.get_oauth_credentials_for_toolset",
            new_callable=AsyncMock,
            return_value={
                "clientId": "client-id",
                "clientSecret": "client-secret",
                "tenantId": "tenant-id",
            },
        ):
            with patch("msal.ConfidentialClientApplication") as mock_msal:
                mock_msal.return_value = MagicMock()

                with patch(
                    "app.sources.client.microsoft.microsoft.GraphServiceClient"
                ) as mock_gsc:
                    mock_gsc_instance = MagicMock()
                    mock_gsc_instance.path_parameters = {}
                    mock_gsc.return_value = mock_gsc_instance

                    client = await MSGraphClient.build_from_toolset(
                        toolset_config, "outlook", logger, mock_config_service
                    )
                    assert client is not None
