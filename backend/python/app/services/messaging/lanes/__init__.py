"""Per-key broker lanes for the indexing pipeline.

See ``interface.py`` for the contract and why producers route into a lane
while consumers read it off the message; ``hash_router.py`` for the Kafka and
Redis implementations; ``producer.py`` for the decorator that applies routing
to every existing publish site without editing any of them; ``backlog.py``
for what a consumer reports as still waiting on each lane.
"""
from app.services.messaging.lanes.backlog import LaneBacklog, redis_lanes_for_key
from app.services.messaging.lanes.hash_router import (
    KafkaLaneRouter,
    RedisLaneRouter,
    build_lane_router,
    stable_lane,
)
from app.services.messaging.lanes.interface import (
    DEFAULT_LANE_KEY,
    LaneConfig,
    LaneRouter,
)
from app.services.messaging.lanes.producer import LaneAwareProducer

__all__ = [
    "DEFAULT_LANE_KEY",
    "KafkaLaneRouter",
    "LaneAwareProducer",
    "LaneBacklog",
    "LaneConfig",
    "LaneRouter",
    "RedisLaneRouter",
    "build_lane_router",
    "redis_lanes_for_key",
    "stable_lane",
]
