"""Shared HTTP client base with retry and timeout.

All inter-service HTTP clients should inherit from :class:`BaseServiceClient`
to get consistent retry/timeout/error-handling behaviour.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any

import httpx

from app.utils.request_context import inject_request_headers

if TYPE_CHECKING:
    from app.services.messaging.backpressure import BackpressureCoordinator

logger = logging.getLogger(__name__)

# ── Defaults ────────────────────────────────────────────────────────────────
DEFAULT_CONNECT_TIMEOUT = 30.0      # seconds to establish TCP connection
DEFAULT_READ_TIMEOUT = 300.0        # seconds to wait for server response
DEFAULT_WRITE_TIMEOUT = 60.0        # seconds to finish writing the request
DEFAULT_POOL_TIMEOUT = 30.0         # seconds to acquire a pool connection
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY = 1.0           # seconds; doubled on each retry (exponential)
TRANSIENT_STATUS_CODES = (set(range(500, 600)) - {501}) | {429}
HTTP_TOO_MANY_REQUESTS = 429

# Backpressure (429 + Retry-After) gets its own, longer attempt budget so a
# saturated-but-healthy downstream service is waited out instead of being
# treated as a failure — see ServiceBackpressureError.
DEFAULT_MAX_BACKPRESSURE_ATTEMPTS = 30
DEFAULT_BACKPRESSURE_WAIT_CAP = 30.0  # seconds; clamps a misbehaving/huge Retry-After
MIN_BACKPRESSURE_WAIT = 0.05  # seconds; avoids a hot retry loop on Retry-After: 0

# ── Circuit breaker defaults ────────────────────────────────────────────────
DEFAULT_CIRCUIT_BREAKER_THRESHOLD = 5    # consecutive failures before opening
DEFAULT_CIRCUIT_BREAKER_COOLDOWN = 30.0  # seconds before a probe is allowed


def parse_retry_after(value: str | None) -> float | None:
    """Parse a Retry-After header (delta-seconds or HTTP-date) into seconds.

    Returns None when the header is absent or unparseable, so callers fall
    back to treating the response as an ordinary transient error rather than
    a distinct backpressure signal. Public so other hand-rolled HTTP clients
    that don't go through ``BaseServiceClient`` (e.g. ``DoclingClient``) can
    honour the same backpressure signal without duplicating the parsing.
    """
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime

        target = parsedate_to_datetime(value)
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        return max(0.0, (target - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        return None


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


class ServiceBackpressureError(ServiceCallError):
    """Raised when a service signals sustained backpressure (429 + Retry-After).

    Distinct from :class:`ServiceUnavailableError`: the service is reachable
    and explicitly asking callers to slow down, not failing — the circuit
    breaker must not see this as a failure (or a success), so callers should
    treat it as a retryable/transient outcome on their own longer schedule
    (e.g. re-queue the message) rather than opening the breaker.
    """

    def __init__(
        self,
        message: str,
        retry_after: float,
        service_name: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, status_code=HTTP_TOO_MANY_REQUESTS, service_name=service_name, details=details)
        self.retry_after = retry_after
def _extract_service_error_message(response: httpx.Response) -> str | None:
    """Pull a useful error message from a failed service JSON/text body."""
    try:
        body = response.json()
    except Exception:
        text = (response.text or "").strip()
        return text[:500] if text else None

    if not isinstance(body, dict):
        return None

    error = body.get("error")
    if isinstance(error, dict):
        msg = error.get("message")
        if isinstance(msg, str) and msg.strip():
            return msg.strip()
    if isinstance(error, str) and error.strip():
        return error.strip()

    msg = body.get("message")
    if isinstance(msg, str) and msg.strip():
        return msg.strip()
    return None


class CircuitState(Enum):
    CLOSED = "closed"        # normal operation
    OPEN = "open"             # failing fast, no requests allowed
    HALF_OPEN = "half_open"   # cooldown elapsed, single probe in flight


class CircuitBreaker:
    """Per-client circuit breaker guarding a single downstream service.

    Not thread-safe by design: each ``BaseServiceClient`` instance owns one
    breaker, and all indexing HTTP calls run on the single indexing
    worker-thread event loop, so no locking is required here.

    CLOSED -> OPEN after ``failure_threshold`` consecutive failures.
    OPEN rejects all calls until ``cooldown_seconds`` elapse, then lets a
    single caller run a ``health_check()`` probe (see
    ``BaseServiceClient._request_with_retry``) instead of a real workload
    request — a real request (e.g. a multi-minute document parse) would
    otherwise have to be capped at ``cooldown_seconds``, which can abort a
    legitimately slow-but-healthy call. A successful probe closes the
    circuit; a failed probe re-opens it for another full cooldown.
    """

    # Safety margin for a claimed probe that never reports back (e.g. its
    # task was cancelled mid health-check) so a stuck flag can't wedge the
    # breaker open past its cooldown. health_check() itself is bounded to
    # ~10s, so this only ever matters in that edge case.
    _PROBE_STUCK_TIMEOUT = 30.0

    def __init__(
        self,
        service_name: str,
        failure_threshold: int = DEFAULT_CIRCUIT_BREAKER_THRESHOLD,
        cooldown_seconds: float = DEFAULT_CIRCUIT_BREAKER_COOLDOWN,
        logger: logging.Logger | None = None,
    ) -> None:
        self.service_name = service_name
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.logger = logger or logging.getLogger(__name__)
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at: float | None = None
        self._half_open_probe_in_flight = False
        self._half_open_probe_started_at: float | None = None

    def _probe_timed_out(self, now: float) -> bool:
        return (
            self._half_open_probe_started_at is not None
            and now - self._half_open_probe_started_at >= self._PROBE_STUCK_TIMEOUT
        )

    @property
    def is_open(self) -> bool:
        """Whether a call right now would be rejected without hitting the network.

        Used for cheap pre-checks (e.g. before marking a record IN_PROGRESS)
        so callers can fail fast without even attempting the request.
        """
        now = time.monotonic()
        if self._state == CircuitState.HALF_OPEN:
            if self._probe_timed_out(now):
                self._reset_stuck_probe(now)
                return False
            return True
        if self._state != CircuitState.OPEN:
            return False
        if (
            self._opened_at is not None
            and now - self._opened_at >= self.cooldown_seconds
        ):
            # Cooldown elapsed — a probe may proceed; the actual state
            # transition happens in should_attempt_probe() to avoid
            # double-probing.
            return False
        return True

    def _reset_stuck_probe(self, now: float) -> None:
        self._state = CircuitState.OPEN
        self._opened_at = now - self.cooldown_seconds
        self._half_open_probe_in_flight = False
        self._half_open_probe_started_at = None

    def should_attempt_probe(self) -> bool:
        """Claim the single health-check probe slot for this cooldown window.

        Returns True if the caller just claimed the slot and must now await
        ``health_check()`` and report the outcome via ``record_success`` /
        ``record_failure``. Returns False if the circuit isn't OPEN, the
        cooldown hasn't elapsed yet, or another caller already owns the
        probe.
        """
        now = time.monotonic()
        if self._state != CircuitState.OPEN:
            return False
        if self._opened_at is None or now - self._opened_at < self.cooldown_seconds:
            return False
        if self._half_open_probe_in_flight:
            return False
        self._state = CircuitState.HALF_OPEN
        self._half_open_probe_in_flight = True
        self._half_open_probe_started_at = now
        self.logger.info(
            "[%s] Circuit breaker cooldown elapsed, running health-check probe",
            self.service_name,
        )
        return True

    def record_success(self) -> None:
        if self._state != CircuitState.CLOSED:
            self.logger.info(
                "[%s] Circuit breaker closing after successful probe", self.service_name
            )
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at = None
        self._half_open_probe_in_flight = False
        self._half_open_probe_started_at = None

    def record_failure(self) -> None:
        self._half_open_probe_in_flight = False
        self._half_open_probe_started_at = None

        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()
            self.logger.warning(
                "[%s] Circuit breaker probe failed, re-opening for %.0fs",
                self.service_name, self.cooldown_seconds,
            )
            return

        self._consecutive_failures += 1
        if self._consecutive_failures >= self.failure_threshold and self._state == CircuitState.CLOSED:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()
            self.logger.warning(
                "[%s] Circuit breaker opened after %d consecutive failures; "
                "rejecting calls for %.0fs",
                self.service_name, self._consecutive_failures, self.cooldown_seconds,
            )


class BaseServiceClient:
    """Async HTTP client with retry, timeout, and health-check.

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
        circuit_breaker_threshold: int = DEFAULT_CIRCUIT_BREAKER_THRESHOLD,
        circuit_breaker_cooldown: float = DEFAULT_CIRCUIT_BREAKER_COOLDOWN,
        max_backpressure_attempts: int = DEFAULT_MAX_BACKPRESSURE_ATTEMPTS,
        backpressure_coordinator: "BackpressureCoordinator | None" = None,
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
        self.max_backpressure_attempts = max_backpressure_attempts
        # Any client sharing a coordinator with the consumer that drives it
        # lets a 429 here also pause that consumer's event-bus reads (see
        # app.services.messaging.backpressure) — this client's own retry
        # budget below is unaffected either way.
        self._backpressure_coordinator = backpressure_coordinator
        self.logger = logging.getLogger(f"{__name__}.{service_name}")
        self.circuit_breaker = CircuitBreaker(
            service_name=service_name,
            failure_threshold=circuit_breaker_threshold,
            cooldown_seconds=circuit_breaker_cooldown,
            logger=self.logger,
        )

    @property
    def circuit_open(self) -> bool:
        """Whether calls to this service would currently be rejected immediately.

        Cheap in-memory check — callers should use this to fail fast (e.g.
        before marking a record IN_PROGRESS) instead of making a real HTTP
        call just to discover the service is down.
        """
        return self.circuit_breaker.is_open

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _make_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self._timeout,
            limits=self._limits,
        )

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
        """Execute *method* request with exponential-backoff retry.

        Guarded by a circuit breaker: when open, this fails immediately with
        no connection attempt and no retry sleeps, so a downstream outage
        doesn't tie up parsing/indexing concurrency slots for every record.

        When the cooldown elapses, recovery is probed with ``health_check()``
        rather than this method's own (possibly multi-minute) workload
        request — a real parse call would otherwise have to be capped at
        ``cooldown_seconds``, aborting legitimately slow-but-healthy calls.

        A 429 carrying ``Retry-After`` is treated as backpressure, not
        failure: it is retried on its own ``max_backpressure_attempts``
        budget (honouring the header, capped per wait), and calls neither
        ``record_success()`` nor ``record_failure()`` — a saturated-but-
        healthy service must never open the circuit breaker. Every other
        transient outcome (5xx, bare 429, transport errors) still counts
        against ``max_retries`` and the breaker as before.
        """
        if self.circuit_breaker.should_attempt_probe():
            probe_ok = await self.health_check()
            if probe_ok:
                self.circuit_breaker.record_success()
            else:
                self.circuit_breaker.record_failure()

        if self.circuit_breaker.is_open:
            self.logger.debug(
                "[%s] %s rejected: circuit breaker open", self.service_name, operation,
            )
            raise ServiceUnavailableError(
                f"{self.service_name} is currently unavailable (circuit breaker open)",
                status_code=503,
                service_name=self.service_name,
            )

        attempt_limit = self.max_retries
        # Every inter-service call carries the caller's root request id so
        # logs across Node/Python service boundaries can be correlated.
        headers = inject_request_headers(headers or {})
        last_exc: Exception | None = None
        last_status: int | None = None
        transient_attempts = 0
        backpressure_attempts = 0
        last_error_message: str | None = None

        async with self._make_client() as client:
            while True:
                try:
                    self.logger.debug(
                        "[%s] %s %s (transient attempt %d/%d, backpressure attempt %d/%d)",
                        self.service_name, method.upper(), url,
                        transient_attempts + 1, attempt_limit,
                        backpressure_attempts, self.max_backpressure_attempts,
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
                        # Service responded — even a 4xx means it's reachable.
                        self.circuit_breaker.record_success()
                        return response

                    retry_after = (
                        parse_retry_after(response.headers.get("Retry-After"))
                        if response.status_code == HTTP_TOO_MANY_REQUESTS
                        else None
                    )
                    if retry_after is not None:
                        # Genuine backpressure signal, not a failure: the
                        # service is reachable and explicitly asking us to
                        # slow down. Retried on its own budget, independent
                        # of attempt_limit, and never touches the breaker.
                        # Signalled on every occurrence (not just the
                        # first) so the coordinator's pause deadline tracks
                        # the service's latest requested wait.
                        wait = min(max(retry_after, MIN_BACKPRESSURE_WAIT), DEFAULT_BACKPRESSURE_WAIT_CAP)
                        if self._backpressure_coordinator is not None:
                            self._backpressure_coordinator.signal(self.service_name, wait)
                        backpressure_attempts += 1
                        if backpressure_attempts > self.max_backpressure_attempts:
                            self.logger.warning(
                                "[%s] %s still backpressured after %d attempt(s), giving up "
                                "(retry-after=%.1fs)",
                                self.service_name, operation, backpressure_attempts, retry_after,
                            )
                            raise ServiceBackpressureError(
                                f"{self.service_name} {operation} is backpressured "
                                f"after {backpressure_attempts} attempts",
                                retry_after=retry_after,
                                service_name=self.service_name,
                            )
                        self.logger.debug(
                            "[%s] %s backpressured (429, Retry-After=%.1fs), waiting %.1fs (%d/%d)",
                            self.service_name, operation, retry_after, wait,
                            backpressure_attempts, self.max_backpressure_attempts,
                        )
                        await asyncio.sleep(wait)
                        continue

                    # Transient 5xx / bare 429 (no Retry-After) — retryable.
                    transient_attempts += 1
                    # Transient 5xx / 429 — retryable. Keep body message for final raise.
                    last_error_message = _extract_service_error_message(response)
                    self.logger.debug(
                        "[%s] %s returned %d on attempt %d",
                        self.service_name, operation, response.status_code, transient_attempts,
                    )
                except (
                    TimeoutError,
                    httpx.TimeoutException,
                    httpx.ConnectError,
                    httpx.WriteError,
                ) as exc:
                    transient_attempts += 1
                    self.logger.debug(
                        "[%s] %s transport error on attempt %d: %s",
                        self.service_name, operation, transient_attempts, exc,
                    )
                    last_exc = exc
                except httpx.RequestError as exc:
                    # Everything else under RequestError (InvalidURL,
                    # UnsupportedProtocol, ProtocolError, ...) is a
                    # client-side/config bug, not a signal that the
                    # downstream service is unhealthy — raise immediately
                    # without retrying or counting it against the circuit
                    # breaker (which would otherwise open on our own bug).
                    self.logger.error(
                        "[%s] %s client-side request error (not retried): %s",
                        self.service_name, operation, exc,
                    )
                    raise ServiceCallError(
                        f"{self.service_name} {operation} failed due to a "
                        f"client-side request error: {exc}",
                        service_name=self.service_name,
                    ) from exc

                if transient_attempts >= attempt_limit:
                    break
                delay = self.retry_delay * (2 ** (transient_attempts - 1))
                self.logger.debug("[%s] Retrying in %.1fs …", self.service_name, delay)
                await asyncio.sleep(delay)

        # All attempts exhausted without a usable response — a single summary
        # WARNING per failed operation, instead of one per retry attempt.
        attempted = attempt_limit
        summary = last_exc or (
            f"status {last_status}: {last_error_message}"
            if last_error_message
            else f"status {last_status}"
        )
        self.logger.warning(
            "[%s] %s failed after %d attempt(s): %s",
            self.service_name, operation, attempted, summary,
        )
        self.circuit_breaker.record_failure()

        if last_exc is not None:
            raise ServiceUnavailableError(
                f"{self.service_name} {operation} failed after {attempted} attempts: {last_exc}",
                service_name=self.service_name,
            ) from last_exc

        message = f"{self.service_name} {operation} failed with status {last_status}"
        if last_error_message:
            message = f"{message}: {last_error_message}"
        raise ServiceCallError(
            message,
            status_code=last_status,
            service_name=self.service_name,
            details={"error_message": last_error_message} if last_error_message else None,
        )

    # ------------------------------------------------------------------
    # Public helpers for sub-classes
    # ------------------------------------------------------------------

    async def _post_json(self, path: str, payload: dict, operation: str = "POST") -> httpx.Response:
        url = f"{self.service_url}{path}"
        body = json.dumps(payload).encode("utf-8")
        headers: dict[str, str] = {"Content-Type": "application/json"}
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
        """Return True when the service responds with HTTP 200."""
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
