"""Producer -> `StreamReadPlanner` -> consumer, end to end against a real
in-memory Redis (Phase 6).

Unlike `test_redis_streams_consumer.py`, which mocks `self.redis` to unit-test
each branch of `_drain_pending`/`_read_new_messages` in isolation, this drives
the real `RedisStreamsProducer` and `RedisStreamsConsumer` against `FakeRedis`
(standalone) and `FakeClusterRedis` (cluster) so a `StreamReadPlanner`
regression that only shows up when live `XREADGROUP` calls actually spans
slots -- e.g. reverting to one `XREADGROUP` for every subscribed topic (R1)
-- fails here even though the mocked unit tests would not catch it.
"""
from __future__ import annotations

from logging import Logger, getLogger
from unittest.mock import MagicMock

import pytest

from app.services.messaging.config import RedisStreamsConfig, StreamMessage
from app.services.messaging.redis_streams.consumer import RedisStreamsConsumer
from app.services.messaging.redis_streams.producer import RedisStreamsProducer
from tests.support.fake_cluster_redis import FakeClusterRedis
from tests.support.fake_redis_connection_provider import FakeRedisConnectionProvider

fakeredis_aioredis = pytest.importorskip("fakeredis.aioredis")

# record-events.0 and record-events.1 hash to different slots (see
# `redis.crc.key_slot`); record-events.2 lands in a third slot again. A
# provider that is not cluster-aware raises CROSSSLOT the moment a consumer
# subscribes to more than one of these in a single XREADGROUP.
_LANE_TOPICS = ["record-events.0", "record-events.1", "record-events.2"]


def _make_provider(*, is_cluster: bool) -> FakeRedisConnectionProvider:
    return FakeRedisConnectionProvider(is_cluster=is_cluster)


PROVIDERS = [
    pytest.param(lambda: _make_provider(is_cluster=False), id="standalone"),
    pytest.param(lambda: _make_provider(is_cluster=True), id="cluster"),
]


def _config(**overrides: object) -> RedisStreamsConfig:
    base = dict(
        topics=_LANE_TOPICS,
        group_id="test-group",
        client_id="consumer-1",
        batch_size=10,
        block_ms=50,
        claim_min_idle_ms=0,
    )
    base.update(overrides)
    return RedisStreamsConfig(**base)


@pytest.mark.asyncio
@pytest.mark.parametrize("make_provider", PROVIDERS)
class TestStreamsContract:
    async def test_produce_to_laned_topics_then_consume_all_via_one_consumer(
        self, make_provider
    ) -> None:
        provider = make_provider()
        producer = RedisStreamsProducer(getLogger("test"), _config(), provider=provider)
        await producer.initialize()
        for i, topic in enumerate(_LANE_TOPICS):
            for j in range(3):
                await producer.send_message(topic, {"eventType": "test", "payload": {"i": i, "j": j}})

        consumer = RedisStreamsConsumer(getLogger("test"), _config(), provider=provider)
        await consumer.initialize()

        # Exercises the real StreamReadPlanner against the real client --
        # must not raise ClusterCrossSlotError even though the three lane
        # topics span three different slots under the cluster provider.
        results = await consumer._read_new_messages()

        total_messages = sum(len(messages) for _stream, messages in results)
        assert total_messages == 9

        await producer.cleanup()
        await consumer.cleanup()

    async def test_pel_recovery_after_a_simulated_consumer_crash(
        self, make_provider
    ) -> None:
        """Consumer A reads (and thereby claims) a message under its own
        name, then "crashes" before acking. Consumer B, a different
        process/name in the same group, must recover it via
        `_drain_pending`'s XAUTOCLAIM phase."""
        provider = make_provider()
        producer = RedisStreamsProducer(getLogger("test"), _config(), provider=provider)
        await producer.initialize()
        await producer.send_message(
            _LANE_TOPICS[0], {"eventType": "test", "payload": {"crash": True}}
        )

        consumer_a = RedisStreamsConsumer(
            getLogger("test"), _config(client_id="consumer-a"), provider=provider
        )
        await consumer_a.initialize()
        claimed = await consumer_a._read_new_messages()
        assert sum(len(messages) for _s, messages in claimed) == 1
        # consumer_a "crashes" here: never acks, never calls cleanup on the
        # message -- it stays in the PEL under consumer_a's name.

        processed: list[StreamMessage] = []

        async def handler(message: StreamMessage) -> bool:
            processed.append(message)
            return True

        consumer_b = RedisStreamsConsumer(
            getLogger("test"), _config(client_id="consumer-b"), provider=provider
        )
        consumer_b.running = True
        consumer_b.message_handler = handler
        await consumer_b.initialize()

        recovered = await consumer_b._drain_pending()

        assert recovered is True
        assert len(processed) == 1
        assert processed[0].payload == {"crash": True}

        await producer.cleanup()
        await consumer_a.cleanup()
        await consumer_b.cleanup()
