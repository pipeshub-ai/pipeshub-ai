"""Unit tests for app.connectors.core.base.token_service.mcp_token_refresh_service."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.mcp import oauth_client as oauth_client_module
from app.agents.mcp.models import OAuthTokens
from app.connectors.core.base.token_service.mcp_token_refresh_service import (
    DEAUTH_REASON_INVALID_CLIENT,
    MAX_REFRESH_TOKEN_INVALID_FAILURES,
    MCP_SERVERS_NOTIFICATION_LINK,
    PROACTIVE_REFRESH_WINDOW_SECONDS,
    MCPTokenRefreshService,
)
from app.services.notification.types import NotificationRecipientRole, NotificationType

CONFIG_PATH = "/services/mcp/credentials/inst-1/user-1"


@pytest.fixture
def mock_config_service() -> MagicMock:
    svc = MagicMock()
    svc.get_config = AsyncMock(return_value=None)
    svc.set_config = AsyncMock(return_value=True)
    svc.delete_config = AsyncMock(return_value=True)
    svc.list_keys_in_directory = AsyncMock(return_value=[])
    return svc


@pytest.fixture
def service(mock_config_service: MagicMock) -> MCPTokenRefreshService:
    return MCPTokenRefreshService(mock_config_service)


def _oauth_token_dict(expires_in: int = 3600, refresh_token: str = "refresh-1", token_url: str = "https://example.com/token") -> dict:
    return {
        "accessToken": "access-1",
        "tokenType": "Bearer",
        "refreshToken": refresh_token,
        "expiresIn": expires_in,
        "tokenUrl": token_url,
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }


class TestLoadTokenFromConfig:
    @pytest.mark.asyncio
    async def test_not_authenticated_returns_false(self, service: MCPTokenRefreshService, mock_config_service: MagicMock) -> None:
        mock_config_service.get_config = AsyncMock(return_value={"isAuthenticated": False})
        token, has_oauth = await service._load_token_from_config(CONFIG_PATH)
        assert has_oauth is False
        assert token is None

    @pytest.mark.asyncio
    async def test_no_refresh_token_returns_false(self, service: MCPTokenRefreshService, mock_config_service: MagicMock) -> None:
        mock_config_service.get_config = AsyncMock(
            return_value={"isAuthenticated": True, "oauthTokens": {"accessToken": "tok"}}
        )
        _, has_oauth = await service._load_token_from_config(CONFIG_PATH)
        assert has_oauth is False

    @pytest.mark.asyncio
    async def test_valid_oauth_record_parsed(self, service: MCPTokenRefreshService, mock_config_service: MagicMock) -> None:
        mock_config_service.get_config = AsyncMock(
            return_value={"isAuthenticated": True, "oauthTokens": _oauth_token_dict()}
        )
        token, has_oauth = await service._load_token_from_config(CONFIG_PATH)
        assert has_oauth is True
        assert token.access_token == "access-1"
        assert token.refresh_token == "refresh-1"

    @pytest.mark.asyncio
    async def test_non_dict_record_returns_false(self, service: MCPTokenRefreshService, mock_config_service: MagicMock) -> None:
        mock_config_service.get_config = AsyncMock(return_value=None)
        _, has_oauth = await service._load_token_from_config(CONFIG_PATH)
        assert has_oauth is False


class TestCalculateRefreshDelay:
    def test_no_expiry_refreshes_immediately(self, service: MCPTokenRefreshService) -> None:
        token = OAuthTokens(access_token="tok", expires_in=None)
        delay, _ = service._calculate_refresh_delay(token)
        assert delay == 0.0

    def test_long_lived_token_uses_proactive_window(self, service: MCPTokenRefreshService) -> None:
        created = datetime.now(timezone.utc)
        token = OAuthTokens(access_token="tok", expires_in=7200, created_at=created)
        delay, refresh_time = service._calculate_refresh_delay(token)
        expected_refresh_time = created + timedelta(seconds=7200 - PROACTIVE_REFRESH_WINDOW_SECONDS)
        assert abs((refresh_time - expected_refresh_time).total_seconds()) < 1

    def test_short_lived_token_uses_ratio_window(self, service: MCPTokenRefreshService) -> None:
        created = datetime.now(timezone.utc)
        token = OAuthTokens(access_token="tok", expires_in=120, created_at=created)
        delay, refresh_time = service._calculate_refresh_delay(token)
        # 20% of 120s = 24s, which is below the 60s floor, so the floor (min(60, 119)) applies.
        expected_refresh_time = created + timedelta(seconds=120 - 60)
        assert abs((refresh_time - expected_refresh_time).total_seconds()) < 1

    def test_already_expired_returns_zero_or_negative_delay(self, service: MCPTokenRefreshService) -> None:
        created = datetime.now(timezone.utc) - timedelta(hours=2)
        token = OAuthTokens(access_token="tok", expires_in=3600, created_at=created)
        delay, _ = service._calculate_refresh_delay(token)
        assert delay <= 0


class TestPerformTokenRefresh:
    @pytest.mark.asyncio
    async def test_refreshes_using_persisted_token_url_and_dcr_client(
        self, service: MCPTokenRefreshService, mock_config_service: MagicMock
    ) -> None:
        record = {"oauthTokens": _oauth_token_dict(token_url="https://custom.example.com/token")}

        async def _get_config_side_effect(path, default=None, use_cache=False):
            if path == CONFIG_PATH:
                return record
            if path.endswith("/dcr-client"):
                return {"clientId": "dcr-cid", "clientSecret": "dcr-secret"}
            return None

        mock_config_service.get_config = AsyncMock(side_effect=_get_config_side_effect)

        new_tokens = OAuthTokens(access_token="new-access", refresh_token="new-refresh", expires_in=3600, token_url="https://custom.example.com/token")
        with patch.object(oauth_client_module, "refresh_access_token", new=AsyncMock(return_value=new_tokens)) as mock_refresh:
            result = await service._perform_token_refresh(CONFIG_PATH, "refresh-1")

        assert result.access_token == "new-access"
        mock_refresh.assert_awaited_once()
        call_kwargs = mock_refresh.await_args.kwargs
        assert call_kwargs["token_url"] == "https://custom.example.com/token"
        assert call_kwargs["client_id"] == "dcr-cid"
        mock_config_service.set_config.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_missing_token_url_raises(self, service: MCPTokenRefreshService, mock_config_service: MagicMock) -> None:
        record = {"oauthTokens": {"accessToken": "tok", "refreshToken": "refresh-1"}}
        mock_config_service.get_config = AsyncMock(return_value=record)
        with pytest.raises(ValueError, match="tokenUrl"):
            await service._perform_token_refresh(CONFIG_PATH, "refresh-1")

    @pytest.mark.asyncio
    async def test_missing_client_config_raises(self, service: MCPTokenRefreshService, mock_config_service: MagicMock) -> None:
        record = {"oauthTokens": _oauth_token_dict()}
        mock_config_service.get_config = AsyncMock(return_value=record)
        with pytest.raises(ValueError, match="OAuth client"):
            await service._perform_token_refresh(CONFIG_PATH, "refresh-1")

    @pytest.mark.asyncio
    async def test_falls_back_to_admin_oauth_client_when_no_dcr(
        self, service: MCPTokenRefreshService, mock_config_service: MagicMock
    ) -> None:
        record = {"oauthTokens": _oauth_token_dict()}

        async def _get_config_side_effect(path, default=None, use_cache=False):
            if path == CONFIG_PATH:
                return record
            if "dcr-client" in path:
                return None
            if "oauth-clients" in path:
                return {"clientId": "shared-cid", "clientSecret": "shared-secret"}
            return None

        mock_config_service.get_config = AsyncMock(side_effect=_get_config_side_effect)
        new_tokens = OAuthTokens(access_token="new-access", expires_in=3600)
        with patch.object(oauth_client_module, "refresh_access_token", new=AsyncMock(return_value=new_tokens)) as mock_refresh:
            await service._perform_token_refresh(CONFIG_PATH, "refresh-1")

        assert mock_refresh.await_args.kwargs["client_id"] == "shared-cid"


class TestHandleRefreshTokenInvalid:
    @pytest.mark.asyncio
    async def test_deactivates_only_on_threshold_rejection(
        self, service: MCPTokenRefreshService, mock_config_service: MagicMock
    ) -> None:
        error = oauth_client_module.MCPRefreshTokenInvalidError("refresh_token is invalid")

        for _ in range(MAX_REFRESH_TOKEN_INVALID_FAILURES - 1):
            await service._handle_refresh_token_invalid(CONFIG_PATH, error)

        mock_config_service.set_config.assert_not_awaited()
        assert service._invalid_refresh_failures[CONFIG_PATH] == MAX_REFRESH_TOKEN_INVALID_FAILURES - 1

        mock_config_service.get_config = AsyncMock(return_value={"isAuthenticated": True})
        await service._handle_refresh_token_invalid(CONFIG_PATH, error)

        mock_config_service.set_config.assert_awaited_once()
        path, record = mock_config_service.set_config.await_args.args
        assert path == CONFIG_PATH
        assert record["isAuthenticated"] is False
        assert record["deauthReason"] == "refresh_token_invalid"
        assert CONFIG_PATH not in service._invalid_refresh_failures

    @pytest.mark.asyncio
    async def test_tolerates_missing_record_on_deactivation(
        self, service: MCPTokenRefreshService, mock_config_service: MagicMock
    ) -> None:
        error = oauth_client_module.MCPRefreshTokenInvalidError("invalid_grant")
        mock_config_service.get_config = AsyncMock(return_value=None)

        for _ in range(MAX_REFRESH_TOKEN_INVALID_FAILURES):
            await service._handle_refresh_token_invalid(CONFIG_PATH, error)

        mock_config_service.set_config.assert_not_awaited()


class TestHandleInvalidClient:
    """`invalid_client` means the OAuth client id/secret is wrong — shared by every
    credential of the instance, so the whole instance is deauthenticated at once, pending
    refreshes are cancelled (no retry can succeed), and org admins are notified."""

    INST1_USER2 = "/services/mcp/credentials/inst-1/user-2"
    INSTANCE_PATH = "/services/mcp/instances/org-1/inst-1"

    @pytest.fixture
    def mock_notification_service(self) -> MagicMock:
        svc = MagicMock()
        svc.publish_notification = AsyncMock()
        return svc

    def _wire_instance_records(self, mock_config_service: MagicMock, records: dict) -> dict:
        mock_config_service.list_keys_in_directory = AsyncMock(
            return_value=[CONFIG_PATH, self.INST1_USER2, f"{CONFIG_PATH}/dcr-client"]
        )

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
    async def test_deauthenticates_whole_instance_and_notifies_admins(
        self, mock_config_service: MagicMock, mock_notification_service: MagicMock,
    ) -> None:
        service = MCPTokenRefreshService(mock_config_service, notification_service=mock_notification_service)
        records = {
            CONFIG_PATH: {"isAuthenticated": True, "orgId": "org-1", "oauthTokens": _oauth_token_dict()},
            self.INST1_USER2: {"isAuthenticated": True, "orgId": "org-1", "oauthTokens": _oauth_token_dict()},
            self.INSTANCE_PATH: {"name": "Custom Jira MCP"},
        }
        written = self._wire_instance_records(mock_config_service, records)
        service._invalid_refresh_failures[CONFIG_PATH] = 2

        await service._handle_invalid_client(
            CONFIG_PATH, oauth_client_module.MCPInvalidClientError("invalid_client")
        )

        assert set(written) == {CONFIG_PATH, self.INST1_USER2}  # nested dcr-client key untouched
        for record in written.values():
            assert record["isAuthenticated"] is False
            assert record["deauthReason"] == DEAUTH_REASON_INVALID_CLIENT
            assert record["deauthAt"] > 0
        assert CONFIG_PATH not in service._invalid_refresh_failures

        mock_notification_service.publish_notification.assert_awaited_once()
        kwargs = mock_notification_service.publish_notification.await_args.kwargs
        assert kwargs["org_id"] == "org-1"
        assert kwargs["type"] == NotificationType.MCP_AUTH_ERROR
        assert kwargs["recipient_roles"] == [NotificationRecipientRole.ADMIN]
        assert "Custom Jira MCP" in kwargs["title"]
        assert "invalid_client" in kwargs["message"]
        assert kwargs["payload"]["instanceId"] == "inst-1"
        assert kwargs["payload"]["deauthedCredentials"] == 2
        assert kwargs["redirect_link"] == MCP_SERVERS_NOTIFICATION_LINK

    @pytest.mark.asyncio
    async def test_refresh_credential_routes_invalid_client_to_handler(
        self, service: MCPTokenRefreshService, mock_config_service: MagicMock,
    ) -> None:
        expired = _oauth_token_dict(expires_in=3600)
        expired["createdAt"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        mock_config_service.get_config = AsyncMock(
            return_value={"isAuthenticated": True, "oauthTokens": expired}
        )

        with (
            patch.object(
                service,
                "_perform_token_refresh",
                new=AsyncMock(side_effect=oauth_client_module.MCPInvalidClientError("invalid_client")),
            ),
            patch.object(service, "_handle_invalid_client", new=AsyncMock()) as handle_invalid_client,
            patch.object(service, "_handle_refresh_token_invalid", new=AsyncMock()) as handle_refresh_invalid,
            patch.object(service, "schedule_token_refresh", new=AsyncMock()) as schedule,
        ):
            await service._refresh_credential(CONFIG_PATH)

        handle_invalid_client.assert_awaited_once()
        handle_refresh_invalid.assert_not_awaited()  # not the 3-strike path — immediate
        schedule.assert_not_awaited()  # no retry is scheduled

    @pytest.mark.asyncio
    async def test_cancels_pending_tasks_for_the_instance_only(
        self, service: MCPTokenRefreshService,
    ) -> None:
        token = OAuthTokens(access_token="tok", expires_in=3600)
        await service.schedule_token_refresh(CONFIG_PATH, token)
        await service.schedule_token_refresh(self.INST1_USER2, token)
        await service.schedule_token_refresh("/services/mcp/credentials/inst-2/user-1", token)

        await service._handle_invalid_client(
            CONFIG_PATH, oauth_client_module.MCPInvalidClientError("invalid_client")
        )

        assert CONFIG_PATH not in service._refresh_tasks
        assert self.INST1_USER2 not in service._refresh_tasks
        assert "/services/mcp/credentials/inst-2/user-1" in service._refresh_tasks
        service.cancel_refresh_task("/services/mcp/credentials/inst-2/user-1")

    @pytest.mark.asyncio
    async def test_does_not_cancel_the_task_it_runs_inside(
        self, service: MCPTokenRefreshService,
    ) -> None:
        """When invoked from a `_delayed_refresh` task registered for this credential,
        cancelling that task would abort the handler itself mid-flight — it must be skipped."""

        async def _run() -> None:
            await service._handle_invalid_client(
                CONFIG_PATH, oauth_client_module.MCPInvalidClientError("invalid_client")
            )

        task = asyncio.create_task(_run())
        service._refresh_tasks[CONFIG_PATH] = task  # register before the task first runs
        await task  # completes; no CancelledError
        assert not task.cancelled()
        service._refresh_tasks.pop(CONFIG_PATH, None)

    @pytest.mark.asyncio
    async def test_deauthenticates_without_notification_service(
        self, service: MCPTokenRefreshService, mock_config_service: MagicMock,
    ) -> None:
        records = {CONFIG_PATH: {"isAuthenticated": True, "orgId": "org-1", "oauthTokens": _oauth_token_dict()}}
        written = self._wire_instance_records(mock_config_service, records)

        await service._handle_invalid_client(
            CONFIG_PATH, oauth_client_module.MCPInvalidClientError("invalid_client")
        )

        assert written[CONFIG_PATH]["isAuthenticated"] is False

    @pytest.mark.asyncio
    async def test_notification_failure_does_not_undo_or_raise(
        self, mock_config_service: MagicMock, mock_notification_service: MagicMock,
    ) -> None:
        mock_notification_service.publish_notification = AsyncMock(side_effect=RuntimeError("kafka down"))
        service = MCPTokenRefreshService(mock_config_service, notification_service=mock_notification_service)
        records = {CONFIG_PATH: {"isAuthenticated": True, "orgId": "org-1", "oauthTokens": _oauth_token_dict()}}
        written = self._wire_instance_records(mock_config_service, records)

        await service._handle_invalid_client(
            CONFIG_PATH, oauth_client_module.MCPInvalidClientError("invalid_client")
        )

        assert written[CONFIG_PATH]["isAuthenticated"] is False

    @pytest.mark.asyncio
    async def test_deauth_waits_for_in_flight_refresh_lock(
        self, mock_config_service: MagicMock,
    ) -> None:
        """The deauth loop must serialize with `token_refresh`'s per-owner locks — an
        unlocked read-modify-write could clobber a re-auth landing mid-flight."""
        from app.agents.mcp import token_refresh as mcp_token_refresh_module

        service = MCPTokenRefreshService(mock_config_service)
        path = "/services/mcp/credentials/inst-lock/user-1"
        records = {path: {"isAuthenticated": True, "orgId": "org-1", "oauthTokens": _oauth_token_dict()}}
        mock_config_service.list_keys_in_directory = AsyncMock(return_value=[path])

        async def _get(p, default=None, use_cache=True) -> dict | None:
            return records.get(p, default)

        written: dict = {}

        async def _set(p, record) -> bool:
            written[p] = record
            return True

        mock_config_service.get_config = AsyncMock(side_effect=_get)
        mock_config_service.set_config = AsyncMock(side_effect=_set)

        lock = mcp_token_refresh_module.get_refresh_lock("inst-lock", "user-1")
        await lock.acquire()
        try:
            task = asyncio.create_task(
                service._handle_invalid_client(path, oauth_client_module.MCPInvalidClientError("invalid_client"))
            )
            await asyncio.sleep(0.05)
            assert written == {}  # blocked behind the in-flight refresh's lock
        finally:
            lock.release()

        await task
        assert path in written
        assert written[path]["isAuthenticated"] is False

    @pytest.mark.asyncio
    async def test_concurrent_handlers_for_same_instance_notify_once(
        self, mock_config_service: MagicMock, mock_notification_service: MagicMock,
    ) -> None:
        """When the shared client breaks, every credential's refresh fails at once — two
        concurrent handlers must produce exactly one admin notification and flip each
        record exactly once, not split the records between them and both notify. The
        mocks yield on every call so unserialized handlers would interleave."""
        service = MCPTokenRefreshService(mock_config_service, notification_service=mock_notification_service)
        path_1 = "/services/mcp/credentials/inst-conc/user-1"
        path_2 = "/services/mcp/credentials/inst-conc/user-2"
        records = {
            path_1: {"isAuthenticated": True, "orgId": "org-1", "oauthTokens": _oauth_token_dict()},
            path_2: {"isAuthenticated": True, "orgId": "org-1", "oauthTokens": _oauth_token_dict()},
        }
        writes: list = []

        async def _list(prefix) -> list:
            await asyncio.sleep(0)
            return [path_1, path_2]

        async def _get(path, default=None, use_cache=True) -> dict | None:
            await asyncio.sleep(0)
            return records.get(path, default)

        async def _set(path, record) -> bool:
            await asyncio.sleep(0)
            records[path] = record  # persist, so the other handler observes the flip
            writes.append(path)
            return True

        mock_config_service.list_keys_in_directory = AsyncMock(side_effect=_list)
        mock_config_service.get_config = AsyncMock(side_effect=_get)
        mock_config_service.set_config = AsyncMock(side_effect=_set)

        await asyncio.gather(
            service._handle_invalid_client(path_1, oauth_client_module.MCPInvalidClientError("invalid_client")),
            service._handle_invalid_client(path_2, oauth_client_module.MCPInvalidClientError("invalid_client")),
        )

        assert sorted(writes) == sorted([path_1, path_2])
        assert mock_notification_service.publish_notification.await_count == 1

    @pytest.mark.asyncio
    async def test_failed_persistence_does_not_count_or_notify(
        self, mock_config_service: MagicMock, mock_notification_service: MagicMock,
    ) -> None:
        """set_config returns False instead of raising; records whose flip did not persist
        are still authenticated and must not count toward the admin notification."""
        service = MCPTokenRefreshService(mock_config_service, notification_service=mock_notification_service)
        records = {
            CONFIG_PATH: {"isAuthenticated": True, "orgId": "org-1", "oauthTokens": _oauth_token_dict()},
        }
        self._wire_instance_records(mock_config_service, records)
        mock_config_service.set_config = AsyncMock(return_value=False)

        await service._handle_invalid_client(
            CONFIG_PATH, oauth_client_module.MCPInvalidClientError("invalid_client")
        )

        mock_notification_service.publish_notification.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_already_deauthed_instance_does_not_renotify(
        self, mock_config_service: MagicMock, mock_notification_service: MagicMock,
    ) -> None:
        """Concurrent in-flight refreshes of the same instance all fail with invalid_client;
        only the handler that actually flips records may notify, or admins get duplicates."""
        service = MCPTokenRefreshService(mock_config_service, notification_service=mock_notification_service)
        records = {
            CONFIG_PATH: {"isAuthenticated": False, "orgId": "org-1", "oauthTokens": _oauth_token_dict()},
            self.INST1_USER2: {"isAuthenticated": False, "orgId": "org-1", "oauthTokens": _oauth_token_dict()},
        }
        written = self._wire_instance_records(mock_config_service, records)

        await service._handle_invalid_client(
            CONFIG_PATH, oauth_client_module.MCPInvalidClientError("invalid_client")
        )

        assert written == {}
        mock_notification_service.publish_notification.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_org_id_skips_notification(
        self, mock_config_service: MagicMock, mock_notification_service: MagicMock,
    ) -> None:
        service = MCPTokenRefreshService(mock_config_service, notification_service=mock_notification_service)
        records = {CONFIG_PATH: {"isAuthenticated": True, "oauthTokens": _oauth_token_dict()}}
        written = self._wire_instance_records(mock_config_service, records)

        await service._handle_invalid_client(
            CONFIG_PATH, oauth_client_module.MCPInvalidClientError("invalid_client")
        )

        assert written[CONFIG_PATH]["isAuthenticated"] is False
        mock_notification_service.publish_notification.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_list_failure_still_deauthenticates_triggering_credential(
        self, service: MCPTokenRefreshService, mock_config_service: MagicMock,
    ) -> None:
        records = {CONFIG_PATH: {"isAuthenticated": True, "orgId": "org-1", "oauthTokens": _oauth_token_dict()}}
        written = self._wire_instance_records(mock_config_service, records)
        mock_config_service.list_keys_in_directory = AsyncMock(side_effect=RuntimeError("etcd down"))

        await service._handle_invalid_client(
            CONFIG_PATH, oauth_client_module.MCPInvalidClientError("invalid_client")
        )

        assert written[CONFIG_PATH]["isAuthenticated"] is False

    @pytest.mark.asyncio
    async def test_end_to_end_http_401_invalid_client_deauths_instance(
        self, mock_config_service: MagicMock, mock_notification_service: MagicMock,
    ) -> None:
        """Integration-style: a real 401 invalid_client HTTP response flows through the
        real `refresh_credential_record` and `oauth_client` classification into the
        handler — guards the wiring (exception types, import paths), which the other
        tests bypass by mocking `_perform_token_refresh`."""
        service = MCPTokenRefreshService(mock_config_service, notification_service=mock_notification_service)
        path = "/services/mcp/credentials/inst-e2e/user-1"
        expired = _oauth_token_dict(expires_in=3600)
        expired["createdAt"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        records = {
            path: {"isAuthenticated": True, "orgId": "org-1", "oauthTokens": expired},
            f"{path}/dcr-client": {"clientId": "cid", "clientSecret": "csec"},
        }
        mock_config_service.list_keys_in_directory = AsyncMock(return_value=[path])

        async def _get(p, default=None, use_cache=True) -> dict | None:
            return records.get(p, default)

        written: dict = {}

        async def _set(p, record) -> bool:
            written[p] = record
            return True

        mock_config_service.get_config = AsyncMock(side_effect=_get)
        mock_config_service.set_config = AsyncMock(side_effect=_set)

        resp = MagicMock()
        resp.status_code = 401
        resp.headers = {"content-type": "application/json"}
        resp.text = '{"error":"invalid_client","error_description":"Invalid client credentials"}'
        resp.json.return_value = {"error": "invalid_client"}
        http_client = MagicMock()
        http_client.post = AsyncMock(return_value=resp)
        client_cm = MagicMock()
        client_cm.__aenter__ = AsyncMock(return_value=http_client)
        client_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("app.agents.mcp.oauth_client.httpx.AsyncClient", return_value=client_cm):
            await service._refresh_credential(path)

        assert written[path]["isAuthenticated"] is False
        assert written[path]["deauthReason"] == DEAUTH_REASON_INVALID_CLIENT
        mock_notification_service.publish_notification.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_further_refresh_attempts_after_instance_deauth(
        self, service: MCPTokenRefreshService, mock_config_service: MagicMock,
    ) -> None:
        """Once deauthenticated, periodic scans must skip the credential entirely — the exact
        retry-forever loop this handling exists to break."""
        deauthed = {
            "isAuthenticated": False,
            "deauthReason": DEAUTH_REASON_INVALID_CLIENT,
            "oauthTokens": _oauth_token_dict(),
        }
        mock_config_service.list_keys_in_directory = AsyncMock(return_value=[CONFIG_PATH])
        mock_config_service.get_config = AsyncMock(return_value=deauthed)

        with patch.object(service, "_perform_token_refresh", new=AsyncMock()) as perform:
            await service._refresh_all_tokens_internal()
            await service._refresh_credential(CONFIG_PATH)

        perform.assert_not_awaited()


class TestScheduleTokenRefresh:
    @pytest.mark.asyncio
    async def test_no_expiry_skips_scheduling(self, service: MCPTokenRefreshService) -> None:
        token = OAuthTokens(access_token="tok", expires_in=None)
        await service.schedule_token_refresh(CONFIG_PATH, token)
        assert CONFIG_PATH not in service._refresh_tasks

    @pytest.mark.asyncio
    async def test_schedules_a_task(self, service: MCPTokenRefreshService) -> None:
        token = OAuthTokens(access_token="tok", expires_in=3600)
        await service.schedule_token_refresh(CONFIG_PATH, token)
        assert CONFIG_PATH in service._refresh_tasks
        service.cancel_refresh_task(CONFIG_PATH)

    @pytest.mark.asyncio
    async def test_does_not_reschedule_within_cooldown_when_refresh_needed_immediately(
        self, service: MCPTokenRefreshService,
    ) -> None:
        """Cooldown only guards the delay<=0 branch (repeated immediate-refresh attempts
        on an already-/near-expired token) — see
        test_reschedules_immediately_after_refresh_despite_cooldown for the branch it must
        NOT gate."""
        import time

        service._last_refresh_time[CONFIG_PATH] = time.time()
        token = OAuthTokens(
            access_token="tok", expires_in=3600,
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),  # already expired -> delay <= 0
        )
        await service.schedule_token_refresh(CONFIG_PATH, token)
        assert CONFIG_PATH not in service._refresh_tasks

    @pytest.mark.asyncio
    async def test_reschedules_immediately_after_refresh_despite_cooldown(
        self, service: MCPTokenRefreshService,
    ) -> None:
        """`_refresh_credential` sets `_last_refresh_time` to "now" and then immediately
        calls `schedule_token_refresh` with the newly-refreshed token to schedule the NEXT
        refresh — that call must not be swallowed by the cooldown just because it's racing
        the timestamp it itself just set, or recovery falls back to the slow 5-minute
        periodic sweep."""
        import time

        service._last_refresh_time[CONFIG_PATH] = time.time()
        token = OAuthTokens(access_token="tok", expires_in=3600)  # freshly issued, real future expiry
        await service.schedule_token_refresh(CONFIG_PATH, token)
        assert CONFIG_PATH in service._refresh_tasks
        service.cancel_refresh_task(CONFIG_PATH)


class TestCancelRefreshTasks:
    @pytest.mark.asyncio
    async def test_cancel_refresh_task_removes_entry(self, service: MCPTokenRefreshService) -> None:
        token = OAuthTokens(access_token="tok", expires_in=3600)
        await service.schedule_token_refresh(CONFIG_PATH, token)
        assert CONFIG_PATH in service._refresh_tasks
        service.cancel_refresh_task(CONFIG_PATH)
        assert CONFIG_PATH not in service._refresh_tasks

    def test_cancel_refresh_task_missing_is_noop(self, service: MCPTokenRefreshService) -> None:
        service.cancel_refresh_task("/does/not/exist")  # should not raise

    @pytest.mark.asyncio
    async def test_cancel_refresh_tasks_for_instance(self, service: MCPTokenRefreshService) -> None:
        token = OAuthTokens(access_token="tok", expires_in=3600)
        await service.schedule_token_refresh("/services/mcp/credentials/inst-1/user-1", token)
        await service.schedule_token_refresh("/services/mcp/credentials/inst-1/user-2", token)
        await service.schedule_token_refresh("/services/mcp/credentials/inst-2/user-1", token)

        cancelled_count = service.cancel_refresh_tasks_for_instance("inst-1")

        assert cancelled_count == 2
        assert "/services/mcp/credentials/inst-2/user-1" in service._refresh_tasks
        service.cancel_refresh_task("/services/mcp/credentials/inst-2/user-1")


class TestStartStop:
    @pytest.mark.asyncio
    async def test_start_and_stop(self, service: MCPTokenRefreshService, mock_config_service: MagicMock) -> None:
        mock_config_service.list_keys_in_directory = AsyncMock(return_value=[])
        await service.start(wait_for_initial_refresh=True)
        assert service._running is True
        await service.stop()
        assert service._running is False

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self, service: MCPTokenRefreshService, mock_config_service: MagicMock) -> None:
        mock_config_service.list_keys_in_directory = AsyncMock(return_value=[])
        await service.start(wait_for_initial_refresh=True)
        await service.start(wait_for_initial_refresh=True)
        await service.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_background_loop_tasks(
        self, service: MCPTokenRefreshService, mock_config_service: MagicMock,
    ) -> None:
        """The periodic-check/state-sweep loops (and a backgrounded initial scan) must be
        cancelled directly by stop() — merely flipping `_running` would leave them running
        until their current `asyncio.sleep()` elapses, able to schedule new refreshes after
        shutdown."""
        mock_config_service.list_keys_in_directory = AsyncMock(return_value=[])
        await service.start(wait_for_initial_refresh=False)
        tasks = list(service._background_tasks)
        assert len(tasks) == 3  # initial scan + periodic refresh check + periodic state sweep

        await service.stop()

        assert service._background_tasks == []
        await asyncio.sleep(0)  # let cancellation propagate
        assert all(task.cancelled() or task.done() for task in tasks)


class TestRefreshAllTokensInternal:
    @pytest.mark.asyncio
    async def test_list_keys_failure_is_swallowed(
        self, service: MCPTokenRefreshService, mock_config_service: MagicMock,
    ) -> None:
        mock_config_service.list_keys_in_directory = AsyncMock(side_effect=RuntimeError("etcd down"))
        await service._refresh_all_tokens_internal()  # no raise

    @pytest.mark.asyncio
    async def test_skips_nested_credential_subkeys_and_refreshes_leaf(
        self, service: MCPTokenRefreshService, mock_config_service: MagicMock,
    ) -> None:
        mock_config_service.list_keys_in_directory = AsyncMock(
            return_value=[
                "/services/mcp/credentials/inst-1/user-1/dcr-client",  # nested — skip
                CONFIG_PATH,
            ]
        )
        mock_config_service.get_config = AsyncMock(
            return_value={"isAuthenticated": True, "oauthTokens": _oauth_token_dict()}
        )

        with patch.object(service, "_refresh_credential", new=AsyncMock()) as refresh:
            await service._refresh_all_tokens_internal()

        refresh.assert_awaited_once_with(CONFIG_PATH)

    @pytest.mark.asyncio
    async def test_per_credential_errors_do_not_abort_scan(
        self, service: MCPTokenRefreshService, mock_config_service: MagicMock,
    ) -> None:
        mock_config_service.list_keys_in_directory = AsyncMock(
            return_value=[CONFIG_PATH, "/services/mcp/credentials/inst-2/user-1"]
        )
        mock_config_service.get_config = AsyncMock(
            return_value={"isAuthenticated": True, "oauthTokens": _oauth_token_dict()}
        )

        async def _boom(path: str) -> None:
            if path == CONFIG_PATH:
                raise RuntimeError("refresh failed")

        with patch.object(service, "_refresh_credential", new=AsyncMock(side_effect=_boom)) as refresh:
            await service._refresh_all_tokens_internal()

        assert refresh.await_count == 2


class TestLoadTokenParseFailure:
    @pytest.mark.asyncio
    async def test_invalid_oauth_token_shape_returns_false(
        self, service: MCPTokenRefreshService, mock_config_service: MagicMock,
    ) -> None:
        mock_config_service.get_config = AsyncMock(
            return_value={
                "isAuthenticated": True,
                "oauthTokens": {"refreshToken": "rt"},  # missing required accessToken
            }
        )
        token, has_oauth = await service._load_token_from_config(CONFIG_PATH)
        assert has_oauth is False
        assert token is None


class TestRefreshCredential:
    @pytest.mark.asyncio
    async def test_immediate_refresh_then_reschedule(
        self, service: MCPTokenRefreshService, mock_config_service: MagicMock,
    ) -> None:
        expired = _oauth_token_dict(expires_in=3600)
        expired["createdAt"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        mock_config_service.get_config = AsyncMock(
            return_value={"isAuthenticated": True, "oauthTokens": expired}
        )
        new_token = OAuthTokens(access_token="new", refresh_token="nr", expires_in=3600)

        with (
            patch.object(service, "_perform_token_refresh", new=AsyncMock(return_value=new_token)) as perform,
            patch.object(service, "schedule_token_refresh", new=AsyncMock()) as schedule,
        ):
            await service._refresh_credential(CONFIG_PATH)

        perform.assert_awaited_once()
        schedule.assert_awaited_once_with(CONFIG_PATH, new_token)

    @pytest.mark.asyncio
    async def test_future_expiry_schedules_without_refreshing(
        self, service: MCPTokenRefreshService, mock_config_service: MagicMock,
    ) -> None:
        mock_config_service.get_config = AsyncMock(
            return_value={"isAuthenticated": True, "oauthTokens": _oauth_token_dict(expires_in=7200)}
        )

        with (
            patch.object(service, "_perform_token_refresh", new=AsyncMock()) as perform,
            patch.object(service, "schedule_token_refresh", new=AsyncMock()) as schedule,
        ):
            await service._refresh_credential(CONFIG_PATH)

        perform.assert_not_awaited()
        schedule.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_invalid_refresh_token_is_handled(
        self, service: MCPTokenRefreshService, mock_config_service: MagicMock,
    ) -> None:
        expired = _oauth_token_dict(expires_in=3600)
        expired["createdAt"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        mock_config_service.get_config = AsyncMock(
            return_value={"isAuthenticated": True, "oauthTokens": expired}
        )

        with (
            patch.object(
                service,
                "_perform_token_refresh",
                new=AsyncMock(side_effect=oauth_client_module.MCPRefreshTokenInvalidError("invalid_grant")),
            ),
            patch.object(service, "_handle_refresh_token_invalid", new=AsyncMock()) as handle,
        ):
            await service._refresh_credential(CONFIG_PATH)

        handle.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_when_credential_no_longer_oauth(
        self, service: MCPTokenRefreshService, mock_config_service: MagicMock,
    ) -> None:
        mock_config_service.get_config = AsyncMock(return_value={"isAuthenticated": False})
        with patch.object(service, "_perform_token_refresh", new=AsyncMock()) as perform:
            await service._refresh_credential(CONFIG_PATH)
        perform.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_lock_timeout_aborts(
        self, service: MCPTokenRefreshService, mock_config_service: MagicMock,
    ) -> None:
        lock = asyncio.Lock()
        await lock.acquire()
        service._credential_locks[CONFIG_PATH] = lock

        with patch(
            "app.connectors.core.base.token_service.mcp_token_refresh_service.LOCK_TIMEOUT_SECONDS",
            0.01,
        ):
            await service._refresh_credential(CONFIG_PATH)

        lock.release()
        mock_config_service.get_config.assert_not_awaited()


class TestHandleRefreshTokenInvalidPersistenceError:
    @pytest.mark.asyncio
    async def test_set_config_failure_is_logged_not_raised(
        self, service: MCPTokenRefreshService, mock_config_service: MagicMock,
    ) -> None:
        error = oauth_client_module.MCPRefreshTokenInvalidError("invalid_grant")
        mock_config_service.get_config = AsyncMock(return_value={"isAuthenticated": True})
        mock_config_service.set_config = AsyncMock(side_effect=RuntimeError("write failed"))

        for _ in range(MAX_REFRESH_TOKEN_INVALID_FAILURES):
            await service._handle_refresh_token_invalid(CONFIG_PATH, error)

        mock_config_service.set_config.assert_awaited()


class TestDelayedRefreshAndPeriodicLoops:
    @pytest.mark.asyncio
    async def test_delayed_refresh_runs_and_clears_task_slot(
        self, service: MCPTokenRefreshService,
    ) -> None:
        with patch.object(service, "_refresh_credential", new=AsyncMock()) as refresh:
            task = asyncio.create_task(service._delayed_refresh(CONFIG_PATH, 0.01))
            service._refresh_tasks[CONFIG_PATH] = task
            await task

        refresh.assert_awaited_once_with(CONFIG_PATH)
        assert CONFIG_PATH not in service._refresh_tasks

    @pytest.mark.asyncio
    async def test_delayed_refresh_swallows_non_cancel_errors(
        self, service: MCPTokenRefreshService,
    ) -> None:
        with patch.object(
            service, "_refresh_credential", new=AsyncMock(side_effect=RuntimeError("boom"))
        ):
            task = asyncio.create_task(service._delayed_refresh(CONFIG_PATH, 0))
            service._refresh_tasks[CONFIG_PATH] = task
            await task  # no raise
        assert CONFIG_PATH not in service._refresh_tasks

    @pytest.mark.asyncio
    async def test_periodic_refresh_check_exits_on_cancel(
        self, service: MCPTokenRefreshService,
    ) -> None:
        service._running = True

        async def _sleep(_seconds: float) -> None:
            raise asyncio.CancelledError

        with (
            patch(
                "app.connectors.core.base.token_service.mcp_token_refresh_service.asyncio.sleep",
                new=_sleep,
            ),
            patch.object(service, "_refresh_all_tokens", new=AsyncMock()) as refresh_all,
        ):
            await service._periodic_refresh_check()

        refresh_all.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_periodic_state_sweep_deletes_expired(
        self, service: MCPTokenRefreshService, mock_config_service: MagicMock,
    ) -> None:
        service._running = True
        mock_config_service.list_keys_in_directory = AsyncMock(return_value=["/services/mcp/oauth-states/a"])
        mock_config_service.get_config = AsyncMock(return_value={"expiresAt": 1})
        mock_config_service.delete_config = AsyncMock()

        sleep_calls = 0

        async def _sleep(_seconds: float) -> None:
            nonlocal sleep_calls
            sleep_calls += 1
            if sleep_calls == 1:
                return
            service._running = False

        with patch(
            "app.connectors.core.base.token_service.mcp_token_refresh_service.asyncio.sleep",
            new=_sleep,
        ):
            await service._periodic_state_sweep()

        mock_config_service.delete_config.assert_awaited_once_with("/services/mcp/oauth-states/a")

    @pytest.mark.asyncio
    async def test_schedule_replaces_completed_task(
        self, service: MCPTokenRefreshService,
    ) -> None:
        done = asyncio.create_task(asyncio.sleep(0))
        await done
        service._refresh_tasks[CONFIG_PATH] = done
        token = OAuthTokens(access_token="tok", expires_in=3600)
        await service.schedule_token_refresh(CONFIG_PATH, token)
        assert CONFIG_PATH in service._refresh_tasks
        assert service._refresh_tasks[CONFIG_PATH] is not done
        service.cancel_refresh_task(CONFIG_PATH)

    @pytest.mark.asyncio
    async def test_schedule_skips_when_live_task_already_pending(
        self, service: MCPTokenRefreshService,
    ) -> None:
        pending = asyncio.create_task(asyncio.sleep(60))
        service._refresh_tasks[CONFIG_PATH] = pending
        token = OAuthTokens(access_token="tok", expires_in=3600)
        await service.schedule_token_refresh(CONFIG_PATH, token)
        assert service._refresh_tasks[CONFIG_PATH] is pending
        pending.cancel()
        try:
            await pending
        except asyncio.CancelledError:
            pass
        service._refresh_tasks.pop(CONFIG_PATH, None)
