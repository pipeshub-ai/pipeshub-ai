"""Tests for app.connectors.core.base.token_service.toolset_token_refresh_service"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.core.base.token_service.oauth_service import (
    DEAUTH_REASON_INVALID_CLIENT,
    InvalidClientError,
    RefreshTokenInvalidError,
)
from app.connectors.core.base.token_service.toolset_token_refresh_service import (
    MAX_REFRESH_TOKEN_INVALID_FAILURES,
    ToolsetTokenRefreshService,
)
from app.services.notification.types import (
    ACTIONS_NOTIFICATION_LINK,
    NotificationRecipientRole,
    NotificationType,
)

CONFIG_PATH = "/services/toolsets/inst-1/user-1"


@pytest.fixture
def mock_config_service() -> MagicMock:
    """Mock ConfigurationService with async get_config/set_config."""
    svc = MagicMock()
    svc.get_config = AsyncMock(return_value={"isAuthenticated": True, "toolsetType": "confluence"})
    svc.set_config = AsyncMock(return_value=True)
    return svc


@pytest.fixture
def service(mock_config_service: MagicMock) -> ToolsetTokenRefreshService:
    return ToolsetTokenRefreshService(mock_config_service)


class TestToolsetRefreshTokenInvalidThreshold:
    """Tests for _handle_refresh_token_invalid() deactivation threshold."""

    @pytest.mark.asyncio
    async def test_deactivates_only_on_threshold_rejection(
        self, service: ToolsetTokenRefreshService, mock_config_service: MagicMock
    ) -> None:
        """First N-1 rejections leave the toolset untouched; the Nth deauthenticates it."""
        error = RefreshTokenInvalidError("refresh_token is invalid")

        for _ in range(MAX_REFRESH_TOKEN_INVALID_FAILURES - 1):
            await service._handle_refresh_token_invalid(CONFIG_PATH, error)

        mock_config_service.set_config.assert_not_awaited()
        assert service._invalid_refresh_failures[CONFIG_PATH] == MAX_REFRESH_TOKEN_INVALID_FAILURES - 1

        await service._handle_refresh_token_invalid(CONFIG_PATH, error)

        mock_config_service.set_config.assert_awaited_once()
        path, config = mock_config_service.set_config.await_args.args
        assert path == CONFIG_PATH
        assert config["isAuthenticated"] is False
        assert config["deauthReason"] == "refresh_token_invalid"
        assert CONFIG_PATH not in service._invalid_refresh_failures

    @pytest.mark.asyncio
    async def test_mark_unauthenticated_tolerates_missing_config(
        self, service: ToolsetTokenRefreshService, mock_config_service: MagicMock
    ) -> None:
        """A deleted toolset config aborts the write without raising."""
        mock_config_service.get_config = AsyncMock(return_value=None)

        await service._mark_toolset_unauthenticated(CONFIG_PATH)

        mock_config_service.set_config.assert_not_awaited()


class TestToolsetDeactivationNotification:
    """Deactivation must reach the owning user — without a notification it is log-only."""

    @pytest.fixture
    def mock_notification_service(self) -> MagicMock:
        svc = MagicMock()
        svc.publish_notification = AsyncMock()
        return svc

    @pytest.mark.asyncio
    async def test_deactivation_notifies_owner_with_actions_link(
        self, mock_config_service: MagicMock, mock_notification_service: MagicMock
    ) -> None:
        service = ToolsetTokenRefreshService(mock_config_service, notification_service=mock_notification_service)
        mock_config_service.get_config = AsyncMock(
            return_value={"isAuthenticated": True, "toolsetType": "confluence", "orgId": "org-1", "userId": "user-1"}
        )

        await service._mark_toolset_unauthenticated(CONFIG_PATH)

        mock_notification_service.publish_notification.assert_awaited_once()
        kwargs = mock_notification_service.publish_notification.await_args.kwargs
        assert kwargs["type"] == NotificationType.TOOLSET_AUTH_ERROR
        assert kwargs["org_id"] == "org-1"
        assert kwargs["recipient_user_ids"] == ["user-1"]
        assert kwargs["redirect_link"] == ACTIONS_NOTIFICATION_LINK
        assert "Confluence" in kwargs["title"]

    @pytest.mark.asyncio
    async def test_missing_org_id_skips_notification_but_still_deauthenticates(
        self, mock_config_service: MagicMock, mock_notification_service: MagicMock
    ) -> None:
        service = ToolsetTokenRefreshService(mock_config_service, notification_service=mock_notification_service)
        mock_config_service.get_config = AsyncMock(
            return_value={"isAuthenticated": True, "toolsetType": "confluence"}
        )

        await service._mark_toolset_unauthenticated(CONFIG_PATH)

        mock_config_service.set_config.assert_awaited_once()
        mock_notification_service.publish_notification.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_notification_failure_does_not_raise(
        self, mock_config_service: MagicMock, mock_notification_service: MagicMock
    ) -> None:
        mock_notification_service.publish_notification = AsyncMock(side_effect=RuntimeError("kafka down"))
        service = ToolsetTokenRefreshService(mock_config_service, notification_service=mock_notification_service)
        mock_config_service.get_config = AsyncMock(
            return_value={"isAuthenticated": True, "toolsetType": "confluence", "orgId": "org-1", "userId": "user-1"}
        )

        await service._mark_toolset_unauthenticated(CONFIG_PATH)  # no raise

        mock_config_service.set_config.assert_awaited_once()


class TestToolsetInvalidClient:
    """invalid_client deauthenticates the whole toolset instance immediately (no strike
    counter) — the OAuth client config is shared by every user and only an admin can fix it."""

    INSTANCE_PATH_1 = "/services/toolsets/2ae0d015-64aa-4f61-8bab-af0d29e737f0/user-1"
    INSTANCE_PATH_2 = "/services/toolsets/2ae0d015-64aa-4f61-8bab-af0d29e737f0/user-2"

    @pytest.fixture
    def mock_notification_service(self) -> MagicMock:
        svc = MagicMock()
        svc.publish_notification = AsyncMock()
        return svc

    def _wire(self, mock_config_service: MagicMock, records: dict) -> dict:
        mock_config_service.list_keys_in_directory = AsyncMock(return_value=list(records))

        async def _get(path, default=None, use_cache=True) -> dict | None:
            return records.get(path, default)

        written: dict = {}

        async def _set(path, record) -> bool:
            written[path] = record
            return True

        mock_config_service.get_config = AsyncMock(side_effect=_get)
        mock_config_service.set_config = AsyncMock(side_effect=_set)
        return written

    @pytest.mark.asyncio
    async def test_deauthenticates_whole_instance_and_notifies_admins_once(
        self, mock_config_service: MagicMock, mock_notification_service: MagicMock
    ) -> None:
        service = ToolsetTokenRefreshService(mock_config_service, notification_service=mock_notification_service)
        records = {
            self.INSTANCE_PATH_1: {"isAuthenticated": True, "toolsetType": "confluence", "orgId": "org-1", "userId": "user-1"},
            self.INSTANCE_PATH_2: {"isAuthenticated": True, "toolsetType": "confluence", "orgId": "org-1", "userId": "user-2"},
        }
        written = self._wire(mock_config_service, records)
        service._invalid_refresh_failures[self.INSTANCE_PATH_1] = 2

        await service._handle_invalid_client(self.INSTANCE_PATH_1, InvalidClientError("invalid_client"))

        assert set(written) == {self.INSTANCE_PATH_1, self.INSTANCE_PATH_2}
        for record in written.values():
            assert record["isAuthenticated"] is False
            assert record["deauthReason"] == DEAUTH_REASON_INVALID_CLIENT
        assert self.INSTANCE_PATH_1 not in service._invalid_refresh_failures

        mock_notification_service.publish_notification.assert_awaited_once()
        kwargs = mock_notification_service.publish_notification.await_args.kwargs
        assert kwargs["recipient_roles"] == [NotificationRecipientRole.ADMIN]
        assert kwargs["payload"]["deauthedCredentials"] == 2
        assert "invalid client credentials" in kwargs["title"]

    @pytest.mark.asyncio
    async def test_already_deauthed_instance_does_not_renotify(
        self, mock_config_service: MagicMock, mock_notification_service: MagicMock
    ) -> None:
        service = ToolsetTokenRefreshService(mock_config_service, notification_service=mock_notification_service)
        records = {
            self.INSTANCE_PATH_1: {"isAuthenticated": False, "toolsetType": "confluence", "orgId": "org-1"},
        }
        written = self._wire(mock_config_service, records)

        await service._handle_invalid_client(self.INSTANCE_PATH_1, InvalidClientError("invalid_client"))

        assert written == {}
        mock_notification_service.publish_notification.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_legacy_path_stays_single_record(
        self, mock_config_service: MagicMock, mock_notification_service: MagicMock
    ) -> None:
        """A legacy /services/toolsets/{userId}/{type} path must not deauth its siblings —
        the parent directory groups unrelated toolset types with independent OAuth apps."""
        service = ToolsetTokenRefreshService(mock_config_service, notification_service=mock_notification_service)
        legacy_path = "/services/toolsets/user-1/slack"
        records = {legacy_path: {"isAuthenticated": True, "toolsetType": "slack", "orgId": "org-1"}}
        written = self._wire(mock_config_service, records)

        await service._handle_invalid_client(legacy_path, InvalidClientError("invalid_client"))

        assert set(written) == {legacy_path}
        mock_config_service.list_keys_in_directory.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_refresh_token_deauth_skips_already_unauthenticated(
        self, mock_config_service: MagicMock, mock_notification_service: MagicMock
    ) -> None:
        """_mark_toolset_unauthenticated now no-ops on already-deauthed records, so a
        racing second deauth cannot re-notify the owner."""
        service = ToolsetTokenRefreshService(mock_config_service, notification_service=mock_notification_service)
        mock_config_service.get_config = AsyncMock(return_value={"isAuthenticated": False})

        result = await service._mark_toolset_unauthenticated(CONFIG_PATH)

        assert result is None
        mock_config_service.set_config.assert_not_awaited()
        mock_notification_service.publish_notification.assert_not_awaited()
