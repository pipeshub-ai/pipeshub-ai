"""
Tests for messaging config:
  - get_message_broker_type (env var, defaults, validation)
  - RedisStreamsConfig (dataclass defaults)
  - REQUIRED_TOPICS constant
"""

import os
import logging
from unittest.mock import patch

import pytest

from app.services.messaging.config import (
    REQUIRED_TOPICS,
    RedisStreamsConfig,
    get_message_broker_type,
)


class TestGetMessageBrokerType:
    def test_defaults_to_kafka(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("MESSAGE_BROKER", None)
            assert get_message_broker_type() == "kafka"

    def test_returns_kafka(self):
        with patch.dict(os.environ, {"MESSAGE_BROKER": "kafka"}):
            assert get_message_broker_type() == "kafka"

    def test_returns_redis(self):
        with patch.dict(os.environ, {"MESSAGE_BROKER": "redis"}):
            assert get_message_broker_type() == "redis"

    def test_case_insensitive(self):
        with patch.dict(os.environ, {"MESSAGE_BROKER": "KAFKA"}):
            assert get_message_broker_type() == "kafka"

        with patch.dict(os.environ, {"MESSAGE_BROKER": "Redis"}):
            assert get_message_broker_type() == "redis"

    def test_raises_for_unsupported(self):
        with patch.dict(os.environ, {"MESSAGE_BROKER": "rabbitmq"}):
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
        assert "record-events" in REQUIRED_TOPICS
        assert "entity-events" in REQUIRED_TOPICS
        assert "sync-events" in REQUIRED_TOPICS
        assert "health-check" in REQUIRED_TOPICS

    def test_has_at_least_four_topics(self):
        assert len(REQUIRED_TOPICS) >= 4
