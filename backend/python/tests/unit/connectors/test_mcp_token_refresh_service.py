"""Unit tests for app.connectors.core.base.token_service.mcp_token_refresh_service."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.mcp import oauth_client as oauth_client_module
from app.agents.mcp.models import OAuthTokens
from app.connectors.core.base.token_service.mcp_token_refresh_service import (
    MAX_REFRESH_TOKEN_INVALID_FAILURES,
    PROACTIVE_REFRESH_WINDOW_SECONDS,
    MCPTokenRefreshService,
)

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
