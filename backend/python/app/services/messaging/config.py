import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional


REQUIRED_TOPICS = [
    "record-events",
    "entity-events",
    "sync-events",
    "health-check",
]


def get_message_broker_type() -> str:
    """Get the message broker type from environment variable."""
    broker_type = os.getenv("MESSAGE_BROKER", "kafka").lower()
    if broker_type not in ("kafka", "redis"):
        raise ValueError(
            f"Unsupported MESSAGE_BROKER type: {broker_type}. Must be 'kafka' or 'redis'."
        )
    return broker_type


@dataclass
class RedisStreamsConfig:
    """Redis Streams configuration"""
    host: str = "localhost"
    port: int = 6379
    password: Optional[str] = None
    db: int = 0
    max_len: int = 10000
    block_ms: int = 2000
    client_id: str = "pipeshub"
    group_id: str = "default_group"
    topics: List[str] = field(default_factory=list)
