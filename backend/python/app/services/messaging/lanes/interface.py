"""Lane routing: physical isolation of one fairness key's backlog from another's.

The DRR scheduler reorders whatever the consumer has already read. It cannot
reach a record it has not read, so on a single FIFO lane a small producer's
records still sit physically behind a large producer's backlog and fairness
is bounded by the read-ahead window.

Lanes remove that bound. Each record event is published to one of N lanes
chosen from its fairness key, so one key's backlog never sits in front of
another's -- and when a key's buffer fills, the consumer can stop reading
*that lane* and keep draining the others. This is what lets a full buffer
apply real backpressure instead of needing the messages re-published to the
tail of the topic.

The two brokers express a lane differently, which is why this is a protocol
rather than one class:

- **Kafka**: a lane *is* a partition. The producer sets the message key and
  the broker's partitioner places it; the lane count is the topic's partition
  count, managed by the Kafka admin, not by this router.
- **Redis Streams**: streams have no partitions, so a lane is a separate
  stream ``<topic>.<n>`` and the router picks the stream name outright.

Note the asymmetry in who computes a lane. Producers route *into* a lane
(this protocol). Consumers never recompute it: a message's lane is intrinsic
to where it arrived -- its Kafka partition, or the Redis stream it was read
from -- so nothing has to agree with the broker's hash function.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

__all__ = ["LaneAssignmentMode", "LaneConfig", "LaneHint", "LaneRouter"]

# Events whose fairness key is missing share this lane rather than being
# spread unpredictably; it matches the scheduler's sentinel so they also
# share one virtual queue.
DEFAULT_LANE_KEY = "__default__"


class LaneAssignmentMode(StrEnum):
    """How a Redis producer picks a connector's lane."""

    # Recorded once per connector in the lane map (``assignment.py``).
    ASSIGNED = "assigned"
    # ``stable_lane(connectorId)``, as before the lane map existed.
    HASH = "hash"


@dataclass(frozen=True)
class LaneHint:
    """What a producer can tell about a connector from one of its events,
    for a lane chosen on its first publish."""

    org_id: str | None = None
    # ``ConnectorClass`` value; None means unknown, which is placed as large.
    connector_class: str | None = None
    connector_type: str | None = None


@runtime_checkable
class LaneRouter(Protocol):
    """Producer-side placement of a message into a lane."""

    @property
    def lane_count(self) -> int: ...

    def route(
        self, topic: str, lane_key: str | None
    ) -> tuple[str, str | None]:
        """Return the ``(topic, broker_key)`` hashing places this message at."""
        ...

    async def place(
        self, topic: str, lane_key: str | None, hint: LaneHint | None = None
    ) -> tuple[str, str | None]:
        """Return the ``(topic, broker_key)`` this message should be sent to.

        The hash routers answer with ``route``; the assigned router looks the
        connector up in the lane map.
        """
        ...

    def lane_topics(self, topic: str) -> list[str]:
        """Every topic a consumer must subscribe to in order to receive all
        of ``topic``'s traffic, including any pre-lane legacy topic."""
        ...


@dataclass(frozen=True)
class LaneConfig:
    """Lane knobs, read once at startup.

    ``lane_count <= 1`` means no laning at all: the factory then leaves the
    producer unwrapped, so the publish path is byte-for-byte what it is
    today. That is the default -- laning changes which physical topic or
    partition a record lands on, which is a deployment change, not a tuning
    knob.
    """

    lane_count: int = 1
    # The innermost fairness level. Lanes must separate what the scheduler
    # separates, and `orgId` alone cannot separate two users of one org.
    lane_key_field: str = "connectorId"
    # Only the indexing topic is laned; entity/sync events are low volume and
    # have no fairness problem to solve.
    laned_topics: tuple[str, ...] = ("record-events",)
    # Redis only: Kafka places by key whatever this says.
    assignment: LaneAssignmentMode = LaneAssignmentMode.HASH
    # How long a producer trusts a lane it looked up before asking again.
    assignment_cache_seconds: float = 60.0

    @property
    def enabled(self) -> bool:
        return self.lane_count > 1
