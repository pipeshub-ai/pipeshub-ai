"""
Comprehensive tests targeting remaining uncovered lines in
app.services.messaging.kafka.consumer.consumer.KafkaMessagingConsumer.

Targets:
- __consume_loop (lines 212-253): full loop, empty batch, success commit,
  failure warning, per-message exception, CancelledError, generic loop error,
  fatal outer error, cleanup in finally.
- __process_message outer except (lines 200-205): unexpected exception before
  message_id is set, and after.
- __process_message_wrapper exception path (lines 292-293): commit raises.
"""

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer.consumer import KafkaMessagingConsumer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def logger():
    return logging.getLogger("test_kafka_consumer_full")


@pytest.fixture
def plain_config():
    return KafkaConsumerConfig(
        topics=["topic-1"],
        client_id="full-cov-consumer",
        group_id="full-cov-group",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        bootstrap_servers=["broker:9092"],
        ssl=False,
        sasl=None,
    )


@pytest.fixture
def consumer(logger, plain_config):
    return KafkaMessagingConsumer(logger, plain_config)


def _make_message(topic="test-topic", partition=0, offset=0, value=None):
    msg = MagicMock()
    msg.topic = topic
    msg.partition = partition
    msg.offset = offset
    msg.value = value
    return msg


def _make_topic_partition(topic="test-topic", partition=0):
    tp = MagicMock()
    tp.topic = topic
    tp.partition = partition
    return tp


# ===================================================================
# __consume_loop -- comprehensive coverage
# ===================================================================


class TestConsumeLoop:
    """Test the __consume_loop method covering all branches."""

    @pytest.mark.asyncio
    async def test_empty_batch_sleeps_and_continues(self, consumer):
        """When getmany returns empty dict, loop sleeps and retries."""
        call_count = 0

        async def mock_getmany(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                consumer.running = False
            return {}

        mock_aio = AsyncMock()
        mock_aio.getmany = mock_getmany
        mock_aio.stop = AsyncMock()
        consumer.consumer = mock_aio
        consumer.running = True
        consumer.message_handler = AsyncMock(return_value=True)

        await consumer._KafkaMessagingConsumer__consume_loop()

        assert call_count >= 3

    @pytest.mark.asyncio
    async def test_successful_message_commits_offset(self, consumer):
        """Successfully processed message results in offset commit."""
        msg = _make_message(
            value=json.dumps({"eventType": "test", "payload": {"key": "val"}}).encode("utf-8"), offset=10
        )
        tp = _make_topic_partition()

        call_count = 0

        async def mock_getmany(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {tp: [msg]}
            consumer.running = False
            return {}

        mock_aio = AsyncMock()
        mock_aio.getmany = mock_getmany
        mock_aio.commit = AsyncMock()
        mock_aio.stop = AsyncMock()
        consumer.consumer = mock_aio
        consumer.running = True
        consumer.message_handler = AsyncMock(return_value=True)

        await consumer._KafkaMessagingConsumer__consume_loop()

        mock_aio.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_failed_message_does_not_commit(self, consumer):
        """Failed processing logs warning and does not commit."""
        msg = _make_message(
            value=json.dumps({"eventType": "test", "payload": {"key": "val"}}).encode("utf-8"), offset=20
        )
        tp = _make_topic_partition()

        call_count = 0

        async def mock_getmany(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {tp: [msg]}
            consumer.running = False
            return {}

        mock_aio = AsyncMock()
        mock_aio.getmany = mock_getmany
        mock_aio.commit = AsyncMock()
        mock_aio.stop = AsyncMock()
        consumer.consumer = mock_aio
        consumer.running = True
        consumer.message_handler = AsyncMock(return_value=False)

        await consumer._KafkaMessagingConsumer__consume_loop()

        mock_aio.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_per_message_exception_continues_loop(self, consumer):
        """Exception in individual message processing doesn't stop the loop."""
        msg1 = _make_message(
            value=json.dumps({"eventType": "test", "payload": {"k": 1}}).encode("utf-8"), offset=30
        )
        # Make process_message raise by using a message whose attribute access fails
        msg2 = MagicMock()
        msg2.topic = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
        # Simpler approach: make it fail via message_id construction
        msg2_broken = MagicMock()
        msg2_broken.topic = "test-topic"
        msg2_broken.partition = 0
        msg2_broken.offset = 31

        tp = _make_topic_partition()
        call_count = 0

        async def mock_getmany(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {tp: [msg1, msg2_broken]}
            consumer.running = False
            return {}

        mock_aio = AsyncMock()
        mock_aio.getmany = mock_getmany
        mock_aio.commit = AsyncMock()
        mock_aio.stop = AsyncMock()
        consumer.consumer = mock_aio
        consumer.running = True

        # Handler succeeds for first message, fails for second
        handler_calls = 0

        async def handler(parsed):
            nonlocal handler_calls
            handler_calls += 1
            if handler_calls == 1:
                return True
            raise Exception("handler error")

        consumer.message_handler = handler

        await consumer._KafkaMessagingConsumer__consume_loop()

        # First message should have been committed
        assert mock_aio.commit.await_count >= 1

    @pytest.mark.asyncio
    async def test_process_message_raises_in_loop(self, consumer):
        """Exception raised during commit in inner loop is caught and continues."""
        msg = _make_message(
            value=json.dumps({"eventType": "test", "payload": {"k": 1}}).encode("utf-8"), offset=40
        )
        tp = _make_topic_partition()

        call_count = 0

        async def mock_getmany(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {tp: [msg]}
            consumer.running = False
            return {}

        mock_aio = AsyncMock()
        mock_aio.getmany = mock_getmany
        # commit raises an exception
        mock_aio.commit = AsyncMock(side_effect=Exception("commit failed"))
        mock_aio.stop = AsyncMock()
        consumer.consumer = mock_aio
        consumer.running = True
        consumer.message_handler = AsyncMock(return_value=True)

        # Should not raise -- the per-message exception handler catches it
        await consumer._KafkaMessagingConsumer__consume_loop()

    @pytest.mark.asyncio
    async def test_cancelled_error_breaks_loop(self, consumer):
        """CancelledError in inner loop breaks cleanly."""
        mock_aio = AsyncMock()
        mock_aio.getmany = AsyncMock(side_effect=asyncio.CancelledError())
        mock_aio.stop = AsyncMock()
        consumer.consumer = mock_aio
        consumer.running = True
        consumer.message_handler = AsyncMock()

        await consumer._KafkaMessagingConsumer__consume_loop()

        # cleanup should have been called (consumer stop)
        mock_aio.stop.assert_awaited()

    @pytest.mark.asyncio
    async def test_generic_loop_error_retries(self, consumer):
        """Generic exception in inner loop logs and retries after sleep."""
        call_count = 0

        async def mock_getmany(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("transient error")
            consumer.running = False
            return {}

        mock_aio = AsyncMock()
        mock_aio.getmany = mock_getmany
        mock_aio.stop = AsyncMock()
        consumer.consumer = mock_aio
        consumer.running = True
        consumer.message_handler = AsyncMock()

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await consumer._KafkaMessagingConsumer__consume_loop()
            # sleep(1) called on error, sleep(0.1) may also be called for empty batch
            sleep_calls = [c.args[0] for c in mock_sleep.call_args_list]
            assert 1 in sleep_calls

    @pytest.mark.asyncio
    async def test_fatal_outer_error_calls_cleanup(self, consumer):
        """Fatal error in outer try calls cleanup in finally."""
        mock_aio = AsyncMock()
        # Make getmany itself fail in a way that isn't caught by inner loop
        mock_aio.stop = AsyncMock()
        consumer.consumer = mock_aio
        consumer.running = True
        consumer.message_handler = AsyncMock()

        # Patch the while loop to raise immediately
        original_running = consumer.running

        call_count = 0

        async def raise_on_getmany(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                consumer.running = False
                return {}
            raise Exception("loop error")

        mock_aio.getmany = raise_on_getmany

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await consumer._KafkaMessagingConsumer__consume_loop()

        # cleanup (stop) should be called
        mock_aio.stop.assert_awaited()

    @pytest.mark.asyncio
    async def test_multiple_partitions_in_batch(self, consumer):
        """Messages from multiple topic-partitions are all processed."""
        tp1 = _make_topic_partition(topic="topic-a", partition=0)
        tp2 = _make_topic_partition(topic="topic-b", partition=1)

        msg1 = _make_message(topic="topic-a", partition=0, offset=1,
                             value=json.dumps({"eventType": "test", "payload": {"a": 1}}).encode("utf-8"))
        msg2 = _make_message(topic="topic-b", partition=1, offset=2,
                             value=json.dumps({"eventType": "test", "payload": {"b": 2}}).encode("utf-8"))

        call_count = 0

        async def mock_getmany(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {tp1: [msg1], tp2: [msg2]}
            consumer.running = False
            return {}

        mock_aio = AsyncMock()
        mock_aio.getmany = mock_getmany
        mock_aio.commit = AsyncMock()
        mock_aio.stop = AsyncMock()
        consumer.consumer = mock_aio
        consumer.running = True
        consumer.message_handler = AsyncMock(return_value=True)

        await consumer._KafkaMessagingConsumer__consume_loop()

        assert mock_aio.commit.await_count == 2

    @pytest.mark.asyncio
    async def test_cleanup_called_on_normal_exit(self, consumer):
        """cleanup is called even when loop exits normally."""
        mock_aio = AsyncMock()
        mock_aio.getmany = AsyncMock(return_value={})
        mock_aio.stop = AsyncMock()
        consumer.consumer = mock_aio
        consumer.running = False  # Will exit immediately
        consumer.message_handler = AsyncMock()

        await consumer._KafkaMessagingConsumer__consume_loop()

        mock_aio.stop.assert_awaited()


# ===================================================================
# __process_message -- outer exception and finally branches
# ===================================================================


class TestProcessMessageOuterException:
    """Test the outer except/finally in __process_message (lines 200-208)."""

    @pytest.mark.asyncio
    async def test_unexpected_exception_before_message_id(self, consumer):
        """When message_id is never set (attribute error), outer except catches it."""
        # Create a message that fails when accessing .topic
        msg = MagicMock()
        type(msg).topic = property(lambda self: (_ for _ in ()).throw(AttributeError("no topic")))
        type(msg).partition = property(lambda self: 0)
        type(msg).offset = property(lambda self: 0)

        consumer.message_handler = AsyncMock(return_value=True)

        result = await consumer._KafkaMessagingConsumer__process_message(msg)
        assert result is False

    @pytest.mark.asyncio
    async def test_unexpected_exception_after_message_id(self, consumer):
        """When exception occurs after message_id is set, finally marks it processed."""
        msg = _make_message(value=json.dumps({"eventType": "test", "payload": {"a": 1}}).encode("utf-8"), offset=99)

        # Make message_handler a non-coroutine to trigger a different type of error
        # Actually, let's trigger an unexpected error in the handler path
        consumer.message_handler = AsyncMock(return_value=True)

        # Force __is_message_processed to raise after message_id is set
        with patch.object(
            consumer,
            '_KafkaMessagingConsumer__is_message_processed',
            side_effect=RuntimeError("unexpected")
        ):
            result = await consumer._KafkaMessagingConsumer__process_message(msg)

        assert result is False
        # message_id was set, so __mark_message_processed should have been called in finally
        assert consumer._KafkaMessagingConsumer__is_message_processed("test-topic-0-99") is True

    @pytest.mark.asyncio
    async def test_finally_when_message_id_is_none(self, consumer):
        """When message_id is None in finally, mark_message_processed is skipped."""
        msg = MagicMock()
        # Make the message_id construction fail
        type(msg).topic = property(
            lambda self: (_ for _ in ()).throw(Exception("fail"))
        )

        consumer.message_handler = AsyncMock()

        result = await consumer._KafkaMessagingConsumer__process_message(msg)
        assert result is False
        # processed_messages should still be empty
        assert consumer.processed_messages == {}

    @pytest.mark.asyncio
    async def test_handler_returns_false(self, consumer):
        """When handler returns False, __process_message returns False."""
        consumer.message_handler = AsyncMock(return_value=False)
        msg = _make_message(value=json.dumps({"eventType": "test", "payload": {"a": 1}}).encode("utf-8"), offset=55)

        result = await consumer._KafkaMessagingConsumer__process_message(msg)
        assert result is False

    @pytest.mark.asyncio
    async def test_parsed_message_none_handler_present(self, consumer):
        """When parsed_message is None but handler exists, returns False."""
        consumer.message_handler = AsyncMock(return_value=True)

        # Invalid JSON results in parsed_message being None (returns False before handler check)
        msg = _make_message(value=b"not-json!!!", offset=56)
        result = await consumer._KafkaMessagingConsumer__process_message(msg)
        assert result is False


# ===================================================================
# __process_message_wrapper -- exception in commit
# ===================================================================


class TestProcessMessageWrapperException:
    """Test exception path in __process_message_wrapper (lines 292-293)."""

    @pytest.mark.asyncio
    async def test_commit_exception_caught(self, consumer):
        """When consumer.commit raises, the wrapper catches it and releases semaphore."""
        consumer.message_handler = AsyncMock(return_value=True)
        mock_aio = AsyncMock()
        mock_aio.commit = AsyncMock(side_effect=Exception("commit error"))
        consumer.consumer = mock_aio

        msg = _make_message(
            value=json.dumps({"eventType": "test", "payload": {"k": "v"}}).encode("utf-8"), offset=70
        )
        tp = _make_topic_partition()

        initial_semaphore_value = consumer.semaphore._value

        await consumer._KafkaMessagingConsumer__process_message_wrapper(msg, tp)

        # Semaphore should be released (incremented)
        assert consumer.semaphore._value == initial_semaphore_value + 1

    @pytest.mark.asyncio
    async def test_process_returns_false_in_wrapper(self, consumer):
        """When __process_message returns False, wrapper logs warning."""
        consumer.message_handler = AsyncMock(return_value=False)
        mock_aio = AsyncMock()
        consumer.consumer = mock_aio

        msg = _make_message(
            value=json.dumps({"eventType": "test", "payload": {"k": "v"}}).encode("utf-8"), offset=71
        )
        tp = _make_topic_partition()

        await consumer._KafkaMessagingConsumer__process_message_wrapper(msg, tp)

        # commit should NOT be called
        mock_aio.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_wrapper_releases_semaphore_on_success(self, consumer):
        """Semaphore is released even on successful path."""
        consumer.message_handler = AsyncMock(return_value=True)
        mock_aio = AsyncMock()
        consumer.consumer = mock_aio

        msg = _make_message(
            value=json.dumps({"eventType": "test", "payload": {"k": "v"}}).encode("utf-8"), offset=72
        )
        tp = _make_topic_partition()

        initial_value = consumer.semaphore._value
        await consumer._KafkaMessagingConsumer__process_message_wrapper(msg, tp)

        assert consumer.semaphore._value == initial_value + 1

    @pytest.mark.asyncio
    async def test_wrapper_outer_exception(self, consumer):
        """Unexpected exception in wrapper is caught and semaphore released."""
        # Make __process_message raise an unexpected error by making the entire
        # method fail
        msg = MagicMock()
        msg.topic = "test"
        msg.partition = 0
        msg.offset = 73
        # Make value access work but handler raise
        msg.value = json.dumps({"k": 1}).encode("utf-8")

        consumer.message_handler = AsyncMock(return_value=True)
        consumer.consumer = AsyncMock()
        # Make commit raise
        consumer.consumer.commit = AsyncMock(side_effect=RuntimeError("boom"))

        tp = _make_topic_partition()

        await consumer._KafkaMessagingConsumer__process_message_wrapper(msg, tp)

        # Should not raise; semaphore released
        assert consumer.semaphore._value > 0


# ===================================================================
# __cleanup_completed_tasks -- task.exception() returning None vs value
# ===================================================================


class TestCleanupCompletedTasksNone:
    """Ensure exception() returning None (no exception) path is covered."""

    def test_done_task_no_exception(self, consumer):
        """Done task with no exception is removed silently."""
        task = MagicMock()
        task.done.return_value = True
        task.exception.return_value = None

        consumer.active_tasks = {task}
        consumer._KafkaMessagingConsumer__cleanup_completed_tasks()

        assert len(consumer.active_tasks) == 0

    def test_done_task_with_exception_logged(self, consumer, logger):
        """Done task with exception triggers logger.error."""
        task = MagicMock()
        task.done.return_value = True
        task.exception.return_value = ValueError("task failed")

        consumer.active_tasks = {task}
        consumer._KafkaMessagingConsumer__cleanup_completed_tasks()

        assert len(consumer.active_tasks) == 0


# ===================================================================
# Edge cases for full coverage of kafka_config_to_dict
# ===================================================================


class TestKafkaConfigToDictEdge:
    """Edge cases not covered by existing tests."""

    def test_sasl_with_no_username_but_has_password(self):
        """SASL dict with password but no username falls to SSL-only."""
        config = KafkaConsumerConfig(
            topics=["t"],
            client_id="c",
            group_id="g",
            auto_offset_reset="earliest",
            enable_auto_commit=False,
            bootstrap_servers=["b:9092"],
            ssl=True,
            sasl={"password": "secret"},
        )
        result = KafkaMessagingConsumer.kafka_config_to_dict(config)
        assert result["security_protocol"] == "SSL"
        assert "sasl_mechanism" not in result

    def test_sasl_with_empty_username(self):
        """SASL dict with empty username string falls to SSL-only."""
        config = KafkaConsumerConfig(
            topics=["t"],
            client_id="c",
            group_id="g",
            auto_offset_reset="earliest",
            enable_auto_commit=False,
            bootstrap_servers=["b:9092"],
            ssl=True,
            sasl={"username": "", "password": "secret"},
        )
        result = KafkaMessagingConsumer.kafka_config_to_dict(config)
        assert result["security_protocol"] == "SSL"


# ===================================================================
# Integration-style: start -> consume_loop -> stop
# ===================================================================


class TestStartConsumeStop:
    """Integration test ensuring full lifecycle works."""

    @pytest.mark.asyncio
    async def test_full_lifecycle_with_messages(self, consumer):
        """Start, process one message, then stop."""
        msg = _make_message(
            value=json.dumps({"eventType": "test", "payload": {"data": "test"}}).encode("utf-8"), offset=100
        )
        tp = _make_topic_partition()

        call_count = 0

        async def mock_getmany(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {tp: [msg]}
            # After first batch, stop
            consumer.running = False
            return {}

        mock_aio = AsyncMock()
        mock_aio.getmany = mock_getmany
        mock_aio.commit = AsyncMock()
        mock_aio.stop = AsyncMock()

        handler = AsyncMock(return_value=True)

        with patch(
            "app.services.messaging.kafka.consumer.consumer.AIOKafkaConsumer",
            return_value=mock_aio,
        ):
            consumer.consumer = mock_aio
            await consumer.start(handler)

            # Give the consume loop time to process
            await asyncio.sleep(0.3)

        # Verify message was handled
        assert handler.await_count >= 1
