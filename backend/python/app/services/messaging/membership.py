"""Which members of the indexing consumer groups follow the stored-write protocol.

An indexing pod that writes the stored record by compare-and-set names its Kafka ``client_id`` and
its Redis consumer with ``/sp3`` (``storage_protocol``). A live member without it is an older pod
whose in-place writes do not invalidate a servable run first. This module finds such members and
publishes the result, so that:

* Node refuses a Labs save that turns artifact reads on while one is live (409);
* artifact readers fall back to the stored record (``membership_hold``) while one is live;
* the retirement sweep and the stray pass pause.

The result is published to ``/services/platform/artifactRollout/membership/<holder>``: one entry
per writer, ``{checkedAt, untagged, groupsChecked}``, ``checkedAt`` being when the check *started*,
each with a TTL. A reader takes the union of untagged members over the fresh entries, so a clean
entry written by an ex-leader cannot hide the new leader's finding; with no fresh entry the result
is stale, which fails safe. The entries are plaintext (``UNENCRYPTED_PREFIXES``) so Node can read
them without the Python encryption.
"""

from __future__ import annotations

import asyncio
import time
import weakref
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from app.services.messaging.storage_protocol import is_tagged

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from logging import Logger

MEMBERSHIP_DIRECTORY = "/services/platform/artifactRollout/membership/"
#: One check every this many seconds, on its own timer.
CHECK_PERIOD_SECONDS = 30.0
#: An entry older than this (ms, on the checker's clock) is stale: three periods.
STALE_AFTER_MS = 90_000
#: Entries expire on their own, so a dead holder drops out.
ENTRY_TTL_SECONDS = 300
#: A Redis consumer with nothing pending and idle at least this long is not live.
REDIS_IDLE_LIVE_MS = 10 * 60 * 1000
#: How long an evaluated result is reused by a reader.
READER_CACHE_SECONDS = 30.0

_registered: dict[str, set[str]] = {}


def register_group(group_id: str, topics: list[str]) -> None:
    """Record that this process consumes *topics* in *group_id* (every indexing consumer calls this)."""
    _registered.setdefault(group_id, set()).update(topics)


def registered_groups() -> dict[str, list[str]]:
    return {group: sorted(topics) for group, topics in _registered.items()}


def reset_registered_groups() -> None:
    _registered.clear()


@dataclass(frozen=True)
class MembershipResult:
    checked_at: int
    untagged: list[str] = field(default_factory=list)
    groups_checked: list[str] = field(default_factory=list)

    def entry(self) -> dict[str, Any]:
        return {"checkedAt": self.checked_at, "untagged": self.untagged, "groupsChecked": self.groups_checked}


@dataclass(frozen=True)
class MembershipVerdict:
    ok: bool
    untagged: tuple[str, ...] = ()
    reason: str | None = None  # "stale" or "untagged"


def evaluate(entries: list[Any], now_ms: int, stale_ms: int = STALE_AFTER_MS) -> MembershipVerdict:
    """The union over the fresh entries; stale when none is fresh."""
    untagged: set[str] = set()
    fresh = 0
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("checkedAt"), (int, float)):
            continue
        if now_ms - entry["checkedAt"] > stale_ms:
            continue
        fresh += 1
        names = entry.get("untagged")
        if isinstance(names, list):
            untagged.update(str(n) for n in names)
    if fresh == 0:
        return MembershipVerdict(False, (), "stale")
    if untagged:
        return MembershipVerdict(False, tuple(sorted(untagged)), "untagged")
    return MembershipVerdict(True)


async def kafka_untagged(admin_factory: Callable[[], Any], groups: list[str]) -> list[str]:
    """Client ids of live members of *groups* without the tag.

    aiokafka 0.14's ``describe_consumer_groups`` returns the raw ``DescribeGroupsResponse_v<N>``
    objects, one per coordinator. ``to_object()`` names the fields the same way in every version:
    ``groups`` is a list of ``{error_code, group, members: [{member_id, client_id, ...}]}``. A group
    the broker reports with an error, or a response of another shape, raises: the check then
    publishes nothing and readers hold.
    """
    admin = admin_factory()
    await admin.start()
    try:
        described = await admin.describe_consumer_groups(groups)
    finally:
        await admin.close()
    names: list[str] = []
    for response in described:
        for group in response.to_object()["groups"]:
            if group["error_code"] != 0:
                raise RuntimeError(
                    f"describing consumer group {group['group']!r} failed with error code {group['error_code']}"
                )
            for member in group["members"]:
                client_id = str(member["client_id"] or "")
                if client_id and not is_tagged(client_id):
                    names.append(client_id)
    return sorted(set(names))


async def redis_untagged(redis: Any, group_streams: dict[str, list[str]]) -> list[str]:  # noqa: ANN401
    """Names of live consumers without the tag. A consumer is live when it has entries pending,
    or was idle for less than ten minutes (a parked blocking read shows a small idle time)."""
    names: set[str] = set()
    for group, streams in group_streams.items():
        for stream in streams:
            try:
                consumers = await redis.xinfo_consumers(stream, group)
            except Exception as exc:
                # A stream with no such group yet has no members.
                if "NOGROUP" in str(exc) or "no such key" in str(exc).lower():
                    continue
                raise
            for consumer in consumers:
                raw = consumer.get("name", consumer.get(b"name"))
                name = raw.decode() if isinstance(raw, bytes) else str(raw)
                pending = int(consumer.get("pending", consumer.get(b"pending", 0)))
                idle = int(consumer.get("idle", consumer.get(b"idle", 0)))
                if is_tagged(name):
                    continue
                if pending > 0 or idle < REDIS_IDLE_LIVE_MS:
                    names.add(name)
    return sorted(names)


class MembershipPublisher:
    """Runs the check on its own timer and publishes the holder's entry.

    ``check`` returns the untagged names for the groups registered in this process. ``put`` stores
    ``(key, value, ttl)``. The timer does not depend on any sweep or lap, so a slow or paused sweep
    never makes the entry stale.
    """

    def __init__(
        self,
        logger: Logger,
        check: Callable[[dict[str, list[str]]], Awaitable[list[str]]],
        put: Callable[[str, dict[str, Any], int], Awaitable[None]],
        *,
        holder: str | None = None,
        is_leader: Callable[[], Awaitable[bool]] | None = None,
        now_ms: Callable[[], int] = lambda: int(time.time() * 1000),
        groups: Callable[[], dict[str, list[str]]] = registered_groups,
    ) -> None:
        self.logger = logger
        self._check = check
        self._put = put
        self.holder = holder or uuid4().hex[:12]
        self._is_leader = is_leader
        self._now_ms = now_ms
        self._groups = groups

    async def publish_once(self) -> MembershipResult | None:
        """One check and one write. None when this process is not the leader, has no registered group or the check failed
        (nothing is written then, so the entry goes stale and readers hold)."""
        if self._is_leader is not None and not await self._is_leader():
            return None
        started = self._now_ms()
        groups = self._groups()
        if not groups:
            # Nothing is registered (the consumers are not constructed yet): a check of no group would
            # publish "all clear" about members it never looked at.
            self.logger.warning("Indexing membership not published: this process has no registered consumer group")
            return None
        try:
            untagged = await self._check(groups)
        except Exception as exc:
            self.logger.warning("Indexing membership check failed: %s: %s", type(exc).__name__, exc)
            return None
        result = MembershipResult(started, sorted(set(untagged)), sorted(groups))
        await self._put(f"{MEMBERSHIP_DIRECTORY}{self.holder}", result.entry(), ENTRY_TTL_SECONDS)
        if result.untagged:
            self.logger.warning(
                "Indexing members that do not follow the stored-write protocol: %s", ", ".join(result.untagged),
            )
        return result

    async def run(self, period_seconds: float = CHECK_PERIOD_SECONDS) -> None:
        while True:
            try:
                await self.publish_once()
            except Exception as exc:
                # The entry was not stored: it goes stale and readers hold, so say why.
                self.logger.warning("Indexing membership publish failed: %s: %s", type(exc).__name__, exc)
            await asyncio.sleep(period_seconds)


class MembershipReader:
    """The evaluated membership result for artifact readers, cached for ``READER_CACHE_SECONDS``.

    Caches the verdict, not the entries. ``read_entries`` lists and reads the directory.
    """

    def __init__(
        self,
        read_entries: Callable[[], Awaitable[list[Any]]],
        *,
        now_ms: Callable[[], int] = lambda: int(time.time() * 1000),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._read = read_entries
        self._now_ms = now_ms
        self._monotonic = monotonic
        self._cached: tuple[float, MembershipVerdict] | None = None

    async def verdict(self) -> MembershipVerdict:
        now = self._monotonic()
        if self._cached is not None and now - self._cached[0] < READER_CACHE_SECONDS:
            return self._cached[1]
        try:
            verdict = evaluate(await self._read(), self._now_ms())
        except Exception:
            verdict = MembershipVerdict(False, (), "stale")
        self._cached = (now, verdict)
        return verdict


async def read_entries_from_config(config_service: Any) -> list[Any]:  # noqa: ANN401
    """The membership entries, read through the configuration service (uncached: the reader caches the verdict)."""
    keys = await config_service.list_keys_in_directory(MEMBERSHIP_DIRECTORY)
    return [await config_service.get_config(key, use_cache=False) for key in keys]


async def publish_entry(config_service: Any, key: str, entry: dict[str, Any], ttl_seconds: int) -> None:  # noqa: ANN401
    """Store one holder's entry with its TTL."""
    if not await config_service.set_config(key, entry, ttl_seconds=ttl_seconds, keep_in_cache=False):
        raise RuntimeError("the membership entry was not stored")


_READERS: weakref.WeakKeyDictionary[Any, MembershipReader] = weakref.WeakKeyDictionary()


def reader_for(config_service: Any) -> MembershipReader | None:  # noqa: ANN401
    """The process-wide reader for a configuration service (None without one)."""
    if config_service is None:
        return None
    reader = _READERS.get(config_service)
    if reader is None:
        reader = MembershipReader(lambda: read_entries_from_config(config_service))
        _READERS[config_service] = reader
    return reader
