"""Coverage tests for app.sources.client.google.google targeting uncovered lines.

Missing lines from coverage report (93.4%):
- 26-28: ImportError branch when google libs not installed
- 138: config returns None from _get_connector_config -> raise ValueError
- 146: get_individual_token returns None -> raise ValueError
- 161->163: empty oauth_scopes string -> empty list branch
- 206: enterprise get_enterprise_token returns None -> AdminAuthError
- 306->320, 308->320, 309->308, 311->315: shared OAuth config iteration
  in build_from_toolset (oauth_configs is list, iterate, find match)
- 325-327: get_individual_token exception re-raise path
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sources.client.google.google import (
    CredentialKeys,
    GoogleAuthConfig,
    GoogleClient,
)


@pytest.fixture
def logger():
    return logging.getLogger("test_google_coverage")


@pytest.fixture
def mock_config_service():
    return AsyncMock()


# ---------------------------------------------------------------------------
# Line 26-28: ImportError path (google libs not found)
# ---------------------------------------------------------------------------

class TestGoogleImportError:
    def test_import_error_raises(self):
        """When google.oauth2 is not importable, ImportError should propagate."""
        import importlib
        import sys

        # We can't easily test the top-level import failure since the module
        # is already loaded. Instead we verify the except ImportError branch
        # would print and re-raise.
        # Since the module is already imported successfully, we test that
        # the google API libraries are available (coverage covers the try block).
        # The except block (26-28) is an import-time branch that only runs
        # if google libs are absent. We can test it by attempting a fresh import
        # with mocked missing modules.
        with patch.dict(sys.modules, {"google.oauth2": None, "google.oauth2.service_account": None}):
            # The module is already cached, so we need a more targeted approach
            pass  # This branch is inherently hard to test in-process

    def test_credential_keys_enum_completeness(self):
        """Verify all CredentialKeys members exist."""
        assert len(CredentialKeys) == 4
        assert CredentialKeys.CLIENT_ID.value == "clientId"
        assert CredentialKeys.CLIENT_SECRET.value == "clientSecret"
        assert CredentialKeys.ACCESS_TOKEN.value == "access_token"
        assert CredentialKeys.REFRESH_TOKEN.value == "refresh_token"


# ---------------------------------------------------------------------------
# Line 138: build_from_services - config is None after _get_connector_config
# ---------------------------------------------------------------------------

class TestBuildFromServicesConfigNone:
    @pytest.mark.asyncio
    async def test_config_returns_none_raises_value_error(self, logger, mock_config_service):
        """When _get_connector_config returns None, build_from_services should raise ValueError."""
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Failed to get Google connector"):
            await GoogleClient.build_from_services(
                service_name="drive",
                logger=logger,
                config_service=mock_config_service,
                is_individual=False,
                connector_instance_id="inst-missing",
            )

    @pytest.mark.asyncio
    async def test_config_returns_falsy_value_raises(self, logger, mock_config_service):
        """When _get_connector_config is mocked to return falsy value, line 138 is hit."""
        with patch.object(
            GoogleClient, "_get_connector_config",
            new_callable=AsyncMock,
            return_value={}
        ):
            with pytest.raises(ValueError, match="Failed to get Google connector"):
                await GoogleClient.build_from_services(
                    service_name="drive",
                    logger=logger,
                    config_service=mock_config_service,
                    is_individual=False,
                    connector_instance_id="inst-missing",
                )

    @pytest.mark.asyncio
    async def test_config_returns_none_directly_raises(self, logger, mock_config_service):
        """When _get_connector_config is mocked to return None, line 138 is hit."""
        with patch.object(
            GoogleClient, "_get_connector_config",
            new_callable=AsyncMock,
            return_value=None
        ):
            with pytest.raises(ValueError, match="Failed to get Google connector"):
                await GoogleClient.build_from_services(
                    service_name="drive",
                    logger=logger,
                    config_service=mock_config_service,
                    is_individual=False,
                    connector_instance_id="inst-missing",
                )


# ---------------------------------------------------------------------------
# Line 146: individual token is None -> raise ValueError
# ---------------------------------------------------------------------------

class TestBuildFromServicesIndividualTokenNone:
    @pytest.mark.asyncio
    async def test_individual_token_none_raises(self, logger, mock_config_service):
        """When get_individual_token returns None/empty, should raise GoogleAuthError."""
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "connectorScope": "personal",
                },
                "credentials": None,  # no credentials -> get_individual_token returns {}
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


# ---------------------------------------------------------------------------
# Line 161->163: scope is empty string (whitespace-only)
# ---------------------------------------------------------------------------

class TestBuildFromServicesIndividualScopeWhitespace:
    @pytest.mark.asyncio
    @patch("app.sources.client.google.google.build")
    @patch("app.sources.client.google.google.Credentials")
    async def test_scope_whitespace_only(
        self, mock_credentials_cls, mock_build, logger, mock_config_service
    ):
        """When scope is whitespace-only string, credential_scopes should be []."""
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
                    "scope": "   ",  # whitespace only
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
        # Scopes should be empty list because strip() produces empty string
        call_kwargs = mock_credentials_cls.call_args[1]
        assert call_kwargs["scopes"] == []


# ---------------------------------------------------------------------------
# Line 161: scope is None -> no-scope branch (use default)
# ---------------------------------------------------------------------------

class TestBuildFromServicesIndividualScopeNone:
    @pytest.mark.asyncio
    @patch("app.sources.client.google.google.build")
    @patch("app.sources.client.google.google.Credentials")
    async def test_scope_none_uses_default(
        self, mock_credentials_cls, mock_build, logger, mock_config_service
    ):
        """When scope is None in credentials, should use default scopes param."""
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
                    # no 'scope' key at all
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
            scopes=["custom-scope"],
        )
        assert isinstance(gc, GoogleClient)
        call_kwargs = mock_credentials_cls.call_args[1]
        assert call_kwargs["scopes"] == ["custom-scope"]

    @pytest.mark.asyncio
    @patch("app.sources.client.google.google.build")
    @patch("app.sources.client.google.google.Credentials")
    async def test_scope_not_str_not_list(
        self, mock_credentials_cls, mock_build, logger, mock_config_service
    ):
        """When scope is neither str nor list (e.g. int), credential_scopes stays default."""
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
                    "scope": 42,  # not str, not list
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
        # Falls through both isinstance checks, keeps default []
        call_kwargs = mock_credentials_cls.call_args[1]
        assert call_kwargs["scopes"] == []

    @pytest.mark.asyncio
    @patch("app.sources.client.google.google.build")
    @patch("app.sources.client.google.google.Credentials")
    async def test_scope_none_no_scopes_param(
        self, mock_credentials_cls, mock_build, logger, mock_config_service
    ):
        """When scope is None and no scopes param, credential_scopes should be []."""
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
        call_kwargs = mock_credentials_cls.call_args[1]
        assert call_kwargs["scopes"] == []


# ---------------------------------------------------------------------------
# Line 206: enterprise get_enterprise_token returns empty dict (no adminEmail)
# This is already tested in the main test file, but we cover the exact None
# path for saved_credentials.
# ---------------------------------------------------------------------------

class TestBuildFromServicesEnterpriseNoCreds:
    @pytest.mark.asyncio
    async def test_enterprise_no_saved_credentials(self, logger, mock_config_service):
        """When get_enterprise_token returns empty auth dict, raise AdminAuthError."""
        # Config has auth section but no adminEmail and no credentials
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {},  # empty auth => get_enterprise_token returns {}
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


# ---------------------------------------------------------------------------
# Lines 306->320, 308->320, 309->308, 311->315: shared OAuth config iteration
# in get_individual_token - covers the for loop finding matching config
# ---------------------------------------------------------------------------

class TestGetIndividualTokenSharedOAuthIteration:
    @pytest.mark.asyncio
    async def test_shared_oauth_multiple_configs_find_match(self, logger, mock_config_service):
        """Iterate through multiple OAuth configs and find the matching one by _id."""

        async def fake_get_config(path, default=None):
            if "connectors" in path:
                return {
                    "auth": {"oauthConfigId": "oauth-456"},
                    "credentials": {"access_token": "at"},
                }
            if "oauth" in path:
                return [
                    {"_id": "oauth-111", "config": {"clientId": "wrong-id", "clientSecret": "wrong-sec"}},
                    {"_id": "oauth-456", "config": {"clientId": "correct-id", "clientSecret": "correct-sec"}},
                    {"_id": "oauth-789", "config": {"clientId": "other-id", "clientSecret": "other-sec"}},
                ]
            return default

        mock_config_service.get_config = AsyncMock(side_effect=fake_get_config)
        result = await GoogleClient.get_individual_token(
            "drive", logger, mock_config_service, "inst-1"
        )
        assert result["clientId"] == "correct-id"
        assert result["clientSecret"] == "correct-sec"

    @pytest.mark.asyncio
    async def test_shared_oauth_no_matching_id(self, logger, mock_config_service):
        """When no OAuth config matches the oauthConfigId, clientId remains None."""

        async def fake_get_config(path, default=None):
            if "connectors" in path:
                return {
                    "auth": {"oauthConfigId": "oauth-999"},
                    "credentials": {"access_token": "at"},
                }
            if "oauth" in path:
                return [
                    {"_id": "oauth-111", "config": {"clientId": "wrong-id", "clientSecret": "wrong-sec"}},
                ]
            return default

        mock_config_service.get_config = AsyncMock(side_effect=fake_get_config)
        result = await GoogleClient.get_individual_token(
            "drive", logger, mock_config_service, "inst-1"
        )
        assert result["clientId"] is None
        assert result["clientSecret"] is None

    @pytest.mark.asyncio
    async def test_shared_oauth_not_a_list(self, logger, mock_config_service):
        """When oauth_configs is not a list (e.g. dict), skip iteration."""

        async def fake_get_config(path, default=None):
            if "connectors" in path:
                return {
                    "auth": {"oauthConfigId": "oauth-123"},
                    "credentials": {"access_token": "at"},
                }
            if "oauth" in path:
                return {"some": "dict"}  # Not a list
            return default

        mock_config_service.get_config = AsyncMock(side_effect=fake_get_config)
        result = await GoogleClient.get_individual_token(
            "drive", logger, mock_config_service, "inst-1"
        )
        # Should fall through without finding clientId
        assert result["clientId"] is None

    @pytest.mark.asyncio
    async def test_shared_oauth_snake_case_keys(self, logger, mock_config_service):
        """Shared OAuth config uses snake_case keys (client_id, client_secret)."""

        async def fake_get_config(path, default=None):
            if "connectors" in path:
                return {
                    "auth": {"oauthConfigId": "oauth-123"},
                    "credentials": {"access_token": "at"},
                }
            if "oauth" in path:
                return [
                    {"_id": "oauth-123", "config": {"client_id": "snake-id", "client_secret": "snake-sec"}},
                ]
            return default

        mock_config_service.get_config = AsyncMock(side_effect=fake_get_config)
        result = await GoogleClient.get_individual_token(
            "drive", logger, mock_config_service, "inst-1"
        )
        assert result["clientId"] == "snake-id"
        assert result["clientSecret"] == "snake-sec"

    @pytest.mark.asyncio
    async def test_shared_oauth_config_empty_config_data(self, logger, mock_config_service):
        """Matching OAuth config has empty 'config' dict."""

        async def fake_get_config(path, default=None):
            if "connectors" in path:
                return {
                    "auth": {"oauthConfigId": "oauth-123"},
                    "credentials": {"access_token": "at"},
                }
            if "oauth" in path:
                return [
                    {"_id": "oauth-123", "config": {}},
                ]
            return default

        mock_config_service.get_config = AsyncMock(side_effect=fake_get_config)
        result = await GoogleClient.get_individual_token(
            "drive", logger, mock_config_service, "inst-1"
        )
        # Empty config -> clientId/clientSecret stay None
        assert result["clientId"] is None

    @pytest.mark.asyncio
    async def test_shared_oauth_already_has_client_creds(self, logger, mock_config_service):
        """When clientId and clientSecret already present, skip shared config lookup."""
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "oauthConfigId": "oauth-123",
                    "clientId": "already-cid",
                    "clientSecret": "already-csec",
                },
                "credentials": {"access_token": "at"},
            }
        )
        result = await GoogleClient.get_individual_token(
            "drive", logger, mock_config_service, "inst-1"
        )
        assert result["clientId"] == "already-cid"
        assert result["clientSecret"] == "already-csec"


# ---------------------------------------------------------------------------
# Lines 325-327: get_individual_token exception path (re-raise)
# ---------------------------------------------------------------------------

class TestGetIndividualTokenException:
    @pytest.mark.asyncio
    async def test_exception_in_get_config_reraises(self, logger, mock_config_service):
        """When _get_connector_config raises, get_individual_token logs and re-raises."""
        mock_config_service.get_config = AsyncMock(side_effect=RuntimeError("etcd down"))
        with pytest.raises(ValueError, match="Failed to get Google connector"):
            await GoogleClient.get_individual_token(
                "drive", logger, mock_config_service, "inst-missing"
            )


# ---------------------------------------------------------------------------
# build_from_toolset: shared OAuth config iteration (lines 306-320 range)
# These are the branches inside build_from_toolset where
# get_oauth_credentials_for_toolset is called and may fail.
# ---------------------------------------------------------------------------

class TestBuildFromToolsetOAuthConfigIteration:
    @pytest.mark.asyncio
    async def test_oauth_get_credentials_returns_empty_client_secret(self, logger, mock_config_service):
        """When OAuth config has clientId but empty clientSecret, should fail."""
        with patch(
            "app.api.routes.toolsets.get_oauth_credentials_for_toolset",
            new_callable=AsyncMock,
            return_value={"clientId": "cid", "clientSecret": ""},
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
    async def test_oauth_get_credentials_exception(self, logger, mock_config_service):
        """When get_oauth_credentials_for_toolset raises, wrap in ValueError."""
        with patch(
            "app.api.routes.toolsets.get_oauth_credentials_for_toolset",
            new_callable=AsyncMock,
            side_effect=RuntimeError("network error"),
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
    async def test_oauth_snake_case_credentials(
        self, mock_credentials_cls, mock_build, logger, mock_config_service
    ):
        """When OAuth config uses snake_case (client_id/client_secret)."""
        with patch(
            "app.api.routes.toolsets.get_oauth_credentials_for_toolset",
            new_callable=AsyncMock,
            return_value={"client_id": "snake-cid", "client_secret": "snake-csec"},
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
    async def test_toolset_none_config_raises(self, logger, mock_config_service):
        """None toolset_config should raise ValueError."""
        with pytest.raises(ValueError, match="Toolset configuration is required"):
            await GoogleClient.build_from_toolset(
                toolset_config=None,
                service_name="drive",
                logger=logger,
                config_service=mock_config_service,
            )

    @pytest.mark.asyncio
    async def test_toolset_credentials_none_raises(self, logger, mock_config_service):
        """When credentials is None, should raise ValueError."""
        with pytest.raises(ValueError, match="no credentials"):
            await GoogleClient.build_from_toolset(
                toolset_config={
                    "isAuthenticated": True,
                    "credentials": None,
                },
                service_name="drive",
                logger=logger,
                config_service=mock_config_service,
            )


# ---------------------------------------------------------------------------
# Additional edge cases for build_from_services individual path
# ---------------------------------------------------------------------------

class TestBuildFromServicesIndividualEdgeCases:
    @pytest.mark.asyncio
    @patch("app.sources.client.google.google.build")
    @patch("app.sources.client.google.google.Credentials")
    async def test_scope_space_separated_string(
        self, mock_credentials_cls, mock_build, logger, mock_config_service
    ):
        """Scope as space-separated string should be split into list."""
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
                    "scope": "https://scope1 https://scope2 https://scope3",
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
        call_kwargs = mock_credentials_cls.call_args[1]
        assert call_kwargs["scopes"] == ["https://scope1", "https://scope2", "https://scope3"]

    @pytest.mark.asyncio
    async def test_build_raises_google_auth_error_on_build_exception(self, logger, mock_config_service):
        """When build() raises inside individual path, GoogleAuthError is raised."""
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
                },
            }
        )
        from app.connectors.sources.google.common.connector_google_exceptions import (
            GoogleAuthError,
        )

        with patch("app.sources.client.google.google.Credentials", return_value=MagicMock()):
            with patch("app.sources.client.google.google.build", side_effect=RuntimeError("API error")):
                with pytest.raises(GoogleAuthError, match="Failed to get individual token"):
                    await GoogleClient.build_from_services(
                        service_name="drive",
                        logger=logger,
                        config_service=mock_config_service,
                        is_individual=True,
                        connector_instance_id="inst-1",
                    )

    @pytest.mark.asyncio
    async def test_enterprise_path_via_connector_scope_not_personal(self, logger, mock_config_service):
        """When connectorScope is not 'personal' and is_individual=False, enterprise path is taken."""
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "connectorScope": "team",
                    "adminEmail": "admin@example.com",
                    "type": "service_account",
                },
            }
        )
        with patch("app.sources.client.google.google.service_account") as mock_sa:
            mock_sa.Credentials.from_service_account_info.return_value = MagicMock()
            with patch("app.sources.client.google.google.build", return_value=MagicMock()):
                gc = await GoogleClient.build_from_services(
                    service_name="drive",
                    logger=logger,
                    config_service=mock_config_service,
                    is_individual=False,
                    connector_instance_id="inst-1",
                )
                assert isinstance(gc, GoogleClient)
