"""
Tests for messaging config:
  - get_message_broker_type (env var, defaults, validation)
  - RedisStreamsConfig (Pydantic model defaults)
  - REQUIRED_TOPICS constant
"""

import pytest

from app.services.messaging.config import (
    REQUIRED_TOPICS,
    MessageBrokerType,
    RedisStreamsConfig,
    Topic,
    get_message_broker_type,
)


class TestGetMessageBrokerType:
    def test_defaults_to_kafka(self, monkeypatch):
        monkeypatch.delenv("MESSAGE_BROKER", raising=False)
        assert get_message_broker_type() == MessageBrokerType.KAFKA

    def test_returns_kafka(self, monkeypatch):
        monkeypatch.setenv("MESSAGE_BROKER", "kafka")
        assert get_message_broker_type() == MessageBrokerType.KAFKA

    def test_returns_redis(self, monkeypatch):
        monkeypatch.setenv("MESSAGE_BROKER", "redis")
        assert get_message_broker_type() == MessageBrokerType.REDIS

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("MESSAGE_BROKER", "KAFKA")
        assert get_message_broker_type() == MessageBrokerType.KAFKA

        monkeypatch.setenv("MESSAGE_BROKER", "Redis")
        assert get_message_broker_type() == MessageBrokerType.REDIS

    def test_raises_for_unsupported(self, monkeypatch):
        monkeypatch.setenv("MESSAGE_BROKER", "rabbitmq")
        with pytest.raises(ValueError, match="Unsupported MESSAGE_BROKER type"):
            get_message_broker_type()


class TestRedisStreamsConfig:
    def test_defaults(self):
        config = RedisStreamsConfig()
        assert config.host == "localhost"
        assert config.port == 6379
        assert config.password is None
        assert config.db == 0
        assert config.max_len == 500000
        assert config.block_ms == 2000
        assert config.client_id == "pipeshub"
        assert config.group_id == "default_group"
        assert config.topics == []

    def test_custom_values(self):
        config = RedisStreamsConfig(
            host="redis.prod",
            port=6380,
            password="secret",
            db=2,
            max_len=50000,
            block_ms=5000,
            client_id="my-app",
            group_id="my-group",
            topics=["topic-a", "topic-b"],
        )
        assert config.host == "redis.prod"
        assert config.port == 6380
        assert config.password == "secret"
        assert config.db == 2
        assert config.max_len == 50000
        assert config.block_ms == 5000
        assert config.client_id == "my-app"
        assert config.group_id == "my-group"
        assert config.topics == ["topic-a", "topic-b"]


class TestRequiredTopics:
    def test_has_expected_topics(self):
        assert isinstance(REQUIRED_TOPICS, list)
        assert Topic.RECORD_EVENTS.value in REQUIRED_TOPICS
        assert Topic.ENTITY_EVENTS.value in REQUIRED_TOPICS
        assert Topic.AI_CONFIG_EVENTS.value in REQUIRED_TOPICS
        assert Topic.SYNC_EVENTS.value in REQUIRED_TOPICS
        assert Topic.HEALTH_CHECK.value in REQUIRED_TOPICS

    def test_has_at_least_five_topics(self):
        assert len(REQUIRED_TOPICS) >= 5


class TestMessagingEnvConfig:
    """Test new unified messaging configuration properties."""

    def test_max_concurrent_parsing_honors_numeric_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.services.messaging.config import messaging_env

        monkeypatch.setenv("MAX_CONCURRENT_PARSING", "3")

        # heavy_slots pinned to 3, light_slots = 3 * LIGHT_TO_HEAVY_RATIO (4) = 12
        assert messaging_env.max_concurrent_parsing == 3 + 12

    def test_max_concurrent_parsing_auto_sizes_from_cpu(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.services.messaging.config import messaging_env

        monkeypatch.setenv("MAX_CONCURRENT_PARSING", "auto")
        monkeypatch.setattr("os.cpu_count", lambda: 2)
        monkeypatch.setattr(
            "app.services.messaging.config.get_memory_limit_bytes", lambda: None
        )

        # No memory signal -> CPU-only sizing: heavy=clamp(2,1,4)=2, light=clamp(2*2,4,16)=4
        assert messaging_env.max_concurrent_parsing == 2 + 4

    def test_max_delivery_attempts_default(self, monkeypatch):
        """Test max_delivery_attempts defaults to 3."""
        from app.services.messaging.config import messaging_env
        
        monkeypatch.delenv("MAX_DELIVERY_ATTEMPTS", raising=False)
        assert messaging_env.max_delivery_attempts == 3

    def test_max_delivery_attempts_from_env(self, monkeypatch):
        """Test max_delivery_attempts can be overridden via env var."""
        from app.services.messaging.config import messaging_env
        
        monkeypatch.setenv("MAX_DELIVERY_ATTEMPTS", "5")
        assert messaging_env.max_delivery_attempts == 5

    def test_message_batch_size_simple_default(self, monkeypatch):
        """Test message_batch_size_simple defaults to 10."""
        from app.services.messaging.config import messaging_env
        
        monkeypatch.delenv("MESSAGE_BATCH_SIZE_SIMPLE", raising=False)
        assert messaging_env.message_batch_size_simple == 10

    def test_message_batch_size_simple_from_env(self, monkeypatch):
        """Test message_batch_size_simple can be overridden."""
        from app.services.messaging.config import messaging_env
        
        monkeypatch.setenv("MESSAGE_BATCH_SIZE_SIMPLE", "20")
        assert messaging_env.message_batch_size_simple == 20

    def test_message_batch_size_indexing_default(self, monkeypatch):
        """Test message_batch_size_indexing defaults to 1."""
        from app.services.messaging.config import messaging_env
        
        monkeypatch.delenv("MESSAGE_BATCH_SIZE_INDEXING", raising=False)
        assert messaging_env.message_batch_size_indexing == 1

    def test_message_batch_size_indexing_from_env(self, monkeypatch):
        """Test message_batch_size_indexing can be overridden."""
        from app.services.messaging.config import messaging_env
        
        monkeypatch.setenv("MESSAGE_BATCH_SIZE_INDEXING", "5")
        assert messaging_env.message_batch_size_indexing == 5

    def test_message_timeout_ms_default(self, monkeypatch):
        """Test message_timeout_ms defaults to 2000."""
        from app.services.messaging.config import messaging_env
        
        monkeypatch.delenv("MESSAGE_TIMEOUT_MS", raising=False)
        assert messaging_env.message_timeout_ms == 2000

    def test_message_timeout_ms_from_env(self, monkeypatch):
        """Test message_timeout_ms can be overridden."""
        from app.services.messaging.config import messaging_env
        
        monkeypatch.setenv("MESSAGE_TIMEOUT_MS", "5000")
        assert messaging_env.message_timeout_ms == 5000
