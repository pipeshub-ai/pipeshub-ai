"""Unit tests for github_teams RuntimeHelper.

Covers:
- ds_call: sync (PyGithub-backed) data-source methods, executor dispatch.
- ds_call_async: async (httpx-backed) data-source methods (get_img_bytes,
  get_attachment_files_content) — the bug this connector's runtime fixes
  relative to a naive "just use ds_call everywhere" approach.
- call_with_auth_retry: auth failure -> refresh -> retry once.
- _is_auth_error: string-based auth-error detection.
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
    async def test_ds_call_dispatches_sync_method_via_executor(self) -> None:
        _c, runtime = _make_runtime()

        def sync_method(x: int) -> GitHubResponse:
            return GitHubResponse(success=True, data=x * 2)

        res = await runtime.ds_call(sync_method, 21)
        assert res.success is True
        assert res.data == 42

    async def test_ds_call_on_async_method_raises_runtime_error(self) -> None:
        """A truly-async data-source method must go through ds_call_async, not
        ds_call — calling it via ds_call surfaces as a failure rather than
        silently returning an unawaited coroutine."""
        _c, runtime = _make_runtime()

        async def async_method() -> GitHubResponse:
            return GitHubResponse(success=True, data=b"bytes")

        with pytest.raises(RuntimeError):
            await runtime.ds_call(async_method)

    async def test_ds_call_auth_retry_then_success(self) -> None:
        c, runtime = _make_runtime()
        c.runtime = runtime  # allow force_refresh_oauth_token to call through real logic if needed
        calls = {"n": 0}

        def flaky_method() -> GitHubResponse:
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

        def failing_method() -> GitHubResponse:
            return GitHubResponse(success=False, error="404 Not Found")

        res = await runtime.ds_call(failing_method)
        assert res.success is False
        runtime.force_refresh_oauth_token.assert_not_awaited()


class TestDsCallAsync:
    async def test_ds_call_async_awaits_coroutine_method(self) -> None:
        """The httpx-backed async data-source methods (get_img_bytes,
        get_attachment_files_content) must be invoked through ds_call_async."""
        _c, runtime = _make_runtime()

        async def async_method(url: str) -> GitHubResponse:
            return GitHubResponse(success=True, data=f"content-of-{url}".encode())

        res = await runtime.ds_call_async(async_method, "http://example.com/x")
        assert res.success is True
        assert res.data == b"content-of-http://example.com/x"

    async def test_ds_call_async_auth_retry(self) -> None:
        _c, runtime = _make_runtime()
        calls = {"n": 0}

        async def flaky_async_method() -> GitHubResponse:
            calls["n"] += 1
            if calls["n"] == 1:
                return GitHubResponse(success=False, error="401 unauthorized")
            return GitHubResponse(success=True, data=b"ok")

        runtime.force_refresh_oauth_token = AsyncMock(return_value=True)
        res = await runtime.ds_call_async(flaky_async_method)
        assert res.success is True
        assert calls["n"] == 2
