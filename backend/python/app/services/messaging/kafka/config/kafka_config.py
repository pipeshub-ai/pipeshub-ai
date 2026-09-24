from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class KafkaProducerConfig:
    """Kafka configuration"""
    bootstrap_servers: List[str]
    client_id: str
    ssl: bool = False
    sasl: Optional[Dict[str, str]] = None


_REBALANCE_DRAIN_MARGIN = 1.25
_MIN_REBALANCE_TIMEOUT_MS = 300_000
_MIN_POLL_INTERVAL_MS = 600_000


def _rebalance_timeout_ms() -> int:
    """A drain window comfortably wider than the shutdown timeout it protects."""
    from app.services.messaging.config import messaging_env

    return max(
        _MIN_REBALANCE_TIMEOUT_MS,
        int(messaging_env.shutdown_task_timeout * 1000 * _REBALANCE_DRAIN_MARGIN),
    )


def _max_poll_interval_ms() -> int:
    """Never below the rebalance timeout it bounds.

    Kafka derives rebalance_timeout from max_poll_interval by default, and a
    rebalance window wider than the poll interval is not a configuration the
    broker can honour — so a raised SHUTDOWN_TASK_TIMEOUT has to lift both.
    """
    return max(_MIN_POLL_INTERVAL_MS, _rebalance_timeout_ms())


@dataclass
class KafkaConsumerConfig:
    """Kafka configuration

    The group-liveness timeouts below were previously left at aiokafka's
    defaults (10s session / 3s heartbeat), which gives only ~3 missed
    heartbeats of slack. Heartbeats run on the same event loop the consumer
    polls on, so any stall longer than 10s evicted this consumer from its
    group mid-batch and triggered a rebalance — and because the rebalance
    timeout defaults to the session timeout, that rebalance would not wait for
    in-flight records either, so their offsets went back uncommitted for
    redelivery. Widened deliberately: a stall is a throughput problem worth an
    alert, not a reason to reshuffle the whole group.
    """
    topics: List[str]
    client_id: str
    group_id: str
    auto_offset_reset: str
    enable_auto_commit: bool
    bootstrap_servers: List[str]
    ssl: bool = False
    sasl: Optional[Dict[str, str]] = None
    session_timeout_ms: int = 45000
    heartbeat_interval_ms: int = 15000
    # Records legitimately take minutes (RECORD_PROCESSING_TIMEOUT defaults to
    # 1800s), but processing never blocks the poll loop — it runs on the
    # worker loop, and backpressure pauses partitions rather than skipping
    # getmany. This only needs to cover a slow poll iteration, with room to
    # spare.
    max_poll_interval_ms: int = field(
        default_factory=lambda: _max_poll_interval_ms()
    )
    # Derived, not pinned: this must exceed SHUTDOWN_TASK_TIMEOUT so a rolling
    # restart drains its in-flight records instead of having them redelivered
    # to another replica while this one is still working on them. That knob is
    # an env var, so a hardcoded 300000 silently breaks the invariant the
    # moment an operator raises it past 300s.
    rebalance_timeout_ms: int = field(
        default_factory=lambda: _rebalance_timeout_ms()
    )
