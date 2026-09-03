"""Redis Streams producer/consumer against a real Redis Cluster (R1, R17).

Companion to `tests/unit/services/redis/test_streams_contract.py` (fake
transport): topics are chosen so they provably land on different slots on
*this* cluster, so `StreamReadPlanner` grouping into one `XREADGROUP` per
slot is exercised against a real multi-master topology, not
`FakeClusterRedis`'s single-node approximation.

`TestMasterFailoverOnARealCluster` restarts one master's `redis-server`
process mid-consume (`supervisorctl restart redis-1` inside the
`redis-cluster-it` container -- see
`docker-compose.integration.redis-cluster.yml`; all three masters run in
one container under supervisord, each independently restartable) while the
other two masters stay up, and skips cleanly wherever that is not possible
(no Docker CLI, or the compose container is not running under its expected
name).
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess

import pytest

from app.services.messaging.config import RedisStreamsConfig, StreamMessage
from app.services.messaging.redis_streams.consumer import RedisStreamsConsumer
from app.services.messaging.redis_streams.producer import RedisStreamsProducer
from app.services.redis.connection_provider_factory import get_redis_provider

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_CLUSTER_CONTAINER = "redis-cluster-it"
_RESTARTABLE_MASTER = "redis-1"


def _topics_in_different_slots(provider, base: str, count: int = 3) -> list[str]:
    topics: list[str] = []
    seen_slots: set[int] = set()
    candidate = 0
    while len(topics) < count:
        name = f"{base}.{candidate}"
        slot = provider.key_slot(name)
        if slot not in seen_slots:
            seen_slots.add(slot)
            topics.append(name)
        candidate += 1
        if candidate > 1000:
            raise AssertionError("could not find topics in distinct slots")
    return topics


def _config(topics: list[str], **overrides: object) -> RedisStreamsConfig:
    base = dict(
        topics=topics,
        group_id="it-cluster-group",
        client_id="it-consumer-1",
        batch_size=10,
        block_ms=200,
        claim_min_idle_ms=0,
    )
    base.update(overrides)
    return RedisStreamsConfig(**base)


@pytest.fixture
def topics(redis_cluster_available, unique_suffix):
    provider = get_redis_provider(mode="cluster")
    return _topics_in_different_slots(provider, f"it-lane-{unique_suffix}")


class TestStreamsOnARealCluster:
    async def test_produce_to_slot_spanning_topics_then_consume_all(
        self, topics
    ) -> None:
        producer = RedisStreamsProducer(logging.getLogger("it-producer"), _config(topics))
        await producer.initialize()
        try:
            for i, topic in enumerate(topics):
                for j in range(3):
                    await producer.send_message(
                        topic, {"eventType": "test", "payload": {"i": i, "j": j}}
                    )
        finally:
            await producer.cleanup()

        consumer = RedisStreamsConsumer(logging.getLogger("it-consumer"), _config(topics))
        await consumer.initialize()
        try:
            # Real StreamReadPlanner against real cluster slots: must not
            # raise ClusterCrossSlotError even though `topics` spans three
            # different masters.
            results = await consumer._read_new_messages()
            total = sum(len(messages) for _stream, messages in results)
            assert total == len(topics) * 3
        finally:
            await consumer.cleanup()


class TestMasterFailoverOnARealCluster:
    @pytest.mark.timeout(90)
    async def test_consumer_resumes_after_a_master_restart_mid_consume(
        self, topics
    ) -> None:
        docker = shutil.which("docker")
        if docker is None:
            pytest.skip("docker CLI not available")
        running = subprocess.run(
            [docker, "ps", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if _CLUSTER_CONTAINER not in running.stdout:
            pytest.skip(f"{_CLUSTER_CONTAINER} container is not running")

        producer = RedisStreamsProducer(logging.getLogger("it-producer"), _config(topics))
        await producer.initialize()
        try:
            for topic in topics:
                for j in range(5):
                    await producer.send_message(
                        topic, {"eventType": "test", "payload": {"j": j}}
                    )
        finally:
            await producer.cleanup()

        # Give the default `appendfsync everysec` AOF policy a chance to
        # persist these writes before the restart below -- otherwise this
        # test is asserting data durability across an abrupt kill (which a
        # 0-replica shard cannot promise) rather than client reconnection,
        # which is what it means to test.
        await asyncio.sleep(1.5)

        processed: list[dict] = []

        async def handler(message: StreamMessage) -> bool:
            processed.append(message.payload)
            return True

        consumer = RedisStreamsConsumer(logging.getLogger("it-consumer"), _config(topics))
        consumer.message_handler = handler
        await consumer.initialize()
        try:
            # Restart one master's redis-server process mid-consume (the
            # other two stay up): the cluster client must recover -- via a
            # reconnect or a topology refresh -- rather than the consumer
            # wedging forever.
            subprocess.run(
                [
                    docker, "exec", _CLUSTER_CONTAINER,
                    "supervisorctl", "restart", _RESTARTABLE_MASTER,
                ],
                check=True,
                timeout=30,
            )

            deadline = asyncio.get_event_loop().time() + 60.0  # keep under the 90s test timeout
            total_expected = len(topics) * 5
            while len(processed) < total_expected:
                if asyncio.get_event_loop().time() > deadline:
                    break
                try:
                    results = await consumer._read_new_messages()
                except Exception:
                    # Topology mid-reshuffle right after the restart; retry.
                    await asyncio.sleep(0.5)
                    continue
                for stream_name, messages in results:
                    for message_id, fields in messages:
                        success, is_terminal = (
                            await consumer._process_message_with_classification(
                                stream_name, message_id, fields
                            )
                        )
                        await consumer._finalize_message(
                            stream_name, message_id, success, is_terminal
                        )
                if not results:
                    await asyncio.sleep(0.5)

            assert len(processed) == total_expected, (
                f"only recovered {len(processed)} of {total_expected} messages "
                "after the master restart"
            )
        finally:
            await consumer.cleanup()
