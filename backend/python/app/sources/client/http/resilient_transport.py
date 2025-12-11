"""
Resilient HTTP transport.
Combines rate limiting and retry logic at the transport layer.
"""

import asyncio
import logging
import random
from http import HTTPStatus
from typing import Optional

import httpx
from aiolimiter import AsyncLimiter


class ResilientHTTPTransport(httpx.AsyncHTTPTransport):
    """
    HTTP transport with optional rate limiting and retry logic.

    Key features:
    - Rate limiting is optional (only applied if rate_limiter is provided)
    - Rate limits ONCE per logical request (not per retry attempt)
    - Retries on 429, 5xx, and network errors
    - Respects Retry-After header
    - Uses exponential backoff with full jitter

    This provides a clean, transport-level implementation.
    """

    def __init__(
        self,
        rate_limiter: Optional[AsyncLimiter] = None,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 32.0,
        logger: Optional[logging.Logger] = None,
        **kwargs
    ) -> None:
        # Validate max_retries
        if not isinstance(max_retries, int) or max_retries < 0:
            raise ValueError(f"max_retries must be a non-negative integer, got: {max_retries}")

        # Validate base_delay
        if not isinstance(base_delay, (int, float)) or base_delay < 0:
            raise ValueError(f"base_delay must be a non-negative number, got: {base_delay}")

        # Validate max_delay
        if not isinstance(max_delay, (int, float)) or max_delay < 0:
            raise ValueError(f"max_delay must be a non-negative number, got: {max_delay}")

        # Validate max_delay >= base_delay
        if max_delay < base_delay:
            raise ValueError(f"max_delay ({max_delay}) must be >= base_delay ({base_delay})")

        super().__init__(**kwargs)
        self.rate_limiter = rate_limiter
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.logger = logger or logging.getLogger(__name__)

    def _should_retry(self, response_or_exc, attempt: int) -> bool:
        """
        Check if a request that returned a response should be retried based on its status code.

        Retries on HTTP status codes:
        - 429 (Too Many Requests)
        - 5xx (Server errors: 500-511)

        Note: Network-level errors (timeouts, connection errors) are handled separately
        in `handle_async_request` and trigger retries there.

        Args:
            response_or_exc: An `httpx.Response` or `httpx.HTTPStatusError` instance.
            attempt: Current attempt number (0-indexed).

        Returns:
            True if the request should be retried, False otherwise.
        """
        if attempt >= self.max_retries:
            return False

        # Handle Response objects
        if isinstance(response_or_exc, httpx.Response):
            status_code = response_or_exc.status_code
            return (
                status_code == HTTPStatus.TOO_MANY_REQUESTS or
                HTTPStatus.INTERNAL_SERVER_ERROR <= status_code <= HTTPStatus.NETWORK_AUTHENTICATION_REQUIRED
            )

        # Handle HTTPStatusError exceptions
        if isinstance(response_or_exc, httpx.HTTPStatusError):
            status_code = response_or_exc.response.status_code
            return (
                status_code == HTTPStatus.TOO_MANY_REQUESTS or
                HTTPStatus.INTERNAL_SERVER_ERROR <= status_code <= HTTPStatus.NETWORK_AUTHENTICATION_REQUIRED
            )

        # Network errors are handled separately in handle_async_request
        return False

    def _calculate_delay(self, response_or_exc, attempt: int) -> float:
        """
        Calculate retry delay using exponential backoff with full jitter.
        Prioritizes Retry-After header if present.

        Args:
            response_or_exc: Either httpx.Response, httpx.HTTPStatusError, or None
            attempt: Current attempt number
        """
        # Priority 1: Retry-After header (from Response or HTTPStatusError)
        response = None
        if isinstance(response_or_exc, httpx.Response):
            response = response_or_exc
        elif isinstance(response_or_exc, httpx.HTTPStatusError):
            response = response_or_exc.response

        if response:
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    return float(retry_after)
                except ValueError:
                    pass  # Header is date string, fallback to backoff

        # Priority 2: Exponential backoff with full jitter
        exponential = min(self.max_delay, self.base_delay * (2 ** attempt))
        return random.uniform(0, exponential)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """
        Handle HTTP request with optional rate limiting and retry logic.

        Rate limiting (if configured) happens ONCE per logical request (outside retry loop).
        This prevents token exhaustion during retry storms.
        """
        last_exception = None
        last_response = None

        # Apply rate limiting if configured (ONCE per logical request)
        if self.rate_limiter:
            await self.rate_limiter.acquire()

        # Retry loop
        for attempt in range(self.max_retries + 1):
            try:
                # Execute the HTTP request
                response = await super().handle_async_request(request)

                # Check if successful or non-retryable
                if not self._should_retry(response, attempt):
                    return response

                # Store response for retry
                last_response = response
                delay = self._calculate_delay(response, attempt)

                self.logger.warning(
                    f"HTTP {response.status_code} (Attempt {attempt + 1}/{self.max_retries + 1}). "
                    f"Retrying in {delay:.2f}s..."
                )
                await asyncio.sleep(delay)

            except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError) as e:
                # Network/protocol errors
                last_exception = e

                if attempt >= self.max_retries:
                    break

                delay = self._calculate_delay(None, attempt)
                self.logger.warning(
                    f"Network error: {type(e).__name__} (Attempt {attempt + 1}/{self.max_retries + 1}). "
                    f"Retrying in {delay:.2f}s..."
                )
                await asyncio.sleep(delay)

        # All retries exhausted
        if last_exception:
            self.logger.error(f"Request failed after {self.max_retries + 1} attempts with network error")
            raise last_exception

        if last_response:
            self.logger.error(f"Request failed after {self.max_retries + 1} attempts with HTTP {last_response.status_code}")
            return last_response

        # Should never reach here, but safety fallback
        raise RuntimeError("Request failed with no response or exception captured")
