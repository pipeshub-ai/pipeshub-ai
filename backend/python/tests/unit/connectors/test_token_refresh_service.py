"""Tests for app.connectors.core.base.token_service.token_refresh_service"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.core.base.token_service.oauth_service import (
    DEAUTH_REASON_INVALID_CLIENT,
    InvalidClientError,
    OAuthProvider,
    OAuthToken,
    RefreshTokenInvalidError,
)
from app.connectors.core.base.token_service.token_refresh_service import (
    MAX_REFRESH_TOKEN_INVALID_FAILURES,
    TokenRefreshService,
)
from app.services.notification.types import (
    CONNECTOR_NOTIFICATION_LINK_PREFIX,
    NotificationRecipientRole,
    NotificationType,
)

CONNECTOR_ID = "conn-123"


@pytest.fixture
def mock_config_service() -> MagicMock:
    """Mock ConfigurationService with async get_config/set_config."""
    svc = MagicMock()
    svc.get_config = AsyncMock(return_value={})
    svc.set_config = AsyncMock()
    return svc


@pytest.fixture
def mock_graph_provider() -> MagicMock:
    """Mock IGraphDBProvider with async update_node."""
    provider = MagicMock()
    provider.update_node = AsyncMock(return_value=True)
    provider.get_document = AsyncMock(return_value=None)
    return provider


@pytest.fixture
def service(mock_config_service: MagicMock, mock_graph_provider: MagicMock) -> TokenRefreshService:
    return TokenRefreshService(mock_config_service, mock_graph_provider)


def _connector_config() -> dict:
    """Connector config using the auth-config credential fallback path."""
    return {
        "auth": {
            "clientId": "test-client-id",
            "clientSecret": "test-client-secret",
            "authorizeUrl": "https://auth.example.com/authorize",
            "tokenUrl": "https://auth.example.com/token",
            "redirectUri": "http://localhost/callback",
        },
        "credentials": {"access_token": "old-access", "refresh_token": "old-refresh"},
    }


# ---------------------------------------------------------------------------
# Refresh-token-invalid threshold behavior
# ---------------------------------------------------------------------------


class TestRefreshTokenInvalidThreshold:
    """Tests for _handle_refresh_token_invalid() deactivation threshold."""

    @pytest.mark.asyncio
    async def test_deactivates_only_on_threshold_rejection(self, service: TokenRefreshService, mock_graph_provider: MagicMock) -> None:
        """First N-1 rejections leave the connector untouched; the Nth deactivates it."""
        error = RefreshTokenInvalidError("refresh_token is invalid")

        for _ in range(MAX_REFRESH_TOKEN_INVALID_FAILURES - 1):
            await service._handle_refresh_token_invalid(CONNECTOR_ID, error)

        mock_graph_provider.update_node.assert_not_awaited()
        assert service._invalid_refresh_failures[CONNECTOR_ID] == MAX_REFRESH_TOKEN_INVALID_FAILURES - 1

        await service._handle_refresh_token_invalid(CONNECTOR_ID, error)

        mock_graph_provider.update_node.assert_awaited_once()
        key, collection, updates = mock_graph_provider.update_node.await_args.args
        assert key == CONNECTOR_ID
        assert collection == "apps"
        assert updates["isAuthenticated"] is False
        assert updates["isActive"] is False
        assert CONNECTOR_ID not in service._invalid_refresh_failures

    @pytest.mark.asyncio
    async def test_deactivation_publishes_app_disabled_event(
        self, mock_config_service: MagicMock, mock_graph_provider: MagicMock
    ) -> None:
        """With a producer available, deactivation reuses the appDisabled event
        and leaves isActive to its consumer."""
        producer = MagicMock()
        producer.send_message = AsyncMock()
        service = TokenRefreshService(mock_config_service, mock_graph_provider, producer)

        mock_graph_provider.get_document = AsyncMock(
            return_value={"_key": CONNECTOR_ID, "type": "Confluence", "appGroup": "Atlassian", "scope": "team"}
        )
        mock_graph_provider.get_edges_to_node = AsyncMock(return_value=[{"_from": "orgs/org-1"}])

        error = RefreshTokenInvalidError("refresh_token is invalid")
        for _ in range(MAX_REFRESH_TOKEN_INVALID_FAILURES):
            await service._handle_refresh_token_invalid(CONNECTOR_ID, error)

        producer.send_message.assert_awaited_once()
        kwargs = producer.send_message.await_args.kwargs
        assert kwargs["topic"] == "entity-events"
        assert kwargs["message"]["eventType"] == "appDisabled"
        payload = kwargs["message"]["payload"]
        assert payload["connectorId"] == CONNECTOR_ID
        assert payload["orgId"] == "org-1"
        assert payload["apps"] == ["confluence"]

        _, _, updates = mock_graph_provider.update_node.await_args.args
        assert updates["isAuthenticated"] is False
        assert "isActive" not in updates

    @pytest.mark.asyncio
    async def test_event_send_failure_falls_back_to_direct_disable(
        self, mock_config_service: MagicMock, mock_graph_provider: MagicMock
    ) -> None:
        """If the appDisabled publish fails, isActive is written directly instead."""
        producer = MagicMock()
        producer.send_message = AsyncMock(side_effect=Exception("kafka down"))
        service = TokenRefreshService(mock_config_service, mock_graph_provider, producer)

        mock_graph_provider.get_document = AsyncMock(
            return_value={"_key": CONNECTOR_ID, "type": "Confluence", "appGroup": "Atlassian", "scope": "team"}
        )
        mock_graph_provider.get_edges_to_node = AsyncMock(return_value=[{"_from": "orgs/org-1"}])

        error = RefreshTokenInvalidError("refresh_token is invalid")
        for _ in range(MAX_REFRESH_TOKEN_INVALID_FAILURES):
            await service._handle_refresh_token_invalid(CONNECTOR_ID, error)

        _, _, updates = mock_graph_provider.update_node.await_args.args
        assert updates["isAuthenticated"] is False
        assert updates["isActive"] is False

    @pytest.mark.asyncio
    async def test_scan_skips_explicitly_unauthenticated_connectors(
        self, service: TokenRefreshService, mock_config_service: MagicMock
    ) -> None:
        """Explicit isAuthenticated=False is skipped; missing flag (legacy) is kept."""
        mock_config_service.get_config = AsyncMock(
            return_value={"credentials": {"refresh_token": "tok"}}
        )
        connectors = [
            {"_key": "dead", "authType": "OAUTH", "isAuthenticated": False},
            {"_key": "legacy", "authType": "OAUTH"},
            {"_key": "live", "authType": "OAUTH", "isAuthenticated": True},
            {"_key": "api", "authType": "API_TOKEN", "isAuthenticated": False},
        ]

        result = await service._filter_authenticated_oauth_connectors(connectors)

        assert [c["_key"] for c in result] == ["legacy", "live"]

    @pytest.mark.asyncio
    async def test_successful_refresh_resets_failure_count(
        self, service: TokenRefreshService, mock_config_service: MagicMock, mock_graph_provider: MagicMock
    ) -> None:
        """A successful refresh clears the streak; deactivation needs N new consecutive failures."""
        error = RefreshTokenInvalidError("refresh_token is invalid")

        for _ in range(MAX_REFRESH_TOKEN_INVALID_FAILURES - 2):
            await service._handle_refresh_token_invalid(CONNECTOR_ID, error)

        mock_config_service.get_config = AsyncMock(return_value=_connector_config())
        new_token = OAuthToken(access_token="new-access", refresh_token="new-refresh", expires_in=3600)
        with (
            patch.object(OAuthProvider, "refresh_access_token", AsyncMock(return_value=new_token)),
            patch.object(OAuthProvider, "close", AsyncMock()),
        ):
            await service.refresh_now(CONNECTOR_ID, "confluence", "old-refresh")

        assert CONNECTOR_ID not in service._invalid_refresh_failures

        for _ in range(MAX_REFRESH_TOKEN_INVALID_FAILURES - 1):
            await service._handle_refresh_token_invalid(CONNECTOR_ID, error)
        mock_graph_provider.update_node.assert_not_awaited()

        await service._handle_refresh_token_invalid(CONNECTOR_ID, error)
        mock_graph_provider.update_node.assert_awaited_once()


class TestConnectorInvalidClient:
    """invalid_client deactivates immediately — no strike counter, distinct audit reason."""

    @pytest.mark.asyncio
    async def test_first_invalid_client_deactivates_with_reason(
        self, service: TokenRefreshService, mock_graph_provider: MagicMock
    ) -> None:
        service._invalid_refresh_failures[CONNECTOR_ID] = 1  # prior refresh-token strikes are irrelevant

        await service._handle_invalid_client(CONNECTOR_ID, InvalidClientError("invalid_client"))

        mock_graph_provider.update_node.assert_awaited_once()
        _, _, updates = mock_graph_provider.update_node.await_args.args
        assert updates["isAuthenticated"] is False
        assert updates["deauthReason"] == DEAUTH_REASON_INVALID_CLIENT
        assert CONNECTOR_ID not in service._invalid_refresh_failures

    @pytest.mark.asyncio
    async def test_refresh_now_reraises_after_deactivation(
        self, service: TokenRefreshService,
    ) -> None:
        with (
            patch.object(service, "_perform_token_refresh", new=AsyncMock(side_effect=InvalidClientError("invalid_client"))),
            patch.object(service, "_handle_invalid_client", new=AsyncMock()) as handle,
        ):
            with pytest.raises(InvalidClientError):
                await service.refresh_now(CONNECTOR_ID, "confluence", "rt")
        handle.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_already_deauthed_connector_does_not_renotify(
        self, mock_config_service: MagicMock, mock_graph_provider: MagicMock
    ) -> None:
        """A repeat invalid_client on an already-deauthed connector (in-flight refresh_now,
        a task mid-refresh when the flip landed) must not rewrite state or re-notify."""
        notification_service = MagicMock()
        notification_service.publish_notification = AsyncMock()
        service = TokenRefreshService(
            mock_config_service, mock_graph_provider, notification_service=notification_service
        )
        mock_graph_provider.get_document = AsyncMock(
            return_value={"_key": CONNECTOR_ID, "type": "Confluence", "isAuthenticated": False, "orgId": "org-1"}
        )

        await service._handle_invalid_client(CONNECTOR_ID, InvalidClientError("invalid_client"))

        mock_graph_provider.update_node.assert_not_awaited()
        notification_service.publish_notification.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_precheck_failure_still_deactivates(
        self, service: TokenRefreshService, mock_graph_provider: MagicMock
    ) -> None:
        """The guard is only dedupe — a failed pre-check read must not block the deauth."""
        mock_graph_provider.get_document = AsyncMock(side_effect=RuntimeError("arango down"))

        await service._handle_invalid_client(CONNECTOR_ID, InvalidClientError("invalid_client"))

        mock_graph_provider.update_node.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_invalid_client_notifies_admins_and_personal_owner(
        self, mock_config_service: MagicMock, mock_graph_provider: MagicMock
    ) -> None:
        notification_service = MagicMock()
        notification_service.publish_notification = AsyncMock()
        service = TokenRefreshService(
            mock_config_service, mock_graph_provider, notification_service=notification_service
        )
        mock_graph_provider.get_document = AsyncMock(
            return_value={"_key": CONNECTOR_ID, "type": "Gmail", "scope": "personal", "orgId": "org-1", "createdBy": "user-9"}
        )

        await service._handle_invalid_client(CONNECTOR_ID, InvalidClientError("invalid_client"))

        kwargs = notification_service.publish_notification.await_args.kwargs
        assert kwargs["recipient_roles"] == [NotificationRecipientRole.ADMIN]
        assert kwargs["recipient_user_ids"] == ["user-9"]  # owner told why their connector stopped
        assert kwargs["payload"]["deauthReason"] == DEAUTH_REASON_INVALID_CLIENT
        assert "invalid client credentials" in kwargs["title"]


class TestConnectorDeactivationNotification:
    """Deactivation must reach a human: the appDisabled event only updates platform state."""

    def _service_with_notifications(
        self, mock_config_service: MagicMock, mock_graph_provider: MagicMock, app_doc: dict
    ) -> tuple[TokenRefreshService, MagicMock]:
        notification_service = MagicMock()
        notification_service.publish_notification = AsyncMock()
        service = TokenRefreshService(
            mock_config_service, mock_graph_provider, notification_service=notification_service
        )
        mock_graph_provider.get_document = AsyncMock(return_value=app_doc)
        mock_graph_provider.get_edges_to_node = AsyncMock(return_value=[{"_from": "orgs/org-1"}])
        return service, notification_service

    @pytest.mark.asyncio
    async def test_team_connector_notifies_admins_with_redirect_link(
        self, mock_config_service: MagicMock, mock_graph_provider: MagicMock
    ) -> None:
        app_doc = {"_key": CONNECTOR_ID, "type": "Confluence", "name": "Confluence", "scope": "team"}
        service, notification_service = self._service_with_notifications(
            mock_config_service, mock_graph_provider, app_doc
        )

        await service._mark_connector_unauthenticated(CONNECTOR_ID)

        notification_service.publish_notification.assert_awaited_once()
        kwargs = notification_service.publish_notification.await_args.kwargs
        assert kwargs["type"] == NotificationType.CONNECTOR_AUTH_ERROR
        assert kwargs["org_id"] == "org-1"  # resolved via the org→app edge fallback
        assert kwargs["recipient_roles"] == [NotificationRecipientRole.ADMIN]
        assert kwargs["recipient_user_ids"] is None
        assert kwargs["redirect_link"] == CONNECTOR_NOTIFICATION_LINK_PREFIX + "team/?connectorType=Confluence"
        assert "Confluence" in kwargs["title"]

    @pytest.mark.asyncio
    async def test_personal_connector_notifies_owner(
        self, mock_config_service: MagicMock, mock_graph_provider: MagicMock
    ) -> None:
        app_doc = {
            "_key": CONNECTOR_ID, "type": "Gmail", "scope": "personal",
            "orgId": "org-1", "createdBy": "user-9",
        }
        service, notification_service = self._service_with_notifications(
            mock_config_service, mock_graph_provider, app_doc
        )

        await service._mark_connector_unauthenticated(CONNECTOR_ID)

        kwargs = notification_service.publish_notification.await_args.kwargs
        assert kwargs["recipient_user_ids"] == ["user-9"]
        assert kwargs["recipient_roles"] is None
        assert kwargs["redirect_link"] == CONNECTOR_NOTIFICATION_LINK_PREFIX + "personal/?connectorType=Gmail"

    @pytest.mark.asyncio
    async def test_failed_state_update_does_not_notify(
        self, mock_config_service: MagicMock, mock_graph_provider: MagicMock
    ) -> None:
        """No notification if the connector was not actually deactivated."""
        app_doc = {"_key": CONNECTOR_ID, "type": "Confluence", "scope": "team", "orgId": "org-1"}
        service, notification_service = self._service_with_notifications(
            mock_config_service, mock_graph_provider, app_doc
        )
        mock_graph_provider.update_node = AsyncMock(return_value=False)

        await service._mark_connector_unauthenticated(CONNECTOR_ID)

        notification_service.publish_notification.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_notification_failure_is_swallowed(
        self, mock_config_service: MagicMock, mock_graph_provider: MagicMock
    ) -> None:
        app_doc = {"_key": CONNECTOR_ID, "type": "Confluence", "scope": "team", "orgId": "org-1"}
        service, notification_service = self._service_with_notifications(
            mock_config_service, mock_graph_provider, app_doc
        )
        notification_service.publish_notification = AsyncMock(side_effect=RuntimeError("kafka down"))

        await service._mark_connector_unauthenticated(CONNECTOR_ID)

        _, _, updates = mock_graph_provider.update_node.await_args.args
        assert updates["isAuthenticated"] is False
