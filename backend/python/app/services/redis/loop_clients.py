"""One Redis client per event loop.

A ``redis.asyncio`` connection belongs to the loop that opened it: a second
loop that borrows it from the pool fails with "Future attached to a different
loop". The indexing service runs two loops (the main one and the consumer's
worker thread), so any client both of them reach has to be held per loop.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import TYPE_CHECKING, Generic, TypeVar

from app.utils.loop_bridge import run_on_loop

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

_CLOSE_TIMEOUT_SECONDS = 5.0

C = TypeVar("C")


class LoopBoundClients(Generic[C]):
    """Hands out the client for the running loop, built by ``factory`` on first use.

    Keyed by thread, with the bound loop stored alongside so a client left over
    from a closed loop (a worker thread restarted between a stop() and a
    start()) is replaced rather than reused.
    """

    def __init__(self, factory: Callable[[], C]) -> None:
        self._factory = factory
        self._lock = threading.Lock()
        self._clients: dict[int, tuple[C, asyncio.AbstractEventLoop | None]] = {}

    def get(self) -> C:
        thread_id = threading.get_ident()
        try:
            current_loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        with self._lock:
            existing = self._clients.get(thread_id)
            if existing is not None:
                client, bound_loop = existing
                stale = bound_loop is not None and (
                    bound_loop.is_closed()
                    or (current_loop is not None and current_loop is not bound_loop)
                )
                if not stale:
                    return client
                logger.debug("Discarding stale Redis client for thread %s", thread_id)
                del self._clients[thread_id]

            client = self._factory()
            self._clients[thread_id] = (client, current_loop)
            return client

    def __len__(self) -> int:
        with self._lock:
            return len(self._clients)

    async def aclose(self) -> None:
        """Close every distinct client handed out, best-effort per client.

        Each is closed on its own loop while that loop still runs, since
        closing it from another loop fails the same way using it does. A
        client bound to an already-closed loop cannot be awaited, and one
        failing to close must not strand the rest.
        """
        with self._lock:
            entries = list(self._clients.values())
            self._clients.clear()
        seen: set[int] = set()
        for client, bound_loop in entries:
            if id(client) in seen:
                continue
            seen.add(id(client))
            try:
                owner = bound_loop if bound_loop is not None and bound_loop.is_running() else None
                await run_on_loop(owner, client.aclose(), _CLOSE_TIMEOUT_SECONDS)  # type: ignore[attr-defined]
            except Exception as exc:
                logger.debug("Error closing Redis client: %s", exc)
