"""The delayed purges of a deleted record's (or org's) stored paid results: a chain of hops.

The delete marker in Redis refuses most late writes, but Redis can be lost (a restart without persistence, a
failover) while a parse that outlived its record is still running, and a parse can run for ``STAGE_DEADLINE_S``
(each new page takes a fresh lock, and the manifest takes none). So the delete handler also publishes a
durable ``purgePaidResults`` event to the record events topic, due ``REPURGE_HOP_S`` after the delete: longer
than a lock lives plus the longest signed upload. The consumers hold an event until its ``_retry_not_before``
(the same field the stored-document cleanup uses), and the broker redelivers it until the purge succeeds.

Each hop purges, then publishes the next one (same payload, ``hop`` + 1, ``_retry_not_before`` one hop later),
and stops once ``repurge_horizon_s()`` has passed since the delete (``deletedAt``). A hop is published, and so
signed, when the hop before it runs, so no queued message waits longer than ``REPURGE_HOP_S`` before delivery:
every one verifies well inside ``ENVELOPE_MAX_AGE_SECONDS`` (900 s by default) even with 600 s of consumer lag.
An org purge schedules one chain for the org (``scope: "org"``), not one per record. The org a consumer purges
is the verified envelope's.
"""

import math
import time
from typing import Any

from app.config.constants.arangodb import EventTypes
from app.services.paid_cache.locks import REPURGE_HOP_S, repurge_horizon_s
from app.utils.loop_bridge import run_on_loop

#: ``scope`` of a purge event that covers an org rather than one record.
ORG_SCOPE = "org"


def repurge_event_payload(org_id: str, record_id: str, *, now: float | None = None) -> dict[str, Any]:
    """The first hop for a record: the org and record, when it was deleted, and when the hop is due."""
    now = time.time() if now is None else now
    return {"orgId": org_id, "recordId": record_id, "deletedAt": now, "hop": 1, "_retry_not_before": now + REPURGE_HOP_S}


def org_repurge_event_payload(org_id: str, *, now: float | None = None) -> dict[str, Any]:
    """The first hop for a whole org: no record, ``scope`` says so."""
    now = time.time() if now is None else now
    return {"orgId": org_id, "scope": ORG_SCOPE, "deletedAt": now, "hop": 1, "_retry_not_before": now + REPURGE_HOP_S}


def next_hop_payload(org_id: str, payload: dict[str, Any], *, now: float | None = None) -> dict[str, Any] | None:
    """The hop after the one that carries ``payload``, or None when the chain is over.

    None for an event with no valid ``deletedAt`` (an older single-hop event), and once more than
    ``repurge_horizon_s()`` has passed since the delete. ``deletedAt`` is never taken from the future. The org is
    the verified envelope's (``org_id``), never a field of the body.
    """
    now = time.time() if now is None else now
    deleted_at = payload.get("deletedAt")
    hop = payload.get("hop")
    if isinstance(deleted_at, bool) or not isinstance(deleted_at, int | float) or not math.isfinite(deleted_at):
        return None
    deleted_at = min(float(deleted_at), now)
    if now - deleted_at > repurge_horizon_s():
        return None
    return {
        "orgId": org_id,
        **({"recordId": payload["recordId"]} if payload.get("recordId") else {}),
        **({"scope": payload["scope"]} if payload.get("scope") else {}),
        "deletedAt": deleted_at,
        "hop": (hop if isinstance(hop, int) and not isinstance(hop, bool) else 1) + 1,
        "_retry_not_before": now + REPURGE_HOP_S,
    }


async def _publish(
    producer: Any, payload: dict[str, Any], key: str, loop: Any,  # noqa: ANN401 - the messaging producer and its owning loop
) -> None:
    from app.services.messaging.kafka.topics import Topic

    if producer is None:
        raise RuntimeError("No messaging producer configured; cannot schedule the paid-result re-purge")
    await run_on_loop(
        loop,
        producer.send_event(
            topic=Topic.RECORD_EVENTS.value,
            event_type=EventTypes.PURGE_PAID_RESULTS.value,
            payload=payload,
            key=key,
        ),
    )


async def schedule_org_repurge(producer: Any, org_id: str, *, now: float | None = None, loop: Any = None) -> None:  # noqa: ANN401 - the messaging producer
    """The first hop of the chain for a deleted org (not one per record). Raises when it cannot be published."""
    await _publish(producer, org_repurge_event_payload(org_id, now=now), org_id, loop)


async def schedule_repurge(producer: Any, org_id: str, record_id: str, *, now: float | None = None, loop: Any = None) -> None:  # noqa: ANN401 - the messaging producer
    """The first hop of the chain for a record. Raises when it cannot be published: the delete event is redelivered."""
    await _publish(producer, repurge_event_payload(org_id, record_id, now=now), record_id, loop)


async def schedule_next_hop(
    producer: Any, org_id: str, payload: dict[str, Any], *, now: float | None = None, loop: Any = None,  # noqa: ANN401
) -> bool:
    """Publishes the hop after ``payload``'s (to ``org_id``, the verified envelope's org). False when the chain is over.

    Raises when it cannot be published: the hop is redelivered, and purging again is harmless.
    """
    following = next_hop_payload(org_id, payload, now=now)
    if following is None:
        return False
    await _publish(producer, following, str(following.get("recordId") or org_id), loop)
    return True
