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
    messaging_env,
)


class TestGetMessageBrokerType:
    def test_defaults_to_redis(self, monkeypatch):
        monkeypatch.delenv("MESSAGE_BROKER", raising=False)
        assert get_message_broker_type() == MessageBrokerType.REDIS

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
        """Test message_batch_size_indexing defaults to 10 (plan: "Fix 6 —
        MESSAGE_BATCH_SIZE_INDEXING Default Increase"). Reading one message
        per consumer-loop iteration added a full round-trip of latency
        between every task spawn even though pending_task_ceiling already
        bounds in-flight tasks, so a bigger batch cannot cause overcommit."""
        from app.services.messaging.config import messaging_env

        monkeypatch.delenv("MESSAGE_BATCH_SIZE_INDEXING", raising=False)
        assert messaging_env.message_batch_size_indexing == 10

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


class TestConcurrencyCeilingEnvVars:
    """Phase 6: MAX_CONCURRENT_PARSING/INDEXING and MAX_PENDING_INDEXING_TASKS
    ship in compose/helm as ``${VAR:-}`` (present but empty) so the
    ResourceGovernor derives ceilings from cgroup/CPU limits by default.
    These properties must treat "unset" and "empty string" identically
    rather than raising on ``int("")``."""

    def test_max_concurrent_parsing_defaults_when_unset(self, monkeypatch):
        from app.services.messaging.config import messaging_env

        monkeypatch.delenv("MAX_CONCURRENT_PARSING", raising=False)
        assert messaging_env.max_concurrent_parsing == 5

    def test_max_concurrent_parsing_defaults_when_empty(self, monkeypatch):
        """Compose's ``${MAX_CONCURRENT_PARSING:-}`` sets the var to ``\"\"``
        (present, not absent) when the operator hasn't pinned a value."""
        from app.services.messaging.config import messaging_env

        monkeypatch.setenv("MAX_CONCURRENT_PARSING", "")
        assert messaging_env.max_concurrent_parsing == 5

    def test_max_concurrent_parsing_respects_explicit_value(self, monkeypatch):
        from app.services.messaging.config import messaging_env

        monkeypatch.setenv("MAX_CONCURRENT_PARSING", "12")
        assert messaging_env.max_concurrent_parsing == 12

    def test_max_concurrent_indexing_defaults_when_unset(self, monkeypatch):
        from app.services.messaging.config import messaging_env

        monkeypatch.delenv("MAX_CONCURRENT_INDEXING", raising=False)
        assert messaging_env.max_concurrent_indexing == 7

    def test_max_concurrent_indexing_defaults_when_empty(self, monkeypatch):
        from app.services.messaging.config import messaging_env

        monkeypatch.setenv("MAX_CONCURRENT_INDEXING", "")
        assert messaging_env.max_concurrent_indexing == 7

    def test_max_concurrent_indexing_respects_explicit_value(self, monkeypatch):
        from app.services.messaging.config import messaging_env

        monkeypatch.setenv("MAX_CONCURRENT_INDEXING", "20")
        assert messaging_env.max_concurrent_indexing == 20

    def test_env_max_concurrent_parsing_none_when_unset_or_empty(self, monkeypatch):
        from app.services.messaging.config import messaging_env

        monkeypatch.delenv("MAX_CONCURRENT_PARSING", raising=False)
        assert messaging_env.env_max_concurrent_parsing is None

        monkeypatch.setenv("MAX_CONCURRENT_PARSING", "")
        assert messaging_env.env_max_concurrent_parsing is None

        monkeypatch.setenv("MAX_CONCURRENT_PARSING", "9")
        assert messaging_env.env_max_concurrent_parsing == 9

    def test_env_max_concurrent_indexing_none_when_unset_or_empty(self, monkeypatch):
        from app.services.messaging.config import messaging_env

        monkeypatch.delenv("MAX_CONCURRENT_INDEXING", raising=False)
        assert messaging_env.env_max_concurrent_indexing is None

        monkeypatch.setenv("MAX_CONCURRENT_INDEXING", "")
        assert messaging_env.env_max_concurrent_indexing is None

        monkeypatch.setenv("MAX_CONCURRENT_INDEXING", "11")
        assert messaging_env.env_max_concurrent_indexing == 11

    def test_max_pending_indexing_tasks_derives_when_unset(self, monkeypatch):
        from app.services.messaging.config import messaging_env

        monkeypatch.delenv("MAX_PENDING_INDEXING_TASKS", raising=False)
        monkeypatch.delenv("MAX_CONCURRENT_PARSING", raising=False)
        monkeypatch.delenv("MAX_CONCURRENT_INDEXING", raising=False)
        assert messaging_env.max_pending_indexing_tasks == max(5, 7) * 4

    def test_max_pending_indexing_tasks_derives_when_empty(self, monkeypatch):
        """Same empty-string-as-unset handling as the ceilings above."""
        from app.services.messaging.config import messaging_env

        monkeypatch.setenv("MAX_PENDING_INDEXING_TASKS", "")
        monkeypatch.delenv("MAX_CONCURRENT_PARSING", raising=False)
        monkeypatch.delenv("MAX_CONCURRENT_INDEXING", raising=False)
        assert messaging_env.max_pending_indexing_tasks == max(5, 7) * 4

    def test_max_pending_indexing_tasks_respects_explicit_value(self, monkeypatch):
        from app.services.messaging.config import messaging_env

        monkeypatch.setenv("MAX_PENDING_INDEXING_TASKS", "17")
        assert messaging_env.max_pending_indexing_tasks == 17

class TestEmptyEnvVarsFallBackToDefaults:
    """The shipped Compose files pass every optional knob as ``${VAR:-}``, so
    an unset one arrives as the empty string rather than absent. A bare
    ``int(os.getenv(name, default))`` raises on that at import and takes the
    whole service down — the "Hub slim still int()s empty strings" hazard
    documented in docker-compose.yml."""

    @pytest.mark.parametrize(
        ("env_var", "attribute", "expected"),
        [
            ("INDEXING_CONCURRENCY_REDIS_MAX_CONNECTIONS", "concurrency_redis_max_connections", 32),
            ("INDEXING_CONCURRENCY_REDIS_TIMEOUT_SECONDS", "concurrency_redis_timeout_seconds", 2.0),
            ("INDEXING_CONCURRENCY_FAILURE_BUDGET", "concurrency_failure_budget", 5),
            ("INDEXING_CONCURRENCY_ACQUIRE_MAX_BACKOFF_SECONDS", "concurrency_acquire_max_backoff_seconds", 5.0),
            ("INDEXING_CONCURRENCY_LEASE_SECONDS", "concurrency_lease_seconds", 120.0),
            ("INDEXING_CONCURRENCY_RENEW_INTERVAL_SECONDS", "concurrency_renew_interval_seconds", 30.0),
            ("INDEXING_CONCURRENCY_ACQUIRE_POLL_SECONDS", "concurrency_acquire_poll_seconds", 0.5),
            ("INDEXING_RECORD_LEASE_WAIT_SECONDS", "record_lease_wait_seconds", 10.0),
            ("MAX_DELIVERY_ATTEMPTS", "max_delivery_attempts", 3),
            ("RECORD_PROCESSING_TIMEOUT", "record_processing_timeout", 1800.0),
            ("SHUTDOWN_TASK_TIMEOUT", "shutdown_task_timeout", 240.0),
        ],
    )
    def test_empty_string_is_treated_as_unset(
        self, monkeypatch: pytest.MonkeyPatch, env_var: str, attribute: str, expected: float
    ) -> None:
        monkeypatch.setenv(env_var, "")
        assert getattr(messaging_env, attribute) == expected

    @pytest.mark.parametrize("raw", ["nan", "inf", "-inf", "-5", "-0.1"])
    @pytest.mark.parametrize(
        ("env_var", "attribute", "expected"),
        [
            ("INDEXING_CONCURRENCY_LEASE_SECONDS", "concurrency_lease_seconds", 120.0),
            ("INDEXING_CONCURRENCY_ACQUIRE_POLL_SECONDS", "concurrency_acquire_poll_seconds", 0.5),
            ("INDEXING_RECORD_LEASE_WAIT_SECONDS", "record_lease_wait_seconds", 10.0),
        ],
    )
    def test_non_finite_and_negative_durations_fall_back(
        self,
        monkeypatch: pytest.MonkeyPatch,
        raw: str,
        env_var: str,
        attribute: str,
        expected: float,
    ) -> None:
        """``float()`` accepts nan/inf, and both reach ``int(seconds * 1000)``
        in the lease calls, where they raise — indistinguishable from a Redis
        failure to ``acquire_distributed_slot``, so a typo degrades as an
        outage. A negative one is quieter and worse: the backoff returns a
        negative delay and ``asyncio.sleep`` of that returns immediately,
        spinning against the Redis it is backing off from."""
        monkeypatch.setenv(env_var, raw)
        assert getattr(messaging_env, attribute) == expected

    @pytest.mark.parametrize(
        ("env_var", "attribute", "expected"),
        [
            ("INDEXING_CONCURRENCY_REDIS_MAX_CONNECTIONS", "concurrency_redis_max_connections", 32),
            ("MAX_DELIVERY_ATTEMPTS", "max_delivery_attempts", 3),
            ("RECORD_PROCESSING_TIMEOUT", "record_processing_timeout", 1800.0),
        ],
    )
    def test_malformed_value_falls_back_rather_than_raising(
        self, monkeypatch: pytest.MonkeyPatch, env_var: str, attribute: str, expected: float
    ) -> None:
        """A typo must degrade to the default, not crash the service at import."""
        monkeypatch.setenv(env_var, "not-a-number")
        assert getattr(messaging_env, attribute) == expected
