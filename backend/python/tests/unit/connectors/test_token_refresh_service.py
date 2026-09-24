"""Tests for app.connectors.core.base.token_service.token_refresh_service"""

import asyncio
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


class TestRotatingRefreshTokenSafety:
    """Atlassian-style rotating refresh tokens must never be reused after a successful refresh."""

    @pytest.mark.asyncio
    async def test_stale_refresh_now_adopts_already_rotated_token(
        self, service: TokenRefreshService, mock_config_service: MagicMock
    ) -> None:
        """If another refresh already persisted a new RT, do not POST the consumed one."""
        from datetime import datetime

        live = OAuthToken(
            access_token="new-access",
            refresh_token="rt-new",
            expires_in=3600,
            created_at=datetime.now(),
        )
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": _connector_config()["auth"],
                "credentials": live.to_dict(),
            }
        )
        with (
            patch.object(OAuthProvider, "refresh_access_token", AsyncMock()) as mock_refresh,
            patch.object(OAuthProvider, "close", AsyncMock()),
        ):
            result = await service.refresh_now(CONNECTOR_ID, "jira", "rt-old")

        mock_refresh.assert_not_called()
        assert result.refresh_token == "rt-new"
        assert result.access_token == "new-access"

    @pytest.mark.asyncio
    async def test_overlapping_refreshes_send_rotating_token_once(
        self, service: TokenRefreshService, mock_config_service: MagicMock
    ) -> None:
        """Two overlapping refresh_now calls must not both send the same refresh token."""
        from copy import deepcopy
        from datetime import datetime, timedelta

        store = {
            "auth": _connector_config()["auth"],
            "credentials": OAuthToken(
                access_token="old-access",
                refresh_token="rt-old",
                expires_in=3600,
                created_at=datetime.now() - timedelta(hours=2),
            ).to_dict(),
        }
        sent: list[str] = []
        first_entered = asyncio.Event()
        release_first = asyncio.Event()

        async def get_config(_key, **_kw):
            return deepcopy(store)

        async def set_config(_key, value):
            store.clear()
            store.update(deepcopy(value))

        mock_config_service.get_config = AsyncMock(side_effect=get_config)
        mock_config_service.set_config = AsyncMock(side_effect=set_config)

        async def fake_refresh(refresh_token: str):
            sent.append(refresh_token)
            if refresh_token != "rt-old":
                raise RefreshTokenInvalidError("refresh_token is invalid")
            if not first_entered.is_set():
                first_entered.set()
                await release_first.wait()
            new_token = OAuthToken(
                access_token="new-access",
                refresh_token="rt-new",
                expires_in=3600,
            )
            store["credentials"] = new_token.to_dict()
            return new_token

        with (
            patch.object(OAuthProvider, "refresh_access_token", AsyncMock(side_effect=fake_refresh)),
            patch.object(OAuthProvider, "close", AsyncMock()),
        ):
            task_a = asyncio.create_task(
                service.refresh_now(CONNECTOR_ID, "jira", "rt-old")
            )
            await first_entered.wait()
            task_b = asyncio.create_task(
                service.refresh_now(CONNECTOR_ID, "jira", "rt-old")
            )
            release_first.set()
            result_a, result_b = await asyncio.gather(task_a, task_b)

        assert sent == ["rt-old"]
        assert result_a.refresh_token == "rt-new"
        assert result_b.refresh_token == "rt-new"

    @pytest.mark.asyncio
    async def test_persist_reloads_config_so_stale_snapshot_cannot_clobber(
        self, service: TokenRefreshService, mock_config_service: MagicMock
    ) -> None:
        """After the provider returns, persist must write the latest config plus new credentials."""
        reads = [
            {
                "auth": _connector_config()["auth"],
                "filters": {"sync": "old"},
                "credentials": {"access_token": "old-access", "refresh_token": "rt-old"},
            },
            {
                "auth": _connector_config()["auth"],
                "filters": {"sync": "new"},
                "credentials": {"access_token": "old-access", "refresh_token": "rt-old"},
            },
        ]

        async def get_config(_key, **_kw):
            return reads.pop(0) if reads else {
                "auth": _connector_config()["auth"],
                "filters": {"sync": "new"},
                "credentials": {"access_token": "new-access", "refresh_token": "rt-new"},
            }

        mock_config_service.get_config = AsyncMock(side_effect=get_config)
        mock_config_service.set_config = AsyncMock()
        new_token = OAuthToken(access_token="new-access", refresh_token="rt-new", expires_in=3600)

        with (
            patch.object(OAuthProvider, "refresh_access_token", AsyncMock(return_value=new_token)),
            patch.object(OAuthProvider, "close", AsyncMock()),
        ):
            await service.refresh_now(CONNECTOR_ID, "jira", "rt-old")

        written = mock_config_service.set_config.await_args.args[1]
        assert written["filters"] == {"sync": "new"}
        assert written["credentials"]["refresh_token"] == "rt-new"
