"""Unit tests for Microsoft Graph client module."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sources.client.microsoft.microsoft import (
    GraphMode,
    MSGraphClient,
    MSGraphClientWithCertificatePath,
    MSGraphClientWithCertificatePathConfig,
    MSGraphClientWithClientIdSecret,
    MSGraphClientWithClientIdSecretConfig,
    MSGraphClientViaUsernamePassword,
    MSGraphResponse,
    MSGraphUsernamePasswordConfig,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def logger():
    return logging.getLogger("test_microsoft_client")


@pytest.fixture
def mock_config_service():
    return AsyncMock()


# ---------------------------------------------------------------------------
# GraphMode enum
# ---------------------------------------------------------------------------


class TestGraphMode:
    def test_delegated(self):
        assert GraphMode.DELEGATED == "delegated"

    def test_app(self):
        assert GraphMode.APP == "app"

    def test_is_str(self):
        assert isinstance(GraphMode.APP, str)


# ---------------------------------------------------------------------------
# MSGraphResponse
# ---------------------------------------------------------------------------


class TestMSGraphResponse:
    def test_success(self):
        resp = MSGraphResponse(success=True, data={"key": "val"})
        assert resp.success is True
        assert resp.data == {"key": "val"}

    def test_error(self):
        resp = MSGraphResponse(success=False, error="oops")
        assert resp.error == "oops"

    def test_success_with_error_raises(self):
        with pytest.raises(ValueError, match="cannot be successful and have an error"):
            MSGraphResponse(success=True, error="oops")


# ---------------------------------------------------------------------------
# REST client classes
# ---------------------------------------------------------------------------


class TestMSGraphClientViaUsernamePassword:
    def test_init_mode(self):
        client = MSGraphClientViaUsernamePassword("u", "p", "cid", "tid")
        assert client.mode == GraphMode.APP

    def test_custom_mode(self):
        client = MSGraphClientViaUsernamePassword("u", "p", "cid", "tid", GraphMode.DELEGATED)
        assert client.mode == GraphMode.DELEGATED

    def test_get_mode(self):
        client = MSGraphClientViaUsernamePassword("u", "p", "cid", "tid")
        assert client.get_mode() == GraphMode.APP


class TestMSGraphClientWithCertificatePath:
    def test_init(self):
        client = MSGraphClientWithCertificatePath("/cert.pem", "tid", "cid")
        assert client.mode == GraphMode.APP

    def test_get_mode(self):
        client = MSGraphClientWithCertificatePath("/cert.pem", "tid", "cid")
        assert client.get_mode() == GraphMode.APP


class TestMSGraphClientWithClientIdSecret:
    @patch("app.sources.client.microsoft.microsoft.HttpxRequestAdapter")
    @patch("app.sources.client.microsoft.microsoft.AzureIdentityAuthenticationProvider")
    @patch("app.sources.client.microsoft.microsoft.ClientSecretCredential")
    @patch("app.sources.client.microsoft.microsoft.GraphServiceClient")
    def test_app_mode(self, mock_gsc, mock_cred, mock_auth, mock_adapter):
        client = MSGraphClientWithClientIdSecret("cid", "csec", "tid")
        assert client.mode == GraphMode.APP
        assert client.get_mode() == GraphMode.APP
        assert client.get_ms_graph_service_client() is mock_gsc.return_value

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="Invalid mode"):
            MSGraphClientWithClientIdSecret("cid", "csec", "tid", mode=GraphMode.DELEGATED)

    @patch("app.sources.client.microsoft.microsoft.HttpxRequestAdapter")
    @patch("app.sources.client.microsoft.microsoft.AzureIdentityAuthenticationProvider")
    @patch("app.sources.client.microsoft.microsoft.ClientSecretCredential")
    @patch("app.sources.client.microsoft.microsoft.GraphServiceClient")
    @pytest.mark.asyncio
    async def test_close_with_credential(self, mock_gsc, mock_cred, mock_auth, mock_adapter):
        client = MSGraphClientWithClientIdSecret("cid", "csec", "tid")
        mock_credential = AsyncMock()
        client.credential = mock_credential
        await client.close()
        mock_credential.close.assert_called_once()
        assert client.credential is None

    @patch("app.sources.client.microsoft.microsoft.HttpxRequestAdapter")
    @patch("app.sources.client.microsoft.microsoft.AzureIdentityAuthenticationProvider")
    @patch("app.sources.client.microsoft.microsoft.ClientSecretCredential")
    @patch("app.sources.client.microsoft.microsoft.GraphServiceClient")
    @pytest.mark.asyncio
    async def test_close_without_credential(self, mock_gsc, mock_cred, mock_auth, mock_adapter):
        client = MSGraphClientWithClientIdSecret("cid", "csec", "tid")
        client.credential = None
        await client.close()  # Should not raise


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------


class TestMSGraphUsernamePasswordConfig:
    def test_create_client(self):
        cfg = MSGraphUsernamePasswordConfig("u", "p", "cid", "tid")
        client = cfg.create_client()
        assert isinstance(client, MSGraphClientViaUsernamePassword)

    def test_to_dict(self):
        cfg = MSGraphUsernamePasswordConfig("u", "p", "cid", "tid")
        d = cfg.to_dict()
        assert d["username"] == "u"
        assert d["tenant_id"] == "tid"


class TestMSGraphClientWithClientIdSecretConfig:
    @patch("app.sources.client.microsoft.microsoft.HttpxRequestAdapter")
    @patch("app.sources.client.microsoft.microsoft.AzureIdentityAuthenticationProvider")
    @patch("app.sources.client.microsoft.microsoft.ClientSecretCredential")
    @patch("app.sources.client.microsoft.microsoft.GraphServiceClient")
    def test_create_client(self, *_):
        cfg = MSGraphClientWithClientIdSecretConfig("cid", "csec", "tid")
        client = cfg.create_client()
        assert isinstance(client, MSGraphClientWithClientIdSecret)

    def test_to_dict(self):
        cfg = MSGraphClientWithClientIdSecretConfig("cid", "csec", "tid")
        d = cfg.to_dict()
        assert d["client_id"] == "cid"


class TestMSGraphClientWithCertificatePathConfig:
    def test_create_client(self):
        cfg = MSGraphClientWithCertificatePathConfig("/cert.pem", "tid", "cid")
        client = cfg.create_client()
        assert isinstance(client, MSGraphClientWithCertificatePath)

    def test_to_dict(self):
        cfg = MSGraphClientWithCertificatePathConfig("/cert.pem", "tid", "cid")
        d = cfg.to_dict()
        assert d["certificate_path"] == "/cert.pem"


# ---------------------------------------------------------------------------
# MSGraphClient init / get_client
# ---------------------------------------------------------------------------


class TestMSGraphClientInit:
    def test_init(self):
        mock_client = MagicMock()
        mc = MSGraphClient(mock_client)
        assert mc.get_client() is mock_client
        assert mc.mode == GraphMode.APP

    def test_init_delegated(self):
        mock_client = MagicMock()
        mc = MSGraphClient(mock_client, GraphMode.DELEGATED)
        assert mc.mode == GraphMode.DELEGATED


# ---------------------------------------------------------------------------
# build_with_config
# ---------------------------------------------------------------------------


class TestBuildWithConfig:
    def test_username_password_config(self):
        cfg = MSGraphUsernamePasswordConfig("u", "p", "cid", "tid")
        mc = MSGraphClient.build_with_config(cfg)
        assert isinstance(mc, MSGraphClient)

    @patch("app.sources.client.microsoft.microsoft.HttpxRequestAdapter")
    @patch("app.sources.client.microsoft.microsoft.AzureIdentityAuthenticationProvider")
    @patch("app.sources.client.microsoft.microsoft.ClientSecretCredential")
    @patch("app.sources.client.microsoft.microsoft.GraphServiceClient")
    def test_client_id_secret_config(self, *_):
        cfg = MSGraphClientWithClientIdSecretConfig("cid", "csec", "tid")
        mc = MSGraphClient.build_with_config(cfg)
        assert isinstance(mc, MSGraphClient)

    def test_certificate_config(self):
        cfg = MSGraphClientWithCertificatePathConfig("/cert.pem", "tid", "cid")
        mc = MSGraphClient.build_with_config(cfg)
        assert isinstance(mc, MSGraphClient)


# ---------------------------------------------------------------------------
# _get_connector_config
# ---------------------------------------------------------------------------


class TestGetConnectorConfig:
    @pytest.mark.asyncio
    async def test_returns_config(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value={"auth": {}})
        result = await MSGraphClient._get_connector_config(
            "outlook", logger, mock_config_service, "inst-1"
        )
        assert result == {"auth": {}}

    @pytest.mark.asyncio
    async def test_empty_config_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Failed to get Microsoft Graph"):
            await MSGraphClient._get_connector_config(
                "outlook", logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_exception_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(side_effect=RuntimeError("boom"))
        with pytest.raises(ValueError, match="Failed to get Microsoft Graph"):
            await MSGraphClient._get_connector_config(
                "outlook", logger, mock_config_service, "inst-1"
            )


# ---------------------------------------------------------------------------
# build_from_services
# ---------------------------------------------------------------------------


class TestBuildFromServices:
    @pytest.mark.asyncio
    async def test_oauth_admin_consent(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "authType": "OAUTH_ADMIN_CONSENT",
                    "tenantId": "tid",
                    "clientId": "cid",
                    "clientSecret": "csec",
                },
            }
        )
        with patch("app.sources.client.microsoft.microsoft.HttpxRequestAdapter"), \
             patch("app.sources.client.microsoft.microsoft.AzureIdentityAuthenticationProvider"), \
             patch("app.sources.client.microsoft.microsoft.ClientSecretCredential"), \
             patch("app.sources.client.microsoft.microsoft.GraphServiceClient"):
            mc = await MSGraphClient.build_from_services(
                "outlook", logger, mock_config_service, connector_instance_id="inst-1"
            )
            assert isinstance(mc, MSGraphClient)

    @pytest.mark.asyncio
    async def test_no_config_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError):
            await MSGraphClient.build_from_services(
                "outlook", logger, mock_config_service, connector_instance_id="inst-1"
            )

    @pytest.mark.asyncio
    async def test_missing_tenant_id_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={"auth": {"authType": "OAUTH_ADMIN_CONSENT", "clientId": "cid"}}
        )
        with pytest.raises(ValueError, match="Tenant ID and Client ID"):
            await MSGraphClient.build_from_services(
                "outlook", logger, mock_config_service, connector_instance_id="inst-1"
            )

    @pytest.mark.asyncio
    async def test_missing_client_secret_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "authType": "OAUTH_ADMIN_CONSENT",
                    "tenantId": "tid",
                    "clientId": "cid",
                },
            }
        )
        with pytest.raises(ValueError, match="Client secret required"):
            await MSGraphClient.build_from_services(
                "outlook", logger, mock_config_service, connector_instance_id="inst-1"
            )

    @pytest.mark.asyncio
    async def test_username_password(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "authType": "USERNAME_PASSWORD",
                    "tenantId": "tid",
                    "clientId": "cid",
                    "username": "u",
                    "password": "p",
                },
            }
        )
        mc = await MSGraphClient.build_from_services(
            "outlook", logger, mock_config_service, connector_instance_id="inst-1"
        )
        assert isinstance(mc, MSGraphClient)

    @pytest.mark.asyncio
    async def test_username_password_missing_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "authType": "USERNAME_PASSWORD",
                    "tenantId": "tid",
                    "clientId": "cid",
                },
            }
        )
        with pytest.raises(ValueError, match="Username and password"):
            await MSGraphClient.build_from_services(
                "outlook", logger, mock_config_service, connector_instance_id="inst-1"
            )

    @pytest.mark.asyncio
    async def test_invalid_auth_type_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "authType": "UNSUPPORTED",
                    "tenantId": "tid",
                    "clientId": "cid",
                },
            }
        )
        with pytest.raises(ValueError, match="Invalid auth type"):
            await MSGraphClient.build_from_services(
                "outlook", logger, mock_config_service, connector_instance_id="inst-1"
            )


# ---------------------------------------------------------------------------
# build_from_toolset
# ---------------------------------------------------------------------------


class TestBuildFromToolset:
    @pytest.mark.asyncio
    async def test_empty_config_raises(self, logger, mock_config_service):
        with pytest.raises(ValueError, match="Toolset configuration is required"):
            await MSGraphClient.build_from_toolset({}, "outlook", logger, mock_config_service)

    @pytest.mark.asyncio
    async def test_not_authenticated_raises(self, logger, mock_config_service):
        with pytest.raises(ValueError, match="not authenticated"):
            await MSGraphClient.build_from_toolset(
                {"isAuthenticated": False, "credentials": {"access_token": "at"}, "auth": {}},
                "outlook",
                logger,
                mock_config_service,
            )

    @pytest.mark.asyncio
    async def test_no_credentials_raises(self, logger, mock_config_service):
        with pytest.raises(ValueError, match="no credentials"):
            await MSGraphClient.build_from_toolset(
                {"isAuthenticated": True, "credentials": {}, "auth": {}},
                "outlook",
                logger,
                mock_config_service,
            )

    @pytest.mark.asyncio
    async def test_no_access_token_raises(self, logger, mock_config_service):
        with pytest.raises(ValueError, match="Access token not found"):
            await MSGraphClient.build_from_toolset(
                {"isAuthenticated": True, "credentials": {"refresh_token": "rt"}, "auth": {}},
                "outlook",
                logger,
                mock_config_service,
            )

    @pytest.mark.asyncio
    async def test_placeholder_token_raises(self, logger, mock_config_service):
        with pytest.raises(ValueError, match="Invalid access token"):
            await MSGraphClient.build_from_toolset(
                {"isAuthenticated": True, "credentials": {"access_token": "me-token-to-replace"}, "auth": {}},
                "outlook",
                logger,
                mock_config_service,
            )

    @pytest.mark.asyncio
    async def test_no_config_service_raises(self, logger):
        with pytest.raises(ValueError, match="Failed to retrieve OAuth"):
            await MSGraphClient.build_from_toolset(
                {"isAuthenticated": True, "credentials": {"access_token": "eyJ.eyJ.sig"}, "auth": {}},
                "outlook",
                logger,
                config_service=None,
            )

    @pytest.mark.asyncio
    async def test_success(self, logger, mock_config_service):
        # msal is imported inside the function body via `import msal`, so we must
        # patch it in sys.modules before the call.
        mock_msal = MagicMock()
        mock_msal_app = MagicMock()
        mock_msal.ConfidentialClientApplication.return_value = mock_msal_app
        mock_msal_app.acquire_token_by_refresh_token.return_value = {
            "access_token": "refreshed",
            "expires_in": 3600,
        }

        with patch("app.sources.client.microsoft.microsoft.HttpxRequestAdapter"), \
             patch("app.sources.client.microsoft.microsoft.GraphServiceClient") as mock_gsc, \
             patch(
                 "app.api.routes.toolsets.get_oauth_credentials_for_toolset",
                 new_callable=AsyncMock,
                 return_value={"clientId": "cid", "clientSecret": "csec", "tenantId": "tid"},
             ), \
             patch.dict("sys.modules", {"msal": mock_msal}):

            mock_gsc.return_value = MagicMock(path_parameters={})

            mc = await MSGraphClient.build_from_toolset(
                {
                    "isAuthenticated": True,
                    "credentials": {
                        "access_token": "eyJ.eyJ.sig",
                        "refresh_token": "rt",
                        "scope": "Mail.ReadWrite offline_access",
                    },
                    "auth": {},
                },
                "outlook",
                logger,
                mock_config_service,
            )
            assert isinstance(mc, MSGraphClient)
            assert mc.mode == GraphMode.DELEGATED


# ---------------------------------------------------------------------------
# build_from_toolset - additional token refresh / error paths
# ---------------------------------------------------------------------------


class TestBuildFromToolsetTokenRefresh:
    @pytest.mark.asyncio
    async def test_refresh_returns_placeholder_token(self, logger, mock_config_service):
        """When MSAL refresh returns a placeholder token, fall back to stored."""
        mock_msal = MagicMock()
        mock_msal_app = MagicMock()
        mock_msal.ConfidentialClientApplication.return_value = mock_msal_app
        mock_msal_app.acquire_token_by_refresh_token.return_value = {
            "access_token": "me-token-to-replace",
        }

        with patch("app.sources.client.microsoft.microsoft.HttpxRequestAdapter"), \
             patch("app.sources.client.microsoft.microsoft.GraphServiceClient") as mock_gsc, \
             patch(
                 "app.api.routes.toolsets.get_oauth_credentials_for_toolset",
                 new_callable=AsyncMock,
                 return_value={"clientId": "cid", "clientSecret": "csec", "tenantId": "tid"},
             ), \
             patch.dict("sys.modules", {"msal": mock_msal}):

            mock_gsc.return_value = MagicMock(path_parameters={})

            mc = await MSGraphClient.build_from_toolset(
                {
                    "isAuthenticated": True,
                    "credentials": {
                        "access_token": "eyJ.eyJ.sig",
                        "refresh_token": "rt",
                        "scope": "Mail.ReadWrite",
                    },
                    "auth": {},
                },
                "outlook",
                logger,
                mock_config_service,
            )
            assert isinstance(mc, MSGraphClient)

    @pytest.mark.asyncio
    async def test_refresh_fails_with_error(self, logger, mock_config_service):
        """When MSAL refresh returns an error dict, fall back to stored token."""
        mock_msal = MagicMock()
        mock_msal_app = MagicMock()
        mock_msal.ConfidentialClientApplication.return_value = mock_msal_app
        mock_msal_app.acquire_token_by_refresh_token.return_value = {
            "error": "invalid_grant",
            "error_description": "Token expired",
        }

        with patch("app.sources.client.microsoft.microsoft.HttpxRequestAdapter"), \
             patch("app.sources.client.microsoft.microsoft.GraphServiceClient") as mock_gsc, \
             patch(
                 "app.api.routes.toolsets.get_oauth_credentials_for_toolset",
                 new_callable=AsyncMock,
                 return_value={"clientId": "cid", "clientSecret": "csec", "tenantId": "tid"},
             ), \
             patch.dict("sys.modules", {"msal": mock_msal}):

            mock_gsc.return_value = MagicMock(path_parameters={})

            mc = await MSGraphClient.build_from_toolset(
                {
                    "isAuthenticated": True,
                    "credentials": {
                        "access_token": "eyJ.eyJ.sig",
                        "refresh_token": "rt",
                        "scope": "Mail.ReadWrite",
                    },
                    "auth": {},
                },
                "outlook",
                logger,
                mock_config_service,
            )
            assert isinstance(mc, MSGraphClient)

    @pytest.mark.asyncio
    async def test_refresh_raises_exception(self, logger, mock_config_service):
        """When MSAL refresh call itself raises, fall back to stored token."""
        mock_msal = MagicMock()
        mock_msal_app = MagicMock()
        mock_msal.ConfidentialClientApplication.return_value = mock_msal_app
        mock_msal_app.acquire_token_by_refresh_token.side_effect = Exception("network error")

        with patch("app.sources.client.microsoft.microsoft.HttpxRequestAdapter"), \
             patch("app.sources.client.microsoft.microsoft.GraphServiceClient") as mock_gsc, \
             patch(
                 "app.api.routes.toolsets.get_oauth_credentials_for_toolset",
                 new_callable=AsyncMock,
                 return_value={"clientId": "cid", "clientSecret": "csec", "tenantId": "tid"},
             ), \
             patch.dict("sys.modules", {"msal": mock_msal}):

            mock_gsc.return_value = MagicMock(path_parameters={})

            mc = await MSGraphClient.build_from_toolset(
                {
                    "isAuthenticated": True,
                    "credentials": {
                        "access_token": "eyJ.eyJ.sig",
                        "refresh_token": "rt",
                        "scope": "Mail.ReadWrite",
                    },
                    "auth": {},
                },
                "outlook",
                logger,
                mock_config_service,
            )
            assert isinstance(mc, MSGraphClient)

    @pytest.mark.asyncio
    async def test_no_refresh_token_still_works(self, logger, mock_config_service):
        """When no refresh_token is present, skip refresh and use stored token."""
        mock_msal = MagicMock()
        mock_msal.ConfidentialClientApplication.return_value = MagicMock()

        with patch("app.sources.client.microsoft.microsoft.HttpxRequestAdapter"), \
             patch("app.sources.client.microsoft.microsoft.GraphServiceClient") as mock_gsc, \
             patch(
                 "app.api.routes.toolsets.get_oauth_credentials_for_toolset",
                 new_callable=AsyncMock,
                 return_value={"clientId": "cid", "clientSecret": "csec", "tenantId": "tid"},
             ), \
             patch.dict("sys.modules", {"msal": mock_msal}):

            mock_gsc.return_value = MagicMock(path_parameters={})

            mc = await MSGraphClient.build_from_toolset(
                {
                    "isAuthenticated": True,
                    "credentials": {
                        "access_token": "eyJ.eyJ.sig",
                        "scope": "Mail.ReadWrite",
                    },
                    "auth": {},
                },
                "outlook",
                logger,
                mock_config_service,
            )
            assert isinstance(mc, MSGraphClient)

    @pytest.mark.asyncio
    async def test_scope_as_list(self, logger, mock_config_service):
        """Scope stored as a list should be handled correctly."""
        mock_msal = MagicMock()
        mock_msal_app = MagicMock()
        mock_msal.ConfidentialClientApplication.return_value = mock_msal_app
        mock_msal_app.acquire_token_by_refresh_token.return_value = {
            "access_token": "refreshed.jwt.token",
            "expires_in": 3600,
        }

        with patch("app.sources.client.microsoft.microsoft.HttpxRequestAdapter"), \
             patch("app.sources.client.microsoft.microsoft.GraphServiceClient") as mock_gsc, \
             patch(
                 "app.api.routes.toolsets.get_oauth_credentials_for_toolset",
                 new_callable=AsyncMock,
                 return_value={"clientId": "cid", "clientSecret": "csec", "tenantId": "tid"},
             ), \
             patch.dict("sys.modules", {"msal": mock_msal}):

            mock_gsc.return_value = MagicMock(path_parameters={})

            mc = await MSGraphClient.build_from_toolset(
                {
                    "isAuthenticated": True,
                    "credentials": {
                        "access_token": "eyJ.eyJ.sig",
                        "refresh_token": "rt",
                        "scope": ["Mail.ReadWrite", "offline_access"],
                    },
                    "auth": {},
                },
                "outlook",
                logger,
                mock_config_service,
            )
            assert isinstance(mc, MSGraphClient)

    @pytest.mark.asyncio
    async def test_non_jwt_token_warning(self, logger, mock_config_service):
        """Non-JWT token (no dots) should produce warning but still work."""
        mock_msal = MagicMock()
        mock_msal.ConfidentialClientApplication.return_value = MagicMock()

        with patch("app.sources.client.microsoft.microsoft.HttpxRequestAdapter"), \
             patch("app.sources.client.microsoft.microsoft.GraphServiceClient") as mock_gsc, \
             patch(
                 "app.api.routes.toolsets.get_oauth_credentials_for_toolset",
                 new_callable=AsyncMock,
                 return_value={"clientId": "cid", "clientSecret": "csec", "tenantId": "tid"},
             ), \
             patch.dict("sys.modules", {"msal": mock_msal}):

            mock_gsc.return_value = MagicMock(path_parameters={})

            mc = await MSGraphClient.build_from_toolset(
                {
                    "isAuthenticated": True,
                    "credentials": {
                        "access_token": "not-a-jwt-token",
                        "scope": "Mail.ReadWrite",
                    },
                    "auth": {},
                },
                "outlook",
                logger,
                mock_config_service,
            )
            assert isinstance(mc, MSGraphClient)

    @pytest.mark.asyncio
    async def test_expires_at_in_credentials(self, logger, mock_config_service):
        """When credentials include expires_at, it should be used for initial expiry."""
        mock_msal = MagicMock()
        mock_msal.ConfidentialClientApplication.return_value = MagicMock()

        with patch("app.sources.client.microsoft.microsoft.HttpxRequestAdapter"), \
             patch("app.sources.client.microsoft.microsoft.GraphServiceClient") as mock_gsc, \
             patch(
                 "app.api.routes.toolsets.get_oauth_credentials_for_toolset",
                 new_callable=AsyncMock,
                 return_value={"clientId": "cid", "clientSecret": "csec", "tenantId": "tid"},
             ), \
             patch.dict("sys.modules", {"msal": mock_msal}):

            mock_gsc.return_value = MagicMock(path_parameters={})

            mc = await MSGraphClient.build_from_toolset(
                {
                    "isAuthenticated": True,
                    "credentials": {
                        "access_token": "eyJ.eyJ.sig",
                        "expires_at": "1700000000",
                        "scope": "Mail.ReadWrite",
                    },
                    "auth": {},
                },
                "outlook",
                logger,
                mock_config_service,
            )
            assert isinstance(mc, MSGraphClient)

    @pytest.mark.asyncio
    async def test_expires_at_milliseconds(self, logger, mock_config_service):
        """When expires_at is in milliseconds (>1e12), it should be converted."""
        mock_msal = MagicMock()
        mock_msal.ConfidentialClientApplication.return_value = MagicMock()

        with patch("app.sources.client.microsoft.microsoft.HttpxRequestAdapter"), \
             patch("app.sources.client.microsoft.microsoft.GraphServiceClient") as mock_gsc, \
             patch(
                 "app.api.routes.toolsets.get_oauth_credentials_for_toolset",
                 new_callable=AsyncMock,
                 return_value={"clientId": "cid", "clientSecret": "csec", "tenantId": "tid"},
             ), \
             patch.dict("sys.modules", {"msal": mock_msal}):

            mock_gsc.return_value = MagicMock(path_parameters={})

            mc = await MSGraphClient.build_from_toolset(
                {
                    "isAuthenticated": True,
                    "credentials": {
                        "access_token": "eyJ.eyJ.sig",
                        "expires_at": "1700000000000",  # ms
                        "scope": "Mail.ReadWrite",
                    },
                    "auth": {},
                },
                "outlook",
                logger,
                mock_config_service,
            )
            assert isinstance(mc, MSGraphClient)

    @pytest.mark.asyncio
    async def test_oauth_missing_client_secret_raises(self, logger, mock_config_service):
        """When OAuth config is missing clientSecret, raise ValueError."""
        with patch(
            "app.api.routes.toolsets.get_oauth_credentials_for_toolset",
            new_callable=AsyncMock,
            return_value={"clientId": "cid", "clientSecret": None},
        ):
            with pytest.raises(ValueError, match="Failed to retrieve OAuth"):
                await MSGraphClient.build_from_toolset(
                    {
                        "isAuthenticated": True,
                        "credentials": {"access_token": "eyJ.eyJ.sig"},
                        "auth": {},
                    },
                    "outlook",
                    logger,
                    mock_config_service,
                )


# ---------------------------------------------------------------------------
# MSGraphClient.close (additional)
# ---------------------------------------------------------------------------


class TestMSGraphClientWithClientIdSecretClose:
    @patch("app.sources.client.microsoft.microsoft.HttpxRequestAdapter")
    @patch("app.sources.client.microsoft.microsoft.AzureIdentityAuthenticationProvider")
    @patch("app.sources.client.microsoft.microsoft.ClientSecretCredential")
    @patch("app.sources.client.microsoft.microsoft.GraphServiceClient")
    @pytest.mark.asyncio
    async def test_close_credential_without_close_method(self, mock_gsc, mock_cred, mock_auth, mock_adapter):
        """When credential doesn't have close(), close() should not raise."""
        from app.sources.client.microsoft.microsoft import MSGraphClientWithClientIdSecret
        client = MSGraphClientWithClientIdSecret("cid", "csec", "tid")
        # Credential without close method
        mock_credential = MagicMock(spec=[])
        client.credential = mock_credential
        await client.close()  # Should not raise


# ---------------------------------------------------------------------------
# build_from_services - scopes parameter
# ---------------------------------------------------------------------------


class TestBuildFromServicesAdditional:
    @pytest.mark.asyncio
    async def test_oauth_admin_consent_with_scopes(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "authType": "OAUTH_ADMIN_CONSENT",
                    "tenantId": "tid",
                    "clientId": "cid",
                    "clientSecret": "csec",
                    "scopes": ["https://graph.microsoft.com/Mail.Read"],
                },
            }
        )
        with patch("app.sources.client.microsoft.microsoft.HttpxRequestAdapter"), \
             patch("app.sources.client.microsoft.microsoft.AzureIdentityAuthenticationProvider"), \
             patch("app.sources.client.microsoft.microsoft.ClientSecretCredential"), \
             patch("app.sources.client.microsoft.microsoft.GraphServiceClient"):
            mc = await MSGraphClient.build_from_services(
                "outlook", logger, mock_config_service, connector_instance_id="inst-1"
            )
            assert isinstance(mc, MSGraphClient)
