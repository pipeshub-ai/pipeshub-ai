"""What the broker wrappers announce: ``message.published`` and ``message.consumed``.

Called from the places every message passes: the producers' send methods and the
end of a consumer's delivery (``message_log``). The facts are the topic, a
bounded message type, how it ended and how long it took. Never a payload or a key.
"""

from __future__ import annotations

import re
import threading
import time

from app import platform_events

UNKNOWN = "unknown"
OTHER = "other"
MAX_MESSAGE_TYPES = 200

# An event type is a short name set by code (newRecord, connector.sync.started). Anything else is data.
_TYPE_SHAPE = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,63}")
_types: set[str] = set()
_types_lock = threading.Lock()


def message_type(value: object) -> str:
    """The message's ``eventType`` as a value from a bounded set: ``unknown`` without one, ``other`` for a
    value that is not a short name or once ``MAX_MESSAGE_TYPES`` distinct ones were seen."""
    if not isinstance(value, str) or not value:
        return UNKNOWN
    if value in _types:
        return value
    if not _TYPE_SHAPE.fullmatch(value):
        return OTHER
    with _types_lock:
        if len(_types) >= MAX_MESSAGE_TYPES and value not in _types:
            return OTHER
        _types.add(value)
    return value


def type_of(message: object) -> str:
    return message_type(message.get("eventType")) if isinstance(message, dict) else UNKNOWN


def lag_ms(published_at_ms: object) -> int | None:
    """Milliseconds since the broker stamped the message, when it gave a timestamp."""
    if isinstance(published_at_ms, bool) or not isinstance(published_at_ms, (int, float)) or published_at_ms <= 0:
        return None
    return max(0, int(time.time() * 1000 - published_at_ms))


def stream_entry_ms(entry_id: object) -> int | None:
    """The millisecond time a Redis stream entry id (``<ms>-<seq>``) starts with."""
    if isinstance(entry_id, bytes):
        entry_id = entry_id.decode("ascii", "replace")
    head = str(entry_id).split("-", 1)[0]
    return int(head) if head.isdigit() else None


def published(broker: str, topic: str, message: object, *, ok: bool, started: float) -> None:
    """One message was handed to the broker (``ok``) or the broker did not take it. ``started``: ``time.perf_counter()`` before the send."""
    if not platform_events.has_listeners("message.published"):
        return
    platform_events.emit(
        "message.published",
        broker=broker,
        topic=topic,
        message_type=type_of(message),
        outcome="success" if ok else "failure",
        duration_seconds=time.perf_counter() - started,
    )


def consumed(
    broker: str, topic: str, event_type: object, *, outcome: str, duration_seconds: float, lag: int | None
) -> None:
    """One delivery ended. ``outcome``: success, skipped, retry or dead_letter."""
    if not platform_events.has_listeners("message.consumed"):
        return
    platform_events.emit(
        "message.consumed",
        broker=broker,
        topic=topic,
        message_type=message_type(event_type),
        outcome=outcome,
        duration_seconds=duration_seconds,
        lag_ms=lag,
    )
