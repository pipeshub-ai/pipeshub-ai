"""Hold time that a record's rate-limit deferral does not count against its 24 h allowance.

A record the source rate-limits waits QUEUED with ``rateLimitedSince`` on the record (no new field is added to the record). While an
operator hold (the fleet scope of the hold ledger, ``sg:hold:ledger:{fleet}``) keeps indexing from spending, that waiting is not
the source's fault, so it is subtracted exactly as a stage job's pause does: the ledger at the start of the run is kept in a Redis
key beside the pause budgets (``messaging:deferral:{<record id>}``), and ``held = ledger now - ledger at since``.

Best effort: with no fleet Redis bound, or when it cannot be read, nothing is subtracted and the record keeps its
allowance as before.
"""

from __future__ import annotations

import logging

from app.services.llm_gateway.fleet_breaker import FleetBreaker
from app.services.llm_gateway.fleet_redis import get_fleet_redis

logger = logging.getLogger(__name__)
FLEET_SCOPE = "fleet"
TTL_SECONDS = 8 * 24 * 3600


async def held_ms(record_id: str, since_ms: int) -> int:
    """Milliseconds of fleet hold since the run of waits that began at ``since_ms`` (0 for a run seen for the first time)."""
    handle = get_fleet_redis()
    if handle is None:
        return 0
    try:
        key = handle.key(f"messaging:deferral:{{{record_id}}}")
        client = handle.client()
        ledger_now = await FleetBreaker(handle).ledger(FLEET_SCOPE)
        stored = await client.hgetall(key)
        stored_since = int(stored.get("since_ms", stored.get(b"since_ms", 0)) or 0) if stored else 0
        if not stored or stored_since != since_ms:
            await client.hset(key, mapping={"since_ms": since_ms, "ledger_at_since": ledger_now})
            await client.expire(key, TTL_SECONDS)
            return 0
        at_since = int(stored.get("ledger_at_since", stored.get(b"ledger_at_since", 0)) or 0)
        return max(0, ledger_now - at_since)
    except Exception:
        logger.warning("could not read the hold ledger of record %s; no hold is subtracted", record_id, exc_info=True)
        return 0


async def clear(record_id: str) -> None:
    handle = get_fleet_redis()
    if handle is None:
        return
    try:
        await handle.client().delete(handle.key(f"messaging:deferral:{{{record_id}}}"))
    except Exception:
        logger.debug("could not clear the deferral ledger of record %s", record_id, exc_info=True)
