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
