"""Shared HTTP client base with retry, circuit-breaker, and timeout logic.

All inter-service HTTP clients should inherit from :class:`BaseServiceClient`
to get consistent retry/timeout/error-handling behaviour.

Resilience layers (see the "Indexing / Parsing / Docling Resiliency Plan"):

1. Retry with full-jitter exponential backoff on transient transport errors
   and 429/502/503/504 responses (honors ``Retry-After`` when present).
2. A per-client circuit breaker that fails fast (without making a network
   call) once a service has shown ``failure_threshold`` consecutive
   failures, and probes for recovery after ``circuit_reset_seconds``.
3. A single long-lived ``httpx.AsyncClient`` per instance so connection
   pooling / keep-alive actually take effect across calls.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from enum import Enum
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ── Defaults ────────────────────────────────────────────────────────────────
DEFAULT_CONNECT_TIMEOUT = 30.0      # seconds to establish TCP connection
DEFAULT_READ_TIMEOUT = 300.0        # seconds to wait for server response
DEFAULT_WRITE_TIMEOUT = 60.0        # seconds to finish writing the request
DEFAULT_POOL_TIMEOUT = 30.0         # seconds to acquire a pool connection
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY = 1.0           # seconds; doubled on each retry (exponential), then jittered
DEFAULT_MAX_BACKOFF = 30.0          # seconds; cap on any single retry delay
TRANSIENT_STATUS_CODES = {502, 503, 504, 429}

# Circuit breaker defaults
DEFAULT_CB_FAILURE_THRESHOLD = 5
DEFAULT_CB_RESET_SECONDS = 30.0

# Transport-layer exceptions that are safe to retry: the request may not have
# been fully processed by the remote peer (connection reset, dropped mid
# response, timed out, etc.). Note httpx.RemoteProtocolError and
# httpx.ReadError are httpx.RequestError subclasses that do NOT subclass
# TimeoutException/ConnectError/WriteError, so they must be listed explicitly
# -- a bare `except httpx.RequestError` will also match non-retryable errors
# like InvalidURL, so we enumerate the retryable ones instead of catching the
# whole hierarchy.
RETRYABLE_TRANSPORT_ERRORS = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.WriteError,
    httpx.ReadError,
    httpx.RemoteProtocolError,
)


class ServiceCallError(Exception):
    """Raised when a service call fails after all retries."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        service_name: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.service_name = service_name
        self.details: dict[str, Any] = details or {}


class ServiceUnavailableError(ServiceCallError):
    """Raised when the remote service is unreachable or persistently 5xx."""


class CircuitBreakerOpenError(ServiceUnavailableError):
    """Raised immediately (no network call attempted) while the breaker is open."""


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Simple consecutive-failure circuit breaker for a single downstream service.

    - CLOSED: requests flow normally.
    - OPEN: requests fail immediately (``allow_request`` raises) until
      ``reset_seconds`` have elapsed since the breaker opened.
    - HALF_OPEN: exactly one probe request is allowed through; success closes
      the breaker, failure re-opens it (and restarts the cooldown).
    """

    def __init__(
        self,
        service_name: str,
        failure_threshold: int = DEFAULT_CB_FAILURE_THRESHOLD,
        reset_seconds: float = DEFAULT_CB_RESET_SECONDS,
        logger_: logging.Logger | None = None,
    ) -> None:
        self.service_name = service_name
        self.failure_threshold = failure_threshold
        self.reset_seconds = reset_seconds
        self.logger = logger_ or logger

        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at = 0.0
        self._half_open_probe_in_flight = False
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        return self._state

    def is_blocking(self) -> bool:
        """True while the breaker is OPEN and its cooldown has not yet elapsed.

        Unlike reading ``state`` directly, this accounts for cooldown expiry.
        The OPEN -> HALF_OPEN transition itself only happens inside
        ``allow_request()``, so callers that gate work on breaker state (e.g.
        pre-flight health checks) must use this instead of ``state == OPEN`` —
        otherwise they block the very request that would let the breaker probe
        for recovery, and it stays OPEN forever.
        """
        return (
            self._state == CircuitState.OPEN
            and (time.monotonic() - self._opened_at) < self.reset_seconds
        )

    async def allow_request(self) -> None:
        """Raise :class:`CircuitBreakerOpenError` if the breaker denies this request.

        Transitions OPEN -> HALF_OPEN once the cooldown elapses, admitting a
        single probe request; concurrent callers during HALF_OPEN are denied
        until that probe resolves.
        """
        async with self._lock:
            if self._state == CircuitState.CLOSED:
                return

            if self._state == CircuitState.OPEN:
                elapsed = time.monotonic() - self._opened_at
                if elapsed < self.reset_seconds:
                    raise CircuitBreakerOpenError(
                        f"{self.service_name} circuit breaker is OPEN "
                        f"({elapsed:.0f}s/{self.reset_seconds:.0f}s cooldown remaining)",
                        service_name=self.service_name,
                    )
                # Cooldown elapsed -- allow exactly one half-open probe.
                self._state = CircuitState.HALF_OPEN
                self._half_open_probe_in_flight = True
                self.logger.info(
                    "[%s] Circuit breaker HALF_OPEN — admitting one probe request",
                    self.service_name,
                )
                return

            # HALF_OPEN
            if self._half_open_probe_in_flight:
                raise CircuitBreakerOpenError(
                    f"{self.service_name} circuit breaker is HALF_OPEN "
                    "(probe request already in flight)",
                    service_name=self.service_name,
                )
            self._half_open_probe_in_flight = True

    async def record_success(self) -> None:
        async with self._lock:
            was_open = self._state != CircuitState.CLOSED
            self._state = CircuitState.CLOSED
            self._consecutive_failures = 0
            self._half_open_probe_in_flight = False
            if was_open:
                self.logger.info(
                    "[%s] Circuit breaker CLOSED — service recovered",
                    self.service_name,
                )

    async def record_failure(self) -> None:
        async with self._lock:
            self._half_open_probe_in_flight = False
            self._consecutive_failures += 1

            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()
                self.logger.warning(
                    "[%s] Circuit breaker re-OPENED — half-open probe failed",
                    self.service_name,
                )
                return

            if (
                self._state == CircuitState.CLOSED
                and self._consecutive_failures >= self.failure_threshold
            ):
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()
                self.logger.warning(
                    "[%s] Circuit breaker OPENED after %d consecutive failures",
                    self.service_name, self._consecutive_failures,
                )


class BaseServiceClient:
    """Async HTTP client with retry, circuit-breaker, timeout, and health-check.

    Sub-classes configure *service_url* and may override any timeout / retry
    parameter.  Call :meth:`_post_json` / :meth:`_get_json` / :meth:`_post_multipart`
    for typed requests; all share the retry machinery.
    """

    def __init__(
        self,
        service_url: str,
        service_name: str = "service",
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        read_timeout: float = DEFAULT_READ_TIMEOUT,
        write_timeout: float = DEFAULT_WRITE_TIMEOUT,
        pool_timeout: float = DEFAULT_POOL_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_delay: float = DEFAULT_RETRY_DELAY,
        max_backoff: float = DEFAULT_MAX_BACKOFF,
        circuit_breaker_threshold: int = DEFAULT_CB_FAILURE_THRESHOLD,
        circuit_breaker_reset_seconds: float = DEFAULT_CB_RESET_SECONDS,
    ) -> None:
        self.service_url = service_url.rstrip("/")
        self.service_name = service_name
        self._timeout = httpx.Timeout(
            connect=connect_timeout,
            read=read_timeout,
            write=write_timeout,
            pool=pool_timeout,
        )
        self._limits = httpx.Limits(
            max_keepalive_connections=5,
            max_connections=10,
            keepalive_expiry=30.0,
        )
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.max_backoff = max_backoff
        self.logger = logging.getLogger(f"{__name__}.{service_name}")

        self.circuit_breaker = CircuitBreaker(
            service_name=service_name,
            failure_threshold=circuit_breaker_threshold,
            reset_seconds=circuit_breaker_reset_seconds,
            logger_=self.logger,
        )

        # Long-lived client, created lazily so subclasses can be constructed
        # without an event loop present, and re-created if closed.
        self._client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()

        # Cache for health_check_cached() -- avoids a redundant health probe
        # (and thus a fully wasted large multipart upload downstream) when
        # callers pre-flight-check right before every request.
        self._health_cache: bool | None = None
        self._health_cache_at: float = 0.0
        self._health_cache_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _make_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self._timeout,
            limits=self._limits,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        """Return the shared client, (re)creating it if needed.

        A single client is reused across calls so the connection pool and
        keep-alive settings configured in ``__init__`` actually apply.
        """
        if self._client is None or self._client.is_closed:
            async with self._client_lock:
                if self._client is None or self._client.is_closed:
                    self._client = self._make_client()
        return self._client

    async def close(self) -> None:
        """Close the shared HTTP client. Safe to call multiple times."""
        async with self._client_lock:
            if self._client is not None and not self._client.is_closed:
                await self._client.aclose()
            self._client = None

    def _compute_backoff(self, attempt: int, retry_after: float | None) -> float:
        """Full-jitter exponential backoff, floored by ``Retry-After`` if given."""
        capped = min(self.retry_delay * (2 ** (attempt - 1)), self.max_backoff)
        delay = random.uniform(0, capped)
        if retry_after is not None:
            delay = max(delay, retry_after)
        return delay

    @staticmethod
    def _parse_retry_after(response: httpx.Response) -> float | None:
        raw = response.headers.get("Retry-After")
        if not raw:
            return None
        try:
            return max(0.0, float(raw))
        except ValueError:
            return None

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        json: dict | None = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
        files: dict | None = None,
        data: dict | None = None,
        operation: str = "request",
    ) -> httpx.Response:
        """Execute *method* request with full-jitter exponential-backoff retry.

        Fails fast (no network call) if the circuit breaker is open.
        """
        await self.circuit_breaker.allow_request()

        headers = headers or {}
        last_exc: Exception | None = None
        last_status: int | None = None
        attempts_made = 0
        client = await self._get_client()

        for attempt in range(1, self.max_retries + 1):
            attempts_made = attempt
            retry_after: float | None = None
            try:
                self.logger.info(
                    "[%s] %s %s (attempt %d/%d)",
                    self.service_name, method.upper(), url, attempt, self.max_retries,
                )
                kwargs: dict[str, Any] = {"headers": headers}
                if json is not None:
                    kwargs["json"] = json
                elif content is not None:
                    kwargs["content"] = content
                if files is not None:
                    kwargs["files"] = files
                if data is not None:
                    kwargs["data"] = data

                response = await client.request(method, url, **kwargs)
                last_status = response.status_code

                if response.status_code not in TRANSIENT_STATUS_CODES:
                    await self.circuit_breaker.record_success()
                    self._health_cache = True
                    self._health_cache_at = time.monotonic()
                    return response

                # Transient 5xx / 429 -- retry.
                self.logger.warning(
                    "[%s] %s returned %d on attempt %d",
                    self.service_name, operation, response.status_code, attempt,
                )
                retry_after = self._parse_retry_after(response)

            except RETRYABLE_TRANSPORT_ERRORS as exc:
                self.logger.warning(
                    "[%s] %s transport error on attempt %d: %s",
                    self.service_name, operation, attempt, exc,
                )
                last_exc = exc
            except httpx.RequestError as exc:
                # Non-retryable request construction errors (bad URL,
                # unsupported protocol, etc.) -- retrying can't help.
                self.logger.warning(
                    "[%s] %s non-retryable request error on attempt %d: %s",
                    self.service_name, operation, attempt, exc,
                )
                await self.circuit_breaker.record_failure()
                raise ServiceUnavailableError(
                    f"{self.service_name} {operation} failed after {attempt} "
                    f"attempt(s) (non-retryable): {exc}",
                    service_name=self.service_name,
                ) from exc

            if attempt < self.max_retries:
                delay = self._compute_backoff(attempt, retry_after)
                self.logger.info(
                    "[%s] Retrying in %.1fs (attempt %d/%d)…",
                    self.service_name, delay, attempt + 1, self.max_retries,
                )
                await asyncio.sleep(delay)

        # Retries exhausted.
        await self.circuit_breaker.record_failure()
        if last_exc is not None:
            raise ServiceUnavailableError(
                f"{self.service_name} {operation} failed after {attempts_made} "
                f"attempt(s): {last_exc}",
                service_name=self.service_name,
            ) from last_exc

        raise ServiceCallError(
            f"{self.service_name} {operation} failed after {attempts_made} "
            f"attempt(s) with status {last_status}",
            status_code=last_status,
            service_name=self.service_name,
        )

    # ------------------------------------------------------------------
    # Public helpers for sub-classes
    # ------------------------------------------------------------------

    async def _post_json(
        self,
        path: str,
        payload: dict,
        operation: str = "POST",
        extra_headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        url = f"{self.service_url}{path}"
        body = json.dumps(payload).encode("utf-8")
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if extra_headers:
            headers.update(extra_headers)
        # Starlette does not auto-decompress Content-Encoding: gzip request bodies,
        # so we never compress JSON request payloads here.
        return await self._request_with_retry(
            "POST", url, content=body, headers=headers, operation=operation
        )

    async def _post_multipart(
        self,
        path: str,
        files: dict,
        data: dict,
        operation: str = "POST multipart",
    ) -> httpx.Response:
        url = f"{self.service_url}{path}"
        return await self._request_with_retry(
            "POST", url, files=files, data=data, operation=operation
        )

    async def _get_json(self, path: str, operation: str = "GET") -> httpx.Response:
        url = f"{self.service_url}{path}"
        return await self._request_with_retry("GET", url, operation=operation)

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Return True when the service responds with HTTP 200.

        Bypasses the retry/circuit-breaker machinery entirely -- this is used
        as a cheap, fast probe (including as a pre-flight check before a large
        upload), not a resilient operation in its own right.
        """
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                response = await client.get(f"{self.service_url}/health")
                healthy = response.status_code == 200
                if healthy:
                    self.logger.info("[%s] health check OK", self.service_name)
                else:
                    self.logger.warning(
                        "[%s] health check returned %d", self.service_name, response.status_code
                    )
                return healthy
        except Exception as exc:
            self.logger.error("[%s] health check failed: %s", self.service_name, exc)
            return False

    async def health_check_cached(
        self, ttl: float = 10.0, failure_ttl: float = 1.0
    ) -> bool:
        """Cached wrapper around :meth:`health_check`.

        Successful results use *ttl*. Failures use the shorter *failure_ttl*
        so a service that restarts is detected promptly. A successful health
        probe closes an open circuit breaker because it is the recovery probe;
        otherwise a pre-flight check can keep rejecting the real request that
        would transition the breaker to half-open.
        """
        now = time.monotonic()
        cache_ttl = ttl if self._health_cache else failure_ttl
        if (
            self._health_cache is not None
            and (now - self._health_cache_at) < cache_ttl
        ):
            return self._health_cache

        async with self._health_cache_lock:
            now = time.monotonic()
            cache_ttl = ttl if self._health_cache else failure_ttl
            if (
                self._health_cache is not None
                and (now - self._health_cache_at) < cache_ttl
            ):
                return self._health_cache

            result = await self.health_check()
            self._health_cache = result
            self._health_cache_at = time.monotonic()
            if result:
                await self.circuit_breaker.record_success()
            return result
