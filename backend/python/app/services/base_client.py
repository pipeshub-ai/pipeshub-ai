"""Shared HTTP client base with retry and timeout.

All inter-service HTTP clients should inherit from :class:`BaseServiceClient`
to get consistent retry/timeout/error-handling behaviour.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ── Defaults ────────────────────────────────────────────────────────────────
DEFAULT_CONNECT_TIMEOUT = 30.0      # seconds to establish TCP connection
DEFAULT_READ_TIMEOUT = 300.0        # seconds to wait for server response
DEFAULT_WRITE_TIMEOUT = 60.0        # seconds to finish writing the request
DEFAULT_POOL_TIMEOUT = 30.0         # seconds to acquire a pool connection
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY = 1.0           # seconds; doubled on each retry (exponential)
TRANSIENT_STATUS_CODES = {502, 503, 504, 429}


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
        self.logger = logging.getLogger(f"{__name__}.{service_name}")

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
        """Execute *method* request with exponential-backoff retry."""
        headers = headers or {}
        last_exc: Exception | None = None
        last_status: int | None = None

        async with self._make_client() as client:
            for attempt in range(1, self.max_retries + 1):
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

                    if response.status_code < 500 or response.status_code not in TRANSIENT_STATUS_CODES:
                        return response

                    # Transient 5xx / 429
                    self.logger.warning(
                        "[%s] %s returned %d on attempt %d",
                        self.service_name, operation, response.status_code, attempt,
                    )

                except (httpx.TimeoutException, httpx.ConnectError, httpx.WriteError) as exc:
                    self.logger.warning(
                        "[%s] %s transport error on attempt %d: %s",
                        self.service_name, operation, attempt, exc,
                    )
                    last_exc = exc
                except httpx.RequestError as exc:
                    self.logger.warning(
                        "[%s] %s request error on attempt %d: %s",
                        self.service_name, operation, attempt, exc,
                    )
                    last_exc = exc
                    break

                if attempt < self.max_retries:
                    delay = self.retry_delay * (2 ** (attempt - 1))
                    self.logger.info("[%s] Retrying in %.1fs …", self.service_name, delay)
                    await asyncio.sleep(delay)

        if last_exc is not None:
            raise ServiceUnavailableError(
                f"{self.service_name} {operation} failed after {self.max_retries} attempts: {last_exc}",
                service_name=self.service_name,
            ) from last_exc

        raise ServiceCallError(
            f"{self.service_name} {operation} failed with status {last_status}",
            status_code=last_status,
            service_name=self.service_name,
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
