"""Metrics for indexing fair scheduling and lane routing.

Deliberately kept out of ``app.services.messaging.scheduling`` and
``app.services.messaging.lanes``: those are pure, broker-agnostic algorithm
packages with no I/O, and the consumers are the things that actually know
when something happened. Instrumentation lives here; the consumers call it.

**Cardinality.** Fairness is per (org, connector), but a per-connector label
would put an unbounded series count on a busy install. Dispatch share is
labelled by org only -- bounded by customer count, and the same choice
``activity_metrics`` already makes -- while connector-level activity is
exposed as a *count* of active keys rather than one series per key.
"""

import threading
from collections import deque

from app.plugins.telemetry.backend import METRICS_BACKEND

# The one you cannot operate without. Every buffered offset must reach a
# terminal state; one that does not stalls every later commit on its
# partition until the process restarts. A lag that only ever grows is that
# failure, and it is invisible until a restart replays everything.
WATERMARK_LAG = METRICS_BACKEND.gauge(
    "pipeshub_indexing_watermark_lag",
    "Offsets read but not yet committed past, per partition",
    ["topic", "partition"],
)

BUFFER_DEPTH = METRICS_BACKEND.gauge(
    "pipeshub_indexing_scheduler_buffer_depth",
    "Messages currently held in the fair-scheduling buffer",
    ["broker"],
)

ACTIVE_KEYS = METRICS_BACKEND.gauge(
    "pipeshub_indexing_scheduler_active_keys",
    "Distinct fairness keys with buffered work, by hierarchy level",
    ["broker", "level"],
)

DISPATCHED = METRICS_BACKEND.counter(
    "pipeshub_indexing_scheduler_dispatched_total",
    "Records dispatched by the fair scheduler",
    ["broker", "org"],
)

DEFERRED = METRICS_BACKEND.counter(
    "pipeshub_indexing_scheduler_deferred_total",
    "Reads stopped because the buffer had no room",
    ["broker", "reason"],
)

# A count, never labelled by record: a waiter is a pending entry kept back
# because another delivery holds its record.
RECORD_WAITS = METRICS_BACKEND.gauge(
    "pipeshub_indexing_record_waits",
    "Pending entries waiting for their record, which another delivery is working on",
    ["broker"],
)

LANES_PAUSED = METRICS_BACKEND.gauge(
    "pipeshub_indexing_lanes_paused",
    "Lanes currently not being read because a key on them is at its cap",
    ["broker"],
)

# Bounded by the reason, never labelled by connector.
LANE_ASSIGNMENT_FALLBACKS = METRICS_BACKEND.counter(
    "pipeshub_lane_assignment_fallbacks_total",
    "Record events sent to their connector's hashed lane because the lane map "
    "could not be read",
    ["reason"],
)

# Bounded by the lane count; never labelled by connector.
LANE_CONNECTORS = METRICS_BACKEND.gauge(
    "pipeshub_indexing_lane_connectors",
    "Connectors assigned to each Redis Streams lane, by size",
    ["lane", "size"],
)

LANE_OLDEST_WAITING = METRICS_BACKEND.gauge(
    "pipeshub_indexing_lane_oldest_waiting_seconds",
    "Age of the oldest event each lane has not finished with; 0 when it has none",
    ["lane"],
)

DWELL_EXCEEDED = METRICS_BACKEND.counter(
    "pipeshub_indexing_scheduler_dwell_exceeded_total",
    "Buffered items force-resolved after exceeding the dwell budget",
    ["broker"],
)

MISSING_KEY = METRICS_BACKEND.counter(
    "pipeshub_indexing_scheduler_missing_key_total",
    "Messages whose fairness key field was absent, grouped under the default",
    ["broker", "field"],
)

CONNECTOR_OFF_SETTLED = METRICS_BACKEND.counter(
    "pipeshub_indexing_connector_off_settled_total",
    "Queued events of turned-off or removed connectors acknowledged as they "
    "were read, without being buffered or dispatched",
    ["broker"],
)

# Bounded: one series per index tier. A heavy tier pinned at its ceiling
# while light reads zero is the signature of attachments queueing on the
# heavy-parse slots -- healthy, as long as light keeps moving.
GATE_WAITERS = METRICS_BACKEND.gauge(
    "pipeshub_indexing_gate_waiters",
    "Tasks spawned but not yet admitted through their index gate, by tier",
    ["broker", "tier"],
)


def record_watermark_lag(topic: str, partition: int, lag: int) -> None:
    WATERMARK_LAG.set(topic, str(partition), value=lag)


def record_scheduler_depth(
    broker: str, pending: int, active_by_level: dict[str, int]
) -> None:
    BUFFER_DEPTH.set(broker, value=pending)
    for level, count in active_by_level.items():
        ACTIVE_KEYS.set(broker, level, value=count)


def record_dispatch(broker: str, org: str) -> None:
    DISPATCHED.inc(broker, org)


def record_deferred(broker: str, reason: str) -> None:
    DEFERRED.inc(broker, reason)


def record_lanes_paused(broker: str, count: int) -> None:
    LANES_PAUSED.set(broker, value=count)


# Several consumers of one broker wait in one process (the record and the
# priority consumers): the gauge is their total, kept here because no one
# consumer can see the others'. A consumer is named by the id it announces
# itself with, which is never reused within a process.
_record_waits_lock = threading.Lock()
_record_waits_by_consumer: dict[str, dict[int, int]] = {}
# Consumers collected without a stop, as (broker, consumer id). The event that
# reports one is emitted from a finalizer, on whatever thread a collection
# happens on, possibly one already inside this lock or the gauge's own
# (prometheus-client's is not re-entrant), so noting it takes no lock and
# publishes nothing. The next publication or refresh takes them out of the total.
_record_waits_discarded: deque[tuple[str, int]] = deque()


def record_record_waits(broker: str, consumer_id: int, count: int) -> None:
    with _record_waits_lock:
        _record_waits_by_consumer.setdefault(broker, {})[consumer_id] = count
        _publish_record_waits_locked(broker)


def forget_record_waits(broker: str, consumer_id: int) -> None:
    with _record_waits_lock:
        _record_waits_by_consumer.setdefault(broker, {}).pop(consumer_id, None)
        _publish_record_waits_locked(broker)


def note_consumer_discarded(broker: str, consumer_id: int) -> None:
    """Lock-free: see ``_record_waits_discarded``."""
    _record_waits_discarded.append((broker, consumer_id))


def refresh_record_waits(broker: str) -> None:
    """Take discarded consumers out of the total; cheap when there are none.
    Called on every consume pass, so a quiet process does not keep counting
    a consumer it dropped."""
    if not _record_waits_discarded:
        return
    with _record_waits_lock:
        _publish_record_waits_locked(broker)


def _publish_record_waits_locked(broker: str) -> None:
    """Drop discarded consumers, then publish the total; hold _record_waits_lock.

    Computed and set under the lock, and noting a discard publishes nothing, so
    no older total can be written after a newer one.
    """
    while _record_waits_discarded:
        discarded_broker, consumer_id = _record_waits_discarded.popleft()
        _record_waits_by_consumer.get(discarded_broker, {}).pop(consumer_id, None)
    RECORD_WAITS.set(broker, value=sum(_record_waits_by_consumer.get(broker, {}).values()))


def record_lane_assignment_fallback(reason: str) -> None:
    LANE_ASSIGNMENT_FALLBACKS.inc(reason)


def record_lane_connectors(lane: str, size: str, count: int) -> None:
    LANE_CONNECTORS.set(lane, size, value=count)


def record_lane_oldest_waiting(lane: str, seconds: float) -> None:
    LANE_OLDEST_WAITING.set(lane, value=seconds)


def record_dwell_exceeded(broker: str, count: int = 1) -> None:
    DWELL_EXCEEDED.inc(broker, value=count)


def record_missing_key(broker: str, field: str) -> None:
    MISSING_KEY.inc(broker, field)


def record_gate_waiters(broker: str, tier: str, count: int) -> None:
    GATE_WAITERS.set(broker, tier, value=count)


def record_connector_off_settled(broker: str, count: int) -> None:
    CONNECTOR_OFF_SETTLED.inc(broker, value=count)
