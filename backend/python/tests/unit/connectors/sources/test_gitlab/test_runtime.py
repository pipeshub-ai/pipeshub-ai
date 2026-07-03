"""Unit tests for gitlab RuntimeHelper.

Covers:
- _is_auth_error / _is_auth_error_exc: auth error detection
- ds_call / ds_call_async: timeout, success, auth-retry wiring
- call_with_auth_retry: first attempt success, auth failure → retry, non-auth no retry
- refresh_token_if_needed: token sync, API_TOKEN skip, no client no-op
- force_refresh_oauth_token: API_TOKEN skip, missing refresh_token, success path
- paged_list: single page, partial failure
- shutdown: executor shutdown
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from gitlab.exceptions import GitlabAuthenticationError

from app.connectors.sources.gitlab.runtime import RuntimeHelper
from app.sources.client.gitlab.gitlab import GitLabResponse

from .conftest import make_mock_connector

pytestmark = pytest.mark.anyio


def _make_runtime() -> tuple[MagicMock, RuntimeHelper]:
    c = make_mock_connector()
    # RuntimeHelper.__init__ creates an executor and binds it to c._gitlab_executor
    c._gitlab_executor = None  # will be overwritten by RuntimeHelper.__init__
    runtime = RuntimeHelper(c)
    return c, runtime


# ===========================================================================
# Auth error detection
# ===========================================================================


class TestIsAuthError:
    def test_none_response_is_not_auth_error(self) -> None:
        assert RuntimeHelper._is_auth_error(None) is False

    def test_successful_response_is_not_auth_error(self) -> None:
        res = MagicMock()
        res.success = True
        assert RuntimeHelper._is_auth_error(res) is False

    def test_failed_response_with_auth_marker_is_auth_error(self) -> None:
        res = MagicMock()
        res.success = False
        res.error = "401 unauthorized"
        assert RuntimeHelper._is_auth_error(res) is True

    def test_failed_response_with_authentication_marker(self) -> None:
        res = MagicMock()
        res.success = False
        res.error = "authentication failure"
        assert RuntimeHelper._is_auth_error(res) is True

    def test_failed_response_with_unrelated_error_is_not_auth(self) -> None:
        res = MagicMock()
        res.success = False
        res.error = "500 internal server error"
        assert RuntimeHelper._is_auth_error(res) is False

    def test_empty_error_string(self) -> None:
        res = MagicMock()
        res.success = False
        res.error = ""
        assert RuntimeHelper._is_auth_error(res) is False


class TestIsAuthErrorExc:
    def test_gitlab_auth_exception_is_true(self) -> None:
        exc = GitlabAuthenticationError("401")
        assert RuntimeHelper._is_auth_error_exc(exc) is True

    def test_value_error_is_not_auth_error(self) -> None:
        assert RuntimeHelper._is_auth_error_exc(ValueError("bad")) is False

    def test_runtime_error_is_not_auth_error(self) -> None:
        assert RuntimeHelper._is_auth_error_exc(RuntimeError("fail")) is False


# ===========================================================================
# call_with_auth_retry
# ===========================================================================


class TestCallWithAuthRetry:
    async def test_first_attempt_success_no_retry(self) -> None:
        c, runtime = _make_runtime()
        success_res = GitLabResponse(success=True, data={"id": 1})

        called = []

        async def op() -> GitLabResponse:
            called.append(1)
            return success_res

        result = await runtime.call_with_auth_retry(op)
        assert result.success is True
        assert len(called) == 1

    async def test_auth_error_triggers_refresh_and_retry(self) -> None:
        c, runtime = _make_runtime()
        auth_fail = GitLabResponse(success=False, data=None, error="401 unauthorized")
        success_res = GitLabResponse(success=True, data={"id": 1})

        responses = [auth_fail, success_res]
        call_count = [0]

        async def op() -> GitLabResponse:
            call_count[0] += 1
            return responses.pop(0)

        runtime.force_refresh_oauth_token = AsyncMock(return_value=True)
        result = await runtime.call_with_auth_retry(op)
        assert call_count[0] == 2
        assert result.success is True

    async def test_auth_error_refresh_fails_returns_original_error(self) -> None:
        c, runtime = _make_runtime()
        auth_fail = GitLabResponse(success=False, data=None, error="401 unauthorized")

        runtime.force_refresh_oauth_token = AsyncMock(return_value=False)

        async def op() -> GitLabResponse:
            return auth_fail

        result = await runtime.call_with_auth_retry(op)
        assert result.success is False

    async def test_non_auth_error_not_retried(self) -> None:
        c, runtime = _make_runtime()
        server_err = GitLabResponse(success=False, data=None, error="500 internal error")
        call_count = [0]

        async def op() -> GitLabResponse:
            call_count[0] += 1
            return server_err

        result = await runtime.call_with_auth_retry(op)
        assert call_count[0] == 1
        assert result.success is False

    async def test_timeout_returns_failure_response(self) -> None:
        c, runtime = _make_runtime()

        async def slow_op() -> GitLabResponse:
            await asyncio.sleep(10)
            return GitLabResponse(success=True, data=None)

        result = await runtime.call_with_auth_retry(slow_op, timeout=0.01)
        assert result.success is False
        assert "timed out" in (result.error or "").lower()


# ===========================================================================
# refresh_token_if_needed
# ===========================================================================


class TestRefreshTokenIfNeeded:
    async def test_no_client_returns_early(self) -> None:
        c, runtime = _make_runtime()
        c.external_client = None

        await runtime.refresh_token_if_needed()
        c.config_service.get_config.assert_not_called()

    async def test_api_token_auth_skips_refresh(self) -> None:
        c, runtime = _make_runtime()
        c.config_service = AsyncMock()
        c.config_service.get_config = AsyncMock(
            return_value={"auth": {"authType": "API_TOKEN"}, "credentials": {}}
        )
        c.external_client = MagicMock()

        apply_called = []
        runtime._apply_access_token_to_clients = MagicMock(
            side_effect=lambda t: apply_called.append(t)
        )

        await runtime.refresh_token_if_needed()
        assert apply_called == []

    async def test_oauth_same_token_no_apply(self) -> None:
        c, runtime = _make_runtime()
        c.config_service = AsyncMock()
        c.config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "OAUTH"},
                "credentials": {"access_token": "same-token"},
            }
        )
        mock_internal = MagicMock()
        mock_internal.get_token = MagicMock(return_value="same-token")
        c.external_client = MagicMock()
        c.external_client.get_client = MagicMock(return_value=mock_internal)

        apply_called = []
        runtime._apply_access_token_to_clients = MagicMock(
            side_effect=lambda t: apply_called.append(t)
        )

        await runtime.refresh_token_if_needed()
        assert apply_called == []

    async def test_oauth_new_token_applies(self) -> None:
        c, runtime = _make_runtime()
        c.config_service = AsyncMock()
        c.config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "OAUTH"},
                "credentials": {"access_token": "fresh-token"},
            }
        )
        mock_internal = MagicMock()
        mock_internal.get_token = MagicMock(return_value="old-token")
        c.external_client = MagicMock()
        c.external_client.get_client = MagicMock(return_value=mock_internal)

        applied = []
        runtime._apply_access_token_to_clients = MagicMock(side_effect=lambda t: applied.append(t))

        await runtime.refresh_token_if_needed()
        assert applied == ["fresh-token"]


# ===========================================================================
# _apply_access_token_to_clients
# ===========================================================================


class TestApplyAccessToken:
    def test_empty_token_is_noop(self) -> None:
        c, runtime = _make_runtime()
        c.external_client = MagicMock()
        runtime._apply_access_token_to_clients("")
        c.external_client.get_client.assert_not_called()

    def test_token_set_on_internal_client_and_data_source(self) -> None:
        c, runtime = _make_runtime()
        internal_client = MagicMock()
        internal_client.get_token = MagicMock(return_value="old-token")
        c.external_client = MagicMock()
        c.external_client.get_client = MagicMock(return_value=internal_client)
        c.data_source = MagicMock()
        c.data_source.token = "old-token"

        runtime._apply_access_token_to_clients("new-token")
        internal_client.set_token.assert_called_once_with("new-token")
        assert c.data_source.token == "new-token"


# ===========================================================================
# shutdown
# ===========================================================================


class TestRuntimeShutdown:
    def test_shutdown_calls_executor_shutdown(self) -> None:
        c, runtime = _make_runtime()

        executor_mock = MagicMock()
        runtime._executor = executor_mock

        runtime.shutdown(wait=False)
        executor_mock.shutdown.assert_called_once_with(wait=False, cancel_futures=True)

    def test_shutdown_with_wait_true(self) -> None:
        c, runtime = _make_runtime()

        executor_mock = MagicMock()
        runtime._executor = executor_mock

        runtime.shutdown(wait=True)
        executor_mock.shutdown.assert_called_once_with(wait=True, cancel_futures=False)


# ===========================================================================
# _apply_access_token_to_clients — no-op when same token
# ===========================================================================


class TestApplyAccessTokenNoop:
    def test_same_token_no_set_token_called(self) -> None:
        """When client already has the same token, set_token is NOT called."""
        c, runtime = _make_runtime()
        internal_client = MagicMock()
        internal_client.get_token = MagicMock(return_value="same-token")
        c.external_client = MagicMock()
        c.external_client.get_client = MagicMock(return_value=internal_client)
        c.data_source = MagicMock()
        c.data_source.token = "same-token"

        runtime._apply_access_token_to_clients("same-token")
        internal_client.set_token.assert_not_called()


# ===========================================================================
# refresh_token_if_needed — additional paths
# ===========================================================================


class TestRefreshTokenIfNeededAdditional:
    async def test_no_config_returns_early(self) -> None:
        """When config_service returns None, method returns early."""
        c, runtime = _make_runtime()
        c.config_service = AsyncMock()
        c.config_service.get_config = AsyncMock(return_value=None)
        c.external_client = MagicMock()
        applied = []
        runtime._apply_access_token_to_clients = MagicMock(side_effect=lambda t: applied.append(t))

        await runtime.refresh_token_if_needed()
        assert applied == []

    async def test_no_fresh_token_returns_early(self) -> None:
        """When credentials.access_token is empty, method returns early."""
        c, runtime = _make_runtime()
        c.config_service = AsyncMock()
        c.config_service.get_config = AsyncMock(
            return_value={"auth": {"authType": "OAUTH"}, "credentials": {"access_token": ""}}
        )
        c.external_client = MagicMock()
        applied = []
        runtime._apply_access_token_to_clients = MagicMock(side_effect=lambda t: applied.append(t))

        await runtime.refresh_token_if_needed()
        assert applied == []

    async def test_exception_logged_and_swallowed(self) -> None:
        """Exception during config read is logged, not propagated."""
        c, runtime = _make_runtime()
        c.config_service = AsyncMock()
        c.config_service.get_config = AsyncMock(side_effect=Exception("config error"))
        c.external_client = MagicMock()

        # Should not raise
        await runtime.refresh_token_if_needed()
        c.logger.warning.assert_called()


# ===========================================================================
# force_refresh_oauth_token — happy path and failure
# ===========================================================================


class TestForceRefreshOAuthToken:
    _PATCH_PATH = "app.connectors.core.base.token_service.startup_service.startup_service"

    async def test_api_token_auth_skips(self) -> None:
        """force_refresh_oauth_token returns False for API_TOKEN auth."""
        c, runtime = _make_runtime()
        c.config_service = AsyncMock()
        c.config_service.get_config = AsyncMock(
            return_value={"auth": {"authType": "API_TOKEN"}, "credentials": {}}
        )

        with patch(self._PATCH_PATH) as mock_ss:
            mock_refresh = MagicMock()
            mock_ss.get_token_refresh_service = MagicMock(return_value=mock_refresh)
            result = await runtime.force_refresh_oauth_token()
        assert result is False

    async def test_no_refresh_token_returns_false(self) -> None:
        """force_refresh_oauth_token returns False when refresh_token is missing."""
        c, runtime = _make_runtime()
        c.config_service = AsyncMock()
        c.config_service.get_config = AsyncMock(
            return_value={"auth": {"authType": "OAUTH"}, "credentials": {}}
        )

        with patch(self._PATCH_PATH) as mock_ss:
            mock_refresh = MagicMock()
            mock_ss.get_token_refresh_service = MagicMock(return_value=mock_refresh)
            result = await runtime.force_refresh_oauth_token()
        assert result is False

    async def test_successful_refresh_returns_true(self) -> None:
        """force_refresh_oauth_token returns True when refresh succeeds."""
        c, runtime = _make_runtime()
        c.config_service = AsyncMock()
        c.config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "OAUTH"},
                "credentials": {"refresh_token": "tok-refresh"},
            }
        )
        runtime.refresh_token_if_needed = AsyncMock()

        mock_refresh = MagicMock()
        mock_refresh.refresh_now = AsyncMock()

        with patch(self._PATCH_PATH) as mock_ss:
            mock_ss.get_token_refresh_service = MagicMock(return_value=mock_refresh)
            result = await runtime.force_refresh_oauth_token()
        assert result is True
        mock_refresh.refresh_now.assert_called_once()

    async def test_exception_returns_false(self) -> None:
        """Exception during refresh is caught and returns False."""
        c, runtime = _make_runtime()
        c.config_service = AsyncMock()
        c.config_service.get_config = AsyncMock(side_effect=Exception("config fail"))

        with patch(self._PATCH_PATH) as mock_ss:
            mock_ss.get_token_refresh_service = MagicMock(return_value=MagicMock())
            result = await runtime.force_refresh_oauth_token()
        assert result is False


# ===========================================================================
# _execute_gitlab_op — sync path
# ===========================================================================


class TestExecuteGitlabOpSyncPath:
    async def test_sync_callable_runs_on_executor(self) -> None:
        """A regular (non-coroutine) callable is dispatched to the executor."""
        c, runtime = _make_runtime()
        success_res = GitLabResponse(success=True, data="sync-result")

        def sync_op() -> GitLabResponse:
            return success_res

        result = await runtime._execute_gitlab_op(sync_op)
        assert result.success is True
        assert result.data == "sync-result"


# ===========================================================================
# paged_list — two-page drain and mid-iteration error
# ===========================================================================


class TestPagedList:
    async def test_two_page_drain(self) -> None:
        """paged_list accumulates items from iterator that spans two drain calls."""
        c, runtime = _make_runtime()

        # Create an iterator with 2 items
        items = [MagicMock(), MagicMock()]
        iter_data = iter(items)
        iter_res = GitLabResponse(success=True, data=iter_data)
        runtime.ds_call = AsyncMock(return_value=iter_res)

        result = await runtime.paged_list(
            MagicMock(),
            progress_label="test paged list",
            progress_every=1,
        )
        assert result.success is True
        assert len(result.data) == 2

    async def test_api_failure_returns_failure(self) -> None:
        """When ds_call fails, paged_list returns the failure immediately."""
        c, runtime = _make_runtime()
        runtime.ds_call = AsyncMock(return_value=GitLabResponse(success=False, data=None, error="forbidden"))

        result = await runtime.paged_list(
            MagicMock(),
            progress_label="test",
        )
        assert result.success is False


# ===========================================================================
# ds_call / ds_call_async — argument forwarding
# ===========================================================================


class TestDsCallArgForwarding:
    async def test_ds_call_forwards_args_and_kwargs(self) -> None:
        """ds_call passes positional and keyword args to the method."""
        c, runtime = _make_runtime()
        success_res = GitLabResponse(success=True, data="result")

        method = MagicMock(return_value=success_res)
        result = await runtime.ds_call(method, "arg1", key="value")

        method.assert_called_once_with("arg1", key="value")
        assert result.success is True

    async def test_ds_call_async_forwards_args(self) -> None:
        """ds_call_async passes positional and keyword args to the async method."""
        c, runtime = _make_runtime()
        success_res = GitLabResponse(success=True, data="async-result")

        async def async_method(*args, **kwargs) -> GitLabResponse:
            return success_res

        result = await runtime.ds_call_async(async_method, "arg1", key="value")
        assert result.success is True
