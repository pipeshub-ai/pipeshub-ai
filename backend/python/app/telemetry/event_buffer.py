"""Thread-safe, bounded buffer for product-behaviour events awaiting shipment.

Unlike logs, events are individually meaningful (not aggregated). Drained by the
MetricsPusher tick. Bounded so a burst can't exhaust memory.
"""

import re
import threading
from datetime import datetime, timezone
from typing import Any

from app.telemetry.flag import telemetry_enabled

MAX_EVENTS = 5000

# Props that would say whose event it is; dropped before the event can leave the install.
IDENTIFYING_PROP = re.compile(r"email|domain|full_?name|org_?name", re.IGNORECASE)


class EventBuffer:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: list[dict[str, Any]] = []

    def enqueue(self, event: dict) -> None:
        with self._lock:
            if len(self._events) >= MAX_EVENTS:
                return  # drop on overflow
            self._events.append(event)

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

    Props carry opaque org/user ids and dimensions (connector/feature), never an
    email, an email domain or a name (dropped here), nor document/query content
    (the collector drops content-smelling keys as a backstop).
    """
    if not telemetry_enabled():
        return
    event_buffer.enqueue(
        {
            "event": event,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "props": {k: v for k, v in (props or {}).items() if not IDENTIFYING_PROP.search(k)},
        }
    )
