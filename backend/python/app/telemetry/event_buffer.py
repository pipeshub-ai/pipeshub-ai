"""Thread-safe, bounded buffer for product-behaviour events awaiting shipment.

Unlike logs, events are individually meaningful (not aggregated). Drained by the
MetricsPusher tick. Bounded so a burst can't exhaust memory.
"""

import threading
from datetime import datetime, timezone
from typing import Any

from app.telemetry.flag import set_collection_consent, telemetry_collecting

MAX_EVENTS = 5000


class EventBuffer:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: list[dict[str, Any]] = []

    def enqueue(self, event: dict) -> None:
        with self._lock:
            # Checked under the lock: an event that raced an opt-out cannot outlive switch_off's drop.
            if not telemetry_collecting() or len(self._events) >= MAX_EVENTS:
                return  # opted out, or drop on overflow
            self._events.append(event)

    def switch_off(self) -> None:
        """Withdraw consent and drop every buffered event, atomically with enqueue."""
        with self._lock:
            set_collection_consent(False)
            self._events = []

    def drain(self) -> list[dict[str, Any]]:
        with self._lock:
            events = self._events
            self._events = []
            return events

    def size(self) -> int:
        with self._lock:
            return len(self._events)


event_buffer = EventBuffer()


def record_event(event: str, props: dict[str, Any] | None = None) -> None:
    """Record a product-behaviour event (search_performed, agent_run, ...).

    Props may carry identity (email/org/user) and dimensions (connector/feature)
    but must NOT carry document/query content — the collector drops
    content-smelling keys as a backstop.
    """
    if not telemetry_collecting():
        return
    event_buffer.enqueue(
        {
            "event": event,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "props": props or {},
        }
    )
