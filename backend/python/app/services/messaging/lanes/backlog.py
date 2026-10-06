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
from typing import TYPE_CHECKING

from app.services.messaging.lanes.hash_router import RedisLaneRouter

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Mapping

__all__ = ["LaneBacklog", "redis_lanes_for_key"]


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
) -> set[str]:
    """Streams an event with ``lane_key`` may be waiting on, out of ``streams``.

    Its own lane, plus every stream it could have been written to by another
    route: the base stream (written before lanes were switched on, and by any
    producer that is not laned), the shared default lane (events published
    without the key), and lanes outside the configured range (left over from a
    larger lane count, which the consumer still drains).
    """
    if lane_count <= 1:
        return set(streams)
    router = RedisLaneRouter(lane_count)
    configured = set(router.lane_topics(topic))
    own, _ = router.route(topic, lane_key)
    default, _ = router.route(topic, None)
    return {topic, own, default} | {s for s in streams if s not in configured}
