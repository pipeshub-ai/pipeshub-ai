"""Serializes lazy construction of connector instances.

Three call sites build a connector when ``connectors_map`` misses: the streaming
router and two event-service paths. Each checks the map and then awaits several
times — graph reads, ``init()``, a live connection test — before storing the
result. Without a shared lock every concurrent caller misses the same check and
builds its own instance. Only the last one is stored, but each racer still
serves its own request from its own instance, with its own HTTP client and its
own ``ResiliencePolicy`` — so the connector's configured rate limit is
multiplied by the number of racers and a 429 seen by one instance pauses none of
the others.
"""

import asyncio

_init_locks: dict[str, asyncio.Lock] = {}


def connector_init_lock(connector_id: str) -> asyncio.Lock:
    """Return the process-wide construction lock for ``connector_id``.

    ``setdefault`` is the atomic step — there is no await between the miss and
    the insert, so two callers can never end up holding different locks for the
    same connector.
    """
    lock = _init_locks.get(connector_id)
    if lock is None:
        lock = _init_locks.setdefault(connector_id, asyncio.Lock())
    return lock
