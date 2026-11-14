"""Reading a consumer group's backlog off Redis Streams.

See ``app.services.messaging.lanes.backlog`` for what the result means.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.services.messaging.lanes.backlog import StalePending

if TYPE_CHECKING:
    from collections.abc import Iterable

    from app.services.redis.connection_provider import RedisClient

__all__ = [
    "PendingBounds",
    "StreamBacklog",
    "read_stream_backlog",
    "read_stream_backlog_detail",
]


@dataclass(frozen=True)
class PendingBounds:
    """When a pending entry stops counting as work the group is still doing.

    ``stale_owner_after_ms``: its consumer has not touched the group for this
    long (it stopped; a live one polls every few seconds). ``max_age_ms``: it
    was published this long ago. Either may be None for no bound.
    """

    stale_owner_after_ms: int | None = None
    max_age_ms: int | None = None

    @property
    def active(self) -> bool:
        return bool(self.stale_owner_after_ms) or bool(self.max_age_ms)


@dataclass(frozen=True)
class StreamBacklog:
    """Per stream (each map leaves out a stream with nothing in it)."""

    # The oldest entry the group has not finished with, whoever holds it.
    oldest_ms: dict[str, float] = field(default_factory=dict)
    # Entries delivered and not acknowledged.
    pending: dict[str, int] = field(default_factory=dict)
    # Publish time of the head of the pending list.
    oldest_pending_ms: dict[str, float] = field(default_factory=dict)
    # As ``oldest_ms``, without the pending entries the bounds discounted.
    oldest_live_ms: dict[str, float] = field(default_factory=dict)
    stale: dict[str, StalePending] = field(default_factory=dict)


def _text(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _field(mapping: dict, name: str) -> object:
    """A reply field, whether or not the client decodes responses."""
    return mapping.get(name, mapping.get(name.encode()))


def _entry_ms(entry_id: object) -> float:
    """The millisecond half of a stream id, which Redis stamps with its own clock."""
    return float(_text(entry_id).split("-", 1)[0])


def _owners(summary: dict) -> dict[str, int]:
    """Consumer name -> pending count, from an XPENDING summary reply."""
    owners: dict[str, int] = {}
    for consumer in _field(summary, "consumers") or []:
        if isinstance(consumer, dict):
            name, count = _field(consumer, "name"), _field(consumer, "pending")
        else:
            name, count = consumer[0], consumer[1]
        if int(count or 0):
            owners[_text(name)] = int(count)
    return owners


async def read_stream_backlog(
    redis: "RedisClient", group: str, streams: "Iterable[str]"
) -> dict[str, float]:
    """Per stream, when the oldest entry ``group`` has not finished with was added.

    See ``read_stream_backlog_detail``, which also counts each pending list.
    """
    return (await read_stream_backlog_detail(redis, group, streams)).oldest_ms


async def _redis_now_ms(redis: "RedisClient") -> float:
    seconds, microseconds = await redis.time()  # type: ignore[union-attr]
    return int(seconds) * 1000 + int(microseconds) / 1000


async def _live_pending_head(
    redis: "RedisClient",
    stream: str,
    group: str,
    summary: dict,
    bounds: PendingBounds,
    now_ms: float,
) -> tuple[float | None, StalePending | None]:
    """The oldest pending entry the bounds still count, and what they discounted.

    Costs one ``XINFO CONSUMERS``; a further ``XPENDING`` per live owner only
    when something is discounted, to find where the counted entries begin.
    """
    head_ms = _entry_ms(_field(summary, "min"))
    owners = _owners(summary)
    idle_by_name = {
        _text(_field(info, "name")): int(_field(info, "idle") or 0)
        for info in await redis.xinfo_consumers(stream, group)  # type: ignore[union-attr]
    }
    stopped = {
        name: (count, idle_by_name.get(name, 0))
        for name, count in owners.items()
        if bounds.stale_owner_after_ms and idle_by_name.get(name, 0) > bounds.stale_owner_after_ms
    }
    floor_ms = now_ms - bounds.max_age_ms if bounds.max_age_ms else None
    too_old = floor_ms is not None and head_ms < floor_ms
    if not stopped and not too_old:
        return head_ms, None

    start = f"{int(floor_ms)}-0" if too_old else "-"
    heads: list[float] = []
    for name in owners:
        if name in stopped:
            continue
        first = await redis.xpending_range(  # type: ignore[union-attr]
            stream, group, min=start, max="+", count=1, consumername=name
        )
        if first:
            heads.append(_entry_ms(_field(first[0], "message_id")))
    return min(heads, default=None), StalePending(stopped, head_ms, too_old)


async def read_stream_backlog_detail(
    redis: "RedisClient",
    group: str,
    streams: "Iterable[str]",
    bounds: PendingBounds | None = None,
) -> StreamBacklog:
    """Per stream, when the oldest entry ``group`` has not finished with was added.

    That is the older of two entries: the oldest one delivered but not yet
    acknowledged (the head of the pending list, which is where a buffered,
    parked or in-flight entry sits), and the first one not delivered at all.
    A stream the group has caught up on is left out.

    Three commands per stream, whatever its length. Raises if a stream or its
    group cannot be read, so a partial answer is never mistaken for "caught up".
    Also returns, per stream, how many entries its pending list holds.

    With ``bounds``, ``oldest_live_ms`` also leaves out pending entries held
    by a consumer that stopped or published longer ago than the cap: such an
    entry is waiting for a reclaim, and is no sign the group will reach a
    later event any time soon. That costs a ``TIME`` per read and an
    ``XINFO CONSUMERS`` per stream with entries pending.
    """
    result = StreamBacklog()
    now_ms: float | None = None
    for stream in streams:
        summary = await redis.xpending(stream, group)  # type: ignore[union-attr]
        pending_head = _field(summary, "min")
        result.pending[stream] = int(_field(summary, "pending") or 0)
        head_ms = live_head_ms = None
        if result.pending[stream] and pending_head:
            head_ms = live_head_ms = _entry_ms(pending_head)
            result.oldest_pending_ms[stream] = head_ms
            if bounds is not None and bounds.active:
                if now_ms is None:
                    now_ms = await _redis_now_ms(redis)
                live_head_ms, stale = await _live_pending_head(
                    redis, stream, group, summary, bounds, now_ms
                )
                if stale is not None:
                    result.stale[stream] = stale

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
        undelivered_ms = _entry_ms(undelivered[0][0]) if undelivered else None

        oldest = [ms for ms in (head_ms, undelivered_ms) if ms is not None]
        if oldest:
            result.oldest_ms[stream] = min(oldest)
        live = [ms for ms in (live_head_ms, undelivered_ms) if ms is not None]
        if live:
            result.oldest_live_ms[stream] = min(live)
    return result
