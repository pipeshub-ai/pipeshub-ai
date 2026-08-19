"""HTTP client for the local embedding server with retries."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, TypeVar

import openai
from langchain_core.embeddings import Embeddings
from langchain_openai.embeddings import OpenAIEmbeddings

from app.config.constants.ai_models import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_EMBEDDING_SERVER_URL,
    EMBEDDING_SERVER_MAX_RETRIES,
    EMBEDDING_SERVER_REQUEST_TIMEOUT_SECONDS,
)
from app.services.base_client import parse_retry_after
from app.services.messaging.backpressure import get_default_backpressure_coordinator
from app.utils.logger import create_logger

if TYPE_CHECKING:
    from app.services.messaging.backpressure import BackpressureCoordinator

logger = create_logger("embedding_server_client")

_EMBEDDING_SERVER_API_KEY = "not-needed"
_EMBEDDING_SERVER_SERVICE_NAME = "EmbeddingServer"
T = TypeVar("T")


def _embedding_server_base_url() -> str:
    base = os.getenv("EMBEDDING_SERVER_URL", DEFAULT_EMBEDDING_SERVER_URL).rstrip("/")
    if not base.endswith("/v1"):
        return f"{base}/v1"
    return base


def _embedding_server_max_retries() -> int:
    raw = os.getenv("EMBEDDING_SERVER_MAX_RETRIES")
    if raw is None:
        return EMBEDDING_SERVER_MAX_RETRIES
    try:
        return max(1, int(raw))
    except ValueError:
        logger.warning("Invalid EMBEDDING_SERVER_MAX_RETRIES=%r; using default", raw)
        return EMBEDDING_SERVER_MAX_RETRIES


def _embedding_server_timeout() -> float:
    raw = os.getenv("EMBEDDING_SERVER_TIMEOUT")
    if raw is None:
        return EMBEDDING_SERVER_REQUEST_TIMEOUT_SECONDS
    try:
        return max(1.0, float(raw))
    except ValueError:
        logger.warning("Invalid EMBEDDING_SERVER_TIMEOUT=%r; using default", raw)
        return EMBEDDING_SERVER_REQUEST_TIMEOUT_SECONDS


_RETRIABLE_HTTP_STATUS_CODES = frozenset({429, 502, 503, 504})


def _is_retriable_embedding_error(exc: BaseException) -> bool:
    """Return True only for transient embedding-server failures worth retrying.

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


def _retry_delay_seconds(attempt: int) -> float:
    """Exponential backoff: 2s, 4s, 8s, ... capped at 30s."""
    return min(30.0, 2.0 ** attempt)


def _signal_backpressure_if_rate_limited(
    exc: BaseException,
    *,
    coordinator: "BackpressureCoordinator | None",
) -> None:
    """Propagate a 429's Retry-After to the shared coordinator so the
    indexing consumer can pause new reads, independent of (and regardless
    of the outcome of) this call's own retry budget below — mirrors
    BaseServiceClient._request_with_retry's signal-on-every-occurrence for
    ParsingClient/DoclingClient."""
    if coordinator is None or not isinstance(exc, openai.RateLimitError):
        return
    retry_after = parse_retry_after(exc.response.headers.get("Retry-After"))
    if retry_after is not None:
        coordinator.signal(_EMBEDDING_SERVER_SERVICE_NAME, retry_after)


def _call_with_retry(
    fn: Callable[[], T],
    *,
    max_retries: int,
    operation: str,
    backpressure_coordinator: "BackpressureCoordinator | None" = None,
) -> T:
    last_exc: BaseException | None = None
    total_attempts = max(1, max_retries)
    for attempt in range(1, total_attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            _signal_backpressure_if_rate_limited(exc, coordinator=backpressure_coordinator)
            if not _is_retriable_embedding_error(exc) or attempt >= total_attempts:
                raise
            delay = _retry_delay_seconds(attempt)
            logger.warning(
                "Embedding server %s failed (attempt %d/%d): %s; retrying in %.1fs",
                operation,
                attempt,
                total_attempts,
                exc,
                delay,
            )
            time.sleep(delay)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"Embedding server {operation} failed without exception")


async def _await_with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    max_retries: int,
    operation: str,
    backpressure_coordinator: "BackpressureCoordinator | None" = None,
) -> T:
    last_exc: BaseException | None = None
    total_attempts = max(1, max_retries)
    for attempt in range(1, total_attempts + 1):
        try:
            return await fn()
        except Exception as exc:
            last_exc = exc
            _signal_backpressure_if_rate_limited(exc, coordinator=backpressure_coordinator)
            if not _is_retriable_embedding_error(exc) or attempt >= total_attempts:
                raise
            delay = _retry_delay_seconds(attempt)
            logger.warning(
                "Embedding server %s failed (attempt %d/%d): %s; retrying in %.1fs",
                operation,
                attempt,
                total_attempts,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"Embedding server {operation} failed without exception")


class EmbeddingServerEmbeddings(Embeddings):
    """LangChain embeddings client for the local embedding server with retries."""

    def __init__(
        self,
        *,
        model: str | None = None,
        max_retries: int | None = None,
        timeout: float | None = None,
        trust_remote_code: bool = False,
        backpressure_coordinator: "BackpressureCoordinator | None" = None,
    ) -> None:
        self.model = model or DEFAULT_EMBEDDING_MODEL
        self.max_retries = max_retries if max_retries is not None else _embedding_server_max_retries()
        self.timeout = timeout if timeout is not None else _embedding_server_timeout()
        self.trust_remote_code = trust_remote_code
        # Falls back to the process-wide default so this shares a pause
        # signal with ParsingClient/DoclingClient in the same indexing
        # worker without every construction site needing to plumb one in.
        self._backpressure_coordinator = backpressure_coordinator or get_default_backpressure_coordinator()
        extra_body = {"trust_remote_code": True} if trust_remote_code else None
        self._inner = OpenAIEmbeddings(
            model=self.model,
            api_key=_EMBEDDING_SERVER_API_KEY,
            base_url=_embedding_server_base_url(),
            check_embedding_ctx_length=False,
            max_retries=0,
            timeout=self.timeout,
            extra_body=extra_body,
        )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return _call_with_retry(
            lambda: self._inner.embed_documents(texts),
            max_retries=self.max_retries,
            operation="embed_documents",
            backpressure_coordinator=self._backpressure_coordinator,
        )

    def embed_query(self, text: str) -> list[float]:
        return _call_with_retry(
            lambda: self._inner.embed_query(text),
            max_retries=self.max_retries,
            operation="embed_query",
            backpressure_coordinator=self._backpressure_coordinator,
        )

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return await _await_with_retry(
            lambda: self._inner.aembed_documents(texts),
            max_retries=self.max_retries,
            operation="aembed_documents",
            backpressure_coordinator=self._backpressure_coordinator,
        )

    async def aembed_query(self, text: str) -> list[float]:
        return await _await_with_retry(
            lambda: self._inner.aembed_query(text),
            max_retries=self.max_retries,
            operation="aembed_query",
            backpressure_coordinator=self._backpressure_coordinator,
        )


def get_embedding_server_embeddings(
    model_name: str | None = None,
    *,
    trust_remote_code: bool = False,
) -> Embeddings:
    return EmbeddingServerEmbeddings(
        model=model_name,
        trust_remote_code=trust_remote_code,
    )
