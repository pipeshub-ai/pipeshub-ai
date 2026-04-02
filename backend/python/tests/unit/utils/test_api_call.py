"""
Tests for make_api_call():
  - Successful JSON response
  - Successful binary response
  - HTTP error status raises
  - Network errors raise
  - Auth header correctness
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from tenacity import RetryError

from app.utils.api_call import make_api_call


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
