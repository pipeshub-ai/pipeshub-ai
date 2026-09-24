"""Shared rate limiting and retry policy for connectors.

Deliberately transport-agnostic: only ~2 of the connectors that rate limit today
route through ``HTTPClient``/httpx — the rest talk to vendor SDKs (aioboto3,
azure-storage, msgraph/kiota, dropbox, asyncpg) that an httpx transport cannot
see. HTTP clients consume this via
``app.sources.client.http.http_resilient_transport``; SDK call sites use
:meth:`ResiliencePolicy.guard` or :meth:`ResiliencePolicy.run` directly.

Scope is one policy per connector *instance*. Two instances pointed at the same
upstream account get independent budgets, and the limiter is per process — with
``CONNECTOR_UVICORN_WORKERS > 1`` the effective rate is multiplied by the worker
count.
"""

import asyncio
import random
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from logging import Logger, getLogger
from typing import Any, TypeVar
from weakref import WeakKeyDictionary

import httpx  # type: ignore
from aiolimiter import AsyncLimiter  # type: ignore

from app.sources.client.http.http_retry import is_retryable_status

T = TypeVar("T")

# Transport-level failures worth another attempt. Mirrors the set handled by
# app.sources.client.http.http_retry.call_with_retry.
RETRYABLE_NETWORK_ERRORS = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.ReadError,
    httpx.WriteError,
    httpx.RemoteProtocolError,
)

# Spread of the random delay applied when the backoff gate lifts, as a fraction
# of the connector's base delay. Waiters released together would otherwise all
# hit the upstream on the same tick.
_GATE_WAKE_JITTER = 0.25


class ResiliencePolicy:
    """Rate limit, backoff gate, and retry knobs shared by one connector.

    The gate is the part that distinguishes this from a bare ``AsyncLimiter``:
    when any request is throttled, :meth:`pause` arms a deadline that every
    other caller on the same connector waits behind, so a single 429 does not
    become N concurrent 429s across a fan-out.

    All waiting is ``await``-based, so a paused connector never blocks the event
    loop, and connectors hold separate policies so one cannot stall another.
    """

    def __init__(
        self,
        *,
        rate_limit: float | None = None,
        max_retries: int = 0,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        name: str = "",
        logger: Logger | None = None,
    ) -> None:
        if rate_limit is not None and (not isinstance(rate_limit, (int, float)) or rate_limit <= 0):
            raise ValueError(f"rate_limit must be a positive number, got: {rate_limit!r}")
        if not isinstance(max_retries, int) or isinstance(max_retries, bool) or max_retries < 0:
            raise ValueError(f"max_retries must be a non-negative integer, got: {max_retries!r}")
        if not isinstance(base_delay, (int, float)) or base_delay < 0:
            raise ValueError(f"base_delay must be a non-negative number, got: {base_delay!r}")
        if not isinstance(max_delay, (int, float)) or max_delay < 0:
            raise ValueError(f"max_delay must be a non-negative number, got: {max_delay!r}")
        if max_delay < base_delay:
            raise ValueError(f"max_delay ({max_delay}) must be >= base_delay ({base_delay})")

        self.rate_limit = rate_limit
        self.max_retries = max_retries
        self.base_delay = float(base_delay)
        self.max_delay = float(max_delay)
        self.name = name
        self.logger = logger or getLogger(__name__)

        # This process runs several event loops (uvicorn plus the Kafka and Redis
        # consumers, which each build their own loop in a worker thread) and
        # AsyncLimiter binds to a loop on first acquire. Same double-checked
        # per-loop construction as app/utils/concurrency.py.
        self._limiters: "WeakKeyDictionary[asyncio.AbstractEventLoop, AsyncLimiter]" = WeakKeyDictionary()
        self._limiter_lock = threading.Lock()

        # Monotonic deadline, on the clock of whichever loop armed it. Loops here
        # are per-thread and long-lived, so cross-loop skew is not a concern in
        # practice; the deadline degrades to "resume immediately", never to a
        # missed pause.
        self._resume_at: float = 0.0

    @classmethod
    def from_config(
        cls,
        config: dict[str, Any] | None,
        *,
        name: str = "",
        logger: Logger | None = None,
    ) -> "ResiliencePolicy | None":
        """Build a policy from ``resilienceConfig`` decorator metadata.

        Returns ``None`` when unconfigured or explicitly disabled, so callers can
        represent "no resilience" without a null-object that silently rate limits.
        """
        if not config or config.get("enabled") is False:
            return None
        return cls(
            rate_limit=config.get("rate_limit"),
            max_retries=config.get("max_retries", 0),
            base_delay=config.get("base_delay", 1.0),
            max_delay=config.get("max_delay", 60.0),
            name=name,
            logger=logger,
        )

    def _limiter(self) -> AsyncLimiter | None:
        if self.rate_limit is None:
            return None
        loop = asyncio.get_running_loop()
        limiter = self._limiters.get(loop)
        if limiter is None:
            with self._limiter_lock:
                limiter = self._limiters.get(loop)
                if limiter is None:
                    # AsyncLimiter caches the loop it binds to, so each value
                    # strongly references its own weak key and the entry can never
                    # be collected on its own. Drop closed loops as we add new ones.
                    for dead in [k for k in list(self._limiters.keys()) if k.is_closed()]:
                        self._limiters.pop(dead, None)
                    if self.rate_limit < 1:
                        # aiolimiter refuses acquire(1) when max_rate < 1, so a
                        # sub-1/s budget has to be spelled as one token per
                        # (1 / rate) seconds instead of a fractional rate.
                        limiter = AsyncLimiter(1, 1 / self.rate_limit)
                    else:
                        limiter = AsyncLimiter(self.rate_limit, 1)
                    self._limiters[loop] = limiter
        return limiter

    def pause(self, seconds: float) -> None:
        """Hold back every caller on this connector for ``seconds``.

        Extends an existing pause but never shortens one, so a long ``Retry-After``
        is not undone by a later, shorter backoff landing on the same connector.
        """
        if seconds <= 0:
            return
        try:
            now = asyncio.get_running_loop().time()
        except RuntimeError:
            return
        self._resume_at = max(self._resume_at, now + min(seconds, self.max_delay))

    async def _wait_for_gate(self) -> None:
        loop = asyncio.get_running_loop()
        waited = False
        while True:
            remaining = self._resume_at - loop.time()
            if remaining <= 0:
                break
            waited = True
            await asyncio.sleep(remaining)
        if waited and self.base_delay:
            # Waiters released together would otherwise resume in lockstep.
            await asyncio.sleep(random.uniform(0, self.base_delay * _GATE_WAKE_JITTER))

    def _gate_is_closed(self) -> bool:
        try:
            return self._resume_at > asyncio.get_running_loop().time()
        except RuntimeError:
            return False

    async def acquire(self) -> None:
        """Wait out any active backoff, then take one rate-limit slot."""
        limiter = self._limiter()
        while True:
            await self._wait_for_gate()
            if limiter is None:
                return
            await limiter.acquire()
            # Waiting for a token can take a long time when the queue is deep, and
            # the gate may have armed meanwhile. Checking only before the queue
            # would let every already-queued caller drain into a throttled API,
            # turning one 429 into one per queued request. Loop instead: wait the
            # pause out and take a fresh token, so the released wave stays paced.
            if not self._gate_is_closed():
                return

    @asynccontextmanager
    async def guard(self) -> AsyncIterator[None]:
        """Rate-limit one call. Drop-in for ``async with self.rate_limiter:``."""
        await self.acquire()
        yield

    def backoff(self, attempt: int, retry_after: float | None = None) -> float:
        """Delay before ``attempt``'s retry.

        ``Retry-After`` wins when present but is capped at ``max_delay`` — an
        upstream asking for an hour would otherwise park the caller (and its
        pooled connection) for that long. Otherwise exponential with full jitter.

        Only numeric ``Retry-After`` values reach here; the HTTP-date form parses
        as ``None`` and falls back to exponential backoff (Notion sends seconds).
        """
        if retry_after is not None:
            # Added, never subtracted: requests throttled in the same instant
            # would otherwise all retry on the same tick, but coming back before
            # the server asked would just earn another 429.
            spread = random.uniform(0, self.base_delay * _GATE_WAKE_JITTER)
            return min(retry_after, self.max_delay) + spread
        ceiling = min(self.max_delay, self.base_delay * (2 ** attempt))
        return random.uniform(0, ceiling)

    def note_retry(self, attempt: int, retry_after: float | None = None) -> float:
        """Compute the retry delay and arm the gate with it."""
        delay = self.backoff(attempt, retry_after)
        self.pause(delay)
        return delay

    async def run(
        self,
        op: Callable[[], Awaitable[T]],
        *,
        label: str = "",
    ) -> T:
        """Run ``op`` under the rate limit, retrying transient failures.

        For SDK call sites that have no httpx transport to hook. HTTP clients get
        the same behavior from ``ResilientHTTPTransport`` instead.
        """
        prefix = f"[{self.name}{'/' + label if label else ''}] " if self.name or label else ""
        last_exc: BaseException | None = None

        for attempt in range(self.max_retries + 1):
            await self.acquire()
            try:
                return await op()
            except RETRYABLE_NETWORK_ERRORS as exc:
                last_exc = exc
                if attempt >= self.max_retries:
                    break
                delay = self.note_retry(attempt)
                self.logger.warning(
                    "%s%s (attempt %s/%s) — retrying in %.2fs",
                    prefix, type(exc).__name__, attempt + 1, self.max_retries + 1, delay,
                )
                await asyncio.sleep(delay)
            except httpx.HTTPStatusError as exc:
                if not is_retryable_status(exc.response.status_code):
                    raise
                last_exc = exc
                if attempt >= self.max_retries:
                    break
                delay = self.note_retry(attempt, parse_retry_after(exc.response.headers))
                self.logger.warning(
                    "%sHTTP %s (attempt %s/%s) — retrying in %.2fs",
                    prefix, exc.response.status_code, attempt + 1, self.max_retries + 1, delay,
                )
                await asyncio.sleep(delay)

        # The loop only exits early via break, which always sets last_exc.
        if last_exc is None:
            raise RuntimeError("Retry loop exited without a result or an exception")
        self.logger.error(
            "%sFailed after %s attempts: %s", prefix, self.max_retries + 1, type(last_exc).__name__
        )
        raise last_exc


def parse_retry_after(headers: Mapping[str, str]) -> float | None:
    """Read ``Retry-After`` as seconds, or ``None`` if absent/an HTTP-date."""
    try:
        value = headers.get("Retry-After") or headers.get("retry-after")
    except AttributeError:
        return None
    if not value:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return seconds if seconds >= 0 else None
