"""Unit tests for lane routing."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from hashlib import sha256

import pytest

from app.services.messaging.lanes.hash_router import (
    KafkaLaneRouter,
    RedisLaneRouter,
    build_lane_router,
    stable_lane,
)
from app.services.messaging.lanes.interface import DEFAULT_LANE_KEY, LaneConfig

_TOPIC = "record-events"


class TestStableLane:
    def test_same_key_always_lands_on_the_same_lane(self):
        first = [stable_lane(f"conn-{i}", 16) for i in range(50)]
        second = [stable_lane(f"conn-{i}", 16) for i in range(50)]
        assert first == second

    def test_lane_is_in_range(self):
        assert all(0 <= stable_lane(f"k{i}", 8) < 8 for i in range(200))

    def test_lane_comes_from_a_seed_independent_digest(self):
        """``hash()`` randomises string hashing per process, so two producer
        replicas would place the same key on different lanes and per-lane
        backpressure would be meaningless. The lane must come from a fixed
        digest instead."""
        digest = sha256(b"connector-42").digest()[:8]
        assert stable_lane("connector-42", 16) == int.from_bytes(digest, "big") % 16

    def test_lane_matches_the_node_producer(self):
        """Pinned vectors shared with the Node lane utils. Node publishes
        record events too, so if the two disagree a connector lands on
        different lanes depending on which service produced the event, and
        the consumer's per-lane view stops meaning anything.

        These exact pairs are asserted in
        backend/nodejs/apps/tests/libs/utils/lane.utils.test.ts.
        """
        assert stable_lane("conn-1", 8) == 5
        assert stable_lane("connector-42", 8) == 4
        assert stable_lane("org-1", 8) == 4

    def test_lane_is_stable_across_interpreter_runs(self):
        """Belt and braces on the above: recompute in a subprocess started
        with a different PYTHONHASHSEED and require the same answer."""
        script = (
            "from app.services.messaging.lanes.hash_router import stable_lane;"
            "print(stable_lane('connector-42', 16))"
        )
        # cwd and PYTHONPATH are pinned to the backend root rather than
        # inherited: pytest can be invoked from the repository root, where
        # `app` is not importable, and the subprocess would then fail for a
        # reason that has nothing to do with lane stability.
        backend_root = Path(__file__).resolve().parents[4]
        runs = {
            subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True,
                text=True,
                check=True,
                cwd=backend_root,
                env={
                    **os.environ,
                    "PYTHONHASHSEED": seed,
                    "PYTHONPATH": str(backend_root),
                },
            ).stdout.strip()
            for seed in ("0", "1", "12345")
        }
        assert len(runs) == 1
        assert runs == {str(stable_lane("connector-42", 16))}

    def test_single_lane_is_always_zero(self):
        assert stable_lane("anything", 1) == 0

    def test_keys_spread_across_lanes(self):
        lanes = {stable_lane(f"connector-{i}", 16) for i in range(200)}
        assert len(lanes) == 16, "hash should use the whole lane space"


class TestRedisLaneRouter:
    def test_routes_to_a_lane_stream(self):
        router = RedisLaneRouter(8)
        topic, key = router.route(_TOPIC, "conn-1")
        assert topic == f"{_TOPIC}.{stable_lane('conn-1', 8)}"
        assert key == "conn-1"

    def test_two_keys_can_reach_different_streams(self):
        router = RedisLaneRouter(16)
        destinations = {router.route(_TOPIC, f"conn-{i}")[0] for i in range(50)}
        assert len(destinations) > 1

    def test_missing_key_uses_the_shared_default_lane(self):
        router = RedisLaneRouter(8)
        topic, _key = router.route(_TOPIC, None)
        assert topic == f"{_TOPIC}.{stable_lane(DEFAULT_LANE_KEY, 8)}"

    def test_lane_topics_keeps_the_pre_lane_stream_subscribed(self):
        """An install that turns laning on must still drain what was written
        to the base stream before the switch."""
        topics = RedisLaneRouter(4).lane_topics(_TOPIC)
        assert topics[0] == _TOPIC
        assert topics[1:] == [f"{_TOPIC}.{i}" for i in range(4)]


class TestKafkaLaneRouter:
    def test_topic_is_unchanged_and_key_becomes_the_lane_key(self):
        """On Kafka a lane is a partition, so the router only places the key
        and lets the broker's partitioner choose."""
        router = KafkaLaneRouter(8)
        topic, key = router.route(_TOPIC, "conn-1")
        assert topic == _TOPIC
        assert key == "conn-1"

    def test_missing_key_falls_back_to_the_shared_sentinel(self):
        """Not None: a null key round-robins across partitions, which would
        scatter unattributable events over every lane."""
        _topic, key = KafkaLaneRouter(8).route(_TOPIC, None)
        assert key == DEFAULT_LANE_KEY

    def test_lane_topics_is_just_the_topic(self):
        assert KafkaLaneRouter(8).lane_topics(_TOPIC) == [_TOPIC]


class TestBuildLaneRouter:
    @pytest.mark.parametrize(
        ("is_kafka", "expected"),
        [(True, KafkaLaneRouter), (False, RedisLaneRouter)],
    )
    def test_picks_the_broker_specific_router(self, is_kafka, expected):
        router = build_lane_router(LaneConfig(lane_count=4), is_kafka=is_kafka)
        assert isinstance(router, expected)


class TestLaneConfig:
    def test_disabled_at_one_lane(self):
        assert LaneConfig().lane_count == 1
        assert not LaneConfig().enabled

    def test_enabled_above_one_lane(self):
        assert LaneConfig(lane_count=2).enabled

    def test_defaults_to_connector_level_and_indexing_topic_only(self):
        config = LaneConfig()
        assert config.lane_key_field == "connectorId"
        assert config.laned_topics == ("record-events",)
