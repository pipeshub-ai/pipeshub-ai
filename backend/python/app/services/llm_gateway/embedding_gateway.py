"""EmbeddingGateway: the one retry layer of a remote embeddings call, and the dimension probe.

Remote embedding providers are paid per request, so their requests follow the same rule as the chat models': the SDK
clients retry nothing (``get_embedding_model(..., sdk_retries=0)``) and the repeats are decided here, by the policy of
``spend_guard.retry_policy``: at most ``MAX_ATTEMPTS`` requests per logical call, a retry-budget token per repeat, and a
provider's Retry-After beyond what a call may wait handed back to the caller as ``EmbeddingRateLimited`` instead of being
slept through. The local embedding server is not paid and keeps its own client-side retry; it does not pass here.

The caller owns the loop (it holds the per-attempt gate, timeout and progress clock) and asks ``decide`` after each failed
attempt. ``probe_dimension`` is the one embeddings call made outside a record: it learns a model's vector size once per
provider key and keeps it in the process.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import threading
from typing import TYPE_CHECKING, TypeVar

import httpx
import openai
import requests

from app.exceptions.indexing_exceptions import EmbeddingError
from app.services.llm_gateway import wire
from app.services.llm_gateway.fleet_breaker import credential_of, provider_pk
from app.services.llm_gateway.gateway import provider_key
from app.services.llm_gateway.wire import parse_retry_after
from app.services.spend_guard.retry_policy import (
    MAX_ATTEMPTS,
    NOT_RETRYABLE,
    RETRY_AFTER_BEYOND_BUDGET,
    RetryBudget,
    RetryDecision,
    RetryKind,
    clamp_retry_after,
    decide_retry,
    process_retry_budget,
)
from app.utils.embedding_retry import is_retriable_embedding_error

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain_core.embeddings import Embeddings

_R = TypeVar("_R")

_TOO_MANY_REQUESTS = 429
_SERVER_ERROR = 500


class EmbeddingRateLimited(EmbeddingError):
    """The provider asked for a longer wait than a call may spend. Nothing was slept; defer the work ``retry_after`` s."""

    rate_limited = True  # see ``provider_rate_limit_wait``

    def __init__(self, message: str, *, retry_after: float) -> None:
        super().__init__(message)
        self.retry_after = clamp_retry_after(retry_after) or 0.0


def _status_of(exc: BaseException) -> int | None:
    """The HTTP status in ``exc`` or its causes: an SDK error's ``status_code`` or ``code``, or a botocore response."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        for name in ("status_code", "code"):
            value = getattr(current, name, None)
            if isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599:
                return value
        response = getattr(current, "response", None)
        if isinstance(response, dict):
            status = (response.get("ResponseMetadata") or {}).get("HTTPStatusCode")
            if isinstance(status, int) and 100 <= status <= 599:
                return status
        # An httpx (or requests) error keeps its status only on the response it carries.
        status = getattr(response, "status_code", None)
        if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599:
            return status
        current = current.__cause__
    return None


def _is_connection_failure(exc: BaseException) -> bool:
    """A refused, reset or unreachable connection (a timeout is the caller's deadline, not this)."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        # Any httpx transport failure (a connect or read timeout, a read error, a closed connection) is the network's, not
        # the request's.
        if isinstance(current, (ConnectionError, httpx.TransportError)):
            return True
        # ``requests`` (Voyage's HTTP client) raises its own hierarchy, which is not the builtin one.
        if isinstance(current, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)):
            return True
        if type(current).__name__ in {"EndpointConnectionError", "ConnectionClosedError"}:  # botocore
            return True
        current = current.__cause__
    return False


def retry_after_of(exc: BaseException) -> float | None:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        response = getattr(current, "response", None)
        headers = getattr(response, "headers", None)
        if headers is None and isinstance(response, dict):
            # botocore keeps them in the parsed error response.
            headers = (response.get("ResponseMetadata") or {}).get("HTTPHeaders")
        if headers is None:
            # Cohere's ApiError keeps them on the exception itself, with no response object.
            own = getattr(current, "headers", None)
            headers = own if hasattr(own, "get") else None
        if headers is not None:
            return parse_retry_after(headers)
        current = current.__cause__
    return None


class EmbeddingGateway:
    def __init__(self, *, retry_budget: RetryBudget | None = None, max_attempts: int = MAX_ATTEMPTS) -> None:
        self._retry_budget = retry_budget if retry_budget is not None else RetryBudget()
        self._max_attempts = max_attempts
        self._dimensions: dict[str, int] = {}
        self._flights: dict[tuple[str, int], asyncio.Lock] = {}
        self._lock = threading.Lock()

    @staticmethod
    def retry_kind(exc: BaseException) -> RetryKind | None:
        """A throttle (429), a transient failure (5xx, a dropped connection, an attempt's own deadline), or None for a
        failure that comes back unchanged however often it is sent."""
        status = _status_of(exc)
        if isinstance(exc, openai.RateLimitError) or status == _TOO_MANY_REQUESTS:
            return RetryKind.THROTTLE
        if is_retriable_embedding_error(exc):
            return RetryKind.TRANSIENT
        if status is not None and status >= _SERVER_ERROR:
            return RetryKind.TRANSIENT
        if status is None and _is_connection_failure(exc):
            return RetryKind.TRANSIENT
        return None

    def decide(self, attempt: int, exc: BaseException, *, slept_s: float = 0.0) -> RetryDecision:
        """After the failed attempt number ``attempt`` (0 for the first): send again, and after how long (full jitter, or
        the provider's Retry-After when longer)? A repeat costs a retry-budget token."""
        return decide_retry(
            attempt=attempt, kind=self.retry_kind(exc), budget=self._retry_budget, retry_after_s=retry_after_of(exc),
            slept_s=slept_s, max_attempts=self._max_attempts,
        )

    def rate_limited(self, exc: BaseException, decision: RetryDecision) -> EmbeddingRateLimited | None:
        """The error to raise when ``decision`` refused another send and the provider's wait should be handed to the caller: its
        Retry-After is beyond the call's allowance, or a throttle that said how long to wait cannot be sent again (its
        attempts or the retry budget are used up)."""
        wait = retry_after_of(exc) or 0.0
        if decision.retry or decision.reason == NOT_RETRYABLE or wait <= 0:
            return None
        if decision.reason != RETRY_AFTER_BEYOND_BUDGET and self.retry_kind(exc) is not RetryKind.THROTTLE:
            return None
        return EmbeddingRateLimited(
            f"the embedding provider asks for {wait:.0f}s, more than a call may wait", retry_after=wait,
        )

    def note_success(self) -> None:
        self._retry_budget.refund()

    @staticmethod
    def _dimension_key(embeddings: Embeddings) -> str | None:
        """What a model's vector size depends on: its class, model, endpoint, credential and requested dimensions. None
        (never cached) for a model that does not name itself, so two different models cannot share a size."""
        key = provider_key(embeddings)
        if ":?" in key:
            return None
        requested = (
            getattr(embeddings, "dimensions", None),
            getattr(embeddings, "output_dimensionality", None),
            # Two Azure deployments can share an endpoint, a credential and a model label and still differ in size.
            getattr(embeddings, "deployment", None),
        )
        return provider_pk(f"{key}|{requested}", credential_of(embeddings))

    async def probe_dimension(self, embeddings: Embeddings) -> int:
        """The vector size of ``embeddings``, learned with one paid ``aembed_query`` per provider key and kept in the
        process (a record's build of the same model does not pay again). The probe repeats under the same retry policy
        as any other call. Raises what the provider raised once the policy gives up."""
        key = self._dimension_key(embeddings)
        if key is None:
            return len(await self.call(lambda: self._query(embeddings)))
        known = self._known_dimension(key)
        if known is not None:
            return known
        # Single flight: callers of one provider key that arrive together wait for the first one's answer (a lock per key
        # and event loop; a lock binds to the loop that uses it).
        lock = self._flight_lock(key)
        async with lock:
            known = self._known_dimension(key)
            if known is not None:
                return known
            size = len(await self.call(lambda: self._query(embeddings)))
            with self._lock:
                self._dimensions[key] = size
            return size

    @staticmethod
    async def _query(embeddings: Embeddings) -> list[float]:
        """One probe request. An embeddings object without an async method (a stand-in) is called in a thread."""
        probe = getattr(embeddings, "aembed_query", None)
        if inspect.iscoroutinefunction(probe):
            return await probe("test")
        return await asyncio.to_thread(embeddings.embed_query, "test")

    def _known_dimension(self, key: str) -> int | None:
        with self._lock:
            return self._dimensions.get(key)

    def _flight_lock(self, key: str) -> asyncio.Lock:
        loop_id = id(asyncio.get_running_loop())
        with self._lock:
            return self._flights.setdefault((key, loop_id), asyncio.Lock())

    async def call(self, make_call: Callable[[], Awaitable[_R]]) -> _R:
        """Run ``make_call`` (one request to a remote embedding provider), repeating a failed one under the retry policy:
        at most ``MAX_ATTEMPTS`` requests, a budget token per repeat, a Retry-After beyond the call's wait allowance
        raised as ``EmbeddingRateLimited``. Whatever the provider raised is re-raised once the policy gives up."""
        slept = 0.0
        attempt = 0
        while True:
            seen, token = wire.open_scope()
            try:
                result = await make_call()
            except asyncio.CancelledError:
                wire.charge_failed_sends(seen.requests)
                raise
            except Exception as exc:
                # What this attempt put on the wire failed: it counts against the stage job's sends, like a model call's.
                wire.charge_failed_sends(seen.requests)
                decision = self.decide(attempt, exc, slept_s=slept)
                if not decision.retry:
                    limited = self.rate_limited(exc, decision)
                    if limited is not None:
                        raise limited from exc
                    raise
                slept += decision.delay_s
                attempt += 1
                await asyncio.sleep(decision.delay_s)
                continue
            finally:
                wire.close_scope(token)
            self.note_success()
            return result


_default_lock = threading.Lock()


@functools.cache
def _build_default() -> EmbeddingGateway:
    return EmbeddingGateway(retry_budget=process_retry_budget())


def get_embedding_gateway() -> EmbeddingGateway:
    """This process's embeddings gateway, sharing the process retry budget with the LLM gateway."""
    with _default_lock:
        return _build_default()

