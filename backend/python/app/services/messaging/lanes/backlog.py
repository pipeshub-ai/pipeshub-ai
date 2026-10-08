"""What is still waiting on a laned topic, as the broker sees it.

A record's status cannot say whether its event is still on the broker: QUEUED
is written before the publish as well as by it. The broker can. Each lane is
consumed roughly in order, so if the oldest event a consumer group has not
finished with on a lane was published before a record was put in line, that
record's own event may simply not have been reached yet. If everything that
old is gone from the lane and the record is still waiting, its event is not
coming.

"Not finished with" is deliberately wider than "not yet delivered". An event
that was read and is buffered, parked behind a paused lane, sleeping out a
retry back-off or being processed right now is still waiting from the
record's point of view, and both brokers report it that way: Redis keeps it
in the group's pending list until it is acknowledged, and Kafka's committed
offset does not pass it until it is done.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from app.services.messaging.lanes.hash_router import RedisLaneRouter
from app.services.messaging.lanes.interface import DEFAULT_LANE_KEY

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Mapping

    from app.services.messaging.lanes.assignment import LaneEntry

__all__ = ["LaneBacklog", "redis_lanes_for_key"]

_NO_PENDING: Mapping[str, int] = MappingProxyType({})
# Nothing assigned: hashing is the whole story.
_NO_ASSIGNMENTS: Mapping[str, LaneEntry] = MappingProxyType({})


@dataclass(frozen=True)
class LaneBacklog:
    """Per lane, when the oldest event a group has not finished with was published."""

    topic: str
    # Lane name -> epoch ms on the broker's clock. A lane the group has caught
    # up on is absent.
    oldest_waiting_ms: Mapping[str, float]
    # The lanes one event could be waiting on, from its payload. None when the
    # broker places messages itself and the lane cannot be recomputed here
    # (Kafka's partitioner), so any lane might hold it.
    lanes_for_event: Callable[[Mapping[str, object]], Collection[str]] | None = None
    # Lane name -> entries delivered but not yet acknowledged, where the broker
    # reports it (Redis). For the lane view on /health.
    pending: Mapping[str, int] = _NO_PENDING

    def oldest_waiting_for(self, payload: Mapping[str, object]) -> float | None:
        """Oldest event still waiting on any lane ``payload`` could be on; None if none is."""
        if self.lanes_for_event is None:
            waiting = list(self.oldest_waiting_ms.values())
        else:
            waiting = [
                self.oldest_waiting_ms[lane]
                for lane in self.lanes_for_event(payload)
                if lane in self.oldest_waiting_ms
            ]
        return min(waiting, default=None)


def redis_lanes_for_key(
    topic: str,
    lane_key: str | None,
    streams: Collection[str],
    lane_count: int,
    assignments: Mapping[str, LaneEntry] | None = _NO_ASSIGNMENTS,
) -> set[str]:
    """Streams an event with ``lane_key`` may be waiting on, out of ``streams``.

    Its own lanes, plus every stream it could have been written to by another
    route: the base stream (written before lanes were switched on, and by any
    producer that is not laned), the shared default lane (events published
    without the key), and lanes outside the configured range (left over from a
    larger lane count, which the consumer still drains).

    Its own lanes are its hash lane (old producers, a rolled-back switch, and
    lookups that fell back) and, from the lane map snapshot ``assignments``,
    its assigned lane and the lane a move is still settling off. ``None`` for
    ``assignments`` means the map could not be read, so any stream could hold
    it; an empty map means nothing has been assigned and hashing is the whole
    story.
    """
    if lane_count <= 1 or assignments is None:
        return set(streams)
    router = RedisLaneRouter(lane_count)
    configured = set(router.lane_topics(topic))
    lanes = {topic} | {s for s in streams if s not in configured}
    for key in {lane_key or DEFAULT_LANE_KEY, DEFAULT_LANE_KEY}:
        lanes.add(router.route(topic, key)[0])
        entry = assignments.get(key)
        if entry is not None:
            lanes.add(router.lane_name(topic, entry.lane))
            if entry.prev_lane is not None:
                lanes.add(router.lane_name(topic, entry.prev_lane))
    return lanes
