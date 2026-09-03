import os
from collections.abc import AsyncGenerator, Awaitable, Callable
from enum import Enum

from pydantic import BaseModel, Field, JsonValue

from app.services.resource_governor.models import ParseTier
from app.utils.env_config import env_int as _env_int
from app.utils.env_config import env_seconds as _env_seconds


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

# Legacy static fallbacks for MAX_CONCURRENT_PARSING/INDEXING, used only when
# no ResourceGovernor is configured (see MessagingEnvConfig.max_concurrent_*
# below). Production sizing comes from the governor's resolved ceilings.
_LEGACY_DEFAULT_MAX_CONCURRENT_PARSING = 5
_LEGACY_DEFAULT_MAX_CONCURRENT_INDEXING = 7


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
    timestamp: int | None = None
    # Trace id propagated from the producer; optional so legacy messages parse.
    requestId: str | None = None
    is_final_failure: bool | None = None  # Set by consumer: True = will commit/dead-letter, False = will retry


class PipelineEventData(BaseModel):
    """Data yielded alongside a pipeline event."""

    record_id: str | None = None
    record_name: str | None = None
    count: int | None = None
    # Set by the handler when yielding START_PARSING (it already knows
    # extension/mime/content length at that point) so the consumer can route
    # to the right resource_governor pool instead of re-deriving format from
    # the payload.
    tier: ParseTier | None = None
    size_bytes: int | None = None


class PipelineEvent(BaseModel):
    """Event yielded by the indexing pipeline handler."""

    event: IndexingEvent
    data: PipelineEventData | None = None


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
        raw = os.getenv("MESSAGE_BROKER", MessageBrokerType.REDIS.value).lower()
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
        """Static legacy fallback, used only when no ``ResourceGovernor`` is
        configured (sizing the pre-governor ``asyncio.Semaphore`` and the
        cluster-wide lease limit in that fallback path — see
        ``consumer_concurrency.parse_ceiling``). Production code should
        prefer ``env_max_concurrent_parsing`` / the governor's resolved
        ceiling. Deliberately reuses that same optional accessor (rather
        than re-reading ``os.getenv`` with a string default) so an operator
        deploying with the var set to empty — the shipped compose/helm
        default as of Phase 6, meaning "derive" — doesn't hit
        ``int("")`` here too.
        """
        return self.env_max_concurrent_parsing or _LEGACY_DEFAULT_MAX_CONCURRENT_PARSING

    @property
    def max_concurrent_indexing(self) -> int:
        """See ``max_concurrent_parsing``. The default was previously ``10``,
        which never matched the compose-shipped ``7`` — aligned to ``7``."""
        return self.env_max_concurrent_indexing or _LEGACY_DEFAULT_MAX_CONCURRENT_INDEXING

    @property
    def env_max_concurrent_parsing(self) -> int | None:
        """Raw ``MAX_CONCURRENT_PARSING`` as set by the operator, or ``None``
        when unset/empty so ``ResourceGovernor`` derives a ceiling from
        cgroup/CPU limits instead of falling back to
        ``max_concurrent_parsing``'s static default. Empty string (not just
        a missing key) must also resolve to "derive": the shipped compose
        files pass ``${MAX_CONCURRENT_PARSING:-}`` so the var is always
        *present* in the container's environment, just blank by default."""
        raw = os.getenv("MAX_CONCURRENT_PARSING")
        return int(raw) if raw else None

    @property
    def env_max_concurrent_indexing(self) -> int | None:
        """Raw ``MAX_CONCURRENT_INDEXING`` as set by the operator, or
        ``None`` when unset/empty — see ``env_max_concurrent_parsing``."""
        raw = os.getenv("MAX_CONCURRENT_INDEXING")
        return int(raw) if raw else None

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
        return _env_seconds("INDEXING_CONCURRENCY_LEASE_SECONDS", 120.0)

    @property
    def concurrency_renew_interval_seconds(self) -> float:
        return _env_seconds("INDEXING_CONCURRENCY_RENEW_INTERVAL_SECONDS", 30.0)

    @property
    def concurrency_acquire_poll_seconds(self) -> float:
        return _env_seconds("INDEXING_CONCURRENCY_ACQUIRE_POLL_SECONDS", 0.5)

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
        return _env_seconds("INDEXING_RECORD_LEASE_WAIT_SECONDS", 10.0)

    @property
    def concurrency_redis_timeout_seconds(self) -> float:
        """Socket timeout for lease/retry Redis commands.

        Two seconds, not five: this has to fire strictly before any caller's
        own deadline, so redis-py raises its own clean timeout instead of
        having the command cancelled underneath it — a cancelled command
        forces the connection closed (redis-py disconnects on ``BaseException``
        while reading a response), and replacing connections at that rate is
        what exhausted Redis's client limit in production.
        """
        return _env_seconds("INDEXING_CONCURRENCY_REDIS_TIMEOUT_SECONDS", 2.0)

    @property
    def concurrency_redis_max_connections(self) -> int:
        """Connection-pool size for the lease/retry clients, per event loop.

        redis-py defaults to effectively unbounded (``2**31``), so every
        connection a cancelled command destroyed was replaced by a new TCP
        connect. A bounded pool turns that into queueing instead.
        """
        return max(1, _env_int("INDEXING_CONCURRENCY_REDIS_MAX_CONNECTIONS", 32))

    @property
    def concurrency_acquire_max_backoff_seconds(self) -> float:
        """Ceiling on the exponential backoff between lease-acquire attempts.

        The wait used to be a flat 0.5s poll with no backoff and no give-up,
        so load on Redis scaled with the size of the queue waiting on it and
        an error storm sustained itself indefinitely.
        """
        return _env_seconds("INDEXING_CONCURRENCY_ACQUIRE_MAX_BACKOFF_SECONDS", 5.0)

    @property
    def split_index_lease_pools(self) -> bool:
        """Whether light records take their own cluster-wide indexing lease.

        Off until no previous-build replica remains: those admit every record
        into the shared ``indexing`` pool at the full budget, so a separate
        ``indexing:light`` pool is additive during a rolling upgrade and the
        fleet can exceed MAX_CONCURRENT_INDEXING by ``index_light``. The
        node-local per-tier gates are unaffected either way — see
        ``consumer_concurrency.index_lease_pool``.
        """
        return os.getenv("INDEXING_SPLIT_LEASE_POOLS", "false").lower() == "true"

    @property
    def concurrency_failure_budget(self) -> int:
        """Consecutive lease-op failures before a capacity pool fails open.

        Capacity leases (indexing/parsing) are a cluster-wide cap layered on
        top of node-local gates, so continuing under the local gate alone is
        a bounded degradation. The per-record lease is mutual exclusion and
        never fails open — see ``LeaseKind``.
        """
        return max(1, _env_int("INDEXING_CONCURRENCY_FAILURE_BUDGET", 5))

    @property
    def shutdown_task_timeout(self) -> float:
        return _env_seconds("SHUTDOWN_TASK_TIMEOUT", 240.0)

    @property
    def max_delivery_attempts(self) -> int:
        """Max times a message can be delivered before being dead-lettered (ACK-ed and discarded)."""
        return _env_int("MAX_DELIVERY_ATTEMPTS", 3)

    @property
    def message_batch_size_simple(self) -> int:
        """Batch size for simple consumers (entity/sync events)."""
        return int(os.getenv("MESSAGE_BATCH_SIZE_SIMPLE", "10"))

    @property
    def message_batch_size_indexing(self) -> int:
        """Batch size for indexing consumers (record events).

        Reading one message per loop iteration adds a full consumer-loop
        round-trip of latency between every task spawn — with high
        MAX_CONCURRENT_* ceilings this becomes the throughput ceiling itself
        even though ``pending_task_ceiling`` (consumer_concurrency.py)
        already caps how many tasks can be in flight, so a bigger batch
        here cannot cause overcommit — it only lets the consumer fill that
        same ceiling faster.
        """
        return int(os.getenv("MESSAGE_BATCH_SIZE_INDEXING", "10"))

    @property
    def message_timeout_ms(self) -> int:
        """Block timeout for reading messages (milliseconds)."""
        return int(os.getenv("MESSAGE_TIMEOUT_MS", "2000"))

    @property
    def record_processing_timeout(self) -> float:
        """Max seconds a single record is allowed to process before being timed out."""
        return _env_seconds("RECORD_PROCESSING_TIMEOUT", 1800.0)

    @property
    def max_pending_indexing_tasks(self) -> int:
        """Static legacy fallback — prefer
        ``consumer_concurrency.pending_task_ceiling(host)``, which derives
        this from the governor's *resolved* ceilings (this node's actual
        cgroup/CPU limits) and only falls back to this property when no
        governor is configured. Reads ``os.getenv`` directly rather than via
        its string-default form so an empty (not just missing)
        ``MAX_PENDING_INDEXING_TASKS`` — the shipped compose/helm default as
        of Phase 6 — also falls through to the derived expression instead of
        raising on ``int("")``."""
        raw = os.getenv("MAX_PENDING_INDEXING_TASKS")
        if raw:
            return int(raw)
        return max(self.max_concurrent_parsing, self.max_concurrent_indexing) * 4

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

    @property
    def stranded_record_republish_after_seconds(self) -> float:
        """How long a record may sit unqueued before its event is re-sent.

        Zero disables the sweep. Guards the gap no other recovery path covers:
        a record on a live connector whose event was lost or never published is
        invisible to the stale scan (which only looks at IN_PROGRESS) and to the
        inactive-connector sweep. Set it well above the largest backlog the
        broker is expected to carry, or records legitimately waiting their turn
        will be published a second time.
        """
        return _env_seconds("STRANDED_RECORD_REPUBLISH_AFTER_SECONDS", 0.0)

    @property
    def vector_membership_backfill_interval_seconds(self) -> float:
        return float(os.getenv("VECTOR_MEMBERSHIP_BACKFILL_INTERVAL_SECONDS", "30"))

    @property
    def vector_membership_backfill_startup_grace_seconds(self) -> float:
        return float(os.getenv("VECTOR_MEMBERSHIP_BACKFILL_STARTUP_GRACE_SECONDS", "30"))

    @property
    def vector_membership_backfill_page_size(self) -> int:
        return int(os.getenv("VECTOR_MEMBERSHIP_BACKFILL_PAGE_SIZE", "50"))

    @property
    def vector_membership_backfill_vrid_pause_ms(self) -> int:
        return int(os.getenv("VECTOR_MEMBERSHIP_BACKFILL_VRID_PAUSE_MS", "20"))

    @property
    def redis_max_deliveries(self) -> int:
        """Delivery-count ceiling for the Redis dead-letter backstop.

        Distinct from ``max_delivery_attempts``, which counts *processing
        failures*. Redis's ``times_delivered`` counts every delivery: the
        first read, a claim after a restart, an idle-drain recovery pass. A
        record delivered three times may have failed zero times, so sharing
        one threshold dead-letters healthy work — especially with fair
        scheduling, where an entry legitimately sits un-ACKed while it waits
        its turn. The backstop only needs to bound a true poison loop that
        kills the process before the failure counter can be written (#2992),
        so it is deliberately slack.
        """
        return max(
            self.max_delivery_attempts + 1,
            _env_int("REDIS_MAX_DELIVERIES", 10),
        )

    @property
    def fair_scheduling_enabled(self) -> bool:
        """Master switch for consumer-side fair scheduling.

        Enabling it buffers messages in-process and dispatches them out of
        broker order, which switches the Kafka consumer from "commit
        offset+1 per message" to a contiguous commit watermark. Setting this
        to ``false`` restores the exact pre-existing FIFO behavior with zero
        buffering overhead.

        Ships **on**: without it a single connector's backlog takes the whole
        pipeline until it drains, which is the behaviour this exists to fix.
        Note that it is a change in commit protocol, not just a tuning knob,
        so it is worth being deliberate about on an upgrade rather than
        discovering it.
        """
        return os.getenv("FAIR_SCHEDULING_ENABLED", "true").lower() == "true"

    @property
    def fair_scheduling_key_fields(self) -> tuple[str, ...]:
        """Payload fields forming the hierarchical fairness key, outermost
        first, comma-separated.

        The default ``orgId,connectorId`` is deliberate: ``orgId`` separates
        customers but *not* users within one customer -- every user in an org
        shares its ``orgId``, so keying on it alone gives a single-org
        install one queue and no fairness. ``connectorId`` (the connector
        instance id, and the knowledge-base id for uploads) is what separates
        one user's sync from another's. Fairness runs across orgs first, then
        across connectors inside each org.
        """
        raw = os.getenv("FAIR_SCHEDULING_KEY_FIELDS", "orgId,connectorId")
        fields = tuple(part.strip() for part in raw.split(",") if part.strip())
        return fields or ("orgId", "connectorId")

    @property
    def fair_scheduling_quantum(self) -> int:
        """Default DRR quantum: messages per key per round when no
        ``WeightProvider`` is injected."""
        return max(1, _env_int("FAIR_SCHEDULING_QUANTUM", 1))

    @property
    def fair_scheduling_max_buffer(self) -> int:
        """Total buffered messages across every key. Bounds memory, not
        throughput — a full buffer pauses reads, it never drops messages."""
        return max(1, _env_int("FAIR_SCHEDULING_MAX_BUFFER", 2000))

    @property
    def fair_scheduling_max_per_entity(self) -> int:
        """Per-key buffer cap. Exceeding it triggers overflow re-publish
        (the excess message goes to the tail of the topic/stream) rather
        than blocking other keys behind a single noisy one."""
        return max(1, _env_int("FAIR_SCHEDULING_MAX_PER_ENTITY", 500))

    @property
    def fair_scheduling_parallel_partitions(self) -> bool:
        """Let one Kafka partition have several records in flight at once.

        Off by default. The consumer holds a partition for a record's whole
        lifetime today, so with a single-partition ``record-events`` it
        indexes exactly one record at a time no matter how high
        ``MAX_CONCURRENT_INDEXING`` is set. Turning this on moves the
        serialisation boundary from the partition to the individual record,
        so concurrency comes from the pipeline gates instead of the topic's
        partition count.

        Requires fair scheduling (the commit watermark): without it,
        completing offsets out of order within a partition would commit past
        work that has not finished.
        """
        return (
            os.getenv("FAIR_SCHEDULING_PARALLEL_PARTITIONS", "false").lower()
            == "true"
        )

    @property
    def fair_scheduling_lane_count(self) -> int:
        """Number of broker lanes for the indexing topic.

        ``1`` means no laning: the producer is left unwrapped and the publish
        path is byte-for-byte what it was before lanes existed. Above 1 --
        which is the default -- records are placed by fairness key so one
        key's backlog never sits in front of another's, which is what lets a
        full buffer pause a single lane instead of stalling every key behind
        it.

        On Kafka a lane *is* a partition, so this only switches key-based
        routing on; the real lane count is the topic's partition count
        (``KAFKA_TOPIC_PARTITIONS``, applied by the Node admin service). On
        Redis Streams this is the actual number of ``record-events.N``
        streams and must match on producers and consumers.
        """
        return max(1, _env_int("FAIR_SCHEDULING_LANE_COUNT", 8))

    @property
    def fair_scheduling_lane_key_field(self) -> str:
        """Payload field a lane is chosen from.

        Defaults to the *innermost* fairness level, ``connectorId``: lanes
        have to separate what the scheduler separates, and ``orgId`` alone
        cannot separate two users of the same org.
        """
        return os.getenv("FAIR_SCHEDULING_LANE_KEY_FIELD", "connectorId")

    @property
    def fair_scheduling_laned_topics(self) -> tuple[str, ...]:
        """Topics subject to lane routing. Only the indexing topic by
        default -- entity and sync events are low volume with no fairness
        problem to solve."""
        raw = os.getenv("FAIR_SCHEDULING_LANED_TOPICS", Topic.RECORD_EVENTS.value)
        topics = tuple(part.strip() for part in raw.split(",") if part.strip())
        return topics or (Topic.RECORD_EVENTS.value,)

    @property
    def fair_scheduling_metrics_per_connector(self) -> bool:
        """Label the dispatch counter by connector as well as org.

        Off by default because it multiplies that metric's series count by
        the number of connector instances, which is unbounded. Worth turning
        on for a single-org install, where labelling by org alone collapses
        every connector into one series and the per-connector share -- the
        thing fair scheduling exists to produce -- becomes invisible.
        """
        return (
            os.getenv("FAIR_SCHEDULING_METRICS_PER_CONNECTOR", "false").lower()
            == "true"
        )

    @property
    def fair_scheduling_max_dwell_seconds(self) -> float:
        """How long a buffered offset may go unresolved before the consumer
        force-commits past it. Bounds the damage from a dispatch path that
        fails to settle its watermark claim: without it, one such offset
        stalls every later commit on its partition until a restart."""
        return _env_seconds("FAIR_SCHEDULING_MAX_DWELL_SECONDS", 900.0)


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
    password: str | None = None
    db: int = 0
    # Set through the admin UI and stored in the encrypted KV config. Kept on
    # the model so it reaches `RedisConnectionConfig.from_host_port(tls=...)`:
    # an install that enabled TLS there has no `REDIS_TLS_ENABLED` env var,
    # and dropping the flag connects it in plaintext.
    tls: bool = False


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
    ephemeral_group: bool = Field(
        default=False,
        description=(
            "Group belongs to one process and is disposable: created at the stream tail "
            "instead of the head (so a fresh group does not replay retained history) and "
            "destroyed on shutdown (so groups do not accumulate, since Redis never "
            "expires them). Used for broadcast consumers, where every process needs its "
            "own group to receive every message."
        ),
    )
