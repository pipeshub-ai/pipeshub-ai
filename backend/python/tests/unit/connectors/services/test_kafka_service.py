"""
Tests for KafkaService (connectors):
  - __init__
  - _ensure_producer (double-checked locking, SSL/SASL, error cleanup)
  - publish_event (message key selection, send_and_wait)
  - send_event_to_kafka (event formatting, success and failure)
  - stop_producer (cleanup, error handling)
  - async context manager (__aenter__ / __aexit__)
"""

import asyncio
import json
import logging
import ssl
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.services.kafka_service import KafkaService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def logger():
    return logging.getLogger("test_kafka_service")


@pytest.fixture
def config_service():
    mock = AsyncMock()
    mock.get_config = AsyncMock(
        return_value={
            "brokers": "broker1:9092,broker2:9092",
            "client_id": "test-client",
        }
    )
    return mock


@pytest.fixture
def kafka_service(config_service, logger):
    return KafkaService(config_service, logger)


# ===========================================================================
# __init__
# ===========================================================================


class TestInit:
    """Test constructor initialisation."""

    def test_default_state(self, config_service, logger):
        svc = KafkaService(config_service, logger)
        assert svc.producer is None
        assert svc.config_service is config_service
        assert svc.logger is logger
        assert isinstance(svc._producer_lock, asyncio.Lock)


# ===========================================================================
# _ensure_producer
# ===========================================================================


class TestEnsureProducer:
    """Test the lazy-initialisation path for the Kafka producer."""

    @pytest.mark.asyncio
    async def test_fast_path_already_initialised(self, kafka_service):
        """If producer is already set, _ensure_producer returns immediately."""
        kafka_service.producer = MagicMock()
        with patch(
            "app.connectors.services.kafka_service.AIOKafkaProducer"
        ) as MockProducer:
            await kafka_service._ensure_producer()
            MockProducer.assert_not_called()

    @pytest.mark.asyncio
    async def test_creates_and_starts_producer_plain(self, kafka_service):
        """Happy path: creates producer with plain config and starts it."""
        mock_producer = AsyncMock()
        mock_producer.start = AsyncMock()

        with patch(
            "app.connectors.services.kafka_service.AIOKafkaProducer",
            return_value=mock_producer,
        ) as MockCls:
            await kafka_service._ensure_producer()

        MockCls.assert_called_once()
        call_kwargs = MockCls.call_args.kwargs
        assert call_kwargs["bootstrap_servers"] == "broker1:9092,broker2:9092"
        assert call_kwargs["client_id"] == "test-client"
        assert "ssl_context" not in call_kwargs
        mock_producer.start.assert_awaited_once()
        assert kafka_service.producer is mock_producer

    @pytest.mark.asyncio
    async def test_brokers_as_list(self, kafka_service):
        """Brokers given as list should be joined with commas."""
        kafka_service.config_service.get_config = AsyncMock(
            return_value={
                "brokers": ["b1:9092", "b2:9092"],
                "client_id": "c",
            }
        )
        mock_producer = AsyncMock()
        mock_producer.start = AsyncMock()

        with patch(
            "app.connectors.services.kafka_service.AIOKafkaProducer",
            return_value=mock_producer,
        ) as MockCls:
            await kafka_service._ensure_producer()

        assert MockCls.call_args.kwargs["bootstrap_servers"] == "b1:9092,b2:9092"

    @pytest.mark.asyncio
    async def test_brokers_as_bracketed_string(self, kafka_service):
        """Brokers given as '["b1","b2"]' string should be cleaned."""
        kafka_service.config_service.get_config = AsyncMock(
            return_value={
                "brokers": '["b1:9092","b2:9092"]',
                "client_id": "c",
            }
        )
        mock_producer = AsyncMock()
        mock_producer.start = AsyncMock()

        with patch(
            "app.connectors.services.kafka_service.AIOKafkaProducer",
            return_value=mock_producer,
        ) as MockCls:
            await kafka_service._ensure_producer()

        bs = MockCls.call_args.kwargs["bootstrap_servers"]
        assert "[" not in bs
        assert '"' not in bs

    @pytest.mark.asyncio
    async def test_ssl_without_sasl(self, kafka_service):
        """ssl=True without sasl username -> security_protocol = SSL."""
        kafka_service.config_service.get_config = AsyncMock(
            return_value={
                "brokers": "b:9092",
                "client_id": "c",
                "ssl": True,
            }
        )
        mock_producer = AsyncMock()
        mock_producer.start = AsyncMock()

        with patch(
            "app.connectors.services.kafka_service.AIOKafkaProducer",
            return_value=mock_producer,
        ) as MockCls:
            await kafka_service._ensure_producer()

        kw = MockCls.call_args.kwargs
        assert isinstance(kw["ssl_context"], ssl.SSLContext)
        assert kw["security_protocol"] == "SSL"
        assert "sasl_mechanism" not in kw

    @pytest.mark.asyncio
    async def test_sasl_ssl(self, kafka_service):
        """ssl=True with sasl username -> SASL_SSL."""
        kafka_service.config_service.get_config = AsyncMock(
            return_value={
                "brokers": "b:9092",
                "client_id": "c",
                "ssl": True,
                "sasl": {
                    "username": "user",
                    "password": "pass",
                    "mechanism": "SCRAM-SHA-256",
                },
            }
        )
        mock_producer = AsyncMock()
        mock_producer.start = AsyncMock()

        with patch(
            "app.connectors.services.kafka_service.AIOKafkaProducer",
            return_value=mock_producer,
        ) as MockCls:
            await kafka_service._ensure_producer()

        kw = MockCls.call_args.kwargs
        assert kw["security_protocol"] == "SASL_SSL"
        assert kw["sasl_mechanism"] == "SCRAM-SHA-256"
        assert kw["sasl_plain_username"] == "user"
        assert kw["sasl_plain_password"] == "pass"

    @pytest.mark.asyncio
    async def test_sasl_default_mechanism(self, kafka_service):
        """SASL without explicit mechanism defaults to SCRAM-SHA-512."""
        kafka_service.config_service.get_config = AsyncMock(
            return_value={
                "brokers": "b:9092",
                "client_id": "c",
                "ssl": True,
                "sasl": {"username": "u", "password": "p"},
            }
        )
        mock_producer = AsyncMock()
        mock_producer.start = AsyncMock()

        with patch(
            "app.connectors.services.kafka_service.AIOKafkaProducer",
            return_value=mock_producer,
        ) as MockCls:
            await kafka_service._ensure_producer()

        assert MockCls.call_args.kwargs["sasl_mechanism"] == "SCRAM-SHA-512"

    @pytest.mark.asyncio
    async def test_invalid_config_type_raises(self, kafka_service):
        """Non-dict config should raise ValueError."""
        kafka_service.config_service.get_config = AsyncMock(return_value="not-a-dict")

        with pytest.raises(ValueError, match="dictionary"):
            await kafka_service._ensure_producer()

    @pytest.mark.asyncio
    async def test_start_failure_stops_producer(self, kafka_service):
        """If producer.start() fails, producer.stop() is called and producer is None."""
        mock_producer = AsyncMock()
        mock_producer.start = AsyncMock(side_effect=Exception("start failed"))
        mock_producer.stop = AsyncMock()

        with patch(
            "app.connectors.services.kafka_service.AIOKafkaProducer",
            return_value=mock_producer,
        ):
            with pytest.raises(Exception, match="start failed"):
                await kafka_service._ensure_producer()

        mock_producer.stop.assert_awaited_once()
        assert kafka_service.producer is None

    @pytest.mark.asyncio
    async def test_start_failure_stop_also_fails(self, kafka_service):
        """If both start() and stop() fail, producer is set to None and original error re-raised."""
        mock_producer = AsyncMock()
        mock_producer.start = AsyncMock(side_effect=Exception("start failed"))
        mock_producer.stop = AsyncMock(side_effect=Exception("stop failed"))

        with patch(
            "app.connectors.services.kafka_service.AIOKafkaProducer",
            return_value=mock_producer,
        ):
            with pytest.raises(Exception):
                await kafka_service._ensure_producer()

        assert kafka_service.producer is None

    @pytest.mark.asyncio
    async def test_double_check_locking(self, kafka_service):
        """Concurrent calls should only create one producer."""
        call_count = 0
        mock_producer = AsyncMock()
        mock_producer.start = AsyncMock()

        def factory(**kwargs):
            nonlocal call_count
            call_count += 1
            return mock_producer

        with patch(
            "app.connectors.services.kafka_service.AIOKafkaProducer",
            side_effect=factory,
        ):
            await asyncio.gather(
                kafka_service._ensure_producer(),
                kafka_service._ensure_producer(),
            )

        assert call_count == 1


# ===========================================================================
# publish_event
# ===========================================================================


class TestPublishEvent:
    """Test publish_event method."""

    @pytest.mark.asyncio
    async def test_publish_with_record_id_key(self, kafka_service):
        """Key should come from payload.recordId when available."""
        mock_producer = AsyncMock()
        record_meta = MagicMock(topic="t", partition=0, offset=1)
        mock_producer.send_and_wait = AsyncMock(return_value=record_meta)
        kafka_service.producer = mock_producer

        event = {
            "payload": {"recordId": "rec-123"},
            "timestamp": 1000,
        }
        result = await kafka_service.publish_event("my-topic", event)

        assert result is True
        call_kw = mock_producer.send_and_wait.call_args.kwargs
        assert call_kw["topic"] == "my-topic"
        assert call_kw["key"] == b"rec-123"
        body = json.loads(call_kw["value"].decode("utf-8"))
        assert body["payload"]["recordId"] == "rec-123"

    @pytest.mark.asyncio
    async def test_publish_without_record_id_uses_timestamp(self, kafka_service):
        """When no recordId, key falls back to timestamp."""
        mock_producer = AsyncMock()
        record_meta = MagicMock(topic="t", partition=0, offset=0)
        mock_producer.send_and_wait = AsyncMock(return_value=record_meta)
        kafka_service.producer = mock_producer

        event = {"timestamp": 9999}
        result = await kafka_service.publish_event("topic", event)

        assert result is True
        key = mock_producer.send_and_wait.call_args.kwargs["key"]
        assert key == b"9999"

    @pytest.mark.asyncio
    async def test_publish_without_record_id_or_timestamp(self, kafka_service):
        """When no recordId and no timestamp, key is empty string bytes."""
        mock_producer = AsyncMock()
        record_meta = MagicMock(topic="t", partition=0, offset=0)
        mock_producer.send_and_wait = AsyncMock(return_value=record_meta)
        kafka_service.producer = mock_producer

        result = await kafka_service.publish_event("topic", {})
        assert result is True
        key = mock_producer.send_and_wait.call_args.kwargs["key"]
        assert key == b""

    @pytest.mark.asyncio
    async def test_publish_failure_raises(self, kafka_service):
        """publish_event re-raises on failure."""
        mock_producer = AsyncMock()
        mock_producer.send_and_wait = AsyncMock(side_effect=Exception("send fail"))
        kafka_service.producer = mock_producer

        with pytest.raises(Exception, match="send fail"):
            await kafka_service.publish_event("t", {})

    @pytest.mark.asyncio
    async def test_publish_calls_ensure_producer(self, kafka_service):
        """publish_event should call _ensure_producer first."""
        mock_producer = AsyncMock()
        record_meta = MagicMock(topic="t", partition=0, offset=0)
        mock_producer.send_and_wait = AsyncMock(return_value=record_meta)

        with patch(
            "app.connectors.services.kafka_service.AIOKafkaProducer",
            return_value=mock_producer,
        ):
            mock_producer.start = AsyncMock()
            result = await kafka_service.publish_event("t", {"timestamp": 1})

        assert result is True
        mock_producer.start.assert_awaited_once()


# ===========================================================================
# send_event_to_kafka
# ===========================================================================


class TestSendEventToKafka:
    """Test send_event_to_kafka method."""

    @pytest.mark.asyncio
    async def test_formats_event_correctly(self, kafka_service):
        """Verifies the event is formatted with correct fields."""
        mock_producer = AsyncMock()
        record_meta = MagicMock(topic="record-events", partition=0, offset=5)
        mock_producer.send_and_wait = AsyncMock(return_value=record_meta)
        kafka_service.producer = mock_producer

        event_data = {
            "eventType": "updateRecord",
            "orgId": "org-1",
            "recordId": "rec-1",
            "virtualRecordId": "vrec-1",
            "recordName": "doc.pdf",
            "recordType": "document",
            "recordVersion": 2,
            "connectorName": "google-drive",
            "origin": "sync",
            "extension": ".pdf",
            "mimeType": "application/pdf",
            "body": "content",
            "createdAtSourceTimestamp": 1000,
            "modifiedAtSourceTimestamp": 2000,
        }

        result = await kafka_service.send_event_to_kafka(event_data)
        assert result is True

        call_kw = mock_producer.send_and_wait.call_args.kwargs
        assert call_kw["topic"] == "record-events"
        assert call_kw["key"] == b"rec-1"

        sent = json.loads(call_kw["value"].decode("utf-8"))
        assert sent["eventType"] == "updateRecord"
        assert sent["payload"]["orgId"] == "org-1"
        assert sent["payload"]["recordId"] == "rec-1"
        assert sent["payload"]["virtualRecordId"] == "vrec-1"
        assert sent["payload"]["recordName"] == "doc.pdf"
        assert sent["payload"]["version"] == 2
        assert sent["payload"]["connectorName"] == "google-drive"
        assert sent["payload"]["extension"] == ".pdf"
        assert sent["payload"]["mimeType"] == "application/pdf"
        assert sent["payload"]["body"] == "content"
        assert sent["payload"]["createdAtTimestamp"] == 1000
        assert sent["payload"]["updatedAtTimestamp"] == 2000
        assert sent["payload"]["sourceCreatedAtTimestamp"] == 1000
        assert "timestamp" in sent

    @pytest.mark.asyncio
    async def test_defaults_event_type_to_new_record(self, kafka_service):
        """When eventType is missing, defaults to EventTypes.NEW_RECORD."""
        mock_producer = AsyncMock()
        record_meta = MagicMock(topic="record-events", partition=0, offset=0)
        mock_producer.send_and_wait = AsyncMock(return_value=record_meta)
        kafka_service.producer = mock_producer

        result = await kafka_service.send_event_to_kafka({"recordId": "r1"})
        assert result is True

        sent = json.loads(
            mock_producer.send_and_wait.call_args.kwargs["value"].decode("utf-8")
        )
        assert sent["eventType"] == "newRecord"

    @pytest.mark.asyncio
    async def test_defaults_version_to_zero(self, kafka_service):
        """When recordVersion is missing, defaults to 0."""
        mock_producer = AsyncMock()
        record_meta = MagicMock(topic="record-events", partition=0, offset=0)
        mock_producer.send_and_wait = AsyncMock(return_value=record_meta)
        kafka_service.producer = mock_producer

        await kafka_service.send_event_to_kafka({"recordId": "r1"})
        sent = json.loads(
            mock_producer.send_and_wait.call_args.kwargs["value"].decode("utf-8")
        )
        assert sent["payload"]["version"] == 0

    @pytest.mark.asyncio
    async def test_returns_false_on_exception(self, kafka_service):
        """On failure, send_event_to_kafka returns False (does not raise)."""
        mock_producer = AsyncMock()
        mock_producer.send_and_wait = AsyncMock(side_effect=Exception("boom"))
        kafka_service.producer = mock_producer

        result = await kafka_service.send_event_to_kafka({"recordId": "r1"})
        assert result is False

    @pytest.mark.asyncio
    async def test_calls_ensure_producer(self, kafka_service):
        """send_event_to_kafka should initialise the producer if needed."""
        mock_producer = AsyncMock()
        mock_producer.start = AsyncMock()
        record_meta = MagicMock(topic="record-events", partition=0, offset=0)
        mock_producer.send_and_wait = AsyncMock(return_value=record_meta)

        with patch(
            "app.connectors.services.kafka_service.AIOKafkaProducer",
            return_value=mock_producer,
        ):
            result = await kafka_service.send_event_to_kafka({"recordId": "r1"})

        assert result is True
        mock_producer.start.assert_awaited_once()


# ===========================================================================
# stop_producer
# ===========================================================================


class TestStopProducer:
    """Test stop_producer method."""

    @pytest.mark.asyncio
    async def test_stops_and_clears_producer(self, kafka_service):
        mock_producer = AsyncMock()
        mock_producer.stop = AsyncMock()
        kafka_service.producer = mock_producer

        await kafka_service.stop_producer()

        mock_producer.stop.assert_awaited_once()
        assert kafka_service.producer is None

    @pytest.mark.asyncio
    async def test_noop_when_no_producer(self, kafka_service):
        """Should not raise when producer is None."""
        await kafka_service.stop_producer()

    @pytest.mark.asyncio
    async def test_handles_stop_exception(self, kafka_service):
        """Exception from producer.stop() is caught and logged."""
        mock_producer = AsyncMock()
        mock_producer.stop = AsyncMock(side_effect=Exception("stop error"))
        kafka_service.producer = mock_producer

        # Should not raise
        await kafka_service.stop_producer()


# ===========================================================================
# Async context manager
# ===========================================================================


class TestAsyncContextManager:
    """Test __aenter__ / __aexit__."""

    @pytest.mark.asyncio
    async def test_aenter_calls_ensure_producer(self, kafka_service):
        mock_producer = AsyncMock()
        mock_producer.start = AsyncMock()

        with patch(
            "app.connectors.services.kafka_service.AIOKafkaProducer",
            return_value=mock_producer,
        ):
            result = await kafka_service.__aenter__()

        assert result is kafka_service
        mock_producer.start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_aexit_calls_stop_producer(self, kafka_service):
        mock_producer = AsyncMock()
        mock_producer.stop = AsyncMock()
        kafka_service.producer = mock_producer

        await kafka_service.__aexit__(None, None, None)

        mock_producer.stop.assert_awaited_once()
        assert kafka_service.producer is None

    @pytest.mark.asyncio
    async def test_context_manager_integration(self, kafka_service):
        mock_producer = AsyncMock()
        mock_producer.start = AsyncMock()
        mock_producer.stop = AsyncMock()

        with patch(
            "app.connectors.services.kafka_service.AIOKafkaProducer",
            return_value=mock_producer,
        ):
            async with kafka_service as svc:
                assert svc is kafka_service
                assert kafka_service.producer is mock_producer

        mock_producer.stop.assert_awaited_once()
        assert kafka_service.producer is None
