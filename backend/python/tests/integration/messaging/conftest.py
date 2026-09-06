"""Fixtures for messaging integration tests against real brokers.

These tests need Docker services:

  docker compose -f deployment/docker-compose/docker-compose.integration.messaging.yml up -d
  cd backend/python && pytest tests/integration/messaging -m integration

They skip cleanly when the brokers are not reachable, so a normal unit run is
unaffected.

Environment:
  KAFKA_IT_BOOTSTRAP  (default: localhost:29192)
  REDIS_IT_HOST       (default: localhost)
  REDIS_IT_PORT       (default: 6389)
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid

import pytest

KAFKA_BOOTSTRAP = os.environ.get("KAFKA_IT_BOOTSTRAP", "localhost:29192")
REDIS_HOST = os.environ.get("REDIS_IT_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_IT_PORT", "6389"))

# Real brokers are slower than the fakes; give the drain loops room without
# letting a genuinely stuck test hang the suite.
DRAIN_TIMEOUT_SECONDS = 60.0


@pytest.fixture
def logger():
    return logging.getLogger("messaging_integration")


@pytest.fixture
def unique_suffix() -> str:
    """Topics and groups are per-test, so a rerun never inherits offsets or
    a pending list from the run before it."""
    return uuid.uuid4().hex[:10]


@pytest.fixture(scope="session")
async def kafka_available() -> str:
    """Bootstrap address, or skip the module if no broker answers."""
    aiokafka = pytest.importorskip("aiokafka", reason="aiokafka not installed")
    client = aiokafka.AIOKafkaProducer(bootstrap_servers=KAFKA_BOOTSTRAP)
    try:
        await asyncio.wait_for(client.start(), timeout=15.0)
    except Exception as exc:
        pytest.skip(f"Kafka not available at {KAFKA_BOOTSTRAP} — {exc}")
    finally:
        try:
            await client.stop()
        except Exception:
            pass
    return KAFKA_BOOTSTRAP


@pytest.fixture(scope="session")
async def redis_available() -> tuple[str, int]:
    """Host/port, or skip the module if no Redis answers."""
    pytest.importorskip("redis", reason="redis package not installed")
    from redis.asyncio import Redis

    client = Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    try:
        await asyncio.wait_for(client.ping(), timeout=10.0)
    except Exception as exc:
        pytest.skip(f"Redis not available at {REDIS_HOST}:{REDIS_PORT} — {exc}")
    finally:
        await client.aclose()
    return REDIS_HOST, REDIS_PORT


async def create_kafka_topic(bootstrap: str, topic: str, partitions: int) -> None:
    """Create a topic with an explicit partition count.

    Auto-create is off on the test broker precisely so a topic cannot appear
    with one partition before the test asks for several -- which is the
    condition the lane tests exist to exercise.
    """
    from aiokafka.admin import AIOKafkaAdminClient, NewTopic

    admin = AIOKafkaAdminClient(bootstrap_servers=bootstrap)
    await admin.start()
    try:
        await admin.create_topics(
            [NewTopic(name=topic, num_partitions=partitions, replication_factor=1)]
        )
    finally:
        await admin.close()


async def delete_kafka_topic(bootstrap: str, topic: str) -> None:
    from aiokafka.admin import AIOKafkaAdminClient

    admin = AIOKafkaAdminClient(bootstrap_servers=bootstrap)
    await admin.start()
    try:
        await admin.delete_topics([topic])
    except Exception:
        pass
    finally:
        await admin.close()


async def committed_offsets(bootstrap: str, group: str, topic: str) -> dict[int, int]:
    """What the group has actually committed, read back from the broker.

    Asserting against this rather than against the consumer's own in-memory
    tracker is the point of an integration test: it is the number a restart
    would resume from.
    """
    from aiokafka import AIOKafkaConsumer, TopicPartition
    from aiokafka.admin import AIOKafkaAdminClient

    # Partitions come from the admin client rather than the consumer's own
    # metadata cache: an unsubscribed consumer's start() does not wait for
    # topic metadata, so partitions_for_topic can return None and the caller
    # would silently assert against an empty offset map.
    admin = AIOKafkaAdminClient(bootstrap_servers=bootstrap)
    await admin.start()
    try:
        metadata = await admin.describe_topics([topic])
    finally:
        await admin.close()

    partitions = [
        p["partition"]
        for entry in metadata
        if entry.get("topic") == topic
        for p in entry.get("partitions", [])
    ]
    if not partitions:
        raise AssertionError(f"topic {topic} reported no partitions")

    consumer = AIOKafkaConsumer(bootstrap_servers=bootstrap, group_id=group)
    await consumer.start()
    try:
        result: dict[int, int] = {}
        for partition in partitions:
            offset = await consumer.committed(TopicPartition(topic, partition))
            if offset is not None:
                result[partition] = offset
        return result
    finally:
        await consumer.stop()
