"""Tests for the resiliency additions to IndexingKafkaConsumer:

- Delayed re-queue (``_retry_not_before`` stamping + backoff schedule)
- Deferred reprocessing of a re-queued message that arrives early
- Dead-letter-queue publishing on exhaustion / terminal errors

See the "Indexing / Parsing / Docling Resiliency Plan" (Phase 2, Layer 3).
"""

import asyncio
import json
import logging
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
    StreamMessage,
    Topic,
    messaging_env,
)
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer.indexing_consumer import (
    REDELIVERY_BACKOFF_SECONDS,
    IndexingKafkaConsumer,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def logger():
    return logging.getLogger("test_indexing_resiliency")


@pytest.fixture
def plain_config():
    return KafkaConsumerConfig(
        topics=["idx-topic"],
        client_id="idx-consumer",
        group_id="idx-group",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        bootstrap_servers=["broker:9092"],
        ssl=False,
        sasl=None,
    )


@pytest.fixture
def mock_producer():
    producer = AsyncMock()
    producer.send_event = AsyncMock(return_value=True)
    return producer


@pytest.fixture
def mock_retry_manager():
    return AsyncMock()


@pytest.fixture
def consumer(logger, plain_config, mock_producer, mock_retry_manager):
    c = IndexingKafkaConsumer(
        logger, plain_config, retry_manager=mock_retry_manager, producer=mock_producer
    )
    # _run_on_main_loop bridges to a separate worker-thread loop in
    # production; in these tests everything runs on one loop so it's a
    # pass-through and doesn't need a real main_loop configured.
    c.main_loop = None
    return c


def _make_message(topic="idx-topic", partition=0, offset=0, value=None):
    msg = MagicMock()
    msg.topic = topic
    msg.partition = partition
    msg.offset = offset
    msg.value = value
    return msg


def _kafka_record(payload: dict, event_type: str = "test", topic: str = "idx-topic", offset: int = 0):
    value = json.dumps({"eventType": event_type, "payload": payload}).encode("utf-8")
    return _make_message(topic=topic, offset=offset, value=value)


# ---------------------------------------------------------------------------
# _compute_redelivery_delay
# ---------------------------------------------------------------------------


class TestComputeRedeliveryDelay:
    def test_first_attempt_uses_first_backoff(self, consumer):
        assert consumer._compute_redelivery_delay(1) == REDELIVERY_BACKOFF_SECONDS[0]

    def test_second_attempt_uses_second_backoff(self, consumer):
        assert consumer._compute_redelivery_delay(2) == REDELIVERY_BACKOFF_SECONDS[1]

    def test_attempts_beyond_list_repeat_last_value(self, consumer):
        assert consumer._compute_redelivery_delay(99) == REDELIVERY_BACKOFF_SECONDS[-1]

    def test_zero_or_negative_clamped_to_first(self, consumer):
        assert consumer._compute_redelivery_delay(0) == REDELIVERY_BACKOFF_SECONDS[0]
        assert consumer._compute_redelivery_delay(-5) == REDELIVERY_BACKOFF_SECONDS[0]


# ---------------------------------------------------------------------------
# _requeue_message: stamps _retry_not_before / _retry_tracking_id
# ---------------------------------------------------------------------------


class TestRequeueMessage:
    @pytest.mark.asyncio
    async def test_stamps_retry_not_before_and_tracking_id(self, consumer, mock_producer):
        message = StreamMessage(eventType="test", payload={"recordId": "r1"})
        before = time.time()

        await consumer._requeue_message(Topic.RECORD_EVENTS.value, message, "stable-id-1", attempt_count=1)

        mock_producer.send_event.assert_awaited_once()
        call = mock_producer.send_event.await_args
        assert call.kwargs["topic"] == Topic.RECORD_EVENTS.value
        payload = call.kwargs["payload"]
        assert payload["_retry_tracking_id"] == "stable-id-1"
        assert payload["recordId"] == "r1"
        # Not-before should reflect the attempt-1 backoff (60s), not be immediate.
        expected_floor = before + REDELIVERY_BACKOFF_SECONDS[0]
        assert payload["_retry_not_before"] >= expected_floor - 1  # small tolerance for test exec time

    @pytest.mark.asyncio
    async def test_second_attempt_uses_longer_backoff(self, consumer, mock_producer):
        message = StreamMessage(eventType="test", payload={"recordId": "r1"})
        before = time.time()

        await consumer._requeue_message(Topic.RECORD_EVENTS.value, message, "stable-id-1", attempt_count=2)

        payload = mock_producer.send_event.await_args.kwargs["payload"]
        assert payload["_retry_not_before"] >= before + REDELIVERY_BACKOFF_SECONDS[1] - 1

    @pytest.mark.asyncio
    async def test_no_producer_logs_and_returns(self, logger, plain_config, mock_retry_manager):
        consumer_no_producer = IndexingKafkaConsumer(
            logger, plain_config, retry_manager=mock_retry_manager, producer=None
        )
        message = StreamMessage(eventType="test", payload={"recordId": "r1"})
        # Should not raise even though there's no producer to publish through.
        await consumer_no_producer._requeue_message(Topic.RECORD_EVENTS.value, message, "id", 1)

    @pytest.mark.asyncio
    async def test_producer_error_propagates(self, consumer, mock_producer):
        mock_producer.send_event = AsyncMock(side_effect=RuntimeError("kafka down"))
        message = StreamMessage(eventType="test", payload={})

        with pytest.raises(RuntimeError):
            await consumer._requeue_message(Topic.RECORD_EVENTS.value, message, "id", 1)


# ---------------------------------------------------------------------------
# __process_message_wrapper: deferred re-processing of early re-queued msgs
# ---------------------------------------------------------------------------


class TestDeferredReprocessing:
    @pytest.mark.asyncio
    async def test_message_before_not_before_is_deferred_without_running_handler(
        self, consumer, mock_producer
    ):
        """A re-queued message that arrives before its _retry_not_before must
        be republished unchanged and committed -- the handler must never run
        and no semaphore slot should be consumed."""
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)
        consumer.consumer = AsyncMock()

        handler_called = False

        async def handler(msg):
            nonlocal handler_called
            handler_called = True
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="r1"))

        consumer.message_handler = handler

        not_before = time.time() + 120  # far in the future
        record = _kafka_record({"recordId": "r1", "_retry_not_before": not_before})

        result = await consumer._IndexingKafkaConsumer__process_message_wrapper(record)

        assert result is False
        assert handler_called is False
        # Semaphores must remain untouched.
        assert consumer.parsing_semaphore._value == 1
        assert consumer.indexing_semaphore._value == 1
        # Republished (unchanged) rather than processed.
        mock_producer.send_event.assert_awaited_once()
        republished_payload = mock_producer.send_event.await_args.kwargs["payload"]
        assert republished_payload["_retry_not_before"] == not_before
        # Offset committed via the consumer despite being deferred.
        consumer.consumer.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_message_past_not_before_is_processed_normally(self, consumer, mock_producer):
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)
        consumer.consumer = AsyncMock()
        consumer.retry_manager.get_count = AsyncMock(return_value=0)

        async def handler(msg):
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="r1"))
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="r1"))

        consumer.message_handler = handler

        not_before = time.time() - 5  # already eligible
        record = _kafka_record({"recordId": "r1", "_retry_not_before": not_before})

        result = await consumer._IndexingKafkaConsumer__process_message_wrapper(record)

        assert result is True
        consumer.retry_manager.clear.assert_awaited()

    @pytest.mark.asyncio
    async def test_missing_not_before_is_processed_normally(self, consumer, mock_producer):
        """Messages without the stamp (first delivery) are unaffected."""
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)
        consumer.consumer = AsyncMock()
        consumer.retry_manager.get_count = AsyncMock(return_value=0)

        async def handler(msg):
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="r1"))
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="r1"))

        consumer.message_handler = handler
        record = _kafka_record({"recordId": "r1"})

        result = await consumer._IndexingKafkaConsumer__process_message_wrapper(record)
        assert result is True

    @pytest.mark.asyncio
    async def test_malformed_not_before_is_processed_normally(self, consumer, mock_producer):
        """A non-numeric _retry_not_before should not crash the pipeline --
        fail open and process the message rather than deferring forever."""
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)
        consumer.consumer = AsyncMock()
        consumer.retry_manager.get_count = AsyncMock(return_value=0)

        async def handler(msg):
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="r1"))
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="r1"))

        consumer.message_handler = handler
        record = _kafka_record({"recordId": "r1", "_retry_not_before": "not-a-number"})

        result = await consumer._IndexingKafkaConsumer__process_message_wrapper(record)
        assert result is True


# ---------------------------------------------------------------------------
# _publish_to_dlq / __commit_if_appropriate: dead-letter behavior
# ---------------------------------------------------------------------------


class TestPublishToDlq:
    @pytest.mark.asyncio
    async def test_publishes_with_failure_metadata_to_dlq_topic(self, consumer, mock_producer):
        parsed = StreamMessage(eventType="test", payload={"recordId": "r1"})
        record = _kafka_record({"recordId": "r1"})

        await consumer._publish_to_dlq(record, parsed, "stable-id-1", "boom")

        mock_producer.send_event.assert_awaited_once()
        call = mock_producer.send_event.await_args
        assert call.kwargs["topic"] == Topic.RECORD_EVENTS_DLQ.value
        payload = call.kwargs["payload"]
        assert payload["_dlq_reason"] == "boom"
        assert payload["_dlq_original_topic"] == record.topic
        assert payload["_retry_tracking_id"] == "stable-id-1"
        assert "_dlq_timestamp" in payload

    @pytest.mark.asyncio
    async def test_strips_retry_not_before_so_replay_starts_fresh(self, consumer, mock_producer):
        parsed = StreamMessage(
            eventType="test", payload={"recordId": "r1", "_retry_not_before": time.time() + 500}
        )
        record = _kafka_record({"recordId": "r1"})

        await consumer._publish_to_dlq(record, parsed, "stable-id-1", "exhausted")

        payload = mock_producer.send_event.await_args.kwargs["payload"]
        assert "_retry_not_before" not in payload

    @pytest.mark.asyncio
    async def test_no_producer_does_not_raise(self, logger, plain_config, mock_retry_manager):
        consumer_no_producer = IndexingKafkaConsumer(
            logger, plain_config, retry_manager=mock_retry_manager, producer=None
        )
        parsed = StreamMessage(eventType="test", payload={"recordId": "r1"})
        record = _kafka_record({"recordId": "r1"})

        await consumer_no_producer._publish_to_dlq(record, parsed, "id", "reason")

    @pytest.mark.asyncio
    async def test_producer_error_is_swallowed(self, consumer, mock_producer):
        """DLQ publish failures must not raise -- offset commit still has to
        happen or the message would loop forever."""
        mock_producer.send_event = AsyncMock(side_effect=RuntimeError("kafka down"))
        parsed = StreamMessage(eventType="test", payload={"recordId": "r1"})
        record = _kafka_record({"recordId": "r1"})

        await consumer._publish_to_dlq(record, parsed, "id", "reason")  # must not raise


class TestCommitIfAppropriateDeadLettering:
    @pytest.mark.asyncio
    async def test_exhausted_transient_failure_publishes_to_dlq_and_commits(
        self, consumer, mock_producer, mock_retry_manager
    ):
        consumer.consumer = AsyncMock()
        mock_retry_manager.increment_and_check = AsyncMock(return_value=(3, True))
        parsed = StreamMessage(eventType="test", payload={"recordId": "r1"})
        record = _kafka_record({"recordId": "r1"})

        await consumer._IndexingKafkaConsumer__commit_if_appropriate(
            record, parsed, success=False, failure_reason="Server disconnected"
        )

        mock_producer.send_event.assert_awaited_once()
        assert mock_producer.send_event.await_args.kwargs["topic"] == Topic.RECORD_EVENTS_DLQ.value
        mock_retry_manager.clear.assert_awaited_once()
        consumer.consumer.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_terminal_error_publishes_to_dlq_without_incrementing_retry(
        self, consumer, mock_producer, mock_retry_manager
    ):
        consumer.consumer = AsyncMock()
        parsed = StreamMessage(eventType="test", payload={"recordId": "r1"})
        record = _kafka_record({"recordId": "r1"})

        await consumer._IndexingKafkaConsumer__commit_if_appropriate(
            record, parsed, success=False, is_terminal_error=True, failure_reason="ValidationError"
        )

        mock_producer.send_event.assert_awaited_once()
        assert mock_producer.send_event.await_args.kwargs["topic"] == Topic.RECORD_EVENTS_DLQ.value
        mock_retry_manager.increment_and_check.assert_not_called()
        consumer.consumer.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_transient_failure_not_yet_exhausted_requeues_not_dlq(
        self, consumer, mock_producer, mock_retry_manager
    ):
        consumer.consumer = AsyncMock()
        mock_retry_manager.increment_and_check = AsyncMock(return_value=(1, False))
        parsed = StreamMessage(eventType="test", payload={"recordId": "r1"})
        record = _kafka_record({"recordId": "r1"})

        await consumer._IndexingKafkaConsumer__commit_if_appropriate(
            record, parsed, success=False, failure_reason="transient"
        )

        # Re-queued to the original topic, not the DLQ.
        mock_producer.send_event.assert_awaited_once()
        assert mock_producer.send_event.await_args.kwargs["topic"] == record.topic
        consumer.consumer.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_success_clears_retry_tracking_without_dlq(
        self, consumer, mock_producer, mock_retry_manager
    ):
        consumer.consumer = AsyncMock()
        parsed = StreamMessage(eventType="test", payload={"recordId": "r1"})
        record = _kafka_record({"recordId": "r1"})

        await consumer._IndexingKafkaConsumer__commit_if_appropriate(record, parsed, success=True)

        mock_producer.send_event.assert_not_called()
        mock_retry_manager.clear.assert_awaited_once()
        consumer.consumer.commit.assert_awaited_once()
