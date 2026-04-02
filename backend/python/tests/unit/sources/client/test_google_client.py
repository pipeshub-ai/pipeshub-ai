"""Unit tests for Google client module."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sources.client.google.google import (
    CredentialKeys,
    GoogleAuthConfig,
    GoogleClient,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def logger():
    return logging.getLogger("test_google_client")


@pytest.fixture
def mock_config_service():
    svc = AsyncMock()
    return svc


@pytest.fixture
def sample_individual_config():
    return {
        "auth": {
            "connectorScope": "personal",
            "clientId": "test-client-id",
            "clientSecret": "test-client-secret",
        },
        "credentials": {
            "access_token": "test-access-token",
            "refresh_token": "test-refresh-token",
            "scope": "https://www.googleapis.com/auth/drive",
        },
    }


@pytest.fixture
def sample_enterprise_config():
    return {
        "auth": {
            "connectorScope": "team",
            "adminEmail": "admin@example.com",
            "type": "service_account",
            "project_id": "test-project",
            "private_key_id": "key-id",
            "private_key": "-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----\n",
            "client_email": "svc@test.iam.gserviceaccount.com",
            "client_id": "123",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        },
    }


# ---------------------------------------------------------------------------
# CredentialKeys Enum
# ---------------------------------------------------------------------------


class TestCredentialKeys:
    def test_client_id_value(self):
        assert CredentialKeys.CLIENT_ID.value == "clientId"

    def test_client_secret_value(self):
        assert CredentialKeys.CLIENT_SECRET.value == "clientSecret"

    def test_access_token_value(self):
        assert CredentialKeys.ACCESS_TOKEN.value == "access_token"

    def test_refresh_token_value(self):
        assert CredentialKeys.REFRESH_TOKEN.value == "refresh_token"


# ---------------------------------------------------------------------------
# GoogleAuthConfig
# ---------------------------------------------------------------------------


class TestGoogleAuthConfig:
    def test_default_values(self):
        config = GoogleAuthConfig()
        assert config.credentials_path is None
        assert config.redirect_uri is None
        assert config.scopes is None
        assert config.oauth_port == 8080
        assert config.token_file_path == "token.json"
        assert config.credentials_file_path == "credentials.json"
        assert config.admin_scopes is None
        assert config.is_individual is False

    def test_custom_values(self):
        config = GoogleAuthConfig(
            credentials_path="/tmp/creds",
            redirect_uri="http://localhost:8080/callback",
            scopes=["scope1"],
            oauth_port=9090,
            token_file_path="custom_token.json",
            credentials_file_path="custom_creds.json",
            admin_scopes=["admin_scope"],
            is_individual=True,
        )
        assert config.credentials_path == "/tmp/creds"
        assert config.redirect_uri == "http://localhost:8080/callback"
        assert config.scopes == ["scope1"]
        assert config.oauth_port == 9090
        assert config.is_individual is True


# ---------------------------------------------------------------------------
# GoogleClient.__init__ and get_client
# ---------------------------------------------------------------------------


class TestGoogleClientInit:
    def test_init_stores_client(self):
        mock_client = MagicMock()
        gc = GoogleClient(mock_client)
        assert gc.client is mock_client

    def test_get_client_returns_client(self):
        mock_client = MagicMock()
        gc = GoogleClient(mock_client)
        assert gc.get_client() is mock_client


# ---------------------------------------------------------------------------
# build_with_client
# ---------------------------------------------------------------------------


class TestBuildWithClient:
    def test_returns_google_client(self):
        mock_client = MagicMock()
        gc = GoogleClient.build_with_client(mock_client)
        assert isinstance(gc, GoogleClient)
        assert gc.get_client() is mock_client


# ---------------------------------------------------------------------------
# build_with_config
# ---------------------------------------------------------------------------


class TestBuildWithConfig:
    def test_returns_google_client_with_none_client(self):
        config = GoogleAuthConfig()
        gc = GoogleClient.build_with_config(config)
        assert isinstance(gc, GoogleClient)
        assert gc.get_client() is None


# ---------------------------------------------------------------------------
# _get_optimized_scopes
# ---------------------------------------------------------------------------


class TestGetOptimizedScopes:
    @patch("app.sources.client.google.google.GOOGLE_SERVICE_SCOPES", {"drive": ["scope_a"]})
    @patch("app.sources.client.google.google.SERVICES_WITH_PARSER_SCOPES", [])
    def test_base_scopes_only(self):
        result = GoogleClient._get_optimized_scopes("drive")
        assert result == ["scope_a"]

    @patch("app.sources.client.google.google.GOOGLE_SERVICE_SCOPES", {"drive": ["scope_a"]})
    @patch("app.sources.client.google.google.GOOGLE_PARSER_SCOPES", ["parser_scope"])
    @patch("app.sources.client.google.google.SERVICES_WITH_PARSER_SCOPES", ["drive"])
    def test_with_parser_scopes(self):
        result = GoogleClient._get_optimized_scopes("drive")
        assert "scope_a" in result
        assert "parser_scope" in result

    @patch("app.sources.client.google.google.GOOGLE_SERVICE_SCOPES", {"drive": ["scope_a"]})
    @patch("app.sources.client.google.google.SERVICES_WITH_PARSER_SCOPES", [])
    def test_with_additional_scopes(self):
        result = GoogleClient._get_optimized_scopes("drive", ["extra_scope"])
        assert "scope_a" in result
        assert "extra_scope" in result

    @patch("app.sources.client.google.google.GOOGLE_SERVICE_SCOPES", {})
    @patch("app.sources.client.google.google.SERVICES_WITH_PARSER_SCOPES", [])
    def test_unknown_service(self):
        result = GoogleClient._get_optimized_scopes("unknown_service")
        assert result == []

    @patch("app.sources.client.google.google.GOOGLE_SERVICE_SCOPES", {})
    @patch("app.sources.client.google.google.SERVICES_WITH_PARSER_SCOPES", [])
    def test_unknown_service_with_additional(self):
        result = GoogleClient._get_optimized_scopes("unknown", ["my_scope"])
        assert result == ["my_scope"]


# ---------------------------------------------------------------------------
# _get_connector_config
# ---------------------------------------------------------------------------


class TestGetConnectorConfig:
    @pytest.mark.asyncio
    async def test_returns_config(self, logger, mock_config_service):
        expected = {"auth": {}, "credentials": {}}
        mock_config_service.get_config = AsyncMock(return_value=expected)
        result = await GoogleClient._get_connector_config(
            "drive", logger, mock_config_service, "inst-1"
        )
        assert result == expected

    @pytest.mark.asyncio
    async def test_raises_on_empty_config(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Failed to get Google connector"):
            await GoogleClient._get_connector_config(
                "drive", logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_raises_on_exception(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(side_effect=RuntimeError("boom"))
        with pytest.raises(ValueError, match="Failed to get Google connector"):
            await GoogleClient._get_connector_config(
                "drive", logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_normalizes_service_name(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value={"auth": {}})
        await GoogleClient._get_connector_config(
            "Google Drive", logger, mock_config_service, "inst-1"
        )
        mock_config_service.get_config.assert_called_once_with(
            "/services/connectors/inst-1/config"
        )


# ---------------------------------------------------------------------------
# get_account_scopes
# ---------------------------------------------------------------------------


class TestGetAccountScopes:
    @pytest.mark.asyncio
    async def test_returns_scopes(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={"config": {"auth": {"scopes": ["s1", "s2"]}}}
        )
        scopes = await GoogleClient.get_account_scopes(
            "drive", logger, mock_config_service, "inst-1"
        )
        assert scopes == ["s1", "s2"]

    @pytest.mark.asyncio
    async def test_returns_empty_on_missing(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value={"config": {}})
        scopes = await GoogleClient.get_account_scopes(
            "drive", logger, mock_config_service, "inst-1"
        )
        assert scopes == []


# ---------------------------------------------------------------------------
# get_individual_token
# ---------------------------------------------------------------------------


class TestGetIndividualToken:
    @pytest.mark.asyncio
    async def test_returns_merged_credentials(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "clientId": "cid",
                    "clientSecret": "csec",
                    "connectorScope": "personal",
                },
                "credentials": {
                    "access_token": "at",
                    "refresh_token": "rt",
                },
            }
        )
        result = await GoogleClient.get_individual_token(
            "drive", logger, mock_config_service, "inst-1"
        )
        assert result["clientId"] == "cid"
        assert result["clientSecret"] == "csec"
        assert result["access_token"] == "at"
        assert result["connectorScope"] == "personal"

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_credentials(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={"auth": {}, "credentials": None}
        )
        result = await GoogleClient.get_individual_token(
            "drive", logger, mock_config_service, "inst-1"
        )
        assert result == {}

    @pytest.mark.asyncio
    async def test_shared_oauth_config_lookup(self, logger, mock_config_service):
        """When oauthConfigId is present and clientId/clientSecret missing, fetch from shared config."""

        async def fake_get_config(path, default=None):
            if "connectors" in path:
                return {
                    "auth": {"oauthConfigId": "oauth-123"},
                    "credentials": {"access_token": "at"},
                }
            if "oauth" in path:
                return [
                    {
                        "_id": "oauth-123",
                        "config": {"clientId": "shared-id", "clientSecret": "shared-sec"},
                    }
                ]
            return default

        mock_config_service.get_config = AsyncMock(side_effect=fake_get_config)
        result = await GoogleClient.get_individual_token(
            "drive", logger, mock_config_service, "inst-1"
        )
        assert result["clientId"] == "shared-id"
        assert result["clientSecret"] == "shared-sec"

    @pytest.mark.asyncio
    async def test_shared_oauth_fallback_on_error(self, logger, mock_config_service):
        """When shared OAuth fetch fails, fall back gracefully."""
        call_count = 0

        async def fake_get_config(path, default=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    "auth": {"oauthConfigId": "oauth-123"},
                    "credentials": {"access_token": "at"},
                }
            raise RuntimeError("etcd down")

        mock_config_service.get_config = AsyncMock(side_effect=fake_get_config)
        result = await GoogleClient.get_individual_token(
            "drive", logger, mock_config_service, "inst-1"
        )
        assert result["access_token"] == "at"
        assert result["clientId"] is None


# ---------------------------------------------------------------------------
# get_enterprise_token
# ---------------------------------------------------------------------------


class TestGetEnterpriseToken:
    @pytest.mark.asyncio
    async def test_returns_auth_dict(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={"auth": {"adminEmail": "a@b.com", "type": "service_account"}}
        )
        result = await GoogleClient.get_enterprise_token(
            "drive", logger, mock_config_service, "inst-1"
        )
        assert result["adminEmail"] == "a@b.com"


# ---------------------------------------------------------------------------
# build_from_services - individual
# ---------------------------------------------------------------------------


class TestBuildFromServicesIndividual:
    @pytest.mark.asyncio
    @patch("app.sources.client.google.google.build")
    @patch("app.sources.client.google.google.Credentials")
    async def test_individual_success(
        self, mock_credentials_cls, mock_build, logger, mock_config_service
    ):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "connectorScope": "personal",
                    "clientId": "cid",
                    "clientSecret": "csec",
                },
                "credentials": {
                    "access_token": "at",
                    "refresh_token": "rt",
                    "scope": "https://www.googleapis.com/auth/drive",
                },
            }
        )
        mock_cred_instance = MagicMock()
        mock_credentials_cls.return_value = mock_cred_instance
        mock_build.return_value = MagicMock()

        gc = await GoogleClient.build_from_services(
            service_name="drive",
            logger=logger,
            config_service=mock_config_service,
            is_individual=True,
            version="v3",
            connector_instance_id="inst-1",
        )
        assert isinstance(gc, GoogleClient)
        mock_build.assert_called_once_with("drive", "v3", credentials=mock_cred_instance)

    @pytest.mark.asyncio
    async def test_individual_no_config_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Failed to get Google connector"):
            await GoogleClient.build_from_services(
                service_name="drive",
                logger=logger,
                config_service=mock_config_service,
                is_individual=True,
                connector_instance_id="inst-1",
            )

    @pytest.mark.asyncio
    async def test_individual_missing_client_id_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"connectorScope": "personal"},
                "credentials": {
                    "access_token": "at",
                    "refresh_token": "rt",
                },
            }
        )
        from app.connectors.sources.google.common.connector_google_exceptions import (
            GoogleAuthError,
        )

        with pytest.raises(GoogleAuthError):
            await GoogleClient.build_from_services(
                service_name="drive",
                logger=logger,
                config_service=mock_config_service,
                is_individual=True,
                connector_instance_id="inst-1",
            )

    @pytest.mark.asyncio
    async def test_individual_missing_refresh_token_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "connectorScope": "personal",
                    "clientId": "cid",
                    "clientSecret": "csec",
                },
                "credentials": {
                    "access_token": "at",
                    # no refresh_token
                },
            }
        )
        from app.connectors.sources.google.common.connector_google_exceptions import (
            GoogleAuthError,
        )

        with pytest.raises(GoogleAuthError):
            await GoogleClient.build_from_services(
                service_name="drive",
                logger=logger,
                config_service=mock_config_service,
                is_individual=True,
                connector_instance_id="inst-1",
            )

    @pytest.mark.asyncio
    @patch("app.sources.client.google.google.build")
    @patch("app.sources.client.google.google.Credentials")
    async def test_individual_missing_access_token_warns(
        self, mock_credentials_cls, mock_build, logger, mock_config_service
    ):
        """Missing access_token is a warning, not an error."""
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "connectorScope": "personal",
                    "clientId": "cid",
                    "clientSecret": "csec",
                },
                "credentials": {
                    "refresh_token": "rt",
                },
            }
        )
        mock_credentials_cls.return_value = MagicMock()
        mock_build.return_value = MagicMock()

        gc = await GoogleClient.build_from_services(
            service_name="drive",
            logger=logger,
            config_service=mock_config_service,
            is_individual=True,
            connector_instance_id="inst-1",
        )
        assert isinstance(gc, GoogleClient)

    @pytest.mark.asyncio
    @patch("app.sources.client.google.google.build")
    @patch("app.sources.client.google.google.Credentials")
    async def test_individual_scope_as_list(
        self, mock_credentials_cls, mock_build, logger, mock_config_service
    ):
        """OAuth scopes can be stored as a list."""
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "connectorScope": "personal",
                    "clientId": "cid",
                    "clientSecret": "csec",
                },
                "credentials": {
                    "access_token": "at",
                    "refresh_token": "rt",
                    "scope": ["scope1", "scope2"],
                },
            }
        )
        mock_credentials_cls.return_value = MagicMock()
        mock_build.return_value = MagicMock()

        gc = await GoogleClient.build_from_services(
            service_name="drive",
            logger=logger,
            config_service=mock_config_service,
            is_individual=True,
            connector_instance_id="inst-1",
        )
        assert isinstance(gc, GoogleClient)
        # Verify scopes passed to Credentials were the list
        call_kwargs = mock_credentials_cls.call_args[1]
        assert call_kwargs["scopes"] == ["scope1", "scope2"]


# ---------------------------------------------------------------------------
# build_from_services - enterprise
# ---------------------------------------------------------------------------


class TestBuildFromServicesEnterprise:
    @pytest.mark.asyncio
    @patch("app.sources.client.google.google.build")
    @patch("app.sources.client.google.google.service_account")
    async def test_enterprise_success(
        self, mock_sa, mock_build, logger, mock_config_service
    ):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "connectorScope": "team",
                    "adminEmail": "admin@co.com",
                    "type": "service_account",
                },
            }
        )
        mock_cred = MagicMock()
        mock_sa.Credentials.from_service_account_info.return_value = mock_cred
        mock_build.return_value = MagicMock()

        gc = await GoogleClient.build_from_services(
            service_name="drive",
            logger=logger,
            config_service=mock_config_service,
            is_individual=False,
            version="v3",
            connector_instance_id="inst-1",
        )
        assert isinstance(gc, GoogleClient)
        mock_sa.Credentials.from_service_account_info.assert_called_once()

    @pytest.mark.asyncio
    async def test_enterprise_missing_credentials_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={"auth": {"connectorScope": "team"}}
        )
        from app.connectors.sources.google.common.connector_google_exceptions import (
            AdminAuthError,
        )

        with pytest.raises(AdminAuthError):
            await GoogleClient.build_from_services(
                service_name="drive",
                logger=logger,
                config_service=mock_config_service,
                is_individual=False,
                connector_instance_id="inst-1",
            )

    @pytest.mark.asyncio
    async def test_enterprise_missing_admin_email_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"connectorScope": "team", "type": "service_account"},
            }
        )
        from app.connectors.sources.google.common.connector_google_exceptions import (
            AdminAuthError,
        )

        with pytest.raises(AdminAuthError):
            await GoogleClient.build_from_services(
                service_name="drive",
                logger=logger,
                config_service=mock_config_service,
                is_individual=False,
                connector_instance_id="inst-1",
            )

    @pytest.mark.asyncio
    @patch("app.sources.client.google.google.service_account")
    async def test_enterprise_delegation_error(
        self, mock_sa, logger, mock_config_service
    ):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "connectorScope": "team",
                    "adminEmail": "admin@co.com",
                },
            }
        )
        mock_sa.Credentials.from_service_account_info.side_effect = Exception("delegation fail")
        from app.connectors.sources.google.common.connector_google_exceptions import (
            AdminDelegationError,
        )

        with pytest.raises(AdminDelegationError):
            await GoogleClient.build_from_services(
                service_name="drive",
                logger=logger,
                config_service=mock_config_service,
                is_individual=False,
                connector_instance_id="inst-1",
            )

    @pytest.mark.asyncio
    @patch("app.sources.client.google.google.build")
    @patch("app.sources.client.google.google.service_account")
    async def test_enterprise_build_service_error(
        self, mock_sa, mock_build, logger, mock_config_service
    ):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "connectorScope": "team",
                    "adminEmail": "admin@co.com",
                },
            }
        )
        mock_sa.Credentials.from_service_account_info.return_value = MagicMock()
        mock_build.side_effect = Exception("build fail")
        from app.connectors.sources.google.common.connector_google_exceptions import (
            AdminServiceError,
        )

        with pytest.raises(AdminServiceError):
            await GoogleClient.build_from_services(
                service_name="drive",
                logger=logger,
                config_service=mock_config_service,
                is_individual=False,
                connector_instance_id="inst-1",
            )

    @pytest.mark.asyncio
    @patch("app.sources.client.google.google.build")
    @patch("app.sources.client.google.google.service_account")
    async def test_enterprise_with_user_email(
        self, mock_sa, mock_build, logger, mock_config_service
    ):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "connectorScope": "team",
                    "adminEmail": "admin@co.com",
                },
            }
        )
        mock_cred = MagicMock()
        mock_sa.Credentials.from_service_account_info.return_value = mock_cred
        mock_build.return_value = MagicMock()

        await GoogleClient.build_from_services(
            service_name="drive",
            logger=logger,
            config_service=mock_config_service,
            is_individual=False,
            user_email="user@co.com",
            connector_instance_id="inst-1",
        )
        call_kwargs = mock_sa.Credentials.from_service_account_info.call_args[1]
        assert call_kwargs["subject"] == "user@co.com"


# ---------------------------------------------------------------------------
# build_from_toolset
# ---------------------------------------------------------------------------


class TestBuildFromToolset:
    @pytest.mark.asyncio
    async def test_empty_config_raises(self, logger, mock_config_service):
        with pytest.raises(ValueError, match="Toolset configuration is required"):
            await GoogleClient.build_from_toolset(
                toolset_config={},
                service_name="drive",
                logger=logger,
                config_service=mock_config_service,
            )

    @pytest.mark.asyncio
    async def test_not_authenticated_raises(self, logger, mock_config_service):
        with pytest.raises(ValueError, match="not authenticated"):
            await GoogleClient.build_from_toolset(
                toolset_config={"isAuthenticated": False, "credentials": {"access_token": "x"}},
                service_name="drive",
                logger=logger,
                config_service=mock_config_service,
            )

    @pytest.mark.asyncio
    async def test_no_credentials_raises(self, logger, mock_config_service):
        with pytest.raises(ValueError, match="no credentials"):
            await GoogleClient.build_from_toolset(
                toolset_config={"isAuthenticated": True, "credentials": {}},
                service_name="drive",
                logger=logger,
                config_service=mock_config_service,
            )

    @pytest.mark.asyncio
    async def test_no_access_token_raises(self, logger, mock_config_service):
        with pytest.raises(ValueError, match="Access token not found"):
            await GoogleClient.build_from_toolset(
                toolset_config={
                    "isAuthenticated": True,
                    "credentials": {"refresh_token": "rt"},
                    "auth": {},
                },
                service_name="drive",
                logger=logger,
                config_service=mock_config_service,
            )

    @pytest.mark.asyncio
    async def test_no_config_service_raises(self, logger):
        with pytest.raises(ValueError, match="Failed to retrieve OAuth"):
            await GoogleClient.build_from_toolset(
                toolset_config={
                    "isAuthenticated": True,
                    "credentials": {"access_token": "at"},
                    "auth": {},
                },
                service_name="drive",
                logger=logger,
                config_service=None,
            )

    @pytest.mark.asyncio
    @patch("app.sources.client.google.google.build")
    @patch("app.sources.client.google.google.Credentials")
    @patch("app.sources.client.google.google.get_oauth_credentials_for_toolset", create=True)
    async def test_success_with_refresh_token(
        self, mock_get_oauth, mock_credentials_cls, mock_build, logger, mock_config_service
    ):
        with patch(
            "app.api.routes.toolsets.get_oauth_credentials_for_toolset",
            new_callable=AsyncMock,
            return_value={"clientId": "cid", "clientSecret": "csec"},
        ):
            mock_credentials_cls.return_value = MagicMock()
            mock_build.return_value = MagicMock()

            gc = await GoogleClient.build_from_toolset(
                toolset_config={
                    "isAuthenticated": True,
                    "credentials": {"access_token": "at", "refresh_token": "rt"},
                    "auth": {},
                },
                service_name="drive",
                logger=logger,
                config_service=mock_config_service,
            )
            assert isinstance(gc, GoogleClient)

    @pytest.mark.asyncio
    @patch("app.sources.client.google.google.build")
    @patch("app.sources.client.google.google.Credentials")
    async def test_success_without_refresh_token(
        self, mock_credentials_cls, mock_build, logger, mock_config_service
    ):
        with patch(
            "app.api.routes.toolsets.get_oauth_credentials_for_toolset",
            new_callable=AsyncMock,
            return_value={"clientId": "cid", "clientSecret": "csec"},
        ):
            mock_credentials_cls.return_value = MagicMock()
            mock_build.return_value = MagicMock()

            gc = await GoogleClient.build_from_toolset(
                toolset_config={
                    "isAuthenticated": True,
                    "credentials": {"access_token": "at"},
                    "auth": {},
                },
                service_name="drive",
                logger=logger,
                config_service=mock_config_service,
            )
            assert isinstance(gc, GoogleClient)

    @pytest.mark.asyncio
    async def test_oauth_config_missing_client_id(self, logger, mock_config_service):
        with patch(
            "app.api.routes.toolsets.get_oauth_credentials_for_toolset",
            new_callable=AsyncMock,
            return_value={"clientId": None, "clientSecret": "csec"},
        ):
            with pytest.raises(ValueError, match="Failed to retrieve OAuth"):
                await GoogleClient.build_from_toolset(
                    toolset_config={
                        "isAuthenticated": True,
                        "credentials": {"access_token": "at"},
                        "auth": {},
                    },
                    service_name="drive",
                    logger=logger,
                    config_service=mock_config_service,
                )

    @pytest.mark.asyncio
    @patch("app.sources.client.google.google.build")
    @patch("app.sources.client.google.google.Credentials")
    async def test_build_failure_raises(
        self, mock_credentials_cls, mock_build, logger, mock_config_service
    ):
        with patch(
            "app.api.routes.toolsets.get_oauth_credentials_for_toolset",
            new_callable=AsyncMock,
            return_value={"clientId": "cid", "clientSecret": "csec"},
        ):
            mock_credentials_cls.return_value = MagicMock()
            mock_build.side_effect = Exception("build fail")

            with pytest.raises(ValueError, match="Failed to create Google"):
                await GoogleClient.build_from_toolset(
                    toolset_config={
                        "isAuthenticated": True,
                        "credentials": {"access_token": "at", "refresh_token": "rt"},
                        "auth": {},
                    },
                    service_name="drive",
                    logger=logger,
                    config_service=mock_config_service,
                )


# ---------------------------------------------------------------------------
# build_from_services - individual scope edge cases
# ---------------------------------------------------------------------------


class TestBuildFromServicesIndividualScopeEdgeCases:
    @pytest.mark.asyncio
    @patch("app.sources.client.google.google.build")
    @patch("app.sources.client.google.google.Credentials")
    async def test_individual_scope_as_empty_string(
        self, mock_credentials_cls, mock_build, logger, mock_config_service
    ):
        """Empty scope string should result in empty scopes."""
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "connectorScope": "personal",
                    "clientId": "cid",
                    "clientSecret": "csec",
                },
                "credentials": {
                    "access_token": "at",
                    "refresh_token": "rt",
                    "scope": "",
                },
            }
        )
        mock_credentials_cls.return_value = MagicMock()
        mock_build.return_value = MagicMock()

        gc = await GoogleClient.build_from_services(
            service_name="drive",
            logger=logger,
            config_service=mock_config_service,
            is_individual=True,
            connector_instance_id="inst-1",
        )
        assert isinstance(gc, GoogleClient)


# ---------------------------------------------------------------------------
# build_from_toolset - edge cases
# ---------------------------------------------------------------------------


class TestBuildFromToolsetEdgeCases:
    @pytest.mark.asyncio
    @patch("app.sources.client.google.google.build")
    @patch("app.sources.client.google.google.Credentials")
    async def test_with_explicit_scopes(
        self, mock_credentials_cls, mock_build, logger, mock_config_service
    ):
        """When explicit scopes are provided, they should be used."""
        with patch(
            "app.api.routes.toolsets.get_oauth_credentials_for_toolset",
            new_callable=AsyncMock,
            return_value={"clientId": "cid", "clientSecret": "csec"},
        ):
            mock_credentials_cls.return_value = MagicMock()
            mock_build.return_value = MagicMock()

            gc = await GoogleClient.build_from_toolset(
                toolset_config={
                    "isAuthenticated": True,
                    "credentials": {"access_token": "at"},
                    "auth": {},
                },
                service_name="drive",
                logger=logger,
                config_service=mock_config_service,
                scopes=["https://www.googleapis.com/auth/drive.readonly"],
            )
            assert isinstance(gc, GoogleClient)

    @pytest.mark.asyncio
    @patch("app.sources.client.google.google.build")
    @patch("app.sources.client.google.google.Credentials")
    async def test_with_explicit_version(
        self, mock_credentials_cls, mock_build, logger, mock_config_service
    ):
        """Version parameter should be passed to build()."""
        with patch(
            "app.api.routes.toolsets.get_oauth_credentials_for_toolset",
            new_callable=AsyncMock,
            return_value={"clientId": "cid", "clientSecret": "csec"},
        ):
            mock_credentials_cls.return_value = MagicMock()
            mock_build.return_value = MagicMock()

            gc = await GoogleClient.build_from_toolset(
                toolset_config={
                    "isAuthenticated": True,
                    "credentials": {"access_token": "at", "refresh_token": "rt"},
                    "auth": {},
                },
                service_name="gmail",
                logger=logger,
                config_service=mock_config_service,
                version="v1",
            )
            assert isinstance(gc, GoogleClient)
            mock_build.assert_called_once()
            call_args = mock_build.call_args
            assert call_args[0][0] == "gmail"
            assert call_args[0][1] == "v1"
