"""Unit tests for OneDrive connector notification triggers."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from msgraph.generated.models.o_data_errors.main_error import MainError
from msgraph.generated.models.o_data_errors.o_data_error import ODataError

from app.connectors.sources.microsoft.onedrive.connector import OneDriveConnector
from app.services.notification.types import NotificationSeverity, NotificationType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_connector() -> OneDriveConnector:
    logger = logging.getLogger("test.onedrive.notifications")
    data_entities_processor = MagicMock()
    data_entities_processor.org_id = "org-123"
    data_entities_processor.on_new_app_users = AsyncMock()
    data_entities_processor.get_all_active_users = AsyncMock(return_value=[])

    connector = OneDriveConnector(
        logger,
        data_entities_processor,
        MagicMock(),
        MagicMock(),
        "conn-onedrive-notify",
        "team",
        "creator-user-id",
    )
    connector.notify = AsyncMock()
    return connector


def _make_403_odata_error() -> ODataError:
    err = ODataError()
    err.error = MainError()
    err.error.code = "Authorization_RequestDenied"
    err.response_status_code = 403
    return err


def _make_transient_odata_error() -> ODataError:
    err = ODataError()
    err.error = MainError()
    err.error.code = "ServiceUnavailable"
    err.error.message = "Microsoft Graph is temporarily unavailable"
    err.response_status_code = 503
    return err


def _expected_payload(connector: OneDriveConnector) -> dict:
    return {
        "connector_id": connector.connector_id,
        "connector_name": connector.connector_name.value,
        "connector_scope": connector.scope,
    }


def _assert_notify(
    connector: OneDriveConnector,
    *,
    notification_type: NotificationType,
    severity: NotificationSeverity,
    title: str,
    message: str | None = None,
    message_contains: str | None = None,
) -> None:
    connector.notify.assert_awaited_once()
    kwargs = connector.notify.await_args.kwargs
    assert kwargs["type"] is notification_type
    assert kwargs["severity"] is severity
    assert kwargs["title"] == title
    if message is not None:
        assert kwargs["message"] == message
    if message_contains is not None:
        assert message_contains in kwargs["message"]
    assert kwargs["payload"] == _expected_payload(connector)
    assert kwargs["recipient_user_ids"] == [connector.created_by]


# ---------------------------------------------------------------------------
# init()
# ---------------------------------------------------------------------------


class TestInitNotifications:

    @pytest.mark.asyncio
    async def test_auth_failure_notifies_and_raises(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {
                "tenantId": "tenant",
                "clientId": "client",
                "clientSecret": "secret",
                "hasAdminConsent": False,
            }
        })

        with patch(
            "app.connectors.sources.microsoft.onedrive.connector.ClientSecretCredential"
        ) as mock_cred_cls:
            mock_cred = AsyncMock()
            mock_cred.get_token = AsyncMock(side_effect=Exception("token fail"))
            mock_cred_cls.return_value = mock_cred

            with pytest.raises(ValueError, match="Failed to initialize OneDrive credential"):
                await connector.init()

        _assert_notify(
            connector,
            notification_type=NotificationType.CONNECTOR_AUTH_ERROR,
            severity=NotificationSeverity.ERROR,
            title="OneDrive authentication failed",
            message=(
                "Unable to authenticate using current credentials. "
                "Please verify the connector authentication credentials."
            ),
        )


# ---------------------------------------------------------------------------
# _sync_users()
# ---------------------------------------------------------------------------


class TestSyncUsersNotifications:

    @pytest.mark.asyncio
    async def test_403_notifies_and_raises(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_all_users = AsyncMock(
            side_effect=_make_403_odata_error()
        )

        with pytest.raises(ODataError):
            await connector._sync_users()

        _assert_notify(
            connector,
            notification_type=NotificationType.CONNECTOR_USER_SYNC_ERROR,
            severity=NotificationSeverity.ERROR,
            title="OneDrive users sync failed",
            message=(
                "Please check your authentication credentials are correct "
                "and has User.Read.All API permission."
            ),
        )

    @pytest.mark.asyncio
    async def test_transient_error_does_not_notify(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_all_users = AsyncMock(
            side_effect=_make_transient_odata_error()
        )

        with pytest.raises(ODataError):
            await connector._sync_users()

        connector.notify.assert_not_called()


# ---------------------------------------------------------------------------
# _sync_user_groups()
# ---------------------------------------------------------------------------


class TestSyncUserGroupsNotifications:

    @pytest.mark.asyncio
    async def test_403_notifies_and_raises(self):
        connector = _make_connector()
        connector.user_group_sync_point = MagicMock()
        connector.user_group_sync_point.read_sync_point = AsyncMock(
            side_effect=_make_403_odata_error()
        )

        with pytest.raises(ODataError):
            await connector._sync_user_groups()

        _assert_notify(
            connector,
            notification_type=NotificationType.CONNECTOR_AUTH_ERROR,
            severity=NotificationSeverity.ERROR,
            title="User group sync failed",
            message=(
                "Please check your authentication credentials are correct "
                "and has Group.Read.All API permission."
            ),
        )

    @pytest.mark.asyncio
    async def test_transient_error_does_not_notify(self):
        connector = _make_connector()
        connector.user_group_sync_point = MagicMock()
        connector.user_group_sync_point.read_sync_point = AsyncMock(
            side_effect=_make_transient_odata_error()
        )

        with pytest.raises(ODataError):
            await connector._sync_user_groups()

        connector.notify.assert_not_called()


# ---------------------------------------------------------------------------
# _process_users_in_batches()
# ---------------------------------------------------------------------------


class TestProcessUsersInBatchesNotifications:

    @pytest.mark.asyncio
    async def test_user_drive_403_raises_without_notification(self):
        connector = _make_connector()
        active_user = MagicMock()
        active_user.email = "active@test.com"
        connector.data_entities_processor.get_all_active_users = AsyncMock(
            return_value=[active_user]
        )
        user = MagicMock()
        user.email = "active@test.com"
        user.source_user_id = "su-1"
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_user_drive = AsyncMock(
            side_effect=_make_403_odata_error()
        )

        with pytest.raises(ODataError):
            await connector._process_users_in_batches([user])

        connector.msgraph_client.get_user_drive.assert_awaited_once_with("su-1")
        connector.notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_user_drive_transient_error_does_not_notify(self):
        connector = _make_connector()
        active_user = MagicMock()
        active_user.email = "active@test.com"
        connector.data_entities_processor.get_all_active_users = AsyncMock(
            return_value=[active_user]
        )
        user = MagicMock()
        user.email = "active@test.com"
        user.source_user_id = "su-1"
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_user_drive = AsyncMock(
            side_effect=_make_transient_odata_error()
        )

        with pytest.raises(ODataError):
            await connector._process_users_in_batches([user])

        connector.notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_onedrive_users_warns(self):
        connector = _make_connector()

        active_user = MagicMock()
        active_user.email = "active@test.com"
        connector.data_entities_processor.get_all_active_users = AsyncMock(
            return_value=[active_user]
        )

        user = MagicMock()
        user.email = "active@test.com"
        user.source_user_id = "su-1"
        connector._user_has_onedrive = AsyncMock(return_value=False)
        connector._run_sync_with_yield = AsyncMock()

        await connector._process_users_in_batches([user])

        _assert_notify(
            connector,
            notification_type=NotificationType.CONNECTOR_RECORD_SYNC_ERROR,
            severity=NotificationSeverity.WARNING,
            title="No OneDrive users synced",
            message=(
                "Ensure that your OneDrive users are invited to Pipeshub, and verify "
                "that your application has Files.Read.All API permission with admin consent."
            ),
        )

    @pytest.mark.asyncio
    async def test_users_with_onedrive_does_not_warn(self):
        connector = _make_connector()

        active_user = MagicMock()
        active_user.email = "active@test.com"
        connector.data_entities_processor.get_all_active_users = AsyncMock(
            return_value=[active_user]
        )

        user = MagicMock()
        user.email = "active@test.com"
        user.source_user_id = "su-1"
        connector._user_has_onedrive = AsyncMock(return_value=True)
        connector._run_sync_with_yield = AsyncMock()

        await connector._process_users_in_batches([user])

        connector.notify.assert_not_called()
        assert connector.onedrive_users_synced == 1


# ---------------------------------------------------------------------------
# run_sync()
# ---------------------------------------------------------------------------


class TestRunSyncNotifications:

    @pytest.mark.asyncio
    async def test_success_notifies_when_users_synced(self):
        connector = _make_connector()

        async def _set_synced_users(_users):
            connector.onedrive_users_synced = 2

        connector._reinitialize_credential_if_needed = AsyncMock()
        connector._sync_users = AsyncMock(return_value=[MagicMock()])
        connector._sync_user_groups = AsyncMock()
        connector._process_users_in_batches = AsyncMock(side_effect=_set_synced_users)

        with patch(
            "app.connectors.sources.microsoft.onedrive.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(MagicMock(), MagicMock()),
        ):
            await connector.run_sync()

        _assert_notify(
            connector,
            notification_type=NotificationType.CONNECTOR_SUCCESS,
            severity=NotificationSeverity.INFO,
            title="OneDrive sync complete",
            message="OneDrive sync completed successfully",
        )

    @pytest.mark.asyncio
    async def test_success_does_not_notify_when_no_users_synced(self):
        connector = _make_connector()

        async def _set_no_synced_users(_users):
            connector.onedrive_users_synced = 0

        connector._reinitialize_credential_if_needed = AsyncMock()
        connector._sync_users = AsyncMock(return_value=[])
        connector._sync_user_groups = AsyncMock()
        connector._process_users_in_batches = AsyncMock(side_effect=_set_no_synced_users)

        with patch(
            "app.connectors.sources.microsoft.onedrive.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(MagicMock(), MagicMock()),
        ):
            await connector.run_sync()

        connector.notify.assert_not_called()


# ---------------------------------------------------------------------------
# _reinitialize_credential_if_needed()
# ---------------------------------------------------------------------------


class TestReinitializeCredentialNotifications:

    @pytest.mark.asyncio
    async def test_reinit_token_failure_notifies_and_raises(self):
        connector = _make_connector()
        connector.credential = AsyncMock()
        connector.credential.get_token = AsyncMock(side_effect=Exception("transport closed"))
        connector.credential.close = AsyncMock()
        connector.config = {
            "credentials": {
                "auth": {
                    "tenantId": "tenant",
                    "clientId": "client",
                    "clientSecret": "secret",
                }
            }
        }

        with patch(
            "app.connectors.sources.microsoft.onedrive.connector.ClientSecretCredential"
        ) as mock_cred_cls, patch(
            "app.connectors.sources.microsoft.onedrive.connector.GraphServiceClient"
        ), patch(
            "app.connectors.sources.microsoft.onedrive.connector.MSGraphClient"
        ):
            new_cred = AsyncMock()
            new_cred.get_token = AsyncMock(side_effect=Exception("invalid secret"))
            mock_cred_cls.return_value = new_cred

            with pytest.raises(Exception, match="invalid secret"):
                await connector._reinitialize_credential_if_needed()

        _assert_notify(
            connector,
            notification_type=NotificationType.CONNECTOR_AUTH_ERROR,
            severity=NotificationSeverity.ERROR,
            title="OneDrive authentication failed",
            message=(
                "Unable to re-authenticate the OneDrive connector. "
                "The client secret may have expired. Please update the connector credentials."
            ),
        )

    @pytest.mark.asyncio
    async def test_valid_credential_does_not_notify(self):
        connector = _make_connector()
        connector.credential = AsyncMock()
        connector.credential.get_token = AsyncMock(return_value=MagicMock())

        await connector._reinitialize_credential_if_needed()

        connector.notify.assert_not_called()


# ---------------------------------------------------------------------------
# test_connection_and_access()
# ---------------------------------------------------------------------------


class TestConnectionAndAccessNotifications:

    @pytest.mark.asyncio
    async def test_all_scopes_pass_does_not_notify(self):
        connector = _make_connector()
        connector._probe_users_scope = AsyncMock()
        connector._probe_groups_scope = AsyncMock()

        result = await connector.test_connection_and_access()

        assert result is True
        connector.notify.assert_not_called()
        connector._probe_users_scope.assert_awaited_once()
        connector._probe_groups_scope.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_missing_user_read_all_notifies_with_scope_in_message(self):
        connector = _make_connector()
        connector._probe_users_scope = AsyncMock(side_effect=_make_403_odata_error())
        connector._probe_groups_scope = AsyncMock()

        with pytest.raises(ConnectionError, match=r"User\.Read\.All"):
            await connector.test_connection_and_access()

        _assert_notify(
            connector,
            notification_type=NotificationType.CONNECTOR_AUTH_ERROR,
            severity=NotificationSeverity.ERROR,
            title="OneDrive: missing API permissions",
            message_contains="User.Read.All",
        )

    @pytest.mark.asyncio
    async def test_missing_group_read_all_notifies_with_scope_in_message(self):
        connector = _make_connector()
        connector._probe_users_scope = AsyncMock()
        connector._probe_groups_scope = AsyncMock(side_effect=_make_403_odata_error())

        with pytest.raises(ConnectionError, match=r"Group\.Read\.All"):
            await connector.test_connection_and_access()

        _assert_notify(
            connector,
            notification_type=NotificationType.CONNECTOR_AUTH_ERROR,
            severity=NotificationSeverity.ERROR,
            title="OneDrive: missing API permissions",
            message_contains="Group.Read.All",
        )

    @pytest.mark.asyncio
    async def test_all_missing_scopes_single_notification_lists_all(self):
        connector = _make_connector()
        connector._probe_users_scope = AsyncMock(side_effect=_make_403_odata_error())
        connector._probe_groups_scope = AsyncMock(side_effect=_make_403_odata_error())

        with pytest.raises(
            ConnectionError,
            match=r"User\.Read\.All.*Group\.Read\.All|Group\.Read\.All.*User\.Read\.All",
        ):
            await connector.test_connection_and_access()

        connector.notify.assert_awaited_once()
        message = connector.notify.await_args.kwargs["message"]
        assert "User.Read.All" in message
        assert "Group.Read.All" in message
        assert connector.notify.await_args.kwargs["type"] is NotificationType.CONNECTOR_AUTH_ERROR

    @pytest.mark.asyncio
    async def test_transient_error_propagates_sanitized_message_without_notification(self):
        connector = _make_connector()
        connector._probe_users_scope = AsyncMock(side_effect=_make_transient_odata_error())
        connector._probe_groups_scope = AsyncMock()

        with pytest.raises(
            ConnectionError,
            match=(
                r"ServiceUnavailable: Microsoft Graph is temporarily unavailable "
                r"\(HTTP 503\)"
            ),
        ):
            await connector.test_connection_and_access()

        connector.notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_generic_exception_propagates_safe_message_without_notification(self):
        connector = _make_connector()
        connector._probe_users_scope = AsyncMock(side_effect=RuntimeError("network down"))
        connector._probe_groups_scope = AsyncMock()

        with pytest.raises(
            ConnectionError,
            match=(
                "Unable to check Microsoft Graph permission "
                "User.Read.All: network down"
            ),
        ):
            await connector.test_connection_and_access()

        connector.notify.assert_not_called()
