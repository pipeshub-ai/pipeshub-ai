"""
Tests for KafkaMessagingProducer:
  - kafka_config_to_dict (SSL, SASL, plain)
  - send_event (message wrapping)
  - initialize (double-checked locking, error handling)
  - cleanup (stops producer)
"""

import asyncio
import json
import logging
import ssl
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.messaging.kafka.config.kafka_config import KafkaProducerConfig
from app.services.messaging.kafka.producer.producer import KafkaMessagingProducer

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def logger():
    return logging.getLogger("test_kafka_producer")


@pytest.fixture
def plain_config():
    return KafkaProducerConfig(
        bootstrap_servers=["broker1:9092", "broker2:9092"],
        client_id="test-client",
        ssl=False,
        sasl=None,
    )


@pytest.fixture
def ssl_config():
    return KafkaProducerConfig(
        bootstrap_servers=["broker:9093"],
        client_id="ssl-client",
        ssl=True,
        sasl=None,
    )


@pytest.fixture
def sasl_config():
    return KafkaProducerConfig(
        bootstrap_servers=["broker:9094"],
        client_id="sasl-client",
        ssl=True,
        sasl={
            "username": "user",
            "password": "pass",
            "mechanism": "SCRAM-SHA-256",
        },
    )


@pytest.fixture
def sasl_default_mechanism_config():
    """SASL config without explicit mechanism -- should default to SCRAM-SHA-512."""
    return KafkaProducerConfig(
        bootstrap_servers=["broker:9094"],
        client_id="sasl-default-client",
        ssl=True,
        sasl={
            "username": "user",
            "password": "pass",
        },
    )


@pytest.fixture
def producer(logger, plain_config):
    return KafkaMessagingProducer(logger, plain_config)


# ===========================================================================
# kafka_config_to_dict
# ===========================================================================


class TestKafkaConfigToDict:
    """Static method that converts KafkaProducerConfig to an aiokafka dict."""

    def test_plain_config(self, plain_config):
        result = KafkaMessagingProducer.kafka_config_to_dict(plain_config)
        assert result["bootstrap_servers"] == "broker1:9092,broker2:9092"
        assert result["client_id"] == "test-client"
        assert "ssl_context" not in result
        assert "security_protocol" not in result

    def test_ssl_without_sasl(self, ssl_config):
        result = KafkaMessagingProducer.kafka_config_to_dict(ssl_config)
        assert isinstance(result["ssl_context"], ssl.SSLContext)
        assert result["security_protocol"] == "SSL"
        assert "sasl_mechanism" not in result

    def test_sasl_ssl_config(self, sasl_config):
        result = KafkaMessagingProducer.kafka_config_to_dict(sasl_config)
        assert result["security_protocol"] == "SASL_SSL"
        assert result["sasl_mechanism"] == "SCRAM-SHA-256"
        assert result["sasl_plain_username"] == "user"
        assert result["sasl_plain_password"] == "pass"
        assert isinstance(result["ssl_context"], ssl.SSLContext)

    def test_sasl_default_mechanism(self, sasl_default_mechanism_config):
        result = KafkaMessagingProducer.kafka_config_to_dict(sasl_default_mechanism_config)
        assert result["sasl_mechanism"] == "SCRAM-SHA-512"

    def test_ssl_true_but_empty_sasl_dict(self):
        """ssl=True with an empty sasl dict should behave like SSL-only."""
        config = KafkaProducerConfig(
            bootstrap_servers=["b:9092"],
            client_id="c",
            ssl=True,
            sasl={},
        )
        result = KafkaMessagingProducer.kafka_config_to_dict(config)
        assert result["security_protocol"] == "SSL"
        assert "sasl_mechanism" not in result

    def test_ssl_true_sasl_no_username(self):
        """ssl=True with sasl dict that has no 'username' key -> SSL only."""
        config = KafkaProducerConfig(
            bootstrap_servers=["b:9092"],
            client_id="c",
            ssl=True,
            sasl={"password": "p"},
        )
        result = KafkaMessagingProducer.kafka_config_to_dict(config)
        assert result["security_protocol"] == "SSL"


# ===========================================================================
# send_event
# ===========================================================================


class TestSendEvent:
    """send_event wraps payload in {eventType, payload, timestamp}."""

    @pytest.mark.asyncio
    async def test_send_event_wraps_message(self, logger, plain_config):
        producer = KafkaMessagingProducer(logger, plain_config)
        producer.send_message = AsyncMock(return_value=True)

        result = await producer.send_event(
            topic="test-topic",
            event_type="USER_CREATED",
            payload={"userId": "123"},
            key="key1",
        )

        assert result is True
        producer.send_message.assert_awaited_once()

        # Inspect the message dict that was passed to send_message
        call_kwargs = producer.send_message.call_args
        sent_message = call_kwargs.kwargs.get("message") or call_kwargs[1].get("message") or call_kwargs[0][1]
        assert sent_message["eventType"] == "USER_CREATED"
        assert sent_message["payload"] == {"userId": "123"}
        assert "timestamp" in sent_message
        assert isinstance(sent_message["timestamp"], int)

    @pytest.mark.asyncio
    async def test_send_event_passes_topic_and_key(self, logger, plain_config):
        producer = KafkaMessagingProducer(logger, plain_config)
        producer.send_message = AsyncMock(return_value=True)

        await producer.send_event(
            topic="my-topic",
            event_type="EVT",
            payload={},
            key="k",
        )

        call_kwargs = producer.send_message.call_args
        assert call_kwargs.kwargs.get("topic") == "my-topic" or call_kwargs[1].get("topic") == "my-topic"
        assert call_kwargs.kwargs.get("key") == "k" or call_kwargs[1].get("key") == "k"

    @pytest.mark.asyncio
    async def test_send_event_raises_on_exception(self, logger, plain_config):
        producer = KafkaMessagingProducer(logger, plain_config)
        producer.send_message = AsyncMock(side_effect=Exception("boom"))

        with pytest.raises(Exception, match="boom"):
            await producer.send_event(
                topic="t", event_type="EVT", payload={}
            )


# ===========================================================================
# initialize (double-checked locking)
# ===========================================================================


class TestInitialize:
    """initialize() with double-checked locking via asyncio.Lock."""

    @pytest.mark.asyncio
    async def test_initialize_creates_and_starts_producer(self, logger, plain_config):
        producer_obj = KafkaMessagingProducer(logger, plain_config)

        mock_aio_producer = AsyncMock()
        mock_aio_producer.start = AsyncMock()

        with patch(
            "app.services.messaging.kafka.producer.producer.AIOKafkaProducer",
            return_value=mock_aio_producer,
        ):
            await producer_obj.initialize()

        mock_aio_producer.start.assert_awaited_once()
        assert producer_obj.producer is mock_aio_producer

    @pytest.mark.asyncio
    async def test_initialize_skips_if_already_initialized(self, logger, plain_config):
        producer_obj = KafkaMessagingProducer(logger, plain_config)
        producer_obj.producer = MagicMock()  # simulate already initialized

        with patch(
            "app.services.messaging.kafka.producer.producer.AIOKafkaProducer"
        ) as MockAIO:
            await producer_obj.initialize()
            MockAIO.assert_not_called()

    @pytest.mark.asyncio
    async def test_initialize_double_check_after_lock(self, logger, plain_config):
        """Even when the fast-path check passes, if another coroutine wins the lock
        and initializes first, the second coroutine should not re-initialize."""
        producer_obj = KafkaMessagingProducer(logger, plain_config)

        call_count = 0

        original_aio = AsyncMock()
        original_aio.start = AsyncMock()

        def side_effect_factory(**kwargs):
            nonlocal call_count
            call_count += 1
            return original_aio

        with patch(
            "app.services.messaging.kafka.producer.producer.AIOKafkaProducer",
            side_effect=side_effect_factory,
        ):
            # Run two concurrent initializations
            await asyncio.gather(
                producer_obj.initialize(),
                producer_obj.initialize(),
            )

        # Only one AIOKafkaProducer should have been constructed
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_initialize_cleans_up_on_start_failure(self, logger, plain_config):
        producer_obj = KafkaMessagingProducer(logger, plain_config)

        mock_aio_producer = AsyncMock()
        mock_aio_producer.start = AsyncMock(side_effect=Exception("start failed"))
        mock_aio_producer.stop = AsyncMock()

        with patch(
            "app.services.messaging.kafka.producer.producer.AIOKafkaProducer",
            return_value=mock_aio_producer,
        ):
            with pytest.raises(Exception, match="start failed"):
                await producer_obj.initialize()

        # Producer should have been stopped and reference cleared
        mock_aio_producer.stop.assert_awaited_once()
        assert producer_obj.producer is None

    @pytest.mark.asyncio
    async def test_initialize_with_invalid_config_raises(self, logger):
        """If kafka_config is falsy, initialize should raise ValueError."""
        producer_obj = KafkaMessagingProducer(logger, None)  # type: ignore

        with pytest.raises(ValueError, match="Kafka configuration is not valid"):
            await producer_obj.initialize()


# ===========================================================================
# cleanup
# ===========================================================================


class TestCleanup:
    """cleanup() stops and clears the underlying producer."""

    @pytest.mark.asyncio
    async def test_cleanup_stops_producer(self, logger, plain_config):
        producer_obj = KafkaMessagingProducer(logger, plain_config)
        mock_aio = AsyncMock()
        mock_aio.stop = AsyncMock()
        producer_obj.producer = mock_aio

        await producer_obj.cleanup()

        mock_aio.stop.assert_awaited_once()
        assert producer_obj.producer is None

    @pytest.mark.asyncio
    async def test_cleanup_noop_when_no_producer(self, logger, plain_config):
        """cleanup when producer is None should not raise."""
        producer_obj = KafkaMessagingProducer(logger, plain_config)
        assert producer_obj.producer is None
        await producer_obj.cleanup()  # should not raise

    @pytest.mark.asyncio
    async def test_cleanup_handles_stop_exception(self, logger, plain_config):
        producer_obj = KafkaMessagingProducer(logger, plain_config)
        mock_aio = AsyncMock()
        mock_aio.stop = AsyncMock(side_effect=Exception("stop failed"))
        producer_obj.producer = mock_aio

        # Should not raise despite the underlying stop() failure
        await producer_obj.cleanup()


# ===========================================================================
# send_message
# ===========================================================================


class TestSendMessage:
    """send_message encodes and sends via AIOKafkaProducer."""

    @pytest.mark.asyncio
    async def test_send_message_success(self, logger, plain_config):
        producer_obj = KafkaMessagingProducer(logger, plain_config)
        mock_aio = AsyncMock()
        record_metadata = MagicMock()
        record_metadata.topic = "t"
        record_metadata.partition = 0
        record_metadata.offset = 42
        mock_aio.send_and_wait = AsyncMock(return_value=record_metadata)
        producer_obj.producer = mock_aio

        result = await producer_obj.send_message("t", {"key": "val"}, key="k")
        assert result is True
        mock_aio.send_and_wait.assert_awaited_once()
        call_kwargs = mock_aio.send_and_wait.call_args.kwargs
        assert call_kwargs["topic"] == "t"
        assert call_kwargs["key"] == b"k"
        assert json.loads(call_kwargs["value"].decode("utf-8")) == {"key": "val"}

    @pytest.mark.asyncio
    async def test_send_message_without_key(self, logger, plain_config):
        producer_obj = KafkaMessagingProducer(logger, plain_config)
        mock_aio = AsyncMock()
        record = MagicMock(topic="t", partition=0, offset=0)
        mock_aio.send_and_wait = AsyncMock(return_value=record)
        producer_obj.producer = mock_aio

        result = await producer_obj.send_message("t", {"data": 1})
        assert result is True
        call_kwargs = mock_aio.send_and_wait.call_args.kwargs
        assert call_kwargs["key"] is None

    @pytest.mark.asyncio
    async def test_send_message_failure_raises(self, logger, plain_config):
        producer_obj = KafkaMessagingProducer(logger, plain_config)
        mock_aio = AsyncMock()
        mock_aio.send_and_wait = AsyncMock(side_effect=Exception("send failed"))
        producer_obj.producer = mock_aio

        with pytest.raises(Exception, match="send failed"):
            await producer_obj.send_message("t", {"data": 1})

    @pytest.mark.asyncio
    async def test_send_message_auto_initializes(self, logger, plain_config):
        """If producer is None, send_message should call initialize()."""
        producer_obj = KafkaMessagingProducer(logger, plain_config)
        assert producer_obj.producer is None

        mock_aio = AsyncMock()
        record = MagicMock(topic="t", partition=0, offset=0)
        mock_aio.send_and_wait = AsyncMock(return_value=record)
        mock_aio.start = AsyncMock()

        with patch(
            "app.services.messaging.kafka.producer.producer.AIOKafkaProducer",
            return_value=mock_aio,
        ):
            result = await producer_obj.send_message("t", {"data": 1})

        assert result is True
        mock_aio.start.assert_awaited_once()


# ===========================================================================
# start / stop
# ===========================================================================


class TestStartStop:
    """Tests for start() and stop() methods."""

    @pytest.mark.asyncio
    async def test_start_calls_initialize_when_no_producer(self, logger, plain_config):
        """start() should call initialize() when producer is None."""
        producer_obj = KafkaMessagingProducer(logger, plain_config)
        assert producer_obj.producer is None

        mock_aio = AsyncMock()
        mock_aio.start = AsyncMock()

        with patch(
            "app.services.messaging.kafka.producer.producer.AIOKafkaProducer",
            return_value=mock_aio,
        ):
            await producer_obj.start()

        mock_aio.start.assert_awaited_once()
        assert producer_obj.producer is mock_aio

    @pytest.mark.asyncio
    async def test_start_skips_when_producer_exists(self, logger, plain_config):
        """start() should not call initialize() when producer already exists."""
        producer_obj = KafkaMessagingProducer(logger, plain_config)
        producer_obj.producer = MagicMock()

        with patch(
            "app.services.messaging.kafka.producer.producer.AIOKafkaProducer"
        ) as MockAIO:
            await producer_obj.start()
            MockAIO.assert_not_called()

    @pytest.mark.asyncio
    async def test_stop_with_producer_recurses(self, logger, plain_config):
        """stop() when producer exists calls self.stop() recursively (a bug).
        This test verifies the code path is reached and causes RecursionError."""
        producer_obj = KafkaMessagingProducer(logger, plain_config)
        producer_obj.producer = MagicMock()

        with pytest.raises(RecursionError):
            await producer_obj.stop()

    @pytest.mark.asyncio
    async def test_stop_noop_when_no_producer(self, logger, plain_config):
        """stop() when producer is None should do nothing."""
        producer_obj = KafkaMessagingProducer(logger, plain_config)
        assert producer_obj.producer is None
        # Should not raise
        await producer_obj.stop()


# ===========================================================================
# initialize - additional edge cases
# ===========================================================================


class TestInitializeEdgeCases:
    """Additional edge cases for initialize()."""

    @pytest.mark.asyncio
    async def test_initialize_double_check_inside_lock(self, logger, plain_config):
        """Covers the second 'if self.producer is not None: return' inside the lock (line 57).

        The fast path (line 51) checks producer is None, so it proceeds.
        Then after acquiring the lock, the double-check finds producer was set
        by another coroutine, so it returns early.
        """
        producer_obj = KafkaMessagingProducer(logger, plain_config)

        mock_aio = AsyncMock()
        mock_aio.start = AsyncMock()

        first_call = True

        original_lock_acquire = producer_obj._producer_lock.acquire

        async def patched_acquire():
            nonlocal first_call
            result = await original_lock_acquire()
            # After the lock is acquired but before the double-check,
            # simulate another coroutine having already initialized the producer
            if first_call:
                first_call = False
                producer_obj.producer = mock_aio
            return result

        with patch.object(
            producer_obj._producer_lock, "acquire", side_effect=patched_acquire
        ):
            with patch(
                "app.services.messaging.kafka.producer.producer.AIOKafkaProducer"
            ) as MockAIO:
                await producer_obj.initialize()
                # AIOKafkaProducer should NOT have been created since the double-check
                # found producer already set
                MockAIO.assert_not_called()

    @pytest.mark.asyncio
    async def test_initialize_cleanup_stop_failure(self, logger, plain_config):
        """When start() fails and then stop() also fails during cleanup,
        the inner except shadows the outer 'e' variable. Due to a bug in the
        source (reusing variable name 'e'), this raises UnboundLocalError
        on line 80 when trying to log str(e). Covers lines 77-78."""
        producer_obj = KafkaMessagingProducer(logger, plain_config)

        mock_aio = AsyncMock()
        mock_aio.start = AsyncMock(side_effect=Exception("start failed"))
        mock_aio.stop = AsyncMock(side_effect=Exception("stop also failed"))

        with patch(
            "app.services.messaging.kafka.producer.producer.AIOKafkaProducer",
            return_value=mock_aio,
        ):
            with pytest.raises(UnboundLocalError):
                await producer_obj.initialize()

        mock_aio.stop.assert_awaited_once()
