"""Tests for app.services.messaging.utils.MessagingUtils.

Covers uncovered lines: 38, 43-44, 71-82, 101-108, 117-133, 158-159, 167, 178, 189, 200.

Methods tested:
- get_broker_type()
- _get_redis_config()
- _build_redis_streams_config()
- _create_kafka_consumer_config() (success, missing config, missing brokers)
- create_consumer_config() (kafka path, redis path)
- create_producer_config() (kafka path, redis path, missing kafka config)
- create_producer_config_from_service() (kafka path, redis path)
- create_entity_consumer_config()
- create_sync_consumer_config()
- create_record_consumer_config()
- create_aiconfig_consumer_config()
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.messaging.config import (
    MessageBrokerType,
    RedisConfig,
    RedisStreamsConfig,
    Topic,
)
from app.services.messaging.kafka.config.kafka_config import (
    KafkaConsumerConfig,
    KafkaProducerConfig,
)
from app.services.messaging.utils import MessagingUtils


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_redis_config() -> RedisConfig:
    return RedisConfig(host="redis-host", port=6380, password="secret", db=2)


def _make_app_container(
    kafka_config=None,
    redis_config=None,
):
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

    if redis_config is None:
        redis_config = _make_redis_config()
    config_service.get_redis_config = AsyncMock(return_value=redis_config)

    return container


# ===================================================================
# get_broker_type  (line 38)
# ===================================================================


class TestGetBrokerType:

    @patch("app.services.messaging.utils.get_message_broker_type",
           return_value=MessageBrokerType.KAFKA)
    def test_returns_kafka(self, mock_fn):
        assert MessagingUtils.get_broker_type() == MessageBrokerType.KAFKA
        mock_fn.assert_called_once()

    @patch("app.services.messaging.utils.get_message_broker_type",
           return_value=MessageBrokerType.REDIS)
    def test_returns_redis(self, mock_fn):
        assert MessagingUtils.get_broker_type() == MessageBrokerType.REDIS


# ===================================================================
# _get_redis_config  (lines 43-44)
# ===================================================================


class TestGetRedisConfig:

    @pytest.mark.asyncio
    async def test_returns_redis_config_from_container(self):
        expected = _make_redis_config()
        container = _make_app_container(redis_config=expected)
        result = await MessagingUtils._get_redis_config(container)
        assert result is expected
        container.config_service.assert_called_once()
        container.config_service.return_value.get_redis_config.assert_awaited_once()


# ===================================================================
# _build_redis_streams_config
# ===================================================================


class TestBuildRedisStreamsConfig:

    @patch("app.services.messaging.utils.messaging_env")
    def test_builds_config(self, mock_env):
        mock_env.redis_streams_maxlen = 5000
        redis_cfg = _make_redis_config()
        result = MessagingUtils._build_redis_streams_config(
            redis_cfg, "my-client", "my-group", ["topic-a", "topic-b"]
        )
        assert isinstance(result, RedisStreamsConfig)
        assert result.host == "redis-host"
        assert result.port == 6380
        assert result.password == "secret"
        assert result.db == 2
        assert result.max_len == 5000
        assert result.client_id == "my-client"
        assert result.group_id == "my-group"
        assert result.topics == ["topic-a", "topic-b"]


# ===================================================================
# _create_kafka_consumer_config  (lines 71-82)
# ===================================================================


class TestCreateKafkaConsumerConfig:

    @pytest.mark.asyncio
    async def test_success(self):
        container = _make_app_container()
        config = await MessagingUtils._create_kafka_consumer_config(
            container, "client-1", "group-1", ["topic-1"]
        )
        assert isinstance(config, KafkaConsumerConfig)
        assert config.client_id == "client-1"
        assert config.group_id == "group-1"
        assert config.topics == ["topic-1"]
        assert config.bootstrap_servers == ["broker1:9092", "broker2:9092"]
        assert config.auto_offset_reset == "earliest"
        assert config.enable_auto_commit is True
        assert config.ssl is False
        assert config.sasl is None

    @pytest.mark.asyncio
    async def test_with_ssl_and_sasl(self):
        kafka_config = {
            "brokers": ["broker:9093"],
            "ssl": True,
            "sasl": {"username": "user", "password": "pass", "mechanism": "PLAIN"},
        }
        container = _make_app_container(kafka_config=kafka_config)
        config = await MessagingUtils._create_kafka_consumer_config(
            container, "c", "g", ["t"]
        )
        assert config.ssl is True
        assert config.sasl == {"username": "user", "password": "pass", "mechanism": "PLAIN"}

    @pytest.mark.asyncio
    async def test_missing_kafka_config_raises(self):
        container = _make_app_container()
        container.config_service.return_value.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Kafka configuration not found"):
            await MessagingUtils._create_kafka_consumer_config(
                container, "c", "g", ["t"]
            )

    @pytest.mark.asyncio
    async def test_missing_brokers_raises(self):
        container = _make_app_container(kafka_config={"ssl": False})
        with pytest.raises(ValueError, match="Kafka brokers not found"):
            await MessagingUtils._create_kafka_consumer_config(
                container, "c", "g", ["t"]
            )


# ===================================================================
# create_consumer_config  (lines 101-108)
# ===================================================================


class TestCreateConsumerConfig:

    @pytest.mark.asyncio
    @patch("app.services.messaging.utils.get_message_broker_type",
           return_value=MessageBrokerType.KAFKA)
    async def test_kafka_path(self, _mock_broker):
        container = _make_app_container()
        config = await MessagingUtils.create_consumer_config(
            container, "c", "g", ["t"]
        )
        assert isinstance(config, KafkaConsumerConfig)
        assert config.client_id == "c"

    @pytest.mark.asyncio
    @patch("app.services.messaging.utils.get_message_broker_type",
           return_value=MessageBrokerType.REDIS)
    @patch("app.services.messaging.utils.messaging_env")
    async def test_redis_path(self, mock_env, _mock_broker):
        mock_env.redis_streams_maxlen = 10000
        container = _make_app_container()
        config = await MessagingUtils.create_consumer_config(
            container, "c", "g", ["t"]
        )
        assert isinstance(config, RedisStreamsConfig)
        assert config.client_id == "c"
        assert config.group_id == "g"
        assert config.topics == ["t"]
        assert config.host == "redis-host"


# ===================================================================
# create_producer_config  (lines 117-133)
# ===================================================================


class TestCreateProducerConfig:

    @pytest.mark.asyncio
    @patch("app.services.messaging.utils.get_message_broker_type",
           return_value=MessageBrokerType.KAFKA)
    async def test_kafka_path(self, _mock_broker):
        container = _make_app_container()
        config = await MessagingUtils.create_producer_config(container)
        assert isinstance(config, KafkaProducerConfig)
        assert config.client_id == "messaging_producer_client"
        assert config.bootstrap_servers == ["broker1:9092", "broker2:9092"]
        assert config.ssl is False
        assert config.sasl is None

    @pytest.mark.asyncio
    @patch("app.services.messaging.utils.get_message_broker_type",
           return_value=MessageBrokerType.KAFKA)
    async def test_kafka_path_missing_config_raises(self, _mock_broker):
        container = _make_app_container()
        container.config_service.return_value.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Kafka configuration not found"):
            await MessagingUtils.create_producer_config(container)

    @pytest.mark.asyncio
    @patch("app.services.messaging.utils.get_message_broker_type",
           return_value=MessageBrokerType.KAFKA)
    async def test_kafka_with_ssl_sasl(self, _mock_broker):
        kafka_config = {
            "brokers": ["broker:9093"],
            "ssl": True,
            "sasl": {"username": "u", "password": "p"},
        }
        container = _make_app_container(kafka_config=kafka_config)
        config = await MessagingUtils.create_producer_config(container)
        assert config.ssl is True
        assert config.sasl == {"username": "u", "password": "p"}

    @pytest.mark.asyncio
    @patch("app.services.messaging.utils.get_message_broker_type",
           return_value=MessageBrokerType.REDIS)
    @patch("app.services.messaging.utils.messaging_env")
    async def test_redis_path(self, mock_env, _mock_broker):
        mock_env.redis_streams_maxlen = 10000
        container = _make_app_container()
        config = await MessagingUtils.create_producer_config(container)
        assert isinstance(config, RedisStreamsConfig)
        assert config.client_id == "messaging_producer_client"
        assert config.group_id == ""
        assert config.topics == []


# ===================================================================
# create_producer_config_from_service  (lines 158-159)
# ===================================================================


class TestCreateProducerConfigFromService:

    @pytest.mark.asyncio
    @patch("app.services.messaging.utils.get_message_broker_type",
           return_value=MessageBrokerType.KAFKA)
    async def test_kafka_path_with_brokers_list(self, _mock_broker):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "brokers": ["b1:9092", "b2:9092"],
            "ssl": False,
            "sasl": None,
        })
        config = await MessagingUtils.create_producer_config_from_service(config_service)
        assert isinstance(config, KafkaProducerConfig)
        assert config.bootstrap_servers == ["b1:9092", "b2:9092"]
        assert config.client_id == "messaging_producer_client"

    @pytest.mark.asyncio
    @patch("app.services.messaging.utils.get_message_broker_type",
           return_value=MessageBrokerType.KAFKA)
    async def test_kafka_path_with_bootstrap_servers_string(self, _mock_broker):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "bootstrap_servers": "b1:9092, b2:9092",
            "ssl": True,
            "sasl": {"username": "u", "password": "p"},
        })
        config = await MessagingUtils.create_producer_config_from_service(
            config_service, client_id="custom-client"
        )
        assert isinstance(config, KafkaProducerConfig)
        assert config.bootstrap_servers == ["b1:9092", "b2:9092"]
        assert config.client_id == "custom-client"
        assert config.ssl is True

    @pytest.mark.asyncio
    @patch("app.services.messaging.utils.get_message_broker_type",
           return_value=MessageBrokerType.REDIS)
    @patch("app.services.messaging.utils.messaging_env")
    async def test_redis_path(self, mock_env, _mock_broker):
        mock_env.redis_streams_maxlen = 10000
        redis_cfg = _make_redis_config()
        config_service = AsyncMock()
        config_service.get_redis_config = AsyncMock(return_value=redis_cfg)
        config = await MessagingUtils.create_producer_config_from_service(
            config_service, client_id="my-producer"
        )
        assert isinstance(config, RedisStreamsConfig)
        assert config.client_id == "my-producer"
        assert config.group_id == ""
        assert config.topics == []
        assert config.host == "redis-host"


# ===================================================================
# Convenience consumer config creators (lines 167, 178, 189, 200)
# ===================================================================


class TestConvenienceConsumerConfigs:

    @pytest.mark.asyncio
    @patch("app.services.messaging.utils.get_message_broker_type",
           return_value=MessageBrokerType.KAFKA)
    async def test_create_entity_consumer_config(self, _mock_broker):
        container = _make_app_container()
        config = await MessagingUtils.create_entity_consumer_config(container)
        assert isinstance(config, KafkaConsumerConfig)
        assert config.client_id == "entity_consumer_client"
        assert config.group_id == "entity_consumer_group"
        assert config.topics == [Topic.ENTITY_EVENTS.value]

    @pytest.mark.asyncio
    @patch("app.services.messaging.utils.get_message_broker_type",
           return_value=MessageBrokerType.KAFKA)
    async def test_create_sync_consumer_config(self, _mock_broker):
        container = _make_app_container()
        config = await MessagingUtils.create_sync_consumer_config(container)
        assert isinstance(config, KafkaConsumerConfig)
        assert config.client_id == "sync_consumer_client"
        assert config.group_id == "sync_consumer_group"
        assert config.topics == [Topic.SYNC_EVENTS.value]

    @pytest.mark.asyncio
    @patch("app.services.messaging.utils.get_message_broker_type",
           return_value=MessageBrokerType.KAFKA)
    async def test_create_record_consumer_config(self, _mock_broker):
        container = _make_app_container()
        config = await MessagingUtils.create_record_consumer_config(container)
        assert isinstance(config, KafkaConsumerConfig)
        assert config.client_id == "records_consumer_client"
        assert config.group_id == "records_consumer_group"
        assert config.topics == [Topic.RECORD_EVENTS.value]

    @pytest.mark.asyncio
    @patch("app.services.messaging.utils.get_message_broker_type",
           return_value=MessageBrokerType.KAFKA)
    async def test_create_aiconfig_consumer_config(self, _mock_broker):
        container = _make_app_container()
        config = await MessagingUtils.create_aiconfig_consumer_config(container)
        assert isinstance(config, KafkaConsumerConfig)
        assert config.client_id == "aiconfig_consumer_client"
        assert config.group_id == "aiconfig_consumer_group"
        assert config.topics == [Topic.AI_CONFIG_EVENTS.value]

    @pytest.mark.asyncio
    @patch("app.services.messaging.utils.get_message_broker_type",
           return_value=MessageBrokerType.REDIS)
    @patch("app.services.messaging.utils.messaging_env")
    async def test_entity_consumer_config_redis(self, mock_env, _mock_broker):
        mock_env.redis_streams_maxlen = 10000
        container = _make_app_container()
        config = await MessagingUtils.create_entity_consumer_config(container)
        assert isinstance(config, RedisStreamsConfig)
        assert config.client_id == "entity_consumer_client"
        assert config.topics == [Topic.ENTITY_EVENTS.value]

    @pytest.mark.asyncio
    @patch("app.services.messaging.utils.get_message_broker_type",
           return_value=MessageBrokerType.REDIS)
    @patch("app.services.messaging.utils.messaging_env")
    async def test_sync_consumer_config_redis(self, mock_env, _mock_broker):
        mock_env.redis_streams_maxlen = 10000
        container = _make_app_container()
        config = await MessagingUtils.create_sync_consumer_config(container)
        assert isinstance(config, RedisStreamsConfig)
        assert config.client_id == "sync_consumer_client"
        assert config.topics == [Topic.SYNC_EVENTS.value]

    @pytest.mark.asyncio
    @patch("app.services.messaging.utils.get_message_broker_type",
           return_value=MessageBrokerType.REDIS)
    @patch("app.services.messaging.utils.messaging_env")
    async def test_record_consumer_config_redis(self, mock_env, _mock_broker):
        mock_env.redis_streams_maxlen = 10000
        container = _make_app_container()
        config = await MessagingUtils.create_record_consumer_config(container)
        assert isinstance(config, RedisStreamsConfig)
        assert config.client_id == "records_consumer_client"
        assert config.topics == [Topic.RECORD_EVENTS.value]

    @pytest.mark.asyncio
    @patch("app.services.messaging.utils.get_message_broker_type",
           return_value=MessageBrokerType.REDIS)
    @patch("app.services.messaging.utils.messaging_env")
    async def test_aiconfig_consumer_config_redis(self, mock_env, _mock_broker):
        mock_env.redis_streams_maxlen = 10000
        container = _make_app_container()
        config = await MessagingUtils.create_aiconfig_consumer_config(container)
        assert isinstance(config, RedisStreamsConfig)
        assert config.client_id == "aiconfig_consumer_client"
        assert config.topics == [Topic.AI_CONFIG_EVENTS.value]
