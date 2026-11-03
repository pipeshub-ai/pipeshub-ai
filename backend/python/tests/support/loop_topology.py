"""The indexing service's two-loop topology, for tests of loop-bound clients.

The indexing consumers run message handlers on a worker thread with its own
event loop (``indexing-worker``) while the clients they share are started on
the main loop. A mock cannot fail the way that goes wrong in production: the
error ("Future attached to a different loop") comes from a real socket's
reader being bound to one loop and awaited from another. So these helpers give
a test a real Redis-protocol server over TCP and a second running loop in a
thread, submitted to the way the consumer submits to it.
"""
from __future__ import annotations

import asyncio
import threading
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, TypeVar

from fakeredis import TcpFakeServer

if TYPE_CHECKING:
    from collections.abc import Coroutine, Iterator

T = TypeVar("T")


@contextmanager
def redis_tcp_server() -> Iterator[tuple[str, int]]:
    """A Redis-protocol server on a real localhost socket; yields (host, port)."""
    server = TcpFakeServer(("127.0.0.1", 0), server_type="redis")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        yield str(host), int(port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@contextmanager
def worker_loop() -> Iterator[asyncio.AbstractEventLoop]:
    """A second running loop in its own thread, like the consumer's worker loop."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, name="indexing-worker_0", daemon=True)
    thread.start()
    started = threading.Event()
    loop.call_soon_threadsafe(started.set)
    assert started.wait(5), "the worker loop never started"
    try:
        yield loop
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        loop.close()


async def on_loop(loop: asyncio.AbstractEventLoop, coro: "Coroutine[Any, Any, T]") -> T:
    """Run ``coro`` on ``loop`` and await it without blocking the caller's loop."""
    return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coro, loop))
