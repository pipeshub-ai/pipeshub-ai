"""Integration tests for Kafka and Redis Streams messaging.

Tests verify end-to-end produce → consume with real PipesHub event payloads,
ensuring no events are dropped under normal and stress conditions.

Run:
    cd integration-tests
    pytest messaging/ -v --timeout=120
"""

import asyncio
import json
import logging
import uuid

import pytest
from redis.asyncio import Redis

from messaging.conftest import (
    _kafka_brokers,
    _redis_host,
    _redis_password,
    _redis_port,
    consume_kafka_messages,
    consume_redis_messages,
)
from messaging.payloads import (
    ALL_AI_CONFIG_EVENTS,
    ALL_ENTITY_EVENTS,
    ALL_RECORD_EVENTS,
    ALL_SYNC_EVENTS,
    new_record_event,
)

logger = logging.getLogger("messaging-integration")

# PipesHub application-level classes
import sys
from pathlib import Path

# Add backend/python to sys.path so we can import the app modules
_BACKEND_PY = Path(__file__).resolve().parent.parent.parent / "backend" / "python"
if str(_BACKEND_PY) not in sys.path:
    sys.path.insert(0, str(_BACKEND_PY))

from app.services.messaging.config import RedisStreamsConfig, StreamMessage
from app.services.messaging.kafka.config.kafka_config import (
    KafkaConsumerConfig,
    KafkaProducerConfig,
)
from app.services.messaging.kafka.consumer.consumer import KafkaMessagingConsumer
from app.services.messaging.kafka.producer.producer import KafkaMessagingProducer
from app.services.messaging.redis_streams.consumer import RedisStreamsConsumer
from app.services.messaging.redis_streams.producer import RedisStreamsProducer

STRESS_COUNT = 500


def _unique_stream(base: str) -> str:
    return f"{base}-{uuid.uuid4().hex[:8]}"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
class TestRedisProducerConsumerE2E:
    """Use the actual RedisStreamsProducer and RedisStreamsConsumer classes
    to verify produce → consume delivers every event."""

    async def test_single_record_event(self, redis_stream_cleanup):
        topic = _unique_stream("e2e-record")
        redis_stream_cleanup.append(topic)
        config = RedisStreamsConfig(
            host=_redis_host(),
            port=_redis_port(),
            password=_redis_password(),
            client_id="e2e-consumer",
            group_id="e2e-group",
            topics=[topic],
        )

        producer = RedisStreamsProducer(logger, config)
        consumer = RedisStreamsConsumer(logger, config)

        received: list[StreamMessage] = []
        done = asyncio.Event()

        async def handler(msg: StreamMessage) -> bool:
            received.append(msg)
            done.set()
            return True

        try:
            await producer.initialize()
            await consumer.start(handler)

            event = new_record_event()
            await producer.send_message(topic, event, key=event["payload"]["recordId"])

            await asyncio.wait_for(done.wait(), timeout=10.0)

            assert len(received) == 1
            assert received[0].eventType == "newRecord"
            assert received[0].payload["recordId"] == event["payload"]["recordId"]
            assert received[0].payload["recordType"] == "FILE"
        finally:
            await consumer.stop()
            await producer.cleanup()

    async def test_all_record_event_types(self, redis_stream_cleanup):
        topic = _unique_stream("e2e-all-records")
        redis_stream_cleanup.append(topic)
        config = RedisStreamsConfig(
            host=_redis_host(),
            port=_redis_port(),
            password=_redis_password(),
            client_id="e2e-consumer",
            group_id="e2e-group",
            topics=[topic],
        )

        producer = RedisStreamsProducer(logger, config)
        consumer = RedisStreamsConsumer(logger, config)

        events = [factory() for factory in ALL_RECORD_EVENTS]
        received: list[StreamMessage] = []
        all_done = asyncio.Event()

        async def handler(msg: StreamMessage) -> bool:
            received.append(msg)
            if len(received) >= len(events):
                all_done.set()
            return True

        try:
            await producer.initialize()
            await consumer.start(handler)

            for event in events:
                key = event.get("payload", {}).get("recordId") or event.get(
                    "payload", {}
                ).get("connectorId", "")
                await producer.send_message(topic, event, key=key)

            await asyncio.wait_for(all_done.wait(), timeout=15.0)

            assert len(received) == len(events)
            received_types = {m.eventType for m in received}
            expected_types = {e["eventType"] for e in events}
            assert received_types == expected_types
        finally:
            await consumer.stop()
            await producer.cleanup()

    async def test_all_entity_event_types(self, redis_stream_cleanup):
        topic = _unique_stream("e2e-all-entity")
        redis_stream_cleanup.append(topic)
        config = RedisStreamsConfig(
            host=_redis_host(),
            port=_redis_port(),
            password=_redis_password(),
            client_id="e2e-consumer",
            group_id="e2e-group",
            topics=[topic],
        )

        producer = RedisStreamsProducer(logger, config)
        consumer = RedisStreamsConsumer(logger, config)

        events = [factory() for factory in ALL_ENTITY_EVENTS]
        received: list[StreamMessage] = []
        all_done = asyncio.Event()

        async def handler(msg: StreamMessage) -> bool:
            received.append(msg)
            if len(received) >= len(events):
                all_done.set()
            return True

        try:
            await producer.initialize()
            await consumer.start(handler)

            for event in events:
                await producer.send_message(
                    topic, event, key=event["payload"].get("orgId", "")
                )

            await asyncio.wait_for(all_done.wait(), timeout=15.0)

            assert len(received) == len(events)
            received_types = {m.eventType for m in received}
            expected_types = {e["eventType"] for e in events}
            assert received_types == expected_types
        finally:
            await consumer.stop()
            await producer.cleanup()

    async def test_all_sync_event_types(self, redis_stream_cleanup):
        topic = _unique_stream("e2e-all-sync")
        redis_stream_cleanup.append(topic)
        config = RedisStreamsConfig(
            host=_redis_host(),
            port=_redis_port(),
            password=_redis_password(),
            client_id="e2e-consumer",
            group_id="e2e-group",
            topics=[topic],
        )

        producer = RedisStreamsProducer(logger, config)
        consumer = RedisStreamsConsumer(logger, config)

        events = [factory() for factory in ALL_SYNC_EVENTS]
        received: list[StreamMessage] = []
        all_done = asyncio.Event()

        async def handler(msg: StreamMessage) -> bool:
            received.append(msg)
            if len(received) >= len(events):
                all_done.set()
            return True

        try:
            await producer.initialize()
            await consumer.start(handler)

            for event in events:
                await producer.send_message(topic, event, key=event["eventType"])

            await asyncio.wait_for(all_done.wait(), timeout=15.0)

            assert len(received) == len(events)
            received_types = {m.eventType for m in received}
            expected_types = {e["eventType"] for e in events}
            assert received_types == expected_types
        finally:
            await consumer.stop()
            await producer.cleanup()

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_stress_no_drops(self, redis_stream_cleanup):
        """Producer dumps STRESS_COUNT events, consumer must receive every one."""
        topic = _unique_stream("e2e-stress")
        redis_stream_cleanup.append(topic)
        config = RedisStreamsConfig(
            host=_redis_host(),
            port=_redis_port(),
            password=_redis_password(),
            client_id="e2e-stress-consumer",
            group_id="e2e-stress-group",
            topics=[topic],
        )

        producer = RedisStreamsProducer(logger, config)
        consumer = RedisStreamsConsumer(logger, config)

        events = [new_record_event() for _ in range(STRESS_COUNT)]
        sent_ids = {e["payload"]["recordId"] for e in events}
        received: list[StreamMessage] = []
        all_done = asyncio.Event()

        async def handler(msg: StreamMessage) -> bool:
            received.append(msg)
            if len(received) >= STRESS_COUNT:
                all_done.set()
            return True

        try:
            await producer.initialize()
            await consumer.start(handler)

            for event in events:
                await producer.send_message(
                    topic, event, key=event["payload"]["recordId"]
                )

            await asyncio.wait_for(all_done.wait(), timeout=60.0)

            assert len(received) == STRESS_COUNT, (
                f"Dropped {STRESS_COUNT - len(received)} of {STRESS_COUNT} events"
            )
            received_ids = {m.payload["recordId"] for m in received}
            assert received_ids == sent_ids
        finally:
            await consumer.stop()
            await producer.cleanup()


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
class TestKafkaProducerConsumerE2E:
    """Use the actual KafkaMessagingProducer and KafkaMessagingConsumer classes
    to verify produce → consume delivers every event."""

    async def test_single_record_event(self):
        topic = f"e2e-kafka-record-{uuid.uuid4().hex[:8]}"
        brokers = _kafka_brokers().split(",")

        producer_config = KafkaProducerConfig(
            bootstrap_servers=brokers,
            client_id="e2e-kafka-producer",
        )
        consumer_config = KafkaConsumerConfig(
            bootstrap_servers=brokers,
            client_id="e2e-kafka-consumer",
            group_id=f"e2e-kafka-group-{uuid.uuid4().hex[:8]}",
            auto_offset_reset="earliest",
            enable_auto_commit=True,
            topics=[topic],
        )

        producer = KafkaMessagingProducer(logger, producer_config)
        consumer = KafkaMessagingConsumer(logger, consumer_config)

        received: list[StreamMessage] = []
        done = asyncio.Event()

        async def handler(msg: StreamMessage) -> bool:
            received.append(msg)
            done.set()
            return True

        try:
            await producer.initialize()
            await consumer.start(handler)

            event = new_record_event()
            await producer.send_message(topic, event, key=event["payload"]["recordId"])

            await asyncio.wait_for(done.wait(), timeout=15.0)

            assert len(received) == 1
            assert received[0].eventType == "newRecord"
            assert received[0].payload["recordId"] == event["payload"]["recordId"]
        finally:
            await consumer.stop()
            await producer.cleanup()

    async def test_all_event_types_across_topics(self):
        """Produce every event type to its respective topic, consume from each."""
        suffix = uuid.uuid4().hex[:8]
        record_topic = f"e2e-kafka-records-{suffix}"
        entity_topic = f"e2e-kafka-entity-{suffix}"
        ai_config_topic = f"e2e-kafka-aiconfig-{suffix}"
        sync_topic = f"e2e-kafka-sync-{suffix}"
        brokers = _kafka_brokers().split(",")

        producer_config = KafkaProducerConfig(
            bootstrap_servers=brokers,
            client_id="e2e-kafka-producer-multi",
        )

        producer = KafkaMessagingProducer(logger, producer_config)

        # Build events per topic
        record_events = [factory() for factory in ALL_RECORD_EVENTS]
        entity_events = [factory() for factory in ALL_ENTITY_EVENTS]
        ai_config_events = [factory() for factory in ALL_AI_CONFIG_EVENTS]
        sync_events = [factory() for factory in ALL_SYNC_EVENTS]

        # One consumer per topic
        consumers = []
        received_per_topic: dict[str, list[StreamMessage]] = {
            record_topic: [],
            entity_topic: [],
            ai_config_topic: [],
            sync_topic: [],
        }
        done_events: dict[str, asyncio.Event] = {
            record_topic: asyncio.Event(),
            entity_topic: asyncio.Event(),
            ai_config_topic: asyncio.Event(),
            sync_topic: asyncio.Event(),
        }
        expected_counts = {
            record_topic: len(record_events),
            entity_topic: len(entity_events),
            ai_config_topic: len(ai_config_events),
            sync_topic: len(sync_events),
        }

        def make_handler(t: str):
            async def handler(msg: StreamMessage) -> bool:
                received_per_topic[t].append(msg)
                if len(received_per_topic[t]) >= expected_counts[t]:
                    done_events[t].set()
                return True

            return handler

        try:
            await producer.initialize()

            for t, events_list in [
                (record_topic, record_events),
                (entity_topic, entity_events),
                (ai_config_topic, ai_config_events),
                (sync_topic, sync_events),
            ]:
                cfg = KafkaConsumerConfig(
                    bootstrap_servers=brokers,
                    client_id=f"e2e-consumer-{t}",
                    group_id=f"e2e-group-{t}-{uuid.uuid4().hex[:8]}",
                    auto_offset_reset="earliest",
                    enable_auto_commit=True,
                    topics=[t],
                )
                c = KafkaMessagingConsumer(logger, cfg)
                await c.start(make_handler(t))
                consumers.append(c)

            # Produce all
            for event in record_events:
                key = event.get("payload", {}).get("recordId") or event.get(
                    "payload", {}
                ).get("connectorId", "")
                await producer.send_message(record_topic, event, key=key)
            for event in entity_events:
                await producer.send_message(
                    entity_topic, event, key=event["payload"].get("orgId", "")
                )
            for event in ai_config_events:
                await producer.send_message(
                    ai_config_topic, event, key=event["payload"].get("orgId", "")
                )
            for event in sync_events:
                await producer.send_message(sync_topic, event, key=event["eventType"])

            # Wait for all
            await asyncio.wait_for(
                asyncio.gather(*(e.wait() for e in done_events.values())),
                timeout=30.0,
            )

            assert len(received_per_topic[record_topic]) == len(record_events)
            assert len(received_per_topic[entity_topic]) == len(entity_events)
            assert len(received_per_topic[ai_config_topic]) == len(ai_config_events)
            assert len(received_per_topic[sync_topic]) == len(sync_events)

            # Verify event types match
            for t, events_list in [
                (record_topic, record_events),
                (entity_topic, entity_events),
                (ai_config_topic, ai_config_events),
                (sync_topic, sync_events),
            ]:
                received_types = {m.eventType for m in received_per_topic[t]}
                expected_types = {e["eventType"] for e in events_list}
                assert received_types == expected_types, f"Event type mismatch on {t}"
        finally:
            for c in consumers:
                await c.stop()
            await producer.cleanup()

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_stress_no_drops(self):
        """Producer dumps STRESS_COUNT events, consumer must receive every one."""
        topic = f"e2e-kafka-stress-{uuid.uuid4().hex[:8]}"
        brokers = _kafka_brokers().split(",")

        producer_config = KafkaProducerConfig(
            bootstrap_servers=brokers,
            client_id="e2e-kafka-stress-producer",
        )
        consumer_config = KafkaConsumerConfig(
            bootstrap_servers=brokers,
            client_id="e2e-kafka-stress-consumer",
            group_id=f"e2e-kafka-stress-{uuid.uuid4().hex[:8]}",
            auto_offset_reset="earliest",
            enable_auto_commit=True,
            topics=[topic],
        )

        producer = KafkaMessagingProducer(logger, producer_config)
        consumer = KafkaMessagingConsumer(logger, consumer_config)

        events = [new_record_event() for _ in range(STRESS_COUNT)]
        sent_ids = {e["payload"]["recordId"] for e in events}
        received: list[StreamMessage] = []
        all_done = asyncio.Event()

        async def handler(msg: StreamMessage) -> bool:
            received.append(msg)
            if len(received) >= STRESS_COUNT:
                all_done.set()
            return True

        try:
            await producer.initialize()
            await consumer.start(handler)

            for event in events:
                await producer.send_message(
                    topic, event, key=event["payload"]["recordId"]
                )

            await asyncio.wait_for(all_done.wait(), timeout=60.0)

            assert len(received) == STRESS_COUNT, (
                f"Dropped {STRESS_COUNT - len(received)} of {STRESS_COUNT} events"
            )
            received_ids = {m.payload["recordId"] for m in received}
            assert received_ids == sent_ids
        finally:
            await consumer.stop()
            await producer.cleanup()


# =========================================================================
# NEGATIVE TESTS — Redis
# =========================================================================


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
class TestRedisNegative:
    """Redis Streams: edge cases and failure paths."""

    async def test_consumer_receives_nothing_on_empty_stream(
        self, redis_client, redis_stream_cleanup
    ):
        """Consumer on an empty stream returns no messages."""
        stream = _unique_stream("neg-empty")
        redis_stream_cleanup.append(stream)
        received = await consume_redis_messages(
            redis_client,
            stream,
            "grp",
            "c1",
            expected=1,
            timeout=3.0,
        )
        assert len(received) == 0

    async def test_consumer_ignores_non_value_fields(
        self, redis_client, redis_stream_cleanup
    ):
        """Messages without a 'value' field are skipped."""
        stream = _unique_stream("neg-no-value")
        redis_stream_cleanup.append(stream)
        await redis_client.xadd(stream, {"some_other_field": "data"})
        received = await consume_redis_messages(
            redis_client,
            stream,
            "grp",
            "c1",
            expected=1,
            timeout=3.0,
        )
        assert len(received) == 0

    async def test_consumer_skips_malformed_json(self, redis_stream_cleanup):
        """Invalid JSON doesn't crash the RedisStreamsConsumer; valid messages still arrive."""
        stream = _unique_stream("neg-bad-json")
        redis_stream_cleanup.append(stream)
        config = RedisStreamsConfig(
            host=_redis_host(),
            port=_redis_port(),
            password=_redis_password(),
            client_id="neg-consumer",
            group_id="neg-group",
            topics=[stream],
        )

        pre = Redis(
            host=_redis_host(),
            port=_redis_port(),
            password=_redis_password(),
            decode_responses=True,
        )
        await pre.xadd(stream, {"value": "not-valid-json{{"})
        valid = new_record_event()
        await pre.xadd(stream, {"value": json.dumps(valid)})
        await pre.aclose()

        consumer = RedisStreamsConsumer(logger, config)
        received: list[StreamMessage] = []
        done = asyncio.Event()

        async def handler(msg: StreamMessage) -> bool:
            received.append(msg)
            done.set()
            return True

        try:
            await consumer.start(handler)
            await asyncio.wait_for(done.wait(), timeout=10.0)
            assert len(received) == 1
            assert received[0].eventType == "newRecord"
        finally:
            await consumer.stop()

    async def test_handler_returning_false_leaves_message_pending(
        self, redis_client, redis_stream_cleanup
    ):
        """When handler returns False the message stays in PEL."""
        stream = _unique_stream("neg-nack")
        redis_stream_cleanup.append(stream)
        group, cname = "neg-nack-group", "neg-nack-consumer"

        await redis_client.xadd(stream, {"value": json.dumps(new_record_event())})
        try:
            await redis_client.xgroup_create(stream, group, id="0", mkstream=True)
        except Exception:
            pass

        await redis_client.xreadgroup(
            groupname=group,
            consumername=cname,
            streams={stream: ">"},
            count=1,
            block=2000,
        )
        pending = await redis_client.xpending(stream, group)
        assert pending["pending"] >= 1

    async def test_handler_exception_does_not_crash_consumer(
        self, redis_stream_cleanup
    ):
        """Handler raising an exception shouldn't kill the consumer loop."""
        stream = _unique_stream("neg-exc")
        redis_stream_cleanup.append(stream)
        config = RedisStreamsConfig(
            host=_redis_host(),
            port=_redis_port(),
            password=_redis_password(),
            client_id="neg-exc-c",
            group_id="neg-exc-g",
            topics=[stream],
        )

        producer = RedisStreamsProducer(logger, config)
        consumer = RedisStreamsConsumer(logger, config)
        call_count = 0
        second_done = asyncio.Event()

        async def handler(msg: StreamMessage) -> bool:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError("boom")
            second_done.set()
            return True

        try:
            await producer.initialize()
            await consumer.start(handler)
            await producer.send_message(stream, new_record_event())
            await producer.send_message(stream, new_record_event())
            await asyncio.wait_for(second_done.wait(), timeout=10.0)
            assert call_count >= 2
        finally:
            await consumer.stop()
            await producer.cleanup()

    async def test_producer_auto_creates_stream(self, redis_stream_cleanup):
        """XADD to a non-existent stream creates it."""
        stream = _unique_stream("neg-autocreate")
        redis_stream_cleanup.append(stream)
        config = RedisStreamsConfig(
            host=_redis_host(),
            port=_redis_port(),
            password=_redis_password(),
        )
        producer = RedisStreamsProducer(logger, config)
        try:
            await producer.initialize()
            assert await producer.send_message(stream, new_record_event()) is True
        finally:
            await producer.cleanup()

    async def test_consumer_stops_gracefully_mid_processing(self, redis_stream_cleanup):
        """Calling stop() inside the handler exits cleanly."""
        stream = _unique_stream("neg-stop-mid")
        redis_stream_cleanup.append(stream)
        config = RedisStreamsConfig(
            host=_redis_host(),
            port=_redis_port(),
            password=_redis_password(),
            client_id="neg-mid-c",
            group_id="neg-mid-g",
            topics=[stream],
        )
        producer = RedisStreamsProducer(logger, config)
        consumer = RedisStreamsConsumer(logger, config)
        received: list[StreamMessage] = []

        async def handler(msg: StreamMessage) -> bool:
            received.append(msg)
            if len(received) >= 3:
                await consumer.stop()
            return True

        try:
            await producer.initialize()
            for _ in range(10):
                await producer.send_message(stream, new_record_event())
            await consumer.start(handler)
            await asyncio.sleep(5.0)
            assert len(received) >= 3
            assert not consumer.is_running()
        finally:
            if consumer.is_running():
                await consumer.stop()
            await producer.cleanup()

    async def test_duplicate_consumer_group_is_idempotent(self, redis_stream_cleanup):
        """Creating the same consumer group twice doesn't raise."""
        stream = _unique_stream("neg-dup-group")
        redis_stream_cleanup.append(stream)
        config = RedisStreamsConfig(
            host=_redis_host(),
            port=_redis_port(),
            password=_redis_password(),
            client_id="neg-dup-c",
            group_id="neg-dup-g",
            topics=[stream],
        )
        c1 = RedisStreamsConsumer(logger, config)
        c2 = RedisStreamsConsumer(logger, config)
        try:
            await c1.initialize()
            await c2.initialize()
        finally:
            await c1.cleanup()
            await c2.cleanup()


# =========================================================================
# NEGATIVE TESTS — Kafka
# =========================================================================


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
class TestKafkaNegative:
    """Kafka: edge cases and failure paths."""

    async def test_consumer_receives_nothing_on_empty_topic(
        self, kafka_consumer_factory
    ):
        """Consumer on an empty topic returns no messages."""
        topic = f"neg-empty-{uuid.uuid4().hex[:8]}"
        consumer = await kafka_consumer_factory([topic])
        received = await consume_kafka_messages(consumer, expected=1, timeout=5.0)
        assert len(received) == 0

    async def test_consumer_skips_malformed_json(self):
        """Invalid JSON doesn't crash the KafkaMessagingConsumer."""
        topic = f"neg-bad-json-{uuid.uuid4().hex[:8]}"
        brokers = _kafka_brokers().split(",")
        consumer_config = KafkaConsumerConfig(
            bootstrap_servers=brokers,
            client_id="neg-kafka-c",
            group_id=f"neg-kafka-g-{uuid.uuid4().hex[:8]}",
            auto_offset_reset="earliest",
            enable_auto_commit=True,
            topics=[topic],
        )

        from aiokafka import AIOKafkaProducer as RawProducer

        raw = RawProducer(bootstrap_servers=_kafka_brokers())
        await raw.start()
        await raw.send_and_wait(topic, value=b"not-valid-json{{")
        await raw.send_and_wait(topic, value=json.dumps(new_record_event()).encode())
        await raw.stop()

        consumer = KafkaMessagingConsumer(logger, consumer_config)
        received: list[StreamMessage] = []
        done = asyncio.Event()

        async def handler(msg: StreamMessage) -> bool:
            received.append(msg)
            done.set()
            return True

        try:
            await consumer.start(handler)
            await asyncio.wait_for(done.wait(), timeout=15.0)
            assert len(received) == 1
            assert received[0].eventType == "newRecord"
        finally:
            await consumer.stop()

    async def test_handler_returning_false_does_not_crash(self):
        """Handler returning False doesn't crash the consumer loop."""
        topic = f"neg-nack-{uuid.uuid4().hex[:8]}"
        brokers = _kafka_brokers().split(",")
        prod_cfg = KafkaProducerConfig(
            bootstrap_servers=brokers, client_id="neg-nack-p"
        )
        cons_cfg = KafkaConsumerConfig(
            bootstrap_servers=brokers,
            client_id="neg-nack-c",
            group_id=f"neg-nack-g-{uuid.uuid4().hex[:8]}",
            auto_offset_reset="earliest",
            enable_auto_commit=True,
            topics=[topic],
        )
        producer = KafkaMessagingProducer(logger, prod_cfg)
        consumer = KafkaMessagingConsumer(logger, cons_cfg)
        call_count = 0
        second_done = asyncio.Event()

        async def handler(msg: StreamMessage) -> bool:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return False
            second_done.set()
            return True

        try:
            await producer.initialize()
            await consumer.start(handler)
            await producer.send_message(topic, new_record_event(), key="k1")
            await producer.send_message(topic, new_record_event(), key="k2")
            await asyncio.wait_for(second_done.wait(), timeout=15.0)
            assert call_count >= 2
            assert consumer.is_running()
        finally:
            await consumer.stop()
            await producer.cleanup()

    async def test_handler_exception_does_not_crash_consumer(self):
        """Handler raising an exception doesn't bring down the consumer loop."""
        topic = f"neg-exc-{uuid.uuid4().hex[:8]}"
        brokers = _kafka_brokers().split(",")
        prod_cfg = KafkaProducerConfig(bootstrap_servers=brokers, client_id="neg-exc-p")
        cons_cfg = KafkaConsumerConfig(
            bootstrap_servers=brokers,
            client_id="neg-exc-c",
            group_id=f"neg-exc-g-{uuid.uuid4().hex[:8]}",
            auto_offset_reset="earliest",
            enable_auto_commit=True,
            topics=[topic],
        )
        producer = KafkaMessagingProducer(logger, prod_cfg)
        consumer = KafkaMessagingConsumer(logger, cons_cfg)
        call_count = 0
        second_done = asyncio.Event()

        async def handler(msg: StreamMessage) -> bool:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError("simulated crash")
            second_done.set()
            return True

        try:
            await producer.initialize()
            await consumer.start(handler)
            await producer.send_message(topic, new_record_event(), key="k1")
            await producer.send_message(topic, new_record_event(), key="k2")
            await asyncio.wait_for(second_done.wait(), timeout=15.0)
            assert call_count >= 2
            assert consumer.is_running()
        finally:
            await consumer.stop()
            await producer.cleanup()

    async def test_producer_auto_creates_topic(self):
        """Sending to a non-existent topic auto-creates it."""
        topic = f"neg-autocreate-{uuid.uuid4().hex[:8]}"
        brokers = _kafka_brokers().split(",")
        prod_cfg = KafkaProducerConfig(
            bootstrap_servers=brokers, client_id="neg-auto-p"
        )
        producer = KafkaMessagingProducer(logger, prod_cfg)
        try:
            await producer.initialize()
            assert (
                await producer.send_message(topic, new_record_event(), key="k1") is True
            )
        finally:
            await producer.cleanup()

    async def test_consumer_stops_gracefully_mid_processing(self):
        """Calling stop() inside the handler exits cleanly."""
        topic = f"neg-stop-mid-{uuid.uuid4().hex[:8]}"
        brokers = _kafka_brokers().split(",")
        prod_cfg = KafkaProducerConfig(bootstrap_servers=brokers, client_id="neg-mid-p")
        cons_cfg = KafkaConsumerConfig(
            bootstrap_servers=brokers,
            client_id="neg-mid-c",
            group_id=f"neg-mid-g-{uuid.uuid4().hex[:8]}",
            auto_offset_reset="earliest",
            enable_auto_commit=True,
            topics=[topic],
        )
        producer = KafkaMessagingProducer(logger, prod_cfg)
        consumer = KafkaMessagingConsumer(logger, cons_cfg)
        received: list[StreamMessage] = []

        async def handler(msg: StreamMessage) -> bool:
            received.append(msg)
            if len(received) >= 3:
                await consumer.stop()
            return True

        try:
            await producer.initialize()
            for _ in range(10):
                await producer.send_message(topic, new_record_event())
            await consumer.start(handler)
            await asyncio.sleep(10.0)
            assert len(received) >= 3
            assert not consumer.is_running()
        finally:
            if consumer.is_running():
                await consumer.stop()
            await producer.cleanup()


# =========================================================================
# OVERLOAD & FAILURE-RECOVERY TESTS
# =========================================================================

OVERLOAD_COUNT = 200


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.asyncio(loop_scope="session")
class TestRedisOverloadAndRecovery:
    """Redis Streams: behaviour under slow handlers, overload, and crash recovery."""

    async def test_slow_handler_does_not_drop_messages(self, redis_stream_cleanup):
        """When the handler is slower than the producer, no messages are lost."""
        stream = _unique_stream("overload-slow")
        redis_stream_cleanup.append(stream)
        config = RedisStreamsConfig(
            host=_redis_host(),
            port=_redis_port(),
            password=_redis_password(),
            client_id="overload-slow-c",
            group_id="overload-slow-g",
            topics=[stream],
        )

        producer = RedisStreamsProducer(logger, config)
        consumer = RedisStreamsConsumer(logger, config)

        events = [new_record_event() for _ in range(OVERLOAD_COUNT)]
        sent_ids = {e["payload"]["recordId"] for e in events}
        received: list[StreamMessage] = []
        all_done = asyncio.Event()

        async def slow_handler(msg: StreamMessage) -> bool:
            await asyncio.sleep(0.01)  # 10ms per message
            received.append(msg)
            if len(received) >= OVERLOAD_COUNT:
                all_done.set()
            return True

        try:
            await producer.initialize()
            await consumer.start(slow_handler)

            for event in events:
                await producer.send_message(
                    stream, event, key=event["payload"]["recordId"]
                )

            await asyncio.wait_for(all_done.wait(), timeout=60.0)

            assert len(received) == OVERLOAD_COUNT, (
                f"Lost {OVERLOAD_COUNT - len(received)} messages under slow handler"
            )
            received_ids = {m.payload["recordId"] for m in received}
            assert received_ids == sent_ids
        finally:
            await consumer.stop()
            await producer.cleanup()

    async def test_intermittent_handler_failure_and_pel_recovery(
        self, redis_stream_cleanup
    ):
        """Messages that fail processing stay in PEL and are recovered on consumer restart."""
        stream = _unique_stream("overload-fail-recover")
        redis_stream_cleanup.append(stream)

        base_config = dict(
            host=_redis_host(),
            port=_redis_port(),
            password=_redis_password(),
            client_id="fail-recover-c",
            group_id="fail-recover-g",
            topics=[stream],
            claim_min_idle_ms=1000,
        )

        producer = RedisStreamsProducer(logger, RedisStreamsConfig(**base_config))

        # Phase 1: send 10 messages, consumer rejects every odd one (returns False)
        events = [new_record_event() for _ in range(10)]
        sent_ids = {e["payload"]["recordId"] for e in events}

        first_pass_ok: list[StreamMessage] = []
        first_pass_done = asyncio.Event()
        call_idx = 0

        async def failing_handler(msg: StreamMessage) -> bool:
            nonlocal call_idx
            call_idx += 1
            if call_idx % 2 == 0:
                first_pass_ok.append(msg)
                if len(first_pass_ok) >= 5:
                    first_pass_done.set()
                return True
            return False  # stays in PEL

        consumer1 = RedisStreamsConsumer(logger, RedisStreamsConfig(**base_config))

        try:
            await producer.initialize()
            await consumer1.start(failing_handler)

            for event in events:
                await producer.send_message(
                    stream, event, key=event["payload"]["recordId"]
                )

            await asyncio.wait_for(first_pass_done.wait(), timeout=15.0)
        finally:
            await consumer1.stop()

        assert len(first_pass_ok) == 5

        # Wait for messages to become idle (claim_min_idle_ms = 1000ms)
        await asyncio.sleep(1.5)

        # Phase 2: new consumer with same group — _drain_pending picks up the 5 rejected messages
        second_pass: list[StreamMessage] = []
        second_done = asyncio.Event()

        async def accept_all(msg: StreamMessage) -> bool:
            second_pass.append(msg)
            if len(second_pass) >= 5:
                second_done.set()
            return True

        # Use a different client_id so xgroup_delconsumer doesn't wipe consumer1's PEL
        recovery_config = {**base_config, "client_id": "fail-recover-c2"}
        consumer2 = RedisStreamsConsumer(logger, RedisStreamsConfig(**recovery_config))
        try:
            await consumer2.start(accept_all)
            await asyncio.wait_for(second_done.wait(), timeout=15.0)
        finally:
            await consumer2.stop()
            await producer.cleanup()

        all_received_ids = {m.payload["recordId"] for m in first_pass_ok + second_pass}
        assert all_received_ids == sent_ids, (
            f"Missing {sent_ids - all_received_ids} after PEL recovery"
        )

    async def test_consumer_crash_and_restart_recovers_pending(
        self, redis_stream_cleanup
    ):
        """Simulates a consumer crash (exception = no ack) and verifies PEL drain on restart."""
        stream = _unique_stream("overload-crash")
        redis_stream_cleanup.append(stream)

        base_config = dict(
            host=_redis_host(),
            port=_redis_port(),
            password=_redis_password(),
            client_id="crash-c",
            group_id="crash-g",
            topics=[stream],
            claim_min_idle_ms=1000,
        )

        producer = RedisStreamsProducer(logger, RedisStreamsConfig(**base_config))
        events = [new_record_event() for _ in range(5)]
        sent_ids = {e["payload"]["recordId"] for e in events}

        # Phase 1: handler always crashes — messages delivered but never acked
        async def crash_handler(msg: StreamMessage) -> bool:
            raise RuntimeError("simulated crash")

        consumer1 = RedisStreamsConsumer(logger, RedisStreamsConfig(**base_config))
        try:
            await producer.initialize()
            await consumer1.start(crash_handler)

            for event in events:
                await producer.send_message(
                    stream, event, key=event["payload"]["recordId"]
                )

            await asyncio.sleep(5.0)  # let consumer attempt all messages
        finally:
            await consumer1.stop()

        # Phase 2: new consumer — _drain_pending should recover un-acked messages
        recovered: list[StreamMessage] = []
        recovered_done = asyncio.Event()

        async def recovery_handler(msg: StreamMessage) -> bool:
            recovered.append(msg)
            if len(recovered) >= 5:
                recovered_done.set()
            return True

        # Use a different client_id so xgroup_delconsumer doesn't wipe consumer1's PEL
        recovery_config = {**base_config, "client_id": "crash-c2"}
        consumer2 = RedisStreamsConsumer(logger, RedisStreamsConfig(**recovery_config))
        try:
            await consumer2.start(recovery_handler)
            await asyncio.wait_for(recovered_done.wait(), timeout=15.0)
        finally:
            await consumer2.stop()
            await producer.cleanup()

        recovered_ids = {m.payload["recordId"] for m in recovered}
        assert recovered_ids == sent_ids, (
            f"PEL recovery missed {sent_ids - recovered_ids}"
        )

    async def test_burst_then_trickle(self, redis_stream_cleanup):
        """Burst of messages followed by slow trickle — consumer handles both patterns."""
        stream = _unique_stream("overload-burst")
        redis_stream_cleanup.append(stream)
        config = RedisStreamsConfig(
            host=_redis_host(),
            port=_redis_port(),
            password=_redis_password(),
            client_id="burst-c",
            group_id="burst-g",
            topics=[stream],
        )

        producer = RedisStreamsProducer(logger, config)
        consumer = RedisStreamsConsumer(logger, config)

        total = 50
        burst = 40
        events = [new_record_event() for _ in range(total)]
        sent_ids = {e["payload"]["recordId"] for e in events}
        received: list[StreamMessage] = []
        all_done = asyncio.Event()

        async def handler(msg: StreamMessage) -> bool:
            received.append(msg)
            if len(received) >= total:
                all_done.set()
            return True

        try:
            await producer.initialize()
            await consumer.start(handler)

            # Burst
            for event in events[:burst]:
                await producer.send_message(
                    stream, event, key=event["payload"]["recordId"]
                )

            # Trickle
            for event in events[burst:]:
                await asyncio.sleep(0.1)
                await producer.send_message(
                    stream, event, key=event["payload"]["recordId"]
                )

            await asyncio.wait_for(all_done.wait(), timeout=30.0)

            assert len(received) == total
            received_ids = {m.payload["recordId"] for m in received}
            assert received_ids == sent_ids
        finally:
            await consumer.stop()
            await producer.cleanup()


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.asyncio(loop_scope="session")
class TestKafkaOverloadAndRecovery:
    """Kafka: behaviour under slow handlers and overload."""

    async def test_slow_handler_does_not_drop_messages(self):
        """When the handler is slower than the producer, no messages are lost."""
        topic = f"overload-slow-{uuid.uuid4().hex[:8]}"
        brokers = _kafka_brokers().split(",")

        prod_cfg = KafkaProducerConfig(
            bootstrap_servers=brokers, client_id="overload-slow-p"
        )
        cons_cfg = KafkaConsumerConfig(
            bootstrap_servers=brokers,
            client_id="overload-slow-c",
            group_id=f"overload-slow-g-{uuid.uuid4().hex[:8]}",
            auto_offset_reset="earliest",
            enable_auto_commit=True,
            topics=[topic],
        )

        producer = KafkaMessagingProducer(logger, prod_cfg)
        consumer = KafkaMessagingConsumer(logger, cons_cfg)

        events = [new_record_event() for _ in range(OVERLOAD_COUNT)]
        sent_ids = {e["payload"]["recordId"] for e in events}
        received: list[StreamMessage] = []
        all_done = asyncio.Event()

        async def slow_handler(msg: StreamMessage) -> bool:
            await asyncio.sleep(0.01)
            received.append(msg)
            if len(received) >= OVERLOAD_COUNT:
                all_done.set()
            return True

        try:
            await producer.initialize()
            await consumer.start(slow_handler)

            for event in events:
                await producer.send_message(
                    topic, event, key=event["payload"]["recordId"]
                )

            await asyncio.wait_for(all_done.wait(), timeout=60.0)

            assert len(received) == OVERLOAD_COUNT, (
                f"Lost {OVERLOAD_COUNT - len(received)} messages under slow handler"
            )
            received_ids = {m.payload["recordId"] for m in received}
            assert received_ids == sent_ids
        finally:
            await consumer.stop()
            await producer.cleanup()

    async def test_handler_failure_continues_processing(self):
        """Failed messages don't block subsequent messages from being processed."""
        topic = f"overload-fail-{uuid.uuid4().hex[:8]}"
        brokers = _kafka_brokers().split(",")

        prod_cfg = KafkaProducerConfig(
            bootstrap_servers=brokers, client_id="fail-cont-p"
        )
        cons_cfg = KafkaConsumerConfig(
            bootstrap_servers=brokers,
            client_id="fail-cont-c",
            group_id=f"fail-cont-g-{uuid.uuid4().hex[:8]}",
            auto_offset_reset="earliest",
            enable_auto_commit=True,
            topics=[topic],
        )

        producer = KafkaMessagingProducer(logger, prod_cfg)
        consumer = KafkaMessagingConsumer(logger, cons_cfg)

        total = 20
        events = [new_record_event() for _ in range(total)]
        fail_indices = {0, 3, 7, 15}
        received_ok: list[StreamMessage] = []
        all_done = asyncio.Event()
        call_idx = 0

        async def selective_fail_handler(msg: StreamMessage) -> bool:
            nonlocal call_idx
            idx = call_idx
            call_idx += 1
            if idx in fail_indices:
                raise ValueError(f"simulated failure at index {idx}")
            received_ok.append(msg)
            if call_idx >= total:
                all_done.set()
            return True

        try:
            await producer.initialize()
            await consumer.start(selective_fail_handler)

            for event in events:
                await producer.send_message(
                    topic, event, key=event["payload"]["recordId"]
                )

            await asyncio.wait_for(all_done.wait(), timeout=30.0)

            assert call_idx == total
            assert len(received_ok) == total - len(fail_indices)
            assert consumer.is_running()
        finally:
            await consumer.stop()
            await producer.cleanup()

    async def test_burst_then_trickle(self):
        """Burst followed by slow trickle — consumer handles both."""
        topic = f"overload-burst-{uuid.uuid4().hex[:8]}"
        brokers = _kafka_brokers().split(",")

        prod_cfg = KafkaProducerConfig(bootstrap_servers=brokers, client_id="burst-p")
        cons_cfg = KafkaConsumerConfig(
            bootstrap_servers=brokers,
            client_id="burst-c",
            group_id=f"burst-g-{uuid.uuid4().hex[:8]}",
            auto_offset_reset="earliest",
            enable_auto_commit=True,
            topics=[topic],
        )

        producer = KafkaMessagingProducer(logger, prod_cfg)
        consumer = KafkaMessagingConsumer(logger, cons_cfg)

        total = 50
        burst = 40
        events = [new_record_event() for _ in range(total)]
        sent_ids = {e["payload"]["recordId"] for e in events}
        received: list[StreamMessage] = []
        all_done = asyncio.Event()

        async def handler(msg: StreamMessage) -> bool:
            received.append(msg)
            if len(received) >= total:
                all_done.set()
            return True

        try:
            await producer.initialize()
            await consumer.start(handler)

            for event in events[:burst]:
                await producer.send_message(
                    topic, event, key=event["payload"]["recordId"]
                )

            for event in events[burst:]:
                await asyncio.sleep(0.1)
                await producer.send_message(
                    topic, event, key=event["payload"]["recordId"]
                )

            await asyncio.wait_for(all_done.wait(), timeout=30.0)

            assert len(received) == total
            received_ids = {m.payload["recordId"] for m in received}
            assert received_ids == sent_ids
        finally:
            await consumer.stop()
            await producer.cleanup()
