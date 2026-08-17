"""Unit tests for github_teams RuntimeHelper.

Covers:
- ds_call: async (httpx-backed) data-source methods, run on the event loop
  under the wall-clock budget; auth failure -> refresh -> retry once.
- _is_auth_error: string-based auth-error detection.
- _apply_access_token_to_clients: token rotation reaches the client wrapper.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.sources.github_teams.runtime import RuntimeHelper
from app.sources.client.github.github import GitHubResponse

from .conftest import make_mock_connector

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
