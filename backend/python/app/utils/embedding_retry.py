"""Shared retry/backoff policy for embedding calls.

One implementation for both places an embedding request is made: the local
embedding server client (``embedding_server_client``) and the indexing
pipeline's per-batch loop (``modules.transformers.vectorstore``). Keeping the
policy here is what lets the indexing side treat a 429 as rate limiting —
signalling the shared ``BackpressureCoordinator`` so the consumer stops
claiming work — instead of letting it expire as an opaque batch timeout.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, TypeVar

import openai

from app.services.base_client import parse_retry_after

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.services.messaging.backpressure import BackpressureCoordinator

logger = logging.getLogger(__name__)

T = TypeVar("T")

_RETRIABLE_HTTP_STATUS_CODES = frozenset({429, 502, 503, 504})

# Backoff ceiling. Retry-After on a 429 is honoured by the BackpressureCoordinator
# (which pauses the whole consumer), not by lengthening this sleep — a single
# batch holding an index permit open for a provider's suggested cool-off would
# stall the pipeline behind it.
_MAX_RETRY_DELAY_SECONDS = 30.0


def is_retriable_embedding_error(exc: BaseException) -> bool:
    """Return True only for transient embedding failures worth retrying.

    Application-level 500 responses (e.g. missing trust_remote_code, bad model
    name) are not retried — they will fail the same way on every attempt.
    """
    if isinstance(
        exc,
        (
            openai.APIConnectionError,
            openai.APITimeoutError,
            openai.RateLimitError,
        ),
    ):
        return True
    if isinstance(exc, openai.APIStatusError):
        return exc.status_code in _RETRIABLE_HTTP_STATUS_CODES
    return False


def retry_delay_seconds(attempt: int) -> float:
    """Exponential backoff: 2s, 4s, 8s, ... capped at 30s."""
    return min(_MAX_RETRY_DELAY_SECONDS, 2.0 ** attempt)


def signal_backpressure_if_rate_limited(
    exc: BaseException,
    *,
    service_name: str,
    coordinator: "BackpressureCoordinator | None",
) -> None:
    """Propagate a 429's Retry-After to the shared coordinator so the
    indexing consumer can pause new reads, independent of (and regardless
    of the outcome of) this call's own retry budget — mirrors
    BaseServiceClient._request_with_retry's signal-on-every-occurrence for
    ParsingClient/DoclingClient."""
    if coordinator is None or not isinstance(exc, openai.RateLimitError):
        return
    retry_after = parse_retry_after(exc.response.headers.get("Retry-After"))
    if retry_after is not None:
        coordinator.signal(service_name, retry_after)


def call_with_retry(
    fn: Callable[[], T],
    *,
    max_retries: int,
    operation: str,
    service_name: str,
    backpressure_coordinator: "BackpressureCoordinator | None" = None,
) -> T:
    last_exc: BaseException | None = None
    total_attempts = max(1, max_retries)
    for attempt in range(1, total_attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            signal_backpressure_if_rate_limited(
                exc, service_name=service_name, coordinator=backpressure_coordinator
            )
            if not is_retriable_embedding_error(exc) or attempt >= total_attempts:
                raise
            delay = retry_delay_seconds(attempt)
            logger.warning(
                "Embedding %s %s failed (attempt %d/%d): %s; retrying in %.1fs",
                service_name,
                operation,
                attempt,
                total_attempts,
                exc,
                delay,
            )
            time.sleep(delay)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"Embedding {service_name} {operation} failed without exception")


async def await_with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    max_retries: int,
    operation: str,
    service_name: str,
    backpressure_coordinator: "BackpressureCoordinator | None" = None,
) -> T:
    last_exc: BaseException | None = None
    total_attempts = max(1, max_retries)
    for attempt in range(1, total_attempts + 1):
        try:
            return await fn()
        except Exception as exc:
            last_exc = exc
            signal_backpressure_if_rate_limited(
                exc, service_name=service_name, coordinator=backpressure_coordinator
            )
            if not is_retriable_embedding_error(exc) or attempt >= total_attempts:
                raise
            delay = retry_delay_seconds(attempt)
            logger.warning(
                "Embedding %s %s failed (attempt %d/%d): %s; retrying in %.1fs",
                service_name,
                operation,
                attempt,
                total_attempts,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"Embedding {service_name} {operation} failed without exception")
