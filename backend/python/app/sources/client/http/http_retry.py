"""
HTTP retry utility for httpx-based clients.

Provides a single entry point for retrying transient HTTP failures with
exponential backoff, targeting httpx transport errors and retryable HTTP
status codes returned as HTTPResponse.
"""

import asyncio
from collections.abc import Awaitable, Callable
from logging import Logger
from typing import TypeVar

import httpx  # type: ignore

from app.sources.client.http.http_response import HTTPResponse


T = TypeVar("T")

# Status codes that should never be retried (client errors indicating permanent failure)
_NON_RETRYABLE_STATUSES = {400, 401, 403, 404}

# Status codes that are worth retrying (transient server errors and rate limits)
_RETRYABLE_STATUSES = {408, 429, 500, 502, 503, 504}


def _is_retryable_status(status_code: int) -> bool:
    """Check if an HTTP status code is retryable."""
    if status_code in _NON_RETRYABLE_STATUSES:
        return False
    return status_code in _RETRYABLE_STATUSES


def _get_retry_after(response: HTTPResponse) -> float | None:
    """Extract Retry-After header value in seconds, if present."""
    retry_after = response.headers.get("Retry-After") or response.headers.get("retry-after")
    if not retry_after:
        return None
    try:
        return float(retry_after)
    except ValueError:
        return None


async def call_with_retry(
    op: Callable[[], Awaitable[T]],
    *,
    logger: Logger,
    label: str = "",
    max_attempts: int = 3,
) -> T:
    """
    Execute an async operation with retry on transient failures.

    Retries httpx transport errors and retryable HTTP status codes (408, 429, 5xx)
    with exponential backoff. Honors Retry-After headers on 429 responses.

    Args:
        op: Async callable that returns the result or HTTPResponse
        logger: Logger for warning messages
        label: Human-readable label for logging (e.g., "content/123")
        max_attempts: Maximum number of attempts (default 3)

    Returns:
        The result from the operation

    Raises:
        httpx.HTTPStatusError: If HTTPResponse with retryable status exhausts retries.
            The error message includes the original response body (up to 500 chars)
            to preserve upstream error details.
        The last exception encountered: If op raises and exhausts retries
    """
    last_exc: Exception | None = None
    prefix = f"[{label}] " if label else ""

    for attempt in range(max_attempts):
        try:
            result = await op()

            # Check if the result is an HTTPResponse with a retryable status
            if isinstance(result, HTTPResponse):
                if _is_retryable_status(result.status):
                    # Treat retryable status as a failure and retry
                    if attempt == max_attempts - 1:
                        # Last attempt - raise httpx.HTTPStatusError preserving original error details
                        error_detail = f"{prefix}HTTP {result.status} after {max_attempts} attempts"
                        
                        # Try to extract error message from response body
                        try:
                            response_text = result.text()
                            if response_text:
                                # Limit to 500 chars to avoid overly long error messages
                                error_detail += f" - {response_text[:500]}"
                        except Exception:
                            pass
                        
                        logger.error("%s", error_detail)
                        
                        if isinstance(result.response, httpx.Response):
                            raise httpx.HTTPStatusError(
                                error_detail,
                                request=result.response.request,
                                response=result.response,
                            )
                        else:
                            # Fallback: create synthetic response
                            request = httpx.Request("GET", label or "internal")
                            response = httpx.Response(result.status, request=request)
                            raise httpx.HTTPStatusError(
                                error_detail,
                                request=request,
                                response=response,
                            )
                    
                    logger.warning(
                        "%sHTTP %s (attempt %s/%s) — retrying",
                        prefix, result.status, attempt + 1, max_attempts,
                    )

                    # Calculate backoff
                    backoff = 0.5 * (2 ** attempt)

                    # Honor Retry-After on 429
                    if result.status == 429:
                        retry_after = _get_retry_after(result)
                        if retry_after is not None:
                            backoff = min(retry_after, 60.0)

                    # Cap backoff at 60 seconds
                    backoff = min(backoff, 60.0)

                    await asyncio.sleep(backoff)
                    continue

            # Success case (either not an HTTPResponse, or HTTPResponse with success status)
            return result

        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if not _is_retryable_status(status):
                raise
            last_exc = e
            if attempt == max_attempts - 1:
                break
            backoff = 0.5 * (2 ** attempt)
            if status == 429:
                retry_after = e.response.headers.get("Retry-After") or e.response.headers.get("retry-after")
                if retry_after:
                    try:
                        backoff = min(float(retry_after), 60.0)
                    except ValueError:
                        pass
            backoff = min(backoff, 60.0)
            logger.warning(
                "%sHTTP %s (attempt %s/%s) — retrying in %.1fs",
                prefix, status, attempt + 1, max_attempts, backoff,
            )
            await asyncio.sleep(backoff)

        except (
            httpx.RemoteProtocolError,
            httpx.ReadError,
            httpx.WriteError,
            httpx.ConnectError,
            httpx.TimeoutException,
        ) as e:
            last_exc = e
            if attempt == max_attempts - 1:
                break

            backoff = 0.5 * (2 ** attempt)
            backoff = min(backoff, 60.0)

            logger.warning(
                "%sTransport error (attempt %s/%s): %s — retrying in %.1fs",
                prefix, attempt + 1, max_attempts, type(e).__name__, backoff,
            )
            await asyncio.sleep(backoff)

    # All attempts exhausted - raise the original exception to preserve type
    if last_exc:
        logger.error(
            "%sFailed after %s attempts: %s",
            prefix, max_attempts, type(last_exc).__name__,
        )
        raise last_exc
    
    raise Exception(f"{prefix}Failed after {max_attempts} attempts with no exception recorded")
