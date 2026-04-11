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
        assert config.max_len == 10000
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
        assert Topic.SYNC_EVENTS.value in REQUIRED_TOPICS
        assert Topic.HEALTH_CHECK.value in REQUIRED_TOPICS

    def test_has_at_least_four_topics(self):
        assert len(REQUIRED_TOPICS) >= 4
