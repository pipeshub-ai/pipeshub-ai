"""A loop-bound resource kept once per event loop.

aiohttp sessions, httpx clients, redis.asyncio pools and asyncio locks bind to the loop that
first uses them: a second loop that borrows one fails with "Future attached to a different
loop". The indexing service runs more than one loop at once (the server loop and the worker
loop its message consumers share), so a client both reach must give each loop its own
resource, and close each one on the loop that owns it.

This is the one per-loop holder; ``app.services.redis.loop_clients.LoopBoundClients`` is the
same class under the name its Redis and HTTP callers use.
"""

import asyncio
import logging
import threading
from collections.abc import Callable, Coroutine
from typing import Any, Generic, TypeVar

from app.utils.loop_bridge import run_on_loop

T = TypeVar("T")

logger = logging.getLogger(__name__)

# How long shutdown waits for another loop to close its resource.
_CLOSE_TIMEOUT_S = 5.0

# Outside any loop a resource is kept per thread, as a synchronous caller on a worker thread
# must not be handed another thread's client.
_Owner = asyncio.AbstractEventLoop | tuple[str, int]


def running_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _current_owner() -> _Owner:
    loop = running_loop()
    return loop if loop is not None else ("thread", threading.get_ident())


class LoopLocal(Generic[T]):
    def __init__(self, factory: Callable[[], T]) -> None:
        super().__init__()
        self._factory = factory
        self._items: dict[_Owner, T] = {}
        # Loops live on different threads, so this is a threading lock.
        self._lock = threading.Lock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def get(self) -> T:
        """This loop's resource, created on first use."""
        owner = _current_owner()
        with self._lock:
            item = self._items.get(owner)
            if item is None:
                # A closed loop can neither use its resource nor close it again; a worker
                # thread restarted between stop() and start() runs a new loop.
                for stale in [o for o in self._items if isinstance(o, asyncio.AbstractEventLoop) and o.is_closed()]:
                    del self._items[stale]
                item = self._factory()
                self._items[owner] = item
            return item

    def replace_current(self) -> T:
        """Swap this loop's resource (closed or broken) for a new one; other loops keep theirs."""
        owner = _current_owner()
        with self._lock:
            item = self._factory()
            self._items[owner] = item
            return item

    async def aclose_all(self, close: Callable[[T], Coroutine[Any, Any, object]]) -> list[Exception]:
        """Close every loop's resource on the loop that owns it, and forget them all.

        A loop that has stopped can no longer run the close, so its resource is only dropped.
        One failing close does not strand the rest. Returns the errors raised while closing,
        for the caller to log.
        """
        with self._lock:
            owned = list(self._items.items())
            self._items.clear()
        current = running_loop()
        errors: list[Exception] = []
        seen: set[int] = set()
        for owner, item in owned:
            if id(item) in seen:
                continue
            seen.add(id(item))
            try:
                if not isinstance(owner, asyncio.AbstractEventLoop) or owner is current:
                    await close(item)
                elif owner.is_running():
                    await run_on_loop(owner, close(item), _CLOSE_TIMEOUT_S)
            except Exception as exc:
                errors.append(exc)
        return errors

    async def aclose(self) -> None:
        """Close every client handed out with its own ``aclose()``, best-effort per client."""
        for exc in await self.aclose_all(lambda client: client.aclose()):  # type: ignore[attr-defined]
            logger.debug("Error closing a loop-bound client: %s", exc)
