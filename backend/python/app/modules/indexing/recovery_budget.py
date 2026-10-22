"""How often stale-record recovery may hand the same record back for indexing.

Recovery republishes a record left IN_PROGRESS past its deadline. Its events
carried no retry tracking id, so the consumer counted their failures under a
new id each time and every recovery started a fresh retry budget: a record
that hung its worker on every attempt was indexed again each recovery
interval, model calls included, for good.

Every event recovery publishes for one version of a record now shares a
tracking id, so their failures add up in the consumer's counter. Recovery also
counts its own hand-backs, in a counter of their own, once each event is
published; that covers attempts that died before a failure could be counted.
Whether to give up is read without counting anything, so a pass that cannot
publish spends nothing. Past either limit the record is ended by an
``ExhaustedRecordSink`` instead, which also moves its queued copies on. A
success clears the counters.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from app.services.messaging.config import messaging_env

if TYPE_CHECKING:
    from logging import Logger

    from app.services.distributed.interface import IRetryTracker


class ExhaustedRecordSink(Protocol):
    """Ends a record stale recovery has given up on. Implemented by ``RecordEventHandler``."""

    async def on_recovery_exhausted(self, record_id: str, *, resume_enrichment: bool) -> None:
        """Mark the record failed (only its enrichment, when it is already
        searchable) and release or advance its queued copies."""
        ...


def recovery_tracking_id(record_id: str, version: object) -> str:
    return f"stale-recovery:{record_id}:{version}"


async def recovery_exhausted(
    tracker: IRetryTracker | None, tracking_id: str, logger: Logger
) -> bool:
    """True when the record has failed or been handed back too often. Counts nothing."""
    if tracker is None:
        return False
    try:
        if await tracker.get_count(tracking_id) >= messaging_env.max_delivery_attempts:
            return True
        return await tracker.get_handbacks(tracking_id) >= messaging_env.redis_max_deliveries
    except Exception as exc:
        # The consumer's own retry budget still applies to the event.
        logger.warning("Could not read recovery budget %s (%r); recovering anyway", tracking_id, exc)
        return False


async def record_handback(tracker: IRetryTracker | None, tracking_id: str, logger: Logger) -> None:
    """Count a hand-back whose event was published. Never raises: the event is already out."""
    if tracker is None:
        return
    try:
        await tracker.record_handback(tracking_id)
    except Exception as exc:
        logger.warning("Could not count the recovery of %s (%r)", tracking_id, exc)
