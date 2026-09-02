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

from app.telemetry.backend import METRICS_BACKEND

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

LANES_PAUSED = METRICS_BACKEND.gauge(
    "pipeshub_indexing_lanes_paused",
    "Lanes currently not being read because a key on them is at its cap",
    ["broker"],
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


def record_dwell_exceeded(broker: str, count: int = 1) -> None:
    DWELL_EXCEEDED.inc(broker, value=count)


def record_missing_key(broker: str, field: str) -> None:
    MISSING_KEY.inc(broker, field)
