"""Per-event-loop admission gate with weighted permits and interval-based
demand accounting.

See plan section 1.2 for why this cannot be a plain ``asyncio.Semaphore``
owned by a cross-thread controller, and section 4.1 for why demand is
accumulated continuously instead of point-sampled.
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from collections import deque
from typing import TYPE_CHECKING

from app.services.resource_governor.models import Pool, PoolDemand

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from app.services.resource_governor.registry import LimitRegistry

# Safety net between wakeups triggered by release()/limit changes — if a
# wakeup is ever missed, a waiter re-checks admission at worst this often
# instead of stalling indefinitely.
_SAFETY_NET_INTERVAL_SECONDS = 1.0


class StartRateLimiter:
    """Token bucket spacing *admissions* (not releases) so newly granted
    work can't all start allocating memory at once (plan section 4,
    "Start-rate limiter"). One token refills every ``interval`` seconds, up
    to ``capacity`` banked tokens.
    """

    def __init__(
        self,
        interval: float,
        capacity: int,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._interval = interval
        self._capacity = float(capacity)
        self._clock = clock
        self._tokens = float(capacity)
        self._last_refill = clock()

    def try_consume(self) -> bool:
        now = self._clock()
        elapsed = now - self._last_refill
        if elapsed > 0:
            self._tokens = min(self._capacity, self._tokens + elapsed / self._interval)
            self._last_refill = now
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False


class _Waiter:
    """One queued acquire. Woken by the gate; re-arms itself after each wake
    so a wake that did not lead to admission is not lost on the next one."""

    __slots__ = ("_loop", "_future")

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._future: asyncio.Future[None] = loop.create_future()

    def wake(self) -> None:
        if not self._future.done():
            self._future.set_result(None)

    async def wait(self, poll: float) -> None:
        # asyncio.wait, not wait_for: a poll timeout must not cancel the
        # future a later wake() will complete.
        await asyncio.wait({self._future}, timeout=poll)
        if self._future.done():
            self._future = self._loop.create_future()


class AdmissionGate:
    """Weighted admission control bound to the event loop that first uses it.

    Never revokes an in-flight permit: shrinking the limit only changes
    whether *future* acquires are admitted (plan principle 4). A gate used
    from a second, different event loop raises ``RuntimeError`` rather than
    silently corrupting its counters.
    """

    def __init__(
        self,
        pool: Pool,
        registry: LimitRegistry,
        *,
        rate_limiter: StartRateLimiter | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._pool = pool
        self._registry = registry
        self._rate_limiter = rate_limiter
        self._clock = clock

        self._loop: asyncio.AbstractEventLoop | None = None
        # Arrival-ordered waiters. Only the head is woken on a release or a
        # limit change, so a record's wait is bounded by its place in the
        # queue: with every waiter woken at once they all raced for the
        # permit, and one could lose that race for the whole of its
        # processing budget while newer arrivals went ahead of it.
        self._waiters: deque[_Waiter] = deque()
        self._unsubscribe: Callable[[], None] | None = None

        self._in_use = 0
        self._permit_seconds = 0.0
        self._blocked_acquires = 0
        self._total_wait_seconds = 0.0
        self._completions = 0
        self._max_in_use = 0
        self._rate_limited_acquires = 0
        self._last_change = clock()

    # -- loop binding ---------------------------------------------------

    def _bind(self) -> asyncio.AbstractEventLoop:
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
            self._unsubscribe = self._registry.subscribe(self._pool, self._on_limit_changed)
        elif loop is not self._loop:
            raise RuntimeError(
                f"AdmissionGate for pool {self._pool.value!r} is bound to event "
                f"loop {self._loop!r} but was used from {loop!r}. One "
                "AdmissionGate instance must not be shared across event loops "
                "— obtain a separate gate for the other loop."
            )
        return loop

    def wake(self) -> None:
        """Re-check admission on this gate's loop. Safe from any thread."""
        loop = self._loop
        if loop is None:
            return
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(self._wake_head)

    def _wake_head(self) -> None:
        if self._waiters:
            self._waiters[0].wake()

    def _on_limit_changed(self, _pool: Pool, _value: int) -> None:
        """Registry subscriber callback — may run on any thread."""
        self.wake()

    # -- bookkeeping -----------------------------------------------------

    def _fold_permit_seconds(self, now: float) -> None:
        elapsed = now - self._last_change
        if elapsed > 0:
            self._permit_seconds += self._in_use * elapsed
        self._last_change = now

    def _try_admit(self, cost: int) -> bool:
        limit = self._registry.get(self._pool)
        # Deadlock guard: an oversized request (cost > limit, or limit fully
        # shrunk to 0) is still admitted alone rather than waiting forever.
        has_room = self._in_use == 0 or (self._in_use + cost <= limit)
        if not has_room:
            return False
        if self._rate_limiter is not None and not self._rate_limiter.try_consume():
            # Distinct from "no room": capacity was free, this acquire was
            # denied purely by the start-rate limiter. Tracked separately so
            # operators can tell "throttled by the burst smoother" apart
            # from "genuinely at the concurrency limit" (drain_demand/stats).
            self._rate_limited_acquires += 1
            return False
        now = self._clock()
        self._fold_permit_seconds(now)
        self._in_use += cost
        self._max_in_use = max(self._max_in_use, self._in_use)
        return True

    # -- public API ----------------------------------------------------

    async def acquire(self, cost: int = 1, timeout: float | None = None) -> bool:
        """Acquire *cost* permits.

        Returns ``False`` on timeout rather than raising, so callers can
        respond with backpressure instead of an exception (plan section
        1.4 — a timeout here must never be treated as a service failure).
        """
        loop = self._bind()
        # Nobody may overtake a queued waiter, so a fresh arrival only takes
        # the fast path while the queue is empty.
        if not self._waiters and self._try_admit(cost):
            return True

        wait_start = self._clock()
        self._blocked_acquires += 1
        deadline = None if timeout is None else wait_start + timeout
        waiter = _Waiter(loop)
        self._waiters.append(waiter)
        try:
            while True:
                remaining = None if deadline is None else deadline - self._clock()
                if remaining is not None and remaining <= 0:
                    return False
                poll = (
                    _SAFETY_NET_INTERVAL_SECONDS
                    if remaining is None
                    else min(_SAFETY_NET_INTERVAL_SECONDS, remaining)
                )
                await waiter.wait(poll)
                if self._waiters[0] is waiter and self._try_admit(cost):
                    return True
        finally:
            self._total_wait_seconds += self._clock() - wait_start
            with contextlib.suppress(ValueError):
                self._waiters.remove(waiter)
            # Whether admitted or given up, the next in line gets its turn:
            # a limit that grew by several permits admits several waiters,
            # one wake at a time.
            self._wake_head()

    def release(self, cost: int = 1) -> None:
        now = self._clock()
        self._fold_permit_seconds(now)
        released = min(cost, self._in_use)
        self._in_use -= released
        self._completions += 1
        self._wake_head()

    @contextlib.asynccontextmanager
    async def slot(self, cost: int = 1, timeout: float | None = None) -> AsyncIterator[bool]:
        """Context manager wrapping acquire/release.

        Yields whether the permit was granted — a caller that ignores a
        ``False`` yield and proceeds anyway is bypassing admission control,
        so this always yields the outcome rather than raising on timeout.
        """
        admitted = await self.acquire(cost, timeout)
        try:
            yield admitted
        finally:
            if admitted:
                self.release(cost)

    def drain_demand(self) -> PoolDemand:
        """Read and reset the interval accumulators (plan section 4.1)."""
        now = self._clock()
        self._fold_permit_seconds(now)
        demand = PoolDemand(
            permit_seconds=self._permit_seconds,
            blocked_acquires=self._blocked_acquires,
            total_wait_seconds=self._total_wait_seconds,
            completions=self._completions,
            max_in_use=self._max_in_use,
            rate_limited_acquires=self._rate_limited_acquires,
        )
        self._permit_seconds = 0.0
        self._blocked_acquires = 0
        self._total_wait_seconds = 0.0
        self._completions = 0
        self._max_in_use = self._in_use
        self._rate_limited_acquires = 0
        return demand

    @property
    def in_use(self) -> int:
        return self._in_use

    @property
    def limit(self) -> int:
        return self._registry.get(self._pool)

    def close(self) -> None:
        """Unsubscribe from the registry. Call on shutdown to avoid leaking
        a callback that bounces onto a closed event loop."""
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
