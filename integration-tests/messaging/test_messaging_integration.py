"""Integration tests for messaging systems (Kafka and Redis Streams).

Generic tests verify end-to-end produce → consume with real PipesHub event payloads,
ensuring no events are dropped under normal and stress conditions. Tests run against
the broker specified in MESSAGE_BROKER env var.

Run:
    cd integration-tests
    MESSAGE_BROKER=redis pytest messaging/test_messaging_integration.py -v --timeout=120
    MESSAGE_BROKER=kafka pytest messaging/test_messaging_integration.py -v --timeout=120
"""

import asyncio
import uuid

import pytest

from app.services.messaging.config import StreamMessage
from messaging.payloads import (
    ALL_ENTITY_EVENTS,
    ALL_RECORD_EVENTS,
    ALL_SYNC_EVENTS,
    new_record_event,
)

STRESS_COUNT = 500
OVERLOAD_COUNT = 200
BURST_COUNT = 40
TRICKLE_COUNT = 10


def _unique_topic(base: str) -> str:
    """Generate unique topic/stream name for tests."""
    return f"{base}-{uuid.uuid4().hex[:8]}"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
class TestMessagingProducerConsumerE2E:
    """Generic E2E tests using IMessagingProducer/IMessagingConsumer interfaces.

    Tests run against the broker specified in MESSAGE_BROKER env var.
    """

    async def test_single_record_event(
        self, messaging_producer, messaging_consumer_factory, messaging_cleanup
    ):
        """Single event produce → consume."""
        topic = _unique_topic("e2e-record")
        messaging_cleanup.append(topic)

        consumer = messaging_consumer_factory([topic])
        received: list[StreamMessage] = []
        done = asyncio.Event()

        async def handler(msg: StreamMessage) -> bool:
            received.append(msg)
            done.set()
            return True

        try:
            await consumer.start(handler)

            event = new_record_event()
            await messaging_producer.send_message(
                topic, event, key=event["payload"]["recordId"]
            )

            await asyncio.wait_for(done.wait(), timeout=10.0)

            assert len(received) == 1
            assert received[0].eventType == "newRecord"
            assert received[0].payload["recordId"] == event["payload"]["recordId"]
            assert received[0].payload["recordType"] == "FILE"
        finally:
            await consumer.stop()

    async def test_all_record_event_types(
        self, messaging_producer, messaging_consumer_factory, messaging_cleanup
    ):
        """All record event types delivered correctly."""
        topic = _unique_topic("e2e-all-records")
        messaging_cleanup.append(topic)

        consumer = messaging_consumer_factory([topic])
        events = [factory() for factory in ALL_RECORD_EVENTS]
        received: list[StreamMessage] = []
        all_done = asyncio.Event()

        async def handler(msg: StreamMessage) -> bool:
            received.append(msg)
            if len(received) >= len(events):
                all_done.set()
            return True

        try:
            await consumer.start(handler)

            for event in events:
                key = event.get("payload", {}).get("recordId") or event.get(
                    "payload", {}
                ).get("connectorId", "")
                await messaging_producer.send_message(topic, event, key=key)

            await asyncio.wait_for(all_done.wait(), timeout=15.0)

            assert len(received) == len(events)
            received_types = {m.eventType for m in received}
            expected_types = {e["eventType"] for e in events}
            assert received_types == expected_types
        finally:
            await consumer.stop()

    async def test_all_entity_event_types(
        self, messaging_producer, messaging_consumer_factory, messaging_cleanup
    ):
        """All entity event types delivered correctly."""
        topic = _unique_topic("e2e-all-entity")
        messaging_cleanup.append(topic)

        consumer = messaging_consumer_factory([topic])
        events = [factory() for factory in ALL_ENTITY_EVENTS]
        received: list[StreamMessage] = []
        all_done = asyncio.Event()

        async def handler(msg: StreamMessage) -> bool:
            received.append(msg)
            if len(received) >= len(events):
                all_done.set()
            return True

        try:
            await consumer.start(handler)

            for event in events:
                await messaging_producer.send_message(
                    topic, event, key=event["payload"].get("orgId", "")
                )

            await asyncio.wait_for(all_done.wait(), timeout=15.0)

            assert len(received) == len(events)
            received_types = {m.eventType for m in received}
            expected_types = {e["eventType"] for e in events}
            assert received_types == expected_types
        finally:
            await consumer.stop()

    async def test_all_sync_event_types(
        self, messaging_producer, messaging_consumer_factory, messaging_cleanup
    ):
        """All sync event types delivered correctly."""
        topic = _unique_topic("e2e-all-sync")
        messaging_cleanup.append(topic)

        consumer = messaging_consumer_factory([topic])
        events = [factory() for factory in ALL_SYNC_EVENTS]
        received: list[StreamMessage] = []
        all_done = asyncio.Event()

        async def handler(msg: StreamMessage) -> bool:
            received.append(msg)
            if len(received) >= len(events):
                all_done.set()
            return True

        try:
            await consumer.start(handler)

            for event in events:
                await messaging_producer.send_message(topic, event, key=event["eventType"])

            await asyncio.wait_for(all_done.wait(), timeout=15.0)

            assert len(received) == len(events)
            received_types = {m.eventType for m in received}
            expected_types = {e["eventType"] for e in events}
            assert received_types == expected_types
        finally:
            await consumer.stop()

    @pytest.mark.slow
    async def test_stress_no_drops(
        self, messaging_producer, messaging_consumer_factory, messaging_cleanup
    ):
        """Producer dumps STRESS_COUNT events, consumer must receive every one."""
        topic = _unique_topic("e2e-stress")
        messaging_cleanup.append(topic)

        consumer = messaging_consumer_factory([topic])
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
            await consumer.start(handler)

            for event in events:
                await messaging_producer.send_message(
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


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
class TestMessagingNegative:
    """Edge cases and failure paths for any messaging backend."""

    async def test_handler_returning_false_does_not_crash(
        self, messaging_producer, messaging_consumer_factory, messaging_cleanup
    ):
        """Handler returning False doesn't crash the consumer."""
        topic = _unique_topic("neg-return-false")
        messaging_cleanup.append(topic)

        consumer = messaging_consumer_factory([topic])
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
            await consumer.start(handler)
            await messaging_producer.send_message(topic, new_record_event())
            await messaging_producer.send_message(topic, new_record_event())
            await asyncio.wait_for(second_done.wait(), timeout=10.0)
            assert call_count >= 2
        finally:
            await consumer.stop()

    async def test_handler_exception_does_not_crash_consumer(
        self, messaging_producer, messaging_consumer_factory, messaging_cleanup
    ):
        """Handler raising an exception shouldn't kill the consumer loop."""
        topic = _unique_topic("neg-exc")
        messaging_cleanup.append(topic)

        consumer = messaging_consumer_factory([topic])
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
            await consumer.start(handler)
            await messaging_producer.send_message(topic, new_record_event())
            await messaging_producer.send_message(topic, new_record_event())
            await asyncio.wait_for(second_done.wait(), timeout=10.0)
            assert call_count >= 2
        finally:
            await consumer.stop()

    async def test_producer_auto_creates_topic(
        self, messaging_producer, messaging_cleanup
    ):
        """Producing to a non-existent topic creates it."""
        topic = _unique_topic("neg-autocreate")
        messaging_cleanup.append(topic)
        assert await messaging_producer.send_message(topic, new_record_event()) is True

    async def test_consumer_stops_gracefully_mid_processing(
        self, messaging_producer, messaging_consumer_factory, messaging_cleanup
    ):
        """Calling stop() inside the handler exits cleanly."""
        topic = _unique_topic("neg-stop-mid")
        messaging_cleanup.append(topic)

        consumer = messaging_consumer_factory([topic])
        received: list[StreamMessage] = []

        async def handler(msg: StreamMessage) -> bool:
            received.append(msg)
            if len(received) >= 3:
                await consumer.stop()
            return True

        try:
            for _ in range(10):
                await messaging_producer.send_message(topic, new_record_event())
            await consumer.start(handler)
            await asyncio.sleep(5.0)
            assert len(received) >= 3
            assert not consumer.is_running()
        finally:
            if consumer.is_running():
                await consumer.stop()


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.asyncio(loop_scope="session")
class TestMessagingOverloadAndRecovery:
    """Behavior under slow handlers, overload, and recovery."""

    async def test_slow_handler_does_not_drop_messages(
        self, messaging_producer, messaging_consumer_factory, messaging_cleanup
    ):
        """When the handler is slower than the producer, no messages are lost."""
        topic = _unique_topic("overload-slow")
        messaging_cleanup.append(topic)

        consumer = messaging_consumer_factory([topic])
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
            await consumer.start(slow_handler)

            for event in events:
                await messaging_producer.send_message(
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

    async def test_handler_failure_continues_processing(
        self, messaging_producer, messaging_consumer_factory, messaging_cleanup
    ):
        """Failed messages don't block subsequent processing."""
        topic = _unique_topic("overload-fail")
        messaging_cleanup.append(topic)

        consumer = messaging_consumer_factory([topic])
        call_count = 0
        success_count = 0
        done = asyncio.Event()

        async def handler(msg: StreamMessage) -> bool:
            nonlocal call_count, success_count
            call_count += 1
            if call_count % 3 == 0:
                raise ValueError("Simulated failure")
            success_count += 1
            if success_count >= 10:
                done.set()
            return True

        try:
            await consumer.start(handler)
            for _ in range(15):
                await messaging_producer.send_message(topic, new_record_event())
            await asyncio.wait_for(done.wait(), timeout=15.0)
            assert success_count >= 10
        finally:
            await consumer.stop()

    async def test_burst_then_trickle(
        self, messaging_producer, messaging_consumer_factory, messaging_cleanup
    ):
        """Burst of messages followed by a trickle — all delivered."""
        topic = _unique_topic("overload-burst")
        messaging_cleanup.append(topic)

        consumer = messaging_consumer_factory([topic])
        total = BURST_COUNT + TRICKLE_COUNT
        received: list[StreamMessage] = []
        all_done = asyncio.Event()

        async def handler(msg: StreamMessage) -> bool:
            received.append(msg)
            if len(received) >= total:
                all_done.set()
            return True

        try:
            await consumer.start(handler)

            for _ in range(BURST_COUNT):
                await messaging_producer.send_message(topic, new_record_event())

            for _ in range(TRICKLE_COUNT):
                await asyncio.sleep(0.1)
                await messaging_producer.send_message(topic, new_record_event())

            await asyncio.wait_for(all_done.wait(), timeout=30.0)

            assert len(received) == total
        finally:
            await consumer.stop()
