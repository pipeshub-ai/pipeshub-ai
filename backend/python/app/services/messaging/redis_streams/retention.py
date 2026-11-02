"""Trimming Redis Streams behind their consumers, never ahead of them.

Producers do not cap a stream on write (``XADD MAXLEN`` ignores consumer
progress and silently drops events nobody has read). Instead one replica,
under a Redis leader lease, periodically runs ``XTRIM MINID ~ <safe id>``,
where the safe id is the lowest point any consumer group still needs:

* its oldest pending (delivered, un-acked) entry, or
* its last-delivered id when nothing is pending.

A stream with no consumer group yet has nothing to be behind, so it is capped at
``no_group_maxlen`` entries (``XTRIM MAXLEN ~``), as every stream was before. So is a stream
whose every group is unread (no consumer has joined and the cursor is still ``0-0``), such as
a group made only so another pool's backlog can be measured. A stream longer than
``alert_length``, or a group with no live consumer, logs a warning naming the
group, so a stuck backlog is visible instead of lost. Groups are never deleted.
Redis older than 6.2 has no ``MINID``: that is logged once and trimming stops.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Protocol

from redis.exceptions import ResponseError

from app.services.messaging.config import MessageBrokerType, Topic

if TYPE_CHECKING:
    from collections.abc import Iterable
    from logging import Logger

    from app.services.redis.connection_provider import RedisClient

DEFAULT_INTERVAL_SECONDS = 60.0
DEFAULT_ALERT_LENGTH = 500_000
LEADER_KEY = "redis_streams:retention:leader"
_WARN_EVERY_SECONDS = 600.0
# Streams the Node services produce that have no entry in the Python ``Topic`` enum.
_NODE_ONLY_TOPICS = ("token-events", "mail-events")
# Capped on write by its producer and never consumed.
_UNTRIMMED_TOPICS = ("health-check",)


def retained_streams() -> list[str]:
    """Every stream a service produces and a group may consume, lanes included.

    Derived from the topic and lane configuration, never SCAN, so a topic added
    to ``Topic`` is covered; a Node-only topic must be added to ``_NODE_ONLY_TOPICS``
    (a test cross-checks the Node ``BrokerTopic`` enum).
    """
    from app.services.messaging.messaging_factory import lane_topics_for

    topics = [t.value for t in Topic if t.value not in _UNTRIMMED_TOPICS] + list(_NODE_ONLY_TOPICS)
    return [stream for topic in topics for stream in lane_topics_for(topic, MessageBrokerType.REDIS)]


class _Lease(Protocol):
    async def try_acquire(self) -> bool: ...


def _text(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _field(mapping: dict, name: str) -> object:
    return mapping.get(name, mapping.get(name.encode()))


def _id_key(entry_id: str) -> tuple[int, int]:
    ms, _, seq = entry_id.partition("-")
    return int(ms), int(seq or 0)


class StreamRetention:
    def __init__(
        self,
        redis: "RedisClient",
        logger: "Logger",
        lease: _Lease,
        alert_length: int = DEFAULT_ALERT_LENGTH,
        no_group_maxlen: int = DEFAULT_ALERT_LENGTH,
    ) -> None:
        self._redis = redis
        self._logger = logger
        self._lease = lease
        self._alert_length = alert_length
        self._no_group_maxlen = no_group_maxlen
        self._minid_unsupported = False
        self._last_warned: dict[str, float] = {}

    def _warn(self, key: str, message: str, *args: object, level: int = logging.WARNING) -> None:
        """Log at most once per ``_WARN_EVERY_SECONDS`` per key: a pass runs every minute."""
        now = time.monotonic()
        if now - self._last_warned.get(key, -_WARN_EVERY_SECONDS) >= _WARN_EVERY_SECONDS:
            self._last_warned[key] = now
            self._logger.log(level, message, *args)

    def report_failure(self, error: Exception) -> None:
        self._warn("pass-failed", "Stream retention pass failed: %s: %s", type(error).__name__, error, level=logging.ERROR)

    async def _group_floors(self, stream: str, groups: list[dict]) -> dict[str, str]:
        floors: dict[str, str] = {}
        for group in groups:
            name = _text(_field(group, "name"))
            floor = _text(_field(group, "last-delivered-id") or "0-0")
            summary = await self._redis.xpending(stream, name)  # type: ignore[union-attr]
            head = _field(summary, "min")
            if int(_field(summary, "pending") or 0) and head:
                floor = min(floor, _text(head), key=_id_key)
            floors[name] = floor
        return floors

    async def safe_id(self, stream: str, groups: list[dict] | None = None) -> str | None:
        """The id below which no consumer group needs entries, or None to keep all."""
        if groups is None:
            groups = await self._redis.xinfo_groups(stream)  # type: ignore[union-attr]
        return self._lowest(await self._group_floors(stream, groups))

    @staticmethod
    def _unread(group: dict, floors: dict[str, str]) -> bool:
        """No consumer has joined and nothing was delivered: the group holds nothing back yet."""
        return int(_field(group, "consumers") or 0) == 0 and floors[_text(_field(group, "name"))] == "0-0"

    @staticmethod
    def _lowest(floors: dict[str, str]) -> str | None:
        """The lowest floor of any group: nothing at or above it may be trimmed."""
        return min(floors.values(), key=_id_key) if floors else None

    async def _trim(self, stream: str, floor: str) -> int:
        try:
            return int(await self._redis.xtrim(stream, minid=floor, approximate=True))  # type: ignore[union-attr]
        except ResponseError as e:
            # Only the parser rejecting MINID (Redis < 6.2) is permanent. READONLY
            # (failover), LOADING, WRONGTYPE etc. are retried by the next pass.
            if "syntax error" not in str(e).lower():
                raise
            self._minid_unsupported = True
            self._logger.error(
                "XTRIM MINID failed (%s). Redis 6.2 or newer is required to trim streams behind "
                "consumers; trimming is disabled until restart and streams will grow without bound.",
                e,
            )
            return 0

    async def trim_once(self, streams: "Iterable[str]") -> dict[str, int]:
        """One pass; returns entries trimmed per stream. A no-op unless this replica leads."""
        if not await self._lease.try_acquire():
            return {}
        trimmed: dict[str, int] = {}
        for stream in streams:
            try:
                groups = await self._redis.xinfo_groups(stream)  # type: ignore[union-attr]
                floors = await self._group_floors(stream, groups)
                floor = self._lowest(floors)
                if not floors or all(self._unread(g, floors) for g in groups):
                    trimmed[stream] = int(
                        await self._redis.xtrim(stream, maxlen=self._no_group_maxlen, approximate=True)  # type: ignore[union-attr]
                    )
                elif not self._minid_unsupported and floor and floor != "0-0":
                    trimmed[stream] = await self._trim(stream, floor)
                length = int(await self._redis.xlen(stream))  # type: ignore[union-attr]
                self._audit(stream, groups, floors, length)
            except Exception as e:
                # One stream's error (missing, failover, wrong type) must not stop the others.
                if "no such key" in str(e).lower():
                    self._logger.debug("Stream %s does not exist yet", stream)
                else:
                    self._warn(f"skip:{stream}", "Stream retention skipped %s: %s: %s", stream, type(e).__name__, e)
        return trimmed

    def _audit(self, stream: str, groups: list[dict], floors: dict[str, str], length: int) -> None:
        over = length > self._alert_length
        if over:
            self._warn(
                f"len:{stream}",
                "Redis stream %s holds %d entries (alert at %d); consumers are behind "
                "and Redis memory is growing",
                stream, length, self._alert_length,
            )
        lowest = self._lowest(floors)
        capped = bool(groups) and all(self._unread(g, floors) for g in groups)
        for group in groups:
            name = _text(_field(group, "name"))
            if capped:
                self._warn(
                    f"unread:{stream}:{name}",
                    "Consumer group %s on stream %s has no consumers and has read nothing; "
                    "the stream is capped at %d entries until a consumer reads it",
                    name, stream, self._no_group_maxlen,
                )
            elif int(_field(group, "consumers") or 0) == 0:
                self._warn(
                    f"dead:{stream}:{name}",
                    "Consumer group %s on stream %s has no consumers; it holds back trimming "
                    "(delete it manually if it is abandoned)",
                    name, stream,
                )
            elif over and floors[name] == lowest:
                self._warn(
                    f"slow:{stream}:{name}",
                    "Consumer group %s on stream %s is pinning trimming and is stalled or slow: "
                    "%s pending, lag %s",
                    name, stream, _field(group, "pending"), _field(group, "lag"),
                )


async def run_retention_loop(
    retention: StreamRetention,
    streams: "Iterable[str]",
    interval: float = DEFAULT_INTERVAL_SECONDS,
) -> None:
    streams = list(streams)
    while True:
        try:
            await retention.trim_once(streams)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            retention.report_failure(e)
        await asyncio.sleep(interval)
