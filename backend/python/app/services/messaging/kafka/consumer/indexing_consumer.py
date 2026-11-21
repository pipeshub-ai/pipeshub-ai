import asyncio
import contextlib
import json
import logging
import math
import threading
import time
import uuid
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import wait as futures_wait
from logging import Logger
from typing import TYPE_CHECKING, Any, Optional, override

from aiokafka import (  # type: ignore
    AIOKafkaConsumer,
    ConsumerRebalanceListener,
    TopicPartition,
)
from aiokafka.structs import ConsumerRecord  # type: ignore
from pydantic import ValidationError

from app.core.envelope import (
    EnvelopeRejected,
    ctx_of,
    decode_message,
    open_stream_message,
)
from app.core.org_context import bind_optional, spawn_detached
from app.modules.indexing.embed_resume import stamp_delivery_id
from app.modules.indexing.record_progress import (
    begin_progress,
    end_progress,
    new_progress,
)
from app.services.messaging import consumer_concurrency as concurrency
from app.services.messaging.backpressure import BackpressureEpisode
from app.services.messaging.config import (
    IndexingEvent,
    IndexingMessageHandler,
    StreamMessage,
    compute_retry_backoff_seconds,
    messaging_env,
)
from app.services.messaging.connector_off import (
    ConnectorOffFilter,
    describe_connector_off,
    settle_connector_off,
)
from app.services.messaging.disposition import (
    AbandonedMessageSink,
    describe_message,
    notify_abandoned,
)
from app.services.messaging.distributed_concurrency import DistributedLeaseSet
from app.services.messaging.error_classifier import (
    MessageErrorClassifier,
    MessageErrorType,
)
from app.services.messaging.interface.consumer import IMessagingConsumer
from app.services.messaging.kafka.config.kafka_config import (
    KafkaConsumerConfig,
    kafka_security_kwargs,
)
from app.services.messaging.kafka.consumer.backlog import read_partition_backlog
from app.services.messaging.kafka.consumer.remembered import (
    OffsetFetcher,
    RememberedOffsets,
)
from app.services.messaging.lanes.backlog import LaneBacklog
from app.services.messaging.lease import LeaseRenewer
from app.services.messaging.membership import register_group
from app.services.messaging.message_log import bound_message
from app.services.messaging.precision_check import PrecisionHandBack, counted
from app.services.messaging.scheduling.drr_scheduler import DRRScheduler
from app.services.messaging.scheduling.interface import (
    EnqueueResult,
    FairnessKey,
    FairnessKeyExtractor,
    FairSchedulerConfig,
    IndexingFairness,
    WeightProvider,
)
from app.services.messaging.scheduling.key_extractors import (
    CompositeKeyExtractor,
    TieredKeyExtractor,
)
from app.services.messaging.scheduling.offset_tracker import PartitionOffsetTracker
from app.services.messaging.scheduling.slot_share import SlotShare, SlotSharePlan
from app.services.messaging.storage_protocol import tagged
from app.services.resource_governor import ParseTier, Pool
from app.telemetry.modules import scheduling_metrics as metrics
from app.utils import indexing_timing as timing
from app.utils.cpu_offload import offload_if_large
from app.utils.error_class import log_failure, logged_level, quotes_content
from app.utils.logging_fields import log_fields
from app.utils.loop_lag_monitor import LoopLagMonitor, loop_lag_monitor_from_env
from app.utils.request_context import (
    context_from_envelope,
    run_in_context,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.services.messaging.backpressure import BackpressureCoordinator
    from app.services.messaging.distributed_concurrency import (
        DistributedConcurrencyManager,
    )
    from app.services.messaging.interface.producer import IMessagingProducer
    from app.services.messaging.retry_manager import RetryManager
    from app.services.messaging.worker_loop import WorkerLoop
    from app.services.resource_governor import ResourceGovernor

FUTURE_CLEANUP_INTERVAL = 100  # Cleanup completed futures every N messages
_MAIN_LOOP_OP_TIMEOUT = 5.0
# The backlog read opens its own short-lived connection, so it gets longer
# than a commit does; the caller's wait is a little longer still, so the read
# reports its own timeout rather than being abandoned mid-way.
_BACKLOG_READ_TIMEOUT = 10.0
_BACKLOG_BRIDGE_TIMEOUT = _BACKLOG_READ_TIMEOUT + 5.0
# How often the retry-backoff wait re-checks self.running, so a shutdown
# request can interrupt a long (up to 300s) wait instead of holding an
# active-future slot — and blocking graceful shutdown — for the full delay.
_DELAY_POLL_INTERVAL_SECONDS = 1.0
# How often the consume loop checks for offsets held past the dwell budget.
# Cheap (a scan of the outstanding sets) but pointless every iteration.
_DWELL_SWEEP_INTERVAL_SECONDS = 30.0
# Longest the fair loop waits for a delivery to finish while it has work it
# cannot dispatch yet. A finish ends the wait at once; this bounds it for what
# no finish signals (a backoff coming due, a downstream pause lifting, records
# on another partition). Also the poll timeout parallel dispatch keeps.
_BUSY_POLL_TIMEOUT_MS = 50
# Sentinel CompositeKeyExtractor uses for an absent fairness field.
_DEFAULT_KEY_LEVEL = "__default__"
# A message the read phase has not parsed yet (see __parse_batch).
_UNPARSED = object()
# Remembered messages fetched back per connector per read pass, and how long
# one fetch-back may take before it is retried on the next pass.
_FETCH_BACK_BATCH = 500
# Messages read per pass while reading past a connector at its cap.
_READ_PAST_BATCH = 1000
_FETCH_BACK_TIMEOUT_SECONDS = 15.0
# A fetch-back stops reading after this long and hands back what it has read,
# inside the timeout above, so one slow partition does not cost the others'
# results; what it did not reach is asked for again.
_FETCH_BACK_READ_SECONDS = 10.0

# Re-exported for backwards compatibility with existing call sites/tests in
# this module; canonical definition lives in app.services.messaging.config
# so the Redis Streams consumer can share the same backoff schedule.
_compute_retry_backoff_seconds = compute_retry_backoff_seconds

# A partition whose head could not be dispatched is paused for this long before it is read again, doubling per failure
# of the same offset. Re-reading it at once spins the loop at ~2,000 polls (and ERROR lines) a second.
_STUCK_RETRY_BASE_S = 1.0
_STUCK_RETRY_MAX_S = 30.0


def _stuck_retry_delay_s(failures: int) -> float:
    return min(_STUCK_RETRY_BASE_S * 2 ** min(failures - 1, 10), _STUCK_RETRY_MAX_S)


# Events waiting for a busy record keep their payload for at most 1/N of the
# buffer, and a few per record; past that only the offset is kept. Without
# it a burst for one busy record fills the buffer and pauses every lane.
_WAITING_HELD_DIVISOR = 4
_WAITING_HELD_PER_RECORD = 4
# Offsets kept that way, across every record. About 100 bytes each.
_WAITING_POSITIONS_BUDGET = 100_000


class _WaitingEvent:
    """An event waiting for its record. ``item`` is None when only the offset is kept."""

    __slots__ = ("tp", "offset", "item", "key", "alive", "not_before")

    def __init__(
        self,
        tp: "TopicPartition",
        offset: int,
        item: "tuple[TopicPartition, ConsumerRecord, StreamMessage] | None",
        key: "FairnessKey | None",
        not_before: float | None = None,
    ) -> None:
        self.tp = tp
        self.offset = offset
        self.item = item
        self.key = key
        # False once out of its record's queue, so a read-back planned
        # against it can tell without searching the queue.
        self.alive = True
        # A retry's backoff, which lives in the payload: kept here for an
        # event held by offset only, and stamped back on when it is read back.
        self.not_before = not_before


def _arrived_before(event: _WaitingEvent, tp: "TopicPartition", offset: int) -> bool:
    # Offsets order events only within one partition.
    return event.tp == tp and event.offset < offset


def _insertion_point(waiting: "deque[_WaitingEvent]", tp: "TopicPartition", offset: int) -> int:
    """Where an event goes in its record's queue: before every later event of its own partition.

    One record's events can sit on several partitions (a re-queue is sent
    without a key), so the scan steps over other partitions' events rather
    than stopping at them, and stops at the first earlier event of its own.
    A new event is usually the latest, which finds its place at once.
    Walked with an iterator: indexing a deque away from its ends is linear,
    which would make a scan over a long queue quadratic on the consume loop.
    """
    position = len(waiting)
    for index, entry in zip(range(len(waiting) - 1, -1, -1), reversed(waiting), strict=True):
        if entry.tp == tp:
            if entry.offset < offset:
                break
            position = index
    return position



class _InFlightOffset:
    """One buffered offset's outstanding claim on the commit watermark.

    ``__process_message_wrapper`` has several paths that deliberately return
    without committing (shutdown, a failed hand-back). Committing
    ``offset + 1`` per message let a later message cover those; a watermark
    does not, so an offset nobody resolves stalls every later commit on its
    partition. Each path therefore states which end it reached, and the
    future's done-callback resolves anything that forgot as ``redeliver`` --
    a stalled watermark is never the silent default.
    """

    __slots__ = ("tp", "offset", "resolved", "epoch")

    def __init__(self, tp: "TopicPartition", offset: int, epoch: int | None = None) -> None:
        self.tp = tp
        self.offset = offset
        self.resolved = False
        # The partition's assignment when this delivery began; None when unknown.
        self.epoch = epoch


class _ReadOutcome:
    """Result of enqueuing one read message (see ``__enqueue_message``)."""

    BUFFERED = "buffered"
    RESOLVED = "resolved"       # terminal inline, e.g. an unparseable message
    PARKED = "parked"           # key is capped; held in memory, keep reading
    REMEMBERED = "remembered"   # key is capped; position kept, keep reading
    STOP_PARTITION = "stop"     # no buffer room at all: seek back and stop


class _SchedulerRebalanceListener(ConsumerRebalanceListener):
    """Keeps the DRR buffer and commit watermark in sync with partition
    ownership. Only registered when fair scheduling is enabled -- without
    buffering there is nothing revocation needs to clean up."""

    def __init__(self, consumer: "IndexingKafkaConsumer") -> None:
        self._consumer = consumer

    async def on_partitions_revoked(self, revoked: "list[TopicPartition]") -> None:
        await self._consumer._on_partitions_revoked(revoked)

    async def on_partitions_assigned(self, assigned: "list[TopicPartition]") -> None:
        await self._consumer._on_partitions_assigned(assigned)


# aiokafka keeps one fetch per broker in flight, and a partition paused for its record is left out of it: once resumed it
# waits for the idle partitions' long-poll to come back. aiokafka's 500 ms default capped a busy partition beside an idle
# one at 2 records/s (FIFO) or one batch per 500 ms (fair).
_FETCH_MAX_WAIT_MS = 50


class IndexingKafkaConsumer(IMessagingConsumer):
    """Kafka consumer with nested concurrency control for indexing.

    MAX_CONCURRENT_INDEXING bounds active handlers across the full pipeline;
    MAX_CONCURRENT_PARSING further bounds parsing within that active set.

    Uses Redis-based RetryManager for persistent retry tracking across restarts.
    Error classification is based purely on exception type, not database status.

    The message handler must be an async generator that yields events:
    - {'event': 'parsing_complete', ...} - when parsing phase is done
    - {'event': 'indexing_complete', ...} - when indexing phase is done
    """

    def __init__(
        self,
        logger: Logger,
        kafka_config: KafkaConsumerConfig,
        retry_manager: Optional["RetryManager"] = None,
        producer: Optional["IMessagingProducer"] = None,
        concurrency_manager: Optional["DistributedConcurrencyManager"] = None,
        governor: Optional["ResourceGovernor"] = None,
        backpressure_coordinator: Optional["BackpressureCoordinator"] = None,
        fair_scheduler_config: FairSchedulerConfig | None = None,
        key_extractor: FairnessKeyExtractor | None = None,
        weight_provider: WeightProvider | None = None,
        disposition_sink: Optional[AbandonedMessageSink] = None,
        connector_off_filter: ConnectorOffFilter | None = None,
        precision_filter: PrecisionHandBack | None = None,
        stage_admission: "concurrency.StageAdmission | None" = None,
        priority_lane: "concurrency.PriorityLane | None" = None,
        worker: "WorkerLoop | None" = None,
        fairness: IndexingFairness | None = None,
    ) -> None:
        self._fairness = fairness
        self.logger = logger
        self.consumer: AIOKafkaConsumer | None = None
        self.running = False
        self.kafka_config = kafka_config
        if kafka_config:
            register_group(kafka_config.group_id, list(kafka_config.topics))
        self.consume_task = None
        self.retry_manager = retry_manager
        # Told about every message this consumer gives up on, before the commit
        # that makes it unrecoverable — see disposition.AbandonedMessageSink.
        self.disposition_sink = disposition_sink
        # Settles, as they are read, the events the handler would only skip
        # because their connector is off or gone -- see connector_off.py.
        self.connector_off_filter = connector_off_filter
        self.precision_filter = precision_filter
        self.producer = producer
        self.concurrency_manager = concurrency_manager
        # When set, node-local parsing/indexing admission is delegated to the
        # ResourceGovernor's adaptive gates instead of the static semaphores
        # below (see consumer_concurrency.acquire_parsing_slot/index_ceiling).
        self.governor = governor
        if stage_admission is not None and governor is not None:
            raise ValueError("a pipeline-stage consumer admits through its own limit, not a governor")
        self.stage_admission = stage_admission
        if priority_lane is not None and stage_admission is not None:
            raise ValueError("a consumer is a pipeline stage or the priority lane, not both")
        # The consumer of the priority topic: reads past the backlog, on caps of its own.
        self.priority_lane = priority_lane
        # Shared by every indexing consumer in the process (see WorkerLoop); None runs a loop of
        # this consumer's own.
        self._shared_worker = worker
        # Shared with the ParsingClient/DoclingClient/EmbeddingServerEmbeddings
        # instances that this consumer's records flow through — see
        # app.services.messaging.backpressure. __apply_backpressure() also
        # pauses partitions whenever one every record calls last saw a
        # 429+Retry-After (a Docling one holds only Docling's records at
        # dispatch), instead of pulling more work a saturated downstream would
        # just reject again.
        self.backpressure_coordinator = backpressure_coordinator
        self._consumer_instance_id = uuid.uuid4().hex
        self._distributed_log_times: dict[str, float] = {}
        # Worker thread infrastructure
        self.worker_executor: ThreadPoolExecutor | None = None
        self.worker_loop: asyncio.AbstractEventLoop | None = None
        self.worker_loop_ready = threading.Event()  # Signal when worker loop is ready
        # Guards publishing worker_loop against a concurrent stop request, so
        # a stop that arrives before the loop exists is not lost.
        self._worker_loop_lock = threading.Lock()
        self._worker_stop_requested = False
        self.main_loop: asyncio.AbstractEventLoop | None = None
        # Nested active-pipeline and parsing gates (created in worker thread).
        # Legacy fallback only: unused (stay None) once a governor is set.
        self.parsing_semaphore: asyncio.Semaphore | None = None
        self.indexing_semaphore: Any = None
        # One renewer for every lease this process holds, started on the
        # worker loop beside the records it guards (see lease.LeaseRenewer).
        self.lease_renewer: LeaseRenewer | None = None
        self.loop_lag_monitor: LoopLagMonitor | None = None
        self.message_handler: IndexingMessageHandler | None = None
        # Track active futures for proper cleanup
        self._active_futures: set[Future[bool]] = set()
        self._futures_lock = threading.Lock()
        self.gate_waiters = concurrency.GateWaiters()
        # Read-only counts of records waiting at each admission stage (see consumer_concurrency).
        from app import edition_services

        self.admission_backlog = edition_services.admission_backlog()
        if fairness is not None and fairness.observer is not None:
            self.admission_backlog.observer = fairness.observer
        self._suspects_waiting = 0
        # Held by a record whose last attempt never finished, so such records
        # run one at a time: when the process dies again, the record that
        # took it down is the only suspect in flight. Used on the worker loop.
        self._suspects = asyncio.Lock()
        self._backpressure_episode = BackpressureEpisode()
        # Reads are paused because no record could be admitted (not a downstream service asking for a pause).
        self._admission_paused = False
        self._partition_lock = threading.Lock()
        self._in_flight_partitions: set[TopicPartition] = set()
        self._deferred_partition_offsets: dict[TopicPartition, int] = {}
        # FIFO: partitions a finish rewound since the last poll; the rest of their batch is stale.
        self._rewound_since_poll: set[TopicPartition] = set()
        # Partitions paused because their head was not dispatched: (head offset, failures of it, monotonic deadline).
        # Main loop only. Nothing here resumes a held partition before its deadline (a rebalance does: __end_holds_read).
        self._held: dict[TopicPartition, tuple[int, int, float]] = {}

        # Absent config disables fair scheduling and keeps this consumer on
        # the exact pre-existing FIFO code path -- only MessagingFactory
        # (the production wiring point) resolves the env-driven OSS default,
        # so tests/call sites that construct this class directly are
        # unaffected unless they opt in explicitly.
        self.fair_scheduler_config = fair_scheduler_config or FairSchedulerConfig(
            enabled=False
        )
        entity_extractor: FairnessKeyExtractor = key_extractor or CompositeKeyExtractor(
            fields=self.fair_scheduler_config.key_fields
        )
        # The tier level is appended here rather than by the factory because
        # it depends on this consumer's governor (a collapsed light budget
        # routes every record to heavy) -- see consumer_concurrency.dispatch_tier.
        self.key_extractor: FairnessKeyExtractor = (
            TieredKeyExtractor(entity_extractor, tier_of=self._dispatch_tier_name)
            if self.fair_scheduler_config.tier_level
            else entity_extractor
        )
        self.weight_provider = weight_provider
        self._scheduler: (
            DRRScheduler[tuple[TopicPartition, ConsumerRecord, StreamMessage]] | None
        ) = None
        self._offset_tracker: PartitionOffsetTracker | None = None
        self._last_dwell_sweep = 0.0
        # Lanes (partitions) paused because one key on them is at its cap,
        # mapped to the key that blocked them. Main-loop-only state.
        self._lane_paused: dict[TopicPartition, FairnessKey] = {}
        # Records in flight, when parallel dispatch replaces per-partition
        # serialisation with per-record serialisation. Guarded by
        # _partition_lock like the other dispatch bookkeeping, because the
        # worker thread's completion callback releases entries.
        self._in_flight_records: set[str] = set()
        # Parallel dispatch: events whose record was in flight when they
        # reached the head of their leaf. The scheduler judges a leaf by its
        # head, so leaving one there stalls every later event of its
        # connector and tier until that record finishes, which for a reindex
        # can be hours. Each record's list is kept in offset order and only
        # its first entry is ever back in the scheduler (``_record_offered``),
        # so a record's events still run oldest first. Their offsets stay
        # tracked with no dwell clock, so the watermark floors at them. Those
        # holding a payload count against the buffer budget
        # (``_record_waiting_count``); past a cap only the offset is kept,
        # and read back when the record is free. Main-loop-only state.
        self._record_waiting: dict[str, deque[_WaitingEvent]] = {}
        self._record_waiting_count = 0
        self._record_waiting_held: dict[str, int] = {}
        self._record_waiting_positions = 0
        self._waiting_positions_logged = False
        self._waiting_fetcher: OffsetFetcher | None = None
        self._waiting_fetch_task: asyncio.Task | None = None
        self._waiting_fetch_plan: tuple[dict[str, list[_WaitingEvent]], dict[TopicPartition, int]] = ({}, {})
        self._waiting_fetch_reserved = 0
        # The positions of each record's events in the scheduler, so an
        # event leaving it can tell whether an older one of its record is
        # still held back in another leaf. Main-loop-only state.
        self._record_scheduled: dict[str, dict[tuple[TopicPartition, int], int]] = {}
        self._record_offered: dict[str, tuple[TopicPartition, ConsumerRecord, StreamMessage]] = {}
        # Records with waiting events and nothing in flight or offered; their
        # first event goes back to the scheduler as soon as it has room.
        self._records_released: set[str] = set()
        # Messages read but with no buffer room for their key yet. Held in
        # memory rather than seeked back: their offsets stay tracked, so the
        # watermark still floors at them, and re-reading them would only
        # re-hit the same cap. Counted against the buffer budget.
        # Partitions that returned records on the most recent poll; see
        # __other_lane_is_readable.
        self._partitions_with_data: set[TopicPartition] = set()
        # Partitions revoked since the last poll; see __read_phase.
        self._revoked_since_poll: set[TopicPartition] = set()
        # Set by every finish, cleared by the dispatch phase; see
        # __wait_for_finish. Made again by start(): a stop() leaves it set, and
        # an Event binds to the first loop that waits on it.
        self._finished = asyncio.Event()
        # Bumped on every revocation: a delivery of an earlier assignment settles nothing, however late it ends.
        self._partition_epoch: dict[TopicPartition, int] = {}
        self._deferred_messages: deque[
            tuple[TopicPartition, ConsumerRecord, StreamMessage, FairnessKey]
        ] = deque()
        # Positions of messages read past a connector at its cap; see
        # remembered.py. None when the budget is 0, which keeps parking.
        self._remembered: RememberedOffsets | None = None
        self._offset_fetcher: OffsetFetcher | None = None
        self._remembered_budget_logged = False
        self._fetch_back_task: asyncio.Task | None = None
        self._fetch_turns = 0
        self._fetch_back_plan: dict[FairnessKey, list[tuple[TopicPartition, int]]] = {}
        # Each connector's records in flight per index slot pool, so that
        # while another connector is waiting no one connector can hold more
        # than its share of the slots (see scheduling.slot_share).
        self._slot_share: SlotShare | None = None
        if self.fair_scheduler_config.enabled:
            room = (lambda key: fairness.buffer_room(key, self._scheduler)) if fairness and fairness.buffer_room else None
            self._scheduler = DRRScheduler(self.fair_scheduler_config, self.weight_provider, room=room)
            self._slot_share = (fairness.planner if fairness and fairness.planner else SlotShare)(  # type: ignore[assignment]
                self.fair_scheduler_config.entity_depth, self.weight_provider
            )
            self._offset_tracker = PartitionOffsetTracker(logger=logger)
            if self.fair_scheduler_config.max_remembered_positions > 0:
                self._remembered = RememberedOffsets(
                    self.fair_scheduler_config.max_remembered_positions
                )

    @staticmethod
    def kafka_config_to_dict(kafka_config: KafkaConsumerConfig) -> dict[str, Any]:
        """Convert KafkaConsumerConfig dataclass to dictionary format for aiokafka consumer"""
        config: dict[str, Any] = {
            'bootstrap_servers': ",".join(kafka_config.bootstrap_servers),
            'group_id': kafka_config.group_id,
            'auto_offset_reset': kafka_config.auto_offset_reset,
            'enable_auto_commit': kafka_config.enable_auto_commit,
            'client_id': tagged(kafka_config.client_id),
            'topics': kafka_config.topics,
            'session_timeout_ms': kafka_config.session_timeout_ms,
            'heartbeat_interval_ms': kafka_config.heartbeat_interval_ms,
            'max_poll_interval_ms': kafka_config.max_poll_interval_ms,
            'rebalance_timeout_ms': kafka_config.rebalance_timeout_ms,
            'fetch_max_wait_ms': _FETCH_MAX_WAIT_MS,
        }

        config.update(kafka_security_kwargs(ssl_enabled=kafka_config.ssl, sasl=kafka_config.sasl))

        return config

    def _on_worker_loop_started(self, loop: asyncio.AbstractEventLoop) -> None:
        """Set up this consumer's admission and lease renewal on its worker loop."""
        # A lock belongs to the loop that first waits on it, and a consumer
        # started again runs on a new loop.
        self._suspects = asyncio.Lock()
        if self.governor is not None:
            # One gate per pool, process-wide (ResourceGovernor.gate
            # memoises by pool, and raises if a second loop uses one), and
            # resolved per-message from the record's tier — index gates
            # from the event payload before admission, parse gates from
            # PipelineEventData.tier on START_PARSING. Binding them here
            # is what claims them for this loop.
            for pool in Pool:
                self.governor.gate(pool)
            self.logger.info(
                "Worker thread event loop started; using ResourceGovernor "
                "gates (index_heavy_ceiling=%d index_light_ceiling=%d "
                "heavy_parse_ceiling=%d light_parse_ceiling=%d)",
                self.governor.ceilings.index_heavy,
                self.governor.ceilings.index_light,
                self.governor.ceilings.heavy,
                self.governor.ceilings.light,
            )

        else:
            # Legacy static semaphores, created in the worker thread's event loop.
            self.parsing_semaphore = asyncio.Semaphore(messaging_env.max_concurrent_parsing)
            self.indexing_semaphore = asyncio.Semaphore(
                concurrency.local_index_permits(self)
            )
            self.logger.info("Worker thread event loop started with semaphores initialized")

        if self.concurrency_manager is not None:
            self.lease_renewer = LeaseRenewer(
                self.logger,
                self.concurrency_manager,
                lease_seconds=messaging_env.concurrency_lease_seconds,
                interval_seconds=messaging_env.concurrency_renew_interval_seconds,
            )
            loop.call_soon(self.lease_renewer.start)

    def _take_lease_renewer(self) -> LeaseRenewer | None:
        """Detach the renewer this consumer started on the shared loop; runs on that loop."""
        renewer, self.lease_renewer = self.lease_renewer, None
        return renewer

    def _stop_taking_work(self) -> None:
        """Called on the shared loop's thread when it dies: stops reads and dispatch, so nothing
        more is handed to a loop that cannot run it."""
        self.running = False

    def __start_worker_thread(self) -> None:
        """Start the worker thread with its own event loop, or join the shared worker loop."""
        if self._shared_worker is not None:
            self.worker_loop_ready.clear()
            loop = self._shared_worker.start()
            self.worker_loop = loop
            self._shared_worker.add_exit_listener(self._stop_taking_work)
            self._shared_worker.call(lambda: self._on_worker_loop_started(loop))
            # Reported once, under the record consumer: every consumer on the loop sees the same lag.
            self.loop_lag_monitor = self._shared_worker.lag_monitor if self.stage_admission is None and self.priority_lane is None else None
            self.worker_loop_ready.set()
            return

        def run_worker_loop() -> None:
            """Run the event loop in the worker thread"""
            loop = asyncio.new_event_loop()
            with self._worker_loop_lock:
                self.worker_loop = loop
                if self._worker_stop_requested:
                    # run_forever() will return after one pass; the cleanup
                    # in its finally still runs.
                    loop.stop()
            asyncio.set_event_loop(self.worker_loop)

            self._on_worker_loop_started(self.worker_loop)
            self.loop_lag_monitor = loop_lag_monitor_from_env(self.logger, "indexing-worker")
            if self.loop_lag_monitor is not None:
                self.worker_loop.call_soon(self.loop_lag_monitor.start)
            # Set from inside the loop, not before run_forever(): initialize()
            # checks is_running() as soon as this fires.
            self.worker_loop.call_soon(self.worker_loop_ready.set)

            # Run the event loop until stopped
            try:
                self.worker_loop.run_forever()
            finally:
                if self.loop_lag_monitor is not None:
                    self.loop_lag_monitor.stop()
                # Cancel all remaining tasks
                pending = asyncio.all_tasks(self.worker_loop)
                for task in pending:
                    task.cancel()

                # Wait for tasks to complete cancellation
                if pending:
                    self.worker_loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )

                # Dropped with the loop it was bound to: a restart builds a
                # new renewer on the new loop, and leaving this pointing at
                # one whose task is cancelled and whose loop is closed would
                # let a lease set attach to it and silently never renew.
                self.lease_renewer = None
                self.worker_loop.close()
                self.logger.info("Worker thread event loop closed")

        # Reset the ready event
        self.worker_loop_ready.clear()
        self._worker_stop_requested = False

        # Create executor with single worker thread
        self.worker_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="indexing-worker")
        self.worker_executor.submit(run_worker_loop)
        self.logger.info("Worker thread started")

    @override
    async def initialize(self) -> None:
        """Initialize the Kafka consumer and worker thread"""
        consumer = None
        try:
            if not self.kafka_config:
                raise ValueError("Kafka configuration is not valid")


            # Start worker thread first
            self.__start_worker_thread()

            # Wait for worker thread to be ready using threading.Event (more efficient than polling)
            if not self.worker_loop_ready.wait(timeout=60.0):
                raise RuntimeError("Worker thread event loop not initialized in time")

            # Double-check the loop is actually running
            if not self.worker_loop or not self.worker_loop.is_running():
                raise RuntimeError("Worker thread event loop failed to start")

            kafka_dict = IndexingKafkaConsumer.kafka_config_to_dict(self.kafka_config)
            topics = kafka_dict.pop('topics')

            if self._scheduler is not None:
                # Subscribing via the constructor's *topics shortcut cannot
                # attach a rebalance listener (aiokafka only wires one
                # through .subscribe()), and the listener is how buffered
                # messages for a revoked partition get purged -- see
                # _on_partitions_revoked.
                consumer = AIOKafkaConsumer(**kafka_dict)
                consumer.subscribe(
                    topics=topics, listener=_SchedulerRebalanceListener(self)
                )
            else:
                consumer = AIOKafkaConsumer(
                    *topics,
                    **kafka_dict
                )

            await consumer.start()  # type: ignore
            self.consumer = consumer
            auto_commit_status = "enabled" if self.kafka_config.enable_auto_commit else "disabled"
            self.logger.info(f"Successfully initialized aiokafka consumer for indexing (auto-commit: {auto_commit_status})")
        except Exception as e:
            self.logger.error(f"Failed to create consumer: {e}")
            await self.stop()
            raise

    def __stop_worker_thread(self) -> None:
        """Stop the worker thread and its event loop, waiting for active tasks"""
        # First, wait for all active futures to complete with a timeout
        self._wait_for_active_futures()
        if self._shared_worker is not None:
            # The loop is shared: release only what this consumer started on it.
            self._shared_worker.remove_exit_listener(self._stop_taking_work)
            if self.worker_loop is not None and self.worker_loop.is_running():
                renewer = self._shared_worker.call(self._take_lease_renewer)
                if renewer is not None:
                    # Awaited, not scheduled: the resources it renews through close, and a restart
                    # starts a new renewer, only once this one has stopped.
                    try:
                        self._shared_worker.run(renewer.stop(), timeout=LeaseRenewer.STOP_TIMEOUT_S)
                    except TimeoutError:
                        self.logger.warning(
                            "Lease renewer did not stop within %.0fs", LeaseRenewer.STOP_TIMEOUT_S
                        )
            self.worker_loop = None

        with self._worker_loop_lock:
            self._worker_stop_requested = True
            loop = self.worker_loop
        # Requested even when the loop is not running yet: a stop queued before
        # run_forever() makes it return straight away, whereas skipping it
        # would leave the shutdown below waiting on a loop that never ends.
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(loop.stop)
                self.logger.info("Worker thread event loop stop requested")
            except RuntimeError:
                # Closed by the worker between the check and the call.
                self.logger.debug("Worker thread event loop already closed")

        # Shutdown the executor and wait for thread to finish
        if self.worker_executor:
            self.worker_executor.shutdown(wait=True)
            self.logger.info("Worker thread executor shut down")
            self.worker_executor = None
            self.worker_loop = None

        # Clear tracking state
        with self._futures_lock:
            self._active_futures.clear()
        # After the loop is gone no token can admit or release, so any count
        # left is a phantom that would throttle the next start().
        self.gate_waiters.reset()

    def _wait_for_active_futures(self) -> None:
        """Wait for all active futures to complete, bounded by ONE shared timeout.

        Uses concurrent.futures.wait() rather than looping over futures and
        giving each up to shutdown_task_timeout individually — a sequential
        per-future timeout would let N stuck futures (e.g. messages mid
        retry-backoff during an outage, see __delay_if_retry_not_ready) stall
        shutdown for up to N * shutdown_task_timeout instead of a single
        shutdown_task_timeout window.
        """
        with self._futures_lock:
            futures_to_wait = list(self._active_futures)

        if not futures_to_wait:
            self.logger.info("No active futures to wait for during shutdown")
            return

        self.logger.info(f"Waiting for {len(futures_to_wait)} active tasks to complete (timeout: {messaging_env.shutdown_task_timeout}s total)")

        done, not_done = futures_wait(futures_to_wait, timeout=messaging_env.shutdown_task_timeout)

        completed = 0
        errored = 0
        for future in done:
            try:
                future.result()
                completed += 1
            except Exception as e:
                errored += 1
                self.logger.warning(f"Task errored during shutdown: {e}")

        for future in not_done:
            self.logger.warning("Task timed out during shutdown")
            future.cancel()
        timed_out = len(not_done)

        self.logger.info(
            f"Shutdown task cleanup: {completed} completed, {timed_out} timed out, {errored} errored"
        )

    def _get_active_task_count(self) -> int:
        """Get the number of currently active processing tasks"""
        with self._futures_lock:
            return len(self._active_futures)

    def _get_gate_waiter_count(self, tier: ParseTier | None = None) -> int:
        return concurrency.get_gate_waiter_count(self, tier)

    def _dispatch_budget(self) -> concurrency.DispatchBudget:
        """Room to spawn tasks this turn. Per tier only with a scheduler:
        the broker-order path cannot pass over a record to reach another
        tier's, so a tier ceiling there would just stall the partition head."""
        return concurrency.dispatch_budget(self, tiered=self._scheduler is not None)

    def _dispatch_tier_name(self, message: StreamMessage) -> str:
        return concurrency.dispatch_tier(self, message).value

    def dispatch_stats(self) -> dict[str, Any]:
        """Dispatch admission state for the health endpoint."""
        return self._dispatch_budget().as_dict()

    def read_paused_for_admission(self) -> bool:
        """Whether reads are paused because no record could be admitted (not a downstream pause)."""
        return self._admission_paused

    def downstream_read_pause(self) -> float:
        """Seconds every read must still wait out a downstream 429 (see ``consumer_concurrency.downstream_read_pause``)."""
        return concurrency.downstream_read_pause(self, scoped=self._scheduler is not None)

    def admission_explain(self) -> dict[str, Any]:
        """Why this consumer is or is not admitting records (see consumer_concurrency)."""
        return concurrency.admission_explain(self)

    def _slot_pool_of(self, key: FairnessKey) -> str:
        return concurrency.slot_pool_of_key(
            self, key, tier_level=self.fair_scheduler_config.tier_level
        )

    def _slot_share_plan(
        self, can_dispatch: "Callable[[Any], bool] | None" = None
    ) -> SlotSharePlan | None:
        """Each waiting connector's cap on records in flight, for one
        dispatch decision. None when fair scheduling is off.

        A connector counts as waiting in two cases. Its next buffered record
        could be dispatched now (``can_dispatch`` is the dispatch phase's own
        check): one whose head is waiting out a retry backoff cannot take a
        slot, so it must not shrink anyone else's share. Or it has a
        dispatched record that has not reached the gate yet (``started`` but
        not ``admitted``, counted by ``SlotShare.plan``): that record holds
        no earlier place in the gate's arrival order, so without counting it
        another connector would get no cap and fill the gate ahead of it."""
        scheduler = self._scheduler
        slot_share = self._slot_share
        if scheduler is None or slot_share is None:
            return None
        return slot_share.plan(
            scheduler.backlogged_keys(can_dispatch),
            self._slot_pool_of,
            lambda pool: concurrency.slot_pool_limit(self, pool),
        )

    @override
    async def cleanup(self) -> None:
        """Stop the Kafka consumer and clean up resources"""
        try:
            await self.stop()
        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")

    @override
    async def start(  # type: ignore[override]
        self,
        message_handler: IndexingMessageHandler,
    ) -> None:
        """Start consuming messages with the provided handler

        Args:
            message_handler: Async generator function that yields events during processing.
            Expected events: 'parsing_complete', 'indexing_complete'
        """
        try:
            self.running = True
            self.message_handler = message_handler
            self.main_loop = asyncio.get_running_loop()
            self._finished = asyncio.Event()
            # An episode a previous run left open must not hide this run's first WARNING.
            self._backpressure_episode.reset()

            if not self.consumer:
                await self.initialize()

            self.consume_task = spawn_detached(self.__consume_loop())
            self.logger.info(
                f"Started Kafka consumer task with "
                f"parsing_ceiling={concurrency.parse_ceiling(self)}, "
                f"light_parsing_ceiling={concurrency.parse_ceiling(self, ParseTier.LIGHT)}, "
                f"indexing_ceiling={concurrency.index_ceiling(self)}, "
                f"max_pending_tasks={concurrency.pending_task_ceiling(self)}"
            )
        except Exception as e:
            self.logger.error(f"Failed to start Kafka consumer: {str(e)}")
            raise

    @override
    async def stop(self, message_handler: IndexingMessageHandler | None = None) -> None:  # type: ignore[override]
        """Stop consuming messages gracefully.

        Order of operations:
        1. Stop accepting new messages (set running = False)
        2. Cancel the consume loop
        3. Wait for active processing tasks to complete
        4. Stop the worker thread
        5. Stop the Kafka consumer
        """
        self.logger.info("Stopping Kafka consumer...")
        self.running = False

        # Cancel the consume loop task
        if self.consume_task:
            self.consume_task.cancel()
            try:
                await self.consume_task
            except asyncio.CancelledError:
                self.logger.debug("Consume task cancelled")

        # Keep the main loop responsive while worker tasks finish. They bridge
        # commits, Redis leases, and retry tracking back onto this loop.
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, self.__stop_worker_thread)
        except Exception as exc:
            self.logger.error("Error stopping worker thread: %s", exc)

        # Stop the Kafka consumer last
        if self.consumer:
            try:
                consumer = self.consumer
                self.consumer = None
                await consumer.stop()
                self.logger.info("Kafka consumer stopped")
            except Exception as e:
                self.logger.error(f"Error stopping Kafka consumer: {e}")
        self._lane_paused.clear()
        self._deferred_messages.clear()
        if self._remembered is not None:
            self._remembered.clear()
        if self._fetch_back_task is not None:
            self._fetch_back_task.cancel()
            with contextlib.suppress(BaseException):
                await self._fetch_back_task
            self._fetch_back_task = None
        if self._offset_fetcher is not None:
            await self._offset_fetcher.close()
            self._offset_fetcher = None
        if self._waiting_fetch_task is not None:
            self._waiting_fetch_task.cancel()
            with contextlib.suppress(BaseException):
                await self._waiting_fetch_task
            self._waiting_fetch_task = None
            self._waiting_fetch_plan = ({}, {})
            self._waiting_fetch_reserved = 0
        if self._waiting_fetcher is not None:
            await self._waiting_fetcher.close()
            self._waiting_fetcher = None
        with self._partition_lock:
            self._in_flight_partitions.clear()
            self._deferred_partition_offsets.clear()
            self._in_flight_records.clear()

        # Never committed: their offsets floor the watermark, so a restart reads them again.
        waited = self.__forget_record_waits(lambda _tp: False)
        if waited:
            self.logger.info(
                "Discarded %d event(s) waiting for a busy record at shutdown (redelivered on restart)",
                waited,
            )
        if self._scheduler is not None:
            drained = self._scheduler.drain_all()
            self._record_scheduled.clear()
            if drained:
                self.logger.info(
                    f"Discarded {len(drained)} buffered message(s) from the "
                    "fair-scheduling queue at shutdown (redelivered on restart)"
                )

        # concurrency_manager/retry_manager are injected, not owned — closing
        # them here would break a restart (start() -> stop() -> start() reuses
        # the same instances) and duplicate indexing_main's own cleanup of
        # them. The creator (start_kafka_consumers/stop_kafka_consumers) is
        # responsible for their lifecycle.

    @override
    def is_running(self) -> bool:
        """Check if consumer is running"""
        return self.running

    @override
    async def lane_backlog(self, topic: str) -> LaneBacklog:
        return await concurrency.bridge_to_main_loop(
            self, self._read_lane_backlog(topic), _BACKLOG_BRIDGE_TIMEOUT
        )

    async def _read_lane_backlog(self, topic: str) -> LaneBacklog:
        if self.consumer is None:
            raise RuntimeError("Kafka consumer is not started")
        partitions = self.consumer.partitions_for_topic(topic)
        if not partitions:
            raise RuntimeError(f"No partition metadata for {topic}")
        client_config = {
            name: value
            for name, value in IndexingKafkaConsumer.kafka_config_to_dict(
                self.kafka_config
            ).items()
            if name not in ("topics", "enable_auto_commit", "auto_offset_reset")
        }
        client_config["client_id"] = f"{tagged(self.kafka_config.client_id)}-backlog"
        oldest = await read_partition_backlog(
            client_config,
            topic,
            partitions,
            auto_offset_reset=self.kafka_config.auto_offset_reset,
            timeout_seconds=_BACKLOG_READ_TIMEOUT,
        )
        # No lanes_for_event: the broker's partitioner placed each message, and
        # by design nothing here recomputes it (see lanes/interface.py), so an
        # event may be waiting on any partition.
        return LaneBacklog(topic, oldest)

    async def _on_partitions_revoked(self, revoked: "list[TopicPartition]") -> None:
        """Drop buffered (not-yet-dispatched) messages for partitions this
        consumer no longer owns, so they are redelivered to the new owner
        instead of dispatched against a partition we can't commit for.

        Runs on the main loop, before the rebalance proceeds and before the
        coordinator lets a new owner start fetching -- exactly where
        aiokafka expects any pre-rebalance cleanup to happen.
        """
        if self._scheduler is None or self._offset_tracker is None:
            return
        revoked_set = set(revoked)
        if not revoked_set:
            return
        self._revoked_since_poll.update(revoked_set)
        for tp in revoked_set:
            self._partition_epoch[tp] = self._partition_epoch.get(tp, 0) + 1

        purged = self._scheduler.purge(lambda item: item[0] in revoked_set)
        for item in purged:
            self.__unschedule(item)
        for tp in revoked_set:
            self._lane_paused.pop(tp, None)
        self._deferred_messages = deque(
            entry for entry in self._deferred_messages if entry[0] not in revoked_set
        )
        waited = self.__forget_record_waits(lambda tp: tp not in revoked_set)
        if waited:
            self.logger.info(
                "Rebalance: dropped %d event(s) waiting for a busy record on revoked "
                "partition(s); their new owner reads them from the committed offset",
                waited,
            )
        forgotten = (
            self._remembered.drop_partitions(revoked_set)
            if self._remembered is not None
            else 0
        )
        if forgotten:
            self.logger.info(
                "Rebalance: forgot %d remembered position(s) on revoked "
                "partition(s); their new owner reads them from the committed offset",
                forgotten,
            )
        with self._partition_lock:
            for tp in revoked_set:
                self._in_flight_partitions.discard(tp)
                self._deferred_partition_offsets.pop(tp, None)
        for tp in revoked_set:
            self._offset_tracker.revoke(tp)

        if purged:
            self.logger.info(
                "Rebalance: dropped %d buffered message(s) for %d revoked "
                "partition(s); redelivered to new owner",
                len(purged),
                len(revoked_set),
            )

    async def _on_partitions_assigned(self, assigned: "list[TopicPartition]") -> None:
        if assigned:
            self.logger.info(
                "Rebalance: assigned %d partition(s): %s",
                len(assigned),
                assigned,
            )

    def __apply_backpressure(self) -> None:
        """Pause or resume Kafka partitions based on active task capacity
        and downstream service health.

        This ensures getmany() is always called (keeping the consumer alive
        and resetting max_poll_interval_ms), while preventing new messages
        from being returned when at capacity OR when a downstream service
        every record calls (parsing/embedding) last signalled 429+Retry-After
        via ``backpressure_coordinator`` — pulling more work in that case would
        just queue up behind the same saturated service. With the fair
        scheduler a Docling 429 leaves the partitions read: the dispatch phase
        holds the records that call Docling (``downstream_read_pause``).
        """
        budget = self._dispatch_budget()
        downstream_paused = self.downstream_read_pause() > 0.0

        # Saturation matters as much as queue depth: with both index pools
        # full and nothing queued behind them, the waiter count reads zero
        # while the node cannot start a single further record. Claiming more
        # then only holds partitions this consumer cannot serve.
        saturated = concurrency.index_gates_saturated(self)
        # A full DRR buffer is a third capacity signal alongside queue depth
        # and saturation: getmany() still has to be called every iteration
        # (it resets max_poll_interval_ms), but pausing every partition here
        # means it returns nothing new instead of reading messages the
        # scheduler would just reject with BUFFER_FULL.
        # Parked messages hold the same budget as buffered ones, so they have
        # to count here too. Reading only pending_count leaves the partitions
        # unpaused once parking has taken the remaining room, and the read
        # phase then polls, gets a message it cannot hold, and seeks it back
        # again on every iteration.
        scheduler_full = self._scheduler is not None and not self.__buffer_has_room()
        if budget.blocked or downstream_paused or saturated or scheduler_full:
            # Pause partitions that aren't already paused
            assigned = self.consumer.assignment()
            not_paused = assigned - self.consumer.paused()
            if not_paused:
                self.consumer.pause(*not_paused)
            self._admission_paused = bool(budget.blocked or saturated or scheduler_full)
            if self._backpressure_episode.engaged():
                if downstream_paused:
                    self.logger.warning(
                        "Downstream backpressure from %s: pausing Kafka "
                        "partition reads for %.1fs",
                        ", ".join(concurrency.read_pausing_services(self, scoped=self._scheduler is not None)),
                        self.downstream_read_pause(),
                    )
                elif saturated:
                    # Distinct from the queue-depth case below: here the
                    # waiter count is typically zero — every index permit is
                    # out, so there is nothing queued and nothing this
                    # consumer could start with another message.
                    self.logger.warning(
                        "Backpressure engaged: both index pools saturated "
                        "(no permit free to start another record); pausing "
                        "Kafka partition reads"
                    )
                elif scheduler_full:
                    self.logger.warning(
                        "Backpressure engaged: fair-scheduling buffer full "
                        "(%d/%d messages); pausing Kafka partition reads",
                        self._scheduler.pending_count  # type: ignore[union-attr]
                        + self.__held_beside_scheduler(),
                        self.fair_scheduler_config.max_buffered_messages,
                    )
                else:
                    self.logger.warning(
                        "Backpressure engaged: tasks waiting for indexing "
                        "admission %s; pausing Kafka partition reads",
                        budget.describe(),
                    )
        else:
            # A partition remains paused while one of its messages is in flight;
            # this preserves Kafka's per-partition processing/commit order.
            paused = self.consumer.paused()
            with self._partition_lock:
                in_flight_partitions = set(self._in_flight_partitions)
            # Lane pauses outlive a global backpressure clear: the buffer as a
            # whole having room says nothing about whether the key that
            # blocked this particular lane has drained.
            resumable = {
                tp for tp in paused - in_flight_partitions - set(self._lane_paused) if not self.__is_held(tp)
            }
            if resumable:
                self.consumer.resume(*resumable)
            self._admission_paused = False
            if self._backpressure_episode.cleared(time.monotonic()):
                self.logger.info("Backpressure cleared: %s", budget.describe())

    def __parallel_dispatch(self) -> bool:
        """Whether several records from one partition may run at once.

        Gated on the scheduler being enabled, not just on the flag: the
        commit watermark is what makes out-of-order completion within a
        partition safe, and without it this would commit past unfinished
        work.
        """
        return (
            self._scheduler is not None
            and self.fair_scheduler_config.parallel_partitions
        )

    def __record_key(
        self, message: ConsumerRecord, parsed: StreamMessage
    ) -> str:
        """The unit parallel dispatch serialises on.

        Deliberately the same identity ``__process_message_wrapper`` takes the
        ``record:`` lease on, so the in-process check and the cluster-wide
        lease agree on what "the same record" means. Events with no
        ``recordId`` (bulk deletes, collection drops) fall back to their
        stable message id, which is unique per message -- they are not
        per-record work and nothing needs serialising.
        """
        return concurrency.work_key(parsed.payload, str(self._get_stable_message_id(message, parsed)))

    def __reserve_record(self, record_key: str) -> bool:
        """Claim a record for dispatch, so no second event for the same
        record runs beside it.

        This replaces -- rather than merely relaxes -- the per-partition
        rule. Letting a partition run several at once without this check would
        put two different events for one record (a create and its update) into
        contention for the cluster-wide ``record:`` lease, and the loser would
        spend an index slot waiting before it is handed back.
        """
        with self._partition_lock:
            if record_key in self._in_flight_records:
                return False
            self._in_flight_records.add(record_key)
        return True

    def __held_beside_scheduler(self) -> int:
        """Buffer places the scheduler does not count: messages parked for room, waiting payloads, and
        the places a read-back in progress will fill."""
        return len(self._deferred_messages) + self._record_waiting_count + self._waiting_fetch_reserved

    def __schedule(
        self,
        key: FairnessKey,
        item: tuple[TopicPartition, ConsumerRecord, StreamMessage],
        not_before: float | None,
    ) -> EnqueueResult:
        """Enqueue into the scheduler, remembering the item under its record (see ``__scheduled_before``)."""
        result = self._scheduler.enqueue(key, item, not_before=not_before)  # type: ignore[union-attr]
        if result == EnqueueResult.ACCEPTED:
            tp, message, parsed = item
            positions = self._record_scheduled.setdefault(self.__record_key(message, parsed), {})
            position = (tp, message.offset)
            positions[position] = positions.get(position, 0) + 1
        return result

    def __unschedule(self, item: tuple[TopicPartition, ConsumerRecord, StreamMessage]) -> None:
        tp, message, parsed = item
        record_key = self.__record_key(message, parsed)
        positions = self._record_scheduled.get(record_key)
        if positions is None:
            return
        position = (tp, message.offset)
        left = positions.get(position, 0) - 1
        if left > 0:
            positions[position] = left
        else:
            positions.pop(position, None)
        if not positions:
            del self._record_scheduled[record_key]

    def __scheduled_before(self, record_key: str, tp: TopicPartition, offset: int) -> bool:
        """Whether the scheduler still holds an older event of this record on ``tp``.

        A leaf is judged by its head, so an older event can be held back in
        another tier's leaf (at its ceiling, or waiting out a retry) while a
        younger one leaves the scheduler.
        """
        positions = self._record_scheduled.get(record_key)
        return positions is not None and any(p_tp == tp and p_offset < offset for p_tp, p_offset in positions)

    def __waits_for_record(
        self,
        record_key: str,
        item: tuple[TopicPartition, ConsumerRecord, StreamMessage],
        key: FairnessKey,
    ) -> bool:
        """Park a dequeued event whose record is busy, or which an older event of its record must precede.

        Called with no await between the dequeue and the reservation, so the
        in-flight check and ``__reserve_record`` see the same set.
        """
        tp, message, _ = item
        offered = self._record_offered.get(record_key)
        is_offered = offered is item
        if is_offered:
            del self._record_offered[record_key]
        with self._partition_lock:
            busy = record_key in self._in_flight_records
        waiting = self._record_waiting.get(record_key)
        older_scheduled = self.__scheduled_before(record_key, tp, message.offset)
        must_wait = (
            busy
            or older_scheduled
            or (offered is not None and not is_offered)
            or (bool(waiting) and (not is_offered or _arrived_before(waiting[0], tp, message.offset)))
        )
        if not must_wait:
            return False
        if waiting is None:
            waiting = self._record_waiting[record_key] = deque()
        entry = _WaitingEvent(tp, message.offset, item, key)
        by_offset = not self.__may_hold_waiting_payload(record_key)
        if by_offset:
            # A re-buffered retry's backoff is stamped on the payload in memory, not in the bytes read back.
            entry.not_before = self.__retry_not_before(item[2])
            entry.item = entry.key = None
        waiting.insert(_insertion_point(waiting, tp, message.offset), entry)
        self.__count_waiting(record_key, entry, 1)
        # With an older event still in the scheduler, that one parks or runs
        # first when it leaves; offering this one meanwhile would only park it again.
        if not busy and not older_scheduled and record_key not in self._record_offered:
            self._records_released.add(record_key)
        if by_offset:
            self.logger.info(
                "Record %s is busy: event %s-%s-%s waits for it by offset only, its payload "
                "to be read back when the record is free (%d waiting for this record)",
                record_key, tp.topic, tp.partition, message.offset, len(waiting),
            )
        else:
            self.logger.info(
                "Record %s is busy: event %s-%s-%s waits for it, not for its connector (%d waiting for this record)",
                record_key, tp.topic, tp.partition, message.offset, len(waiting),
            )
        return True

    def __wait_by_offset(self, message: ConsumerRecord, parsed: StreamMessage) -> bool:
        """Keep a failed hand-back for its record by offset only, when the buffer has no room for its payload.

        Its record is still in flight (the hand-back runs inside its delivery),
        so it is read back when that delivery ends, like any event kept by
        offset. Its offset stays tracked, so the watermark floors at it. False
        when parallel dispatch is off or the offset budget is spent, and the
        caller holds the payload as before.
        """
        if not self.__parallel_dispatch() or self._record_waiting_positions >= _WAITING_POSITIONS_BUDGET:
            return False
        record_key = self.__record_key(message, parsed)
        tp = TopicPartition(message.topic, message.partition)
        entry = _WaitingEvent(tp, message.offset, None, None, self.__retry_not_before(parsed))
        waiting = self._record_waiting.setdefault(record_key, deque())
        waiting.insert(_insertion_point(waiting, tp, message.offset), entry)
        self.__count_waiting(record_key, entry, 1)
        with self._partition_lock:
            busy = record_key in self._in_flight_records
        if not busy and record_key not in self._record_offered:
            self._records_released.add(record_key)
        self.logger.info(
            "Record %s: its retry %s-%s-%s, which could not be re-published, waits by offset only, "
            "the buffer being full (%d waiting for this record)",
            record_key, tp.topic, tp.partition, message.offset, len(waiting),
        )
        return True

    def __may_hold_waiting_payload(self, record_key: str) -> bool:
        """Whether one more waiting event may keep its payload in memory.

        Held payloads count against the buffer, so a burst of events for one
        busy record would otherwise fill it and pause reads for every
        connector until that record finishes. Past these caps only the offset
        is kept, which costs no buffer room.
        """
        if self._record_waiting_positions >= _WAITING_POSITIONS_BUDGET:
            if not self._waiting_positions_logged:
                self.logger.warning(
                    "%d events already wait by offset only for busy records; further ones keep "
                    "their payload and count against the buffer",
                    self._record_waiting_positions,
                )
                self._waiting_positions_logged = True
            return True
        held_cap = max(1, self.fair_scheduler_config.max_buffered_messages // _WAITING_HELD_DIVISOR)
        return (
            self._record_waiting_count < held_cap
            and self._record_waiting_held.get(record_key, 0) < _WAITING_HELD_PER_RECORD
        )

    def __count_waiting(self, record_key: str, entry: "_WaitingEvent", sign: int) -> None:
        if entry.item is None:
            self._record_waiting_positions += sign
            return
        self._record_waiting_count += sign
        held = self._record_waiting_held.get(record_key, 0) + sign
        if held > 0:
            self._record_waiting_held[record_key] = held
        else:
            self._record_waiting_held.pop(record_key, None)

    def __drop_waiting(self, record_key: str) -> None:
        """Forget a record with nothing left waiting."""
        self._record_waiting.pop(record_key, None)
        self._record_waiting_held.pop(record_key, None)
        self._records_released.discard(record_key)

    def __reoffer_released(self) -> None:
        """Give each released record's oldest waiting event back to the scheduler.

        One at a time per record: the next waits until that one has run. An
        event the scheduler has no room for stays waiting, and is offered
        again on the next pass. An oldest event kept by offset only stays
        released until ``__fetch_back_waiting`` has read its payload back.
        """
        scheduler = self._scheduler
        if not self._records_released or scheduler is None:
            return
        for record_key in list(self._records_released):
            waiting = self._record_waiting.get(record_key)
            with self._partition_lock:
                busy = record_key in self._in_flight_records
            if not waiting or busy or record_key in self._record_offered:
                # A busy record's finish releases it again.
                self._records_released.discard(record_key)
                continue
            head = waiting[0]
            if self.__scheduled_before(record_key, head.tp, head.offset):
                # The older event parks in front of it when it leaves the scheduler, and releases the record.
                self._records_released.discard(record_key)
                continue
            if head.item is None or head.key is None:
                continue
            item, key = head.item, head.key
            tp, message, parsed = item
            result = self.__schedule(key, item, self.__retry_not_before(parsed))
            if result != EnqueueResult.ACCEPTED:
                self.logger.debug(
                    "No room to re-offer event %s-%s-%s of record %s yet (%s)",
                    tp.topic, tp.partition, message.offset, record_key, result.value,
                )
                continue
            waiting.popleft()
            head.alive = False
            self.__count_waiting(record_key, head, -1)
            if not waiting:
                self.__drop_waiting(record_key)
            self._record_offered[record_key] = item
            self._records_released.discard(record_key)
            self.logger.info(
                "Record %s is free: re-offered its waiting event %s-%s-%s (%d more waiting)",
                record_key, tp.topic, tp.partition, message.offset, len(waiting),
            )

    def __forget_record_waits(self, keep: "Callable[[TopicPartition], bool]") -> int:
        """Drop waiting and offered events on partitions ``keep`` rejects; return how many waited."""
        dropped = 0
        for record_key in list(self._record_waiting):
            waiting = self._record_waiting[record_key]
            kept: deque[_WaitingEvent] = deque()
            for entry in waiting:
                if keep(entry.tp):
                    kept.append(entry)
                else:
                    entry.alive = False
                    self.__count_waiting(record_key, entry, -1)
                    dropped += 1
            if kept:
                self._record_waiting[record_key] = kept
                # What held this record back (an older event in the scheduler,
                # an offered one) may have gone with the revoked partitions,
                # and nothing else would release it; the re-offer checks again.
                self._records_released.add(record_key)
            else:
                self.__drop_waiting(record_key)
        for record_key, item in list(self._record_offered.items()):
            if not keep(item[0]):
                del self._record_offered[record_key]
                if record_key in self._record_waiting:
                    self._records_released.add(record_key)
        return dropped

    def __waiting_fetcher(self) -> OffsetFetcher:
        # A reader of its own: the remembered-offsets fetch-back runs in its
        # own task, and one reader cannot be assigned and sought for both.
        if self._waiting_fetcher is None:
            client_config = {
                name: value
                for name, value in IndexingKafkaConsumer.kafka_config_to_dict(self.kafka_config).items()
                if name not in ("topics", "group_id", "enable_auto_commit", "auto_offset_reset")
            }
            client_config["client_id"] = f"{tagged(self.kafka_config.client_id)}-record-fetch-back"
            self._waiting_fetcher = OffsetFetcher(client_config, self.logger)
        return self._waiting_fetcher

    async def __fetch_back_waiting(self) -> None:
        """Read back the payloads of released records' oldest events that waited by offset only.

        Only a released record's, from its oldest event on: one read back
        while its record is still busy would only wait again, holding the
        memory the cap exists to bound. Runs in the background like the
        remembered-offsets fetch-back; its results are taken on a later pass.
        The places the payloads will take are reserved while it runs, so a
        read cannot fill them first.
        """
        task = self._waiting_fetch_task
        if task is not None:
            if not task.done():
                return
            self._waiting_fetch_task = None
            plan, epochs = self._waiting_fetch_plan
            self._waiting_fetch_plan = ({}, {})
            self._waiting_fetch_reserved = 0
            try:
                found, gone = task.result()
            except Exception as e:
                self.logger.warning(
                    "Could not read back %d event(s) that waited for a busy record by offset; "
                    "trying again on the next pass: %r",
                    sum(len(p) for p in plan.values()),
                    e,
                )
                return
            await self.__take_fetched_waiting(plan, epochs, found, gone)
            return

        scheduler = self._scheduler
        if not self._records_released or scheduler is None:
            return
        room = (
            self.fair_scheduler_config.max_buffered_messages
            - scheduler.pending_count
            - self.__held_beside_scheduler()
        )
        plan: dict[str, list[_WaitingEvent]] = {}
        for record_key in list(self._records_released):
            waiting = self._record_waiting.get(record_key)
            if not waiting or waiting[0].item is not None or record_key in self._record_offered:
                continue
            with self._partition_lock:
                if record_key in self._in_flight_records:
                    continue
            if room <= 0:
                # The record's own younger payloads can be what fills the
                # buffer; nothing else frees it while its oldest event cannot run.
                room += self.__make_room_for_oldest(record_key, waiting)
                if room <= 0:
                    continue
            entries: list[_WaitingEvent] = []
            for entry in waiting:
                if entry.item is not None or len(entries) >= min(_WAITING_HELD_PER_RECORD, room):
                    break
                entries.append(entry)
            plan[record_key] = entries
            room -= len(entries)
        if not plan:
            return
        wanted: dict[TopicPartition, list[int]] = {}
        for entries in plan.values():
            for entry in entries:
                wanted.setdefault(entry.tp, []).append(entry.offset)
        for record_key, entries in plan.items():
            self.logger.info(
                "Record %s is free: reading back %d event(s) that waited for it by offset only, from %s-%s-%s",
                record_key, len(entries), entries[0].tp.topic, entries[0].tp.partition, entries[0].offset,
            )
        self._waiting_fetch_plan = (plan, {tp: self._partition_epoch.get(tp, 0) for tp in wanted})
        self._waiting_fetch_reserved = sum(len(entries) for entries in plan.values())
        self._waiting_fetch_task = spawn_detached(
            asyncio.wait_for(
                self.__waiting_fetcher().fetch(wanted, timeout_seconds=_FETCH_BACK_READ_SECONDS),
                timeout=_FETCH_BACK_TIMEOUT_SECONDS,
            )
        )

    def __make_room_for_oldest(self, record_key: str, waiting: "deque[_WaitingEvent]") -> int:
        """Keep the youngest held payload of this record by offset only instead; return the places freed."""
        for entry in reversed(waiting):
            if entry is waiting[0]:
                return 0
            if entry.item is not None:
                self.__count_waiting(record_key, entry, -1)
                entry.not_before = self.__retry_not_before(entry.item[2])
                entry.item = entry.key = None
                self.__count_waiting(record_key, entry, 1)
                self.logger.info(
                    "Record %s: event %s-%s-%s now waits by offset only, to make room to read back "
                    "its oldest waiting event %s-%s-%s",
                    record_key, entry.tp.topic, entry.tp.partition, entry.offset,
                    waiting[0].tp.topic, waiting[0].tp.partition, waiting[0].offset,
                )
                return 1
        return 0

    async def __take_fetched_waiting(
        self,
        plan: dict[str, list[_WaitingEvent]],
        epochs: dict[TopicPartition, int],
        found: dict[tuple[TopicPartition, int], ConsumerRecord],
        gone: set[tuple[TopicPartition, int]],
    ) -> None:
        """Put read-back payloads into their waiting entries in place, so each record's order is unchanged."""
        parsed_at: dict[tuple[TopicPartition, int], StreamMessage] = {}
        # Parsed when first read, but no longer (an envelope key gone since):
        # left waiting it would be read back on every pass and hold its
        # record's later events for good, so it is settled as a fresh read
        # settles one that never parses.
        poison: set[tuple[TopicPartition, int]] = set()
        for entries in plan.values():
            for entry in entries:
                position = (entry.tp, entry.offset)
                if position not in found:
                    continue
                try:
                    parsed = await self.__parse_message(found[position])
                except Exception as e:
                    self.logger.warning(
                        "Could not parse read-back event %s-%s; it keeps waiting by offset",
                        position[0], position[1], extra={"error_type": type(e).__name__},
                    )
                    continue
                if parsed is not None:
                    parsed_at[position] = parsed
                else:
                    poison.add(position)

        # No awaits from here until the deleted offsets are committed, so the
        # entries and epochs checked below cannot change underneath.
        deleted: list[tuple[TopicPartition, int]] = []
        unparseable: list[tuple[str, _WaitingEvent]] = []
        for record_key, entries in plan.items():
            waiting = self._record_waiting.get(record_key)
            for entry in entries:
                tp = entry.tp
                position = (tp, entry.offset)
                # Revoked since the fetch began: the revocation dropped the
                # entry, or the new assignment's re-read made a fresh one.
                if (
                    not entry.alive
                    or entry.item is not None
                    or waiting is None
                    or tp in self._revoked_since_poll
                    or self._partition_epoch.get(tp, 0) != epochs.get(tp)
                ):
                    continue
                if position in poison:
                    # Stays queued until settled, so a failure on the way
                    # leaves it to be read back and settled again.
                    unparseable.append((record_key, entry))
                    continue
                if position in gone:
                    self.__remove_waiting(record_key, waiting, entry)
                    deleted.append(position)
                    continue
                parsed = parsed_at.get(position)
                if parsed is None:
                    continue
                if entry.not_before is not None:
                    parsed.payload["_retry_not_before"] = entry.not_before
                self.__count_waiting(record_key, entry, -1)
                entry.item = (tp, found[position], parsed)
                entry.key = self.key_extractor.extract(parsed)
                self.__count_waiting(record_key, entry, 1)
                self.logger.info(
                    "Record %s: read back its waiting event %s-%s-%s",
                    record_key, tp.topic, tp.partition, entry.offset,
                )
        if deleted:
            self.logger.warning(
                "%d event(s) waiting for a busy record were deleted by Kafka retention before "
                "they could be read back; the stranded-record sweep re-sends a record only while "
                "it is still waiting to be indexed",
                len(deleted),
            )
            await self.__commit_resolved(deleted)
        await self.__settle_unparseable(unparseable, found)
        self.__reoffer_released()

    def __remove_waiting(self, record_key: str, waiting: "deque[_WaitingEvent]", entry: _WaitingEvent) -> None:
        waiting.remove(entry)
        entry.alive = False
        self.__count_waiting(record_key, entry, -1)
        if not waiting:
            self.__drop_waiting(record_key)

    async def __settle_unparseable(
        self,
        unparseable: list[tuple[str, _WaitingEvent]],
        found: dict[tuple[TopicPartition, int], ConsumerRecord],
    ) -> None:
        """Settle read-back events that no longer parse, as poison.

        Each is reported to the disposition sink first, as a fresh poison read
        is. That report is best effort: ``notify_abandoned`` logs a failing
        sink and carries on, so a sink failure does not keep the event. Only
        an unexpected error on the way keeps it queued, to be tried again on a
        later pass. Those reported are then marked done together, before the
        one commit, so a failed broker commit strands none of them: the next
        commit carries them.
        """
        settled: list[tuple[TopicPartition, int]] = []
        for record_key, entry in unparseable:
            message = found[(entry.tp, entry.offset)]
            message_id = f"{entry.tp.topic}-{entry.tp.partition}-{entry.offset}"
            try:
                await self.__abandon_message(
                    message_id,
                    self._get_stable_message_id(message, None),
                    None,
                    reason="terminal error",
                    attempts=1,
                )
            except Exception as e:
                self.logger.warning(
                    "Could not settle event %s of record %s, which no longer parses; it keeps waiting "
                    "and is tried again: %r",
                    message_id, record_key, e,
                )
                continue
            # The sink's await can span a revocation, which dropped the entry.
            if not entry.alive:
                continue
            waiting = self._record_waiting.get(record_key)
            if waiting is not None:
                self.__remove_waiting(record_key, waiting, entry)
            self.logger.error(
                "Event %s, which waited for record %s, no longer parses; settled as poison",
                message_id, record_key,
            )
            settled.append((entry.tp, entry.offset))
        if settled:
            await self.__commit_resolved(settled)

    def __reserve_partition(self, message: ConsumerRecord) -> bool:
        # Only one message per partition is ever in flight at a time (Kafka
        # ordering), so real concurrency is capped by min(MAX_CONCURRENT_*,
        # partition count) — raising the semaphore limits without also
        # increasing the topic's partition count won't raise throughput.
        topic_partition = TopicPartition(message.topic, message.partition)
        with self._partition_lock:
            if topic_partition in self._in_flight_partitions:
                self.__defer_offset(topic_partition, message.offset)
                return False
        # Pause first: it raises for a partition a rebalance revoked during the parse, and nothing is reserved yet.
        self.consumer.pause(topic_partition)
        with self._partition_lock:
            self._in_flight_partitions.add(topic_partition)
        return True

    def __defer_offset(self, topic_partition: TopicPartition, offset: int) -> None:
        """Remember the lowest offset to re-read once the partition's in-flight message ends. Hold ``_partition_lock``."""
        current = self._deferred_partition_offsets.get(topic_partition)
        self._deferred_partition_offsets[topic_partition] = offset if current is None else min(current, offset)

    def __hold_partition(self, tp: TopicPartition, offset: int, delay_s: float | None = None) -> None:
        """Pause ``tp`` for ``delay_s`` (a re-queued head not yet due), or for the backoff of another failure of ``offset``.
        Only failures count toward the backoff. The seek is the caller's."""
        if self.consumer is None:
            return
        held = self._held.get(tp)
        failures = held[1] if held is not None and held[0] == offset else 0
        if delay_s is None:
            failures += 1
            delay_s = _stuck_retry_delay_s(failures)
        try:
            self.consumer.pause(tp)
        except Exception as e:  # revoked during the parse: its next owner reads it from the commit
            self.logger.warning("Failed to pause %s for a retry backoff: %s", tp, e)
            return
        self._held[tp] = (offset, failures, time.monotonic() + delay_s)

    def __is_held(self, tp: TopicPartition) -> bool:
        held = self._held.get(tp)
        return held is not None and time.monotonic() < held[2]

    def __end_holds_read(self, batch: dict[TopicPartition, list[ConsumerRecord]] | None) -> None:
        """A partition getmany() returned was not paused: a reassignment or a restart dropped its hold. Keep the count."""
        for tp in batch or ():
            held = self._held.get(tp)
            if held is not None and held[2] > 0:
                self._held[tp] = (held[0], held[1], 0.0)

    def __redeliver_from(self, message: ConsumerRecord) -> None:
        """Re-read this partition from ``message`` on: now if nothing is in flight, else when the in-flight one ends.

        For a message that never reserved the partition. Releasing another message's reservation here would let a later
        offset run and commit beside it, and its finish would seek over this offset.
        """
        topic_partition = TopicPartition(message.topic, message.partition)
        with self._partition_lock:
            if topic_partition in self._in_flight_partitions:
                self.__defer_offset(topic_partition, message.offset)
                return
        if topic_partition not in self._rewound_since_poll:  # a finish already seeked lower
            self.__seek_back(topic_partition, message.offset)

    def __finish_partition(
        self,
        message: ConsumerRecord,
        retry_current: bool,
        record_key: str | None = None,
    ) -> None:
        self._finished.set()
        if record_key is not None:
            # Parallel dispatch: the partition was never reserved or paused
            # for this message, so there is nothing to resume. Lane pauses and
            # global backpressure own the read side.
            with self._partition_lock:
                self._in_flight_records.discard(record_key)
            if record_key in self._record_waiting:
                self._records_released.add(record_key)
                self.__reoffer_released()
            return

        topic_partition = TopicPartition(message.topic, message.partition)
        with self._partition_lock:
            self._in_flight_partitions.discard(topic_partition)
            deferred_offset = self._deferred_partition_offsets.pop(
                topic_partition,
                None,
            )

        retry_offset = message.offset if retry_current else None
        if deferred_offset is not None:
            retry_offset = (
                deferred_offset
                if retry_offset is None
                else min(retry_offset, deferred_offset)
            )

        if self.consumer is None:
            return
        # Never seek while the scheduler holds a buffer: offsets above this
        # one are already buffered or dispatched, so rewinding would re-read
        # and re-enqueue every one of them. Under fair scheduling a failed
        # delivery is settled by its watermark claim (re-queued, dead-lettered
        # or floored for redelivery) instead, and the read position belongs
        # solely to the read phase.
        if retry_offset is not None and self._scheduler is None:
            self.consumer.seek(topic_partition, retry_offset)
            self._rewound_since_poll.add(topic_partition)
        if (
            self.running
            and self.downstream_read_pause() <= 0.0
            and topic_partition not in self._lane_paused
            and not self._dispatch_budget().blocked
            and not self.__is_held(topic_partition)
        ):
            self.consumer.resume(topic_partition)

    async def __consume_loop(self) -> None:
        """Main consumption loop with dual semaphore control"""
        try:
            self.logger.info("Starting Kafka consumer loop")
            while self.running:
                try:
                    self.__apply_backpressure()

                    if self._scheduler is not None:
                        await self.__sweep_stale_offsets()
                        self.__resume_drained_lanes()
                        await self.__read_phase()
                        await self.__dispatch_phase()
                        self.__publish_scheduler_metrics()
                        continue

                    available_capacity = max(1, self._dispatch_budget().remaining)

                    message_batch = await self.consumer.getmany(
                        timeout_ms=messaging_env.message_timeout_ms,
                        max_records=min(
                            max(1, messaging_env.message_batch_size_indexing),
                            available_capacity,
                        ),
                    )  # type: ignore
                    self._rewound_since_poll.clear()
                    self.__end_holds_read(message_batch)

                    if not message_batch:
                        continue

                    for messages in message_batch.values():
                        for message in messages:
                            # Check if we should stop before processing
                            if not self.running:
                                self.logger.info("Consumer stopping, skipping remaining messages in batch")
                                break

                            reserved = False
                            try:
                                self.logger.debug(f"Received message: topic={message.topic}, partition={message.partition}, offset={message.offset}")
                                deferred, parsed_message = (
                                    await self.__defer_if_retry_not_ready(message)
                                )
                                if deferred:
                                    # Not ready: seeked back already, and
                                    # held until it is due. Kafka's
                                    # per-partition ordering means we can't
                                    # skip ahead to later messages in this
                                    # partition anyway, so stop draining this
                                    # partition's batch, without spending a
                                    # worker-thread/active-task slot on a
                                    # multi-minute sleep in the meantime.
                                    break
                                # One message per partition in flight: the rest of the batch is re-read
                                # from here when it ends, so nothing behind it may run or commit first.
                                if not self.__reserve_partition(message):
                                    break
                                reserved = True
                                # The read-time check also runs here, with the fair scheduler off: the one
                                # message this iteration holds is handed back before anything is downloaded.
                                if await self.__hand_back_one(message, parsed_message):
                                    self.__finish_partition(message, retry_current=False)
                                    reserved = False
                                    continue
                                await self.__start_processing_task(
                                    message, parsed_message
                                )
                            except Exception as e:
                                self.__hold_partition(
                                    TopicPartition(message.topic, message.partition), message.offset
                                )
                                if reserved:
                                    self.__finish_partition(message, retry_current=True)
                                else:
                                    self.__redeliver_from(message)
                                # The type only: a parse error's text can quote the payload's values.
                                self.logger.error(
                                    f"Error processing message {message.topic}-{message.partition}-{message.offset}",
                                    extra={"error_type": type(e).__name__},
                                )
                                break

                except asyncio.CancelledError:
                    self.logger.info("Kafka consumer task cancelled")
                    break
                except Exception as e:
                    self.logger.error(f"Error in consume_messages loop: {e}")
                    if self.running:
                        await asyncio.sleep(1)

        except Exception as e:
            self.logger.error(f"Fatal error in consume_messages: {e}")
        finally:
            active_count = self._get_active_task_count()
            self.logger.info(f"Consume loop exited. Active tasks remaining: {active_count}")

    async def __read_phase(self) -> None:
        """Read a batch and enqueue each message into the DRR scheduler
        instead of dispatching it directly (fair-scheduling enabled path).

        Always calls ``getmany()`` -- resetting ``max_poll_interval_ms`` --
        even when the scheduler is full; ``__apply_backpressure`` pauses
        every partition in that case, so the call just returns nothing new.
        """
        # Bounded by *buffer* room, not by pipeline capacity. The FIFO path
        # reads only what it can immediately dispatch, but that is exactly
        # what starves the scheduler: with one record in flight per
        # partition, a capacity-bounded read keeps the buffer one message
        # deep and DRR has no mixture of keys to interleave -- fair
        # scheduling degenerates to FIFO. Reading ahead into the buffer is
        # the whole point; __apply_backpressure pauses partitions when the
        # buffer itself fills.
        scheduler = self._scheduler
        if scheduler is None or self.consumer is None:
            return
        # Before the parked messages: an event that waited for its record is
        # older, and must not lose its connector's room to them every pass.
        await self.__fetch_back_waiting()
        self.__reoffer_released()
        self.__drain_deferred()
        await self.__fetch_back_remembered()
        buffer_room = max(
            0,
            self.fair_scheduler_config.max_buffered_messages
            - scheduler.pending_count
            - self.__held_beside_scheduler(),
        )
        # getmany() is called on every iteration even with no buffer room --
        # it is what resets max_poll_interval_ms, and skipping it would have
        # the group evict this consumer while it drains a full buffer.
        # __apply_backpressure has paused every partition in that case, so
        # the call returns nothing; the floor of 1 only matters if a
        # partition slips through, and that message is seeked back.
        # Do not block for new messages while the buffer holds work (remembered
        # messages included): read and dispatch alternate, so a long poll here
        # delays everything already buffered. While a record is in flight its
        # partition is paused under it and only its finish frees the next one,
        # but a finish does not wake getmany(): any poll timeout then caps a
        # partition at one record per timeout. So poll without blocking and
        # wait for the finish instead (__wait_for_finish).
        # A read-back of waiting events in progress, and a released record
        # whose events are still to be read back or offered, are buffered work
        # too: their results are taken on the next pass, which a full-length
        # poll would hold off. (Remembered positions stay remembered while
        # their own fetch-back runs, so ``total`` covers that one.)
        holds_work = bool(
            scheduler.pending_count
            or (self._remembered is not None and self._remembered.total)
            or self._waiting_fetch_task is not None
            or self._records_released
        )
        with self._partition_lock:
            in_flight = bool(self._in_flight_partitions)
        # shortcut: parallel dispatch keeps the busy poll timeout. It commits once
        # per record and aiokafka commits one at a time, so waking on every finish
        # outruns the commits; upgrade once commits are coalesced per partition.
        wait_for_finish = in_flight and not self.__parallel_dispatch()
        poll_timeout_ms = (
            0
            if wait_for_finish
            else _BUSY_POLL_TIMEOUT_MS
            if holds_work
            else messaging_env.message_timeout_ms
        )
        max_records = max(
            1, min(max(1, messaging_env.message_batch_size_indexing), buffer_room)
        )
        remembered = self._remembered
        if remembered is not None and remembered.total and remembered.has_room:
            # Reading past a connector at its cap: its messages cost a position,
            # not buffer room, so the read is sized to the positions left. One
            # that does turn out to need buffer room with none left is seeked
            # back, as before.
            max_records = max(
                max_records,
                min(_READ_PAST_BATCH, remembered.budget - remembered.total),
            )
        message_batch = await self.consumer.getmany(
            timeout_ms=poll_timeout_ms,
            max_records=max_records,
        )  # type: ignore
        self._partitions_with_data = {
            tp for tp, messages in (message_batch or {}).items() if messages
        }
        self.__end_holds_read(message_batch)
        # A rebalance that ran inside getmany() is already reflected in what it
        # returned (aiokafka hands back only the new assignment's records), so
        # only a revocation from here on makes a partition's batch stale.
        self._revoked_since_poll.clear()
        if not message_batch:
            if wait_for_finish:
                await self.__wait_for_finish()
            return

        pre_parsed = await self.__parse_batch(message_batch)
        settled = await self.__settle_connector_off(pre_parsed)
        handed_back = self.__precision_hand_backs(pre_parsed, settled)
        settled_reached: list[tuple[TopicPartition, int]] = []
        for tp, messages in message_batch.items():
            # The filter (and the enqueue below) await, and a rebalance can
            # revoke a partition meanwhile. Its messages belong to whoever owns
            # it next, which reads them from the last commit: the revocation
            # dropped this batch's buffered and tracked offsets, and an owner,
            # this consumer included, starts again from the committed offset.
            # None of the rest is tracked, buffered or committed here.
            if tp in self._revoked_since_poll:
                continue
            # Every partition in the batch is drained or explicitly seeked
            # back. Returning early from the outer loop would abandon
            # messages getmany() already handed us for the *other*
            # partitions: their fetch position has advanced, nothing would
            # re-read them, and the watermark would step over them once
            # later offsets on those partitions resolved. Silent loss.
            for message in messages:
                if not self.running:
                    self.__seek_back(tp, message.offset)
                    break
                if tp in self._revoked_since_poll:
                    break
                position = (tp, message.offset)
                if position in settled or position in handed_back:
                    # Tracked as soon as it is reached, not when it is published: a later offset of this
                    # partition (an unparseable one resolves inline) must not commit past it first.
                    if self._offset_tracker is not None:
                        self._offset_tracker.track(tp, message.offset)
                    settled_reached.append(position)
                    continue
                try:
                    outcome, blocked_key = await self.__enqueue_message(
                        tp, message, pre_parsed.get(position, _UNPARSED)
                    )
                except Exception as e:
                    # The offset is already tracked, so leaving it unresolved
                    # would pin the watermark; hand it back for redelivery.
                    # The type only: a ValidationError's text quotes the payload's values.
                    self.logger.error(
                        f"Error enqueuing message {tp}-{message.offset} for fair scheduling",
                        extra={"error_type": type(e).__name__},
                    )
                    await self.__resolve_offset(
                        _InFlightOffset(tp, message.offset),
                        done=False,
                        awaiting_reread=True,
                    )
                    self.__seek_back(tp, message.offset)
                    self.__hold_partition(tp, message.offset)
                    break
                if outcome == _ReadOutcome.REMEMBERED:
                    continue
                if outcome == _ReadOutcome.PARKED:
                    # Steer the remaining budget at lanes that can still make
                    # progress -- but only if there are any. Pausing the last
                    # readable lane is what stalls a single-lane topic.
                    if self.__other_lane_is_readable(tp):
                        # Rewind past the parked message before pausing. It is
                        # held in memory and its offset is tracked, but the
                        # rest of this partition's batch is neither -- and
                        # getmany() has already advanced the fetch position
                        # over all of it. Breaking without this seek abandons
                        # those records: nothing re-reads them, and with no
                        # watermark floor of their own the commit walks
                        # straight past them once the parked one resolves.
                        self.__seek_back(tp, message.offset + 1)
                        self.__pause_lane(tp, blocked_key)
                        break
                    continue
                if outcome == _ReadOutcome.STOP_PARTITION:
                    self.__seek_back(tp, message.offset)
                    self.__pause_lane(tp, blocked_key)
                    break
        await self.__resolve_settled(settled_reached, settled, handed_back)

    async def __wait_for_finish(self) -> None:
        """Wait for a delivery to finish, for at most _BUSY_POLL_TIMEOUT_MS.

        Returns at once if one finished since the last dispatch phase. The poll
        itself is never cancelled instead: aiokafka then never ends that poll's
        fetch_context, and max_poll_interval_ms stops being enforced.
        """
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(
                self._finished.wait(), timeout=_BUSY_POLL_TIMEOUT_MS / 1000
            )

    async def __parse_batch(
        self, message_batch: dict[TopicPartition, list[ConsumerRecord]]
    ) -> dict[tuple[TopicPartition, int], StreamMessage | None]:
        """Parse a polled batch up front, for the connector-off filter.

        A message whose parse raises is left out: ``__enqueue_message`` parses
        it again and fails it exactly as it always did.
        """
        parsed: dict[tuple[TopicPartition, int], StreamMessage | None] = {}
        for tp, messages in message_batch.items():
            for message in messages:
                try:
                    parsed[(tp, message.offset)] = await self.__parse_message(message)
                except Exception:
                    continue
        return parsed

    async def __settle_connector_off(
        self, pre_parsed: dict[tuple[TopicPartition, int], StreamMessage | None]
    ) -> dict[tuple[TopicPartition, int], StreamMessage]:
        """The messages of turned-off or removed connectors the filter settled:
        their record status is written, so they only need their offsets
        resolved, and never take buffer room or a dispatch slot."""
        if self.connector_off_filter is None or not pre_parsed:
            return {}
        positions = list(pre_parsed)
        with self._partition_lock:
            in_flight_records = set(self._in_flight_records)
        # A record already being processed here is left to its handler.
        considered = [
            None
            if message is None
            or str(message.payload.get("recordId") or "") in in_flight_records
            else message
            for message in (pre_parsed[p] for p in positions)
        ]
        result = await settle_connector_off(
            self.connector_off_filter, considered, self.logger
        )
        settled = {positions[i]: considered[i] for i in result.settled}
        return {p: m for p, m in settled.items() if m is not None}

    def __precision_hand_backs(
        self,
        pre_parsed: dict[tuple[TopicPartition, int], StreamMessage | None],
        settled: dict[tuple[TopicPartition, int], StreamMessage],
    ) -> dict[tuple[TopicPartition, int], tuple[StreamMessage, float]]:
        """Messages that would write vectors of the wrong precision, with how long to wait before they come back.

        Run after the connector-off filter, so a turned-off connector's events are settled, not handed back.
        Nothing is published here: the read loop may still seek back, and a message read again would be
        published again. ``__resolve_settled`` publishes the ones the loop reached.
        """
        if self.precision_filter is None or not pre_parsed:
            return {}
        positions = [p for p in pre_parsed if p not in settled]
        messages = [pre_parsed[p] for p in positions]
        try:
            delays = self.precision_filter.hand_back(messages)
        except Exception:
            self.logger.warning("Could not check this batch against the embedding precision; it takes the normal path", exc_info=True)
            return {}
        return {positions[i]: (messages[i], delay) for i, delay in delays.items() if messages[i] is not None}

    def __precision_delay(self, parsed: StreamMessage | None) -> float | None:
        """How long one message waits if the precision check hands it back now, else None. Never raises."""
        if self.precision_filter is None or parsed is None:
            return None
        try:
            return self.precision_filter.hand_back([parsed]).get(0)
        except Exception:
            self.logger.warning("Could not check a message against the embedding precision; it takes the normal path", exc_info=True)
            return None

    async def __publish_one_hand_back(self, message: ConsumerRecord, parsed: StreamMessage, delay: float) -> None:
        stable_id = str(parsed.payload.get("_retry_tracking_id") or f"{message.topic}-{message.partition}-{message.offset}")
        await self._requeue_message(
            message.topic, parsed, stable_id, retry_count=0, delay_s=delay, extra_payload=counted(parsed.payload),
        )
        await self._clear_retry_tracking(stable_id)

    async def __hand_back_one(self, message: ConsumerRecord, parsed: StreamMessage | None) -> bool:
        """The FIFO loop's hand-back: publish, then commit this offset. A failed publish raises, so the loop's
        own handler seeks back to the offset and the message is read and handed back again."""
        delay = self.__precision_delay(parsed)
        if delay is None or parsed is None:
            return False
        await self.__publish_one_hand_back(message, parsed, delay)
        await self.__settle_done(message, None)
        return True

    async def __publish_hand_backs(
        self,
        reached: list[tuple[TopicPartition, int]],
        handed_back: dict[tuple[TopicPartition, int], tuple[StreamMessage, float]],
    ) -> list[tuple[TopicPartition, int]]:
        """Re-queue the handed-back messages the read reached; return the positions that may now be resolved.

        Each offset is tracked before its publish, so a failed publish leaves a floor under the commit
        watermark and a later offset that did publish cannot commit past it. A partition whose publish failed
        publishes nothing more and is rewound once, to its lowest failed offset: everything behind it is read
        again, not published twice. A partition revoked while a publish was awaited is left alone, since its
        next owner reads from the last commit.
        """
        tracker = self._offset_tracker
        epochs = {tp: self._partition_epoch.get(tp, 0) for tp, _ in reached}

        def owned(tp: TopicPartition) -> bool:
            return epochs[tp] == self._partition_epoch.get(tp, 0) and tp not in self._revoked_since_poll

        resolvable: list[tuple[TopicPartition, int]] = []
        failed_at: dict[TopicPartition, int] = {}
        failed = 0
        for position in reached:
            tp, offset = position
            if not owned(tp):
                continue
            if tp in failed_at:
                continue
            entry = handed_back.get(position)
            if entry is None:
                resolvable.append(position)
                continue
            message, delay = entry
            if tracker is not None:
                tracker.track(tp, offset)
            stable_id = str(message.payload.get("_retry_tracking_id") or f"{tp.topic}-{tp.partition}-{offset}")
            try:
                await self._requeue_message(
                    tp.topic, message, stable_id, retry_count=0, delay_s=delay, extra_payload=counted(message.payload),
                )
            except Exception:
                failed += 1
                failed_at[tp] = min(offset, failed_at.get(tp, offset))
                if owned(tp) and tracker is not None:
                    # Read again within this process: a floor, with no dwell clock. Nothing to commit for it;
                    # the next offset that resolves carries the watermark.
                    tracker.mark_redeliver(tp, offset, awaiting_reread=True)
                continue
            resolvable.append(position)
        for tp, offset in failed_at.items():
            if owned(tp):
                self.__seek_back(tp, offset)
        if failed:
            self.logger.warning(
                "Could not hand back %d event(s) waiting for the embedding precision; they are read again", failed,
            )
        # A rebalance during any publish above: what it touched is no longer ours to resolve.
        resolvable = [p for p in resolvable if owned(p[0])]
        sent = len(resolvable) - sum(1 for p in resolvable if p not in handed_back)
        if sent:
            self.logger.info(
                "Handed back %d event(s) that write vectors while the embedding precision and the indexes disagree", sent,
            )
        return resolvable

    async def __resolve_settled(
        self,
        reached: list[tuple[TopicPartition, int]],
        settled: dict[tuple[TopicPartition, int], StreamMessage],
        handed_back: dict[tuple[TopicPartition, int], tuple[StreamMessage, float]] | None = None,
    ) -> None:
        """Resolve the settled offsets the read reached, with one commit per
        partition rather than one per message.

        Settled messages behind a seek-back are not resolved: they are read
        again, and settling them again writes the same status.
        """
        offset_tracker = self._offset_tracker
        reached = [p for p in reached if p[0] not in self._revoked_since_poll]
        if handed_back:
            reached = await self.__publish_hand_backs(reached, handed_back)
            settled = {**settled, **{p: m for p, (m, _) in handed_back.items()}}
        if not reached or offset_tracker is None or self.consumer is None:
            return
        commits: dict[TopicPartition, int] = {}
        for tp, offset in reached:
            offset_tracker.track(tp, offset)
            watermark = offset_tracker.mark_done(tp, offset)
            if watermark is not None:
                commits[tp] = watermark
        messages = [settled[position] for position in reached]
        for message in messages:
            tracking_id = message.payload.get("_retry_tracking_id")
            if tracking_id:
                await self._clear_retry_tracking(str(tracking_id))
        off = [m for p, m in zip(reached, messages) if not handed_back or p not in handed_back]
        if off:
            metrics.record_connector_off_settled("kafka", len(off))
            self.logger.info(
                "Acknowledged %d queued event(s) of turned-off or removed connectors "
                "without indexing them: %s",
                len(off),
                describe_connector_off(off),
            )
        if commits:
            try:
                await self.consumer.commit(commits)  # type: ignore
            except Exception as e:
                # The next commit on these partitions carries them.
                self.logger.warning(
                    "Could not commit past %d settled offset(s): %s", len(reached), e
                )

    def __publish_scheduler_metrics(self) -> None:
        """Gauges, refreshed once per consume iteration.

        Watermark lag is the one that matters operationally: every buffered
        offset is meant to reach a terminal state, and one that does not
        stalls every later commit on its partition until a restart. That
        failure is otherwise invisible until the restart replays everything.
        """
        scheduler = self._scheduler
        if scheduler is None:
            return
        try:
            metrics.record_scheduler_depth(
                "kafka",
                scheduler.pending_count,
                {
                    "org": scheduler.active_count_at(0),
                    "connector": scheduler.active_entity_count,
                },
            )
            metrics.record_lanes_paused("kafka", len(self._lane_paused))
            for tier, count in self.gate_waiters.snapshot().items():
                metrics.record_gate_waiters("kafka", tier.value, count)
            tracker = self._offset_tracker
            if tracker is not None:
                for tp in tracker.tracked_partitions:
                    metrics.record_watermark_lag(
                        tp.topic, tp.partition, tracker.watermark_lag(tp)
                    )
        except Exception as e:
            # Never let instrumentation take the consume loop down.
            self.logger.debug("Failed to publish scheduler metrics: %s", e)

    def __buffer_has_room(self) -> bool:
        scheduler = self._scheduler
        if scheduler is None:
            return False
        held = scheduler.pending_count + self.__held_beside_scheduler()
        return held < self.fair_scheduler_config.max_buffered_messages

    def __other_lane_is_readable(self, tp: TopicPartition) -> bool:
        """Whether another partition is actually producing work right now.

        Pausing a lane hands the remaining buffer budget to lanes that can
        make progress. But an *idle* partition is not an alternative source
        of work: with several partitions and traffic on one, the quiet ones
        would look readable forever, the busy one would never be read again,
        and every key behind the one that filled up would starve -- the
        failure fair scheduling exists to prevent. So this asks which
        partitions actually returned records on the last poll.
        """
        return bool(self._partitions_with_data - {tp} - set(self._lane_paused))

    def __drain_deferred(self) -> None:
        """Re-offer parked messages, oldest first.

        One key still being full must not hold back another key's parked
        messages, so this walks every entry and only skips further ones
        belonging to a key that already failed this pass -- which is what
        keeps each key's own messages in offset order.
        """
        parked = self._deferred_messages
        scheduler = self._scheduler
        if not parked or scheduler is None:
            return
        # Keyed by entity, not by leaf: the per-entity cap is what refused
        # these, and it spans every tier leaf of one connector.
        still_full: set[FairnessKey] = set()
        kept: deque[
            tuple[TopicPartition, ConsumerRecord, StreamMessage, FairnessKey]
        ] = deque()
        for entry in parked:
            tp, message, parsed, key = entry
            entity = scheduler.entity_key(key)
            if entity in still_full:
                kept.append(entry)
                continue
            result = self.__schedule(key, (tp, message, parsed), self.__retry_not_before(parsed))
            if result == EnqueueResult.ACCEPTED:
                continue
            still_full.add(entity)
            kept.append(entry)
        self._deferred_messages = kept

    def __pause_lane(self, tp: TopicPartition, blocked_key: FairnessKey | None) -> None:
        """Stop reading one lane because a key on it has no buffer room.

        On Kafka a lane is a partition, so this pauses just that partition
        and leaves every other one flowing -- which is the whole point of
        lanes. Without it, a single busy connector's cap would stall the
        consumer's reads across every key it owns.

        A ``None`` key means the *whole* buffer is full rather than one key's
        share of it; that is already handled globally by
        ``__apply_backpressure``, so there is no per-lane state to keep.
        """
        if blocked_key is None or self.consumer is None:
            return
        self._lane_paused[tp] = blocked_key
        try:
            self.consumer.pause(tp)
        except Exception as e:
            self.logger.error("Failed to pause lane %s: %s", tp, e)

    def __resume_drained_lanes(self) -> None:
        """Resume lanes whose blocking key has drained back under its cap.

        Checked before every read so a lane is held no longer than the key that
        blocked it actually needs.
        """
        if not self._lane_paused or self._scheduler is None:
            return
        scheduler = self._scheduler
        cap = self.fair_scheduler_config.max_per_entity_messages
        buffer_has_room = self.__buffer_has_room()
        remembered = self._remembered
        for tp, key in list(self._lane_paused.items()):
            if not buffer_has_room or scheduler.pending_count_for(key) >= cap:
                continue
            # Stopped for want of positions: the next message is this key's,
            # and it cannot be read until a fetch-back has freed one.
            if remembered is not None and remembered.holds(key) and not remembered.has_room:
                continue
            del self._lane_paused[tp]
            if self.consumer is None:
                continue
            with self._partition_lock:
                in_flight = tp in self._in_flight_partitions
            # A partition with a message in flight stays paused for ordering;
            # __finish_partition resumes it when that message completes.
            if not in_flight:
                try:
                    self.consumer.resume(tp)
                except Exception as e:
                    self.logger.error("Failed to resume lane %s: %s", tp, e)

    def __seek_back(self, tp: TopicPartition, offset: int) -> None:
        """Rewind a partition to ``offset`` so it and everything after it in
        this batch is read again. The scheduler buffer only ever holds
        offsets below this one, so the re-read cannot duplicate buffered
        work. FIFO uses it too: a rebalance during a parse can revoke the
        partition, and a seek that raises there would drop the whole batch."""
        if self.consumer is None:
            return
        try:
            self.consumer.seek(tp, offset)
        except Exception as e:
            self.logger.error(f"Failed to seek {tp} back to {offset}: {e}")

    async def __enqueue_message(
        self,
        tp: TopicPartition,
        message: ConsumerRecord,
        pre_parsed: "StreamMessage | None | object" = _UNPARSED,
    ) -> tuple[str, FairnessKey | None]:
        """Enqueue one read message into the scheduler, returning a
        :class:`_ReadOutcome` and, when the read must stop, the fairness key
        that has no room left.

        A full buffer -- whether the whole buffer or just this key's share of
        it -- stops the partition rather than re-publishing the message to
        the tail of the topic. Re-publishing bounced messages without a retry
        budget, destroyed ordering, inflated the Redis stream past its
        ``MAXLEN`` trim point, and made consumer lag stop meaning "work
        remaining".

        The key is handed back so the caller can pause *this partition only*
        and resume it once that key drains, instead of stalling every lane
        behind one busy one.
        """
        scheduler = self._scheduler
        offset_tracker = self._offset_tracker
        if scheduler is None or offset_tracker is None:
            raise RuntimeError("Fair scheduling read phase ran without a scheduler")

        offset_tracker.track(tp, message.offset)

        parsed = (
            await self.__parse_message(message)
            if pre_parsed is _UNPARSED
            else pre_parsed
        )
        if parsed is None:
            # Poison message: can never become valid, so it never enters the
            # scheduler -- resolve it immediately via the existing terminal
            # path, which commits through the watermark since the tracker is
            # set.
            await self.__commit_if_appropriate(
                message,
                None,
                success=False,
                is_terminal_error=True,
                in_flight=_InFlightOffset(tp, message.offset),
            )
            return _ReadOutcome.RESOLVED, None

        not_before = self.__retry_not_before(parsed)
        key = self.key_extractor.extract(parsed)
        for field, level in zip(
            self.fair_scheduler_config.key_fields, key, strict=False
        ):
            if level == _DEFAULT_KEY_LEVEL:
                metrics.record_missing_key("kafka", field)
        entity = scheduler.entity_key(key)
        remembered = self._remembered
        if remembered is not None and remembered.holds(entity):
            # Behind this connector's own remembered messages: buffering it
            # now would put it ahead of them.
            return self.__remember(entity, tp, message.offset)
        # The scheduler counts only its own entries; parked and waiting
        # messages hold the same budget, and a read past remembered positions
        # can return more messages than the buffer has room for.
        result = (
            self.__schedule(key, (tp, message, parsed), not_before)
            if self.__buffer_has_room()
            else EnqueueResult.BUFFER_FULL
        )
        if result == EnqueueResult.ACCEPTED:
            return _ReadOutcome.BUFFERED, None

        # BUFFER_FULL or ENTITY_FULL: no room. The offset stays tracked and
        # unresolved on purpose -- it floors the watermark until the seek-back
        # re-reads it, which is exactly the guarantee that makes it safe to
        # drop the message here.
        metrics.record_deferred("kafka", result.value)
        if result == EnqueueResult.ENTITY_FULL and remembered is not None:
            return self.__remember(entity, tp, message.offset)
        if result == EnqueueResult.ENTITY_FULL and self.__buffer_has_room():
            # This key is capped but the buffer as a whole is not. Park the
            # message and keep reading: reading is the only way to reach a key
            # that is *not* backed up, and stopping here caps read-ahead at
            # one key's share of the buffer. On a single-lane topic that means
            # a large backlog at the head is never read past and every key
            # behind it starves -- the problem fair scheduling exists to fix.
            self._deferred_messages.append((tp, message, parsed, key))
            return _ReadOutcome.PARKED, scheduler.entity_key(key)
        return _ReadOutcome.STOP_PARTITION, scheduler.entity_key(key)

    def __remember(
        self, entity: FairnessKey, tp: TopicPartition, offset: int
    ) -> tuple[str, FairnessKey | None]:
        """Keep only this message's position and read on.

        Its offset stays tracked, with no dwell clock, so the watermark floors
        at it and the dwell sweep leaves it alone, exactly as for a buffered
        message. With the positions budget spent, the lane stops as it did
        before positions existed: seeked back and paused until this connector
        has room.
        """
        remembered = self._remembered
        if remembered is not None and remembered.remember(entity, tp, offset):
            return _ReadOutcome.REMEMBERED, None
        if not self._remembered_budget_logged:
            self.logger.warning(
                "Remembered-positions budget of %d is spent; lanes whose next "
                "message belongs to a connector at its cap stop until it drains",
                self.fair_scheduler_config.max_remembered_positions,
            )
            self._remembered_budget_logged = True
        return _ReadOutcome.STOP_PARTITION, entity

    def __offset_fetcher(self) -> OffsetFetcher:
        if self._offset_fetcher is None:
            client_config = {
                name: value
                for name, value in IndexingKafkaConsumer.kafka_config_to_dict(
                    self.kafka_config
                ).items()
                if name
                not in ("topics", "group_id", "enable_auto_commit", "auto_offset_reset")
            }
            client_config["client_id"] = f"{tagged(self.kafka_config.client_id)}-fetch-back"
            self._offset_fetcher = OffsetFetcher(client_config, self.logger)
        return self._offset_fetcher

    async def __fetch_back_remembered(self) -> None:
        """Buffer remembered messages again, oldest first per connector, as far
        as each connector's cap and the buffer allow.

        Read through a separate reader, so the main consumer's position and
        the group are untouched. An offset retention has deleted is resolved
        and logged: its record is still waiting, and the stranded-record sweep
        re-sends it once the partition has been committed past its queue time.
        """
        task = self._fetch_back_task
        if task is not None:
            if not task.done():
                return
            self._fetch_back_task = None
            plan, self._fetch_back_plan = self._fetch_back_plan, {}
            try:
                found, gone = task.result()
            except Exception as e:
                self.logger.warning(
                    "Could not fetch back %d remembered message(s); trying again "
                    "on the next pass: %r",
                    sum(len(p) for p in plan.values()),
                    e,
                )
                return
            await self.__buffer_fetched_back(plan, found, gone)
            return

        remembered = self._remembered
        scheduler = self._scheduler
        if not remembered or not remembered.total or scheduler is None:
            return
        cap = self.fair_scheduler_config.max_per_entity_messages
        room = (
            self.fair_scheduler_config.max_buffered_messages
            - scheduler.pending_count
            - self.__held_beside_scheduler()
        )
        plan: dict[FairnessKey, list[tuple[TopicPartition, int]]] = {}
        planned: dict[object, int] = {}
        buffer_room = self._fairness.buffer_room if self._fairness else None
        entities = remembered.entities()
        if buffer_room and entities:  # entities sharing a bucket take turns at being first
            self._fetch_turns += 1
            entities = entities[self._fetch_turns % len(entities):] + entities[: self._fetch_turns % len(entities)]
        for entity in entities:
            free = cap - scheduler.pending_count_for(entity)
            shared = buffer_room(entity, scheduler) if buffer_room else None
            chunk = min(cap // 2, remembered.count(entity))
            if shared is not None:
                # Less what this plan took from its bucket; a chunk is never more than the bucket has left to give.
                left = shared[1] - planned.get(shared[0], 0)
                free, chunk = min(free, left), min(chunk, left)
            # Refilled in chunks, not a message at a time as the connector's
            # buffered work is dispatched: each fetch is a broker round trip.
            if free < max(1, chunk):
                continue
            take = min(free, room, _FETCH_BACK_BATCH)
            if take <= 0:
                continue
            plan[entity] = remembered.peek(entity, take)
            room -= len(plan[entity])
            if shared is not None:
                planned[shared[0]] = planned.get(shared[0], 0) + len(plan[entity])
            if room <= 0:
                break
        if not plan:
            return
        wanted: dict[TopicPartition, list[int]] = {}
        for positions in plan.values():
            for tp, offset in positions:
                wanted.setdefault(tp, []).append(offset)
        # In the background: reading and dispatching carry on while the
        # broker answers, and the results are buffered on a later pass.
        self._fetch_back_plan = plan
        self._fetch_back_task = spawn_detached(
            asyncio.wait_for(
                self.__offset_fetcher().fetch(wanted, timeout_seconds=_FETCH_BACK_READ_SECONDS),
                timeout=_FETCH_BACK_TIMEOUT_SECONDS,
            )
        )

    async def __buffer_fetched_back(
        self,
        plan: dict[FairnessKey, list[tuple[TopicPartition, int]]],
        found: dict[tuple[TopicPartition, int], ConsumerRecord],
        gone: set[tuple[TopicPartition, int]],
    ) -> None:
        """Buffer, or resolve, what a fetch-back brought.

        Positions stay in ``RememberedOffsets`` through every await, so a
        revocation that runs during one forgets them like any other. They are
        taken off the queue only in the synchronous step below, and only from
        the head while the next one is on a partition not revoked since the
        last poll. Anything not taken stays remembered for a later pass.
        """
        remembered = self._remembered
        scheduler = self._scheduler
        if remembered is None or scheduler is None:
            return
        revoked = self._revoked_since_poll

        pre_parsed: dict[tuple[TopicPartition, int], StreamMessage | None] = {}
        for entity in plan:
            for position in remembered.peek(entity, len(plan[entity])):
                if position in gone:
                    continue
                if position not in found:
                    break
                try:
                    parsed = await self.__parse_message(found[position])
                except Exception as e:
                    # It parsed when it was first read; try again next pass.
                    self.logger.warning(
                        "Could not parse fetched-back message %s-%s; it stays remembered",
                        position[0], position[1], extra={"error_type": type(e).__name__},
                    )
                    parsed = None
                if parsed is not None:
                    pre_parsed[position] = parsed
        settled = await self.__settle_connector_off(pre_parsed)

        # No awaits from here until the positions taken are resolved.
        buffered = 0
        settled_taken: list[tuple[TopicPartition, int]] = []
        gone_taken: list[tuple[TopicPartition, int]] = []
        for entity in plan:
            while True:
                position = remembered.head(entity)
                if position is None or position[0] in revoked:
                    break
                if position in gone:
                    gone_taken.append(remembered.pop(entity))
                    continue
                if position in settled:
                    settled_taken.append(remembered.pop(entity))
                    continue
                parsed = pre_parsed.get(position)
                if parsed is None or not self.__buffer_has_room():
                    break
                result = self.__schedule(
                    self.key_extractor.extract(parsed),
                    (position[0], found[position], parsed),
                    self.__retry_not_before(parsed),
                )
                if result != EnqueueResult.ACCEPTED:
                    # Others may have taken the room measured when the fetch
                    # was planned; the rest wait for a later pass, in order.
                    break
                remembered.pop(entity)
                buffered += 1

        if gone_taken:
            self.logger.warning(
                "%d remembered message(s) were deleted by Kafka retention before "
                "they could be read back; the stranded-record sweep re-sends a "
                "record only while it is still waiting to be indexed",
                len(gone_taken),
            )
            await self.__commit_resolved(gone_taken)
        await self.__resolve_settled(settled_taken, settled)

    async def __commit_resolved(self, positions: list[tuple[TopicPartition, int]]) -> None:
        """Mark offsets done in the watermark, all before any await, then
        commit once per partition. A failed commit is carried by the next one."""
        offset_tracker = self._offset_tracker
        if offset_tracker is None or self.consumer is None:
            return
        commits: dict[TopicPartition, int] = {}
        for tp, offset in positions:
            if tp in self._revoked_since_poll:
                continue
            watermark = offset_tracker.mark_done(tp, offset)
            if watermark is not None:
                commits[tp] = watermark
        if not commits:
            return
        try:
            await self.consumer.commit(commits)  # type: ignore
        except Exception as e:
            self.logger.warning(
                "Could not commit past %d resolved offset(s): %s", len(positions), e
            )

    def __retry_not_before(self, parsed: StreamMessage) -> float | None:
        """When a re-queued message falls due; None (due now) when it has no time or the time is malformed. NaN and inf
        parse as floats but are malformed: NaN never compares as due or not due, and inf is never due."""
        not_before = parsed.payload.get("_retry_not_before")
        if not not_before:
            return None
        try:
            value = float(not_before)
        except (TypeError, ValueError):
            return None
        return value if math.isfinite(value) else None

    async def __resolve_offset(
        self,
        in_flight: "_InFlightOffset | None",
        *,
        done: bool,
        awaiting_reread: bool = False,
    ) -> bool:
        """Settle a buffered offset's claim on the commit watermark, exactly
        once, and commit if that let the watermark advance. False when the
        delivery belongs to a revoked assignment and nothing was settled.

        ``done=True`` means this delivery is finished with (processed,
        re-queued or dead-lettered) and the watermark may pass it. ``done=False`` means it
        was not processed and must be redelivered, so it becomes a floor the
        watermark stops at until the offset is read again.

        Bridged onto the main loop as one unit (mirroring ``_commit_offset``)
        so the offset tracker, like the scheduler, is only ever touched from
        one thread -- safe to call from the main loop (read/dispatch phase)
        or the worker loop (processing wrapper).
        """
        if in_flight is None or in_flight.resolved:
            return True
        in_flight.resolved = True

        offset_tracker = self._offset_tracker
        if offset_tracker is None or self.consumer is None:
            return True
        tp, offset = in_flight.tp, in_flight.offset

        async def do_resolve() -> bool:
            # Revoked while this delivery was settling: the revocation cleared
            # the partition's watermark, so marking it now would compute one
            # from nothing and commit past offsets the next owner still needs.
            if self.consumer is None or tp in self._revoked_since_poll:
                return False
            # The revocation flag is cleared at the next poll; a delivery that ends after that is still of the
            # revoked assignment, and settling it would commit from a watermark rebuilt without its backlog.
            if in_flight.epoch is not None and in_flight.epoch != self._partition_epoch.get(tp, 0):
                return False
            watermark = (
                offset_tracker.mark_done(tp, offset)
                if done
                else offset_tracker.mark_redeliver(
                    tp, offset, awaiting_reread=awaiting_reread
                )
            )
            if watermark is not None:
                await self.consumer.commit({tp: watermark})  # type: ignore
            return True

        return await self._run_on_main_loop(do_resolve())

    async def __rebuffer_after_failed_handback(
        self,
        message: ConsumerRecord,
        parsed: StreamMessage,
        in_flight: "_InFlightOffset | None",
        delay_s: float | None,
    ) -> None:
        """Put a message nothing re-published back in the buffer.

        A redelivery floor would not do: the read position is already past the
        offset, so only a restart brings it back, and the dwell sweep commits
        past it first. A buffered offset floors the watermark with no dwell clock.
        """
        if not self.running:
            await self.__resolve_offset(in_flight, done=False)
            return
        tp = TopicPartition(message.topic, message.partition)
        delay = delay_s if delay_s is not None else _compute_retry_backoff_seconds(1)
        parsed.payload["_retry_not_before"] = time.time() + delay

        async def rebuffer() -> None:
            epoch = in_flight.epoch if in_flight is not None else None
            # Revoked meanwhile: nothing committed it, so the next owner reads it.
            if tp in self._revoked_since_poll or (
                epoch is not None and epoch != self._partition_epoch.get(tp, 0)
            ):
                return
            self._offset_tracker.track(tp, message.offset)
            if not self.__buffer_has_room() and self.__wait_by_offset(message, parsed):
                return
            key = self.key_extractor.extract(parsed)
            accepted = self.__schedule(key, (tp, message, parsed), self.__retry_not_before(parsed))
            if accepted != EnqueueResult.ACCEPTED:
                self._deferred_messages.append((tp, message, parsed, key))

        await self._run_on_main_loop(rebuffer())
        if in_flight is not None:
            in_flight.resolved = True

    async def __sweep_stale_offsets(self) -> None:
        """Force-resolve offsets held past the dwell budget.

        Last-resort escape, not a normal path: every offset is meant to be
        resolved by the code that dispatched it. But one delivery that never
        resolves stalls every later commit on its partition until a restart,
        so the sweep trades at-most-once for that offset against an
        indefinitely pinned watermark, and says so loudly.
        """
        offset_tracker = self._offset_tracker
        if offset_tracker is None:
            return
        budget = self.fair_scheduler_config.max_dwell_seconds
        now = time.monotonic()
        if now - self._last_dwell_sweep < _DWELL_SWEEP_INTERVAL_SECONDS:
            return
        self._last_dwell_sweep = now

        stale_offsets = offset_tracker.stale_offsets(budget)
        if stale_offsets:
            metrics.record_dwell_exceeded("kafka", len(stale_offsets))
        for stale in stale_offsets:
            self.logger.error(
                "Offset %s-%s has been unresolved for %.0fs (budget %.0fs); "
                "force-committing past it to unpin the commit watermark. "
                "This message may not be reprocessed -- investigate the "
                "dispatch path that failed to resolve it.",
                stale.tp,
                stale.offset,
                stale.age_seconds,
                budget,
            )
            await self.__resolve_offset(
                _InFlightOffset(stale.tp, stale.offset), done=True
            )

    async def __dispatch_phase(self) -> None:
        """Dispatch fairly-scheduled messages while pipeline capacity and
        downstream health allow, then hand control back to the read phase."""
        scheduler = self._scheduler
        if scheduler is None:
            return

        # Every finish so far is seen by this phase; only a later one frees more.
        self._finished.clear()
        parallel = self.__parallel_dispatch()
        budget = self._dispatch_budget()
        held = concurrency.held_pools(self)

        def can_dispatch(
            item: tuple[TopicPartition, ConsumerRecord, StreamMessage],
        ) -> bool:
            tp, message, parsed = item
            # A tier at its ceiling, or a record whose downstream asked for a
            # pause, is passed over, not waited on: DRR skips the leaf without
            # charging it and moves to the entity's other tier or the next entity.
            if not budget.allows(concurrency.dispatch_tier(self, parsed)):
                return False
            if concurrency.held_downstream(self, parsed, held):
                return False
            if parallel:
                # A busy record is not refused here: refusing the head stalls
                # the whole leaf. The loop below parks it instead.
                return True
            with self._partition_lock:
                return tp not in self._in_flight_partitions

        self.__reoffer_released()
        while self.running:
            # Per record, like the budget: a 429 arriving mid-pass holds the rest of it.
            budget = self._dispatch_budget()
            held = concurrency.held_pools(self)
            if (
                budget.blocked
                or self.downstream_read_pause() > 0.0
                or concurrency.index_gates_saturated(self)
            ):
                break

            # Rebuilt for every record: each dispatch changes one connector's
            # count in flight, and may empty its queue.
            plan = self._slot_share_plan(can_dispatch)
            dispatched = scheduler.dequeue(
                can_dispatch=can_dispatch,
                key_allowed=plan.allows if plan is not None else None,
            )
            if dispatched is None:
                break

            key, item = dispatched
            tp, message, parsed = item
            self.__unschedule(item)
            in_flight = _InFlightOffset(tp, message.offset, self._partition_epoch.get(tp, 0))
            # The verdict may have turned since this message was buffered: hand it back now, before a handler
            # starts and downloads anything. A publish that fails puts it back in the buffer. This comes before
            # the record wait below, which must have no await between it and the reservation.
            hand_back_delay = self.__precision_delay(parsed)
            if hand_back_delay is not None:
                try:
                    await self.__publish_one_hand_back(message, parsed, hand_back_delay)
                except Exception:
                    self.logger.warning("Could not hand back a buffered event; it stays buffered", exc_info=True)
                    await self.__rebuffer_after_failed_handback(message, parsed, in_flight, hand_back_delay)
                else:
                    await self.__resolve_offset(in_flight, done=True)
                continue
            record_key = self.__record_key(message, parsed) if parallel else None
            if record_key is not None and self.__waits_for_record(record_key, item, key):
                continue
            metrics.record_dispatch("kafka", key[0] if key else "unknown")
            reserved = (
                self.__reserve_record(record_key)
                if record_key is not None
                else self.__reserve_partition(message)
            )
            if not reserved:
                # can_dispatch (the partition) or __waits_for_record (the
                # record) already checked it wasn't in flight, and nothing awaits between that check and this
                # reservation, so a single-threaded main loop rules this out.
                # If it ever fires the item is already out of the scheduler,
                # so hand the offset back for redelivery rather than dropping
                # it on the floor.
                self.logger.error(
                    "Invariant violation: %s was reserved between the dispatch "
                    "eligibility check and reservation; returning offset %s "
                    "for redelivery",
                    record_key or tp,
                    message.offset,
                )
                await self.__resolve_offset(in_flight, done=False)
                continue
            if self._offset_tracker is not None:
                # Arms the dwell sweep for this offset. Until a worker has
                # actually taken it, a sweep must not commit past it.
                self._offset_tracker.mark_dispatched(tp, message.offset)
            try:
                await self.__start_processing_task(
                    message, parsed, in_flight, record_key, slot_key=key
                )
            except Exception as e:
                self.__finish_partition(
                    message, retry_current=True, record_key=record_key
                )
                await self.__resolve_offset(in_flight, done=False)
                self.logger.error(
                    f"Error dispatching message {message.topic}-{message.partition}"
                    f"-{message.offset}: {e}"
                )

    async def __defer_if_retry_not_ready(
        self, message: ConsumerRecord
    ) -> "tuple[bool, StreamMessage | None]":
        """Return ``(defer, parsed)``: whether to seek back because this
        message's retry backoff (``_retry_not_before``, stamped by
        ``_requeue_message``) hasn't elapsed, and the parse it did to find
        out. A message that is not due seeks back and holds its partition
        until it is (``__hold_partition``), so the loop does not re-read it
        meanwhile. Also defers, without a seek or hold of its own, when the in-flight
        message's finish rewound the partition during the parse: that
        rewind is below this message and must not be overtaken.

        The parse is handed back rather than dropped because the wrapper needs
        the same envelope moments later. Re-parsing it there costs a second
        full ``json.loads`` — and, above the offload threshold, a second thread
        hop — on exactly the large payloads the offload exists to keep off this
        loop.

        Checked here — before ``__reserve_partition`` pauses the partition
        and before a worker-thread task/active-task slot is spent — so a
        single backing-off record doesn't tie up pipeline capacity for
        others while it waits out its (up to 5 minute) backoff. Ordering
        still means this partition can't skip ahead to later messages, but
        at least the wait no longer consumes real processing resources.
        """
        parsed = await self.__parse_message(message)
        if TopicPartition(message.topic, message.partition) in self._rewound_since_poll:
            return True, parsed  # a finish rewound below this message during the parse
        if parsed is None:
            return False, None
        not_before = self.__retry_not_before(parsed)
        if not_before is None:
            return False, parsed
        remaining = not_before - time.time()
        if remaining <= 0:
            return False, parsed

        tp = TopicPartition(message.topic, message.partition)
        self.consumer.seek(tp, message.offset)
        self.__hold_partition(tp, message.offset, remaining)
        return True, parsed

    async def __parse_message(self, message: ConsumerRecord) -> StreamMessage | None:
        """Parse the Kafka message value into a StreamMessage.

        Handles bytes decoding, JSON parsing, and double-encoded JSON. Never logs the value.

        Returns:
            StreamMessage, or None for a message that can never parse (poison): not UTF-8, not JSON (including nesting or a
            number too large to decode), a rejected
            envelope, or JSON that is not a StreamMessage. Anything else (a key-store or network failure) raises,
            so the caller re-reads it.
        """
        message_id = f"{message.topic}-{message.partition}-{message.offset}"
        message_value = message.value
        size_bytes = len(message_value) if isinstance(message_value, (bytes, str)) else None

        with log_fields(topic=message.topic, message_id=message_id):
            try:
                if isinstance(message_value, bytes):
                    message_value = message_value.decode("utf-8")

                if isinstance(message_value, str):
                    try:
                        # Offloaded above a size threshold: a connector can emit a
                        # record whose whole body rides in the envelope, and
                        # parsing it inline blocks every other in-flight record on
                        # this one worker loop.
                        parsed = await offload_if_large(decode_message, message_value)
                        return await open_stream_message(message.topic, parsed, StreamMessage)
                    except EnvelopeRejected as e:
                        self.logger.error(f"Envelope rejected for {message_id}: {e.describe()}")
                        return None
                    except (ValidationError, TypeError) as e:
                        # Valid JSON that is not a StreamMessage never becomes one: poison, as in the Redis twin.
                        # The type only: a ValidationError's text quotes the payload's values.
                        self.logger.error(f"Failed to parse message {message_id} as StreamMessage: {type(e).__name__}")
                        return None
                    except json.JSONDecodeError as e:
                        # The cause names nesting or a too-long number when the text is valid JSON.
                        self.logger.error(
                            f"JSON parsing failed for message {message_id}",
                            extra={"size_bytes": size_bytes, "error_type": type(e.__cause__ or e).__name__},
                        )
                        return None
                else:
                    self.logger.error(
                        f"Unexpected message value type for {message_id}: {type(message_value)}"
                    )
                    return None

            except UnicodeDecodeError:
                self.logger.error(f"Failed to decode message {message_id}", extra={"size_bytes": size_bytes})
                return None

    async def __start_processing_task(
        self,
        message: ConsumerRecord,
        parsed_message: "StreamMessage | None" = None,
        in_flight: "_InFlightOffset | None" = None,
        record_key: str | None = None,
        slot_key: FairnessKey | None = None,
    ) -> None:
        """Start a new task for processing a message with semaphore control.
        Submits the task to the worker thread's event loop instead of the main loop.
        Tracks futures to ensure proper cleanup during shutdown.
        """
        if not self.worker_loop:
            # Raise (not return) so the caller's except-block runs
            # __finish_partition — otherwise __reserve_partition's pause
            # above is never undone and this partition wedges forever.
            raise RuntimeError("Worker loop not initialized, cannot process message")

        if not self.running:
            raise RuntimeError("Consumer is stopping, skipping message processing")

        # Submit coroutine to worker thread's event loop and track the future
        waiter_token = concurrency.GateWaiterToken(
            self,
            concurrency.dispatch_tier(self, parsed_message),
            parsed_message,
        )
        processing_coro = self.__process_message_wrapper(
            message, waiter_token, parsed_message, in_flight
        )
        slot_share = self._slot_share if slot_key is not None else None
        slot_token = (message.topic, message.partition, message.offset)
        if slot_share is not None and slot_key is not None:
            # Counted against the connector's share from dispatch until the
            # task ends, including any wait at the gate: a record queued for
            # a slot is as good as holding one, because the gate admits in
            # arrival order. Until it is admitted it also marks the connector
            # as waiting, since it may be held up before the gate.
            slot_share.started(slot_token, slot_key, self._slot_pool_of(slot_key))
        try:
            future = asyncio.run_coroutine_threadsafe(
                processing_coro,
                self.worker_loop,
            )
        except BaseException:
            processing_coro.close()
            waiter_token.release()
            if slot_share is not None:
                slot_share.finished(slot_token)
            raise

        # Track the future for cleanup during shutdown
        with self._futures_lock:
            self._active_futures.add(future)

        # Add callback to remove future from tracking when done
        def on_future_done(f: Future[bool]) -> None:
            waiter_token.release()
            if slot_share is not None:
                slot_share.finished(slot_token)
            with self._futures_lock:
                self._active_futures.discard(f)

            retry_current = False
            try:
                _ = f.result()
            except asyncio.CancelledError:
                # Shutdown/reassignment cancelled the task — don't retry, but
                # still fall through to __finish_partition below so the
                # partition gets resumed/committed instead of stalling.
                pass
            except Exception as exc:
                retry_current = True
                # A broker error already written (index.requeue_failed, index.commit_failed) drops to DEBUG here,
                # and stays without the text that line left out.
                already = logged_level(exc) is not None
                log_failure(
                    self.logger, "index.task_failed", exc, include_error_text=not already and not quotes_content(exc),
                    level=logging.WARNING if already else None,
                )
            main_loop = self.main_loop
            if main_loop is not None and main_loop.is_running():
                main_loop.call_soon_threadsafe(
                    self.__finish_partition,
                    message,
                    retry_current,
                    record_key,
                )
            if in_flight is not None and not in_flight.resolved:
                # Backstop for the watermark contract: the wrapper is meant
                # to resolve every offset it was handed, but a path that
                # returns without doing so would otherwise stall every later
                # commit on this partition. Redelivery is the safe default --
                # it never commits past unprocessed work.
                self.logger.warning(
                    "Offset %s-%s finished without resolving its commit "
                    "claim; returning it for redelivery",
                    in_flight.tp,
                    in_flight.offset,
                )
                self.__schedule_redeliver(in_flight)

        future.add_done_callback(on_future_done)

    def __schedule_redeliver(self, in_flight: "_InFlightOffset") -> None:
        """Hand an unresolved offset back for redelivery from a synchronous
        callback. The future's done-callback runs on the worker thread, so
        the resolution has to be hopped onto the main loop as a task rather
        than awaited here."""
        main_loop = self.main_loop
        if main_loop is None or not main_loop.is_running():
            return

        def schedule() -> None:
            task = asyncio.ensure_future(
                self.__resolve_offset(in_flight, done=False)
            )
            task.add_done_callback(self.__log_redeliver_failure)

        main_loop.call_soon_threadsafe(schedule)

    def __log_redeliver_failure(self, task: "asyncio.Future[None]") -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            self.logger.error(
                "Failed to return an unresolved offset for redelivery; the "
                "commit watermark stays pinned until the dwell sweep clears "
                "it: %s",
                error,
            )

    async def _run_on_main_loop(self, coro: Any) -> Any:
        """Run a coroutine on the main loop (safe when called from the worker loop)."""
        return await concurrency.bridge_to_main_loop(self, coro, _MAIN_LOOP_OP_TIMEOUT)

    def _log_distributed_error(self, operation: str, error: Exception) -> None:
        concurrency.log_distributed_error(self, operation, error)

    async def _acquire_distributed_slot(
        self,
        pool: str,
        owner: str,
        limit: int,
        deadline_seconds: float | None = None,
        *,
        leases: DistributedLeaseSet | None = None,
    ) -> bool:
        """Try to acquire a distributed lease; see consumer_concurrency for semantics.

        Passing ``leases`` is how a lease gets recorded: the helper adds it
        only when Redis granted it, so a fail-open admission is never
        registered as one this owner holds.
        """
        return await concurrency.acquire_distributed_slot(
            self, pool, owner, limit, deadline_seconds, leases=leases
        )

    async def _release_distributed_slot(self, pool: str, owner: str) -> None:
        await concurrency.release_distributed_slot(self, pool, owner)

    async def _clear_retry_tracking(self, message_id: str) -> None:
        await concurrency.clear_retry_tracking(self, message_id)

    async def _get_retry_count(self, message_id: str) -> int:
        return await concurrency.get_retry_count(self, message_id)

    async def _increment_retry_and_check(
        self, message_id: str
    ) -> tuple[int, bool]:
        return await concurrency.increment_retry_and_check(self, message_id)

    async def _commit_offset(self, message: ConsumerRecord) -> None:
        """Commit offset on the main loop where the Kafka consumer was started."""
        if not self.consumer:
            return
        topic_partition = TopicPartition(message.topic, message.partition)
        await self._run_on_main_loop(
            self.consumer.commit({topic_partition: message.offset + 1})  # type: ignore
        )

    def _get_stable_message_id(self, message: ConsumerRecord, parsed_message: StreamMessage | None = None) -> str:
        """Get a stable message ID for retry tracking.
        
        Uses _retry_tracking_id from payload if present (for re-queued messages),
        otherwise constructs one from the current offset.
        
        Args:
            message: The Kafka message record
            parsed_message: The parsed StreamMessage (if available)
            
        Returns:
            Stable message ID for retry tracking
        """
        if parsed_message and "_retry_tracking_id" in parsed_message.payload:
            return str(parsed_message.payload["_retry_tracking_id"])

        return f"{message.topic}-{message.partition}-{message.offset}"

    async def _send_in_org(self, message: StreamMessage, topic: str, event_type: str, payload: dict) -> None:
        """Re-publish under the message's own org so a retry is signed like the original (the handler's bind is gone by now)."""
        with bind_optional(ctx_of(message)):
            await self.producer.send_event(topic=topic, event_type=event_type, payload=payload)

    async def _requeue_message(
        self,
        topic: str,
        message: StreamMessage,
        stable_message_id: str,
        retry_count: int = 1,
        delay_s: float | None = None,
        parse_enqueued_at_ms: int | None = None,
        extra_payload: dict | None = None,
    ) -> None:
        """Re-publish a failed message to the same topic for retry.
        
        The message goes to the end of the queue, allowing transient errors
        to resolve before retry. The original offset is committed.
        
        Preserves the stable message ID in the payload for retry tracking.
        Stamps an absolute `_retry_not_before` timestamp (exponential backoff
        on retry_count) instead of sleeping here: this call runs inside the
        `except` clause, before the `finally` block releases the parsing
        semaphore, so sleeping here would hold that slot for the whole
        backoff window. The delay is honored later, on the consume side,
        before any semaphore is acquired (see __process_message_wrapper).
        
        Args:
            topic: Topic to re-queue to
            message: The message to re-queue
            stable_message_id: Stable ID for retry tracking (preserved across re-queues)
            retry_count: Current delivery attempt count, used to size the backoff
            delay_s: Hold it this long instead (a paused dependency's wait)
        """
        if not self.producer:
            raise RuntimeError("No producer available for re-queue")

        payload = dict(message.payload)
        payload.update(extra_payload or {})
        payload["_retry_tracking_id"] = stable_message_id
        backoff_seconds = delay_s if delay_s is not None else _compute_retry_backoff_seconds(retry_count)
        payload["_retry_not_before"] = time.time() + backoff_seconds
        if parse_enqueued_at_ms:
            # PARSE_ADMISSION=classes: when it first queued, kept across hand-backs so aging survives them.
            payload.setdefault("parseEnqueuedAtMs", parse_enqueued_at_ms)

        # The handler's context is reset by now, and the send runs on the
        # main loop under its own: re-bind the message's so the retry
        # keeps its request id and org.
        await self._run_on_main_loop(
            run_in_context(
                context_from_envelope(
                    {"requestId": message.requestId, "orgId": message.orgId}
                ),
                self._send_in_org(message, topic, message.eventType, payload),
            )
        )
        self.logger.debug(
            f"Re-queued {stable_message_id} with {backoff_seconds:.0f}s backoff (attempt {retry_count})"
        )

    async def __abandon_message(
        self,
        message_id: str,
        tracking_id: str,
        parsed_message: StreamMessage | None,
        *,
        reason: str,
        attempts: int,
    ) -> None:
        """Give up on a message: tell the sink, then drop its tracking.

        The caller commits immediately afterwards, which is final — so a record
        whose message is dropped without this notification is left on whatever
        status it happened to hold, which no recovery sweep revisits, and the
        log names only an offset that explains nothing.
        """
        await notify_abandoned(
            self.disposition_sink,
            self.logger,
            parsed_message,
            reason=reason,
            attempts=attempts,
        )
        if timing.current_timeline() is None:  # in a record's run, its index.end says it
            self.logger.warning(
                "Dead-lettered %s (%s, tracking id %s): %s",
                message_id,
                describe_message(parsed_message),
                tracking_id,
                reason,
            )
        await self._clear_retry_tracking(tracking_id)

    async def __settle_done(
        self, message: ConsumerRecord, in_flight: "_InFlightOffset | None"
    ) -> bool:
        """Mark this delivery finished with, whichever settlement mode is on:
        the commit watermark under fair scheduling, a direct offset commit
        otherwise. ``__resolve_offset`` alone is a no-op without a tracker,
        which left FIFO-mode offsets uncommitted. False when the partition was
        revoked under this delivery, so nothing was settled."""
        if self._offset_tracker is not None:
            return await self.__resolve_offset(
                in_flight
                or _InFlightOffset(
                    TopicPartition(message.topic, message.partition),
                    message.offset,
                ),
                done=True,
            )
        await self._commit_offset(message)
        return True

    async def __settle_noted(
        self, message: ConsumerRecord, in_flight: "_InFlightOffset | None"
    ) -> bool:
        """``__settle_done``, with what went wrong noted for index.end. A failed commit is
        written once, without its text, and raised: the offset is not settled. False when
        the partition was revoked: its next owner reads the offset again."""
        try:
            settled = await self.__settle_done(message, in_flight)
        except Exception as e:
            timing.note(disposition="retry", reason="commit_failed")
            log_failure(self.logger, "index.commit_failed", e, level=logging.WARNING, include_error_text=False)
            raise
        if not settled:
            timing.note(disposition="retry", reason="claim_lost")
        return settled

    async def __commit_if_appropriate(
        self,
        message: ConsumerRecord,
        parsed_message: StreamMessage | None,
        success: bool,
        is_terminal_error: bool = False,
        in_flight: "_InFlightOffset | None" = None,
    ) -> None:
        """Commit offset and re-queue message on transient failure.

        Uses Redis-based RetryManager for persistent retry tracking.
        Error classification is based purely on exception type.

        On transient failure, the message is published back to the same topic
        (goes to end of queue) and the original offset is committed. This
        eliminates all offset ordering issues.

        Args:
            message: The Kafka message record
            parsed_message: The parsed StreamMessage (None if parsing failed)
            success: Whether processing succeeded
            is_terminal_error: Whether the error is terminal (don't retry)
        """
        message_id = f"{message.topic}-{message.partition}-{message.offset}"
        stable_message_id = self._get_stable_message_id(message, parsed_message)

        # Noted on the record's index.end once the commit returns: until then nothing is decided.
        decision: dict[str, object] = {"disposition": "ack"}
        if success:
            self.logger.debug(f"Message {message_id} processed successfully")
            await self._clear_retry_tracking(stable_message_id)
        elif is_terminal_error:
            # Route it through the sink as well — the handler usually wrote
            # FAILED on its way out, in which case this is a no-op, but an
            # error raised before it ever ran (a malformed envelope, a missing
            # orgId) leaves no trace at all otherwise.
            await self.__abandon_message(
                message_id,
                stable_message_id,
                parsed_message,
                reason="terminal error",
                attempts=1,
            )
            decision = {"disposition": "dead_letter", "reason": "terminal"}
        elif self.retry_manager and parsed_message:
            count, should_dead_letter = await self._increment_retry_and_check(stable_message_id)
            if should_dead_letter:
                await self.__abandon_message(
                    message_id,
                    stable_message_id,
                    parsed_message,
                    reason=(
                        f"{count} transient failures "
                        f"(max {messaging_env.max_delivery_attempts})"
                    ),
                    attempts=count,
                )
                decision = {"disposition": "dead_letter", "reason": "attempts_exhausted", "attempt": count}
            else:
                # RE-QUEUE: Publish back to same topic for retry
                try:
                    await self._requeue_message(message.topic, parsed_message, stable_message_id, retry_count=count)
                except Exception as e:
                    timing.note(disposition="retry", reason="requeue_failed", attempt=count)
                    log_failure(self.logger, "index.requeue_failed", e, level=logging.WARNING, include_error_text=False)
                    if self._scheduler is None:
                        # The future's callback seeks back here. Returning would
                        # let the next message's commit cover this offset.
                        raise
                    # No copy on the topic: retry this offset here, after the backoff the copy would have had.
                    await self.__rebuffer_after_failed_handback(
                        message, parsed_message, in_flight, _compute_retry_backoff_seconds(count)
                    )
                    # The helper settled the claim; the tail would log a commit that did not happen.
                    return
                decision = {"disposition": "retry", "attempt": count}
        else:
            decision = {"disposition": "dead_letter", "reason": "no_retry_tracker"}
            if timing.current_timeline() is None:
                self.logger.warning(
                    f"Message {message_id} failed, no retry manager or unparseable, committing"
                )

        # ALWAYS commit - message is either done, dead-lettered, or re-queued
        if not await self.__settle_noted(message, in_flight):
            return
        self.logger.debug(f"Committed offset for {message_id}")
        timing.note(**decision)

    async def __delay_if_retry_not_ready(
        self, parsed_message: StreamMessage, message_id: str
    ) -> bool:
        """Sleep out the remaining backoff window for a re-queued message.

        Called before any semaphore is acquired (see __process_message_wrapper),
        so the wait ties up only a pending-task slot, not a parsing/indexing
        concurrency slot, while a downstream outage clears.

        Sleeps in small increments and re-checks ``self.running`` between
        them, so a shutdown request interrupts the wait quickly instead of
        holding this future — and blocking graceful shutdown's per-future
        wait in __stop_worker_thread — for up to the full ~300s backoff.

        Returns False if the consumer is shutting down and the wait was
        abandoned early (caller should not process/commit the message —
        its offset stays uncommitted and it will be redelivered on restart).
        """
        not_before = self.__retry_not_before(parsed_message)
        if not_before is None:
            return True
        remaining = not_before - time.time()
        if remaining <= 0:
            return True

        self.logger.debug(
            f"Delaying re-queued message {message_id} for {remaining:.1f}s before processing"
        )
        while remaining > 0:
            if not self.running:
                self.logger.info(
                    f"Consumer stopping, abandoning delayed retry for {message_id} "
                    "(offset left uncommitted, will be redelivered)"
                )
                return False
            await asyncio.sleep(min(_DELAY_POLL_INTERVAL_SECONDS, remaining))
            remaining -= _DELAY_POLL_INTERVAL_SECONDS
        return True

    async def __process_message_wrapper(
        self,
        message: ConsumerRecord,
        waiter_token: "concurrency.GateWaiterToken | None" = None,
        parsed_message: "StreamMessage | None" = None,
        in_flight: "_InFlightOffset | None" = None,
    ) -> bool:
        """Process a message with its root, org and ids bound for the whole run, so the lines
        after the handler (commit, re-queue, index.end) carry them too."""
        message_id = f"{message.topic}-{message.partition}-{message.offset}"
        with log_fields(topic=message.topic, message_id=message_id):
            # Reuses the parse __defer_if_retry_not_ready already did; only a
            # caller that skipped that check (tests, direct invocation) parses here.
            if parsed_message is None:
                parsed_message = await self.__parse_message(message)
            with bound_message(parsed_message):
                return await self.__process_bound_message(message, waiter_token, parsed_message, in_flight)

    async def __process_bound_message(
        self,
        message: ConsumerRecord,
        waiter_token: "concurrency.GateWaiterToken | None",
        parsed_message: "StreamMessage | None",
        in_flight: "_InFlightOffset | None",
    ) -> bool:
        """Wrapper to handle async task cleanup and semaphore release based on yielded events.

        Semaphore lifecycle:
        - indexing_semaphore: outer active-pipeline gate, held from handler
          entry through INDEXING_COMPLETE
        - parsing_semaphore: nested parse gate, acquired on START_PARSING and
          released on PARSING_COMPLETE

        The outer gate is acquired before the handler so up to
        MAX_CONCURRENT_INDEXING records can be IN_PROGRESS. Parsing slots are
        acquired only after the handler requests them, so already-parsed
        records can keep progressing through extraction/vectordb while new
        ones wait for a free parse slot.

        Error classification is based purely on exception type:
        - TERMINAL: Commit immediately (parsing errors, validation errors)
        - TRANSIENT: Check retry count via RetryManager

        Ensures semaphores are released even on error via finally block.
        """
        topic = message.topic
        partition = message.partition
        offset = message.offset
        message_id = f"{topic}-{partition}-{offset}"

        suspect_held = False
        attempt_started = False
        abandoned = False
        # Set once settlement starts: what it raises is already noted, so it propagates and is never settled again.
        settling = False
        parsing_held = False
        indexing_held = False
        shutting_down = False
        parse_lease_pool = "parsing"
        index_lease_pool = "indexing"
        parsing_admission: concurrency.Admission | None = None
        memory_admission: concurrency.Admission | None = None
        download_admission: concurrency.Admission | None = None
        index_admission: concurrency.Admission | None = None
        distributed_leases = concurrency.new_lease_set(self)
        lease_handle: Any | None = None
        renewal_task: asyncio.Future[bool] | None = None
        lease_owner = (
            f"{self._consumer_instance_id}:{message_id}:{uuid.uuid4().hex}"
        )

        if self.governor is None and (
            self.indexing_semaphore is None or self.parsing_semaphore is None
        ):
            self.logger.error(f"Concurrency gates not initialized for {message_id}")
            await self.__resolve_offset(in_flight, done=False)
            return False

        # Parsed (and, for re-queued messages, any backoff waited out) before
        # acquiring the parsing semaphore. This way a retry waiting for a
        # downed service to recover only occupies a pending-task slot
        # (counted against backpressure), never a parsing/indexing semaphore
        # slot — the exact resource a sibling record needs to make progress.
        if parsed_message is None:
            self.logger.warning(f"Failed to parse message {message_id}, skipping")
            await self.__commit_if_appropriate(
                message, None, success=False, is_terminal_error=True,
                in_flight=in_flight,
            )
            return False

        if not await self.__delay_if_retry_not_ready(parsed_message, message_id):
            # Shutdown interrupted the backoff wait: nothing was processed,
            # so the offset must not be committed past.
            await self.__resolve_offset(in_flight, done=False)
            return False

        stable_message_id = self._get_stable_message_id(message, parsed_message)
        record_lock_id = concurrency.work_key(parsed_message.payload, stable_message_id)
        record_pool = f"record:{record_lock_id}"

        # Route the active-pipeline permit by tier, from the record event's own
        # extension/mimeType. The permit is held for the record's whole
        # lifetime — including the wait for a parse slot — so a single shared
        # budget would let a queue of Docling PDFs hold every permit while
        # light records that finish in seconds never get admitted at all.
        # Resolved once here, then passed down: with a MAX_CONCURRENT_INDEXING
        # too small to split, the light tier is collapsed away and every
        # record routes to heavy (see effective_index_tier). The gate, the
        # lease limit and the lease pool name all have to agree on that.
        index_tier = concurrency.dispatch_tier(self, parsed_message)
        class_ctx = concurrency.class_context(self, parsed_message.payload)  # None unless PARSE_ADMISSION=classes
        index_lease_pool = concurrency.admission_lease_pool(self, index_tier)
        success = False
        # Set where an exit is neither a success nor a failure; the
        # index.end falls back to success/shutting_down otherwise.
        outcome: str | None = None
        timeline, timeline_token = timing.begin_record(
            parsed_message.payload.get("recordId"),
            parsed_message.eventType,
            index_tier.value,
        )

        async def give_up(reason: str, attempts: int) -> None:
            nonlocal abandoned, settling
            settling = True
            # Our own record lease reads as a live holder, which keeps the
            # sink from failing a record left IN_PROGRESS.
            if distributed_leases.discard(record_pool) is not None:
                await self._release_distributed_slot(record_pool, lease_owner)
            await self.__abandon_message(
                message_id, stable_message_id, parsed_message, reason=reason, attempts=attempts,
            )
            if await self.__settle_noted(message, in_flight):
                timing.note(disposition="dead_letter")
            abandoned = True

        try:
            # Taken before any permit, so suspects queue here without holding
            # one; one that will be given up on below does not need it.
            unfinished = await concurrency.unfinished_attempts(self, record_lock_id)
            if 0 < unfinished < concurrency.MAX_UNFINISHED_ATTEMPTS:
                self._suspects_waiting += 1
                try:
                    await self._suspects.acquire()
                finally:
                    self._suspects_waiting -= 1
                suspect_held = True

            # The active-pipeline bound. Without this outer permit, parsed
            # records can accumulate while waiting for an indexing permit and
            # every one can remain IN_PROGRESS in the DB.
            index_admission = await concurrency.at_gate(
                waiter_token, concurrency.acquire_index_slot(self, index_tier, **concurrency.class_kwargs(class_ctx))
            )
            indexing_held = True
            timeline.mark(timing.ADMITTED)
            if self._slot_share is not None:
                self._slot_share.admitted(
                    (message.topic, message.partition, message.offset)
                )
            if waiter_token is not None:
                waiter_token.admit()

            if self.concurrency_manager is not None:
                # Taken only once the local permit is held: stale-record
                # recovery reads this lease as proof of active processing, so
                # a task still queued on the gate must not own one.
                with timing.stage("lease.index"):
                    index_lease_held = await concurrency.in_phase(
                        self,
                        concurrency.PHASE_LEASE,
                        index_tier,
                        parsed_message,
                        self._acquire_distributed_slot(
                            index_lease_pool,
                            lease_owner,
                            concurrency.index_ceiling(self, index_tier),
                            leases=distributed_leases,
                        ),
                        pool=index_lease_pool,
                    )
                if not index_lease_held:
                    # Capacity leases only give up when self.running flips,
                    # so this is a clean shutdown: redeliver, do not commit.
                    outcome = "stopped"
                    settling = True
                    await self.__resolve_offset(in_flight, done=False)
                    return False
                lease_handle, renewal_task = concurrency.start_lease_guard(
                    self, lease_owner
                )

                with timing.stage("lease.record"):
                    record_lease_held = await self._acquire_distributed_slot(
                        record_pool,
                        lease_owner,
                        1,
                        deadline_seconds=messaging_env.record_lease_wait_seconds,
                        leases=distributed_leases,
                    )
                if not record_lease_held:
                    if not self.running:
                        outcome = "stopped"
                        settling = True
                        await self.__resolve_offset(in_flight, done=False)
                        return False
                    # Re-published, not left unresolved: redelivery by offset
                    # would pin the commit watermark for the whole partition.
                    raise concurrency.RecordLeaseHeld(
                        f"{record_pool} not free within "
                        f"{messaging_env.record_lease_wait_seconds:.0f}s",
                        delay_s=messaging_env.record_lease_wait_seconds,
                    )

            parsed_message.payload["_processing_started_at"] = int(time.time() * 1000)
            stamp_delivery_id(parsed_message.payload, stable_message_id, parsed_message.org_ctx)

            # Counted only now that the record is ours: a crash while it waited
            # for a permit or for its lease was not its doing.
            unfinished = await concurrency.start_attempt(self, record_lock_id)
            attempt_started = True
            if unfinished >= concurrency.MAX_UNFINISHED_ATTEMPTS:
                timing.note(reason="unfinished_attempts")
                await give_up(
                    f"{unfinished} earlier attempts never finished; it is likely "
                    "taking the indexing service down",
                    unfinished,
                )
                return False

            # Check current retry count to predict if this will be the final attempt on failure
            current_retry_count = await self._get_retry_count(stable_message_id)
            timing.note(attempt=current_retry_count + 1)

            will_be_final_on_failure = (
                not self.retry_manager
                or current_retry_count >= messaging_env.max_delivery_attempts - 1
            )

            # Set flag on message so handler knows whether to update DB status on failure
            parsed_message.is_final_failure = will_be_final_on_failure

            if self.message_handler:
                record_kind = concurrency.record_parse_kind(parsed_message.payload)
                processing_timeout = messaging_env.record_processing_timeout_for(
                    parsed_message.payload.get("extension"),
                    parsed_message.payload.get("mimeType"),
                )

                progress = new_progress()
                begin_progress(progress)

                processing_budget: asyncio.Timeout | None = None

                async def consume_handler_events() -> None:
                    nonlocal download_admission, memory_admission, parsing_held, indexing_held, success, shutting_down, parsing_admission, parse_lease_pool, class_ctx, processing_budget
                    event_gen = self.message_handler(parsed_message)
                    try:
                        async with asyncio.timeout(processing_timeout) as budget:
                            processing_budget = budget
                            progress.attach(budget)
                            async for event in event_gen:
                                if (
                                    event.event == IndexingEvent.START_DOWNLOAD
                                    and memory_admission is None
                                    and download_admission is None
                                ):
                                    # Before the download: a record waiting
                                    # for memory, or behind the heavy records
                                    # already holding content for a parse
                                    # slot, must not hold its own.
                                    async with concurrency.parse_admission_wait(
                                        self, budget, message_id
                                    ) as wait:
                                        # Place first: a record waiting for memory
                                        # must not hold what a place holder needs,
                                        # nor the reverse (one cycle, both time out).
                                        download_admission = await concurrency.in_phase(
                                            self,
                                            concurrency.PHASE_DOWNLOAD,
                                            index_tier,
                                            parsed_message,
                                            concurrency.acquire_download_slot(
                                                self, index_tier, timeout=wait.remaining(),
                                                **concurrency.class_kwargs(
                                                    class_ctx, size_bytes=event.data.size_bytes if event.data else None
                                                ),
                                            ),
                                        )
                                        memory_admission = await concurrency.reserve_record_memory(
                                            self,
                                            index_tier,
                                            event.data.size_bytes if event.data else None,
                                            timeout=wait.remaining(),
                                            kind=record_kind,
                                            **concurrency.class_kwargs(class_ctx),
                                        )
                                elif (
                                    event.event == IndexingEvent.START_PARSING
                                    and not parsing_held
                                ):
                                    parse_tier = event.data.tier if event.data else None
                                    timeline.mark(timing.PARSE_REQUESTED)
                                    parse_lease_pool = concurrency.admission_parse_lease_pool(self, parse_tier)
                                    # Node-local gate first, cluster lease
                                    # second. The gate is an asyncio Event and
                                    # costs nothing to queue on, so it absorbs
                                    # the wait; only records it has already
                                    # admitted go on to contend for the Redis
                                    # lease. Taking the lease first put the
                                    # whole queue on Redis instead, each waiter
                                    # re-polling on a timer — which is how a
                                    # backlog turned into a Redis outage.
                                    async with concurrency.parse_admission_wait(
                                        self, budget, message_id
                                    ) as wait:
                                        size_bytes = event.data.size_bytes if event.data else None
                                        # The size and what the inspection found are known now: the index permit
                                        # moves to its class in place.
                                        if class_ctx is not None and event.data is not None:
                                            class_ctx = class_ctx.inspected(event.data.pages, scanned=event.data.scanned, cells=event.data.cells)
                                        concurrency.reclass_index_slot(index_admission, class_ctx, size_bytes)
                                        download_admission = await concurrency.reclass_download_slot(
                                            download_admission, class_ctx, size_bytes, wait.remaining()
                                        )
                                        memory_admission = await concurrency.reserve_record_memory(
                                            self, parse_tier, size_bytes,
                                            timeout=wait.remaining(), downloaded=True,
                                            current=memory_admission, kind=record_kind,
                                            **concurrency.class_kwargs(class_ctx),
                                        )
                                        parsing_admission = await concurrency.in_phase(
                                            self,
                                            concurrency.PHASE_PARSE,
                                            parse_tier,
                                            parsed_message,
                                            concurrency.acquire_parsing_slot(
                                                self,
                                                parse_tier,
                                                size_bytes,
                                                timeout=wait.remaining(),
                                                kind=record_kind,
                                                reserved_bytes=memory_admission.cost if memory_admission else 0,
                                                **concurrency.class_kwargs(class_ctx),
                                            ),
                                        )
                                        # Set before the lease attempt so the
                                        # wrapper's finally hands this permit
                                        # back on every exit path below.
                                        parsing_held = True
                                        lease_held = (
                                            self.concurrency_manager is None
                                            or await concurrency.in_phase(
                                                self,
                                                concurrency.PHASE_LEASE,
                                                parse_tier,
                                                parsed_message,
                                                self._acquire_distributed_slot(
                                                    parse_lease_pool,
                                                    lease_owner,
                                                    concurrency.parse_ceiling(self, parse_tier),
                                                    deadline_seconds=wait.remaining(),
                                                    leases=distributed_leases,
                                                ),
                                                pool=parse_lease_pool,
                                            )
                                        )
                                        if not lease_held and self.running:
                                            raise wait.timed_out(parse_lease_pool)
                                    if not lease_held:
                                        # A capacity lease only gives up when
                                        # self.running flips (it fails open on
                                        # error), so this is a clean shutdown —
                                        # abort without raising rather than
                                        # burning a retry attempt.
                                        shutting_down = True
                                        return
                                    timeline.mark(timing.PARSE_ADMITTED)
                                    # The permit and lease now account for the content.
                                    concurrency.release_admission(download_admission)
                                    self.logger.debug(
                                        f"Acquired parsing slot for {message_id}"
                                    )
                                elif (
                                    event.event == IndexingEvent.PARSING_COMPLETE
                                    and parsing_held
                                ):
                                    timeline.mark(timing.PARSED)
                                    distributed_leases.discard(parse_lease_pool)
                                    await self._release_distributed_slot(
                                        parse_lease_pool, lease_owner
                                    )
                                    concurrency.release_admission(parsing_admission)
                                    parsing_admission = None
                                    parsing_held = False
                                    self.logger.debug(
                                        f"Released parsing slot for {message_id}"
                                    )
                                elif (
                                    event.event == IndexingEvent.INDEXING_COMPLETE
                                    and indexing_held
                                ):
                                    distributed_leases.discard(index_lease_pool)
                                    await self._release_distributed_slot(
                                        index_lease_pool, lease_owner
                                    )
                                    concurrency.release_admission(index_admission)
                                    concurrency.release_admission(memory_admission)
                                    concurrency.release_admission(download_admission)
                                    indexing_held = False
                                    self.logger.debug(
                                        f"Released indexing gate for {message_id}"
                                    )
                                    success = True
                                    break
                        # The record is done once INDEXING_COMPLETE is out; the
                        # handler's bookkeeping after it (releasing queued copies)
                        # must not fail a finished record on the processing deadline.
                        await concurrency.finish_after_indexing_complete(event_gen)
                    finally:
                        # Closing delivers GeneratorExit so the handler's own
                        # cleanup (reverting IN_PROGRESS) runs on every exit,
                        # including cancellation while parked on a slot. It sits
                        # outside the timeout so a deadline passing mid-cleanup
                        # cannot cut that cleanup short, orphan inner generators
                        # (their GC finalizer then runs in another Context and
                        # ContextVar.reset fails), or turn the real error into a
                        # TimeoutError.
                        await event_gen.aclose()

                handler_task: asyncio.Task[None] | None = None
                try:
                    handler_task = asyncio.create_task(consume_handler_events())
                    if renewal_task is not None:
                        done, _pending = await asyncio.wait(
                            {handler_task, renewal_task},
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if handler_task not in done:
                            # The shared renewer marked this owner's leases
                            # lost; the rest of the fleet may already have
                            # reassigned the record, so stop rather than keep
                            # working under a lease we no longer hold.
                            handler_task.cancel()
                            await asyncio.gather(handler_task, return_exceptions=True)
                            raise concurrency.lease_guard_error(lease_handle)
                    await handler_task
                except TimeoutError:
                    if processing_budget is not None and processing_budget.expired():
                        timing.note(timeout_s=processing_timeout)
                    elif success:  # the bookkeeping after INDEXING_COMPLETE ran out of its own time, not the record's budget
                        timing.note(phase="after_indexing_complete")
                    await concurrency.requeue_after_productive_cut(
                        self, progress, lease_handle, message_id
                    )
                    raise
                finally:
                    if handler_task is not None and not handler_task.done():
                        handler_task.cancel()
                        await asyncio.gather(
                            handler_task,
                            return_exceptions=True,
                        )
                    if renewal_task is not None:
                        renewal_task.cancel()
                        await asyncio.gather(renewal_task, return_exceptions=True)
                        renewal_task = None
                    end_progress()
            else:
                self.logger.error(f"No message handler available for {message_id}")
                settling = True
                await self.__commit_if_appropriate(
                    message, parsed_message, success=False, is_terminal_error=True,
                    in_flight=in_flight,
                )
                return False

            if shutting_down:
                # Consumer stopped while waiting for the parsing slot: leave
                # the offset uncommitted (redelivered on restart) instead of
                # committing/retrying, matching the indexing/record lease
                # gates above which already just return on shutdown.
                self.logger.debug(
                    f"Consumer stopping, abandoning {message_id} without commit"
                )
                settling = True
                await self.__resolve_offset(in_flight, done=False)
                return False

            # Commit based on success
            settling = True
            await self.__commit_if_appropriate(
                message, parsed_message, success=success, in_flight=in_flight
            )
            return success

        except asyncio.CancelledError:
            # A BaseException, so the recovery below cannot see it: no failure
            # is counted, nothing is re-queued and nothing is committed. That is
            # the correct outcome — the offset resolves as redeliver — but it
            # has to be visible, and it has to name the record, because the
            # handler has already written a status on the way out: index.end says it.
            timing.note(reason="cancelled")
            raise
        except concurrency.RequeueWithoutAttempt as e:
            # Queue time, not a failure (a parse-slot wait that ran out, or a stage
            # whose dependency is paused): hand the message back with no attempt
            # counted, so it cannot be dead-lettered for waiting.
            outcome = "requeued"
            timing.note(code=type(e).__name__, pool=getattr(e, "pool", None), end_level=e.log_level)
            if parsed_message is not None:
                if not e.counts_toward_backstop:
                    # A clean run that found a dependency down, not a crash loop: its
                    # growing delay bounds how often it returns while the outage lasts.
                    await concurrency.clear_delivery_count(self, stable_message_id)
                else:
                    # A re-queue has a failure count of zero; only this counter
                    # stops it coming back for ever.
                    cycles, backstop_tripped = await concurrency.record_delivery(
                        self, stable_message_id
                    )
                    if backstop_tripped:
                        timing.note(reason="backstop")
                        await give_up(
                            concurrency.backstop_give_up_reason(e, cycles, messaging_env.redis_max_deliveries),
                            cycles,
                        )
                        return False
                try:
                    await self._requeue_message(
                        topic, parsed_message, stable_message_id, retry_count=0, delay_s=e.delay_s,
                        **concurrency.first_admission_kwargs(e),
                    )
                except Exception as requeue_error:
                    # Kept for redelivery below.
                    timing.note(disposition="retry", reason="requeue_failed")
                    log_failure(
                        self.logger, "index.requeue_failed", requeue_error,
                        level=logging.WARNING, include_error_text=False,
                    )
                    if self._scheduler is None:
                        # The future's callback seeks back here. Returning would
                        # let the next message's commit cover this offset.
                        raise
                    await self.__rebuffer_after_failed_handback(
                        message, parsed_message, in_flight, e.delay_s
                    )
                    return False
            # Published again: a failed commit or a revoked partition says so on index.end, not "requeued".
            await self.__settle_noted(message, in_flight)
            return False
        except Exception as e:
            timing.note(exc=e)  # index.end writes it once, after the decision below
            if settling:
                raise  # settlement started: settling again would charge the handler and publish a copy
            if success:
                outcome = "failed"  # the handler raised after INDEXING_COMPLETE: a failed commit later does not make it a success
            concurrency.report_memory_incident_if_applicable(self, message_id, e)

            # Classify the exception to determine if we should retry
            error_type = MessageErrorClassifier.classify_by_exception(e)
            is_terminal = error_type == MessageErrorType.TERMINAL

            # Update is_final_failure on the message for terminal errors
            # (it was already set for transient based on retry count prediction)
            if is_terminal and parsed_message:
                parsed_message.is_final_failure = True

            await self.__commit_if_appropriate(
                message, parsed_message, success=False,
                is_terminal_error=is_terminal, in_flight=in_flight,
            )
            return False
        finally:
            if suspect_held:
                self._suspects.release()
            concurrency.release_admission(memory_admission)
            concurrency.release_admission(download_admission)
            try:
                # Ensure semaphores are released even on error
                if renewal_task is not None:
                    renewal_task.cancel()
                    await asyncio.gather(renewal_task, return_exceptions=True)

                # Before the releases below, not after: every one of them awaits,
                # so a cancellation landing in this block would skip the
                # unregister and leave the renewer refreshing this owner's leases
                # forever. Unregistering first means the worst case is a lease
                # that outlives the record until its TTL expires, which is what
                # the TTL is for.
                if self.lease_renewer is not None:
                    self.lease_renewer.unregister(lease_owner)
                # Any exit this process lives through finishes the attempt; only
                # a crash leaves it counted.
                if attempt_started:
                    await concurrency.finish_attempt(
                        self, record_lock_id, settled=success or abandoned
                    )
                # Leases lost to the renewal deadline are gone from Redis
                # already; releasing them is doomed traffic against a Redis that
                # is still failing, and it arrives from every record at once.
                release_leases = not (
                    lease_handle is not None and getattr(lease_handle, "lost_to_deadline", False)
                )
                if not release_leases:
                    self.logger.debug(
                        "Skipping distributed lease release for %s: leases expired with the renewal deadline",
                        lease_owner,
                    )

                if parsing_held:
                    if distributed_leases.discard(parse_lease_pool) is not None and release_leases:
                        await self._release_distributed_slot(parse_lease_pool, lease_owner)
                    concurrency.release_admission(parsing_admission)
                    parsing_admission = None
                    self.logger.debug(f"Released parsing slot in finally for {message_id}")

                if indexing_held:
                    if distributed_leases.discard(index_lease_pool) is not None and release_leases:
                        await self._release_distributed_slot(index_lease_pool, lease_owner)
                    concurrency.release_admission(index_admission)
                    self.logger.debug(f"Released indexing gate in finally for {message_id}")

                for pool, owner in distributed_leases.snapshot():
                    distributed_leases.discard(pool)
                    if release_leases:
                        await self._release_distributed_slot(pool, owner)
            finally:
                # After the releases, so slow cleanup counts toward the hold;
                # in a finally, so a release that raises or is cancelled
                # still writes index.end.
                timeline.mark(timing.RELEASED)
                timing.end_record(
                    timeline,
                    timeline_token,
                    outcome=outcome or (
                        "ok" if success else ("stopped" if shutting_down else "failed")
                    ),
                    logger=self.logger,
                )


