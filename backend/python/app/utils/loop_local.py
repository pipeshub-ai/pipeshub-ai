"""A loop-bound resource kept once per event loop.

aiohttp sessions, httpx clients, redis.asyncio pools and asyncio locks bind to the loop that
first uses them. The indexing service runs several loops at once (the server loop and one
worker loop per message consumer), so a client shared across them must give each loop its own
resource, and close each one on the loop that owns it.
"""

import asyncio
import threading
from collections.abc import Callable, Coroutine
from typing import Any, Generic, TypeVar

T = TypeVar("T")

# How long shutdown waits for another loop to close its resource.
_CLOSE_TIMEOUT_S = 5.0


def running_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


class LoopLocal(Generic[T]):
    def __init__(self, factory: Callable[[], T]) -> None:
        super().__init__()
        self._factory = factory
        self._items: dict[asyncio.AbstractEventLoop | None, T] = {}
        # Loops live on different threads, so this is a threading lock.
        self._lock = threading.Lock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def get(self) -> T:
        """This loop's resource, created on first use."""
        loop = running_loop()
        with self._lock:
            item = self._items.get(loop)
            if item is None:
                # A closed loop can neither use its resource nor close it again.
                for stale in [owner for owner in self._items if owner is not None and owner.is_closed()]:
                    del self._items[stale]
                item = self._factory()
                self._items[loop] = item
            return item

    def replace_current(self) -> T:
        """Swap this loop's resource (closed or broken) for a new one; other loops keep theirs."""
        loop = running_loop()
        with self._lock:
            item = self._factory()
            self._items[loop] = item
            return item

    async def aclose_all(self, close: Callable[[T], Coroutine[Any, Any, object]]) -> list[Exception]:
        """Close every loop's resource on the loop that owns it, and forget them all.

        A loop that has stopped can no longer run the close, so its resource is only dropped.
        Returns the errors raised while closing, for the caller to log.
        """
        with self._lock:
            owned = list(self._items.items())
            self._items.clear()
        current = running_loop()
        errors: list[Exception] = []
        for owner, item in owned:
            try:
                if owner is None or owner is current:
                    await close(item)
                elif owner.is_running():
                    future = asyncio.run_coroutine_threadsafe(close(item), owner)
                    await asyncio.wait_for(asyncio.wrap_future(future), timeout=_CLOSE_TIMEOUT_S)
            except Exception as exc:
                errors.append(exc)
        return errors
