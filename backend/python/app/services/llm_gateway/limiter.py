"""A counting semaphore that holds across event loops.

An ``asyncio.Semaphore`` binds to one loop, so a cap built from them multiplies with the loops
that call it (the server loop and the worker loop here; each service's own loops elsewhere).
This one is guarded by a thread lock, and waiters on any loop are served in arrival order.
"""

import asyncio
import contextlib
import threading
import time
from collections import deque
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import NamedTuple


class SlotTimings(NamedTuple):
    acquired: int
    released: int
    wait_s: float
    hold_s: float


class CrossLoopSemaphore:
    def __init__(self, value: int) -> None:
        super().__init__()
        if value < 1:
            raise ValueError("a semaphore needs at least one permit")
        self._limit = value
        self._free = value
        self._lock = threading.Lock()
        self._waiters: deque[tuple[asyncio.AbstractEventLoop, asyncio.Future[None]]] = deque()
        # Totals for slot(): wait against hold time tells this cap binding apart from a slow provider.
        self._acquired = 0
        self._released = 0
        self._wait_s = 0.0
        self._hold_s = 0.0

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def in_use(self) -> int:
        with self._lock:
            return self._limit - self._free

    @property
    def waiting(self) -> int:
        with self._lock:
            return len(self._waiters)

    async def acquire(self) -> None:
        loop = asyncio.get_running_loop()
        with self._lock:
            if self._free > 0 and not self._waiters:
                self._free -= 1
                return
            future: asyncio.Future[None] = loop.create_future()
            self._waiters.append((loop, future))
        try:
            await future
        except asyncio.CancelledError:
            if future.done() and not future.cancelled():
                # Granted in the same instant we were cancelled: the permit is ours to return.
                self.release()
            else:
                # Not found: already handed a permit, and _grant sees the cancellation and passes it on.
                with self._lock, contextlib.suppress(ValueError):
                    self._waiters.remove((loop, future))
            raise

    def release(self) -> None:
        with self._lock:
            while self._waiters:
                loop, future = self._waiters.popleft()
                try:
                    loop.call_soon_threadsafe(self._grant, future)
                except RuntimeError:
                    continue  # that waiter's loop has closed; it will never resume
                return
            if self._free >= self._limit:
                raise ValueError("released more permits than were acquired")
            self._free += 1

    def _grant(self, future: asyncio.Future[None]) -> None:
        if future.cancelled():
            self.release()
        else:
            future.set_result(None)

    def timings(self) -> SlotTimings:
        """Permits granted and returned through ``slot()``, and the seconds spent waiting for and
        holding them (hold time grows only when a permit is returned)."""
        with self._lock:
            return SlotTimings(self._acquired, self._released, self._wait_s, self._hold_s)

    @asynccontextmanager
    async def slot(self) -> AsyncGenerator[None, None]:
        asked = time.perf_counter()
        await self.acquire()
        granted = time.perf_counter()
        with self._lock:
            self._acquired += 1
            self._wait_s += granted - asked
        try:
            yield
        finally:
            self.release()
            with self._lock:
                self._released += 1
                self._hold_s += time.perf_counter() - granted
