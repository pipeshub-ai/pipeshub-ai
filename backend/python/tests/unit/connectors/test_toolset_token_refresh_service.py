"""Tests for app.connectors.core.base.token_service.toolset_token_refresh_service"""

import asyncio
import time
from collections.abc import AsyncIterator
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.core.base.token_service import (
    oauth_service,
    toolset_token_refresh_service,
)
from app.connectors.core.base.token_service.oauth_service import (
    OAuthToken,
    RefreshTokenInvalidError,
)
from app.connectors.core.base.token_service.toolset_token_refresh_service import (
    MAX_REFRESH_TOKEN_INVALID_FAILURES,
    REFRESH_COOLDOWN,
    ToolsetTokenRefreshService,
)
from tests.support import slack_oauth
from tests.support.refusing_token_endpoint import (
    ENTRA_INVALID_CLIENT,
    RefusingTokenEndpoint,
)
from tests.support.slack_oauth import TOKEN_URL as SLACK_TOKEN_URL
from tests.support.slack_oauth import FakeSlackOAuth

CONFIG_PATH = "/services/toolsets/inst-1/user-1"
SLACK_OAUTH_CONFIG = {
    "clientId": "client-1",
    "clientSecret": "secret-1",
    "authorizeUrl": "https://slack.com/oauth/v2/authorize",
    "tokenUrl": SLACK_TOKEN_URL,
    "redirectUri": "https://pipeshub.example/toolsets/oauth/callback/slack",
    "scopes": [],
    "scopeParameterName": "user_scope",
    "tokenResponsePath": "authed_user",
}


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


def _service_over(
    store: dict, oauth_config: dict, monkeypatch: pytest.MonkeyPatch, *, stub_scheduling: bool = True
) -> ToolsetTokenRefreshService:
    """A service whose config store is `store` and whose toolset signs in with `oauth_config`."""
    async def get_config(_path: str, *_: object, **__: object) -> dict:
        return deepcopy(store)

    async def set_config(_path: str, value: dict) -> bool:
        store.clear()
        store.update(deepcopy(value))
        return True

    config_service = MagicMock()
    config_service.get_config = AsyncMock(side_effect=get_config)
    config_service.set_config = AsyncMock(side_effect=set_config)
    service = ToolsetTokenRefreshService(config_service)
    monkeypatch.setattr(service, "_build_complete_oauth_config", AsyncMock(return_value=oauth_config))
    if stub_scheduling:
        monkeypatch.setattr(service, "schedule_token_refresh", AsyncMock())
    return service


class TestSlackToolsetRotation:
    """A Slack toolset signed in to an app with token rotation, refreshed through Slack's endpoint."""

    @pytest.fixture
    def store(self) -> dict:
        return {
            "isAuthenticated": True,
            "auth": {"type": "OAUTH"},
            "credentials": {
                "access_token": "xoxe.xoxp-1-old",
                "refresh_token": "xoxe-1-old",
                "expires_in": 43200,
                "created_at": (datetime.now() - timedelta(hours=13)).isoformat(),  # noqa: DTZ005 - OAuthToken is naive
            },
        }

    @pytest.fixture
    def slack_service(self, store: dict, monkeypatch: pytest.MonkeyPatch) -> ToolsetTokenRefreshService:
        return _service_over(store, SLACK_OAUTH_CONFIG, monkeypatch)

    @pytest.mark.asyncio
    async def test_an_expired_token_is_refreshed_and_both_new_tokens_saved(
        self, slack_service: ToolsetTokenRefreshService, store: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slack = FakeSlackOAuth("xoxe-1-old")
        monkeypatch.setattr(oauth_service, "ClientSession", slack.client_session)

        await slack_service._refresh_toolset_token(CONFIG_PATH, "slack")

        assert [r["refresh_token"] for r in slack.requests] == ["xoxe-1-old"]
        new_access, new_refresh = slack.issued[0]
        assert (store["credentials"]["access_token"], store["credentials"]["refresh_token"]) == (new_access, new_refresh)

    @pytest.mark.asyncio
    async def test_a_revoked_refresh_token_counts_towards_deactivation(
        self, slack_service: ToolsetTokenRefreshService, store: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slack = FakeSlackOAuth("xoxe-1-issued-after-a-reinstall")
        monkeypatch.setattr(oauth_service, "ClientSession", slack.client_session)

        await slack_service._refresh_toolset_token(CONFIG_PATH, "slack")

        assert len(slack.requests) == 1
        assert slack_service._invalid_refresh_failures[CONFIG_PATH] == 1
        assert store["credentials"]["refresh_token"] == "xoxe-1-old"


class TestEntraRefusingTheClient:
    """Microsoft answers every refresh with invalid_client once the app's client secret expires."""

    @pytest.mark.asyncio
    async def test_the_toolset_is_deauthenticated_after_the_threshold(self, monkeypatch: pytest.MonkeyPatch) -> None:
        store = {
            "isAuthenticated": True,
            "auth": {"type": "OAUTH"},
            "credentials": {
                "access_token": "old-access",
                "refresh_token": "old-refresh",
                "expires_in": 3600,
                "created_at": (datetime.now() - timedelta(hours=2)).isoformat(),  # noqa: DTZ005 - OAuthToken is naive
            },
        }
        service = _service_over(store, {
            "clientId": "client-1",
            "clientSecret": "expired-secret",
            "authorizeUrl": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
            "tokenUrl": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
            "redirectUri": "https://pipeshub.example/toolsets/oauth/callback/outlook",
            "scopes": ["offline_access"],
        }, monkeypatch)
        entra = RefusingTokenEndpoint(400, ENTRA_INVALID_CLIENT)
        monkeypatch.setattr(oauth_service, "ClientSession", entra.client_session)

        for _ in range(MAX_REFRESH_TOKEN_INVALID_FAILURES - 1):
            await service._refresh_toolset_token(CONFIG_PATH, "outlook")
        assert store["isAuthenticated"] is True

        await service._refresh_toolset_token(CONFIG_PATH, "outlook")

        assert len(entra.requests) == MAX_REFRESH_TOKEN_INVALID_FAILURES
        assert store["isAuthenticated"] is False
        assert store["deauthReason"] == "refresh_token_invalid"


SHORT_TTL = 300  # refreshed 60 s before expiry, so the next refresh is due 240 s after issue
NEXT_REFRESH_DELAY = SHORT_TTL - 60


def _due_store(**credentials: object) -> dict:
    """A toolset whose 300 s token is 10 s from expiry, so it is inside its refresh window."""
    return {
        "isAuthenticated": True,
        "auth": {"type": "OAUTH"},
        "credentials": {
            "access_token": "xoxe.xoxp-1-old",
            "refresh_token": "xoxe-1-old",
            "expires_in": SHORT_TTL,
            "created_at": (datetime.now() - timedelta(seconds=SHORT_TTL - 10)).isoformat(),  # noqa: DTZ005
            **credentials,
        },
    }


def _armed_delay(service: ToolsetTokenRefreshService) -> float:
    """The delay of the live refresh task for CONFIG_PATH, read from the `_create_refresh_task` spy."""
    task = service._refresh_tasks.get(CONFIG_PATH)
    assert task is not None and not task.done(), "no live refresh task for the toolset"
    return service._create_refresh_task.call_args.args[2]


async def _fire_scheduled_refresh(service: ToolsetTokenRefreshService) -> asyncio.Task:
    """Run a due timer task the way `_create_refresh_task` tracks one, and wait for it."""
    fired = asyncio.create_task(service._delayed_refresh(CONFIG_PATH, "slack", 0))
    service._refresh_tasks[CONFIG_PATH] = fired
    await fired
    return fired


class TestNextRefreshIsScheduledAfterARefresh:
    """A successful refresh arms a timer for the new token; the cooldown only dedupes."""

    @pytest.fixture
    def store(self) -> dict:
        return _due_store()

    @pytest.fixture
    def slack(self, monkeypatch: pytest.MonkeyPatch) -> FakeSlackOAuth:
        monkeypatch.setattr(slack_oauth, "ACCESS_TOKEN_LIFETIME", SHORT_TTL)
        fake = FakeSlackOAuth("xoxe-1-old")
        monkeypatch.setattr(oauth_service, "ClientSession", fake.client_session)
        return fake

    @pytest.fixture
    async def service(
        self, store: dict, monkeypatch: pytest.MonkeyPatch
    ) -> AsyncIterator[ToolsetTokenRefreshService]:
        service = _service_over(store, SLACK_OAUTH_CONFIG, monkeypatch, stub_scheduling=False)
        service._running = True
        monkeypatch.setattr(service, "_create_refresh_task", MagicMock(wraps=service._create_refresh_task))
        yield service
        await service.stop()

    @pytest.mark.asyncio
    async def test_proactive_refresh_schedules_the_next_one(self, service, slack) -> None:
        await service._refresh_toolset_token(CONFIG_PATH, "slack")

        assert len(slack.issued) == 1
        assert NEXT_REFRESH_DELAY - 5 < _armed_delay(service) <= NEXT_REFRESH_DELAY

    @pytest.mark.asyncio
    async def test_delayed_task_reschedules_itself_after_refreshing(self, service, slack) -> None:
        fired = await _fire_scheduled_refresh(service)

        assert len(slack.issued) == 1
        assert service._refresh_tasks[CONFIG_PATH] is not fired
        assert NEXT_REFRESH_DELAY - 5 < _armed_delay(service) <= NEXT_REFRESH_DELAY

    @pytest.mark.asyncio
    async def test_concurrent_refreshes_refresh_once_and_schedule_once(self, service, slack) -> None:
        """Scan and timer racing on one toolset: the per-toolset lock serialises them."""
        await asyncio.gather(
            service._refresh_toolset_token(CONFIG_PATH, "slack"),
            service._refresh_toolset_token(CONFIG_PATH, "slack"),
        )

        assert len(slack.requests) == 1
        assert service._create_refresh_task.call_count == 1
        assert NEXT_REFRESH_DELAY - 5 < _armed_delay(service) <= NEXT_REFRESH_DELAY

    @pytest.mark.asyncio
    async def test_two_schedule_calls_right_after_a_refresh_create_one_task(self, service) -> None:
        service._last_refresh_time[CONFIG_PATH] = time.time()
        fresh = OAuthToken(access_token="a", refresh_token="r", expires_in=SHORT_TTL)

        await asyncio.gather(
            service.schedule_token_refresh(CONFIG_PATH, "slack", fresh),
            service.schedule_token_refresh(CONFIG_PATH, "slack", fresh),
        )

        assert service._create_refresh_task.call_count == 1
        assert list(service._refresh_tasks) == [CONFIG_PATH]

    @pytest.mark.asyncio
    async def test_scan_with_a_pending_old_task_refreshes_once_and_the_old_task_reschedules(
        self, service, slack
    ) -> None:
        old = asyncio.create_task(service._delayed_refresh(CONFIG_PATH, "slack", 0))
        service._refresh_tasks[CONFIG_PATH] = old

        await service._refresh_toolset_token(CONFIG_PATH, "slack")
        await old

        assert len(slack.requests) == 1
        assert service._refresh_tasks[CONFIG_PATH] is not old
        assert NEXT_REFRESH_DELAY - 5 < _armed_delay(service) <= NEXT_REFRESH_DELAY

    @pytest.mark.asyncio
    async def test_a_failed_refresh_arms_nothing_and_the_next_scan_recovers(
        self, service, slack, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        down = RefusingTokenEndpoint(503, '{"error":"temporarily_unavailable"}')
        monkeypatch.setattr(oauth_service, "ClientSession", down.client_session)
        service._invalid_refresh_failures[CONFIG_PATH] = 1

        await _fire_scheduled_refresh(service)

        assert len(down.requests) == 1
        assert CONFIG_PATH not in service._refresh_tasks
        assert CONFIG_PATH not in service._last_refresh_time
        assert service._invalid_refresh_failures[CONFIG_PATH] == 1

        monkeypatch.setattr(oauth_service, "ClientSession", slack.client_session)
        await service._refresh_toolset_token(CONFIG_PATH, "slack")

        assert len(slack.issued) == 1
        assert NEXT_REFRESH_DELAY - 5 < _armed_delay(service) <= NEXT_REFRESH_DELAY
        assert CONFIG_PATH not in service._invalid_refresh_failures

    @pytest.mark.asyncio
    async def test_a_new_token_already_at_threshold_is_not_refreshed_in_a_loop(
        self, service, slack, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(slack_oauth, "ACCESS_TOKEN_LIFETIME", 1)

        await service._refresh_toolset_token(CONFIG_PATH, "slack")

        assert len(slack.requests) == 1
        assert CONFIG_PATH not in service._refresh_tasks

    @pytest.mark.asyncio
    async def test_a_rejected_refresh_schedules_nothing(self, service, store: dict, monkeypatch) -> None:
        revoked = FakeSlackOAuth("xoxe-1-issued-after-a-reinstall")
        monkeypatch.setattr(oauth_service, "ClientSession", revoked.client_session)

        await service._refresh_toolset_token(CONFIG_PATH, "slack")

        assert service._invalid_refresh_failures[CONFIG_PATH] == 1
        assert CONFIG_PATH not in service._last_refresh_time
        assert CONFIG_PATH not in service._refresh_tasks
        assert store["credentials"]["refresh_token"] == "xoxe-1-old"

    @pytest.mark.asyncio
    async def test_a_new_token_without_expiry_schedules_nothing(
        self, service, slack, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(slack_oauth, "ACCESS_TOKEN_LIFETIME", None)

        await service._refresh_toolset_token(CONFIG_PATH, "slack")

        assert len(slack.issued) == 1
        assert CONFIG_PATH not in service._refresh_tasks

    @pytest.mark.asyncio
    async def test_stored_credentials_without_refresh_token_are_not_refreshed_or_scheduled(
        self, service, slack, store: dict
    ) -> None:
        store["credentials"].pop("refresh_token")

        await service._refresh_toolset_token(CONFIG_PATH, "slack")

        assert slack.requests == []
        assert CONFIG_PATH not in service._refresh_tasks


_REAL_SLEEP = asyncio.sleep


class _SimulatedClock:
    """Wall clock for the service under test; `sleep` advances it instead of waiting."""

    START = datetime(2026, 1, 1)  # noqa: DTZ001 - the service uses naive local times

    def __init__(self) -> None:
        self.elapsed = 0.0

    def time(self) -> float:
        return self.START.timestamp() + self.elapsed

    def now(self) -> datetime:
        return self.START + timedelta(seconds=self.elapsed)

    async def sleep(self, seconds: float) -> None:
        self.elapsed += max(0.0, seconds)
        await _REAL_SLEEP(0)


class _InstantProvider:
    """Stands in for OAuthProvider: every refresh succeeds with a token issued at the simulated now."""

    def __init__(self, clock: _SimulatedClock, expires_in: int) -> None:
        self.clock = clock
        self.expires_in = expires_in
        self.refreshed_at: list[float] = []

    def __call__(self, **_: object) -> "_InstantProvider":
        return self

    async def refresh_access_token(self, refresh_token: str) -> OAuthToken:
        self.refreshed_at.append(self.clock.elapsed)
        return OAuthToken(
            access_token=f"at-{len(self.refreshed_at)}",
            refresh_token=refresh_token,
            expires_in=self.expires_in,
            created_at=self.clock.now(),
        )

    async def close(self) -> None:
        return None


class TestShortLivedTokenRefreshRate:
    """A token whose refresh window is TTL - 1 s is due ~1 s after issue; the cooldown caps the rate."""

    TTL = 30
    HORIZON = 60.0

    @pytest.mark.asyncio
    async def test_a_30s_token_refreshes_once_per_cooldown_and_never_lapses(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clock = _SimulatedClock()
        provider = _InstantProvider(clock, self.TTL)
        monkeypatch.setattr(toolset_token_refresh_service, "time", SimpleNamespace(time=clock.time))
        monkeypatch.setattr(toolset_token_refresh_service, "datetime", SimpleNamespace(now=clock.now))
        monkeypatch.setattr(asyncio, "sleep", clock.sleep)
        monkeypatch.setattr(oauth_service, "OAuthProvider", provider)
        store = _due_store(expires_in=self.TTL, created_at=clock.now().isoformat())
        service = _service_over(store, SLACK_OAUTH_CONFIG, monkeypatch, stub_scheduling=False)
        service._running = True

        try:
            await service._refresh_toolset_token(CONFIG_PATH, "slack")  # the scan arms a timer for t=1 s
            for _ in range(10_000):
                if clock.elapsed >= self.HORIZON:
                    break
                await _REAL_SLEEP(0)
        finally:
            await service.stop()

        refreshes = [t for t in provider.refreshed_at if t < self.HORIZON]
        gaps = [later - earlier for earlier, later in zip(refreshes, refreshes[1:])]
        assert len(refreshes) >= self.HORIZON / REFRESH_COOLDOWN - 1, f"refresh chain stopped: {refreshes}"
        assert all(gap >= REFRESH_COOLDOWN for gap in gaps), f"refreshed faster than the cooldown: {gaps}"
        assert all(gap < self.TTL for gap in gaps), f"token lapsed between refreshes: {gaps}"
