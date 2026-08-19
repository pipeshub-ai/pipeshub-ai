"""Thread-safe storage for current per-pool limits.

``AdmissionGate`` instances — running on arbitrary event loops, possibly
inside worker threads (see plan section 1.2: both indexing consumers create
their concurrency primitives inside a worker thread's own event loop) — read
limits from here on every acquire attempt and subscribe to be woken when a
limit changes. The ``ResourceGovernor`` sample loop is the only writer.
"""
from __future__ import annotations

import contextlib
import threading
from typing import TYPE_CHECKING

from app.services.resource_governor.models import Limits, Pool

if TYPE_CHECKING:
    from collections.abc import Callable


class LimitRegistry:
    """Source of truth for effective per-pool limits.

    Reads and writes are protected by a plain lock rather than being
    lock-free: writes happen once per sample interval (a few Hz at most), so
    contention is a non-issue, and a lock keeps this trivially correct
    across however many threads read it — which a bare ``asyncio.Semaphore``
    cannot be, since it is bound to a single event loop for its lifetime.
    """

    def __init__(self, initial: Limits) -> None:
        self._lock = threading.Lock()
        self._limits: dict[Pool, int] = dict(initial.values)
        self._subscribers: dict[Pool, list[Callable[[Pool, int], None]]] = {pool: [] for pool in Pool}

    def get(self, pool: Pool) -> int:
        with self._lock:
            return self._limits[pool]

    def snapshot(self) -> Limits:
        with self._lock:
            return Limits(values=dict(self._limits))

    def set(self, pool: Pool, value: int) -> bool:
        """Update *pool*'s limit. Returns True if the value actually changed.

        Subscriber callbacks run outside the lock and their exceptions are
        swallowed — a misbehaving gate must not stop other gates from being
        woken, nor stop the next sample from running.
        """
        with self._lock:
            if self._limits.get(pool) == value:
                return False
            self._limits[pool] = value
            subscribers = list(self._subscribers[pool])
        for callback in subscribers:
            with contextlib.suppress(Exception):
                callback(pool, value)
        return True

    def subscribe(self, pool: Pool, callback: Callable[[Pool, int], None]) -> Callable[[], None]:
        """Register *callback* to run (outside the lock) whenever *pool*'s
        limit changes. Returns an unsubscribe function."""
        with self._lock:
            self._subscribers[pool].append(callback)

        def _unsubscribe() -> None:
            with self._lock, contextlib.suppress(ValueError):
                self._subscribers[pool].remove(callback)

        return _unsubscribe
