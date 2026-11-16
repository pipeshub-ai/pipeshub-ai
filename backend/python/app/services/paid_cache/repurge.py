"""The delayed second purge of a deleted record's stored paid results.

The delete marker in Redis refuses most late writes, but Redis can be lost (a restart without persistence, a
failover) while a parse that outlived its record is still running. So the delete handler also publishes a
durable ``purgePaidResults`` event to the record events topic, due ``REPURGE_DELAY_S`` after the delete: longer
than a lock lives plus the longest signed upload. The consumers hold an event until its ``_retry_not_before``
(the same field the stored-document cleanup uses), and the broker redelivers it until the purge succeeds.
Whatever landed in between is removed whatever happened to Redis.
"""

import time
from typing import Any

from app.config.constants.arangodb import EventTypes
from app.services.paid_cache.locks import REPURGE_DELAY_S


def repurge_event_payload(org_id: str, record_id: str, *, now: float | None = None) -> dict[str, Any]:
    """The payload of the delayed purge: the org and record, and the time before which it must not run."""
    return {
        "orgId": org_id,
        "recordId": record_id,
        "_retry_not_before": (time.time() if now is None else now) + REPURGE_DELAY_S,
    }


async def schedule_repurge(producer: Any, org_id: str, record_id: str, *, now: float | None = None) -> None:  # noqa: ANN401 - the messaging producer
    """Publishes the delayed purge. Raises when it cannot be published: the delete event is then redelivered."""
    from app.services.messaging.kafka.topics import Topic

    if producer is None:
        raise RuntimeError("No messaging producer configured; cannot schedule the paid-result re-purge")
    await producer.send_event(
        topic=Topic.RECORD_EVENTS.value,
        event_type=EventTypes.PURGE_PAID_RESULTS.value,
        payload=repurge_event_payload(org_id, record_id, now=now),
        key=record_id,
    )
