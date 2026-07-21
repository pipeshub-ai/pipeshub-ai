"""Shared fixtures for messaging integration tests.

Env vars:
  MESSAGE_BROKER           – "kafka" or "redis" (set in integration-tests/.env.local)
  KAFKA_BOOTSTRAP_SERVERS  – default localhost:29092 (host listener for integration compose)
  REDIS_HOST               – default localhost
  REDIS_PORT               – default 6379
  REDIS_PASSWORD           – optional
"""

import asyncio
import json
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest_asyncio
from aiokafka import AIOKafkaConsumer
from redis.asyncio import Redis

_BACKEND_PY = Path(__file__).resolve().parent.parent.parent / "backend" / "python"
if str(_BACKEND_PY) not in sys.path:
    sys.path.insert(0, str(_BACKEND_PY))

from app.services.messaging.config import ConsumerType, MessageBrokerType, RedisStreamsConfig
from app.services.messaging.kafka.config.kafka_config import (
    KafkaConsumerConfig,
    KafkaProducerConfig,
)
from app.services.messaging.messaging_factory import MessagingFactory

logger = logging.getLogger("messaging-integration")

_MAX_DRAIN = 50
_DEFAULT_EVENT_TIMEOUT = 30.0


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


def _get_broker_type() -> MessageBrokerType:
    """Resolve active broker from MESSAGE_BROKER env var."""
    raw = os.getenv("MESSAGE_BROKER", "redis").lower().strip()
    try:
        return MessageBrokerType(raw)
    except ValueError:
        valid = ", ".join(f"'{m.value}'" for m in MessageBrokerType)
        raise RuntimeError(
            f"MESSAGE_BROKER must be one of {valid}, got '{raw}' "
            "(see integration-tests/.env.local)"
        ) from None


def _create_topic_config(topics: list[str], broker: MessageBrokerType):
    """Create broker-specific consumer config for topics/streams."""
    if broker == MessageBrokerType.KAFKA:
        return KafkaConsumerConfig(
            topics=topics,
            bootstrap_servers=_kafka_brokers().split(","),
            client_id=f"test-{uuid.uuid4().hex[:8]}",
            group_id=f"test-group-{uuid.uuid4().hex[:8]}",
            auto_offset_reset="earliest",
            enable_auto_commit=True,
        )
    return RedisStreamsConfig(
        topics=topics,
        host=_redis_host(),
        port=_redis_port(),
        password=_redis_password(),
        client_id=f"test-{uuid.uuid4().hex[:8]}",
        group_id=f"test-group-{uuid.uuid4().hex[:8]}",
    )


def _create_producer_config(broker: MessageBrokerType):
    """Create broker-specific producer config."""
    if broker == MessageBrokerType.KAFKA:
        return KafkaProducerConfig(
            bootstrap_servers=_kafka_brokers().split(","),
            client_id=f"test-producer-{uuid.uuid4().hex[:8]}",
        )
    return RedisStreamsConfig(
        host=_redis_host(),
        port=_redis_port(),
        password=_redis_password(),
    )


# ---------------------------------------------------------------------------
# Unified messaging fixtures (IMessagingProducer / IMessagingConsumer)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def messaging_producer():
    """Generic producer for the broker specified by MESSAGE_BROKER."""
    broker = _get_broker_type()
    config = _create_producer_config(broker)
    producer = MessagingFactory.create_producer(logger, config, broker)
    await producer.initialize()
    yield producer
    await producer.cleanup()


@pytest_asyncio.fixture
async def messaging_consumer_factory():
    """Factory that creates consumers for specific topics on the active broker."""
    broker = _get_broker_type()
    consumers = []

    def _make(topics: list[str], consumer_type: ConsumerType = ConsumerType.SIMPLE):
        config = _create_topic_config(topics, broker)
        consumer = MessagingFactory.create_consumer(
            logger, config, broker, consumer_type
        )
        consumers.append(consumer)
        return consumer

    yield _make

    for consumer in consumers:
        try:
            if consumer.is_running():
                await consumer.stop()
        except Exception:
            pass


@pytest_asyncio.fixture
async def messaging_cleanup():
    """Collect topic/stream names for teardown (Redis streams are deleted)."""
    cleanup_items: list[str] = []
    yield cleanup_items

    if _get_broker_type() != MessageBrokerType.REDIS:
        return

    client = Redis(
        host=_redis_host(),
        port=_redis_port(),
        password=_redis_password(),
        decode_responses=True,
    )
    try:
        for stream in cleanup_items:
            try:
                await client.delete(stream)
            except Exception:
                pass
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# EventConsumer — topic polling for API-level E2E tests
# ---------------------------------------------------------------------------


def _match_event(event: dict, event_type: str, record_id: str | None) -> bool:
    if event.get("eventType") != event_type:
        return False
    if record_id is None:
        return True
    return event.get("payload", {}).get("recordId") == record_id


def _match_event_by(
    event: dict,
    event_type: str,
    payload_key: str | None,
    payload_value: str | None,
) -> bool:
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
        self._kafka_consumer: AIOKafkaConsumer | None = None
        self._buffer: list[dict] = []

    def _check_buffer(self, match_fn) -> dict | None:
        for i, event in enumerate(self._buffer):
            if match_fn(event):
                return self._buffer.pop(i)
        return None

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
                    logger.debug(
                        "Consumed event: %s (payload keys: %s)",
                        event.get("eventType"),
                        list(event.get("payload", {}).keys()),
                    )
                    if matched is None and match_fn(event):
                        matched = event
                    else:
                        self._buffer.append(event)
            if matched:
                return matched
        return None

    async def _ensure_kafka(self, topic: str) -> AIOKafkaConsumer:
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
                    logger.debug(
                        "Consumed event: %s (payload keys: %s)",
                        event.get("eventType"),
                        list(event.get("payload", {}).keys()),
                    )
                    if matched is None and match_fn(event):
                        matched = event
                    else:
                        self._buffer.append(event)
            if matched:
                return matched
        return None

    async def wait_for_event(
        self,
        topic: str,
        event_type: str,
        record_id: str | None = None,
        timeout: float = _DEFAULT_EVENT_TIMEOUT,
    ) -> dict | None:
        """Wait for an event matching *event_type* and optionally *record_id*."""
        match_fn = lambda e: _match_event(e, event_type, record_id)
        hit = self._check_buffer(match_fn)
        if hit:
            return hit
        if _get_broker_type() == MessageBrokerType.REDIS:
            return await self._drain_redis(topic, match_fn, timeout)
        return await self._drain_kafka(topic, match_fn, timeout)

    async def wait_for_event_by(
        self,
        topic: str,
        event_type: str,
        payload_key: str | None = None,
        payload_value: str | None = None,
        timeout: float = _DEFAULT_EVENT_TIMEOUT,
    ) -> dict | None:
        """Wait for an event matching *event_type* and an arbitrary payload field."""
        match_fn = lambda e: _match_event_by(e, event_type, payload_key, payload_value)
        hit = self._check_buffer(match_fn)
        if hit:
            return hit
        if _get_broker_type() == MessageBrokerType.REDIS:
            return await self._drain_redis(topic, match_fn, timeout)
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
    """Module-scoped event consumer for API-level E2E tests."""
    consumer = EventConsumer()
    yield consumer
    await consumer.close()
