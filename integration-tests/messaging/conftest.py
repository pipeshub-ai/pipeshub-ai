"""Shared fixtures for messaging integration tests.

Env vars:
  MESSAGE_BROKER           – "kafka" or "redis" (default: redis)
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
from typing import Any, AsyncGenerator

import pytest
import pytest_asyncio
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from redis.asyncio import Redis

logger = logging.getLogger("messaging-integration")

# ---------------------------------------------------------------------------
# env helpers
# ---------------------------------------------------------------------------

def _kafka_brokers() -> str:
    return os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:29092")


def _redis_host() -> str:
    return os.getenv("REDIS_HOST", "localhost")


def _redis_port() -> int:
    return int(os.getenv("REDIS_PORT", "6379"))


def _redis_password() -> str | None:
    return os.getenv("REDIS_PASSWORD") or None


# ---------------------------------------------------------------------------
# Kafka fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(loop_scope="session")
async def kafka_producer() -> AsyncGenerator[AIOKafkaProducer, None]:
    producer = AIOKafkaProducer(
        bootstrap_servers=_kafka_brokers(),
        value_serializer=lambda v: json.dumps(v).encode(),
        key_serializer=lambda k: k.encode() if k else None,
    )
    await producer.start()
    yield producer
    await producer.stop()


@pytest_asyncio.fixture(loop_scope="session")
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

@pytest_asyncio.fixture(loop_scope="session")
async def redis_client() -> AsyncGenerator[Redis, None]:
    client = Redis(
        host=_redis_host(),
        port=_redis_port(),
        password=_redis_password(),
        decode_responses=True,
    )
    await client.ping()
    yield client
    await client.aclose()


@pytest_asyncio.fixture(loop_scope="session")
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


# ---------------------------------------------------------------------------
# EventConsumer — reusable across test modules
# ---------------------------------------------------------------------------

_MAX_DRAIN = 50
_DEFAULT_EVENT_TIMEOUT = 30.0


def _message_broker() -> str:
    return os.getenv("MESSAGE_BROKER", "redis").lower()


def _match_event(event: dict, event_type: str, record_id: str | None) -> bool:
    if event.get("eventType") != event_type:
        return False
    if record_id is None:
        return True
    return event.get("payload", {}).get("recordId") == record_id


def _match_event_by(event: dict, event_type: str, payload_key: str | None, payload_value: str | None) -> bool:
    """Match event by type and an arbitrary payload field."""
    if event.get("eventType") != event_type:
        return False
    if payload_key is None:
        return True
    return event.get("payload", {}).get(payload_key) == payload_value


class EventConsumer:
    """Long-lived consumer that drains a topic and searches for matching events.

    Non-matching events are buffered so they can be found by later
    ``wait_for_event`` calls.
    """

    def __init__(self) -> None:
        self._group = f"e2e-test-{uuid.uuid4().hex[:8]}"
        self._consumer_name = "e2e-consumer"
        self._redis: Redis | None = None
        self._kafka_consumer: Any = None
        self._buffer: list[dict] = []

    def _check_buffer(self, match_fn) -> dict | None:
        for i, event in enumerate(self._buffer):
            if match_fn(event):
                return self._buffer.pop(i)
        return None

    # -- Redis --

    async def _ensure_redis(self, topic: str) -> Redis:
        if self._redis is None:
            self._redis = Redis(
                host=_redis_host(),
                port=_redis_port(),
                password=_redis_password(),
                decode_responses=True,
            )
            await self._redis.ping()
        try:
            await self._redis.xgroup_create(topic, self._group, id="$", mkstream=True)
        except Exception:
            pass
        return self._redis

    async def _drain_redis(self, topic: str, match_fn, timeout: float) -> dict | None:
        client = await self._ensure_redis(topic)
        deadline = asyncio.get_event_loop().time() + timeout
        seen = 0
        matched: dict | None = None
        while seen < _MAX_DRAIN:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            block_ms = max(100, int(remaining * 1000))
            results = await client.xreadgroup(
                groupname=self._group,
                consumername=self._consumer_name,
                streams={topic: ">"},
                count=10,
                block=block_ms,
            )
            if not results:
                if matched:
                    break
                continue
            for _stream_name, entries in results:
                for msg_id, fields in entries:
                    await client.xack(topic, self._group, msg_id)
                    value = fields.get("value")
                    if not value:
                        continue
                    seen += 1
                    try:
                        event = json.loads(value)
                    except json.JSONDecodeError:
                        continue
                    logger.debug("Consumed event: %s (payload keys: %s)",
                                 event.get("eventType"),
                                 list(event.get("payload", {}).keys()))
                    if matched is None and match_fn(event):
                        matched = event
                    else:
                        self._buffer.append(event)
            if matched:
                return matched
        return None

    # -- Kafka --

    async def _ensure_kafka(self, topic: str) -> Any:
        if self._kafka_consumer is None:
            self._kafka_consumer = AIOKafkaConsumer(
                topic,
                bootstrap_servers=_kafka_brokers(),
                group_id=self._group,
                auto_offset_reset="latest",
                enable_auto_commit=True,
                value_deserializer=lambda v: json.loads(v.decode()),
            )
            await self._kafka_consumer.start()
        return self._kafka_consumer

    async def _drain_kafka(self, topic: str, match_fn, timeout: float) -> dict | None:
        consumer = await self._ensure_kafka(topic)
        deadline = asyncio.get_event_loop().time() + timeout
        seen = 0
        matched: dict | None = None
        while seen < _MAX_DRAIN:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            batch = await consumer.getmany(
                timeout_ms=max(100, int(remaining * 1000)),
                max_records=10,
            )
            for _tp, msgs in batch.items():
                for msg in msgs:
                    seen += 1
                    event = msg.value
                    logger.debug("Consumed event: %s (payload keys: %s)",
                                 event.get("eventType"),
                                 list(event.get("payload", {}).keys()))
                    if matched is None and match_fn(event):
                        matched = event
                    else:
                        self._buffer.append(event)
            if matched:
                return matched
        return None

    # -- Public API --

    async def wait_for_event(
        self,
        topic: str,
        event_type: str,
        record_id: str | None = None,
        timeout: float = _DEFAULT_EVENT_TIMEOUT,
    ) -> dict | None:
        """Wait for an event matching *event_type* and optionally *record_id*
        (matched against ``payload.recordId``).  Non-matching events are buffered."""
        match_fn = lambda e: _match_event(e, event_type, record_id)
        hit = self._check_buffer(match_fn)
        if hit:
            return hit
        broker = _message_broker()
        if broker == "redis":
            return await self._drain_redis(topic, match_fn, timeout)
        else:
            return await self._drain_kafka(topic, match_fn, timeout)

    async def wait_for_event_by(
        self,
        topic: str,
        event_type: str,
        payload_key: str | None = None,
        payload_value: str | None = None,
        timeout: float = _DEFAULT_EVENT_TIMEOUT,
    ) -> dict | None:
        """Wait for an event matching *event_type* and an arbitrary payload field.

        Example: ``wait_for_event_by("entity-events", "userAdded", "email", "alice@acme.com")``
        """
        match_fn = lambda e: _match_event_by(e, event_type, payload_key, payload_value)
        hit = self._check_buffer(match_fn)
        if hit:
            return hit
        broker = _message_broker()
        if broker == "redis":
            return await self._drain_redis(topic, match_fn, timeout)
        else:
            return await self._drain_kafka(topic, match_fn, timeout)

    async def close(self) -> None:
        if self._redis:
            try:
                await self._redis.aclose()
            except Exception:
                pass
            self._redis = None
        if self._kafka_consumer:
            try:
                await self._kafka_consumer.stop()
            except Exception:
                pass
            self._kafka_consumer = None


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def event_consumer():
    """Module-scoped event consumer — one consumer group per test module."""
    consumer = EventConsumer()
    yield consumer
    await consumer.close()


# ---------------------------------------------------------------------------
# Legacy helpers (used by test_messaging_integration.py)
# ---------------------------------------------------------------------------

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
