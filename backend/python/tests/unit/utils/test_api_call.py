"""
Tests for make_api_call():
  - Successful JSON response
  - Successful binary response
  - HTTP error status raises
  - Network errors raise
  - Auth header correctness
  - _should_retry() logic
  - ClientPayloadError re-raised without retry
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from tenacity import RetryError

from app.utils.api_call import ApiCallError, _should_retry, make_api_call

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_response(
    status=200, content_type="application/json", json_data=None, binary_data=None, text="error"
):
    """Create a mock aiohttp response."""
    resp = AsyncMock()
    resp.status = status
    resp.headers = {"Content-Type": content_type}
    resp.json = AsyncMock(return_value=json_data)
    resp.read = AsyncMock(return_value=binary_data or b"")
    resp.text = AsyncMock(return_value=text)

    # Make it usable as an async context manager
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _make_mock_session(response):
    """Create a mock aiohttp.ClientSession."""
    session = AsyncMock()
    session.get = MagicMock(return_value=response)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


def _no_retry_call(route, token):
    """Create a version of make_api_call with retries disabled (single attempt)."""
    return make_api_call.retry_with(
        stop=lambda retry_state: True,  # stop after first attempt
    )(route, token)


# ===========================================================================
# Tests
# ===========================================================================


class TestMakeApiCall:
    """Tests for make_api_call function."""

    @pytest.mark.asyncio
    async def test_successful_json_response(self):
        """200 + application/json -> returns {is_json: True, data: ...}."""
        response = _make_mock_response(
            status=200,
            content_type="application/json; charset=utf-8",
            json_data={"key": "value"},
        )
        session = _make_mock_session(response)

        with patch("app.utils.api_call.aiohttp.ClientSession", return_value=session):
            result = await _no_retry_call("http://api/route", "test-token")

        assert result["is_json"] is True
        assert result["data"] == {"key": "value"}

    @pytest.mark.asyncio
    async def test_successful_binary_response(self):
        """200 + non-JSON content type -> returns {is_json: False, data: bytes}."""
        binary_data = b"\x89PNG\r\n"
        response = _make_mock_response(
            status=200,
            content_type="application/octet-stream",
            binary_data=binary_data,
        )
        session = _make_mock_session(response)

        with patch("app.utils.api_call.aiohttp.ClientSession", return_value=session):
            result = await _no_retry_call("http://api/file", "test-token")

        assert result["is_json"] is False
        assert result["data"] == binary_data

    @pytest.mark.asyncio
    async def test_http_error_raises_retry_error(self):
        """Non-200 status should raise RetryError (wrapping the inner Exception)."""
        response = _make_mock_response(
            status=401,
            text="Unauthorized",
        )
        session = _make_mock_session(response)

        with patch("app.utils.api_call.aiohttp.ClientSession", return_value=session):
            with pytest.raises((Exception, RetryError)):
                await _no_retry_call("http://api/route", "bad-token")

    @pytest.mark.asyncio
    async def test_auth_header_included(self):
        """Verify the Authorization header is set correctly."""
        response = _make_mock_response(
            status=200,
            content_type="application/json",
            json_data={},
        )
        session = _make_mock_session(response)

        with patch("app.utils.api_call.aiohttp.ClientSession", return_value=session):
            await _no_retry_call("http://api/route", "my-jwt-token")

        # Verify session.get was called with correct headers
        session.get.assert_called_once()
        call_kwargs = session.get.call_args
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers")
        assert headers["Authorization"] == "Bearer my-jwt-token"
        assert headers["Content-Type"] == "application/json"

    @pytest.mark.asyncio
    async def test_server_error_raises(self):
        """500 status should raise."""
        response = _make_mock_response(
            status=500,
            text="Internal Server Error",
        )
        session = _make_mock_session(response)

        with patch("app.utils.api_call.aiohttp.ClientSession", return_value=session):
            with pytest.raises((Exception, RetryError)):
                await _no_retry_call("http://api/route", "token")

    @pytest.mark.asyncio
    async def test_session_exception_raises(self):
        """Network errors should raise."""
        session = AsyncMock()
        session.__aenter__ = AsyncMock(side_effect=Exception("connection refused"))
        session.__aexit__ = AsyncMock(return_value=False)

        with patch("app.utils.api_call.aiohttp.ClientSession", return_value=session):
            with pytest.raises((Exception, RetryError)):
                await _no_retry_call("http://api/route", "token")

    @pytest.mark.asyncio
    async def test_url_passed_correctly(self):
        """Verify the URL is passed to session.get()."""
        response = _make_mock_response(
            status=200,
            content_type="application/json",
            json_data={"result": True},
        )
        session = _make_mock_session(response)

        with patch("app.utils.api_call.aiohttp.ClientSession", return_value=session):
            await _no_retry_call("http://api/my-endpoint", "token")

        call_args = session.get.call_args
        assert call_args[0][0] == "http://api/my-endpoint"

    @pytest.mark.asyncio
    async def test_client_payload_error_is_reraised_without_retry(self):
        """ClientPayloadError (truncated chunked stream) must propagate immediately
        without being wrapped in ApiCallError or consumed by the retry machinery."""
        payload_error = aiohttp.ClientPayloadError("transfer encoding error")

        response = _make_mock_response(status=200, content_type="application/json")
        # Simulate the server dying mid-stream after headers are committed.
        response.json = AsyncMock(side_effect=payload_error)
        response.read = AsyncMock(side_effect=payload_error)
        # Make __aenter__ succeed (headers delivered) but the read fail.
        response.__aenter__ = AsyncMock(return_value=response)
        response.__aexit__ = AsyncMock(return_value=False)

        session = _make_mock_session(response)

        with patch("app.utils.api_call.aiohttp.ClientSession", return_value=session):
            with pytest.raises(aiohttp.ClientPayloadError):
                await _no_retry_call("http://api/route", "token")


class TestShouldRetry:
    """Unit tests for the _should_retry() predicate (line 38, 49)."""

    def test_client_payload_error_is_not_retried(self):
        """Line 38: ClientPayloadError is a server-side stream truncation;
        retrying it would just reproduce the same broken response."""
        exc = aiohttp.ClientPayloadError("truncated response")
        assert _should_retry(exc) is False

    def test_unknown_exception_type_is_not_retried(self):
        """Line 49: Exceptions that are neither ClientPayloadError nor
        ApiCallError fall through to the final return False."""
        assert _should_retry(ValueError("unexpected")) is False
        assert _should_retry(RuntimeError("boom")) is False
        assert _should_retry(KeyError("missing")) is False

    def test_api_call_error_with_no_status_is_retried(self):
        """Transport-level ApiCallError (no HTTP status) is treated as transient."""
        exc = ApiCallError("connection reset", status_code=None)
        assert _should_retry(exc) is True

    def test_api_call_error_with_retryable_4xx_is_retried(self):
        """429 (Too Many Requests) and 408 (Request Timeout) are retryable."""
        assert _should_retry(ApiCallError("rate limited", status_code=429)) is True
        assert _should_retry(ApiCallError("request timeout", status_code=408)) is True

    def test_api_call_error_with_non_retryable_4xx_is_not_retried(self):
        """Permanent client errors (400, 401, 403, 404) must not be retried."""
        for status in (400, 401, 403, 404, 422):
            assert _should_retry(ApiCallError("client error", status_code=status)) is False, (
                f"Expected _should_retry to return False for status {status}"
            )

    def test_api_call_error_with_5xx_is_retried(self):
        """Server errors are transient and should be retried."""
        assert _should_retry(ApiCallError("server error", status_code=500)) is True
        assert _should_retry(ApiCallError("bad gateway", status_code=502)) is True


class TestRateLimits:
    """A 429 carries how long the server asked us to wait, and one that asks
    for longer than these retries could cover is not retried here."""

    def test_a_short_retry_after_is_retried(self) -> None:
        assert _should_retry(ApiCallError("slow down", status_code=429, retry_after_seconds=5)) is True
        assert _should_retry(ApiCallError("slow down", status_code=429, retry_after_seconds=15)) is True

    def test_a_long_retry_after_is_left_to_the_caller(self) -> None:
        assert _should_retry(ApiCallError("slow down", status_code=429, retry_after_seconds=16)) is False
        assert _should_retry(ApiCallError("slow down", status_code=429, retry_after_seconds=3600)) is False

    def test_retry_after_does_not_change_other_statuses(self) -> None:
        assert _should_retry(ApiCallError("down", status_code=503, retry_after_seconds=3600)) is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("header", "seconds"), [("1800", 1800.0), (None, None), ("soon", None)])
    async def test_the_retry_after_header_is_kept_on_the_error(self, header: str | None, seconds: float | None) -> None:
        response = _make_mock_response(status=429, text="Too many requests to GitHub.")
        if header is not None:
            response.headers["Retry-After"] = header
        session = _make_mock_session(response)

        once = make_api_call.retry_with(stop=lambda retry_state: True, reraise=True)
        with patch("app.utils.api_call.aiohttp.ClientSession", return_value=session):
            with pytest.raises(ApiCallError) as exc_info:
                await once("http://connectors/stream", "token")

        assert exc_info.value.status_code == 429
        assert exc_info.value.retry_after_seconds == seconds

    @pytest.mark.asyncio
    async def test_an_hour_long_rate_limit_is_raised_after_one_attempt(self) -> None:
        response = _make_mock_response(status=429, text="Too many requests to GitHub.")
        response.headers["Retry-After"] = "3600"
        session = _make_mock_session(response)

        with patch("app.utils.api_call.aiohttp.ClientSession", return_value=session):
            with pytest.raises(ApiCallError) as exc_info:
                await make_api_call("http://connectors/stream", "token")

        assert session.get.call_count == 1
        assert exc_info.value.retry_after_seconds == 3600.0
