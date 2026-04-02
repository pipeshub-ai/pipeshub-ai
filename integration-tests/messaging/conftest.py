"""Shared fixtures for messaging integration tests.

Env vars:
  KAFKA_BOOTSTRAP_SERVERS  – default localhost:9092
  REDIS_HOST               – default localhost
  REDIS_PORT               – default 6379
  REDIS_PASSWORD           – optional
"""

import asyncio
import json
import logging
import os
import uuid
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from redis.asyncio import Redis

logger = logging.getLogger("messaging-integration")

# ---------------------------------------------------------------------------
# env helpers
# ---------------------------------------------------------------------------

def _kafka_brokers() -> str:
    return os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")


def _redis_host() -> str:
    return os.getenv("REDIS_HOST", "localhost")


def _redis_port() -> int:
    return int(os.getenv("REDIS_PORT", "6379"))


def _redis_password() -> str | None:
    return os.getenv("REDIS_PASSWORD") or None


# ---------------------------------------------------------------------------
# Kafka fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def kafka_producer() -> AsyncGenerator[AIOKafkaProducer, None]:
    producer = AIOKafkaProducer(
        bootstrap_servers=_kafka_brokers(),
        value_serializer=lambda v: json.dumps(v).encode(),
        key_serializer=lambda k: k.encode() if k else None,
    )
    await producer.start()
    yield producer
    await producer.stop()


@pytest_asyncio.fixture
async def kafka_consumer_factory():
    """Factory that creates a consumer subscribed to given topics with a unique group."""
    consumers: list[AIOKafkaConsumer] = []

    async def _make(topics: list[str]) -> AIOKafkaConsumer:
        consumer = AIOKafkaConsumer(
            *topics,
            bootstrap_servers=_kafka_brokers(),
            group_id=f"integration-test-{uuid.uuid4().hex[:8]}",
            auto_offset_reset="earliest",
            enable_auto_commit=True,
            value_deserializer=lambda v: json.loads(v.decode()),
            consumer_timeout_ms=10_000,
        )
        await consumer.start()
        consumers.append(consumer)
        return consumer

    yield _make

    for c in consumers:
        await c.stop()


# ---------------------------------------------------------------------------
# Redis fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def redis_client() -> AsyncGenerator[Redis, None]:
    client = Redis(
        host=_redis_host(),
        port=_redis_port(),
        password=_redis_password(),
        decode_responses=True,
    )
    await client.ping()
    yield client
    await client.close()


@pytest_asyncio.fixture
async def redis_stream_cleanup(redis_client: Redis):
    """Returns a list; append stream names to auto-delete after the test."""
    streams: list[str] = []
    yield streams
    for stream in streams:
        try:
            await redis_client.delete(stream)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

async def consume_kafka_messages(
    consumer: AIOKafkaConsumer,
    expected: int,
    timeout: float = 15.0,
) -> list[dict]:
    """Read *expected* messages from a Kafka consumer within *timeout* seconds."""
    received: list[dict] = []
    deadline = asyncio.get_event_loop().time() + timeout
    while len(received) < expected:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            break
        batch = await consumer.getmany(timeout_ms=int(remaining * 1000), max_records=expected - len(received))
        for _tp, msgs in batch.items():
            for msg in msgs:
                received.append(msg.value)
    return received


async def consume_redis_messages(
    redis: Redis,
    stream: str,
    group: str,
    consumer_name: str,
    expected: int,
    timeout: float = 15.0,
) -> list[dict]:
    """Read *expected* messages from a Redis stream consumer group within *timeout*."""
    # ensure group exists
    try:
        await redis.xgroup_create(stream, group, id="0", mkstream=True)
    except Exception:
        pass  # BUSYGROUP

    received: list[dict] = []
    deadline = asyncio.get_event_loop().time() + timeout
    while len(received) < expected:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            break
        block_ms = max(100, int(remaining * 1000))
        results = await redis.xreadgroup(
            groupname=group,
            consumername=consumer_name,
            streams={stream: ">"},
            count=expected - len(received),
            block=block_ms,
        )
        if not results:
            continue
        for _stream_name, entries in results:
            for msg_id, fields in entries:
                value = fields.get("value")
                if value:
                    received.append(json.loads(value))
                await redis.xack(stream, group, msg_id)
    return received
