"""
Tests for KafkaService (connectors):
  - __init__
  - _ensure_producer (raises when no producer set, calls start when set)
  - set_producer
  - publish_event (message key selection, delegates to send_message)
  - send_event_to_kafka (event formatting, success and failure)
  - stop_producer (calls cleanup, error handling)
  - async context manager (__aenter__ / __aexit__)
"""

import logging
from unittest.mock import AsyncMock

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
    return mock


@pytest.fixture
def mock_producer():
    producer = AsyncMock()
    producer.start = AsyncMock()
    producer.cleanup = AsyncMock()
    producer.send_message = AsyncMock(return_value=True)
    return producer


@pytest.fixture
def kafka_service(config_service, logger):
    return KafkaService(config_service, logger)


@pytest.fixture
def kafka_service_with_producer(config_service, logger, mock_producer):
    return KafkaService(config_service, logger, producer=mock_producer)


# ===========================================================================
# __init__
# ===========================================================================


class TestInit:
    """Test constructor initialisation."""

    def test_default_state_no_producer(self, config_service, logger):
        svc = KafkaService(config_service, logger)
        assert svc._producer is None
        assert svc.config_service is config_service
        assert svc.logger is logger

    def test_producer_passed_via_constructor(self, config_service, logger, mock_producer):
        svc = KafkaService(config_service, logger, producer=mock_producer)
        assert svc._producer is mock_producer

    def test_no_producer_lock_attribute(self, config_service, logger):
        svc = KafkaService(config_service, logger)
        assert not hasattr(svc, "_producer_lock")


# ===========================================================================
# _ensure_producer
# ===========================================================================


class TestEnsureProducer:
    """Test _ensure_producer behaviour."""

    @pytest.mark.asyncio
    async def test_raises_when_no_producer_set(self, kafka_service):
        """RuntimeError is raised when no producer has been configured."""
        with pytest.raises(RuntimeError, match="No messaging producer configured"):
            await kafka_service._ensure_producer()

    @pytest.mark.asyncio
    async def test_calls_start_when_producer_set(self, kafka_service_with_producer, mock_producer):
        """When a producer is set, _ensure_producer calls producer.start()."""
        await kafka_service_with_producer._ensure_producer()
        mock_producer.start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_multiple_calls_call_start_each_time(self, kafka_service_with_producer, mock_producer):
        """_ensure_producer calls start every time it is invoked."""
        await kafka_service_with_producer._ensure_producer()
        await kafka_service_with_producer._ensure_producer()
        assert mock_producer.start.await_count == 2


# ===========================================================================
# set_producer
# ===========================================================================


class TestSetProducer:
    """Test set_producer method."""

    def test_set_producer_updates_internal_attribute(self, kafka_service, mock_producer):
        assert kafka_service._producer is None
        kafka_service.set_producer(mock_producer)
        assert kafka_service._producer is mock_producer

    def test_set_producer_replaces_existing(self, kafka_service_with_producer, mock_producer):
        new_producer = AsyncMock()
        kafka_service_with_producer.set_producer(new_producer)
        assert kafka_service_with_producer._producer is new_producer


# ===========================================================================
# publish_event
# ===========================================================================


class TestPublishEvent:
    """Test publish_event method."""

    @pytest.mark.asyncio
    async def test_publish_with_record_id_key(self, kafka_service_with_producer, mock_producer):
        """Key should come from payload.recordId when available."""
        event = {
            "payload": {"recordId": "rec-123"},
            "timestamp": 1000,
        }
        result = await kafka_service_with_producer.publish_event("my-topic", event)

        assert result is True
        mock_producer.send_message.assert_awaited_once_with(
            topic="my-topic",
            message=event,
            key="rec-123",
        )

    @pytest.mark.asyncio
    async def test_publish_without_record_id_uses_timestamp(self, kafka_service_with_producer, mock_producer):
        """When no recordId, key falls back to str(timestamp)."""
        event = {"timestamp": 9999}
        result = await kafka_service_with_producer.publish_event("topic", event)

        assert result is True
        call_kw = mock_producer.send_message.call_args.kwargs
        assert call_kw["key"] == "9999"

    @pytest.mark.asyncio
    async def test_publish_without_record_id_or_timestamp(self, kafka_service_with_producer, mock_producer):
        """When neither recordId nor timestamp, key is empty string."""
        result = await kafka_service_with_producer.publish_event("topic", {})

        assert result is True
        call_kw = mock_producer.send_message.call_args.kwargs
        assert call_kw["key"] == ""

    @pytest.mark.asyncio
    async def test_publish_returns_send_message_result(self, kafka_service_with_producer, mock_producer):
        """publish_event returns whatever send_message returns."""
        mock_producer.send_message = AsyncMock(return_value=False)
        result = await kafka_service_with_producer.publish_event("t", {"timestamp": 1})
        assert result is False

    @pytest.mark.asyncio
    async def test_publish_failure_raises(self, kafka_service_with_producer, mock_producer):
        """publish_event re-raises on send_message failure."""
        mock_producer.send_message = AsyncMock(side_effect=Exception("send fail"))

        with pytest.raises(Exception, match="send fail"):
            await kafka_service_with_producer.publish_event("t", {})

    @pytest.mark.asyncio
    async def test_publish_raises_without_producer(self, kafka_service):
        """publish_event raises RuntimeError when no producer is configured."""
        with pytest.raises(RuntimeError, match="No messaging producer configured"):
            await kafka_service.publish_event("t", {})


# ===========================================================================
# send_event_to_kafka
# ===========================================================================


class TestSendEventToKafka:
    """Test send_event_to_kafka method."""

    @pytest.mark.asyncio
    async def test_formats_event_correctly(self, kafka_service_with_producer, mock_producer):
        """Verifies the event is formatted with correct fields and sent to record-events."""
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

        result = await kafka_service_with_producer.send_event_to_kafka(event_data)
        assert result is True

        mock_producer.send_message.assert_awaited_once()
        call_kw = mock_producer.send_message.call_args.kwargs
        assert call_kw["topic"] == "record-events"
        assert call_kw["key"] == "rec-1"

        sent = call_kw["message"]
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
    async def test_defaults_event_type_to_new_record(self, kafka_service_with_producer, mock_producer):
        """When eventType is missing, defaults to EventTypes.NEW_RECORD."""
        result = await kafka_service_with_producer.send_event_to_kafka({"recordId": "r1"})
        assert result is True

        sent = mock_producer.send_message.call_args.kwargs["message"]
        assert sent["eventType"] == "newRecord"

    @pytest.mark.asyncio
    async def test_defaults_version_to_zero(self, kafka_service_with_producer, mock_producer):
        """When recordVersion is missing, defaults to 0."""
        await kafka_service_with_producer.send_event_to_kafka({"recordId": "r1"})
        sent = mock_producer.send_message.call_args.kwargs["message"]
        assert sent["payload"]["version"] == 0

    @pytest.mark.asyncio
    async def test_returns_false_on_exception(self, kafka_service_with_producer, mock_producer):
        """On failure, send_event_to_kafka returns False (does not raise)."""
        mock_producer.send_message = AsyncMock(side_effect=Exception("boom"))

        result = await kafka_service_with_producer.send_event_to_kafka({"recordId": "r1"})
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_no_producer(self, kafka_service):
        """Returns False (not RuntimeError) when no producer is configured."""
        result = await kafka_service.send_event_to_kafka({"recordId": "r1"})
        assert result is False

    @pytest.mark.asyncio
    async def test_sends_to_record_events_topic(self, kafka_service_with_producer, mock_producer):
        """The topic is always record-events regardless of event_data content."""
        await kafka_service_with_producer.send_event_to_kafka({"recordId": "r1"})
        call_kw = mock_producer.send_message.call_args.kwargs
        assert call_kw["topic"] == "record-events"


# ===========================================================================
# stop_producer
# ===========================================================================


class TestStopProducer:
    """Test stop_producer method."""

    @pytest.mark.asyncio
    async def test_calls_cleanup_on_producer(self, kafka_service_with_producer, mock_producer):
        await kafka_service_with_producer.stop_producer()
        mock_producer.cleanup.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_noop_when_no_producer(self, kafka_service):
        """Should not raise when producer is None."""
        await kafka_service.stop_producer()

    @pytest.mark.asyncio
    async def test_handles_cleanup_exception(self, kafka_service_with_producer, mock_producer):
        """Exception from producer.cleanup() is caught and logged, not raised."""
        mock_producer.cleanup = AsyncMock(side_effect=Exception("cleanup error"))

        # Should not raise
        await kafka_service_with_producer.stop_producer()


# ===========================================================================
# Async context manager
# ===========================================================================


class TestAsyncContextManager:
    """Test __aenter__ / __aexit__."""

    @pytest.mark.asyncio
    async def test_aenter_raises_when_no_producer(self, kafka_service):
        """__aenter__ raises RuntimeError when no producer has been set."""
        with pytest.raises(RuntimeError, match="No messaging producer configured"):
            await kafka_service.__aenter__()

    @pytest.mark.asyncio
    async def test_aenter_calls_ensure_producer_and_returns_self(
        self, kafka_service_with_producer, mock_producer
    ):
        result = await kafka_service_with_producer.__aenter__()
        assert result is kafka_service_with_producer
        mock_producer.start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_aexit_calls_stop_producer(self, kafka_service_with_producer, mock_producer):
        await kafka_service_with_producer.__aexit__(None, None, None)
        mock_producer.cleanup.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_context_manager_integration(self, kafka_service_with_producer, mock_producer):
        async with kafka_service_with_producer as svc:
            assert svc is kafka_service_with_producer

        mock_producer.start.assert_awaited_once()
        mock_producer.cleanup.assert_awaited_once()
