import asyncio
import json
import math
import re
import threading
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from concurrent.futures import CancelledError as FuturesCancelledError
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import wait as futures_wait
from logging import Logger
from typing import TYPE_CHECKING, Any, Optional, override

from pydantic import ValidationError

from app.core.envelope import EnvelopeRejected, ctx_of, open_stream_message
from app.core.org_context import bind_optional, spawn_detached
from app.core.org_context import pop as pop_org
from app.core.org_context import push as push_org
from app.services.distributed.interface import IDistributedLeaseManager, IRetryTracker
from app.services.messaging import consumer_concurrency as concurrency
from app.services.messaging.config import (
    IndexingEvent,
    IndexingMessageHandler,
    RedisStreamsConfig,
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
    format_exception_chain,
    is_expected_outcome,
)
from app.services.messaging.interface.consumer import IMessagingConsumer
from app.services.messaging.interface.producer import IMessagingProducer
from app.services.messaging.lanes.assignment import read_lane_map, write_lane_count
from app.services.messaging.lanes.backlog import LaneBacklog, redis_lanes_for_key
from app.services.messaging.lease import LeaseRenewer
from app.services.messaging.membership import register_group
from app.services.messaging.redis_streams.backlog import read_stream_backlog_detail
from app.services.messaging.redis_streams.retention import (
    DEFAULT_INTERVAL_SECONDS,
    LEADER_KEY,
    StreamRetention,
    retained_streams,
    run_retention_loop,
)
from app.services.messaging.redis_streams.stream_read_planner import StreamReadPlanner
from app.services.messaging.scheduling.drr_scheduler import DRRScheduler
from app.services.messaging.scheduling.interface import (
    EnqueueResult,
    FairnessKey,
    FairnessKeyExtractor,
    FairSchedulerConfig,
    WeightProvider,
)
from app.services.messaging.scheduling.key_extractors import (
    CompositeKeyExtractor,
    TieredKeyExtractor,
)
from app.services.messaging.scheduling.slot_share import SlotShare, SlotSharePlan
from app.services.messaging.storage_protocol import tagged
from app.services.redis.config import ClientOptions, RedisConnectionConfig
from app.services.redis.connection_provider_factory import get_redis_provider
from app.services.resource_governor import ParseTier, Pool
from app.telemetry.modules import scheduling_metrics as metrics
from app.utils import indexing_timing as timing
from app.utils.cpu_offload import offload_if_large
from app.utils.loop_lag_monitor import LoopLagMonitor, loop_lag_monitor_from_env
from app.utils.request_context import (
    bind_context,
    context_from_envelope,
    reset_context,
    run_in_context,
)

if TYPE_CHECKING:
    from app.services.messaging.backpressure import BackpressureCoordinator
    from app.services.messaging.lanes.assignment import LaneEntry
    from app.services.messaging.worker_loop import WorkerLoop
    from app.services.redis.connection_provider import IRedisConnectionProvider
    from app.services.redis.connection_provider import RedisClient as Redis
    from app.services.resource_governor import ResourceGovernor

_BUSYGROUP_ERROR = "BUSYGROUP"
_STREAM_TYPE = "stream"
# ``record-events.3`` -- a lane stream, as opposed to its base topic.
_LANE_SUFFIX = re.compile(r"\.\d+$")
# Sentinel CompositeKeyExtractor uses for an absent fairness field.
_DEFAULT_KEY_LEVEL = "__default__"
# Pending-list inspection: held entries occupy the head of the list, so the
# scan has to be able to page past them to find genuinely unrecovered work.
_PENDING_SCAN_PAGE = 100
_PENDING_SCAN_MAX_PAGES = 20
_MESSAGE_VALUE_FIELD = "value"
_MAIN_LOOP_OP_TIMEOUT = 5.0
# How often the retry-backoff wait re-checks self.running, so a shutdown
# request can interrupt a long (up to 300s) wait instead of holding an
# active-future slot for the full delay (see __delay_if_retry_not_ready).
_DELAY_POLL_INTERVAL_SECONDS = 1.0
# XREADGROUP block used while the scheduler still has buffered work.
_BUSY_BLOCK_MS = 50
# Pending entries this consumer is not holding are looked for this often
# while it reads (see __recover_while_reading), and at most this many are
# taken back per look. The batch matches __recovery_claim_budget's cap.
_RECOVERY_INTERVAL_SECONDS = 1.0
_RECOVERY_BATCH = 10
# A held entry whose ownership was last confirmed this fraction of the
# idle-claim window ago has missed a refresh (they run every third of it),
# so a peer may be about to claim it (see __release_entries_at_risk).
_AT_RISK_FRACTION = 2 / 3


def _loads_possibly_double_encoded(value: str) -> object:
    """Decode an envelope that producers sometimes JSON-encode twice."""
    raw = json.loads(value)
    if isinstance(raw, str):
        raw = json.loads(raw)
    return raw


class RedisAcknowledgementError(RuntimeError):
    """The stream entry could not be confirmed as acknowledged."""


class IndexingRedisStreamsConsumer(IMessagingConsumer):
    """Redis Streams consumer with nested concurrency control for indexing.

    MAX_CONCURRENT_INDEXING bounds active handlers across the full pipeline;
    MAX_CONCURRENT_PARSING further bounds parsing within that active set.
    Uses RetryManager for failure-based retry counting (Redis times_delivered
    counts every read/delivery, not actual processing failures).
    Error classification is based purely on exception type, not database status.
    Pending messages (failed retries) are processed only when no new messages
    arrive (idle-based retry).
    """

    def __init__(
        self,
        logger: Logger,
        config: RedisStreamsConfig,
        retry_manager: IRetryTracker | None = None,
        producer: IMessagingProducer | None = None,
        concurrency_manager: IDistributedLeaseManager | None = None,
        governor: Optional["ResourceGovernor"] = None,
        backpressure_coordinator: Optional["BackpressureCoordinator"] = None,
        fair_scheduler_config: FairSchedulerConfig | None = None,
        key_extractor: FairnessKeyExtractor | None = None,
        weight_provider: WeightProvider | None = None,
        disposition_sink: Optional[AbandonedMessageSink] = None,
        provider: "IRedisConnectionProvider | None" = None,
        connector_off_filter: ConnectorOffFilter | None = None,
        stage_admission: "concurrency.StageAdmission | None" = None,
        worker: "WorkerLoop | None" = None,
    ) -> None:
        self.logger = logger
        self.config = config
        if config:
            register_group(config.group_id, list(config.topics))
        self._provider: "IRedisConnectionProvider" = provider or get_redis_provider(
            RedisConnectionConfig.from_redis_config(config)
        )
        self._planner = StreamReadPlanner(self._provider)
        # PEL ownership is keyed by consumer name; sharing one across replicas
        # lets one process re-read another process's still-active messages.
        self.consumer_name = f"{tagged(config.client_id)}-{uuid.uuid4().hex}"
        self.retry_manager = retry_manager
        # Told about every message this consumer gives up on, before the XACK
        # that makes it unrecoverable — see disposition.AbandonedMessageSink.
        self.disposition_sink = disposition_sink
        # Settles, as they are read, the events the handler would only skip
        # because their connector is off or gone -- see connector_off.py.
        self.connector_off_filter = connector_off_filter
        self.producer = producer
        self.concurrency_manager = concurrency_manager
        # When set, node-local parsing/indexing admission is delegated to the
        # ResourceGovernor's adaptive gates instead of the static semaphores
        # below (see consumer_concurrency.acquire_parsing_slot/index_ceiling).
        self.governor = governor
        if stage_admission is not None and governor is not None:
            raise ValueError("a pipeline-stage consumer admits through its own limit, not a governor")
        self.stage_admission = stage_admission
        # Shared by every indexing consumer in the process (see WorkerLoop); None runs a loop of
        # this consumer's own.
        self._shared_worker = worker
        # Shared with the ParsingClient/DoclingClient/EmbeddingServerEmbeddings
        # instances that this consumer's records flow through — see
        # app.services.messaging.backpressure. Reading is paused whenever any
        # of them last saw a 429+Retry-After, instead of pulling more work a
        # saturated downstream would just reject again.
        self.backpressure_coordinator = backpressure_coordinator
        self._downstream_backpressure_active = False
        self._distributed_log_times: dict[str, float] = {}
        self.redis: Redis | None = None
        self.running = False
        self.consume_task: asyncio.Task | None = None
        self._retention_task: asyncio.Task | None = None
        self.worker_executor: ThreadPoolExecutor | None = None
        self.worker_loop: asyncio.AbstractEventLoop | None = None
        self.worker_loop_ready = threading.Event()
        # Guards publishing worker_loop against a concurrent stop request, so
        # a stop that arrives before the loop exists is not lost.
        self._worker_loop_lock = threading.Lock()
        self._worker_stop_requested = False
        self.main_loop: asyncio.AbstractEventLoop | None = None
        # Legacy fallback only: unused (stay None) once a governor is set.
        self.parsing_semaphore: asyncio.Semaphore | None = None
        self.indexing_semaphore: Any = None
        # One renewer for every lease this process holds, started on the
        # worker loop beside the records it guards (see lease.LeaseRenewer).
        self.lease_renewer: LeaseRenewer | None = None
        self.loop_lag_monitor: LoopLagMonitor | None = None
        self.message_handler: IndexingMessageHandler | None = None
        self._active_futures: set[Future[bool]] = set()
        self._futures_lock = threading.Lock()
        self.gate_waiters = concurrency.GateWaiters()
        self._backpressure_active = False
        self._consecutive_empty_polls = 0
        self._idle_threshold = 3  # Drain pending after N consecutive empty polls
        # (stream, entry id): Redis entry ids are only unique within one
        # stream, and lane streams are written in the same milliseconds, so
        # an id alone can name entries of two lanes. The stream is "" when a
        # caller does not know it.
        self._in_flight_message_ids: set[tuple[str, str]] = set()
        # Keyed by recordId, not by entry id. Two entries can carry the same
        # record -- the original and one the stranded sweep re-published -- and
        # the message-id set above cannot see that they are the same work.
        self._in_flight_record_ids: set[str] = set()
        self._in_flight_lock = threading.Lock()
        # Held by a record whose last attempt never finished, so such records
        # run one at a time: when the process dies again, the record that
        # took it down is the only suspect in flight. Used on the worker loop.
        self._suspects = asyncio.Lock()

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
            DRRScheduler[tuple[str, str, dict[str, str], StreamMessage, float]] | None
        ) = None
        # Entries this consumer already owns in its PEL but had no buffer
        # room for. They are re-offered at the top of every read iteration:
        # leaving them for _drain_pending would strand them, because that
        # only runs after several consecutive *empty* polls, which never
        # happens during the sustained backlog this scheduler exists for.
        self._deferred_entries: deque[
            tuple[str, str, dict[str, str], StreamMessage, FairnessKey, float]
        ] = deque()
        # Entries this consumer already holds: buffered in the scheduler or
        # parked for want of room. They stay un-ACKed in the pending list by
        # design, so the recovery path would otherwise claim them again while
        # they wait.
        # (stream, entry id), for the same reason as the in-flight set.
        self._held_entries: set[tuple[str, str]] = set()
        # Lanes that returned entries on the most recent read. Skipping a
        # blocked lane is only sound while some *other* lane is actually
        # producing; an idle lane is not an alternative source of work.
        self._lanes_with_data: set[str] = set()
        self._last_ownership_refresh = 0.0
        # When each stream's held entries last had their ownership confirmed
        # (monotonic). An entry is only released when this goes stale.
        self._ownership_confirmed_at: dict[str, float] = {}
        # Read-phase recovery: when it last looked, and which lane it
        # starts from next.
        self._last_recovery = 0.0
        self._recovery_lane_offset = 0
        # Each connector's records in flight per index slot pool, so that
        # while another connector is waiting no one connector can hold more
        # than its share of the slots (see scheduling.slot_share).
        self._slot_share: SlotShare | None = None
        if self.fair_scheduler_config.enabled:
            self._scheduler = DRRScheduler(self.fair_scheduler_config, self.weight_provider)
            self._slot_share = SlotShare(
                self.fair_scheduler_config.entity_depth, self.weight_provider
            )


    @override
    async def initialize(self) -> None:
        try:
            self._start_worker_thread()

            if not self.worker_loop_ready.wait(timeout=60.0):
                raise RuntimeError("Worker thread event loop not initialized in time")

            if not self.worker_loop or not self.worker_loop.is_running():
                raise RuntimeError("Worker thread event loop failed to start")

            self.redis = self._provider.create_client(
                ClientOptions(
                    decode_responses=True,
                    blocking=True,
                    # One connection is parked in XREADGROUP BLOCK for up to
                    # `block_ms` at a time; XACK/XAUTOCLAIM run on the same
                    # client and must not queue behind it. Sized off the same
                    # knob the lease/retry pools use rather than ClientOptions'
                    # conservative default.
                    max_connections=messaging_env.concurrency_redis_max_connections,
                )
            )
            await self.redis.ping()

            await self.__adopt_existing_lane_streams()
            await self.__record_lane_count()

            for topic in self.config.topics:
                try:
                    await self.redis.xgroup_create(  # type: ignore
                        topic,
                        self.config.group_id,
                        id="0",
                        mkstream=True,
                    )
                    self.logger.info(
                        "Created consumer group %s for stream %s",
                        self.config.group_id,
                        topic,
                    )
                except Exception as e:
                    if _BUSYGROUP_ERROR in str(e):
                        self.logger.debug(
                            "Consumer group %s already exists for %s",
                            self.config.group_id,
                            topic,
                        )
                    else:
                        raise

            self.logger.info(
                "Successfully initialized Redis Streams consumer for indexing"
            )
        except Exception as e:
            self.logger.error("Failed to create consumer: %s", e)
            await self.stop()
            raise

    async def __record_lane_count(self) -> None:
        """Tell producers how many lanes this consumer reads, in the lane map's
        meta hash, so a producer whose own setting differs still places
        connectors on lanes that are read.

        Best-effort: a producer without it uses its own configured count.
        """
        if self.redis is None:
            return
        lane_count = messaging_env.fair_scheduling_lane_count
        if lane_count <= 1:
            return
        for topic in messaging_env.fair_scheduling_laned_topics:
            if topic not in self.config.topics:
                continue
            try:
                await write_lane_count(self.redis, topic, lane_count)
            except Exception as e:
                self.logger.warning(
                    "Could not record the lane count for %s; producers use "
                    "their own setting: %s",
                    topic,
                    e,
                )

    async def __adopt_existing_lane_streams(self) -> None:
        """Subscribe to lane streams that exist but are not configured.

        Lane count is a deployment setting, and lowering it would otherwise
        silently orphan whatever is still sitting in the lanes that dropped
        out of the configured range -- not lost, but unread until someone
        raises the count again. Discovering the lanes that actually exist
        makes the consumer drain them regardless, so reducing the lane count
        is safe without a manual drain step first.

        Best-effort: a scan failure just leaves the configured subscription
        as-is rather than blocking startup.
        """
        if self.redis is None:
            return
        bases = [
            topic for topic in self.config.topics if not _LANE_SUFFIX.search(topic)
        ]
        if not bases:
            return

        known = set(self.config.topics)
        discovered: list[str] = []
        try:
            for base in bases:
                async for key in self.redis.scan_iter(match=f"{base}.*"):
                    name = key.decode() if isinstance(key, bytes) else str(key)
                    if name in known or not _LANE_SUFFIX.search(name):
                        continue
                    if await self.redis.type(name) != _STREAM_TYPE:  # type: ignore
                        continue
                    known.add(name)
                    discovered.append(name)
        except Exception as e:
            self.logger.warning(
                "Could not scan for existing lane streams; consuming only the "
                "configured ones: %s",
                e,
            )
            return

        if discovered:
            self.config.topics = [*self.config.topics, *sorted(discovered)]
            self.logger.info(
                "Adopted %d lane stream(s) that exist but are not in the "
                "configured lane range, so they drain rather than stranding: %s",
                len(discovered),
                ", ".join(sorted(discovered)),
            )

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
            self.parsing_semaphore = asyncio.Semaphore(messaging_env.max_concurrent_parsing)
            self.indexing_semaphore = asyncio.Semaphore(
                self.stage_admission.limit if self.stage_admission else messaging_env.max_concurrent_indexing
            )
            self.logger.info(
                "Worker thread event loop started with semaphores initialized"
            )
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
        """Called on the shared loop's thread when it dies: stops reads, dispatch and PEL recovery,
        so no XAUTOCLAIM spends a delivery attempt on work no loop can run."""
        self.running = False

    def _start_worker_thread(self) -> None:
        if self._shared_worker is not None:
            self.worker_loop_ready.clear()
            loop = self._shared_worker.start()
            self.worker_loop = loop
            self._shared_worker.add_exit_listener(self._stop_taking_work)
            self._shared_worker.call(lambda: self._on_worker_loop_started(loop))
            # Reported once, under the record consumer: every consumer on the loop sees the same lag.
            self.loop_lag_monitor = self._shared_worker.lag_monitor if self.stage_admission is None else None
            self.worker_loop_ready.set()
            return

        def run_worker_loop() -> None:
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
            try:
                self.worker_loop.run_forever()
            finally:
                if self.loop_lag_monitor is not None:
                    self.loop_lag_monitor.stop()
                pending = asyncio.all_tasks(self.worker_loop)
                for task in pending:
                    task.cancel()
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

        self.worker_loop_ready.clear()
        self._worker_stop_requested = False
        self.worker_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="indexing-worker"
        )
        self.worker_executor.submit(run_worker_loop)

    @override
    async def cleanup(self) -> None:
        try:
            await self.stop()
        except Exception as e:
            self.logger.error("Error during cleanup: %s", e)

    async def start(  # type: ignore[override]
        self,
        message_handler: IndexingMessageHandler,
    ) -> None:
        try:
            self.running = True
            self.message_handler = message_handler
            self.main_loop = asyncio.get_running_loop()

            if not self.redis:
                await self.initialize()

            self.consume_task = spawn_detached(self._consume_loop())
            if self.config.retention_mode == "after_ack":
                self._retention_task = spawn_detached(self.__run_retention())
            self.logger.info(
                "Started Redis Streams consumer task with parsing_ceiling=%d, "
                "light_parsing_ceiling=%d, indexing_ceiling=%d",
                concurrency.parse_ceiling(self),
                concurrency.parse_ceiling(self, ParseTier.LIGHT),
                concurrency.index_ceiling(self),
            )
        except Exception as e:
            self.logger.error("Failed to start Redis Streams consumer: %s", e)
            raise

    async def __run_retention(self) -> None:
        # Imported here: the leader lock lives with the indexing sweeps.
        from app.modules.indexing.vector_membership_backfill import (
            VectorMembershipBackfillLeaderLock,
        )

        lock = VectorMembershipBackfillLeaderLock(
            self.logger, self.config, f"stream-retention:{uuid.uuid4().hex}",
            ttl_seconds=int(DEFAULT_INTERVAL_SECONDS) * 3, key=LEADER_KEY,
        )
        try:
            await run_retention_loop(
                StreamRetention(self.redis, self.logger, lock, no_group_maxlen=self.config.max_len),  # type: ignore[arg-type]
                retained_streams(),
            )
        finally:
            await lock.release()
            await lock.close()

    async def stop(  # type: ignore[override]
        self,
        message_handler: IndexingMessageHandler | None = None,
    ) -> None:
        self.logger.info("Stopping Redis Streams consumer...")
        self.running = False

        if self._retention_task:
            self._retention_task.cancel()
            self._retention_task = None
        if self.consume_task:
            self.consume_task.cancel()
            try:
                await self.consume_task
            except asyncio.CancelledError:
                self.logger.debug("Consume task cancelled")

        # Wait for in-flight tasks in a thread executor so the main event loop
        # stays responsive. Worker tasks schedule xack back onto this loop via
        # run_coroutine_threadsafe; blocking the loop here deadlocks those calls
        # and leaves messages stuck in the PEL.
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, self._stop_worker_thread)
        except Exception as exc:
            self.logger.error("Error stopping worker thread: %s", exc)

        self._deferred_entries.clear()
        self._held_entries.clear()
        if self._scheduler is not None:
            drained = self._scheduler.drain_all()
            if drained:
                self.logger.info(
                    "Discarded %d buffered message(s) from the fair-scheduling "
                    "queue at shutdown (left un-acked; redelivered on restart)",
                    len(drained),
                )

        if self.redis:
            try:
                await self._cleanup_empty_consumers(include_current=True)
                redis = self.redis
                self.redis = None
                await redis.aclose()
                self.logger.info("Redis Streams consumer stopped")
            except Exception as e:
                self.logger.error("Error stopping Redis Streams consumer: %s", e)

        # concurrency_manager/retry_manager are injected, not owned — closing
        # them here would break a restart (start() -> stop() -> start() reuses
        # the same instances) and duplicate indexing_main's own cleanup of
        # them. The creator (start_kafka_consumers/stop_kafka_consumers) is
        # responsible for their lifecycle.

    @override
    def is_running(self) -> bool:
        return self.running

    @override
    async def lane_backlog(self, topic: str) -> LaneBacklog:
        return await self._run_on_main_loop(self._read_lane_backlog(topic))

    async def _read_lane_backlog(self, topic: str) -> LaneBacklog:
        if self.redis is None:
            raise RuntimeError("Redis Streams consumer is not connected")
        streams = [
            stream
            for stream in self.config.topics
            if stream == topic
            or (stream.startswith(f"{topic}.") and _LANE_SUFFIX.search(stream))
        ]
        if not streams:
            raise RuntimeError(f"This consumer does not read {topic}")
        oldest, pending = await read_stream_backlog_detail(
            self.redis, self.config.group_id, streams
        )

        # The same settings the lane-aware producer routes by.
        laned = topic in messaging_env.fair_scheduling_laned_topics
        lane_count = messaging_env.fair_scheduling_lane_count if laned else 1
        key_field = messaging_env.fair_scheduling_lane_key_field
        # Read whatever the producers' switch says: after a rollback to
        # hashing, assigned lanes still hold events until they drain.
        assignments = await self.__read_lane_map(topic) if lane_count > 1 else {}

        def lanes_for_event(payload: Mapping[str, object]) -> set[str]:
            key = payload.get(key_field)
            return redis_lanes_for_key(
                topic,
                None if key in (None, "") else str(key),
                streams,
                lane_count,
                assignments,
            )

        return LaneBacklog(topic, oldest, lanes_for_event, pending)

    async def __read_lane_map(self, topic: str) -> "Mapping[str, LaneEntry] | None":
        """The lane map, once per backlog read; None if it cannot be read,
        which makes every lane one a record's event could be on."""
        try:
            return await read_lane_map(self.redis, topic)  # type: ignore[arg-type]
        except Exception as e:
            self.logger.warning(
                "Could not read the lane map for %s, so every lane counts as "
                "one a waiting record's event could be on: %s: %s",
                topic,
                type(e).__name__,
                e,
            )
            return None

    def _stop_worker_thread(self) -> None:
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
        else:
            with self._worker_loop_lock:
                self._worker_stop_requested = True
                loop = self.worker_loop
            # Requested even when the loop is not running yet: a stop queued before
            # run_forever() makes it return straight away, whereas skipping it
            # would leave the shutdown below waiting on a loop that never ends.
            if loop is not None and not loop.is_closed():
                try:
                    loop.call_soon_threadsafe(loop.stop)
                except RuntimeError:
                    # Closed by the worker between the check and the call.
                    self.logger.debug("Worker thread event loop already closed")
        if self.worker_executor:
            self.worker_executor.shutdown(wait=True)
            self.worker_executor = None
            self.worker_loop = None
        with self._futures_lock:
            self._active_futures.clear()
        # After the loop is gone no token can admit or release, so any count
        # left is a phantom that would throttle the next start().
        self.gate_waiters.reset()
        with self._in_flight_lock:
            self._in_flight_message_ids.clear()
            self._in_flight_record_ids.clear()

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
            return

        self.logger.info(
            "Waiting for %d active tasks to complete (timeout: %ss total)",
            len(futures_to_wait),
            messaging_env.shutdown_task_timeout,
        )

        done, not_done = futures_wait(futures_to_wait, timeout=messaging_env.shutdown_task_timeout)

        for future in done:
            try:
                future.result()
            except Exception as e:
                self.logger.warning("Task errored during shutdown: %s", e)

        for future in not_done:
            self.logger.warning("Task timed out during shutdown")
            future.cancel()

    def _get_active_task_count(self) -> int:
        with self._futures_lock:
            return len(self._active_futures)

    def _get_gate_waiter_count(self, tier: ParseTier | None = None) -> int:
        return concurrency.get_gate_waiter_count(self, tier)

    def _dispatch_budget(self) -> concurrency.DispatchBudget:
        """Room to spawn tasks this turn. Per tier only with a scheduler:
        the broker-order path cannot pass over a record to reach another
        tier's, so a tier ceiling there would just stall the stream head."""
        return concurrency.dispatch_budget(self, tiered=self._scheduler is not None)

    def _dispatch_tier_name(self, message: StreamMessage) -> str:
        return concurrency.dispatch_tier(self, message).value

    def dispatch_stats(self) -> dict[str, Any]:
        """Dispatch admission state for the health endpoint."""
        return self._dispatch_budget().as_dict()

    def _slot_pool_of(self, key: FairnessKey) -> str:
        return concurrency.slot_pool_of_key(
            self, key, tier_level=self.fair_scheduler_config.tier_level
        )

    def _slot_share_plan(
        self, can_dispatch: "Callable[[Any], bool] | None" = None
    ) -> SlotSharePlan | None:
        """Each waiting connector's cap on records in flight, for one
        dispatch decision. None when fair scheduling is off.

        Only connectors whose next record could be dispatched now count as
        waiting (``can_dispatch`` is the dispatch phase's own check). One
        whose head is waiting out a retry backoff cannot take a slot, so it
        must not shrink anyone else's share."""
        scheduler = self._scheduler
        slot_share = self._slot_share
        if scheduler is None or slot_share is None:
            return None
        return slot_share.plan(
            scheduler.backlogged_keys(can_dispatch),
            self._slot_pool_of,
            lambda pool: concurrency.slot_pool_limit(self, pool),
        )

    def _is_in_flight(self, message_id: str, stream_name: str | None = None) -> bool:
        """Whether this entry is being processed. Without a stream, whether
        an entry with this id is, on any stream."""
        with self._in_flight_lock:
            if stream_name is not None:
                return (stream_name, message_id) in self._in_flight_message_ids
            return any(mid == message_id for _s, mid in self._in_flight_message_ids)

    def _mark_in_flight(self, message_id: str, stream_name: str | None = None) -> None:
        with self._in_flight_lock:
            self._in_flight_message_ids.add((stream_name or "", message_id))

    def _unmark_in_flight(self, message_id: str, stream_name: str | None = None) -> None:
        with self._in_flight_lock:
            self._in_flight_message_ids.discard((stream_name or "", message_id))

    def _in_flight_by_stream(self) -> dict[str, list[str]]:
        """In-flight entry ids per stream (entries with no known stream left out)."""
        with self._in_flight_lock:
            by_stream: dict[str, list[str]] = {}
            for stream, message_id in self._in_flight_message_ids:
                if stream:
                    by_stream.setdefault(stream, []).append(message_id)
            return by_stream

    def _in_flight_count_by_stream(self) -> dict[str, int]:
        return {stream: len(ids) for stream, ids in self._in_flight_by_stream().items()}

    def _claim_record(self, record_id: str) -> bool:
        """Take this process's claim on a record, or report it already taken.

        The distributed ``record:`` lease is the cross-replica guard, but it is
        only taken when a concurrency manager is configured. Without one there
        was nothing keyed by record at all in this consumer -- entries are
        tracked by stream id -- so two deliveries carrying the same record (the
        original, and one the stranded sweep re-published) could run at the same
        time and race each other's status writes. The Kafka consumer already
        keeps the equivalent set; this is its counterpart.
        """
        with self._in_flight_lock:
            if record_id in self._in_flight_record_ids:
                return False
            self._in_flight_record_ids.add(record_id)
        return True

    def _release_record(self, record_id: str) -> None:
        with self._in_flight_lock:
            self._in_flight_record_ids.discard(record_id)

    def _is_record_in_flight(self, record_id: str) -> bool:
        with self._in_flight_lock:
            return record_id in self._in_flight_record_ids

    @staticmethod
    def _pending_detail_id(detail: dict) -> str | None:
        raw_id = detail.get("message_id", detail.get(b"message_id"))
        if raw_id is None:
            return None
        return raw_id.decode() if isinstance(raw_id, bytes) else str(raw_id)

    async def _cleanup_empty_consumers(
        self,
        topic: str | None = None,
        *,
        include_current: bool = False,
    ) -> None:
        if self.redis is None:
            return

        topics = [topic] if topic is not None else self.config.topics
        for stream_name in topics:
            try:
                consumers = await self.redis.xinfo_consumers(  # type: ignore
                    stream_name,
                    self.config.group_id,
                )
                for consumer in consumers:
                    raw_name = consumer.get("name", consumer.get(b"name"))
                    name = (
                        raw_name.decode()
                        if isinstance(raw_name, bytes)
                        else str(raw_name)
                    )
                    if name == self.consumer_name and not include_current:
                        continue
                    pending = int(
                        consumer.get("pending", consumer.get(b"pending", 0))
                    )
                    idle_ms = int(
                        consumer.get("idle", consumer.get(b"idle", 0))
                    )
                    if pending != 0:
                        continue
                    if (
                        name != self.consumer_name
                        and idle_ms < self.config.claim_min_idle_ms
                    ):
                        continue
                    await self.redis.xgroup_delconsumer(  # type: ignore
                        stream_name,
                        self.config.group_id,
                        name,
                    )
            except Exception as exc:
                self.logger.debug(
                    "Could not clean empty Redis Stream consumers for %s: %s",
                    stream_name,
                    exc,
                )

    async def _should_dead_letter(
        self,
        topic: str,
        message_id: str,
        stable_message_id: str | None = None,
        parsed_message: StreamMessage | None = None,
    ) -> bool:
        """Check if message should be dead-lettered based on failure retry count.

        With a retry tracker, only its failure count decides here. Redis's own
        times_delivered goes up on every claim (each restart claims every
        entry the dead consumer held, buffered or not; so does a claim of an
        entry left waiting for its record's lock), so as a dead-letter rule it
        dropped healthy work. The poison loop it guarded against (#2992), a
        record that kills the process before any failure is counted, is now
        bounded by the tracker's unfinished attempts, counted in
        ``_process_message_wrapper``. Without a tracker, times_delivered is
        still the backstop.

        ``stable_message_id`` is the ``_retry_tracking_id`` a re-queued entry
        carries in its payload — RetryManager state is keyed by that stable
        id everywhere else (see ``_process_message_wrapper``), not by the
        current Redis message_id, which changes on every re-queue. Falls back
        to ``message_id`` when no stable id is available (first delivery).
        """
        max_attempts = messaging_env.max_delivery_attempts
        delivery_backstop = messaging_env.redis_max_deliveries
        tracking_id = stable_message_id or message_id

        if self.retry_manager is not None:
            try:
                failure_count = await self._get_retry_count(tracking_id)
            except Exception as e:
                self.logger.error("Error checking app-tracked retry count: %s", e)
                return False
            if failure_count >= max_attempts:
                return await self._abandon_or_leave_pending(
                    message_id,
                    tracking_id,
                    parsed_message,
                    reason=(
                        f"exhausted {failure_count} of {max_attempts} "
                        "allowed failures"
                    ),
                    attempts=failure_count,
                    ack=lambda: self.redis.xack(  # type: ignore[union-attr]
                        topic, self.config.group_id, message_id
                    ),
                )
            return False

        try:
            details = await self.redis.xpending_range(  # type: ignore
                topic,
                self.config.group_id,
                min=message_id,
                max=message_id,
                count=1,
            )
            times_delivered = int(details[0].get("times_delivered", 0)) if details else 0
        except Exception as e:
            self.logger.error("Error checking delivery count: %s", e)
            return False

        if times_delivered >= delivery_backstop:
            record_id = (
                concurrency.work_key(parsed_message.payload, "") if parsed_message else None
            )
            # A sibling delivery of the same record is still running in this
            # process. times_delivered here is often an own-PEL re-read, not
            # a crash loop; abandoning would ACK the entry and mark FAILED
            # under the live handler.
            if record_id and self._is_record_in_flight(str(record_id)):
                return False
            return await self._abandon_or_leave_pending(
                message_id,
                tracking_id,
                parsed_message,
                reason=(
                    f"delivered {times_delivered} times "
                    f"(backstop {delivery_backstop}); likely crashing "
                    "the consumer before the failure counter is written"
                ),
                attempts=times_delivered,
                ack=lambda: self.redis.xack(  # type: ignore[union-attr]
                    topic, self.config.group_id, message_id
                ),
            )

        return False

    async def _abandon_or_leave_pending(
        self,
        message_id: str,
        tracking_id: str,
        parsed_message: StreamMessage | None,
        *,
        reason: str,
        attempts: int,
        ack: Callable[[], Awaitable[Any]],
    ) -> bool:
        """Abandon the message; if the ack itself fails, leave it in the PEL.

        Always ``True``: the decision to abandon has been made, and the sink
        has been told. The failure mode this closes is a Redis error inside
        the ack turning into "not dead-lettered" -- the message was then
        dispatched as healthy work straight after the sink had marked its
        record abandoned. Left un-acked instead, the next drain finds it,
        re-evaluates it and acks it once Redis answers again.
        """
        try:
            await self._abandon_message(
                message_id, tracking_id, parsed_message, reason=reason, attempts=attempts, ack=ack,
            )
        except Exception as e:
            self.logger.error(
                "Dead-lettering %s could not be completed (%s); leaving it pending for the next drain",
                message_id, e,
            )
        return True

    async def _abandon_message(
        self,
        message_id: str,
        tracking_id: str,
        parsed_message: StreamMessage | None,
        *,
        reason: str,
        attempts: int,
        ack: Callable[[], Awaitable[Any]],
    ) -> None:
        """Give up on a message: tell the sink, then acknowledge it.

        The notification comes first and on every path. An XACK is final — the
        entry leaves the PEL and nothing redelivers it — so a record whose
        message is dropped without this is left on whatever status it happened
        to hold, which no recovery sweep revisits, and the log names only a
        stream id that no longer resolves to anything.

        ``ack`` is supplied by the caller because acknowledging is loop-bound:
        the drain loop owns ``self.redis`` directly, while the processing
        wrapper runs on the worker loop and has to bridge back (see
        ``_ack_message``).
        """
        await notify_abandoned(
            self.disposition_sink,
            self.logger,
            parsed_message,
            reason=reason,
            attempts=attempts,
        )
        self.logger.warning(
            "Dead-lettered %s (%s, tracking id %s): %s",
            message_id,
            describe_message(parsed_message),
            tracking_id,
            reason,
        )
        await ack()
        await self._clear_retry_tracking(tracking_id)

    async def _wait_out_backpressure(self) -> None:
        """Block while any downstream service has an active 429+Retry-After
        pause signalled, so this consumer stops pulling new work a saturated
        parser/embedder/Docling instance would just reject again.

        Polls in small increments and re-checks ``self.running`` between
        them, mirroring ``_delay_if_retry_not_ready``, so shutdown interrupts
        the wait instead of blocking on it.
        """
        if self.backpressure_coordinator is None:
            return
        while self.running and self.backpressure_coordinator.is_paused():
            if not self._downstream_backpressure_active:
                self._downstream_backpressure_active = True
                self.logger.warning(
                    "Downstream backpressure from %s: pausing new stream "
                    "reads for %.1fs",
                    ", ".join(sorted(self.backpressure_coordinator.paused_services)),
                    self.backpressure_coordinator.pause_remaining(),
                )
            # Buffered entries stay un-ACKed through a pause of any length;
            # keep their ownership confirmed, or the sweep would read a long
            # pause as a failed refresh and release them.
            await self.__refresh_held_ownership()
            remaining = self.backpressure_coordinator.pause_remaining()
            await asyncio.sleep(min(_DELAY_POLL_INTERVAL_SECONDS, remaining) if remaining > 0 else _DELAY_POLL_INTERVAL_SECONDS)
        if self._downstream_backpressure_active:
            self._downstream_backpressure_active = False
            self.logger.info("Downstream backpressure cleared; resuming stream reads")

    async def __recover_entry(
        self, topic: str, message_id: str, fields: dict[str, str], *, owned: bool
    ) -> bool:
        """Take responsibility for one pending entry recovery found.

        ``owned`` entries were found in this consumer's own pending list by
        read-only XPENDING + XRANGE, so their idle timer kept climbing and a
        peer's XAUTOCLAIM could take one before it is dispatched: ownership
        is reasserted first with ``XCLAIM JUSTID``, which resets idle time
        without counting a delivery (same as ``__refresh_held_ownership``).
        Entries from XAUTOCLAIM are already freshly claimed.

        Returns whether the entry was handed on (buffered or dispatched).
        """
        prefix = "own " if owned else ""
        if self.__already_held(message_id, topic):
            return False
        try:
            parsed_message = await self._parse_message(message_id, fields, topic)
            stable_message_id = self._get_stable_message_id(message_id, parsed_message)
            if await self._should_dead_letter(
                topic, message_id, stable_message_id, parsed_message
            ):
                return False
            if owned:
                await self.__reclaim_for_dispatch(topic, message_id)
            self.logger.info(
                "Recovering %spending message: stream=%s, id=%s",
                prefix,
                topic,
                message_id,
            )
            # Acknowledges an unparseable envelope, as a fresh read does;
            # left pending it would sit at the head of this lane's recovery
            # for good, since reclaiming never counts a delivery.
            await self.__enqueue_parsed(topic, message_id, fields, parsed_message)
            if parsed_message is None:
                return True
            return self.__already_held(message_id, topic)
        except Exception as e:
            self.logger.error(
                "Error recovering %spending message %s: %s", prefix, message_id, e
            )
            return False

    async def __autoclaim(
        self, topic: str, start_id: str, count: int
    ) -> tuple[str, list[tuple[str, dict[str, str]]]]:
        """One XAUTOCLAIM page: entries idle past ``claim_min_idle_ms`` in any
        consumer's pending list -- a crashed replica's, or this one's own --
        become this consumer's. Returns the next cursor and what it claimed."""
        result = await self.redis.xautoclaim(  # type: ignore
            topic,
            self.config.group_id,
            self.consumer_name,
            min_idle_time=self.config.claim_min_idle_ms,
            start_id=start_id,
            count=count,
        )
        next_id, claimed, _deleted = result
        if claimed:
            self.logger.debug(
                "XAUTOCLAIM on %s took %d idle entry(ies)", topic, len(claimed)
            )
        if isinstance(next_id, bytes):
            next_id = next_id.decode()
        return str(next_id), list(claimed or [])

    async def _drain_pending(self) -> bool:
        """Re-process messages left in the Pending Entries List (PEL).

        Called at startup and whenever the streams go idle. Returns True if
        any pending messages were processed.

        Phase 1: XAUTOCLAIM to steal idle messages from other (crashed)
        consumers. Phase 2: recover entries already in THIS consumer's
        pending list that it is not holding.

        Both phases take the lanes in turn, a share of the budget each per
        round, instead of draining one lane until the buffer is full. A
        restart after a first sync can leave thousands of entries pending in
        one lane; taken lane by lane, the buffer filled with that lane alone
        and every other connector waited behind it.
        """
        processed_any = False
        topics = list(self.config.topics)

        # Phase 1: claim idle messages from other (possibly crashed) consumers.
        cursors: dict[str, str] = dict.fromkeys(topics, "0-0")
        active = list(topics)
        while active and self.running:
            # A drain can run long; keep held entries confirmed meanwhile.
            await self.__refresh_held_ownership()
            budget = self._dispatch_budget()
            if budget.blocked:
                await asyncio.sleep(0.5)
                continue
            claim_budget = self.__recovery_claim_budget(budget.remaining)
            if claim_budget <= 0:
                break
            per_lane = max(1, claim_budget // len(active))
            for topic in list(active):
                if not self.running:
                    return processed_any
                claim = min(
                    per_lane,
                    self.__recovery_claim_budget(self._dispatch_budget().remaining),
                )
                if claim <= 0:
                    break
                try:
                    next_id, claimed = await self.__autoclaim(topic, cursors[topic], claim)
                except Exception as e:
                    self.logger.error("Error during XAUTOCLAIM on %s: %s", topic, e)
                    active.remove(topic)
                    continue
                cursors[topic] = next_id
                if not claimed or next_id == "0-0":
                    active.remove(topic)
                for message_id, fields in claimed:
                    if not self.running:
                        return processed_any
                    if self._dispatch_budget().blocked:
                        break
                    if await self.__recover_entry(topic, message_id, fields, owned=False):
                        processed_any = True

        # Phase 2: recover messages already in THIS consumer's PEL.
        #
        # Skipped unless something in it is genuinely unaccounted for.
        # Must not use XREADGROUP id="0": Redis increments times_delivered
        # on every own-PEL re-read, and the dead-letter backstop treats
        # that counter as a crash loop. A healthy entry left untracked
        # (buffer full) or sitting behind a sibling delivery was being
        # failed after ~10 idle cycles (~1 min). XPENDING is read-only;
        # XRANGE fetches the payload without counting a delivery.
        lanes = [topic for topic in topics if await self.__has_unheld_pending(topic)]
        # Ids this pass has already tried, so an entry that is still pending
        # when the next round looks again is not taken twice.
        tried: set[tuple[str, str]] = set()
        while lanes and self.running:
            await self.__refresh_held_ownership()
            budget = self._dispatch_budget()
            if budget.blocked:
                break
            share = max(1, budget.remaining)
            room = self.__buffer_room()
            if room is not None:
                if room <= 0:
                    break
                share = min(share, room)
            per_lane = max(1, share // len(lanes))
            progressed = False
            for topic in list(lanes):
                if not self.running:
                    return processed_any
                try:
                    pending = await self.__own_unheld_pending_payloads(topic, limit=per_lane)
                except Exception as e:
                    self.logger.error(
                        "Error inspecting own PEL on %s: %s",
                        topic,
                        e,
                    )
                    pending = []
                pending = [(m, f) for m, f in pending if (topic, m) not in tried]
                if not pending:
                    lanes.remove(topic)
                    continue
                for message_id, fields in pending:
                    if not self.running:
                        return processed_any
                    if self._dispatch_budget().blocked:
                        break
                    tried.add((topic, message_id))
                    if await self.__recover_entry(topic, message_id, fields, owned=True):
                        processed_any = True
                        progressed = True
            if not progressed:
                break

        for topic in topics:
            await self._cleanup_empty_consumers(topic)

        if processed_any:
            self.logger.info("Processed pending messages from PEL")
        return processed_any

    def __buffer_room(self) -> int | None:
        """Free places in the buffer, or None when there is no scheduler."""
        scheduler = self._scheduler
        if scheduler is None:
            return None
        return (
            self.fair_scheduler_config.max_buffered_messages
            - scheduler.pending_count
            - len(self._deferred_entries)
        )

    def __held_count_by_stream(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for stream, _message_id in self._held_entries:
            counts[stream] = counts.get(stream, 0) + 1
        return counts

    def __pending_by_owner(self, summary: object) -> tuple[int, int]:
        """(this consumer's, everyone else's) pending count from an XPENDING
        summary reply."""
        if not isinstance(summary, dict):
            return 0, 0
        own = 0
        others = 0
        for consumer in summary.get("consumers") or []:
            if isinstance(consumer, dict):
                name = consumer.get("name", consumer.get(b"name"))
                count = consumer.get("pending", consumer.get(b"pending", 0))
            else:
                name, count = consumer[0], consumer[1]
            if isinstance(name, bytes):
                name = name.decode()
            if str(name) == self.consumer_name:
                own += int(count)
            else:
                others += int(count)
        return own, others

    def __release_for_room(self, protect: str) -> Callable[[], None] | None:
        """Give up one held entry so a lane holding nothing can be served.

        Taken from the lane this consumer holds most entries from: its
        newest parked entry, else its newest buffered one. The entry stays in
        the pending list, unheld, and comes back through recovery once there
        is room. Returns a callable that takes it back, for when the entry
        it made room for did not need the room after all, or None when there
        was nothing to give up.
        """
        scheduler = self._scheduler
        if scheduler is None:
            return None
        counts = self.__held_count_by_stream()
        counts.pop(protect, None)
        if not counts:
            return None
        donor = max(counts, key=lambda stream: counts[stream])

        for index in range(len(self._deferred_entries) - 1, -1, -1):
            entry = self._deferred_entries[index]
            if entry[0] == donor:
                del self._deferred_entries[index]
                self._held_entries.discard((entry[0], entry[1]))

                def restore_parked(
                    entry: tuple[str, str, dict[str, str], StreamMessage, FairnessKey, float] = entry,
                    index: int = index,
                ) -> None:
                    self._deferred_entries.insert(
                        min(index, len(self._deferred_entries)), entry
                    )
                    self._held_entries.add((entry[0], entry[1]))

                return restore_parked

        def stream_order(message_id: str) -> tuple[int, ...]:
            try:
                return tuple(int(part) for part in message_id.split("-"))
            except ValueError:
                return (0,)

        candidates = [
            message_id for stream, message_id in self._held_entries if stream == donor
        ]
        if not candidates:
            return None
        victim = max(candidates, key=stream_order)
        removed = scheduler.purge(lambda item: item[0] == donor and item[1] == victim)
        if not removed:
            return None
        self._held_entries.discard((donor, victim))
        stream_name, message_id, fields, parsed, _buffered_at = removed[0]

        def restore_buffered() -> None:
            self.__try_enqueue(stream_name, message_id, fields, parsed)

        return restore_buffered

    async def __only_consumer(self, topics: list[str]) -> bool:
        """Whether no other consumer is listed in this consumer's group on
        any of its lanes (``XINFO CONSUMERS``).

        Read-phase recovery runs only then. With other consumers -- live
        replicas, or a crashed or replaced process still listed with pending
        entries -- the group behaves as before this existed: taking entries
        back and making room safely next to another consumer needs an atomic
        ownership check, which is a separate design. A replaced process's
        entries come back as they always have, through the startup and idle
        drains, after which it is removed from the group.
        """
        for topic in topics:
            try:
                consumers = await self.redis.xinfo_consumers(  # type: ignore
                    topic, self.config.group_id
                )
            except Exception as e:
                self.logger.debug("Could not list consumers of %s: %s", topic, e)
                return False
            for consumer in consumers or []:
                raw_name = consumer.get("name", consumer.get(b"name"))
                name = raw_name.decode() if isinstance(raw_name, bytes) else str(raw_name)
                if name != self.consumer_name:
                    return False
        return True

    async def __recover_while_reading(self) -> None:
        """Re-offer entries pending under this consumer's own name that it
        is not holding, a few at a time, on every read pass.

        They end up there after an at-risk release, or when a read found no
        room. ``_drain_pending`` only runs once the streams go idle, and
        during a long first sync they never do -- and while the buffer is
        full the read pass does not even poll. So this runs before the
        full-buffer check, at most once per ``_RECOVERY_INTERVAL_SECONDS``
        and ``_RECOVERY_BATCH`` entries:

        - With room in the buffer, lanes that have unheld entries are served
          in turn, a share each.
        - With the buffer full, only lanes this consumer holds nothing from
          are served, and each entry taken back first gives up one held
          entry from the lane it holds most from (``__release_for_room``).
          A full buffer of one connector can no longer lock the others out.

        It never claims another consumer's entries, and it runs only while
        this consumer is the only one in its group (``__only_consumer``).
        An XPENDING summary per lane decides where to look; it is cheap and
        changes nothing.
        """
        scheduler = self._scheduler
        if scheduler is None or self.redis is None:
            return
        now = time.monotonic()
        if now - self._last_recovery < _RECOVERY_INTERVAL_SECONDS:
            return
        self._last_recovery = now

        topics = list(self.config.topics)
        if not topics:
            return
        if not await self.__only_consumer(topics):
            return
        start = self._recovery_lane_offset % len(topics)
        self._recovery_lane_offset += 1
        topics = topics[start:] + topics[:start]

        held_by_stream = self.__held_count_by_stream()
        in_flight = self._in_flight_count_by_stream()
        unheld: set[str] = set()
        for topic in topics:
            try:
                summary = await self.redis.xpending(topic, self.config.group_id)  # type: ignore
            except Exception as e:
                self.logger.debug("Could not summarise pending list of %s: %s", topic, e)
                continue
            own, _others = self.__pending_by_owner(summary)
            if own - held_by_stream.get(topic, 0) - in_flight.get(topic, 0) > 0:
                unheld.add(topic)
        if not unheld:
            return

        room = self.__buffer_room() or 0
        if room > 0:
            lanes = [t for t in topics if t in unheld]
            limit = min(_RECOVERY_BATCH, room)
        else:
            lanes = [
                t
                for t in topics
                if t in unheld and held_by_stream.get(t, 0) + in_flight.get(t, 0) == 0
            ]
            limit = _RECOVERY_BATCH
        if not lanes:
            return

        per_lane = max(1, limit // len(lanes))
        taken = 0
        for topic in lanes:
            if taken >= limit or not self.running:
                break
            want = min(per_lane, limit - taken)
            try:
                found = await self.__own_unheld_pending_payloads(
                    topic, limit=want, ignore_budget=True
                )
            except Exception as e:
                self.logger.debug("Could not recover pending entries on %s: %s", topic, e)
                continue
            for message_id, fields in found:
                undo = None
                if not self.__buffer_has_room():
                    undo = self.__release_for_room(topic)
                    if undo is None:
                        break
                if await self.__recover_entry(topic, message_id, fields, owned=True):
                    taken += 1
                if undo is not None and not self.__already_held(message_id, topic):
                    # The room went unused (the entry was acknowledged, or
                    # could not be taken): give the donor its place back.
                    undo()
        if taken:
            self.logger.info(
                "Recovered %d of this consumer's own pending entry(ies) while reading",
                taken,
            )

    def __retry_not_before(self, parsed: StreamMessage) -> float | None:
        not_before = parsed.payload.get("_retry_not_before")
        if not not_before:
            return None
        try:
            return float(not_before)
        except (TypeError, ValueError):
            return None

    def __pending_scan_pages(self, topic: str) -> int:
        """Pages an own-pending-list scan may read on ``topic``.

        The entries this consumer holds and works on sit in that list too,
        so the scan has to be able to page past all of them: one full buffer
        plus the stream's in-flight entries, and a page to spare. Never
        fewer than ``_PENDING_SCAN_MAX_PAGES``.
        """
        page_size = max(self.config.batch_size, _PENDING_SCAN_PAGE)
        tracked = (
            self.fair_scheduler_config.max_buffered_messages
            + self._in_flight_count_by_stream().get(topic, 0)
        )
        return max(_PENDING_SCAN_MAX_PAGES, math.ceil(tracked / page_size) + 1)

    async def __has_unheld_pending(self, topic: str) -> bool:
        """Whether this consumer's pending list holds anything it is not
        already tracking.

        Inspected with XPENDING, which is read-only and does not touch
        delivery counts, unlike the XREADGROUP recovery read it guards.
        """
        if self.redis is None:
            return False

        # Paged rather than a single window: held entries stay in the pending
        # list by design and have the lowest ids, so they occupy the head of
        # it. A one-shot scan the size of a read batch would see nothing but
        # held entries as soon as there are that many, and conclude there is
        # nothing to recover -- switching Phase 2 off for as long as the
        # buffer stays populated.
        page_size = max(self.config.batch_size, _PENDING_SCAN_PAGE)
        cursor = "-"
        for _ in range(self.__pending_scan_pages(topic)):
            try:
                details = await self.redis.xpending_range(  # type: ignore
                    topic,
                    self.config.group_id,
                    min=cursor,
                    max="+",
                    count=page_size,
                    consumername=self.consumer_name,
                )
            except Exception as e:
                # Fail open: a failed inspection must not stop recovery.
                self.logger.debug(
                    "Could not inspect pending list on %s: %s", topic, e
                )
                return True
            if not isinstance(details, list) or not details:
                return False
            last_id = cursor
            for detail in details:
                message_id = self._pending_detail_id(detail) if isinstance(detail, dict) else None
                if not message_id:
                    continue
                last_id = message_id
                if not self.__already_held(message_id, topic):
                    return True
            if len(details) < page_size:
                return False
            cursor = f"({last_id}"
        # Out of pages while they were still full: something may lie beyond.
        return True

    async def __own_unheld_pending_payloads(
        self, topic: str, limit: int | None = None, *, ignore_budget: bool = False
    ) -> list[tuple[str, dict[str, str]]]:
        """Payloads for this consumer's pending entries that are not already
        held or in flight.

        XPENDING lists them without incrementing ``times_delivered``;
        XRANGE loads the envelope the same way. XREADGROUP id=0 is what
        used to live here and is what burned the delivery backstop.
        """
        if self.redis is None:
            return []

        if ignore_budget:
            # Buffering, not dispatching: the dispatch budget does not apply.
            remaining = max(1, limit or 1)
        else:
            budget = self._dispatch_budget()
            if budget.blocked:
                return []
            remaining = max(1, budget.remaining)
            if limit is not None:
                remaining = min(remaining, max(1, limit))
        page_size = max(self.config.batch_size, _PENDING_SCAN_PAGE)
        found: list[tuple[str, dict[str, str]]] = []
        cursor = "-"
        for _ in range(self.__pending_scan_pages(topic)):
            details = await self.redis.xpending_range(  # type: ignore
                topic,
                self.config.group_id,
                min=cursor,
                max="+",
                count=page_size,
                consumername=self.consumer_name,
            )
            if not isinstance(details, list) or not details:
                break
            last_id = cursor
            for detail in details:
                message_id = self._pending_detail_id(detail) if isinstance(detail, dict) else None
                if not message_id:
                    continue
                last_id = message_id
                if self.__already_held(message_id, topic):
                    continue
                entries = await self.redis.xrange(  # type: ignore
                    topic, min=message_id, max=message_id, count=1
                )
                if not entries:
                    continue
                _entry_id, fields = entries[0]
                found.append((message_id, fields))
                if len(found) >= remaining:
                    return found
            if len(details) < page_size:
                break
            cursor = f"({last_id}"
        return found

    async def __reclaim_for_dispatch(self, topic: str, message_id: str) -> None:
        """Reassert ownership of a PEL entry before dispatching it.

        Phase 2 discovers entries with read-only XPENDING + XRANGE, so the
        entry's idle timer is never reset by the scan itself. A peer's
        XAUTOCLAIM (min_idle_time = ``claim_min_idle_ms``) can therefore steal
        the entry between the scan and the dispatch. ``XCLAIM JUSTID`` with
        ``min_idle_time=0`` resets the idle timer without inflating
        ``times_delivered``, closing the window for the next
        ``claim_min_idle_ms`` milliseconds.
        """
        if self.redis is None:
            return
        try:
            await self.redis.xclaim(  # type: ignore
                topic,
                self.config.group_id,
                self.consumer_name,
                min_idle_time=0,
                message_ids=[message_id],
                justid=True,
            )
        except Exception as e:
            self.logger.debug(
                "Could not reclaim %s on %s before dispatch: %s",
                message_id, topic, e,
            )

    async def __refresh_held_ownership(self) -> None:
        """Reset the idle timer on entries this consumer is holding.

        A buffered or parked entry is un-ACKed, so Redis counts it as idle
        and it becomes claimable once it passes ``claim_min_idle_ms`` --
        by a peer replica, or by this consumer's own recovery pass. Either
        way the claim itself increments ``times_delivered``, which the
        dead-letter backstop reads as a failed attempt, so entries that are
        merely waiting their turn get dead-lettered after a few passes.

        ``XCLAIM ... JUSTID`` is the documented way out: it resets idle time
        without incrementing the delivery counter, and re-claiming an entry
        we already own is a no-op otherwise. A stream whose refresh succeeds
        is recorded as confirmed, which is what ``__sweep_stale_buffered``
        judges risk by.
        """
        if self.redis is None or not self._held_entries:
            return
        interval = self.__ownership_refresh_interval()
        now = time.monotonic()
        if now - self._last_ownership_refresh < interval:
            return
        self._last_ownership_refresh = now

        by_stream: dict[str, list[str]] = {}
        for stream, message_id in self._held_entries:
            by_stream.setdefault(stream, []).append(message_id)
        for stream, message_ids in by_stream.items():
            try:
                await self.redis.xclaim(  # type: ignore
                    stream,
                    self.config.group_id,
                    self.consumer_name,
                    min_idle_time=0,
                    message_ids=message_ids,
                    justid=True,
                )
                self._ownership_confirmed_at[stream] = now
            except Exception as e:
                self.logger.warning(
                    "Could not refresh ownership of %d held entry(ies) on %s: %s",
                    len(message_ids),
                    stream,
                    e,
                )

    def __buffer_has_room(self) -> bool:
        """Whether anything more can be held in memory.

        Buffered and parked entries share one budget: it is what bounds this
        consumer's memory, so it cannot be an allowance each of them gets.
        """
        scheduler = self._scheduler
        if scheduler is None:
            return False
        held = scheduler.pending_count + len(self._deferred_entries)
        return held < self.fair_scheduler_config.max_buffered_messages

    def __recovery_claim_budget(self, pipeline_capacity: int) -> int:
        """How many pending entries recovery may claim right now.

        XAUTOCLAIM increments ``times_delivered`` for **every** entry it
        returns, and the dead-letter backstop reads that count as failed
        attempts. Claiming a batch and then stopping part-way through it --
        because capacity ran out -- therefore burns delivery attempts on
        entries nothing ever tried to process, and dead-letters healthy
        records after a couple of restarts. Claim only what there is room to
        take responsibility for.
        """
        budget = max(0, pipeline_capacity)
        scheduler = self._scheduler
        if scheduler is not None:
            buffer_room = (
                self.fair_scheduler_config.max_buffered_messages
                - scheduler.pending_count
                - len(self._deferred_entries)
            )
            budget = min(budget, max(0, buffer_room))
        return min(10, budget)

    def __already_held(self, message_id: str, stream_name: str | None = None) -> bool:
        """Whether this consumer is already responsible for an entry.

        Buffered and parked entries stay un-ACKed in the pending list on
        purpose, so the recovery path sees them and would claim them again.
        Every claim increments Redis's ``times_delivered``, which the
        dead-letter backstop reads as a failed attempt -- so re-claiming
        entries that are merely waiting their turn dead-letters healthy
        records, and duplicates the work besides.
        """
        if stream_name is None:
            return any(
                held_id == message_id for _stream, held_id in self._held_entries
            ) or self._is_in_flight(message_id)
        return (
            (stream_name, message_id) in self._held_entries
            or self._is_in_flight(message_id, stream_name)
        )

    def __publish_scheduler_metrics(self) -> None:
        """Gauges, refreshed once per consume iteration. Never allowed to
        take the consume loop down."""
        scheduler = self._scheduler
        if scheduler is None:
            return
        try:
            metrics.record_scheduler_depth(
                "redis",
                scheduler.pending_count,
                {
                    "org": scheduler.active_count_at(0),
                    "connector": scheduler.active_entity_count,
                },
            )
            metrics.record_lanes_paused(
                "redis", len({stream for stream, *_r in self._deferred_entries})
            )
            for tier, count in self.gate_waiters.snapshot().items():
                metrics.record_gate_waiters("redis", tier.value, count)
        except Exception as e:
            self.logger.debug("Failed to publish scheduler metrics: %s", e)

    async def __drain_deferred(self) -> None:
        """Re-offer entries parked for want of buffer room, oldest first.

        One key still being full must not hold back another key's parked
        entries -- that would re-create, inside this consumer, exactly the
        head-of-line blocking lanes exist to remove. So the pass walks every
        parked entry, and only skips further entries belonging to a key that
        has already failed this round, which is what keeps each key's own
        entries in arrival order.
        """
        parked = self._deferred_entries
        scheduler = self._scheduler
        if not parked or scheduler is None:
            return
        # Keyed by entity, not by leaf: the per-entity cap is what refused
        # these, and it spans every tier leaf of one connector.
        still_full: set[FairnessKey] = set()
        kept: deque[
            tuple[str, str, dict[str, str], StreamMessage, FairnessKey, float]
        ] = deque()
        for entry in parked:
            stream_name, message_id, fields, parsed, key, _parked_at = entry
            entity = scheduler.entity_key(key)
            if entity in still_full:
                kept.append(entry)
                continue
            if self.__try_enqueue(stream_name, message_id, fields, parsed):
                continue
            still_full.add(entity)
            kept.append(entry)
        self._deferred_entries = kept

    def __ownership_refresh_interval(self) -> float:
        return max(1.0, (self.config.claim_min_idle_ms / 1000.0) / 3.0)

    def __ownership_risk_after(self) -> float:
        """Seconds without a confirmed ownership refresh after which a held
        entry is at risk: two refreshes missed, and at least
        ``_AT_RISK_FRACTION`` of the idle-claim window gone."""
        return max(
            (self.config.claim_min_idle_ms / 1000.0) * _AT_RISK_FRACTION,
            2 * self.__ownership_refresh_interval(),
        )

    def __sweep_stale_buffered(self) -> None:
        """Release held entries whose ownership is at risk, and only those.

        A buffered or parked entry is un-ACKed, so Redis counts it as idle,
        and a peer replica's XAUTOCLAIM may take it once it passes
        ``claim_min_idle_ms``. ``__refresh_held_ownership`` resets that idle
        time every third of the window, so while the refresh works no peer
        can take an entry, however long it waits for its turn; age alone is
        no reason to let it go. Releasing an entry used to happen at a fixed
        dwell budget, and a released entry only came back when the stream
        went idle -- which, during a long sync, it never does -- so whole
        connectors stopped indexing.

        An entry is at risk when its stream's ownership has not been
        confirmed for ``__ownership_risk_after`` seconds: refreshes have
        failed, and a peer may be about to claim it. It is released (the
        pending entry is left as it is), so this consumer does not dispatch
        work another replica now owns; read-phase recovery takes it back if
        no one else does. A single consumer whose refresh keeps working
        never releases anything.
        """
        scheduler = self._scheduler
        if scheduler is None:
            return
        risk_after = self.__ownership_risk_after()
        now = time.monotonic()

        def at_risk(stream: str, held_since: float) -> bool:
            confirmed = max(held_since, self._ownership_confirmed_at.get(stream, 0.0))
            return now - confirmed >= risk_after

        dropped = scheduler.purge(lambda item: at_risk(item[0], item[4]))
        for stream, message_id, *_rest in dropped:
            self._held_entries.discard((stream, message_id))

        stale_parked = [e for e in self._deferred_entries if at_risk(e[0], e[5])]
        if stale_parked:
            self._deferred_entries = deque(
                e for e in self._deferred_entries if not at_risk(e[0], e[5])
            )
            for stream, message_id, *_rest in stale_parked:
                self._held_entries.discard((stream, message_id))

        released = len(dropped) + len(stale_parked)
        if released:
            metrics.record_dwell_exceeded("redis", released)
            self.logger.warning(
                "Released %d held entry(ies) whose ownership could not be "
                "confirmed for %.0fs, before another replica's idle claim "
                "(after %.0fs) can take them; recovery picks them up again "
                "if no one else does",
                released,
                risk_after,
                self.config.claim_min_idle_ms / 1000.0,
            )

    async def __enqueue_message(
        self, stream_name: str, message_id: str, fields: dict[str, str]
    ) -> None:
        """Parse a freshly read entry and hand it to the scheduler."""
        parsed = await self._parse_message(message_id, fields, stream_name)
        await self.__enqueue_parsed(stream_name, message_id, fields, parsed)

    async def __enqueue_parsed(
        self,
        stream_name: str,
        message_id: str,
        fields: dict[str, str],
        parsed: StreamMessage | None,
    ) -> None:
        """Hand a freshly read, already parsed entry to the scheduler."""
        if parsed is None:
            # No recordId can be recovered from an envelope that would not
            # parse. The entry itself is not logged: it carries the whole
            # record payload, and XACK leaves it in the stream, so XRANGE on
            # the id below retrieves it without putting customer data in the
            # logs. The size is here because a truncated write is the usual
            # cause.
            self.logger.warning(
                "Unparseable message %s from stream %s (%d byte payload); "
                "acknowledging without retry",
                message_id,
                stream_name,
                len(fields.get(_MESSAGE_VALUE_FIELD, "") or ""),
            )
            await notify_abandoned(
                self.disposition_sink,
                self.logger,
                None,
                reason=f"unparseable envelope on {stream_name}",
                attempts=1,
            )
            try:
                await self._ack_message(stream_name, message_id)
            except RedisAcknowledgementError as exc:
                self.logger.warning("%s", exc)
                return
            await self._clear_retry_tracking(message_id)
            return

        await self.__dispatch_or_enqueue(stream_name, message_id, fields, parsed)

    async def __dispatch_or_enqueue(
        self,
        stream_name: str,
        message_id: str,
        fields: dict[str, str],
        parsed_message: "StreamMessage | None" = None,
    ) -> None:
        """Route an already-parsed, already-delivered entry to the scheduler
        (fair scheduling enabled) or straight to processing (disabled).

        Shared by the read phase and ``_drain_pending``'s two recovery
        phases, so PEL-recovered entries are just as subject to fair
        ordering as freshly read ones.
        """
        scheduler = self._scheduler
        if scheduler is None:
            # Exact pre-existing call signature/behavior when disabled.
            await self._start_processing_task(stream_name, message_id, fields)
            return

        parsed = parsed_message or await self._parse_message(message_id, fields, stream_name)
        if parsed is None:
            return

        # Metered here, on the first offer only: __try_enqueue is re-run for
        # every parked entry on every read iteration, so counting there would
        # inflate both of these without bound while an entry waits.
        for field, level in zip(
            self.fair_scheduler_config.key_fields,
            self.key_extractor.extract(parsed),
            strict=False,
        ):
            if level == _DEFAULT_KEY_LEVEL:
                metrics.record_missing_key("redis", field)

        if not self.__try_enqueue(stream_name, message_id, fields, parsed):
            metrics.record_deferred("redis", "no_buffer_room")
            if not self.__buffer_has_room():
                # Nothing left to hold it with. Left un-ACKed and untracked so
                # the pending list keeps it and recovery picks it up later --
                # parking it anyway would put this consumer's memory use above
                # the budget that bounds it.
                return
            # No room for this key (or for anything). The entry stays
            # un-ACKed in this consumer's PEL and is re-offered at the top of
            # the next read iteration. It is never re-published to the tail
            # of the stream: XACK does not delete a stream entry, so bouncing
            # messages grows the stream toward its MAXLEN trim point, which
            # discards entries nobody has consumed.
            self._held_entries.add((stream_name, message_id))
            self._deferred_entries.append(
                (
                    stream_name,
                    message_id,
                    fields,
                    parsed,
                    self.key_extractor.extract(parsed),
                    time.monotonic(),
                )
            )

    def __try_enqueue(
        self,
        stream_name: str,
        message_id: str,
        fields: dict[str, str],
        parsed: StreamMessage,
    ) -> bool:
        """Offer one owned entry to the scheduler. False means no buffer room."""
        scheduler = self._scheduler
        if scheduler is None:
            return False
        not_before = self.__retry_not_before(parsed)
        key = self.key_extractor.extract(parsed)
        result = scheduler.enqueue(
            key,
            (stream_name, message_id, fields, parsed, time.monotonic()),
            not_before=not_before,
        )
        if result == EnqueueResult.ACCEPTED:
            self._held_entries.add((stream_name, message_id))
            return True
        return False

    async def _xreadgroup_grouped(
        self, streams: dict[str, str], count: int, block_ms: int
    ) -> list:
        """One ``XREADGROUP`` per hash-slot group of ``streams`` (R1).

        On standalone every stream is in one group, so this is exactly the
        single call this replaced, with the full ``block_ms`` budget and the
        per-stream ``count`` unaffected (COUNT applies per-stream, not per
        call). On Redis Cluster / MemoryDB, lane streams especially can span
        slots, so each group gets its own call with a fair share of the
        overall block budget -- an empty poll still costs at most
        ``block_ms`` total, not once per group.

        Each group's call is isolated with :func:`asyncio.wait_for` and its
        own try/except: while one slot's node is mid-failover/reconnect, a
        ``ClusterDownError`` (or a client that hangs retrying past its
        stated ``BLOCK`` budget) must not stop *other*, perfectly healthy
        groups from being polled -- seen live on a 3-master cluster where a
        restarted node's group blocked for tens of seconds while the other
        groups' streams sat fully caught-up and unread. A group that fails
        alongside others that succeed does not lose those successful reads:
        once ``XREADGROUP >`` claims a message it will never be handed out
        again by ``>``, only by an own-PEL read, so silently discarding a
        mixed batch here would strand the successful groups' messages until
        the next idle-triggered pending-drain pass (or forever, if new
        messages keep arriving often enough that the idle threshold is never
        reached). The error from the failing group is only re-raised -- so
        the caller's existing backoff kicks in -- when no group produced
        anything at all.
        """
        groups = self._planner.group(list(streams.keys()))
        if not groups:
            # Sleep, don't return straight into the caller's `continue`: with no
            # topics subscribed this path does no I/O at all, so returning
            # immediately spins the consume loop at 100% CPU. The inline
            # XREADGROUP this replaced blocked for `block_ms` in the same
            # state, and the Node consumer keeps an explicit idle sleep here.
            await asyncio.sleep(self.config.block_ms / 1000.0)
            return []

        per_group_block_ms = (
            block_ms if len(groups) == 1 else max(1, block_ms // len(groups))
        )
        per_group_timeout_seconds = (per_group_block_ms / 1000.0) + 5.0

        combined: list = []
        first_error: Optional[Exception] = None
        for group in groups:
            group_streams = {name: streams[name] for name in group}
            try:
                read = self.redis.xreadgroup(  # type: ignore
                    groupname=self.config.group_id,
                    consumername=self.consumer_name,
                    streams=group_streams,
                    count=count,
                    block=per_group_block_ms,
                )
                # Single group (every standalone deployment) gets no deadline:
                # it exists only to stop one wedged slot starving the others,
                # and with nothing else to protect it just invents a failure
                # whenever the event loop is too busy to service the BLOCK in
                # time. See the matching comment in `consumer.py`.
                results = (
                    await read
                    if len(groups) == 1
                    else await asyncio.wait_for(read, timeout=per_group_timeout_seconds)
                )
            except Exception as e:
                # `type(e).__name__`: `str(asyncio.TimeoutError())` is empty.
                self.logger.warning(
                    "XREADGROUP failed for slot group %s (%d streams): %s: %s",
                    group[0],
                    len(group),
                    type(e).__name__,
                    e,
                )
                first_error = first_error or e
                continue
            if results:
                combined.extend(results)
        if first_error is not None and not combined:
            raise first_error
        return combined

    async def __read_phase(self) -> None:
        """Read a batch and enqueue each entry into the DRR scheduler."""
        budget = self._dispatch_budget()
        saturated = concurrency.index_gates_saturated(self)
        scheduler = self._scheduler
        if scheduler is None:
            return
        self.__sweep_stale_buffered()
        await self.__drain_deferred()
        await self.__recover_while_reading()
        # Parked entries are held in memory too, so they count against the
        # buffer budget -- that budget is what bounds this consumer's memory.
        scheduler_full = (
            scheduler.pending_count + len(self._deferred_entries)
            >= self.fair_scheduler_config.max_buffered_messages
        )

        if budget.blocked or saturated or scheduler_full:
            if not self._backpressure_active:
                self.logger.warning(
                    "Backpressure engaged: tasks waiting for indexing "
                    "admission %s (index gates saturated: %s, scheduler "
                    "buffer full: %s)",
                    budget.describe(),
                    saturated,
                    scheduler_full,
                )
                self._backpressure_active = True
            await asyncio.sleep(0.5)
            return
        elif self._backpressure_active:
            self.logger.info("Backpressure cleared: %s", budget.describe())
            self._backpressure_active = False

        # Skip lanes holding parked entries, so the shared buffer budget goes
        # to lanes that can still make progress. A lane is a stream here, so
        # skipping one leaves its entries *unread* -- not claimed into this
        # consumer's pending list, where they would start ageing toward
        # another replica's idle-claim window.
        #
        # But never skip every lane. Reading is the only way to discover a
        # key that is not backed up, so if the blocked lanes are the only
        # lanes, keep reading them and park what does not fit: the total
        # buffer, not one key's share of it, is the read-ahead window. Losing
        # that distinction caps read-ahead at max_per_entity_messages, which
        # on a single lane means a big backlog at the head of the stream is
        # never read past and every other key starves -- the exact problem
        # fair scheduling exists to solve.
        blocked_lanes = {stream for stream, *_rest in self._deferred_entries}
        readable = [t for t in self.config.topics if t not in blocked_lanes]
        # Skip the blocked lanes only while another lane is actually
        # producing. Merely *existing* is not enough: with eight lanes
        # configured and traffic on one, the seven idle ones look readable
        # forever, so the busy lane is never read again and every key behind
        # the one that filled up starves -- which is the whole failure fair
        # scheduling is meant to prevent.
        if not readable or not (set(readable) & self._lanes_with_data):
            readable = list(self.config.topics)
        streams = dict.fromkeys(readable, ">")

        # Bounded by buffer room, not pipeline capacity: reading only what
        # can be dispatched right now keeps the buffer one entry deep, and
        # DRR needs a mixture of keys buffered to interleave anything.
        # Unreachable at 0 -- the scheduler_full check above already
        # returned -- but the clamp keeps the count valid if that guard ever
        # moves. Redis Streams has no poll-interval eviction, so unlike
        # Kafka there is no reason to issue a read with no room for it.
        buffer_room = max(
            1,
            self.fair_scheduler_config.max_buffered_messages
            - scheduler.pending_count
            - len(self._deferred_entries),
        )
        # COUNT applies to each stream in the request, not to the call, so
        # reading N lanes with count=C can return N*C entries. Anything past
        # the buffer budget would land in this consumer's pending list with
        # times_delivered incremented and then be neither buffered nor
        # parked -- burning delivery attempts on work it cannot hold.
        per_stream = max(1, buffer_room // max(1, len(streams)))
        results = await self._xreadgroup_grouped(
            streams,
            count=min(max(1, self.config.batch_size), per_stream),
            # Short block while work is already buffered: read and dispatch
            # alternate, so blocking the full timeout here delays dispatch of
            # everything already read.
            block_ms=(
                _BUSY_BLOCK_MS if scheduler.pending_count else self.config.block_ms
            ),
        )

        self._lanes_with_data = {
            stream.decode() if isinstance(stream, bytes) else str(stream)
            for stream, entries in (results or [])
            if entries
        }

        if not results:
            self._consecutive_empty_polls += 1
            if self._consecutive_empty_polls >= self._idle_threshold:
                await self._wait_out_backpressure()
                await self._drain_pending()
                self._consecutive_empty_polls = 0
            return

        self._consecutive_empty_polls = 0
        entries = [
            (stream_name, message_id, fields)
            for stream_name, messages in results
            for message_id, fields in messages
        ]
        parsed = [
            await self._parse_message(message_id, fields, _stream)
            for _stream, message_id, fields in entries
        ]
        settled = await self.__settle_connector_off(entries, parsed)
        for position, (stream_name, message_id, fields) in enumerate(entries):
            if not self.running:
                return
            if position in settled:
                continue
            try:
                await self.__enqueue_parsed(
                    stream_name, message_id, fields, parsed[position]
                )
            except Exception as e:
                self.logger.error(
                    "Error enqueuing message %s for fair scheduling: %s",
                    message_id,
                    e,
                )

    async def __settle_connector_off(
        self,
        entries: list[tuple[str, str, dict[str, str]]],
        parsed: list[StreamMessage | None],
    ) -> frozenset[int]:
        """Acknowledge the entries of turned-off or removed connectors that the
        filter settled, before any of them takes buffer room or a dispatch slot.

        Positions of entries whose ack failed are not returned, so they take the
        normal path, where the handler skips them as it always did.
        """
        if self.connector_off_filter is None or self.redis is None:
            return frozenset()
        # A record already being processed here is left to its handler.
        considered = [
            None
            if message is None
            or self._is_record_in_flight(str(message.payload.get("recordId") or ""))
            else message
            for message in parsed
        ]
        result = await settle_connector_off(
            self.connector_off_filter, considered, self.logger
        )
        if not result.settled:
            return frozenset()
        by_stream: dict[str, list[int]] = {}
        for position in sorted(result.settled):
            by_stream.setdefault(entries[position][0], []).append(position)
        acked: set[int] = set()
        for stream_name, positions in by_stream.items():
            try:
                await self.redis.xack(  # type: ignore[union-attr]
                    stream_name,
                    self.config.group_id,
                    *[entries[p][1] for p in positions],
                )
            except Exception as e:
                self.logger.warning(
                    "Could not acknowledge %d settled event(s) of turned-off "
                    "connectors on %s; they take the normal path: %s",
                    len(positions),
                    stream_name,
                    e,
                )
                continue
            acked.update(positions)
        settled = [parsed[p] for p in sorted(acked)]
        for message in settled:
            tracking_id = message.payload.get("_retry_tracking_id") if message else None
            if tracking_id:
                await self._clear_retry_tracking(str(tracking_id))
        if settled:
            metrics.record_connector_off_settled("redis", len(settled))
            self.logger.info(
                "Acknowledged %d queued event(s) of turned-off or removed "
                "connectors without indexing them: %s",
                len(settled),
                describe_connector_off(settled),
            )
        return frozenset(acked)

    async def __dispatch_phase(self) -> None:
        """Dispatch fairly-scheduled entries while pipeline capacity and
        downstream health allow, then hand control back to the read phase."""
        scheduler = self._scheduler
        if scheduler is None:
            return
        budget = self._dispatch_budget()

        def can_dispatch(
            item: tuple[str, str, dict[str, str], StreamMessage, float],
        ) -> bool:
            _stream_name, message_id, _fields, parsed, _buffered_at = item
            # A tier at its ceiling is passed over, not waited on: DRR skips
            # the leaf without charging it and moves to the entity's other
            # tier or the next entity.
            return not self._is_in_flight(message_id, _stream_name) and budget.allows(
                concurrency.dispatch_tier(self, parsed)
            )

        while self.running:
            # Re-checked inside the loop, not just once per iteration in
            # _consume_loop: a dispatch pass can start many records, and a
            # downstream 429 arriving part-way through must stop the rest of
            # them rather than being honoured only on the next poll. Mirrors
            # the Kafka dispatch phase.
            budget = self._dispatch_budget()
            downstream_paused = (
                self.backpressure_coordinator is not None
                and self.backpressure_coordinator.is_paused()
            )
            if (
                budget.blocked
                or downstream_paused
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

            key, (stream_name, message_id, fields, parsed, _buffered_at) = dispatched
            # Ownership passes to the in-flight set from here.
            self._held_entries.discard((stream_name, message_id))
            metrics.record_dispatch("redis", key[0] if key else "unknown")
            try:
                await self._start_processing_task(
                    stream_name, message_id, fields, parsed, slot_key=key
                )
            except Exception as e:
                self.logger.error(
                    "Error dispatching message %s: %s", message_id, e
                )

    async def _consume_loop(self) -> None:
        """Main consumption loop with idle-based pending drain.

        New messages are processed first. When no new messages arrive for
        several consecutive polls (idle), pending messages from the PEL
        are processed (retry of failed messages).
        """
        try:
            self.logger.info("Starting Redis Streams consumer loop")
            # Initial drain on startup
            await self._wait_out_backpressure()
            await self._drain_pending()
            while self.running:
                try:
                    await self._wait_out_backpressure()

                    if self._scheduler is not None:
                        await self.__refresh_held_ownership()
                        await self.__read_phase()
                        await self.__dispatch_phase()
                        self.__publish_scheduler_metrics()
                        continue

                    budget = self._dispatch_budget()
                    # Saturation matters as much as queue depth: with both
                    # index pools full and nothing queued behind them, the
                    # waiter count reads zero while the node cannot start a
                    # single further record. Claiming more entries then only
                    # grows this consumer's PEL.
                    saturated = concurrency.index_gates_saturated(self)
                    if budget.blocked or saturated:
                        if not self._backpressure_active:
                            self.logger.warning(
                                "Backpressure engaged: tasks waiting for "
                                "indexing admission %s (index gates saturated: %s)",
                                budget.describe(),
                                saturated,
                            )
                            self._backpressure_active = True
                        await asyncio.sleep(0.5)
                        continue
                    elif self._backpressure_active:
                        self.logger.info(
                            "Backpressure cleared: %s", budget.describe()
                        )
                        self._backpressure_active = False

                    streams = dict.fromkeys(self.config.topics, ">")
                    available_capacity = budget.remaining
                    results = await self._xreadgroup_grouped(
                        streams,
                        count=min(
                            max(1, self.config.batch_size),
                            available_capacity,
                        ),
                        block_ms=self.config.block_ms,
                    )

                    if not results:
                        # No new messages - increment idle counter
                        self._consecutive_empty_polls += 1
                        if self._consecutive_empty_polls >= self._idle_threshold:
                            # Idle: process pending messages. Re-check backpressure
                            # immediately before draining — it may have engaged
                            # during the xreadgroup block/idle wait above, and
                            # draining would otherwise resubmit recovered
                            # messages to an already-saturated downstream service.
                            await self._wait_out_backpressure()
                            await self._drain_pending()
                            self._consecutive_empty_polls = 0
                        continue

                    # Reset idle counter when new messages arrive
                    self._consecutive_empty_polls = 0

                    for stream_name, messages in results:
                        for message_id, fields in messages:
                            if not self.running:
                                break
                            if self._dispatch_budget().blocked:
                                break
                            try:
                                self.logger.debug(
                                    "Received message: stream=%s, id=%s",
                                    stream_name,
                                    message_id,
                                )
                                await self._start_processing_task(
                                    stream_name, message_id, fields
                                )
                            except Exception as e:
                                self.logger.error(
                                    "Error processing individual message: %s", e
                                )
                                continue

                except asyncio.CancelledError:
                    self.logger.info("Redis Streams consumer task cancelled")
                    break
                except Exception as e:
                    self.logger.error("Error in consume_messages loop: %s", e)
                    if self.running:
                        await asyncio.sleep(1)

        except Exception as e:
            self.logger.error("Fatal error in consume_messages: %s", e)
        finally:
            active_count = self._get_active_task_count()
            self.logger.info(
                "Consume loop exited. Active tasks remaining: %d", active_count
            )

    async def _parse_message(
        self, message_id: str, fields: dict[str, str], stream_name: str
    ) -> StreamMessage | None:
        """Parse a Redis stream entry into a ``StreamMessage``.

        Returns ``None`` for any unparseable ("poison") entry — missing value
        field, malformed JSON, non-object payload, or a payload that fails
        ``StreamMessage`` validation. Such entries can never become valid on
        retry, so the caller drops them (see ``_process_message_wrapper``).
        """
        if _MESSAGE_VALUE_FIELD not in fields:
            self.logger.debug(
                "Message %s has no value field (likely init message); treating as unparseable",
                message_id,
            )
            return None

        try:
            value_str = fields[_MESSAGE_VALUE_FIELD]
            # Offloaded above a size threshold: a connector can emit a record
            # whose whole body rides in the envelope, and parsing it inline
            # blocks every other in-flight record on this one worker loop.
            raw = await offload_if_large(_loads_possibly_double_encoded, value_str)
            return await open_stream_message(stream_name, raw, StreamMessage)
        except EnvelopeRejected as e:
            self.logger.error("Envelope rejected for %s on %s: %s", message_id, stream_name, e.reason)
        except (json.JSONDecodeError, ValidationError, TypeError) as e:
            self.logger.error(
                "Failed to parse message %s as StreamMessage: %s", message_id, e
            )
            return None

    async def _start_processing_task(
        self,
        stream_name: str,
        message_id: str,
        fields: dict[str, str],
        parsed_message: "StreamMessage | None" = None,
        slot_key: FairnessKey | None = None,
    ) -> None:
        if not self.worker_loop:
            self.logger.error("Worker loop not initialized, cannot process message")
            return
        if not self.running:
            return

        self._mark_in_flight(message_id, stream_name)
        slot_share = self._slot_share
        if slot_share is not None and slot_key is not None:
            # Counted against the connector's share from dispatch until the
            # task ends, including any wait at the gate: a record queued for
            # a slot is as good as holding one, because the gate admits in
            # arrival order.
            slot_share.started(
                (stream_name, message_id), slot_key, self._slot_pool_of(slot_key)
            )
        # Everything from the mark to the hand-off is guarded, not just the
        # scheduling call. `__already_held` reads an id in the in-flight set as
        # this consumer's live work, so a stranded id makes the read and
        # dispatch phases skip that entry forever and keeps the recovery scan
        # from ever reclaiming or dead-lettering it. The record it carries
        # would then never be indexed, with nothing logged to say so.
        waiter_token: "concurrency.GateWaiterToken | None" = None
        processing_coro = None
        try:
            if parsed_message is None:
                # The broker-order path spawns from raw fields; the tier has to
                # be known before the token exists. The wrapper reuses this parse.
                parsed_message = await self._parse_message(message_id, fields, stream_name)
            waiter_token = concurrency.GateWaiterToken(
                self, concurrency.dispatch_tier(self, parsed_message)
            )
            processing_coro = self._process_message_wrapper(
                stream_name,
                message_id,
                dict(fields),
                waiter_token,
                parsed_message,
            )
            future = asyncio.run_coroutine_threadsafe(
                processing_coro,
                self.worker_loop,
            )
        except BaseException:
            if processing_coro is not None:
                processing_coro.close()
            self._unmark_in_flight(message_id, stream_name)
            if slot_share is not None:
                slot_share.finished((stream_name, message_id))
            if waiter_token is not None:
                waiter_token.release()
            raise
        with self._futures_lock:
            self._active_futures.add(future)

        def on_future_done(f: Future[bool]) -> None:
            self._unmark_in_flight(message_id, stream_name)
            if slot_share is not None:
                slot_share.finished((stream_name, message_id))
            waiter_token.release()
            with self._futures_lock:
                self._active_futures.discard(f)
            try:
                _ = f.result()
            except FuturesCancelledError:
                # Shutdown cancelled the task. The entry was never ACKed, so it
                # stays in the PEL and is redelivered — this is a normal
                # outcome, not the unhandled-exception case below.
                self.logger.info(
                    "Processing task for %s was cancelled; entry left for "
                    "redelivery",
                    message_id,
                )
            except Exception as exc:
                # %r and the traceback: a bare TimeoutError's message is empty.
                self.logger.error(
                    "Processing task for %s ended with an unhandled exception: %r",
                    message_id,
                    exc,
                    exc_info=exc,
                )

        future.add_done_callback(on_future_done)

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

    async def _ack_message(self, stream_name: str, message_id: str) -> None:
        """Acknowledge ``message_id`` so it leaves the consumer group's PEL.

        Processing runs on the worker loop, but ``self.redis`` is bound to the
        main loop where it was created — so the XACK is scheduled there and
        awaited via ``wrap_future`` so the worker loop is never blocked.
        """
        if not self.redis or not self.main_loop or not self.main_loop.is_running():
            raise RedisAcknowledgementError(
                f"Cannot acknowledge {message_id}: Redis main loop is unavailable"
            )

        try:
            await self._run_on_main_loop(
                self.redis.xack(  # type: ignore
                    stream_name,
                    self.config.group_id,
                    message_id,
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise RedisAcknowledgementError(
                f"Could not acknowledge {message_id}; entry remains retryable"
            ) from exc

    async def _pending_message_is_owned(
        self,
        stream_name: str,
        message_id: str,
    ) -> bool:
        if not self.redis or not self.main_loop or not self.main_loop.is_running():
            raise RuntimeError(
                f"Cannot validate pending ownership for {message_id}"
            )

        details = await self._run_on_main_loop(
            self.redis.xpending_range(  # type: ignore
                stream_name,
                self.config.group_id,
                min=message_id,
                max=message_id,
                count=1,
            )
        )
        for detail in details:
            raw_message_id = detail.get(
                "message_id",
                detail.get(b"message_id"),
            )
            raw_consumer = detail.get("consumer", detail.get(b"consumer"))
            pending_id = (
                raw_message_id.decode()
                if isinstance(raw_message_id, bytes)
                else str(raw_message_id)
            )
            owner = (
                raw_consumer.decode()
                if isinstance(raw_consumer, bytes)
                else str(raw_consumer)
            )
            if pending_id == message_id:
                return owner == self.consumer_name
        return False

    def _get_stable_message_id(self, message_id: str, parsed_message: StreamMessage | None = None) -> str:
        """Get a stable message ID for retry tracking.
        
        Uses _retry_tracking_id from payload if present (for re-queued messages),
        otherwise uses the current message ID.
        
        Args:
            message_id: The current Redis Streams message ID
            parsed_message: The parsed StreamMessage (if available)
            
        Returns:
            Stable message ID for retry tracking
        """
        if parsed_message and "_retry_tracking_id" in parsed_message.payload:
            return str(parsed_message.payload["_retry_tracking_id"])

        return message_id

    async def _send_in_org(self, message: StreamMessage, topic: str, event_type: str, payload: dict) -> None:
        """Re-publish under the message's own org so a retry is signed like the original (the handler's bind is gone by now)."""
        with bind_optional(ctx_of(message)):
            await self.producer.send_event(topic=topic, event_type=event_type, payload=payload)

    async def _requeue_message(
        self,
        stream_name: str,
        message: StreamMessage,
        stable_message_id: str,
        retry_count: int = 1,
        delay_s: float | None = None,
    ) -> None:
        """Re-publish a failed message for retry.

        A lane stream's message goes back to its topic, so the producer's
        router places it: the same lane for a connector that has not moved,
        and the new one for a connector that has, so nothing new reaches a
        lane after its move is fenced. A retry was never ordered against the
        rest of its lane anyway. Any other stream gets it back as it was.

        The message goes to the end of the queue. Stamps an exponential-backoff
        "not before" timestamp (see __delay_if_retry_not_ready) so a downed
        downstream service gets time to recover instead of the message being
        immediately re-picked-up and re-failed in a tight loop. The original
        message is acknowledged.
        
        Preserves the stable message ID in the payload for retry tracking.
        
        Args:
            stream_name: Stream the message was read from
            message: The message to re-queue
            stable_message_id: Stable ID for retry tracking (preserved across re-queues)
            retry_count: Number of prior failures, used to compute backoff delay
            delay_s: Hold it this long instead (a paused dependency's wait)
        """
        if not self.producer:
            raise RuntimeError("No producer available for re-queue")

        try:
            payload = dict(message.payload)
            payload["_retry_tracking_id"] = stable_message_id
            backoff = delay_s if delay_s is not None else compute_retry_backoff_seconds(retry_count)
            payload["_retry_not_before"] = time.time() + backoff

            # The handler's context is reset by now, and the send runs on the
            # main loop under its own: re-bind the message's so the retry
            # keeps its request id and org.
            await self._run_on_main_loop(
                run_in_context(
                    context_from_envelope(
                        {"requestId": message.requestId, "orgId": message.orgId}
                    ),
                    self._send_in_org(
                        message, self._retry_topic(stream_name), message.eventType, payload
                    ),
                )
            )
        except Exception as e:
            self.logger.error(f"Failed to re-queue message to {stream_name}: {e}")
            raise

    @staticmethod
    def _retry_topic(stream_name: str) -> str:
        base = _LANE_SUFFIX.sub("", stream_name)
        if base != stream_name and base in messaging_env.fair_scheduling_laned_topics:
            return base
        return stream_name

    async def _delay_if_retry_not_ready(
        self, parsed_message: StreamMessage, message_id: str
    ) -> bool:
        """Sleep out the remaining backoff window for a re-queued message.

        Called before any semaphore is acquired (see _process_message_wrapper),
        so the wait ties up only a pending-task slot, not a parsing/indexing
        concurrency slot, while a downstream outage clears.

        Sleeps in small increments and re-checks ``self.running`` between
        them, so a shutdown request interrupts the wait quickly instead of
        holding this future for up to the full ~300s backoff.

        Returns False if the consumer is shutting down and the wait was
        abandoned early (caller should not process/ack the message — it
        stays in the PEL and will be redelivered).
        """
        not_before = parsed_message.payload.get("_retry_not_before")
        if not not_before:
            return True
        try:
            remaining = float(not_before) - time.time()
        except (TypeError, ValueError):
            return True
        if remaining <= 0:
            return True

        self.logger.debug(
            "Delaying re-queued message %s for %.1fs before processing",
            message_id,
            remaining,
        )
        while remaining > 0:
            if not self.running:
                self.logger.info(
                    "Consumer stopping, abandoning delayed retry for %s "
                    "(left un-acked, will be redelivered)",
                    message_id,
                )
                return False
            await asyncio.sleep(min(_DELAY_POLL_INTERVAL_SECONDS, remaining))
            remaining -= _DELAY_POLL_INTERVAL_SECONDS
        return True

    async def _process_message_wrapper(
        self,
        stream_name: str,
        message_id: str,
        fields: dict[str, str],
        waiter_token: "concurrency.GateWaiterToken | None" = None,
        parsed_message: "StreamMessage | None" = None,
    ) -> bool:
        """Process a message under bounded pipeline and parsing concurrency.

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
        - TERMINAL: ACK immediately (parsing errors, validation errors)
        - TRANSIENT: Don't ACK, let PEL retry
        """
        record_claim_held = False
        suspect_held = False
        attempt_started = False
        abandoned = False
        parsing_held = False
        indexing_held = False
        indexing_complete = False
        shutting_down = False
        acked = False
        parse_lease_pool = "parsing"
        index_lease_pool = "indexing"
        parsing_admission: concurrency.Admission | None = None
        memory_admission: concurrency.Admission | None = None
        index_admission: concurrency.Admission | None = None
        distributed_leases = concurrency.new_lease_set(self)
        lease_handle: Any | None = None
        renewal_task: asyncio.Future[bool] | None = None
        lease_owner = f"{self.consumer_name}:{message_id}:{uuid.uuid4().hex}"

        if self.governor is None and (
            self.indexing_semaphore is None or self.parsing_semaphore is None
        ):
            self.logger.error("Concurrency gates not initialized for %s", message_id)
            return False

        # Parse (and, for re-queued messages, wait out any backoff) before
        # acquiring the parsing semaphore, so a retry waiting for a downed
        # service to recover only occupies a pending-task slot (counted
        # against backpressure), never a parsing/indexing concurrency slot.
        # Reuses the caller's parse when the read/dispatch split (or PEL
        # recovery) already did it -- avoids a second full json.loads (and,
        # above the offload threshold, a second thread hop) on the same
        # envelope.
        if parsed_message is None:
            parsed_message = await self._parse_message(message_id, fields, stream_name)
        if parsed_message is None:
            self.logger.warning(
                "Unparseable message %s from stream %s (%d byte payload); "
                "acknowledging without retry",
                message_id,
                stream_name,
                len(fields.get(_MESSAGE_VALUE_FIELD, "") or ""),
            )
            await notify_abandoned(
                self.disposition_sink,
                self.logger,
                None,
                reason=f"unparseable envelope on {stream_name}",
                attempts=1,
            )
            try:
                await self._ack_message(stream_name, message_id)
            except RedisAcknowledgementError as exc:
                self.logger.warning("%s", exc)
                return False
            await self._clear_retry_tracking(message_id)
            return False

        stable_message_id = self._get_stable_message_id(message_id, parsed_message)
        record_lock_id = concurrency.work_key(parsed_message.payload, stable_message_id)
        record_pool = f"record:{record_lock_id}"

        if not await self._delay_if_retry_not_ready(parsed_message, message_id):
            return False

        # Verify PEL ownership unconditionally (only needs self.redis, not the
        # distributed concurrency manager) — an XAUTOCLAIMed message can still
        # be concurrently processed by whichever consumer held it before if we
        # only check this when distributed concurrency is enabled.
        try:
            owned = await self._pending_message_is_owned(stream_name, message_id)
        except Exception as exc:
            self.logger.warning(
                "Could not check pending ownership of %s (%r); left pending "
                "for retry",
                message_id,
                exc,
            )
            return False
        if not owned:
            self.logger.debug(
                "Skipping %s because its pending entry was ACKed or "
                "transferred to another consumer",
                message_id,
            )
            return False

        # Claimed after the backoff wait and the ownership check, so a message
        # that is only sleeping or is not ours never holds one.
        if not self._claim_record(record_lock_id):
            self.logger.debug(
                "Skipping %s: another in-flight delivery in this process "
                "already holds record %s. Left un-acked so it is redelivered "
                "once that one finishes.",
                message_id,
                record_lock_id,
            )
            return False
        record_claim_held = True

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
        index_lease_pool = concurrency.admission_lease_pool(self, index_tier)
        # Set where an exit is neither a success nor a failure; the
        # timing summary falls back to indexing_complete/shutting_down.
        outcome: str | None = None
        timeline, timeline_token = timing.begin_record(
            parsed_message.payload.get("recordId"),
            parsed_message.eventType,
            index_tier.value,
        )

        async def give_up(reason: str, attempts: int) -> None:
            nonlocal acked, abandoned
            # Our own record lease reads as a live holder, which keeps the
            # sink from failing a record left IN_PROGRESS.
            if distributed_leases.discard(record_pool) is not None:
                await self._release_distributed_slot(record_pool, lease_owner)
            try:
                await self._abandon_message(
                    message_id,
                    stable_message_id,
                    parsed_message,
                    reason=reason,
                    attempts=attempts,
                    ack=lambda: self._ack_message(stream_name, message_id),
                )
            except RedisAcknowledgementError as exc:
                # Left pending, its count intact: the next delivery decides again.
                self.logger.warning("%s", exc)
                return
            acked = abandoned = True

        try:
            # Taken before any permit, so suspects queue here without holding
            # one; one that will be given up on below does not need it.
            unfinished = await concurrency.unfinished_attempts(self, record_lock_id)
            if 0 < unfinished < concurrency.MAX_UNFINISHED_ATTEMPTS:
                await self._suspects.acquire()
                suspect_held = True

            # The active-pipeline bound. Without this outer permit, parsed
            # records can accumulate while waiting for an indexing permit and
            # every one can remain IN_PROGRESS in the DB.
            index_admission = await concurrency.acquire_index_slot(self, index_tier)
            indexing_held = True
            timeline.mark(timing.ADMITTED)
            if waiter_token is not None:
                waiter_token.admit()

            if self.concurrency_manager is not None:
                # Taken only once the local permit is held: stale-record
                # recovery reads this lease as proof of active processing, so
                # a task still queued on the gate must not own one.
                with timing.stage("lease.index"):
                    index_lease_held = await self._acquire_distributed_slot(
                        index_lease_pool,
                        lease_owner,
                        concurrency.index_ceiling(self, index_tier),
                        leases=distributed_leases,
                    )
                if not index_lease_held:
                    # Capacity leases only give up when self.running flips.
                    outcome = "stopped"
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
                    outcome = "contended" if self.running else "stopped"
                    if self.running:
                        self.logger.debug(
                            "Record lease contended for %s; another in-flight "
                            "duplicate delivery already owns it, leaving this "
                            "entry un-acked in the PEL (XAUTOCLAIM will retry "
                            "it once idle, by which point the other delivery "
                            "should have advanced the record past IN_PROGRESS)",
                            message_id,
                        )
                    return False

                parsed_message.payload["_processing_started_at"] = int(time.time() * 1000)

            # Counted only now that the record is ours: a crash while it waited
            # for a permit or for its lease was not its doing.
            unfinished = await concurrency.start_attempt(self, record_lock_id)
            attempt_started = True
            if unfinished >= concurrency.MAX_UNFINISHED_ATTEMPTS:
                await give_up(
                    f"{unfinished} earlier attempts never finished; it is likely "
                    "taking the indexing service down",
                    unfinished,
                )
                return False

            # Check current retry count to predict if this will be the final attempt on failure
            current_retry_count = await self._get_retry_count(stable_message_id)

            will_be_final_on_failure = (
                not self.retry_manager or
                current_retry_count >= messaging_env.max_delivery_attempts - 1
            )

            parsed_message.is_final_failure = will_be_final_on_failure

            if self.message_handler:
                token = bind_context(
                    context_from_envelope(
                        {
                            "requestId": parsed_message.requestId,
                            "orgId": parsed_message.orgId,
                        }
                    )
                )
                processing_timeout = messaging_env.record_processing_timeout_for(
                    parsed_message.payload.get("extension"),
                    parsed_message.payload.get("mimeType"),
                )
                org_token = push_org(ctx_of(parsed_message))

                async def consume_handler_events() -> None:
                    nonlocal memory_admission, parsing_held, indexing_held, indexing_complete, shutting_down, parsing_admission, parse_lease_pool
                    event_gen = self.message_handler(parsed_message)
                    try:
                        async with asyncio.timeout(processing_timeout) as budget:
                            async for event in event_gen:
                                if (
                                    event.event == IndexingEvent.START_DOWNLOAD
                                    and memory_admission is None
                                ):
                                    # Before the download: a record waiting
                                    # for memory must not hold its content.
                                    async with concurrency.parse_admission_wait(
                                        self, budget, message_id
                                    ) as wait:
                                        memory_admission = await concurrency.reserve_record_memory(
                                            self,
                                            index_tier,
                                            event.data.size_bytes if event.data else None,
                                            timeout=wait.remaining(),
                                        )
                                elif (
                                    event.event == IndexingEvent.START_PARSING
                                    and not parsing_held
                                ):
                                    parse_tier = event.data.tier if event.data else None
                                    timeline.mark(timing.PARSE_REQUESTED)
                                    if self.governor is not None:
                                        parse_lease_pool = concurrency.parse_lease_pool(parse_tier)
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
                                        memory_admission = await concurrency.reserve_record_memory(
                                            self, parse_tier, size_bytes,
                                            timeout=wait.remaining(), downloaded=True,
                                            current=memory_admission,
                                        )
                                        parsing_admission = await concurrency.acquire_parsing_slot(
                                            self,
                                            parse_tier,
                                            size_bytes,
                                            timeout=wait.remaining(),
                                        )
                                        # Set before the lease attempt so the
                                        # wrapper's finally hands this permit
                                        # back on every exit path below.
                                        parsing_held = True
                                        lease_held = (
                                            self.concurrency_manager is None
                                            or await self._acquire_distributed_slot(
                                                parse_lease_pool,
                                                lease_owner,
                                                concurrency.parse_ceiling(self, parse_tier),
                                                deadline_seconds=wait.remaining(),
                                                leases=distributed_leases,
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
                                    indexing_held = False
                                    indexing_complete = True
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
                    self.logger.error(
                        "Record processing timed out after %ss for %s",
                        processing_timeout,
                        message_id,
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
                    pop_org(org_token)
                    reset_context(token)

                if shutting_down:
                    # Consumer stopped while waiting for the parsing slot:
                    # leave the entry un-acked (PEL redelivers it) instead of
                    # raising into the retry-count-incrementing exception path.
                    self.logger.info(
                        "Consumer stopping, abandoning %s without ack", message_id
                    )
                    return False
                if not indexing_complete:
                    raise RuntimeError(
                        f"Handler ended without INDEXING_COMPLETE for {message_id}"
                    )
                await self._ack_message(stream_name, message_id)
                acked = True
                await self._clear_retry_tracking(stable_message_id)
            else:
                self.logger.error("No message handler available for %s", message_id)
                return False

            return True

        except asyncio.CancelledError:
            # A BaseException, so the recovery below cannot see it: no failure
            # is counted, nothing is re-queued and nothing is ACKed. That is the
            # correct outcome — the entry stays in the PEL and is redelivered —
            # but it has to be visible, and it has to name the record, because
            # the handler has already written a status on the way out.
            self.logger.warning(
                "Processing of %s (%s) was cancelled; leaving it un-acked for "
                "redelivery",
                message_id,
                describe_message(parsed_message),
            )
            raise
        except RedisAcknowledgementError as e:
            self.logger.warning("%s", e)
            return False
        except concurrency.RequeueWithoutAttempt as e:
            # Queue time, not a failure (a parse-slot wait that ran out, or a stage
            # whose dependency is paused): hand the message back with no attempt
            # counted, so it cannot be dead-lettered for waiting.
            outcome = "requeued"
            self.logger.warning(
                "%s handed back without counting an attempt: %s", message_id, e,
            )
            if parsed_message is not None:
                if not e.counts_toward_backstop:
                    # A clean run that found a dependency down, not a crash loop: its
                    # growing delay bounds how often it returns while the outage lasts.
                    await concurrency.clear_delivery_count(self, stable_message_id)
                else:
                    # Each re-queue is a fresh stream entry with a failure count of
                    # zero; only this counter stops it coming back for ever.
                    cycles, backstop_tripped = await concurrency.record_delivery(
                        self, stable_message_id
                    )
                    if backstop_tripped:
                        await give_up(
                            f"handed back {cycles} times without being admitted "
                            f"(backstop {messaging_env.redis_max_deliveries})",
                            cycles,
                        )
                        return False
                try:
                    await self._requeue_message(
                        stream_name, parsed_message, stable_message_id, retry_count=0, delay_s=e.delay_s
                    )
                    await self._ack_message(stream_name, message_id)
                    acked = True
                except Exception as requeue_error:
                    self.logger.error(
                        "Failed to re-queue %s after its admission wait: %s. "
                        "Message will stay in PEL",
                        message_id, requeue_error,
                    )
            return False
        except Exception as e:
            if acked:
                exception_chain = format_exception_chain(e)
                self.logger.error(
                    "Post-ACK cleanup failed for %s (message already committed, "
                    "not retrying):\n%s",
                    message_id,
                    exception_chain,
                )
                await self._clear_retry_tracking(stable_message_id)
                return True

            if is_expected_outcome(e):
                # Expected: the handler already logged it once.
                self.logger.info("Item for %s ended as expected: %s: %s", message_id, type(e).__name__, e)
            else:
                # Log the full exception chain for debugging
                exception_chain = format_exception_chain(e)
                self.logger.error(
                    "Error in process_message_wrapper for %s:\n%s", message_id, exception_chain
                )
            concurrency.report_memory_incident_if_applicable(self, message_id, e)

            # Classify the exception to determine if we should retry
            error_type = MessageErrorClassifier.classify_by_exception(e)

            if error_type == MessageErrorType.TERMINAL:
                # Update is_final_failure for terminal errors
                if parsed_message:
                    parsed_message.is_final_failure = True
                # Terminal error: ACK immediately to skip this message. Route
                # it through the sink too — the handler usually wrote FAILED on
                # its way out, in which case this is a no-op, but an error
                # raised before it ever ran (a malformed envelope, a missing
                # orgId) leaves no trace at all otherwise.
                await self._abandon_message(
                    message_id,
                    stable_message_id,
                    parsed_message,
                    reason=f"terminal error: {type(e).__name__}",
                    attempts=1,
                    ack=lambda: self._ack_message(stream_name, message_id),
                )
                acked = True
            elif self.retry_manager is not None and parsed_message:
                try:
                    failure_count, should_dead_letter = (
                        await self._increment_retry_and_check(stable_message_id)
                    )
                except Exception as count_error:
                    self.logger.warning(
                        "Could not count the failed attempt of %s (%r); left "
                        "pending for retry",
                        message_id,
                        count_error,
                    )
                    return False
                if should_dead_letter:
                    await self._abandon_message(
                        message_id,
                        stable_message_id,
                        parsed_message,
                        reason=(
                            f"{failure_count} transient failures "
                            f"(max {messaging_env.max_delivery_attempts}), "
                            f"last was {type(e).__name__}"
                        ),
                        attempts=failure_count,
                        ack=lambda: self._ack_message(stream_name, message_id),
                    )
                    acked = True
                else:
                    # RE-QUEUE: Publish back to same stream for retry, then ACK
                    try:
                        await self._requeue_message(
                            stream_name, parsed_message, stable_message_id, retry_count=failure_count
                        )
                        await self._ack_message(stream_name, message_id)
                        acked = True
                        self.logger.info(
                            "Re-queued %s (tracking ID: %s) for retry (attempt %d/%d): %s",
                            message_id,
                            stable_message_id,
                            failure_count,
                            messaging_env.max_delivery_attempts,
                            type(e).__name__,
                        )
                    except Exception as requeue_error:
                        self.logger.error(
                            "Failed to re-queue %s: %s. Message will stay in PEL",
                            message_id,
                            requeue_error,
                        )
            else:
                # Transient error: don't ACK, let PEL retry (fallback for no retry manager or unparseable)
                self.logger.warning(
                    "Transient error for %s, will retry via PEL: %s",
                    message_id,
                    type(e).__name__,
                )

            return False
        finally:
            # First, and deliberately: this is a plain set discard with no
            # await, so unlike everything below it cannot be skipped by a
            # cancellation landing inside this block. A stranded claim would
            # block every later delivery of the record for the life of the
            # process.
            if record_claim_held:
                self._release_record(record_lock_id)
            if suspect_held:
                self._suspects.release()
            concurrency.release_admission(memory_admission)
            try:
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
                        self, record_lock_id, settled=indexing_complete or abandoned
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
                if indexing_held:
                    if distributed_leases.discard(index_lease_pool) is not None and release_leases:
                        await self._release_distributed_slot(index_lease_pool, lease_owner)
                    concurrency.release_admission(index_admission)

                for pool, owner in distributed_leases.snapshot():
                    distributed_leases.discard(pool)
                    if release_leases:
                        await self._release_distributed_slot(pool, owner)
            finally:
                # After the releases, so slow cleanup counts toward the hold;
                # in a finally, so a release that raises or is cancelled
                # still leaves a summary.
                timeline.mark(timing.RELEASED)
                timing.end_record(
                    timeline,
                    timeline_token,
                    outcome=outcome or (
                        "ok" if indexing_complete else ("stopped" if shutting_down else "failed")
                    ),
                    logger=self.logger,
                )
