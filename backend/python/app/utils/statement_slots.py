"""A limit on the statements in flight against one connector's database.

Readers outside the sync (profiling, background jobs, agents) hold a slot per
statement, so the source database sees at most ``MAX_STATEMENTS_IN_FLIGHT`` of
them from one process.

Per event loop: an asyncio semaphore belongs to one loop, and a process may
read the same source from more than one loop.
"""

from __future__ import annotations

import asyncio
import threading
import weakref

# A Snowflake warehouse runs about 8 statements at once and queues the rest,
# its client's deadline counting queued time; a MariaDB statement here is a
# connection and a thread of its own.
MAX_STATEMENTS_IN_FLIGHT = 4

_slots: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Semaphore]] = (
    weakref.WeakKeyDictionary()
)
_slots_lock = threading.Lock()


def statement_slots(connector_id: str) -> asyncio.Semaphore:
    """The limit on statements in flight for this connector, on the running loop."""
    loop = asyncio.get_running_loop()
    with _slots_lock:
        by_connector = _slots.setdefault(loop, {})
        if connector_id not in by_connector:
            by_connector[connector_id] = asyncio.Semaphore(MAX_STATEMENTS_IN_FLIGHT)
        return by_connector[connector_id]
