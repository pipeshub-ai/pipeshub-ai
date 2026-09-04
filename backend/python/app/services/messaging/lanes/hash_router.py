"""Stable-hash lane routers for Kafka and Redis Streams."""
from __future__ import annotations

from hashlib import sha256

from app.services.messaging.lanes.interface import DEFAULT_LANE_KEY, LaneConfig

__all__ = ["KafkaLaneRouter", "RedisLaneRouter", "stable_lane", "build_lane_router"]


def stable_lane(lane_key: str, lane_count: int) -> int:
    """Map a fairness key to a lane, identically in every process *and* in
    every language that publishes record events.

    Deliberately not :func:`hash`: ``PYTHONHASHSEED`` randomises string
    hashing per process, so two producer replicas would place the same key on
    different lanes and a consumer's per-lane view would be meaningless.

    SHA-256 rather than BLAKE2b for the same reason one step further out: the
    Node producer has to land a given connector on the same lane as this one,
    and Node's crypto cannot produce BLAKE2b at an 8-byte digest (the digest
    length is mixed into BLAKE2b's IV, so truncating blake2b512 gives a
    different value). Both runtimes compute SHA-256 natively and agree
    byte-for-byte. See laneStreamFor in the Node lane utils; the two must
    stay in step.
    """
    if lane_count <= 1:
        return 0
    digest = sha256(lane_key.encode("utf-8")).digest()[:8]
    return int.from_bytes(digest, "big") % lane_count


class KafkaLaneRouter:
    """Lanes are partitions; the broker's partitioner does the placement.

    The router's whole job is to put the fairness key on the message so
    records for one connector land together. ``lane_count`` mirrors the
    topic's partition count for reporting only -- this class never computes a
    partition itself, because the consumer reads a message's lane off the
    partition it arrived on rather than recomputing it.

    Keying by ``connectorId`` rather than ``recordId`` is a *coarsening*: a
    record belongs to exactly one connector, so every ordering guarantee
    ``recordId`` gave is preserved, and per-connector ordering is added.
    """

    def __init__(self, lane_count: int) -> None:
        self._lane_count = max(1, lane_count)

    @property
    def lane_count(self) -> int:
        return self._lane_count

    def route(self, topic: str, lane_key: str | None) -> tuple[str, str | None]:
        return topic, lane_key or DEFAULT_LANE_KEY

    def lane_topics(self, topic: str) -> list[str]:
        return [topic]


class RedisLaneRouter:
    """Lanes are separate streams, ``<topic>.<n>``.

    Redis Streams have no partitions and no key-based placement -- the
    producer's ``key`` is stored as a plain field -- so the lane has to be
    the stream name itself.
    """

    def __init__(self, lane_count: int) -> None:
        self._lane_count = max(1, lane_count)

    @property
    def lane_count(self) -> int:
        return self._lane_count

    def lane_name(self, topic: str, lane: int) -> str:
        return f"{topic}.{lane}"

    def route(self, topic: str, lane_key: str | None) -> tuple[str, str | None]:
        lane = stable_lane(lane_key or DEFAULT_LANE_KEY, self._lane_count)
        return self.lane_name(topic, lane), lane_key

    def lane_topics(self, topic: str) -> list[str]:
        """Base stream first, then the lanes.

        The base stream stays subscribed so an install that laned an existing
        deployment keeps draining whatever was written before the switch;
        nothing is published to it any more.
        """
        return [topic] + [
            self.lane_name(topic, lane) for lane in range(self._lane_count)
        ]


def build_lane_router(config: LaneConfig, is_kafka: bool) -> "KafkaLaneRouter | RedisLaneRouter":
    return (
        KafkaLaneRouter(config.lane_count)
        if is_kafka
        else RedisLaneRouter(config.lane_count)
    )
