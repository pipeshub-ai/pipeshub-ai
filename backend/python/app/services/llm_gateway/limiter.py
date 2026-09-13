"""A counting semaphore that holds across event loops.

An ``asyncio.Semaphore`` binds to one loop, so a cap built from them multiplies with the loops
that call it (the server loop and the worker loop here; each service's own loops elsewhere).
This one is guarded by a thread lock, and waiters on any loop are served in arrival order.
"""

import asyncio
import contextlib
import threading
from collections import deque
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager


class CrossLoopSemaphore:
    def __init__(self, value: int) -> None:
        super().__init__()
        if value < 1:
            raise ValueError("a semaphore needs at least one permit")
        self._limit = value
        self._free = value
        self._lock = threading.Lock()
        self._waiters: deque[tuple[asyncio.AbstractEventLoop, asyncio.Future[None]]] = deque()

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

    @asynccontextmanager
    async def slot(self) -> AsyncGenerator[None, None]:
        await self.acquire()
        try:
            yield
        finally:
            self.release()
