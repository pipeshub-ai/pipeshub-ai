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
# Environment-driven settings
# ---------------------------------------------------------------------------

MESSAGE_BROKER_ENV = os.getenv("MESSAGE_BROKER", MessageBrokerType.KAFKA.value)
REDIS_STREAMS_MAXLEN = int(os.getenv("REDIS_STREAMS_MAXLEN", "10000"))

# Indexing concurrency controls (shared by Kafka & Redis indexing consumers)
MAX_CONCURRENT_PARSING = int(os.getenv("MAX_CONCURRENT_PARSING", "5"))
MAX_CONCURRENT_INDEXING = int(os.getenv("MAX_CONCURRENT_INDEXING", "10"))
SHUTDOWN_TASK_TIMEOUT = float(os.getenv("SHUTDOWN_TASK_TIMEOUT", "240.0"))
MAX_PENDING_INDEXING_TASKS = int(
    os.getenv(
        "MAX_PENDING_INDEXING_TASKS",
        str(max(MAX_CONCURRENT_PARSING, MAX_CONCURRENT_INDEXING) * 4),
    )
)


def get_message_broker_type() -> MessageBrokerType:
    """Get the message broker type from environment variable."""
    raw = MESSAGE_BROKER_ENV.lower()
    try:
        return MessageBrokerType(raw)
    except ValueError:
        valid = ", ".join(f"'{m.value}'" for m in MessageBrokerType)
        raise ValueError(
            f"Unsupported MESSAGE_BROKER type: {raw}. Must be one of {valid}."
        )


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
    client_id: str = "pipeshub"
    group_id: str = "default_group"
    topics: list[str] = Field(default_factory=list)
