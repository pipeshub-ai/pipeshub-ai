"""
Unit tests for app.sources.client.http.http_retry

Tests retry logic, exponential backoff, rate limiting, and timeout handling.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.sources.client.http.http_response import HTTPResponse
from app.sources.client.http.http_retry import call_with_retry


# =============================================================================
# Helper for creating mock HTTP responses
# =============================================================================

def mock_http_response(status: int, text: str = "", headers: dict | None = None):
    """Create a mock HTTPResponse."""
    response = MagicMock(spec=HTTPResponse)
    response.status = status
    response.text = MagicMock(return_value=text)
    response.headers = headers or {}
    response.response = MagicMock(spec=httpx.Response)
    response.response.status_code = status
    response.response.text = text
    return response


# =============================================================================
# Success cases
# =============================================================================

class TestSuccessCases:
    @pytest.mark.asyncio
    async def test_immediate_success(self):
        """Operation succeeds on first try."""
        mock_logger = MagicMock()
        
        async def op():
            return "success"
        
        result = await call_with_retry(op, logger=mock_logger, label="test")
        assert result == "success"

    @pytest.mark.asyncio
    async def test_returns_http_response_200(self):
        """HTTPResponse with 200 status is returned."""
        mock_logger = MagicMock()
        response = mock_http_response(200, "OK")
        
        async def op():
            return response
        
        result = await call_with_retry(op, logger=mock_logger)
        assert result.status == 200


# =============================================================================
# Retry on transient failures
# =============================================================================

class TestRetryTransientFailures:
    @pytest.mark.asyncio
    async def test_retry_on_500_then_success(self):
        """Retries 500 error, succeeds on second attempt."""
        mock_logger = MagicMock()
        attempts = []
        
        async def op():
            attempts.append(1)
            if len(attempts) == 1:
                return mock_http_response(500, "Internal Server Error")
            return "success"
        
        result = await call_with_retry(op, logger=mock_logger, max_attempts=3)
        assert result == "success"
        assert len(attempts) == 2

    @pytest.mark.asyncio
    async def test_retry_on_429_rate_limit(self):
        """Retries 429 rate limit."""
        mock_logger = MagicMock()
        attempts = []
        
        async def op():
            attempts.append(1)
            if len(attempts) == 1:
                return mock_http_response(429, "Rate Limited")
            return "success"
        
        result = await call_with_retry(op, logger=mock_logger, max_attempts=3)
        assert result == "success"

    @pytest.mark.asyncio
    async def test_retry_on_503_service_unavailable(self):
        """Retries 503 service unavailable."""
        mock_logger = MagicMock()
        attempts = []
        
        async def op():
            attempts.append(1)
            if len(attempts) < 2:
                return mock_http_response(503, "Service Unavailable")
            return "success"
        
        result = await call_with_retry(op, logger=mock_logger, max_attempts=3)
        assert result == "success"

    @pytest.mark.asyncio
    async def test_retry_on_httpx_timeout(self):
        """Retries httpx.TimeoutException."""
        mock_logger = MagicMock()
        attempts = []
        
        async def op():
            attempts.append(1)
            if len(attempts) == 1:
                raise httpx.TimeoutException("Request timeout")
            return "success"
        
        result = await call_with_retry(op, logger=mock_logger, max_attempts=3)
        assert result == "success"

    @pytest.mark.asyncio
    async def test_retry_on_httpx_connect_error(self):
        """Retries httpx.ConnectError."""
        mock_logger = MagicMock()
        attempts = []
        
        async def op():
            attempts.append(1)
            if len(attempts) == 1:
                raise httpx.ConnectError("Connection refused")
            return "success"
        
        result = await call_with_retry(op, logger=mock_logger, max_attempts=3)
        assert result == "success"


# =============================================================================
# Non-retryable errors
# =============================================================================

class TestNonRetryableErrors:
    @pytest.mark.asyncio
    async def test_no_retry_on_400_bad_request(self):
        """400 bad request is not retried."""
        mock_logger = MagicMock()
        response = mock_http_response(400, "Bad Request")
        
        async def op():
            return response
        
        result = await call_with_retry(op, logger=mock_logger, max_attempts=3)
        assert result.status == 400

    @pytest.mark.asyncio
    async def test_no_retry_on_401_unauthorized(self):
        """401 unauthorized is not retried."""
        mock_logger = MagicMock()
        response = mock_http_response(401, "Unauthorized")
        
        async def op():
            return response
        
        result = await call_with_retry(op, logger=mock_logger)
        assert result.status == 401

    @pytest.mark.asyncio
    async def test_no_retry_on_403_forbidden(self):
        """403 forbidden is not retried."""
        mock_logger = MagicMock()
        response = mock_http_response(403, "Forbidden")
        
        async def op():
            return response
        
        result = await call_with_retry(op, logger=mock_logger)
        assert result.status == 403

    @pytest.mark.asyncio
    async def test_no_retry_on_404_not_found(self):
        """404 not found is not retried."""
        mock_logger = MagicMock()
        response = mock_http_response(404, "Not Found")
        
        async def op():
            return response
        
        result = await call_with_retry(op, logger=mock_logger)
        assert result.status == 404


# =============================================================================
# Exhausted retries
# =============================================================================

class TestExhaustedRetries:
    @pytest.mark.asyncio
    async def test_raises_after_max_attempts_on_500(self):
        """Raises HTTPStatusError after exhausting retries on 500."""
        mock_logger = MagicMock()
        
        async def op():
            return mock_http_response(500, "Persistent Error")
        
        with pytest.raises(httpx.HTTPStatusError):
            await call_with_retry(op, logger=mock_logger, max_attempts=2)

    @pytest.mark.asyncio
    async def test_raises_after_max_attempts_on_timeout(self):
        """Raises TimeoutException after exhausting retries."""
        mock_logger = MagicMock()
        
        async def op():
            raise httpx.TimeoutException("Always timeout")
        
        with pytest.raises(httpx.TimeoutException):
            await call_with_retry(op, logger=mock_logger, max_attempts=2)


# =============================================================================
# Retry-After header handling
# =============================================================================

class TestRetryAfterHeader:
    @pytest.mark.asyncio
    async def test_honors_retry_after_header(self):
        """Honors Retry-After header on 429."""
        mock_logger = MagicMock()
        attempts = []
        
        async def op():
            attempts.append(1)
            if len(attempts) == 1:
                return mock_http_response(429, "Rate Limited", headers={"Retry-After": "1"})
            return "success"
        
        start = asyncio.get_event_loop().time()
        result = await call_with_retry(op, logger=mock_logger, max_attempts=3)
        elapsed = asyncio.get_event_loop().time() - start
        assert result == "success"
        assert elapsed >= 1.0  # Should have waited at least 1 second

    @pytest.mark.asyncio
    async def test_ignores_invalid_retry_after(self):
        """Ignores invalid Retry-After header."""
        mock_logger = MagicMock()
        attempts = []
        
        async def op():
            attempts.append(1)
            if len(attempts) == 1:
                return mock_http_response(429, "Rate Limited", headers={"Retry-After": "invalid"})
            return "success"
        
        result = await call_with_retry(op, logger=mock_logger, max_attempts=3)
        assert result == "success"


# =============================================================================
# Logging
# =============================================================================

class TestLogging:
    @pytest.mark.asyncio
    async def test_logs_retry_attempts(self):
        """Logs warning on retry attempts."""
        mock_logger = MagicMock()
        attempts = []
        
        async def op():
            attempts.append(1)
            if len(attempts) < 2:
                return mock_http_response(500, "Error")
            return "success"
        
        await call_with_retry(op, logger=mock_logger, label="test-op", max_attempts=3)
        assert mock_logger.warning.called

    @pytest.mark.asyncio
    async def test_logs_final_failure(self):
        """Logs error on final failure."""
        mock_logger = MagicMock()
        
        async def op():
            return mock_http_response(500, "Persistent Error")
        
        with pytest.raises(httpx.HTTPStatusError):
            await call_with_retry(op, logger=mock_logger, label="test-op", max_attempts=2)
        assert mock_logger.error.called
