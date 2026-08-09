"""AppEvent consumer — listens to Topic.APP_EVENTS and calls TaskEngine.fire_event.

Lives in the query service (port 8000). One instance per process.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.services.events.models import CHAIN_DEPTH_CAP

if TYPE_CHECKING:
    from app.services.messaging.config import StreamMessage
    from app.services.tasks.application.engine import TaskEngine

__all__ = ["AppEventConsumer"]

logger = logging.getLogger(__name__)


class AppEventConsumer:
    """Consumes APP_EVENTS messages and fans out to TaskEngine.fire_event."""

    def __init__(self, *, task_engine: "TaskEngine") -> None:
        self._engine = task_engine

    async def handle(self, message: "StreamMessage") -> bool:
        """Message handler for Topic.APP_EVENTS.

        Returns True to commit. False is returned only for a failure that
        redelivery could plausibly fix (the stores or broker being down),
        because a malformed or capped event will fail identically forever
        and would otherwise be redelivered until it poisons the stream.

        Redelivery is safe: the fan-out is keyed on the provider's
        `dedupe_key`, so a retried event re-uses the runs that already
        exist instead of starting duplicates.
        """
        payload = message.payload
        org_id = payload.get("org_id", "")
        event_type = payload.get("event_type", "")
        event_payload = payload.get("payload", {})
        dedupe_key = payload.get("dedupe_key", "")
        chain_depth = int(payload.get("chain_depth", 0))

        if chain_depth >= CHAIN_DEPTH_CAP:
            logger.warning(
                "APP_EVENT chain_depth cap reached: event_type=%s org=%s dedupe_key=%s",
                event_type, org_id, dedupe_key,
            )
            return True

        if not org_id or not event_type:
            logger.error("APP_EVENT missing org_id or event_type: %s", payload)
            return True

        # `_dedupe_key` is what makes a redelivery land on the existing runs:
        # `fire_event` derives each dispatch's idempotency key from it.
        enriched_payload = {
            **event_payload,
            "_dedupe_key": dedupe_key,
            "_chain_depth": chain_depth + 1,
            "event_type": event_type,
        }

        try:
            runs = await self._engine.fire_event(
                org_id=org_id,
                event_type=event_type,
                payload=enriched_payload,
            )
        except Exception:
            logger.exception(
                "APP_EVENT fan-out failed, will retry on redelivery: "
                "event_type=%s org=%s dedupe_key=%s",
                event_type, org_id, dedupe_key,
            )
            return False

        logger.info(
            "APP_EVENT fanned out: event_type=%s org=%s dedupe_key=%s runs=%d",
            event_type, org_id, dedupe_key, len(runs),
        )
        return True
