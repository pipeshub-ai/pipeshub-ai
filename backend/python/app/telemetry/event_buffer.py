"""Thread-safe, bounded buffer for product-behaviour events awaiting shipment.

Unlike logs, events are individually meaningful (not aggregated). Drained by the
MetricsPusher tick. Bounded so a burst can't exhaust memory.
"""

import threading
from datetime import datetime, timezone
from typing import List, Optional

MAX_EVENTS = 5000


class EventBuffer:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: List[dict] = []

    def enqueue(self, event: dict) -> None:
        with self._lock:
            if len(self._events) >= MAX_EVENTS:
                return  # drop on overflow
            self._events.append(event)

    def drain(self) -> List[dict]:
        with self._lock:
            events = self._events
            self._events = []
            return events

    def size(self) -> int:
        with self._lock:
            return len(self._events)


event_buffer = EventBuffer()


def record_event(event: str, props: Optional[dict] = None) -> None:
    """Record a product-behaviour event (search_performed, agent_run, ...).

    Props may carry identity (email/org/user) and dimensions (connector/feature)
    but must NOT carry document/query content — the collector drops
    content-smelling keys as a backstop.
    """
    event_buffer.enqueue(
        {
            "event": event,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "props": props or {},
        }
    )
