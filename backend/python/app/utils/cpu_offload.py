"""Move large synchronous CPU work off the event loop that scheduled it.

The indexing pipeline runs every record on a *single* worker-thread event
loop, so any synchronous CPU work in a handler blocks every other in-flight
record on that loop — and, worse, blocks the loop's own timers. That is how a
slow record used to become a Redis outage: the cross-loop lease calls armed
their deadline on this loop, so a loop that stalled past the deadline expired
it and cancelled an in-flight Redis command, which forces redis-py to drop the
connection. The lease calls no longer cross loops, but the stalls were real
work and still hurt throughput and heartbeat liveness.

Only work above a size threshold is offloaded: a thread hop costs a few tens
of microseconds, which is pure loss on the few-KB Jira/Confluence records that
make up most of a connector sync, and a win on a 40 MB PDF.
"""
from __future__ import annotations

import asyncio
import sys
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable

T = TypeVar("T")

# Below this, the work is comfortably shorter than the thread hop that would
# avoid it. Sized for the small structured records connectors emit in bulk.
DEFAULT_OFFLOAD_THRESHOLD_BYTES = 256 * 1024


def _sizeof(value: object) -> int:
    """Best-effort byte size of a payload, for the threshold check only.

    Anything whose size cannot be read cheaply is reported as large: an
    unknown payload is more likely to be a document than a short string, and
    a needless thread hop is far cheaper than a stalled loop. ``maxsize``
    rather than the default threshold, so "unknown" still counts as large
    when the caller raised ``threshold_bytes`` above that default.
    """
    if isinstance(value, (bytes, bytearray, str)):
        return len(value)
    if isinstance(value, memoryview):
        return value.nbytes
    return sys.maxsize


async def offload_if_large(
    fn: "Callable[..., T]",
    *args: object,
    sized_arg: object = None,
    threshold_bytes: int = DEFAULT_OFFLOAD_THRESHOLD_BYTES,
) -> T:
    """Run ``fn(*args)`` in a thread when the payload is big, else inline.

    ``sized_arg`` is the *value* whose size decides — not a byte count. It
    defaults to the first positional argument, which is the payload at every
    call site here; pass it only when the deciding value is not ``args[0]``.
    """
    payload = args[0] if sized_arg is None and args else sized_arg
    if payload is not None and _sizeof(payload) >= threshold_bytes:
        return await asyncio.to_thread(fn, *args)
    return fn(*args)
