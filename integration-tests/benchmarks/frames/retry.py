"""HTTP retry policy shared by every outbound call (PipesHub, MediaWiki)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, TypeVar

import requests
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from benchmarks.frames.errors import TransientHTTPError

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_RETRY_AFTER_S = 120.0
_backoff = wait_exponential_jitter(initial=2, max=60)

F = TypeVar("F", bound=Callable[..., Any])


def retry_after_seconds(resp: requests.Response) -> float | None:
    raw = resp.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return min(max(float(raw), 0.0), _MAX_RETRY_AFTER_S)
    except ValueError:
        return None


def raise_for_transient(resp: requests.Response) -> requests.Response:
    """Raise `TransientHTTPError` for a retryable status; pass others through."""
    if resp.status_code in RETRYABLE_STATUS:
        raise TransientHTTPError(resp.status_code, retry_after_seconds(resp), resp.url or "")
    return resp


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, (TransientHTTPError, requests.ConnectionError, requests.Timeout))


def _wait(state: RetryCallState) -> float:
    exc = state.outcome.exception() if state.outcome else None
    if isinstance(exc, TransientHTTPError) and exc.retry_after:
        return exc.retry_after
    return _backoff(state)


def _log_retry(state: RetryCallState) -> None:
    exc = state.outcome.exception() if state.outcome else None
    logger.warning("retrying after %s (attempt %d)", exc, state.attempt_number)


def http_retry(attempts: int = 6) -> Callable[[F], F]:
    """Retry 429/5xx, connection errors and timeouts, honouring Retry-After.

    Six attempts (~60s of backoff) so a backend restart mid-run is ridden out
    rather than ending the run."""
    return retry(  # type: ignore[return-value]
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(attempts),
        wait=_wait,
        before_sleep=_log_retry,
        reraise=True,
    )
