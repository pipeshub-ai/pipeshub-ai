"""One Redis (or HTTP) client per event loop.

A ``redis.asyncio`` connection belongs to the loop that opened it: a second
loop that borrows it from the pool fails with "Future attached to a different
loop". The indexing service runs two loops (the main one and the consumer's
worker thread), so any client both of them reach has to be held per loop.

The holder is ``app.utils.loop_local.LoopLocal``: one mechanism for every
loop-bound resource. ``get()`` hands out the running loop's client, built by
the factory on first use; a client left over from a closed loop (a worker
thread restarted between a stop() and a start()) is replaced rather than
reused; ``aclose()`` closes each client on its own loop while that loop runs.
"""
from __future__ import annotations

from app.utils.loop_local import LoopLocal

LoopBoundClients = LoopLocal

__all__ = ["LoopBoundClients"]
