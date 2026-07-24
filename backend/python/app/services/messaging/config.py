import os
from collections.abc import AsyncGenerator, Awaitable, Callable
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, JsonValue


class MessageBrokerType(str, Enum):
    """Supported message broker backends."""

    KAFKA = "kafka"
    REDIS = "redis"


class ConsumerType(str, Enum):
    """Consumer type variants."""

    SIMPLE = "simple"
    INDEXING = "indexing"


class Topic(str, Enum):
    """Well-known messaging topics."""

    RECORD_EVENTS = "record-events"
    ENTITY_EVENTS = "entity-events"
    AI_CONFIG_EVENTS = "ai-config-events"
    SYNC_EVENTS = "sync-events"
    HEALTH_CHECK = "health-check"
    NOTIFICATION = "notification"


REQUIRED_TOPICS: list[str] = [t.value for t in Topic]


class IndexingEvent(str, Enum):
    """Events emitted during the indexing pipeline."""

    # Handler has written IN_PROGRESS and needs the nested parse slot.
    START_PARSING = "start_parsing"
    PARSING_COMPLETE = "parsing_complete"
    INDEXING_COMPLETE = "indexing_complete"
    DOCLING_FAILED = "docling_failed"


# ---------------------------------------------------------------------------
# Message models
# ---------------------------------------------------------------------------


class StreamMessage(BaseModel):
    """Incoming message envelope consumed by handlers."""

    eventType: str
    payload: dict[str, JsonValue]
    timestamp: Optional[int] = None
    # Trace id propagated from the producer; optional so legacy messages parse.
    requestId: Optional[str] = None
    is_final_failure: Optional[bool] = None  # Set by consumer: True = will commit/dead-letter, False = will retry


class PipelineEventData(BaseModel):
    """Data yielded alongside a pipeline event."""

    record_id: Optional[str] = None
    record_name: Optional[str] = None
    count: Optional[int] = None


class PipelineEvent(BaseModel):
    """Event yielded by the indexing pipeline handler."""

    event: IndexingEvent
    data: Optional[PipelineEventData] = None


# ---------------------------------------------------------------------------
# Handler type aliases
# ---------------------------------------------------------------------------

MessageHandler = Callable[[StreamMessage], Awaitable[bool]]
IndexingMessageHandler = Callable[[StreamMessage], AsyncGenerator[PipelineEvent, None]]


# ---------------------------------------------------------------------------
# Environment-driven configuration
# ---------------------------------------------------------------------------


class MessagingEnvConfig:
    """Reads messaging-related environment variables lazily.

    Each property reads ``os.getenv`` on every access so that tests can
    patch ``os.environ`` between calls without stale cached values.
    """

    @property
    def message_broker_type(self) -> MessageBrokerType:
        raw = os.getenv("MESSAGE_BROKER", MessageBrokerType.KAFKA.value).lower()
        try:
            return MessageBrokerType(raw)
        except ValueError:
            valid = ", ".join(f"'{m.value}'" for m in MessageBrokerType)
            raise ValueError(  # noqa: B904
                f"Unsupported MESSAGE_BROKER type: {raw}. Must be one of {valid}."
            )

    @property
    def redis_streams_maxlen(self) -> int:
        return int(os.getenv("REDIS_STREAMS_MAXLEN", "500000"))

    @property
    def max_concurrent_parsing(self) -> int:
        return int(os.getenv("MAX_CONCURRENT_PARSING", "5"))

    @property
    def max_concurrent_indexing(self) -> int:
        return int(os.getenv("MAX_CONCURRENT_INDEXING", "10"))

    @property
    def distributed_concurrency_enabled(self) -> bool:
        return os.getenv("DISTRIBUTED_INDEXING_CONCURRENCY", "true").lower() == "true"

    @property
    def concurrency_key_prefix(self) -> str:
        return os.getenv(
            "INDEXING_CONCURRENCY_KEY_PREFIX",
            "pipeshub:indexing:concurrency",
        )

    @property
    def concurrency_lease_seconds(self) -> float:
        return float(os.getenv("INDEXING_CONCURRENCY_LEASE_SECONDS", "120"))

    @property
    def concurrency_renew_interval_seconds(self) -> float:
        return float(os.getenv("INDEXING_CONCURRENCY_RENEW_INTERVAL_SECONDS", "30"))

    @property
    def concurrency_acquire_poll_seconds(self) -> float:
        return float(os.getenv("INDEXING_CONCURRENCY_ACQUIRE_POLL_SECONDS", "0.5"))

    @property
    def record_lease_wait_seconds(self) -> float:
        """Bounded wait for the per-record lease before giving up.

        This lease is only contended by *duplicate* in-flight deliveries of
        the same record (a different, unrelated record never competes for
        it), and the task already holds an outer indexing slot/semaphore
        while waiting. An unbounded wait here convoys the whole pipeline if
        several duplicates of one record arrive together; a short bounded
        wait is enough since whoever already holds the lease is actively
        processing that same record.
        """
        return float(os.getenv("INDEXING_RECORD_LEASE_WAIT_SECONDS", "10"))

    @property
    def concurrency_redis_timeout_seconds(self) -> float:
        return float(os.getenv("INDEXING_CONCURRENCY_REDIS_TIMEOUT_SECONDS", "5"))

    @property
    def shutdown_task_timeout(self) -> float:
        return float(os.getenv("SHUTDOWN_TASK_TIMEOUT", "240.0"))

    @property
    def max_delivery_attempts(self) -> int:
        """Max times a message can be delivered before being dead-lettered (ACK-ed and discarded)."""
        return int(os.getenv("MAX_DELIVERY_ATTEMPTS", "3"))

    @property
    def message_batch_size_simple(self) -> int:
        """Batch size for simple consumers (entity/sync events)."""
        return int(os.getenv("MESSAGE_BATCH_SIZE_SIMPLE", "10"))

    @property
    def message_batch_size_indexing(self) -> int:
        """Batch size for indexing consumers (record events)."""
        return int(os.getenv("MESSAGE_BATCH_SIZE_INDEXING", "1"))

    @property
    def message_timeout_ms(self) -> int:
        """Block timeout for reading messages (milliseconds)."""
        return int(os.getenv("MESSAGE_TIMEOUT_MS", "2000"))

    @property
    def record_processing_timeout(self) -> float:
        """Max seconds a single record is allowed to process before being timed out."""
        return float(os.getenv("RECORD_PROCESSING_TIMEOUT", "1800"))

    @property
    def max_pending_indexing_tasks(self) -> int:
        return int(
            os.getenv(
                "MAX_PENDING_INDEXING_TASKS",
                str(max(self.max_concurrent_parsing, self.max_concurrent_indexing) * 4),
            )
        )

    @property
    def stale_recovery_interval_seconds(self) -> float:
        return float(os.getenv("STALE_INDEXING_RECOVERY_INTERVAL_SECONDS", "60"))

    @property
    def stale_recovery_startup_grace_seconds(self) -> float:
        default = self.shutdown_task_timeout + 90
        return float(
            os.getenv(
                "STALE_INDEXING_RECOVERY_STARTUP_GRACE_SECONDS",
                str(default),
            )
        )

    @property
    def stale_recovery_after_seconds(self) -> float:
        default = self.record_processing_timeout + self.concurrency_lease_seconds
        return float(os.getenv("STALE_INDEXING_RECOVERY_AFTER_SECONDS", str(default)))

    @property
    def stale_recovery_page_size(self) -> int:
        return int(os.getenv("STALE_INDEXING_RECOVERY_PAGE_SIZE", "100"))


messaging_env = MessagingEnvConfig()


def get_message_broker_type() -> MessageBrokerType:
    """Convenience wrapper around ``messaging_env.message_broker_type``."""
    return messaging_env.message_broker_type


# ---------------------------------------------------------------------------
# Retry backoff (shared by the Kafka and Redis Streams indexing consumers)
# ---------------------------------------------------------------------------

# Backoff applied to a re-queued (retried) message, stamped as an absolute
# "not before" timestamp so the delay can be honored on the consume side
# (before any semaphore is acquired) instead of held during re-queue.
RETRY_BACKOFF_BASE_SECONDS = 15.0
RETRY_BACKOFF_FACTOR = 4.0
RETRY_BACKOFF_MAX_SECONDS = 300.0


def compute_retry_backoff_seconds(retry_count: int) -> float:
    """Exponential backoff for a re-queued message: ~15s, 60s, 240s (capped at 300s)."""
    delay = RETRY_BACKOFF_BASE_SECONDS * (RETRY_BACKOFF_FACTOR ** max(retry_count - 1, 0))
    return min(delay, RETRY_BACKOFF_MAX_SECONDS)


# ---------------------------------------------------------------------------
# Connection models
# ---------------------------------------------------------------------------


class RedisConfig(BaseModel):
    """Base Redis connection configuration."""

    host: str = "localhost"
    port: int = 6379
    password: Optional[str] = None
    db: int = 0


class RedisStreamsConfig(RedisConfig):
    """Redis Streams configuration (extends RedisConfig)."""

    max_len: int = Field(default=500000, description="Max stream length for XADD")
    block_ms: int = Field(default=2000, description="XREADGROUP block timeout in ms")
    batch_size: int = Field(
        default=1,
        description="Messages per XREADGROUP call (default 1 for indexing; overridden to 10 for simple consumers)"
    )
    claim_min_idle_ms: int = Field(
        default=30000,
        description="Min idle time in ms before XAUTOCLAIM can steal a pending message",
    )
    client_id: str = "pipeshub"
    group_id: str = "default_group"
    topics: list[str] = Field(default_factory=list)
