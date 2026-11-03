"""Reading a consumer group's backlog off Redis Streams.

See ``app.services.messaging.lanes.backlog`` for what the result means.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from app.services.redis.connection_provider import RedisClient

__all__ = ["read_stream_backlog", "read_stream_backlog_detail"]


def _text(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _field(mapping: dict, name: str) -> object:
    """A reply field, whether or not the client decodes responses."""
    return mapping.get(name, mapping.get(name.encode()))


def _entry_ms(entry_id: object) -> float:
    """The millisecond half of a stream id, which Redis stamps with its own clock."""
    return float(_text(entry_id).split("-", 1)[0])


async def read_stream_backlog(
    redis: "RedisClient", group: str, streams: "Iterable[str]"
) -> dict[str, float]:
    """Per stream, when the oldest entry ``group`` has not finished with was added.

    See ``read_stream_backlog_detail``, which also counts each pending list.
    """
    oldest, _pending = await read_stream_backlog_detail(redis, group, streams)
    return oldest


async def read_stream_backlog_detail(
    redis: "RedisClient", group: str, streams: "Iterable[str]"
) -> tuple[dict[str, float], dict[str, int]]:
    """Per stream, when the oldest entry ``group`` has not finished with was added.

    That is the older of two entries: the oldest one delivered but not yet
    acknowledged (the head of the pending list, which is where a buffered,
    parked or in-flight entry sits), and the first one not delivered at all.
    A stream the group has caught up on is left out.

    Three commands per stream, whatever its length. Raises if a stream or its
    group cannot be read, so a partial answer is never mistaken for "caught up".
    Also returns, per stream, how many entries its pending list holds.
    """
    oldest: dict[str, float] = {}
    pending: dict[str, int] = {}
    for stream in streams:
        waiting: list[float] = []

        summary = await redis.xpending(stream, group)  # type: ignore[union-attr]
        pending_head = _field(summary, "min")
        pending[stream] = int(_field(summary, "pending") or 0)
        if pending[stream] and pending_head:
            waiting.append(_entry_ms(pending_head))

        info = next(
            (
                entry
                for entry in await redis.xinfo_groups(stream)  # type: ignore[union-attr]
                if _text(_field(entry, "name")) == group
            ),
            None,
        )
        if info is None:
            raise RuntimeError(f"Consumer group {group} not found on stream {stream}")
        # Looked up rather than read off the group's `lag`: that field is
        # absent before Redis 7.0, null once entries have been deleted, and
        # has been wrong in more than one server release.
        last_delivered = _text(_field(info, "last-delivered-id") or "0-0")
        undelivered = await redis.xrange(  # type: ignore[union-attr]
            stream, min=f"({last_delivered}", max="+", count=1
        )
        if undelivered:
            waiting.append(_entry_ms(undelivered[0][0]))

        if waiting:
            oldest[stream] = min(waiting)
    return oldest, pending
