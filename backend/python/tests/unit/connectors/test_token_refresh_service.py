"""Tests for app.connectors.core.base.token_service.token_refresh_service"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.core.base.token_service.oauth_service import (
    OAuthProvider,
    OAuthToken,
    RefreshTokenInvalidError,
)
from app.connectors.core.base.token_service.token_refresh_service import (
    MAX_REFRESH_TOKEN_INVALID_FAILURES,
    TokenRefreshService,
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
