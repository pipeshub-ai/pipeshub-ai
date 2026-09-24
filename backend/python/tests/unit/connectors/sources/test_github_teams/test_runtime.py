"""Unit tests for github_teams RuntimeHelper.

Covers:
- ds_call: async (httpx-backed) data-source methods, run on the event loop
  under the wall-clock budget; auth failure -> refresh -> retry once.
- _is_auth_error: string-based auth-error detection.
- _apply_access_token_to_clients: token rotation reaches the client wrapper.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.sources.github_teams.runtime import RuntimeHelper
from app.sources.client.github.github import GitHubResponse

from tests.unit.connectors.sources.test_github_teams.conftest import make_mock_connector

pytestmark = pytest.mark.anyio


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


def _make_runtime() -> tuple[MagicMock, RuntimeHelper]:
    c = make_mock_connector()
    c._github_executor = None
    runtime = RuntimeHelper(c)
    return c, runtime


class TestIsAuthError:
    def test_none_response_not_auth_error(self) -> None:
        assert RuntimeHelper._is_auth_error(None) is False

    def test_successful_response_not_auth_error(self) -> None:
        res = GitHubResponse(success=True, data="x")
        assert RuntimeHelper._is_auth_error(res) is False

    def test_401_marker_is_auth_error(self) -> None:
        res = GitHubResponse(success=False, error="401 Bad credentials")
        assert RuntimeHelper._is_auth_error(res) is True

    def test_unrelated_error_not_auth(self) -> None:
        res = GitHubResponse(success=False, error="500 internal server error")
        assert RuntimeHelper._is_auth_error(res) is False


class TestDsCall:
    async def test_ds_call_awaits_method_with_args(self) -> None:
        _c, runtime = _make_runtime()

        async def async_method(x: int) -> GitHubResponse:
            return GitHubResponse(success=True, data=x * 2)

        res = await runtime.ds_call(async_method, 21)
        assert res.success is True
        assert res.data == 42

    async def test_ds_call_auth_retry_then_success(self) -> None:
        _c, runtime = _make_runtime()
        calls = {"n": 0}

        async def flaky_method() -> GitHubResponse:
            calls["n"] += 1
            if calls["n"] == 1:
                return GitHubResponse(success=False, error="401 Unauthorized")
            return GitHubResponse(success=True, data="ok")

        runtime.force_refresh_oauth_token = AsyncMock(return_value=True)
        res = await runtime.ds_call(flaky_method)
        assert res.success is True
        assert res.data == "ok"
        assert calls["n"] == 2
        runtime.force_refresh_oauth_token.assert_awaited_once()

    async def test_ds_call_non_auth_failure_no_retry(self) -> None:
        _c, runtime = _make_runtime()
        runtime.force_refresh_oauth_token = AsyncMock(return_value=True)

        async def failing_method() -> GitHubResponse:
            return GitHubResponse(success=False, error="404 Not Found")

        res = await runtime.ds_call(failing_method)
        assert res.success is False
        runtime.force_refresh_oauth_token.assert_not_awaited()


class TestApplyAccessTokenToClients:
    """A rotated token only needs to reach the client wrapper — the async
    data source reads it live from there on every request."""

    def test_stores_rotated_token_on_client_wrapper(self) -> None:
        c, runtime = _make_runtime()
        internal_client = MagicMock()
        internal_client.get_token.return_value = "old-token"
        c.external_client = MagicMock()
        c.external_client.get_client.return_value = internal_client

        runtime._apply_access_token_to_clients("new-token")

        internal_client.set_token.assert_called_once_with("new-token")

    def test_noop_when_token_unchanged(self) -> None:
        c, runtime = _make_runtime()
        internal_client = MagicMock()
        internal_client.get_token.return_value = "same-token"
        c.external_client = MagicMock()
        c.external_client.get_client.return_value = internal_client

        runtime._apply_access_token_to_clients("same-token")

        internal_client.set_token.assert_not_called()

    def test_noop_when_access_token_empty(self) -> None:
        c, runtime = _make_runtime()
        c.external_client = MagicMock()

        runtime._apply_access_token_to_clients("")

        c.external_client.get_client.assert_not_called()


class TestIsRetryableError:
    def test_none_and_success_are_not_retryable(self) -> None:
        assert RuntimeHelper._is_retryable_error(None) is False
        assert RuntimeHelper._is_retryable_error(GitHubResponse(success=True, data="x")) is False

    def test_429_and_5xx_are_retryable(self) -> None:
        assert RuntimeHelper._is_retryable_error(
            GitHubResponse(success=False, error="rate", status_code=429)
        ) is True
        assert RuntimeHelper._is_retryable_error(
            GitHubResponse(success=False, error="boom", status_code=503)
        ) is True

    def test_404_is_not_retryable(self) -> None:
        assert RuntimeHelper._is_retryable_error(
            GitHubResponse(success=False, error="missing", status_code=404)
        ) is False


class TestIsAuthErrorStatusCode:
    def test_401_status_is_auth_even_without_marker_text(self) -> None:
        res = GitHubResponse(success=False, error="nope", status_code=401)
        assert RuntimeHelper._is_auth_error(res) is True

    def test_non_401_status_is_not_auth(self) -> None:
        res = GitHubResponse(success=False, error="unauthorized", status_code=403)
        assert RuntimeHelper._is_auth_error(res) is False


class TestRefreshTokenIfNeeded:
    async def test_no_client_is_noop(self) -> None:
        c, runtime = _make_runtime()
        c.external_client = None
        c.config_service = AsyncMock()

        await runtime.refresh_token_if_needed()

        c.config_service.get_config.assert_not_awaited()

    async def test_missing_config_returns_early(self) -> None:
        c, runtime = _make_runtime()
        c.external_client = MagicMock()
        c.config_service = AsyncMock()
        c.config_service.get_config = AsyncMock(return_value=None)
        runtime._apply_access_token_to_clients = MagicMock()

        await runtime.refresh_token_if_needed()

        runtime._apply_access_token_to_clients.assert_not_called()

    async def test_api_token_auth_skips_refresh(self) -> None:
        c, runtime = _make_runtime()
        c.external_client = MagicMock()
        c.config_service = AsyncMock()
        c.config_service.get_config = AsyncMock(
            return_value={"auth": {"authType": "API_TOKEN"}, "credentials": {"access_token": "pat"}}
        )
        runtime._apply_access_token_to_clients = MagicMock()

        await runtime.refresh_token_if_needed()

        runtime._apply_access_token_to_clients.assert_not_called()

    async def test_empty_fresh_token_returns_early(self) -> None:
        c, runtime = _make_runtime()
        c.external_client = MagicMock()
        c.config_service = AsyncMock()
        c.config_service.get_config = AsyncMock(
            return_value={"auth": {"authType": "OAUTH"}, "credentials": {"access_token": ""}}
        )
        runtime._apply_access_token_to_clients = MagicMock()

        await runtime.refresh_token_if_needed()

        runtime._apply_access_token_to_clients.assert_not_called()

    async def test_rotated_oauth_token_is_applied(self) -> None:
        c, runtime = _make_runtime()
        c.config_service = AsyncMock()
        c.config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "OAUTH"},
                "credentials": {"access_token": "fresh-token"},
            }
        )
        internal = MagicMock()
        internal.get_token.return_value = "old-token"
        c.external_client = MagicMock()
        c.external_client.get_client.return_value = internal

        await runtime.refresh_token_if_needed()

        internal.set_token.assert_called_once_with("fresh-token")

    async def test_config_error_is_logged_not_raised(self) -> None:
        c, runtime = _make_runtime()
        c.external_client = MagicMock()
        c.config_service = AsyncMock()
        c.config_service.get_config = AsyncMock(side_effect=RuntimeError("etcd down"))

        await runtime.refresh_token_if_needed()

        c.logger.warning.assert_called()


class TestForceRefreshOAuthToken:
    _PATCH = "app.connectors.core.base.token_service.startup_service.startup_service"

    async def test_missing_refresh_service_returns_false(self) -> None:
        c, runtime = _make_runtime()
        with patch(self._PATCH) as mock_ss:
            mock_ss.get_token_refresh_service.return_value = None
            assert await runtime.force_refresh_oauth_token() is False

    async def test_missing_config_returns_false(self) -> None:
        c, runtime = _make_runtime()
        c.config_service = AsyncMock()
        c.config_service.get_config = AsyncMock(return_value=None)
        with patch(self._PATCH) as mock_ss:
            mock_ss.get_token_refresh_service.return_value = MagicMock()
            assert await runtime.force_refresh_oauth_token() is False

    async def test_api_token_returns_false(self) -> None:
        c, runtime = _make_runtime()
        c.config_service = AsyncMock()
        c.config_service.get_config = AsyncMock(
            return_value={"auth": {"authType": "API_TOKEN"}, "credentials": {}}
        )
        with patch(self._PATCH) as mock_ss:
            mock_ss.get_token_refresh_service.return_value = MagicMock()
            assert await runtime.force_refresh_oauth_token() is False

    async def test_missing_refresh_token_returns_false(self) -> None:
        c, runtime = _make_runtime()
        c.config_service = AsyncMock()
        c.config_service.get_config = AsyncMock(
            return_value={"auth": {"authType": "OAUTH"}, "credentials": {}}
        )
        with patch(self._PATCH) as mock_ss:
            mock_ss.get_token_refresh_service.return_value = MagicMock()
            assert await runtime.force_refresh_oauth_token() is False

    async def test_successful_refresh_syncs_client(self) -> None:
        c, runtime = _make_runtime()
        c.connector_name = SimpleNamespace(value="GITHUB TEAMS")
        c.config_service = AsyncMock()
        c.config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "OAUTH"},
                "credentials": {"refresh_token": "rt"},
            }
        )
        runtime.refresh_token_if_needed = AsyncMock()
        refresh = MagicMock()
        refresh.refresh_now = AsyncMock()
        with patch(self._PATCH) as mock_ss:
            mock_ss.get_token_refresh_service.return_value = refresh
            assert await runtime.force_refresh_oauth_token() is True
        refresh.refresh_now.assert_awaited_once()
        runtime.refresh_token_if_needed.assert_awaited_once()

    async def test_refresh_exception_returns_false(self) -> None:
        c, runtime = _make_runtime()
        c.config_service = AsyncMock()
        c.config_service.get_config = AsyncMock(side_effect=RuntimeError("boom"))
        with patch(self._PATCH) as mock_ss:
            mock_ss.get_token_refresh_service.return_value = MagicMock()
            assert await runtime.force_refresh_oauth_token() is False


class TestExecuteGithubOp:
    async def test_timeout_returns_failure_response(self) -> None:
        _c, runtime = _make_runtime()

        async def slow() -> GitHubResponse:
            await asyncio.sleep(10)
            return GitHubResponse(success=True, data="late")

        res = await runtime._execute_github_op(slow, timeout=0.01, op_label="slow")
        assert res.success is False
        assert "timed out" in (res.error or "").lower()

    async def test_unexpected_exception_returns_failure_response(self) -> None:
        _c, runtime = _make_runtime()

        async def boom() -> GitHubResponse:
            raise RuntimeError("sdk exploded")

        res = await runtime._execute_github_op(boom, op_label="boom")
        assert res.success is False
        assert res.error == "sdk exploded"
        assert res.exception_type == "RuntimeError"


class TestDsCallRetries:
    async def test_auth_refresh_failure_returns_original(self) -> None:
        _c, runtime = _make_runtime()
        runtime.force_refresh_oauth_token = AsyncMock(return_value=False)

        async def failing() -> GitHubResponse:
            return GitHubResponse(success=False, error="401 Unauthorized")

        res = await runtime.ds_call(failing)
        assert res.success is False
        runtime.force_refresh_oauth_token.assert_awaited_once()

    async def test_retryable_status_retries_then_succeeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _c, runtime = _make_runtime()
        monkeypatch.setattr("app.connectors.sources.github_teams.runtime.asyncio.sleep", AsyncMock())
        monkeypatch.setattr("app.connectors.sources.github_teams.runtime.random.random", lambda: 0.0)
        calls = {"n": 0}

        async def flaky() -> GitHubResponse:
            calls["n"] += 1
            if calls["n"] < 3:
                return GitHubResponse(success=False, error="busy", status_code=503)
            return GitHubResponse(success=True, data="ok")

        res = await runtime.ds_call(flaky)
        assert res.success is True
        assert calls["n"] == 3

    async def test_retryable_gives_up_after_max_attempts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _c, runtime = _make_runtime()
        monkeypatch.setattr("app.connectors.sources.github_teams.runtime.asyncio.sleep", AsyncMock())
        monkeypatch.setattr("app.connectors.sources.github_teams.runtime.random.random", lambda: 0.0)

        async def always_busy() -> GitHubResponse:
            return GitHubResponse(success=False, error="busy", status_code=429)

        res = await runtime.ds_call(always_busy)
        assert res.success is False
        assert res.status_code == 429


class TestSearchCall:
    async def test_burst_within_window_budget_does_not_sleep(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Back-to-back calls are allowed — the budget is per minute, and a
        fixed inter-call gap added dead latency to every picker keystroke."""
        _c, runtime = _make_runtime()
        slept: list[float] = []

        async def fake_sleep(delay: float) -> None:
            slept.append(delay)

        monkeypatch.setattr("app.connectors.sources.github_teams.runtime.asyncio.sleep", fake_sleep)

        async def search_method() -> GitHubResponse:
            return GitHubResponse(success=True, data=[])

        for _ in range(2):
            res = await runtime.search_call(search_method)
            assert res.success is True
        assert slept == []
        assert len(runtime._search_call_times) == 2

    async def test_paces_when_window_budget_is_spent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.connectors.sources.github_teams.constants import (
            GITHUB_SEARCH_WINDOW_BUDGET,
        )

        _c, runtime = _make_runtime()
        slept: list[float] = []

        async def fake_sleep(delay: float) -> None:
            slept.append(delay)

        monkeypatch.setattr("app.connectors.sources.github_teams.runtime.asyncio.sleep", fake_sleep)
        now = time.monotonic()
        runtime._search_call_times.extend(
            now - i * 0.001 for i in reversed(range(GITHUB_SEARCH_WINDOW_BUDGET))
        )

        async def search_method() -> GitHubResponse:
            return GitHubResponse(success=True, data=[])

        res = await runtime.search_call(search_method)
        assert res.success is True
        assert slept and slept[0] > 0

    async def test_old_calls_age_out_of_the_window(self) -> None:
        from app.connectors.sources.github_teams.constants import (
            GITHUB_SEARCH_WINDOW_BUDGET,
            GITHUB_SEARCH_WINDOW_SECONDS,
        )

        _c, runtime = _make_runtime()
        stale = time.monotonic() - GITHUB_SEARCH_WINDOW_SECONDS - 1
        runtime._search_call_times.extend([stale] * GITHUB_SEARCH_WINDOW_BUDGET)

        async def search_method() -> GitHubResponse:
            return GitHubResponse(success=True, data=[])

        res = await runtime.search_call(search_method)
        assert res.success is True
        # Stale entries purged; only this call remains in the window.
        assert len(runtime._search_call_times) == 1
