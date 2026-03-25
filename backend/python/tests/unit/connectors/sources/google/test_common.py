"""Tests for Google connector common utilities: apps, scopes, exceptions, datasource_refresh."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.sources.google.common.apps import (
    GmailIndividualApp,
    GmailTeamApp,
    GoogleCalendarApp,
    GoogleDriveApp,
    GoogleDriveTeamApp,
)
from app.connectors.sources.google.common.connector_google_exceptions import (
    AdminAuthError,
    AdminDelegationError,
    AdminListError,
    AdminOperationError,
    AdminQuotaError,
    AdminServiceError,
    BatchOperationError,
    DriveOperationError,
    DrivePermissionError,
    DriveSyncError,
    GoogleAuthError,
    GoogleConnectorError,
    GoogleDriveError,
    GoogleMailError,
    MailOperationError,
    MailSyncError,
    MailThreadError,
    UserOperationError,
)
from app.connectors.sources.google.common.scopes import (
    GOOGLE_CONNECTOR_ENTERPRISE_SCOPES,
    GOOGLE_CONNECTOR_INDIVIDUAL_SCOPES,
    GOOGLE_PARSER_SCOPES,
    GOOGLE_SERVICE_SCOPES,
)
from app.config.constants.arangodb import AppGroups, Connectors


# ---------------------------------------------------------------------------
# Apps
# ---------------------------------------------------------------------------

class TestGoogleApps:
    """Tests for Google connector App classes."""

    def test_gmail_individual_app_connector_name(self):
        app = GmailIndividualApp("conn-1")
        assert app.get_app_name() == Connectors.GOOGLE_MAIL

    def test_gmail_team_app_connector_name(self):
        app = GmailTeamApp("conn-2")
        assert app.get_app_name() == Connectors.GOOGLE_MAIL_WORKSPACE

    def test_google_drive_app_connector_name(self):
        app = GoogleDriveApp("conn-3")
        assert app.get_app_name() == Connectors.GOOGLE_DRIVE

    def test_google_drive_team_app_connector_name(self):
        app = GoogleDriveTeamApp("conn-4")
        assert app.get_app_name() == Connectors.GOOGLE_DRIVE_WORKSPACE

    def test_google_calendar_app_connector_name(self):
        app = GoogleCalendarApp("conn-5")
        assert app.get_app_name() == Connectors.GOOGLE_CALENDAR

    def test_all_apps_have_google_workspace_group(self):
        apps = [
            GmailIndividualApp("c1"),
            GmailTeamApp("c2"),
            GoogleDriveApp("c3"),
            GoogleDriveTeamApp("c4"),
            GoogleCalendarApp("c5"),
        ]
        for app in apps:
            assert app.get_app_group_name() == AppGroups.GOOGLE_WORKSPACE


# ---------------------------------------------------------------------------
# Scopes
# ---------------------------------------------------------------------------

class TestGoogleScopes:
    """Tests for Google connector scope definitions."""

    def test_individual_scopes_include_gmail_readonly(self):
        assert "https://www.googleapis.com/auth/gmail.readonly" in GOOGLE_CONNECTOR_INDIVIDUAL_SCOPES

    def test_individual_scopes_include_drive_readonly(self):
        assert "https://www.googleapis.com/auth/drive.readonly" in GOOGLE_CONNECTOR_INDIVIDUAL_SCOPES

    def test_individual_scopes_include_calendar_readonly(self):
        assert "https://www.googleapis.com/auth/calendar.readonly" in GOOGLE_CONNECTOR_INDIVIDUAL_SCOPES

    def test_enterprise_scopes_include_admin_directory(self):
        assert "https://www.googleapis.com/auth/admin.directory.user.readonly" in GOOGLE_CONNECTOR_ENTERPRISE_SCOPES

    def test_enterprise_scopes_superset_of_individual(self):
        for scope in GOOGLE_CONNECTOR_INDIVIDUAL_SCOPES:
            assert scope in GOOGLE_CONNECTOR_ENTERPRISE_SCOPES

    def test_parser_scopes_include_drive_readonly(self):
        assert "https://www.googleapis.com/auth/drive.readonly" in GOOGLE_PARSER_SCOPES

    def test_service_scopes_keys(self):
        expected_keys = {"meet", "gmail", "drive", "calendar", "admin", "sheets", "docs", "slides", "forms"}
        assert set(GOOGLE_SERVICE_SCOPES.keys()) == expected_keys

    def test_service_scopes_are_all_lists(self):
        for key, scopes in GOOGLE_SERVICE_SCOPES.items():
            assert isinstance(scopes, list), f"Scopes for '{key}' should be a list"
            for scope in scopes:
                assert scope.startswith("https://"), f"Scope '{scope}' for '{key}' should start with https://"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class TestGoogleExceptions:
    """Tests for Google connector exception hierarchy."""

    def test_base_exception_stores_message_and_details(self):
        exc = GoogleConnectorError("test error", {"key": "value"})
        assert exc.message == "test error"
        assert exc.details == {"key": "value"}
        assert str(exc) == "test error"

    def test_base_exception_defaults_empty_details(self):
        exc = GoogleConnectorError("test error")
        assert exc.details == {}

    def test_google_auth_error_is_connector_error(self):
        exc = GoogleAuthError("auth failed")
        assert isinstance(exc, GoogleConnectorError)

    def test_google_drive_error_hierarchy(self):
        assert issubclass(DriveOperationError, GoogleDriveError)
        assert issubclass(DrivePermissionError, GoogleDriveError)
        assert issubclass(DriveSyncError, GoogleDriveError)
        assert issubclass(GoogleDriveError, GoogleConnectorError)

    def test_google_mail_error_hierarchy(self):
        assert issubclass(MailOperationError, GoogleMailError)
        assert issubclass(MailSyncError, GoogleMailError)
        assert issubclass(MailThreadError, GoogleMailError)
        assert issubclass(GoogleMailError, GoogleConnectorError)

    def test_admin_error_hierarchy(self):
        assert issubclass(AdminAuthError, AdminServiceError)
        assert issubclass(AdminListError, AdminServiceError)
        assert issubclass(AdminDelegationError, AdminServiceError)
        assert issubclass(AdminQuotaError, AdminServiceError)
        assert issubclass(AdminServiceError, GoogleConnectorError)

    def test_batch_operation_error_stores_failed_items(self):
        failed = [{"id": "1", "error": "fail"}]
        exc = BatchOperationError("batch failed", failed)
        assert exc.failed_items == failed
        assert exc.message == "batch failed"

    def test_user_operation_error(self):
        exc = UserOperationError("user error")
        assert isinstance(exc, GoogleConnectorError)

    def test_admin_operation_error(self):
        exc = AdminOperationError("admin error")
        assert isinstance(exc, GoogleConnectorError)


# ---------------------------------------------------------------------------
# Datasource Refresh
# ---------------------------------------------------------------------------

class TestDatasourceRefresh:
    """Tests for refresh_google_datasource_credentials."""

    async def test_raises_auth_error_when_config_not_found(self):
        from app.connectors.sources.google.common.datasource_refresh import (
            refresh_google_datasource_credentials,
        )

        mock_config_service = AsyncMock()
        mock_config_service.get_config = AsyncMock(return_value=None)
        mock_google_client = MagicMock()
        mock_data_source = MagicMock()
        mock_logger = MagicMock()

        with pytest.raises(GoogleAuthError, match="configuration not found"):
            await refresh_google_datasource_credentials(
                google_client=mock_google_client,
                data_source=mock_data_source,
                config_service=mock_config_service,
                connector_id="test-connector",
                logger=mock_logger,
                service_name="Drive"
            )

    async def test_raises_auth_error_when_no_credentials(self):
        from app.connectors.sources.google.common.datasource_refresh import (
            refresh_google_datasource_credentials,
        )

        mock_config_service = AsyncMock()
        mock_config_service.get_config = AsyncMock(return_value={
            "credentials": {"access_token": "", "refresh_token": ""}
        })
        mock_google_client = MagicMock()
        mock_data_source = MagicMock()
        mock_logger = MagicMock()

        with pytest.raises(GoogleAuthError, match="No OAuth credentials available"):
            await refresh_google_datasource_credentials(
                google_client=mock_google_client,
                data_source=mock_data_source,
                config_service=mock_config_service,
                connector_id="test-connector",
                logger=mock_logger,
                service_name="Gmail"
            )

    async def test_updates_credentials_when_refresh_token_changed(self):
        from app.connectors.sources.google.common.datasource_refresh import (
            refresh_google_datasource_credentials,
        )

        mock_config_service = AsyncMock()
        mock_config_service.get_config = AsyncMock(return_value={
            "credentials": {
                "access_token": "new-access-token",
                "refresh_token": "new-refresh-token",
            }
        })

        # Create mock credentials with old tokens
        mock_old_creds = MagicMock()
        mock_old_creds.token = "old-access-token"
        mock_old_creds.refresh_token = "old-refresh-token"
        mock_old_creds.scopes = ["scope1"]
        mock_old_creds.client_id = "client-id"
        mock_old_creds.client_secret = "client-secret"

        mock_http = MagicMock()
        mock_http.credentials = mock_old_creds

        mock_client = MagicMock()
        mock_client._http = mock_http

        mock_google_client = MagicMock()
        mock_google_client.get_client.return_value = mock_client
        mock_data_source = MagicMock()
        mock_logger = MagicMock()

        with patch("app.connectors.sources.google.common.datasource_refresh.Credentials") as MockCreds:
            mock_new_creds = MagicMock()
            MockCreds.return_value = mock_new_creds

            await refresh_google_datasource_credentials(
                google_client=mock_google_client,
                data_source=mock_data_source,
                config_service=mock_config_service,
                connector_id="test-connector",
                logger=mock_logger,
                service_name="Drive"
            )

            # Verify new credentials were created
            MockCreds.assert_called_once()
            call_kwargs = MockCreds.call_args
            assert call_kwargs[1]["token"] == "new-access-token"
            assert call_kwargs[1]["refresh_token"] == "new-refresh-token"

            # Verify credentials were replaced on the client
            assert mock_client._http.credentials == mock_new_creds

    async def test_no_update_when_credentials_unchanged(self):
        from app.connectors.sources.google.common.datasource_refresh import (
            refresh_google_datasource_credentials,
        )

        mock_config_service = AsyncMock()
        mock_config_service.get_config = AsyncMock(return_value={
            "credentials": {
                "access_token": "same-token",
                "refresh_token": "same-refresh",
            }
        })

        mock_old_creds = MagicMock()
        mock_old_creds.token = "same-token"
        mock_old_creds.refresh_token = "same-refresh"

        mock_http = MagicMock()
        mock_http.credentials = mock_old_creds

        mock_client = MagicMock()
        mock_client._http = mock_http

        mock_google_client = MagicMock()
        mock_google_client.get_client.return_value = mock_client
        mock_data_source = MagicMock()
        mock_logger = MagicMock()

        # Should not raise or create new credentials
        await refresh_google_datasource_credentials(
            google_client=mock_google_client,
            data_source=mock_data_source,
            config_service=mock_config_service,
            connector_id="test-connector",
            logger=mock_logger,
            service_name="Drive"
        )

        # credentials object should remain the same
        assert mock_client._http.credentials == mock_old_creds
