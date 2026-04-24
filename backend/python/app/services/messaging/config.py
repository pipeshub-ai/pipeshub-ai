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


REQUIRED_TOPICS: list[str] = [t.value for t in Topic]


class IndexingEvent(str, Enum):
    """Events emitted during the indexing pipeline."""

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


class PipelineEventData(BaseModel):
    """Data yielded alongside a pipeline event."""

    record_id: str
    count: Optional[int] = None


class PipelineEvent(BaseModel):
    """Event yielded by the indexing pipeline handler."""

    event: IndexingEvent
    data: PipelineEventData


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
        return int(os.getenv("REDIS_STREAMS_MAXLEN", "10000"))

    @property
    def max_concurrent_parsing(self) -> int:
        return int(os.getenv("MAX_CONCURRENT_PARSING", "5"))

    @property
    def max_concurrent_indexing(self) -> int:
        return int(os.getenv("MAX_CONCURRENT_INDEXING", "10"))

    @property
    def shutdown_task_timeout(self) -> float:
        return float(os.getenv("SHUTDOWN_TASK_TIMEOUT", "240.0"))

    @property
    def max_delivery_attempts(self) -> int:
        """Max times a message can be delivered before being dead-lettered (ACK-ed and discarded)."""
        return int(os.getenv("MAX_DELIVERY_ATTEMPTS", "10"))

    @property
    def max_pending_indexing_tasks(self) -> int:
        return int(
            os.getenv(
                "MAX_PENDING_INDEXING_TASKS",
                str(max(self.max_concurrent_parsing, self.max_concurrent_indexing) * 4),
            )
        )


messaging_env = MessagingEnvConfig()


def get_message_broker_type() -> MessageBrokerType:
    """Convenience wrapper around ``messaging_env.message_broker_type``."""
    return messaging_env.message_broker_type


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

    max_len: int = Field(default=10000, description="Max stream length for XADD")
    block_ms: int = Field(default=2000, description="XREADGROUP block timeout in ms")
    batch_size: int = Field(default=10, description="Messages per XREADGROUP call")
    claim_min_idle_ms: int = Field(
        default=30000,
        description="Min idle time in ms before XAUTOCLAIM can steal a pending message",
    )
    client_id: str = "pipeshub"
    group_id: str = "default_group"
    topics: list[str] = Field(default_factory=list)
