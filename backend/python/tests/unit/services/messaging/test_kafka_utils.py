"""Tests for app.services.messaging.kafka.utils.utils.KafkaUtils.

Covers:
- _create_base_consumer_config (success, missing config, missing brokers)
- create_producer_config (success, missing config)
- create_entity_kafka_consumer_config
- create_sync_kafka_consumer_config
- create_record_kafka_consumer_config
- create_aiconfig_kafka_consumer_config
- kafka_config_to_dict (plain, SSL, SASL, empty SASL)
- create_entity_message_handler (success, None message, missing event_type, missing payload, exception)
- create_sync_message_handler (success, None message, missing event_type, connector from payload, no connector, exception)
- create_record_message_handler (success, None message, missing event_type, missing payload, exception)
- create_aiconfig_message_handler (success, None message, missing event_type, non-AI event, exception)
"""

import ssl
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.messaging.config import StreamMessage
from app.services.messaging.kafka.config.kafka_config import (
    KafkaConsumerConfig,
    KafkaProducerConfig,
)
from app.services.messaging.kafka.utils.utils import KafkaUtils


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app_container(kafka_config=None):
    """Create a mock app container with config_service."""
    container = MagicMock()
    config_service = AsyncMock()
    container.config_service.return_value = config_service
    if kafka_config is None:
        kafka_config = {
            "brokers": ["broker1:9092", "broker2:9092"],
            "ssl": False,
            "sasl": None,
        }
    config_service.get_config = AsyncMock(return_value=kafka_config)
    container.logger.return_value = MagicMock()
    return container


# ===================================================================
# _create_base_consumer_config
# ===================================================================

class TestCreateBaseConsumerConfig:

    @pytest.mark.asyncio
    async def test_success(self):
        container = _make_app_container()
        config = await KafkaUtils._create_base_consumer_config(
            container, "client-1", "group-1", ["topic-1"]
        )
        assert isinstance(config, KafkaConsumerConfig)
        assert config.client_id == "client-1"
        assert config.group_id == "group-1"
        assert config.topics == ["topic-1"]
        assert config.bootstrap_servers == ["broker1:9092", "broker2:9092"]
        assert config.auto_offset_reset == "earliest"
        assert config.enable_auto_commit is True

    @pytest.mark.asyncio
    async def test_missing_config_raises(self):
        container = _make_app_container(kafka_config=None)
        container.config_service.return_value.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Kafka configuration not found"):
            await KafkaUtils._create_base_consumer_config(
                container, "c", "g", ["t"]
            )

    @pytest.mark.asyncio
    async def test_missing_brokers_raises(self):
        container = _make_app_container(kafka_config={"ssl": False})
        container.config_service.return_value.get_config = AsyncMock(
            return_value={"ssl": False}
        )
        with pytest.raises(ValueError, match="Kafka brokers not found"):
            await KafkaUtils._create_base_consumer_config(
                container, "c", "g", ["t"]
            )

    @pytest.mark.asyncio
    async def test_with_ssl_and_sasl(self):
        kafka_config = {
            "brokers": ["broker:9093"],
            "ssl": True,
            "sasl": {"username": "user", "password": "pass", "mechanism": "PLAIN"},
        }
        container = _make_app_container(kafka_config=kafka_config)
        container.config_service.return_value.get_config = AsyncMock(
            return_value=kafka_config
        )
        config = await KafkaUtils._create_base_consumer_config(
            container, "c", "g", ["t"]
        )
        assert config.ssl is True
        assert config.sasl == {"username": "user", "password": "pass", "mechanism": "PLAIN"}


# ===================================================================
# create_producer_config
# ===================================================================

class TestCreateProducerConfig:

    @pytest.mark.asyncio
    async def test_success(self):
        container = _make_app_container()
        config = await KafkaUtils.create_producer_config(container)
        assert isinstance(config, KafkaProducerConfig)
        assert config.client_id == "messaging_producer_client"
        assert config.bootstrap_servers == ["broker1:9092", "broker2:9092"]

    @pytest.mark.asyncio
    async def test_missing_config_raises(self):
        container = _make_app_container()
        container.config_service.return_value.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Kafka configuration not found"):
            await KafkaUtils.create_producer_config(container)


# ===================================================================
# Specialized consumer config creators
# ===================================================================

class TestSpecializedConsumerConfigs:

    @pytest.mark.asyncio
    async def test_entity_consumer_config(self):
        container = _make_app_container()
        config = await KafkaUtils.create_entity_kafka_consumer_config(container)
        assert config.client_id == "entity_consumer_client"
        assert config.group_id == "entity_consumer_group"
        assert config.topics == ["entity-events"]

    @pytest.mark.asyncio
    async def test_sync_consumer_config(self):
        container = _make_app_container()
        config = await KafkaUtils.create_sync_kafka_consumer_config(container)
        assert config.client_id == "sync_consumer_client"
        assert config.group_id == "sync_consumer_group"
        assert config.topics == ["sync-events"]

    @pytest.mark.asyncio
    async def test_record_consumer_config(self):
        container = _make_app_container()
        config = await KafkaUtils.create_record_kafka_consumer_config(container)
        assert config.client_id == "records_consumer_client"
        assert config.group_id == "records_consumer_group"
        assert config.topics == ["record-events"]

    @pytest.mark.asyncio
    async def test_aiconfig_consumer_config(self):
        container = _make_app_container()
        config = await KafkaUtils.create_aiconfig_kafka_consumer_config(container)
        assert config.client_id == "aiconfig_consumer_client"
        assert config.group_id == "aiconfig_consumer_group"
        assert config.topics == ["ai-config-events"]


# ===================================================================
# kafka_config_to_dict
# ===================================================================

class TestKafkaConfigToDict:

    @pytest.mark.asyncio
    async def test_plain_config(self):
        config = KafkaConsumerConfig(
            topics=["t1", "t2"],
            client_id="c",
            group_id="g",
            auto_offset_reset="earliest",
            enable_auto_commit=True,
            bootstrap_servers=["b1:9092", "b2:9092"],
            ssl=False,
        )
        result = await KafkaUtils.kafka_config_to_dict(config)
        assert result["bootstrap_servers"] == "b1:9092,b2:9092"
        assert result["group_id"] == "g"
        assert result["topics"] == ["t1", "t2"]
        assert "ssl_context" not in result
        assert "security_protocol" not in result

    @pytest.mark.asyncio
    async def test_ssl_without_sasl(self):
        config = KafkaConsumerConfig(
            topics=["t"],
            client_id="c",
            group_id="g",
            auto_offset_reset="earliest",
            enable_auto_commit=True,
            bootstrap_servers=["b:9093"],
            ssl=True,
            sasl=None,
        )
        result = await KafkaUtils.kafka_config_to_dict(config)
        assert isinstance(result["ssl_context"], ssl.SSLContext)
        assert result["security_protocol"] == "SSL"
        assert "sasl_mechanism" not in result

    @pytest.mark.asyncio
    async def test_sasl_ssl_config(self):
        config = KafkaConsumerConfig(
            topics=["t"],
            client_id="c",
            group_id="g",
            auto_offset_reset="earliest",
            enable_auto_commit=True,
            bootstrap_servers=["b:9094"],
            ssl=True,
            sasl={"username": "user", "password": "pass", "mechanism": "SCRAM-SHA-256"},
        )
        result = await KafkaUtils.kafka_config_to_dict(config)
        assert result["security_protocol"] == "SASL_SSL"
        assert result["sasl_mechanism"] == "SCRAM-SHA-256"
        assert result["sasl_plain_username"] == "user"
        assert result["sasl_plain_password"] == "pass"

    @pytest.mark.asyncio
    async def test_sasl_default_mechanism(self):
        config = KafkaConsumerConfig(
            topics=["t"],
            client_id="c",
            group_id="g",
            auto_offset_reset="earliest",
            enable_auto_commit=True,
            bootstrap_servers=["b:9094"],
            ssl=True,
            sasl={"username": "user", "password": "pass"},
        )
        result = await KafkaUtils.kafka_config_to_dict(config)
        assert result["sasl_mechanism"] == "SCRAM-SHA-512"

    @pytest.mark.asyncio
    async def test_ssl_with_empty_sasl(self):
        config = KafkaConsumerConfig(
            topics=["t"],
            client_id="c",
            group_id="g",
            auto_offset_reset="earliest",
            enable_auto_commit=True,
            bootstrap_servers=["b:9093"],
            ssl=True,
            sasl={},
        )
        result = await KafkaUtils.kafka_config_to_dict(config)
        assert result["security_protocol"] == "SSL"
        assert "sasl_mechanism" not in result


# ===================================================================
# create_entity_message_handler
# ===================================================================

class TestCreateEntityMessageHandler:

    @pytest.mark.asyncio
    async def test_success(self):
        container = _make_app_container()
        graph_provider = MagicMock()
        with patch("app.services.messaging.kafka.utils.utils.EntityEventService") as MockSvc:
            mock_svc = AsyncMock()
            mock_svc.process_event = AsyncMock(return_value=True)
            MockSvc.return_value = mock_svc

            handler = await KafkaUtils.create_entity_message_handler(container, graph_provider)
            result = await handler(StreamMessage(eventType="orgCreated", payload={"orgId": "o1"}))
            assert result is True
            mock_svc.process_event.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_none_message_returns_false(self):
        """None is invalid; handler catches errors and returns False."""
        container = _make_app_container()
        graph_provider = MagicMock()
        with patch("app.services.messaging.kafka.utils.utils.EntityEventService"):
            handler = await KafkaUtils.create_entity_message_handler(container, graph_provider)
            result = await handler(None)
            assert result is False

    @pytest.mark.asyncio
    async def test_missing_event_type(self):
        container = _make_app_container()
        graph_provider = MagicMock()
        with patch("app.services.messaging.kafka.utils.utils.EntityEventService"):
            handler = await KafkaUtils.create_entity_message_handler(container, graph_provider)
            result = await handler(StreamMessage(eventType="", payload={"key": "val"}))
            assert result is False

    @pytest.mark.asyncio
    async def test_missing_payload(self):
        container = _make_app_container()
        graph_provider = MagicMock()
        with patch("app.services.messaging.kafka.utils.utils.EntityEventService"):
            handler = await KafkaUtils.create_entity_message_handler(container, graph_provider)
            result = await handler(StreamMessage(eventType="orgCreated", payload={}))
            assert result is False

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        container = _make_app_container()
        graph_provider = MagicMock()
        with patch("app.services.messaging.kafka.utils.utils.EntityEventService") as MockSvc:
            mock_svc = AsyncMock()
            mock_svc.process_event = AsyncMock(side_effect=Exception("error"))
            MockSvc.return_value = mock_svc

            handler = await KafkaUtils.create_entity_message_handler(container, graph_provider)
            result = await handler(StreamMessage(eventType="orgCreated", payload={"orgId": "o1"}))
            assert result is False


# ===================================================================
# create_sync_message_handler
# ===================================================================

class TestCreateSyncMessageHandler:

    @pytest.mark.asyncio
    async def test_success_connector_from_event_type(self):
        container = _make_app_container()
        graph_provider = MagicMock()
        with patch("app.services.messaging.kafka.utils.utils.EventService") as MockSvc:
            mock_svc = AsyncMock()
            mock_svc.process_event = AsyncMock(return_value=True)
            MockSvc.return_value = mock_svc

            handler = await KafkaUtils.create_sync_message_handler(container, graph_provider)
            result = await handler(StreamMessage(eventType="gmail.start", payload={"orgId": "o1"}))
            assert result is True

    @pytest.mark.asyncio
    async def test_success_connector_from_payload(self):
        container = _make_app_container()
        graph_provider = MagicMock()
        with patch("app.services.messaging.kafka.utils.utils.EventService") as MockSvc:
            mock_svc = AsyncMock()
            mock_svc.process_event = AsyncMock(return_value=True)
            MockSvc.return_value = mock_svc

            handler = await KafkaUtils.create_sync_message_handler(container, graph_provider)
            result = await handler(StreamMessage(eventType="start", payload={"connector": "gmail"}))
            assert result is True

    @pytest.mark.asyncio
    async def test_none_message_returns_false(self):
        """None is invalid; handler catches errors and returns False."""
        container = _make_app_container()
        graph_provider = MagicMock()
        handler = await KafkaUtils.create_sync_message_handler(container, graph_provider)
        result = await handler(None)
        assert result is False

    @pytest.mark.asyncio
    async def test_missing_event_type(self):
        container = _make_app_container()
        graph_provider = MagicMock()
        handler = await KafkaUtils.create_sync_message_handler(container, graph_provider)
        result = await handler(StreamMessage(eventType="", payload={}))
        assert result is False

    @pytest.mark.asyncio
    async def test_no_connector_found(self):
        container = _make_app_container()
        graph_provider = MagicMock()
        handler = await KafkaUtils.create_sync_message_handler(container, graph_provider)
        # event_type has no dot and payload has no connector
        result = await handler(StreamMessage(eventType="start", payload={}))
        assert result is False

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        container = _make_app_container()
        graph_provider = MagicMock()
        with patch("app.services.messaging.kafka.utils.utils.EventService") as MockSvc:
            mock_svc = AsyncMock()
            mock_svc.process_event = AsyncMock(side_effect=Exception("error"))
            MockSvc.return_value = mock_svc

            handler = await KafkaUtils.create_sync_message_handler(container, graph_provider)
            result = await handler(StreamMessage(eventType="gmail.start", payload={"orgId": "o1"}))
            assert result is False


# ===================================================================
# create_record_message_handler
# ===================================================================

class TestCreateRecordMessageHandler:

    @pytest.mark.asyncio
    async def test_success(self):
        container = _make_app_container()
        # Setup event_processor
        mock_event_processor = AsyncMock()
        container._event_processor = mock_event_processor
        container.config_service.return_value = AsyncMock()

        with patch("app.services.messaging.kafka.utils.utils.RecordEventHandler") as MockHandler:
            mock_handler = AsyncMock()

            async def mock_process(event_type, payload):
                yield {"event": "parsing_complete", "data": {}}
                yield {"event": "indexing_complete", "data": {}}

            mock_handler.process_event = mock_process
            MockHandler.return_value = mock_handler

            handler = await KafkaUtils.create_record_message_handler(container)
            events = []
            async for event in handler(StreamMessage(eventType="recordCreated", payload={"id": "r1"})):
                events.append(event)
            assert len(events) == 2
            assert events[0]["event"] == "parsing_complete"
            assert events[1]["event"] == "indexing_complete"

    @pytest.mark.asyncio
    async def test_none_message_raises(self):
        """Handler now expects StreamMessage, not None. Passing None should raise."""
        container = _make_app_container()
        container._event_processor = AsyncMock()
        container.config_service.return_value = AsyncMock()

        with patch("app.services.messaging.kafka.utils.utils.RecordEventHandler"):
            handler = await KafkaUtils.create_record_message_handler(container)
            with pytest.raises((TypeError, AttributeError)):
                async for _ in handler(None):
                    pass

    @pytest.mark.asyncio
    async def test_missing_event_type(self):
        container = _make_app_container()
        container._event_processor = AsyncMock()
        container.config_service.return_value = AsyncMock()

        with patch("app.services.messaging.kafka.utils.utils.RecordEventHandler"):
            handler = await KafkaUtils.create_record_message_handler(container)
            events = []
            async for event in handler(StreamMessage(eventType="", payload={"id": "r1"})):
                events.append(event)
            assert len(events) == 0

    @pytest.mark.asyncio
    async def test_missing_payload(self):
        container = _make_app_container()
        container._event_processor = AsyncMock()
        container.config_service.return_value = AsyncMock()

        with patch("app.services.messaging.kafka.utils.utils.RecordEventHandler"):
            handler = await KafkaUtils.create_record_message_handler(container)
            events = []
            async for event in handler(StreamMessage(eventType="recordCreated", payload={})):
                events.append(event)
            assert len(events) == 0

    @pytest.mark.asyncio
    async def test_exception_raises(self):
        container = _make_app_container()
        container._event_processor = AsyncMock()
        container.config_service.return_value = AsyncMock()

        with patch("app.services.messaging.kafka.utils.utils.RecordEventHandler") as MockHandler:
            mock_handler = AsyncMock()

            async def mock_process(event_type, payload):
                raise RuntimeError("processing error")
                yield  # noqa - needed for generator

            mock_handler.process_event = mock_process
            MockHandler.return_value = mock_handler

            handler = await KafkaUtils.create_record_message_handler(container)
            with pytest.raises(RuntimeError, match="processing error"):
                async for _ in handler(StreamMessage(eventType="recordCreated", payload={"id": "r1"})):
                    pass

    @pytest.mark.asyncio
    async def test_uses_cached_event_processor(self):
        container = _make_app_container()
        cached_processor = AsyncMock()
        container._event_processor = cached_processor
        container.config_service.return_value = AsyncMock()

        with patch("app.services.messaging.kafka.utils.utils.RecordEventHandler") as MockHandler:
            MockHandler.return_value = AsyncMock()
            await KafkaUtils.create_record_message_handler(container)
            MockHandler.assert_called_once()
            call_kwargs = MockHandler.call_args[1]
            assert call_kwargs["event_processor"] is cached_processor

    @pytest.mark.asyncio
    async def test_resolves_event_processor_when_not_cached(self):
        container = _make_app_container()
        # No _event_processor attribute
        if hasattr(container, '_event_processor'):
            delattr(container, '_event_processor')
        resolved_processor = AsyncMock()
        container.event_processor = AsyncMock(return_value=resolved_processor)
        container.config_service.return_value = AsyncMock()

        with patch("app.services.messaging.kafka.utils.utils.RecordEventHandler") as MockHandler:
            MockHandler.return_value = AsyncMock()
            await KafkaUtils.create_record_message_handler(container)
            container.event_processor.assert_awaited_once()


# ===================================================================
# create_aiconfig_message_handler
# ===================================================================

class TestCreateAiConfigMessageHandler:

    @pytest.mark.asyncio
    async def test_llm_configured_event(self):
        container = _make_app_container()
        mock_retrieval = AsyncMock()
        container.retrieval_service = AsyncMock(return_value=mock_retrieval)

        with patch("app.services.messaging.kafka.utils.utils.AiConfigEventService") as MockSvc:
            mock_svc = AsyncMock()
            mock_svc.process_event = AsyncMock(return_value=True)
            MockSvc.return_value = mock_svc

            handler = await KafkaUtils.create_aiconfig_message_handler(container)
            result = await handler(StreamMessage(eventType="llmConfigured", payload={"provider": "openai"}))
            assert result is True
            mock_svc.process_event.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_embedding_configured_event(self):
        container = _make_app_container()
        mock_retrieval = AsyncMock()
        container.retrieval_service = AsyncMock(return_value=mock_retrieval)

        with patch("app.services.messaging.kafka.utils.utils.AiConfigEventService") as MockSvc:
            mock_svc = AsyncMock()
            mock_svc.process_event = AsyncMock(return_value=True)
            MockSvc.return_value = mock_svc

            handler = await KafkaUtils.create_aiconfig_message_handler(container)
            result = await handler(StreamMessage(eventType="embeddingModelConfigured", payload={"model": "ada"}))
            assert result is True

    @pytest.mark.asyncio
    async def test_none_message_returns_false(self):
        """None is invalid; handler catches errors and returns False."""
        container = _make_app_container()
        mock_retrieval = AsyncMock()
        container.retrieval_service = AsyncMock(return_value=mock_retrieval)

        with patch("app.services.messaging.kafka.utils.utils.AiConfigEventService"):
            handler = await KafkaUtils.create_aiconfig_message_handler(container)
            result = await handler(None)
            assert result is False

    @pytest.mark.asyncio
    async def test_missing_event_type(self):
        container = _make_app_container()
        mock_retrieval = AsyncMock()
        container.retrieval_service = AsyncMock(return_value=mock_retrieval)

        with patch("app.services.messaging.kafka.utils.utils.AiConfigEventService"):
            handler = await KafkaUtils.create_aiconfig_message_handler(container)
            result = await handler(StreamMessage(eventType="", payload={}))
            assert result is False

    @pytest.mark.asyncio
    async def test_non_ai_config_event_skipped(self):
        container = _make_app_container()
        mock_retrieval = AsyncMock()
        container.retrieval_service = AsyncMock(return_value=mock_retrieval)

        with patch("app.services.messaging.kafka.utils.utils.AiConfigEventService") as MockSvc:
            mock_svc = AsyncMock()
            MockSvc.return_value = mock_svc

            handler = await KafkaUtils.create_aiconfig_message_handler(container)
            result = await handler(StreamMessage(eventType="userAdded", payload={}))
            assert result is True
            mock_svc.process_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        container = _make_app_container()
        mock_retrieval = AsyncMock()
        container.retrieval_service = AsyncMock(return_value=mock_retrieval)

        with patch("app.services.messaging.kafka.utils.utils.AiConfigEventService") as MockSvc:
            mock_svc = AsyncMock()
            mock_svc.process_event = AsyncMock(side_effect=Exception("error"))
            MockSvc.return_value = mock_svc

            handler = await KafkaUtils.create_aiconfig_message_handler(container)
            result = await handler(StreamMessage(eventType="llmConfigured", payload={}))
            assert result is False

# =============================================================================
# Merged from test_kafka_utils_coverage.py
# =============================================================================

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app_container(kafka_config=None):
    """Create a mock app container with config_service."""
    container = MagicMock()
    config_service = AsyncMock()
    container.config_service.return_value = config_service
    if kafka_config is None:
        kafka_config = {
            "brokers": ["broker1:9092", "broker2:9092"],
            "ssl": False,
            "sasl": None,
        }
    config_service.get_config = AsyncMock(return_value=kafka_config)
    container.logger.return_value = MagicMock()
    return container


# ===================================================================
# _create_base_consumer_config
# ===================================================================

class TestCreateBaseConsumerConfigCoverage:

    @pytest.mark.asyncio
    async def test_success(self):
        container = _make_app_container()
        config = await KafkaUtils._create_base_consumer_config(
            container, "client-1", "group-1", ["topic-1"]
        )
        assert isinstance(config, KafkaConsumerConfig)
        assert config.client_id == "client-1"
        assert config.group_id == "group-1"
        assert config.topics == ["topic-1"]
        assert config.bootstrap_servers == ["broker1:9092", "broker2:9092"]
        assert config.auto_offset_reset == "earliest"
        assert config.enable_auto_commit is True

    @pytest.mark.asyncio
    async def test_missing_config_raises(self):
        container = _make_app_container(kafka_config=None)
        container.config_service.return_value.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Kafka configuration not found"):
            await KafkaUtils._create_base_consumer_config(
                container, "c", "g", ["t"]
            )

    @pytest.mark.asyncio
    async def test_missing_brokers_raises(self):
        container = _make_app_container(kafka_config={"ssl": False})
        container.config_service.return_value.get_config = AsyncMock(
            return_value={"ssl": False}
        )
        with pytest.raises(ValueError, match="Kafka brokers not found"):
            await KafkaUtils._create_base_consumer_config(
                container, "c", "g", ["t"]
            )

    @pytest.mark.asyncio
    async def test_with_ssl_and_sasl(self):
        kafka_config = {
            "brokers": ["broker:9093"],
            "ssl": True,
            "sasl": {"username": "user", "password": "pass", "mechanism": "PLAIN"},
        }
        container = _make_app_container(kafka_config=kafka_config)
        container.config_service.return_value.get_config = AsyncMock(
            return_value=kafka_config
        )
        config = await KafkaUtils._create_base_consumer_config(
            container, "c", "g", ["t"]
        )
        assert config.ssl is True
        assert config.sasl == {"username": "user", "password": "pass", "mechanism": "PLAIN"}


# ===================================================================
# create_producer_config
# ===================================================================

class TestCreateProducerConfigCoverage:

    @pytest.mark.asyncio
    async def test_success(self):
        container = _make_app_container()
        config = await KafkaUtils.create_producer_config(container)
        assert isinstance(config, KafkaProducerConfig)
        assert config.client_id == "messaging_producer_client"
        assert config.bootstrap_servers == ["broker1:9092", "broker2:9092"]

    @pytest.mark.asyncio
    async def test_missing_config_raises(self):
        container = _make_app_container()
        container.config_service.return_value.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Kafka configuration not found"):
            await KafkaUtils.create_producer_config(container)


# ===================================================================
# Specialized consumer config creators
# ===================================================================

class TestSpecializedConsumerConfigsCoverage:

    @pytest.mark.asyncio
    async def test_entity_consumer_config(self):
        container = _make_app_container()
        config = await KafkaUtils.create_entity_kafka_consumer_config(container)
        assert config.client_id == "entity_consumer_client"
        assert config.group_id == "entity_consumer_group"
        assert config.topics == ["entity-events"]

    @pytest.mark.asyncio
    async def test_sync_consumer_config(self):
        container = _make_app_container()
        config = await KafkaUtils.create_sync_kafka_consumer_config(container)
        assert config.client_id == "sync_consumer_client"
        assert config.group_id == "sync_consumer_group"
        assert config.topics == ["sync-events"]

    @pytest.mark.asyncio
    async def test_record_consumer_config(self):
        container = _make_app_container()
        config = await KafkaUtils.create_record_kafka_consumer_config(container)
        assert config.client_id == "records_consumer_client"
        assert config.group_id == "records_consumer_group"
        assert config.topics == ["record-events"]

    @pytest.mark.asyncio
    async def test_aiconfig_consumer_config(self):
        container = _make_app_container()
        config = await KafkaUtils.create_aiconfig_kafka_consumer_config(container)
        assert config.client_id == "aiconfig_consumer_client"
        assert config.group_id == "aiconfig_consumer_group"
        assert config.topics == ["ai-config-events"]


# ===================================================================
# kafka_config_to_dict
# ===================================================================

class TestKafkaConfigToDictCoverage:

    @pytest.mark.asyncio
    async def test_plain_config(self):
        config = KafkaConsumerConfig(
            topics=["t1", "t2"],
            client_id="c",
            group_id="g",
            auto_offset_reset="earliest",
            enable_auto_commit=True,
            bootstrap_servers=["b1:9092", "b2:9092"],
            ssl=False,
        )
        result = await KafkaUtils.kafka_config_to_dict(config)
        assert result["bootstrap_servers"] == "b1:9092,b2:9092"
        assert result["group_id"] == "g"
        assert result["topics"] == ["t1", "t2"]
        assert "ssl_context" not in result
        assert "security_protocol" not in result

    @pytest.mark.asyncio
    async def test_ssl_without_sasl(self):
        config = KafkaConsumerConfig(
            topics=["t"],
            client_id="c",
            group_id="g",
            auto_offset_reset="earliest",
            enable_auto_commit=True,
            bootstrap_servers=["b:9093"],
            ssl=True,
            sasl=None,
        )
        result = await KafkaUtils.kafka_config_to_dict(config)
        assert isinstance(result["ssl_context"], ssl.SSLContext)
        assert result["security_protocol"] == "SSL"
        assert "sasl_mechanism" not in result

    @pytest.mark.asyncio
    async def test_sasl_ssl_config(self):
        config = KafkaConsumerConfig(
            topics=["t"],
            client_id="c",
            group_id="g",
            auto_offset_reset="earliest",
            enable_auto_commit=True,
            bootstrap_servers=["b:9094"],
            ssl=True,
            sasl={"username": "user", "password": "pass", "mechanism": "SCRAM-SHA-256"},
        )
        result = await KafkaUtils.kafka_config_to_dict(config)
        assert result["security_protocol"] == "SASL_SSL"
        assert result["sasl_mechanism"] == "SCRAM-SHA-256"
        assert result["sasl_plain_username"] == "user"
        assert result["sasl_plain_password"] == "pass"

    @pytest.mark.asyncio
    async def test_sasl_default_mechanism(self):
        config = KafkaConsumerConfig(
            topics=["t"],
            client_id="c",
            group_id="g",
            auto_offset_reset="earliest",
            enable_auto_commit=True,
            bootstrap_servers=["b:9094"],
            ssl=True,
            sasl={"username": "user", "password": "pass"},
        )
        result = await KafkaUtils.kafka_config_to_dict(config)
        assert result["sasl_mechanism"] == "SCRAM-SHA-512"

    @pytest.mark.asyncio
    async def test_ssl_with_empty_sasl(self):
        config = KafkaConsumerConfig(
            topics=["t"],
            client_id="c",
            group_id="g",
            auto_offset_reset="earliest",
            enable_auto_commit=True,
            bootstrap_servers=["b:9093"],
            ssl=True,
            sasl={},
        )
        result = await KafkaUtils.kafka_config_to_dict(config)
        assert result["security_protocol"] == "SSL"
        assert "sasl_mechanism" not in result


# ===================================================================
# create_entity_message_handler
# ===================================================================

class TestCreateEntityMessageHandlerCoverage:

    @pytest.mark.asyncio
    async def test_success(self):
        container = _make_app_container()
        graph_provider = MagicMock()
        with patch("app.services.messaging.kafka.utils.utils.EntityEventService") as MockSvc:
            mock_svc = AsyncMock()
            mock_svc.process_event = AsyncMock(return_value=True)
            MockSvc.return_value = mock_svc

            handler = await KafkaUtils.create_entity_message_handler(container, graph_provider)
            result = await handler(StreamMessage(eventType="orgCreated", payload={"orgId": "o1"}))
            assert result is True
            mock_svc.process_event.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_none_message_returns_false(self):
        container = _make_app_container()
        graph_provider = MagicMock()
        with patch("app.services.messaging.kafka.utils.utils.EntityEventService"):
            handler = await KafkaUtils.create_entity_message_handler(container, graph_provider)
            result = await handler(None)
            assert result is False

    @pytest.mark.asyncio
    async def test_missing_event_type(self):
        container = _make_app_container()
        graph_provider = MagicMock()
        with patch("app.services.messaging.kafka.utils.utils.EntityEventService"):
            handler = await KafkaUtils.create_entity_message_handler(container, graph_provider)
            result = await handler(StreamMessage(eventType="", payload={"key": "val"}))
            assert result is False

    @pytest.mark.asyncio
    async def test_missing_payload(self):
        container = _make_app_container()
        graph_provider = MagicMock()
        with patch("app.services.messaging.kafka.utils.utils.EntityEventService"):
            handler = await KafkaUtils.create_entity_message_handler(container, graph_provider)
            result = await handler(StreamMessage(eventType="orgCreated", payload={}))
            assert result is False

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        container = _make_app_container()
        graph_provider = MagicMock()
        with patch("app.services.messaging.kafka.utils.utils.EntityEventService") as MockSvc:
            mock_svc = AsyncMock()
            mock_svc.process_event = AsyncMock(side_effect=Exception("error"))
            MockSvc.return_value = mock_svc

            handler = await KafkaUtils.create_entity_message_handler(container, graph_provider)
            result = await handler(StreamMessage(eventType="orgCreated", payload={"orgId": "o1"}))
            assert result is False


# ===================================================================
# create_sync_message_handler
# ===================================================================

class TestCreateSyncMessageHandlerCoverage:

    @pytest.mark.asyncio
    async def test_success_connector_from_event_type(self):
        container = _make_app_container()
        graph_provider = MagicMock()
        with patch("app.services.messaging.kafka.utils.utils.EventService") as MockSvc:
            mock_svc = AsyncMock()
            mock_svc.process_event = AsyncMock(return_value=True)
            MockSvc.return_value = mock_svc

            handler = await KafkaUtils.create_sync_message_handler(container, graph_provider)
            result = await handler(StreamMessage(eventType="gmail.start", payload={"orgId": "o1"}))
            assert result is True

    @pytest.mark.asyncio
    async def test_success_connector_from_payload(self):
        container = _make_app_container()
        graph_provider = MagicMock()
        with patch("app.services.messaging.kafka.utils.utils.EventService") as MockSvc:
            mock_svc = AsyncMock()
            mock_svc.process_event = AsyncMock(return_value=True)
            MockSvc.return_value = mock_svc

            handler = await KafkaUtils.create_sync_message_handler(container, graph_provider)
            result = await handler(StreamMessage(eventType="start", payload={"connector": "gmail"}))
            assert result is True

    @pytest.mark.asyncio
    async def test_none_message_returns_false(self):
        container = _make_app_container()
        graph_provider = MagicMock()
        handler = await KafkaUtils.create_sync_message_handler(container, graph_provider)
        result = await handler(None)
        assert result is False

    @pytest.mark.asyncio
    async def test_missing_event_type(self):
        container = _make_app_container()
        graph_provider = MagicMock()
        handler = await KafkaUtils.create_sync_message_handler(container, graph_provider)
        result = await handler(StreamMessage(eventType="", payload={}))
        assert result is False

    @pytest.mark.asyncio
    async def test_no_connector_found(self):
        container = _make_app_container()
        graph_provider = MagicMock()
        handler = await KafkaUtils.create_sync_message_handler(container, graph_provider)
        result = await handler(StreamMessage(eventType="start", payload={}))
        assert result is False

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        container = _make_app_container()
        graph_provider = MagicMock()
        with patch("app.services.messaging.kafka.utils.utils.EventService") as MockSvc:
            mock_svc = AsyncMock()
            mock_svc.process_event = AsyncMock(side_effect=Exception("error"))
            MockSvc.return_value = mock_svc

            handler = await KafkaUtils.create_sync_message_handler(container, graph_provider)
            result = await handler(StreamMessage(eventType="gmail.start", payload={"orgId": "o1"}))
            assert result is False


# ===================================================================
# create_record_message_handler
# ===================================================================

class TestCreateRecordMessageHandlerCoverage:

    @pytest.mark.asyncio
    async def test_success(self):
        container = _make_app_container()
        mock_event_processor = AsyncMock()
        container._event_processor = mock_event_processor
        container.config_service.return_value = AsyncMock()

        with patch("app.services.messaging.kafka.utils.utils.RecordEventHandler") as MockHandler:
            mock_handler = AsyncMock()

            async def mock_process(event_type, payload):
                yield {"event": "parsing_complete", "data": {}}
                yield {"event": "indexing_complete", "data": {}}

            mock_handler.process_event = mock_process
            MockHandler.return_value = mock_handler

            handler = await KafkaUtils.create_record_message_handler(container)
            events = []
            async for event in handler(StreamMessage(eventType="recordCreated", payload={"id": "r1"})):
                events.append(event)
            assert len(events) == 2
            assert events[0]["event"] == "parsing_complete"
            assert events[1]["event"] == "indexing_complete"

    @pytest.mark.asyncio
    async def test_none_message_raises(self):
        container = _make_app_container()
        container._event_processor = AsyncMock()
        container.config_service.return_value = AsyncMock()

        with patch("app.services.messaging.kafka.utils.utils.RecordEventHandler"):
            handler = await KafkaUtils.create_record_message_handler(container)
            with pytest.raises((TypeError, AttributeError)):
                async for _ in handler(None):
                    pass

    @pytest.mark.asyncio
    async def test_missing_event_type(self):
        container = _make_app_container()
        container._event_processor = AsyncMock()
        container.config_service.return_value = AsyncMock()

        with patch("app.services.messaging.kafka.utils.utils.RecordEventHandler"):
            handler = await KafkaUtils.create_record_message_handler(container)
            events = []
            async for event in handler(StreamMessage(eventType="", payload={"id": "r1"})):
                events.append(event)
            assert len(events) == 0

    @pytest.mark.asyncio
    async def test_missing_payload(self):
        container = _make_app_container()
        container._event_processor = AsyncMock()
        container.config_service.return_value = AsyncMock()

        with patch("app.services.messaging.kafka.utils.utils.RecordEventHandler"):
            handler = await KafkaUtils.create_record_message_handler(container)
            events = []
            async for event in handler(StreamMessage(eventType="recordCreated", payload={})):
                events.append(event)
            assert len(events) == 0

    @pytest.mark.asyncio
    async def test_exception_raises(self):
        container = _make_app_container()
        container._event_processor = AsyncMock()
        container.config_service.return_value = AsyncMock()

        with patch("app.services.messaging.kafka.utils.utils.RecordEventHandler") as MockHandler:
            mock_handler = AsyncMock()

            async def mock_process(event_type, payload):
                raise RuntimeError("processing error")
                yield  # noqa - needed for generator

            mock_handler.process_event = mock_process
            MockHandler.return_value = mock_handler

            handler = await KafkaUtils.create_record_message_handler(container)
            with pytest.raises(RuntimeError, match="processing error"):
                async for _ in handler(StreamMessage(eventType="recordCreated", payload={"id": "r1"})):
                    pass

    @pytest.mark.asyncio
    async def test_uses_cached_event_processor(self):
        container = _make_app_container()
        cached_processor = AsyncMock()
        container._event_processor = cached_processor
        container.config_service.return_value = AsyncMock()

        with patch("app.services.messaging.kafka.utils.utils.RecordEventHandler") as MockHandler:
            MockHandler.return_value = AsyncMock()
            await KafkaUtils.create_record_message_handler(container)
            MockHandler.assert_called_once()
            call_kwargs = MockHandler.call_args[1]
            assert call_kwargs["event_processor"] is cached_processor

    @pytest.mark.asyncio
    async def test_resolves_event_processor_when_not_cached(self):
        container = _make_app_container()
        if hasattr(container, '_event_processor'):
            delattr(container, '_event_processor')
        resolved_processor = AsyncMock()
        container.event_processor = AsyncMock(return_value=resolved_processor)
        container.config_service.return_value = AsyncMock()

        with patch("app.services.messaging.kafka.utils.utils.RecordEventHandler") as MockHandler:
            MockHandler.return_value = AsyncMock()
            await KafkaUtils.create_record_message_handler(container)
            container.event_processor.assert_awaited_once()


# ===================================================================
# create_aiconfig_message_handler
# ===================================================================

class TestCreateAiConfigMessageHandlerCoverage:

    @pytest.mark.asyncio
    async def test_llm_configured_event(self):
        container = _make_app_container()
        mock_retrieval = AsyncMock()
        container.retrieval_service = AsyncMock(return_value=mock_retrieval)

        with patch("app.services.messaging.kafka.utils.utils.AiConfigEventService") as MockSvc:
            mock_svc = AsyncMock()
            mock_svc.process_event = AsyncMock(return_value=True)
            MockSvc.return_value = mock_svc

            handler = await KafkaUtils.create_aiconfig_message_handler(container)
            result = await handler(StreamMessage(eventType="llmConfigured", payload={"provider": "openai"}))
            assert result is True
            mock_svc.process_event.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_embedding_configured_event(self):
        container = _make_app_container()
        mock_retrieval = AsyncMock()
        container.retrieval_service = AsyncMock(return_value=mock_retrieval)

        with patch("app.services.messaging.kafka.utils.utils.AiConfigEventService") as MockSvc:
            mock_svc = AsyncMock()
            mock_svc.process_event = AsyncMock(return_value=True)
            MockSvc.return_value = mock_svc

            handler = await KafkaUtils.create_aiconfig_message_handler(container)
            result = await handler(StreamMessage(eventType="embeddingModelConfigured", payload={"model": "ada"}))
            assert result is True

    @pytest.mark.asyncio
    async def test_none_message_returns_false(self):
        container = _make_app_container()
        mock_retrieval = AsyncMock()
        container.retrieval_service = AsyncMock(return_value=mock_retrieval)

        with patch("app.services.messaging.kafka.utils.utils.AiConfigEventService"):
            handler = await KafkaUtils.create_aiconfig_message_handler(container)
            result = await handler(None)
            assert result is False

    @pytest.mark.asyncio
    async def test_missing_event_type(self):
        container = _make_app_container()
        mock_retrieval = AsyncMock()
        container.retrieval_service = AsyncMock(return_value=mock_retrieval)

        with patch("app.services.messaging.kafka.utils.utils.AiConfigEventService"):
            handler = await KafkaUtils.create_aiconfig_message_handler(container)
            result = await handler(StreamMessage(eventType="", payload={}))
            assert result is False

    @pytest.mark.asyncio
    async def test_non_ai_config_event_skipped(self):
        container = _make_app_container()
        mock_retrieval = AsyncMock()
        container.retrieval_service = AsyncMock(return_value=mock_retrieval)

        with patch("app.services.messaging.kafka.utils.utils.AiConfigEventService") as MockSvc:
            mock_svc = AsyncMock()
            MockSvc.return_value = mock_svc

            handler = await KafkaUtils.create_aiconfig_message_handler(container)
            result = await handler(StreamMessage(eventType="userAdded", payload={}))
            assert result is True
            mock_svc.process_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        container = _make_app_container()
        mock_retrieval = AsyncMock()
        container.retrieval_service = AsyncMock(return_value=mock_retrieval)

        with patch("app.services.messaging.kafka.utils.utils.AiConfigEventService") as MockSvc:
            mock_svc = AsyncMock()
            mock_svc.process_event = AsyncMock(side_effect=Exception("error"))
            MockSvc.return_value = mock_svc

            handler = await KafkaUtils.create_aiconfig_message_handler(container)
            result = await handler(StreamMessage(eventType="llmConfigured", payload={}))
            assert result is False
