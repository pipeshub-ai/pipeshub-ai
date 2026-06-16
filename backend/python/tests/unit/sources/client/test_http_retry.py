"""Tests for http_retry utility."""

import asyncio
from logging import Logger
from unittest.mock import MagicMock

import httpx
import pytest

from app.sources.client.http.http_response import HTTPResponse
from app.sources.client.http.http_retry import call_with_retry


@pytest.fixture
def mock_logger():
    """Create a mock logger for tests."""
    logger = MagicMock(spec=Logger)
    return logger


@pytest.mark.asyncio
async def test_succeeds_on_second_attempt_after_remote_protocol_error(mock_logger):
    """Test that call_with_retry succeeds on 2nd attempt after RemoteProtocolError."""
    call_count = 0

    async def failing_op():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.RemoteProtocolError("Connection reset by peer")
        return "success"

    result = await call_with_retry(
        failing_op,
        logger=mock_logger,
        label="test_op",
        max_attempts=3,
    )

    assert result == "success"
    assert call_count == 2
    assert mock_logger.warning.call_count == 1


@pytest.mark.asyncio
async def test_succeeds_on_second_attempt_after_http_503(mock_logger):
    """Test that call_with_retry succeeds on 2nd attempt after HTTPResponse(503)."""
    call_count = 0

    async def failing_op():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Create a mock httpx.Response with status 503
            mock_response = MagicMock(spec=httpx.Response)
            mock_response.status_code = 503
            mock_response.headers = {}
            return HTTPResponse(mock_response)
        # Success on second attempt
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.headers = {}
        return HTTPResponse(mock_response)

    result = await call_with_retry(
        failing_op,
        logger=mock_logger,
        label="test_op",
        max_attempts=3,
    )

    assert result.status == 200
    assert call_count == 2
    assert mock_logger.warning.call_count == 1


@pytest.mark.asyncio
async def test_does_not_retry_on_http_404(mock_logger):
    """Test that call_with_retry does not retry on HTTPResponse(404)."""
    call_count = 0

    async def failing_op():
        nonlocal call_count
        call_count += 1
        # Return 404 which should not be retried
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 404
        mock_response.headers = {}
        return HTTPResponse(mock_response)

    result = await call_with_retry(
        failing_op,
        logger=mock_logger,
        label="test_op",
        max_attempts=3,
    )

    # Should return the 404 response without retry
    assert result.status == 404
    assert call_count == 1
    assert mock_logger.warning.call_count == 0


@pytest.mark.asyncio
async def test_honors_retry_after_on_429(mock_logger):
    """Test that call_with_retry honors Retry-After header on 429."""
    call_count = 0
    start_time = asyncio.get_event_loop().time()

    async def failing_op():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Return 429 with Retry-After header
            mock_response = MagicMock(spec=httpx.Response)
            mock_response.status_code = 429
            mock_response.headers = {"Retry-After": "1"}
            return HTTPResponse(mock_response)
        # Success on second attempt
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.headers = {}
        return HTTPResponse(mock_response)

    result = await call_with_retry(
        failing_op,
        logger=mock_logger,
        label="test_op",
        max_attempts=3,
    )

    end_time = asyncio.get_event_loop().time()
    elapsed = end_time - start_time

    assert result.status == 200
    assert call_count == 2
    # Should have waited at least 1 second (the Retry-After value)
    assert elapsed >= 1.0
    assert mock_logger.warning.call_count == 1


@pytest.mark.asyncio
async def test_succeeds_on_second_attempt_after_http_status_error_503(mock_logger):
    """Test that call_with_retry retries on httpx.HTTPStatusError for 503."""
    call_count = 0

    async def failing_op():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            request = httpx.Request("GET", "https://example.com/file")
            response = httpx.Response(503, request=request)
            raise httpx.HTTPStatusError("Service Unavailable", request=request, response=response)
        return b"ok"

    result = await call_with_retry(
        failing_op,
        logger=mock_logger,
        label="test_op",
        max_attempts=3,
    )

    assert result == b"ok"
    assert call_count == 2
    assert mock_logger.warning.call_count == 1


@pytest.mark.asyncio
async def test_fails_after_max_attempts(mock_logger):
    """Test that call_with_retry raises original exception after max_attempts exhausted."""
    call_count = 0

    async def failing_op():
        nonlocal call_count
        call_count += 1
        raise httpx.RemoteProtocolError("Connection reset by peer")

    with pytest.raises(httpx.RemoteProtocolError, match="Connection reset by peer"):
        await call_with_retry(
            failing_op,
            logger=mock_logger,
            label="test_op",
            max_attempts=3,
        )

    assert call_count == 3
    assert mock_logger.warning.call_count == 2  # Warnings on attempt 1 and 2, not on final attempt
    assert mock_logger.error.call_count == 1  # Error logged on final failure


@pytest.mark.asyncio
async def test_http_response_503_exhausts_retries_raises_http_status_error(mock_logger):
    """Test that HTTPResponse with 503 exhausting retries raises httpx.HTTPStatusError with status 503 and error details."""
    call_count = 0

    async def failing_op():
        nonlocal call_count
        call_count += 1
        # Always return 503 with error message in body
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 503
        mock_response.headers = {}
        mock_response.text = "Service temporarily unavailable - maintenance in progress"
        http_resp = HTTPResponse(mock_response)
        # Make text() callable
        http_resp.text = lambda: "Service temporarily unavailable - maintenance in progress"
        return http_resp

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await call_with_retry(
            failing_op,
            logger=mock_logger,
            label="test_op",
            max_attempts=3,
        )

    # Verify it's HTTPStatusError with status 503
    assert exc_info.value.response.status_code == 503
    assert "HTTP 503 after 3 attempts" in str(exc_info.value)
    # Verify error message from response body is included
    assert "Service temporarily unavailable" in str(exc_info.value)
    assert call_count == 3
    assert mock_logger.warning.call_count == 2
    # Error logged once when building HTTPStatusError detail, once in final handler
    assert mock_logger.error.call_count == 2


@pytest.mark.asyncio
async def test_succeeds_on_second_attempt_after_connect_timeout(mock_logger):
    """Test that call_with_retry retries on httpx.ConnectTimeout (TimeoutException subclass)."""
    call_count = 0

    async def failing_op():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.ConnectTimeout("Connection timed out")
        return "success"

    result = await call_with_retry(
        failing_op,
        logger=mock_logger,
        label="test_op",
        max_attempts=3,
    )

    assert result == "success"
    assert call_count == 2
    assert mock_logger.warning.call_count == 1
