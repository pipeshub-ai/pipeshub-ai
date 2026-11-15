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
  ``<flags>|<completed-at-ms>|<delivery id>``.
* ``messaging:indexed:{<recordId>}:order``: a sorted set of the newest ``REPLAY_SET_SIZE`` event ids; trimming
  one removes its hash field.

**The identity of a replay is the envelope event id.** The broker delivery id is a transport detail: after
an offset reset it can repeat for a different event. A replay is acknowledged only when its event id is in the set
and, when it carries a delivery id, the stored one is the same. A match on the delivery id alone never
acknowledges, and an event with no envelope has no event id, so the guard does not apply to it.

``flags`` say what intent the completed delivery carried: ``F`` when it was a forced reindex, ``S=<digest>``
of the sorted stage names when it named stages. A replay is only acknowledged when the stored flags cover it:
a replay that asks for more (a forced run after a non-forced completion, another stage set) still runs.
"""

from __future__ import annotations

import hashlib
import os
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


# Add one delivery, then trim to the newest REPLAY_SET_SIZE and drop the trimmed ones' fields.
_RECORD_LUA = """
local h, z = KEYS[1], KEYS[2]
local ms, size, ttl, value, event = tonumber(ARGV[1]), tonumber(ARGV[2]), tonumber(ARGV[3]), ARGV[4], ARGV[5]
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


def parse_entry(value: object) -> StoredDelivery | None:
    """A stored ``<flags>|<ms>|<delivery id>`` value, or None when it is not one."""
    if isinstance(value, bytes):
        value = value.decode()
    if not isinstance(value, str):
        return None
    parts = value.split("|", 2)
    if len(parts) != 3:
        return None
    return StoredDelivery(flags=parts[0], delivery_id=parts[2])


async def record_delivery(
    redis: Redis, record_id: str, *, event_id: str | None, delivery_id: str | None, flags: str, now_ms: int
) -> None:
    """Remember that this event completed the record. Raises on a Redis failure; the caller treats it as best effort.

    The identity of a delivery is its envelope event id. An event with none (no envelope) is not
    remembered: a broker delivery id alone cannot tell a replay from a new event after an offset reset.
    """
    if not event_id:
        return
    await redis.eval(  # type: ignore[misc]
        _RECORD_LUA, 2, hash_key(record_id), order_key(record_id),
        now_ms, REPLAY_SET_SIZE, REPLAY_SET_TTL_MS, f"{flags}|{now_ms}|{delivery_id or ''}", event_id,
    )


async def read_entry(redis: Redis, record_id: str, payload: Mapping[str, Any]) -> StoredDelivery | None:
    """What the set holds for the event's id (None when nothing, or the event has no id). Raises on a Redis failure."""
    event_id = payload.get(EVENT_ID_KEY)
    if not event_id:
        return None
    return parse_entry(await redis.hget(hash_key(record_id), str(event_id)))  # type: ignore[misc]


@dataclass(frozen=True, slots=True)
class DeliveryRef:
    """The delivery being processed: what the sink needs to remember it when it completes the record."""

    event_id: str | None
    delivery_id: str | None
    flags: str
    #: Writes the entry for a record id; supplied by the handler, which owns the Redis client of this loop.
    remember: Callable[[str], Awaitable[None]]


_current: ContextVar[DeliveryRef | None] = ContextVar("replay_delivery", default=None)


def begin_delivery(ref: DeliveryRef) -> None:
    """Bind the delivery the handler is about to run, for the sink to find when the record completes."""
    _current.set(ref)


def end_delivery() -> None:
    """Nothing of an earlier delivery's run survives into this one."""
    _current.set(None)


async def note_record_completed(record_id: str, logger: Logger | None = None) -> None:
    """Called after the record's COMPLETED write returned true, as a call of its own: a failure here is
    logged and counted and never fails the completion."""
    ref = _current.get()
    if ref is None or not enabled():
        return
    try:
        await ref.remember(record_id)
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
