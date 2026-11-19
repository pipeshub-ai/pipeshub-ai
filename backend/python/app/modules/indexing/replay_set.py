"""The last deliveries that completed a record, so an exact replay of one is acknowledged without work.

A broker redelivers a message whose acknowledgement was lost (a failed XACK, a Kafka rebalance, a consumer
restart before the offset commit). Such a replay of an event that already finished its record would download,
parse and embed the same bytes again. The delivery that completes a record is remembered here, and the handler
acknowledges an exact replay of it before any download.

**An optimisation, never the correctness mechanism.** The set is written to the Redis whose trouble often
causes the replay, and a memory policy that evicts keys drops it. Nothing may depend on it: a replay whose
entry is missing does what it does without this module. A failed write or read only weakens the guard, and is
logged and counted.

Keys, per record (one hash tag, so both live on one cluster slot):

* ``messaging:indexed:{<recordId>}``: a hash. The field is the envelope event id; the value is
  ``<flags>|<completed-at-ms>|<delivery id>``, where completed-at is the ``lastIndexTimestamp`` that the
  delivery's COMPLETED write gave the record.
* ``messaging:indexed:{<recordId>}:order``: a sorted set of the newest ``REPLAY_SET_SIZE`` event ids; trimming
  one removes its hash field.

**The identity of a replay is the envelope event id.** The broker delivery id is a transport detail: after
an offset reset it can repeat for a different event. A replay is acknowledged only when its event id is in the set
and, when it carries a delivery id, the stored one is the same. A match on the delivery id alone never
acknowledges, and an event with no envelope has no event id, so the guard does not apply to it.

**An entry answers a replay only while the record still shows a remembered completion that is not older.**
The record's ``lastIndexTimestamp`` must be the completed-at of an entry (the current completion is one the
set knows), and the replayed event must be that delivery, or come before it in the same broker log (a Kafka
partition, a PostgreSQL queue; a Redis Streams entry id names no stream, so it is compared with nothing). Without
that, an older event whose entry was lost (a failed write, a trim, an eviction) runs again on replay and can
index its own, older bytes; a newer event acknowledged after it would leave the record there. Since the
record is the proof, a lost write can only make a replay run, never make one skip.

``flags`` say what intent the completed delivery carried: ``F`` when it was a forced reindex, ``S=<digest>``
of the sorted stage names when it named stages. A replay is only acknowledged when the stored flags cover it:
a replay that asks for more (a forced run after a non-forced completion, another stage set) still runs.
"""

from __future__ import annotations

import hashlib
import os
import re
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.telemetry.backend import METRICS_BACKEND

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping
    from logging import Logger

    from redis.asyncio import Redis

#: Newest deliveries kept per record: more than any plausible window of redelivered events for one record.
REPLAY_SET_SIZE = 32
REPLAY_SET_TTL_MS = 7 * 24 * 3600 * 1000
KEY_PREFIX = "messaging:indexed:"

#: Payload keys: the envelope event id (set by the consumer from the verified envelope) and the delivery id.
EVENT_ID_KEY = "_event_id"
DELIVERY_ID_KEY = "_delivery_id"

REPLAY_SET_ERRORS = METRICS_BACKEND.counter(
    "pipeshub_replay_set_errors_total",
    "Replay-set reads or writes that failed; the replay guard is weaker for that record, nothing else changes",
    ["op"],
)
REPLAYS_ACKNOWLEDGED = METRICS_BACKEND.counter(
    "pipeshub_replay_set_acknowledged_total",
    "Redelivered events of an already completed delivery acknowledged without work",
    ["service"],
)


def enabled() -> bool:
    """``REPLAY_SET=off`` switches the guard off (the durable-state tests run that way)."""
    return os.getenv("REPLAY_SET", "on").strip().lower() not in ("off", "0", "false", "no")


def order_key(record_id: str) -> str:
    return f"{KEY_PREFIX}{{{record_id}}}:order"


def hash_key(record_id: str) -> str:
    return f"{KEY_PREFIX}{{{record_id}}}"


def flags_of(payload: Mapping[str, Any]) -> str:
    """The intent a delivery carries: ``F`` when forced, ``S=<digest>`` when it names stages."""
    parts: list[str] = []
    if payload.get("forceReindex"):
        parts.append("F")
    stages = payload.get("stages")
    if isinstance(stages, list) and stages:
        digest = hashlib.sha1(",".join(sorted(str(s) for s in stages)).encode(), usedforsecurity=False).hexdigest()
        parts.append(f"S={digest[:8]}")
    return ",".join(parts)


def covers(stored: str, replay: str) -> bool:
    """Whether a completed delivery with flags ``stored`` answers a replay carrying ``replay``.

    A forced replay needs a forced completion; a replay naming stages needs the same stage set. A replay
    that asks for nothing special is answered by any completion.
    """
    stored_parts = set(filter(None, stored.split(",")))
    return set(filter(None, replay.split(","))) <= stored_parts


# Add one delivery, then trim to the newest REPLAY_SET_SIZE and drop the trimmed ones' fields. A hash whose
# order key is gone (one key of the pair evicted) is dropped first: trimming reads only the order key, so
# its fields would never be trimmed.
_RECORD_LUA = """
local h, z = KEYS[1], KEYS[2]
local ms, size, ttl, value, event = tonumber(ARGV[1]), tonumber(ARGV[2]), tonumber(ARGV[3]), ARGV[4], ARGV[5]
if redis.call('EXISTS', z) == 0 then redis.call('DEL', h) end
redis.call('HSET', h, event, value)
redis.call('ZADD', z, ms, event)
local over = redis.call('ZCARD', z) - size
if over > 0 then
  for _, old in ipairs(redis.call('ZRANGE', z, 0, over - 1)) do redis.call('HDEL', h, old) end
  redis.call('ZREMRANGEBYRANK', z, 0, over - 1)
end
redis.call('PEXPIRE', h, ttl)
redis.call('PEXPIRE', z, ttl)
return 1
"""


@dataclass(frozen=True, slots=True)
class StoredDelivery:
    """What the set holds for one event id."""

    flags: str
    delivery_id: str
    #: The ``lastIndexTimestamp`` the delivery's COMPLETED write gave the record.
    completed_at_ms: int | None = None


def parse_entry(value: object) -> StoredDelivery | None:
    """A stored ``<flags>|<ms>|<delivery id>`` value, or None when it is not one."""
    if isinstance(value, bytes):
        value = value.decode()
    if not isinstance(value, str):
        return None
    parts = value.split("|", 2)
    if len(parts) != 3:
        return None
    return StoredDelivery(flags=parts[0], delivery_id=parts[2], completed_at_ms=_as_ms(parts[1]))


def _as_ms(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


async def record_delivery(
    redis: Redis, record_id: str, *, event_id: str | None, delivery_id: str | None, flags: str,
    completed_at_ms: int,
) -> None:
    """Remember that this event completed the record. Raises on a Redis failure; the caller treats it as best effort.

    ``completed_at_ms`` is the ``lastIndexTimestamp`` the COMPLETED write gave the record. The identity of a
    delivery is its envelope event id. An event with none (no envelope) is not remembered: a broker delivery
    id alone cannot tell a replay from a new event after an offset reset.
    """
    if not event_id:
        return
    await redis.eval(  # type: ignore[misc]
        _RECORD_LUA, 2, hash_key(record_id), order_key(record_id),
        completed_at_ms, REPLAY_SET_SIZE, REPLAY_SET_TTL_MS,
        f"{flags}|{completed_at_ms}|{delivery_id or ''}", event_id,
    )


async def read_entries(redis: Redis, record_id: str, payload: Mapping[str, Any]) -> dict[str, StoredDelivery]:
    """The record's entries by event id (at most ``REPLAY_SET_SIZE``); empty when the event has no id, which
    nothing can match. Raises on a Redis failure."""
    if not payload.get(EVENT_ID_KEY):
        return {}
    raw = await redis.hgetall(hash_key(record_id))  # type: ignore[misc]
    entries: dict[str, StoredDelivery] = {}
    for field, value in (raw or {}).items():
        entry = parse_entry(value)
        if entry is not None:
            entries[field.decode() if isinstance(field, bytes) else str(field)] = entry
    return entries


# The consumers' delivery ids (``stamp_delivery_id``): Kafka ``<topic>-<partition>-<offset>`` and the PostgreSQL
# queue ``<topic>:<row id>``. A retry keeps its original's id. Redis Streams stamps the bare entry id
# ``<ms>-<seq>``, which does not name its stream: a record's events can be on different lane streams, whose ids
# are not ordered against each other, so it names no position here.
_PG_ID = re.compile(r"(.+):(\d+)")
_KAFKA_ID = re.compile(r"(.+)-(\d+)-(\d+)")


def log_position(delivery_id: str | None) -> tuple[str, tuple[int, ...]] | None:
    """The broker log a delivery id names and its position there, or None when it names no position."""
    if not delivery_id:
        return None
    if match := _PG_ID.fullmatch(delivery_id):
        return f"pg:{match[1]}", (int(match[2]),)
    if match := _KAFKA_ID.fullmatch(delivery_id):
        return f"kafka:{match[1]}-{match[2]}", (int(match[3]),)
    return None


def not_older_than_the_record(
    event_id: str, entries: Mapping[str, StoredDelivery], indexed_at: object
) -> bool:
    """Whether the record still shows a remembered completion that the event ``event_id`` does not postdate.

    ``indexed_at`` is the record's ``lastIndexTimestamp``. The remembered delivery whose COMPLETED write set
    it made the record what it is now; the event is answered by it when it is that delivery, or when both
    delivery ids name positions in one broker log and the event's comes first. A completion the set does not
    know, two entries with one timestamp, or positions that cannot be compared answer nothing.
    """
    stored = entries.get(event_id)
    now = _as_ms(indexed_at)
    if stored is None or now is None:
        return False
    current = [(eid, entry) for eid, entry in entries.items() if entry.completed_at_ms == now]
    if len(current) != 1:
        return False
    current_id, current_entry = current[0]
    if current_id == event_id:
        return True
    mine, theirs = log_position(stored.delivery_id), log_position(current_entry.delivery_id)
    return mine is not None and theirs is not None and mine[0] == theirs[0] and mine[1] < theirs[1]


@dataclass(frozen=True, slots=True)
class DeliveryRef:
    """The delivery being processed: what the sink needs to remember it when it completes the record."""

    event_id: str | None
    delivery_id: str | None
    flags: str
    #: Writes the entry for a record id and its completed-at; supplied by the handler, which owns the Redis
    #: client of this loop.
    remember: Callable[[str, int], Awaitable[None]]


_current: ContextVar[DeliveryRef | None] = ContextVar("replay_delivery", default=None)


def begin_delivery(ref: DeliveryRef) -> None:
    """Bind the delivery the handler is about to run, for the sink to find when the record completes."""
    _current.set(ref)


def end_delivery() -> None:
    """Nothing of an earlier delivery's run survives into this one."""
    _current.set(None)


async def note_record_completed(record_id: str, completed_at_ms: int, logger: Logger | None = None) -> None:
    """Called after the record's COMPLETED write returned true, with the ``lastIndexTimestamp`` it wrote, as a
    call of its own: a failure here is logged and counted and never fails the completion."""
    ref = _current.get()
    if ref is None or not enabled():
        return
    try:
        await ref.remember(record_id, completed_at_ms)
    except Exception as exc:
        REPLAY_SET_ERRORS.inc("write")
        if logger is not None:
            logger.warning("Replay set not written for record %s: %s", record_id, type(exc).__name__)


#: Eviction policies that cannot silently drop a ``messaging:`` or ``sg:`` key.
SAFE_EVICTION_POLICIES = frozenset({"noeviction"})

EVICTION_POLICY_UNSAFE = METRICS_BACKEND.gauge(
    "pipeshub_redis_eviction_policy_unsafe",
    "1 when the Redis maxmemory-policy can evict the replay set, pause counters and budgets silently",
    ["service"],
)

#: What ``/health`` reports under ``redis.evictionPolicyWarning`` (None: safe, or not read).
_eviction_warning: str | None = None


def eviction_policy_warning() -> str | None:
    return _eviction_warning


async def check_eviction_policy(redis: Redis, logger: Logger | None = None) -> str | None:
    """Read ``maxmemory-policy`` from ``INFO memory`` (``CONFIG GET`` is often disabled on managed Redis) and
    report a policy that can evict keys silently. Indexing keeps running either way; the admin is told."""
    global _eviction_warning  # noqa: PLW0603 - module state read by /health
    try:
        info = await redis.info("memory")  # type: ignore[misc]
        policy = str(info.get("maxmemory_policy") or "").strip().lower()
    except Exception as exc:
        if logger is not None:
            logger.warning("Redis eviction policy not read: %s", type(exc).__name__)
        return _eviction_warning
    unsafe = bool(policy) and policy not in SAFE_EVICTION_POLICIES
    EVICTION_POLICY_UNSAFE.set("indexing", value=1.0 if unsafe else 0.0)
    _eviction_warning = (
        f"maxmemory-policy is '{policy}': Redis may evict the replay set, pause counters and budgets silently. "
        "Use 'noeviction'."
        if unsafe else None
    )
    if unsafe and logger is not None:
        logger.warning(_eviction_warning)
    return _eviction_warning
