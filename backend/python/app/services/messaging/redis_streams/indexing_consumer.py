import asyncio
import json
import re
import threading
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from concurrent.futures import CancelledError as FuturesCancelledError
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import wait as futures_wait
from logging import Logger
from typing import TYPE_CHECKING, Any, Optional, override

from pydantic import ValidationError

from app.services.messaging import consumer_concurrency as concurrency
from app.services.messaging.config import (
    IndexingEvent,
    IndexingMessageHandler,
    RedisStreamsConfig,
    StreamMessage,
    compute_retry_backoff_seconds,
    messaging_env,
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
)
from app.services.messaging.interface.consumer import IMessagingConsumer
from app.services.messaging.interface.producer import IMessagingProducer
from app.services.distributed.interface import IDistributedLeaseManager, IRetryTracker
from app.services.messaging.lease import LeaseRenewer
from app.services.messaging.redis_streams.stream_read_planner import StreamReadPlanner
from app.services.messaging.retry_manager import RetryManager
from app.services.messaging.scheduling.drr_scheduler import DRRScheduler
from app.services.messaging.scheduling.interface import (
    EnqueueResult,
    FairnessKey,
    FairnessKeyExtractor,
    FairSchedulerConfig,
    WeightProvider,
)
from app.services.messaging.scheduling.key_extractors import CompositeKeyExtractor
from app.services.redis.config import ClientOptions, RedisConnectionConfig
from app.services.redis.connection_provider_factory import get_redis_provider
from app.services.resource_governor import ParseTier, Pool, classify
from app.telemetry.modules import scheduling_metrics as metrics
from app.utils.cpu_offload import offload_if_large
from app.utils.request_context import (
    context_from_envelope,
    reset_context,
    set_context,
)

if TYPE_CHECKING:
    from app.services.messaging.backpressure import BackpressureCoordinator
    from app.services.messaging.distributed_concurrency import (
        DistributedConcurrencyManager,
    )
    from app.services.redis.connection_provider import IRedisConnectionProvider, RedisClient as Redis
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
    ) -> None:
        self.logger = logger
        self.config = config
        self._provider: "IRedisConnectionProvider" = provider or get_redis_provider(
            RedisConnectionConfig.from_redis_config(config)
        )
        self._planner = StreamReadPlanner(self._provider)
        # PEL ownership is keyed by consumer name; sharing one across replicas
        # lets one process re-read another process's still-active messages.
        self.consumer_name = f"{config.client_id}-{uuid.uuid4().hex}"
        self.retry_manager = retry_manager
        # Told about every message this consumer gives up on, before the XACK
        # that makes it unrecoverable — see disposition.AbandonedMessageSink.
        self.disposition_sink = disposition_sink
        self.producer = producer
        self.concurrency_manager = concurrency_manager
        # When set, node-local parsing/indexing admission is delegated to the
        # ResourceGovernor's adaptive gates instead of the static semaphores
        # below (see consumer_concurrency.acquire_parsing_slot/index_ceiling).
        self.governor = governor
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
        self.worker_executor: ThreadPoolExecutor | None = None
        self.worker_loop: asyncio.AbstractEventLoop | None = None
        self.worker_loop_ready = threading.Event()
        self.main_loop: asyncio.AbstractEventLoop | None = None
        # Legacy fallback only: unused (stay None) once a governor is set.
        self.parsing_semaphore: asyncio.Semaphore | None = None
        self.indexing_semaphore: Any = None
        # One renewer for every lease this process holds, started on the
        # worker loop beside the records it guards (see lease.LeaseRenewer).
        self.lease_renewer: LeaseRenewer | None = None
        self.message_handler: IndexingMessageHandler | None = None
        self._active_futures: set[Future[bool]] = set()
        self._futures_lock = threading.Lock()
        self._gate_waiters = 0
        self._backpressure_active = False
        self._consecutive_empty_polls = 0
        self._idle_threshold = 3  # Drain pending after N consecutive empty polls
        self._in_flight_message_ids: set[str] = set()
        # Keyed by recordId, not by entry id. Two entries can carry the same
        # record -- the original and one the stranded sweep re-published -- and
        # the message-id set above cannot see that they are the same work.
        self._in_flight_record_ids: set[str] = set()
        self._in_flight_lock = threading.Lock()

        # Absent config disables fair scheduling and keeps this consumer on
        # the exact pre-existing FIFO code path -- only MessagingFactory
        # (the production wiring point) resolves the env-driven OSS default,
        # so tests/call sites that construct this class directly are
        # unaffected unless they opt in explicitly.
        self.fair_scheduler_config = fair_scheduler_config or FairSchedulerConfig(
            enabled=False
        )
        self.key_extractor: FairnessKeyExtractor = key_extractor or CompositeKeyExtractor(
            fields=self.fair_scheduler_config.key_fields
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
        # design, so the recovery path would otherwise re-claim them -- and
        # every claim bumps Redis's times_delivered, which the dead-letter
        # backstop reads as a failed attempt.
        self._held_entries: dict[str, str] = {}
        # Lanes that returned entries on the most recent read. Skipping a
        # blocked lane is only sound while some *other* lane is actually
        # producing; an idle lane is not an alternative source of work.
        self._lanes_with_data: set[str] = set()
        self._last_ownership_refresh = 0.0
        if self.fair_scheduler_config.enabled:
            self._scheduler = DRRScheduler(self.fair_scheduler_config, self.weight_provider)


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

    def _start_worker_thread(self) -> None:
        def run_worker_loop() -> None:
            self.worker_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.worker_loop)
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
                self.indexing_semaphore = asyncio.Semaphore(messaging_env.max_concurrent_indexing)
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
                self.worker_loop.call_soon(self.lease_renewer.start)
            self.worker_loop_ready.set()
            try:
                self.worker_loop.run_forever()
            finally:
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

            self.consume_task = asyncio.create_task(self._consume_loop())
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

    async def stop(  # type: ignore[override]
        self,
        message_handler: IndexingMessageHandler | None = None,
    ) -> None:
        self.logger.info("Stopping Redis Streams consumer...")
        self.running = False

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

    def _stop_worker_thread(self) -> None:
        self._wait_for_active_futures()
        if self.worker_loop and self.worker_loop.is_running():
            self.worker_loop.call_soon_threadsafe(self.worker_loop.stop)
        if self.worker_executor:
            self.worker_executor.shutdown(wait=True)
            self.worker_executor = None
            self.worker_loop = None
        with self._futures_lock:
            self._active_futures.clear()
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

    def _get_gate_waiter_count(self) -> int:
        return concurrency.get_gate_waiter_count(self)

    def _is_in_flight(self, message_id: str) -> bool:
        with self._in_flight_lock:
            return message_id in self._in_flight_message_ids

    def _mark_in_flight(self, message_id: str) -> None:
        with self._in_flight_lock:
            self._in_flight_message_ids.add(message_id)

    def _unmark_in_flight(self, message_id: str) -> None:
        with self._in_flight_lock:
            self._in_flight_message_ids.discard(message_id)

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

        Prefers RetryManager's app-tracked failure count (actual transient
        failures, not Redis times_delivered which also counts every idle-drain
        re-read), but always falls back to checking the Redis-native
        times_delivered too: the app-tracked counter only increments inside
        _process_message_wrapper's except handler, which a process crash or
        kill mid-handler skips entirely, so it can never catch up to the real
        delivery count on its own (#2992).

        ``stable_message_id`` is the ``_retry_tracking_id`` a re-queued entry
        carries in its payload — RetryManager state is keyed by that stable
        id everywhere else (see ``_process_message_wrapper``), not by the
        current Redis message_id, which changes on every re-queue. Falls back
        to ``message_id`` when no stable id is available (first delivery).
        """
        max_attempts = messaging_env.max_delivery_attempts
        # The two counters measure different things and must not share a
        # threshold. The app-tracked count is *processing failures*; Redis's
        # times_delivered counts every delivery -- the first read, a claim
        # after a restart, an idle-drain recovery pass. A record that has
        # simply been redelivered a few times has failed zero times, and
        # dead-lettering it would discard healthy work. The backstop exists
        # only to bound a true poison loop that crashes the process before
        # the app counter can be written (#2992), so it is deliberately
        # slack relative to the real retry budget.
        delivery_backstop = messaging_env.redis_max_deliveries
        tracking_id = stable_message_id or message_id

        if self.retry_manager is not None:
            try:
                failure_count = await self._get_retry_count(tracking_id)
            except Exception as e:
                # Isolated from the times_delivered backstop below: a failed
                # lookup here (e.g. a Redis error inside RetryManager) must
                # not skip the backstop, which doesn't depend on this count.
                self.logger.error("Error checking app-tracked retry count: %s", e)
            else:
                if failure_count >= max_attempts:
                    await self._abandon_message(
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
                    return True
                # Fall through to the times_delivered backstop below: the
                # app-tracked count only increments inside
                # _process_message_wrapper's except handler, which never runs
                # if the process crashes/is killed mid-handler, so it can lag
                # the real delivery count indefinitely (see #2992).

        try:
            details = await self.redis.xpending_range(  # type: ignore
                topic,
                self.config.group_id,
                min=message_id,
                max=message_id,
                count=1,
            )
            if details:
                times_delivered = details[0].get("times_delivered", 0)
                if times_delivered >= delivery_backstop:
                    await self._abandon_message(
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
                    return True
        except Exception as e:
            self.logger.error("Error checking delivery count: %s", e)

        return False

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
            remaining = self.backpressure_coordinator.pause_remaining()
            await asyncio.sleep(min(_DELAY_POLL_INTERVAL_SECONDS, remaining) if remaining > 0 else _DELAY_POLL_INTERVAL_SECONDS)
        if self._downstream_backpressure_active:
            self._downstream_backpressure_active = False
            self.logger.info("Downstream backpressure cleared; resuming stream reads")

    async def _drain_pending(self) -> bool:
        """Re-process messages left in the Pending Entries List (PEL).

        Called when no new messages arrive (idle-based retry). Returns True
        if any pending messages were processed.

        Phase 1: XAUTOCLAIM to steal idle messages from other (crashed) consumers.
        Phase 2: XREADGROUP with id "0" to recover messages already owned by THIS
        consumer.
        """
        processed_any = False

        for topic in self.config.topics:
            # Phase 1: claim idle messages from other (possibly crashed) consumers
            start_id = "0-0"
            while self.running:
                waiter_count = self._get_gate_waiter_count()
                pending_ceiling = concurrency.pending_task_ceiling(self)
                if waiter_count >= pending_ceiling:
                    await asyncio.sleep(0.5)
                    continue
                claim_budget = self.__recovery_claim_budget(
                    pending_ceiling - waiter_count
                )
                if claim_budget <= 0:
                    break
                try:
                    result = await self.redis.xautoclaim(  # type: ignore
                        topic,
                        self.config.group_id,
                        self.consumer_name,
                        min_idle_time=self.config.claim_min_idle_ms,
                        start_id=start_id,
                        count=claim_budget,
                    )
                    next_id, claimed, _deleted = result
                    if not claimed:
                        break
                    for message_id, fields in claimed:
                        if not self.running:
                            return processed_any
                        if self._is_in_flight(message_id):
                            continue
                        if (
                            self._get_gate_waiter_count()
                            >= concurrency.pending_task_ceiling(self)
                        ):
                            break
                        try:
                            parsed_message = await self._parse_message(message_id, fields)
                            stable_message_id = self._get_stable_message_id(
                                message_id, parsed_message
                            )
                            if self.__already_held(message_id):
                                continue
                            if await self._should_dead_letter(
                                topic, message_id, stable_message_id, parsed_message
                            ):
                                continue
                            processed_any = True
                            self.logger.info(
                                "Recovering pending message: stream=%s, id=%s",
                                topic,
                                message_id,
                            )
                            await self.__dispatch_or_enqueue(
                                topic, message_id, fields, parsed_message
                            )
                        except Exception as e:
                            self.logger.error(
                                "Error recovering pending message %s: %s",
                                message_id,
                                e,
                            )
                    start_id = next_id
                    if next_id == b"0-0" or next_id == "0-0":
                        break
                except Exception as e:
                    self.logger.error("Error during XAUTOCLAIM on %s: %s", topic, e)
                    break

            # Phase 2: read messages already in THIS consumer's PEL.
            #
            # Skipped unless something in it is genuinely unaccounted for.
            # The XREADGROUP id="0" below re-reads the *whole* pending list,
            # and that read increments times_delivered on every entry it
            # returns -- including ones merely buffered in the scheduler.
            # The dead-letter backstop reads that counter as failed
            # attempts, so running this pass unconditionally dead-letters
            # healthy records after a few idle cycles.
            if not await self.__has_unheld_pending(topic):
                await self._cleanup_empty_consumers(topic)
                continue
            last_pending_id = "0"
            while self.running:
                waiter_count = self._get_gate_waiter_count()
                pending_ceiling = concurrency.pending_task_ceiling(self)
                if waiter_count >= pending_ceiling:
                    await asyncio.sleep(0.5)
                    continue
                try:
                    available_capacity = pending_ceiling - waiter_count
                    results = await self.redis.xreadgroup(  # type: ignore
                        groupname=self.config.group_id,
                        consumername=self.consumer_name,
                        streams={topic: last_pending_id},
                        count=min(
                            max(1, self.config.batch_size),
                            available_capacity,
                        ),
                    )

                    if not results:
                        break

                    drained_any = False
                    for _stream_name, messages in results:
                        if not messages:
                            continue
                        for message_id, fields in messages:
                            if not self.running:
                                return processed_any
                            if self._is_in_flight(message_id):
                                drained_any = True
                                last_pending_id = message_id
                                continue
                            if (
                                self._get_gate_waiter_count()
                                >= concurrency.pending_task_ceiling(self)
                            ):
                                break
                            drained_any = True
                            last_pending_id = message_id
                            try:
                                parsed_message = await self._parse_message(message_id, fields)
                                stable_message_id = self._get_stable_message_id(
                                    message_id, parsed_message
                                )
                                if self.__already_held(message_id):
                                    continue
                                if await self._should_dead_letter(
                                    topic, message_id, stable_message_id, parsed_message
                                ):
                                    continue
                                processed_any = True
                                self.logger.info(
                                    "Recovering own pending message: stream=%s, id=%s",
                                    topic,
                                    message_id,
                                )
                                await self.__dispatch_or_enqueue(
                                    topic, message_id, fields, parsed_message
                                )
                            except Exception as e:
                                self.logger.error(
                                    "Error recovering own pending message %s: %s",
                                    message_id,
                                    e,
                                )

                    if not drained_any:
                        break
                except Exception as e:
                    self.logger.error(
                        "Error draining own PEL on %s: %s",
                        topic,
                        e,
                    )
                    break

            await self._cleanup_empty_consumers(topic)

        if processed_any:
            self.logger.info("Processed pending messages from PEL")
        return processed_any

    def __retry_not_before(self, parsed: StreamMessage) -> float | None:
        not_before = parsed.payload.get("_retry_not_before")
        if not not_before:
            return None
        try:
            return float(not_before)
        except (TypeError, ValueError):
            return None

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
        for _ in range(_PENDING_SCAN_MAX_PAGES):
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
            if not details:
                return False
            last_id = cursor
            for detail in details:
                raw_id = detail.get("message_id", detail.get(b"message_id"))
                message_id = (
                    raw_id.decode() if isinstance(raw_id, bytes) else str(raw_id)
                )
                last_id = message_id
                if not self.__already_held(message_id):
                    return True
            if len(details) < page_size:
                return False
            cursor = f"({last_id}"
        return False

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
        we already own is a no-op otherwise.
        """
        if self.redis is None or not self._held_entries:
            return
        interval = max(1.0, (self.config.claim_min_idle_ms / 1000.0) / 3.0)
        now = time.monotonic()
        if now - self._last_ownership_refresh < interval:
            return
        self._last_ownership_refresh = now

        by_stream: dict[str, list[str]] = {}
        for message_id, stream in self._held_entries.items():
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

    def __already_held(self, message_id: str) -> bool:
        """Whether this consumer is already responsible for an entry.

        Buffered and parked entries stay un-ACKed in the pending list on
        purpose, so the recovery path sees them and would claim them again.
        Every claim increments Redis's ``times_delivered``, which the
        dead-letter backstop reads as a failed attempt -- so re-claiming
        entries that are merely waiting their turn dead-letters healthy
        records, and duplicates the work besides.
        """
        return message_id in self._held_entries or self._is_in_flight(message_id)

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
        if not parked:
            return
        still_full: set[FairnessKey] = set()
        kept: deque[
            tuple[str, str, dict[str, str], StreamMessage, FairnessKey, float]
        ] = deque()
        for entry in parked:
            stream_name, message_id, fields, parsed, key, _parked_at = entry
            if key in still_full:
                kept.append(entry)
                continue
            if self.__try_enqueue(stream_name, message_id, fields, parsed):
                continue
            still_full.add(key)
            kept.append(entry)
        self._deferred_entries = kept

    def __sweep_stale_buffered(self) -> None:
        """Drop entries buffered past the dwell budget.

        A buffered entry is un-ACKed, so Redis counts it as idle and a peer
        replica's XAUTOCLAIM will steal it once it passes claim_min_idle_ms.
        Releasing it here keeps our in-memory view honest -- the PEL entry is
        untouched, so the idle drain or the new owner picks it up.
        """
        scheduler = self._scheduler
        if scheduler is None:
            return
        budget = self.fair_scheduler_config.max_dwell_seconds
        cutoff = time.monotonic() - budget
        dropped = scheduler.purge(lambda item: item[4] < cutoff)
        for _stream, message_id, *_rest in dropped:
            self._held_entries.pop(message_id, None)

        # Parked entries age out on the same budget. A key that stays at its
        # cap would otherwise hold them forever: ownership refresh keeps
        # resetting their idle time, so no peer would claim them either, and
        # they would never reach the dwell metric.
        stale_parked = [e for e in self._deferred_entries if e[5] < cutoff]
        if stale_parked:
            self._deferred_entries = deque(
                e for e in self._deferred_entries if e[5] >= cutoff
            )
            for _stream, message_id, *_rest in stale_parked:
                self._held_entries.pop(message_id, None)

        released = len(dropped) + len(stale_parked)
        if released:
            metrics.record_dwell_exceeded("redis", released)
            self.logger.warning(
                "Released %d entry(ies) held longer than %.0fs back to the "
                "pending list; they will be re-read rather than held past "
                "Redis's idle-claim window",
                released,
                budget,
            )

    async def __enqueue_message(
        self, stream_name: str, message_id: str, fields: dict[str, str]
    ) -> None:
        """Parse a freshly read entry and hand it to the scheduler."""
        parsed = await self._parse_message(message_id, fields)
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

        parsed = parsed_message or await self._parse_message(message_id, fields)
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
            self._held_entries[message_id] = stream_name
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
            self._held_entries[message_id] = stream_name
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
        waiter_count = self._get_gate_waiter_count()
        pending_ceiling = concurrency.pending_task_ceiling(self)
        saturated = concurrency.index_gates_saturated(self)
        scheduler = self._scheduler
        if scheduler is None:
            return
        self.__sweep_stale_buffered()
        await self.__drain_deferred()
        # Parked entries are held in memory too, so they count against the
        # buffer budget -- that budget is what bounds this consumer's memory.
        scheduler_full = (
            scheduler.pending_count + len(self._deferred_entries)
            >= self.fair_scheduler_config.max_buffered_messages
        )

        if waiter_count >= pending_ceiling or saturated or scheduler_full:
            if not self._backpressure_active:
                self.logger.warning(
                    "Backpressure engaged: %d tasks waiting for indexing "
                    "admission (index gates saturated: %s, scheduler buffer "
                    "full: %s)",
                    waiter_count,
                    saturated,
                    scheduler_full,
                )
                self._backpressure_active = True
            await asyncio.sleep(0.5)
            return
        elif self._backpressure_active:
            self.logger.info(
                "Backpressure cleared: %d/%d", waiter_count, pending_ceiling
            )
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
        for stream_name, messages in results:
            for message_id, fields in messages:
                if not self.running:
                    return
                try:
                    await self.__enqueue_message(stream_name, message_id, fields)
                except Exception as e:
                    self.logger.error(
                        "Error enqueuing message %s for fair scheduling: %s",
                        message_id,
                        e,
                    )

    async def __dispatch_phase(self) -> None:
        """Dispatch fairly-scheduled entries while pipeline capacity and
        downstream health allow, then hand control back to the read phase."""
        scheduler = self._scheduler
        if scheduler is None:
            return

        def can_dispatch(
            item: tuple[str, str, dict[str, str], StreamMessage, float],
        ) -> bool:
            _stream_name, message_id, _fields, _parsed, _buffered_at = item
            return not self._is_in_flight(message_id)

        while self.running:
            # Re-checked inside the loop, not just once per iteration in
            # _consume_loop: a dispatch pass can start many records, and a
            # downstream 429 arriving part-way through must stop the rest of
            # them rather than being honoured only on the next poll. Mirrors
            # the Kafka dispatch phase.
            downstream_paused = (
                self.backpressure_coordinator is not None
                and self.backpressure_coordinator.is_paused()
            )
            if (
                self._get_gate_waiter_count() >= concurrency.pending_task_ceiling(self)
                or downstream_paused
                or concurrency.index_gates_saturated(self)
            ):
                break

            dispatched = scheduler.dequeue(can_dispatch=can_dispatch)
            if dispatched is None:
                break

            key, (stream_name, message_id, fields, parsed, _buffered_at) = dispatched
            # Ownership passes to the in-flight set from here.
            self._held_entries.pop(message_id, None)
            metrics.record_dispatch("redis", key[0] if key else "unknown")
            try:
                await self._start_processing_task(
                    stream_name, message_id, fields, parsed
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

                    waiter_count = self._get_gate_waiter_count()
                    pending_ceiling = concurrency.pending_task_ceiling(self)
                    # Saturation matters as much as queue depth: with both
                    # index pools full and nothing queued behind them, the
                    # waiter count reads zero while the node cannot start a
                    # single further record. Claiming more entries then only
                    # grows this consumer's PEL.
                    saturated = concurrency.index_gates_saturated(self)
                    if waiter_count >= pending_ceiling or saturated:
                        if not self._backpressure_active:
                            self.logger.warning(
                                "Backpressure engaged: %d tasks waiting for "
                                "indexing admission (index gates saturated: %s)",
                                waiter_count,
                                saturated,
                            )
                            self._backpressure_active = True
                        await asyncio.sleep(0.5)
                        continue
                    elif self._backpressure_active:
                        self.logger.info(
                            "Backpressure cleared: %d/%d",
                            waiter_count,
                            pending_ceiling,
                        )
                        self._backpressure_active = False

                    streams = dict.fromkeys(self.config.topics, ">")
                    available_capacity = pending_ceiling - waiter_count
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
                            if (
                                self._get_gate_waiter_count()
                                >= concurrency.pending_task_ceiling(self)
                            ):
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
        self, message_id: str, fields: dict[str, str]
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
            return StreamMessage(**raw)
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
    ) -> None:
        if not self.worker_loop:
            self.logger.error("Worker loop not initialized, cannot process message")
            return
        if not self.running:
            return

        self._mark_in_flight(message_id)
        waiter_token = concurrency.GateWaiterToken(self)
        processing_coro = self._process_message_wrapper(
            stream_name,
            message_id,
            dict(fields),
            waiter_token,
            parsed_message,
        )
        try:
            future = asyncio.run_coroutine_threadsafe(
                processing_coro,
                self.worker_loop,
            )
        except BaseException:
            processing_coro.close()
            self._unmark_in_flight(message_id)
            waiter_token.release()
            raise
        with self._futures_lock:
            self._active_futures.add(future)

        def on_future_done(f: Future[bool]) -> None:
            self._unmark_in_flight(message_id)
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
                self.logger.error("Task completed with unhandled exception: %s", exc)

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

    async def _requeue_message(
        self,
        stream_name: str,
        message: StreamMessage,
        stable_message_id: str,
        retry_count: int = 1,
    ) -> None:
        """Re-publish a failed message to the same stream for retry.
        
        The message goes to the end of the queue. Stamps an exponential-backoff
        "not before" timestamp (see __delay_if_retry_not_ready) so a downed
        downstream service gets time to recover instead of the message being
        immediately re-picked-up and re-failed in a tight loop. The original
        message is acknowledged.
        
        Preserves the stable message ID in the payload for retry tracking.
        
        Args:
            stream_name: Stream to re-queue to
            message: The message to re-queue
            stable_message_id: Stable ID for retry tracking (preserved across re-queues)
            retry_count: Number of prior failures, used to compute backoff delay
        """
        if not self.producer:
            raise RuntimeError("No producer available for re-queue")

        try:
            payload = dict(message.payload)
            payload["_retry_tracking_id"] = stable_message_id
            payload["_retry_not_before"] = time.time() + compute_retry_backoff_seconds(retry_count)

            await self._run_on_main_loop(
                self.producer.send_event(
                    topic=stream_name,
                    event_type=message.eventType,
                    payload=payload,
                )
            )
        except Exception as e:
            self.logger.error(f"Failed to re-queue message to {stream_name}: {e}")
            raise

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
        parsing_held = False
        indexing_held = False
        indexing_complete = False
        shutting_down = False
        acked = False
        parse_lease_pool = "parsing"
        index_lease_pool = "indexing"
        parsing_admission: concurrency.Admission | None = None
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
            parsed_message = await self._parse_message(message_id, fields)
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
        record_lock_id = (
            parsed_message.payload.get("recordId") or stable_message_id
        )
        record_pool = f"record:{record_lock_id}"

        if not await self._delay_if_retry_not_ready(parsed_message, message_id):
            return False

        # Verify PEL ownership unconditionally (only needs self.redis, not the
        # distributed concurrency manager) — an XAUTOCLAIMed message can still
        # be concurrently processed by whichever consumer held it before if we
        # only check this when distributed concurrency is enabled.
        if not await self._pending_message_is_owned(stream_name, message_id):
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
        index_tier = concurrency.effective_index_tier(
            self,
            classify(
                str(parsed_message.payload.get("extension") or ""),
                str(parsed_message.payload.get("mimeType") or ""),
            ),
        )
        index_lease_pool = concurrency.index_lease_pool(index_tier)

        try:
            # The active-pipeline bound. Without this outer permit, parsed
            # records can accumulate while waiting for an indexing permit and
            # every one can remain IN_PROGRESS in the DB.
            index_admission = await concurrency.acquire_index_slot(self, index_tier)
            indexing_held = True
            if waiter_token is not None:
                waiter_token.admit()

            if self.concurrency_manager is not None:
                # Taken only once the local permit is held: stale-record
                # recovery reads this lease as proof of active processing, so
                # a task still queued on the gate must not own one.
                if not await self._acquire_distributed_slot(
                    index_lease_pool,
                    lease_owner,
                    concurrency.index_ceiling(self, index_tier),
                    leases=distributed_leases,
                ):
                    return False
                lease_handle, renewal_task = concurrency.start_lease_guard(
                    self, lease_owner
                )

                if not await self._acquire_distributed_slot(
                    record_pool,
                    lease_owner,
                    1,
                    deadline_seconds=messaging_env.record_lease_wait_seconds,
                    leases=distributed_leases,
                ):
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

            # Check current retry count to predict if this will be the final attempt on failure
            current_retry_count = await self._get_retry_count(stable_message_id)

            will_be_final_on_failure = (
                not self.retry_manager or
                current_retry_count >= messaging_env.max_delivery_attempts - 1
            )

            parsed_message.is_final_failure = will_be_final_on_failure

            if self.message_handler:
                ctx = context_from_envelope({"requestId": parsed_message.requestId})
                token = set_context(ctx.root_id)

                async def consume_handler_events() -> None:
                    nonlocal parsing_held, indexing_held, indexing_complete, shutting_down, parsing_admission, parse_lease_pool
                    async with asyncio.timeout(messaging_env.record_processing_timeout):
                        event_gen = self.message_handler(parsed_message)
                        try:
                            async for event in event_gen:
                                if (
                                    event.event == IndexingEvent.START_PARSING
                                    and not parsing_held
                                ):
                                    parse_tier = event.data.tier if event.data else None
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
                                    parsing_admission = await concurrency.acquire_parsing_slot(
                                        self,
                                        parse_tier,
                                        event.data.size_bytes if event.data else None,
                                    )
                                    # Set before the lease attempt so the
                                    # wrapper's finally hands this permit back
                                    # on every exit path below.
                                    parsing_held = True
                                    if (
                                        self.concurrency_manager is not None
                                        and not await self._acquire_distributed_slot(
                                            parse_lease_pool,
                                            lease_owner,
                                            concurrency.parse_ceiling(self, parse_tier),
                                            leases=distributed_leases,
                                        )
                                    ):
                                            # A capacity lease only gives up
                                            # when self.running flips (it fails
                                            # open on error), so this is a
                                            # clean shutdown — abort without
                                            # raising rather than burning a
                                            # retry attempt.
                                            shutting_down = True
                                            return
                                elif (
                                    event.event == IndexingEvent.PARSING_COMPLETE
                                    and parsing_held
                                ):
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
                                    indexing_held = False
                                    indexing_complete = True
                        finally:
                            # If this coroutine is cancelled (timeout, or the
                            # renewal-loss path cancelling handler_task below)
                            # while suspended on the semaphore acquire, the
                            # CancelledError lands here — not inside the
                            # handler generator. Explicitly closing it
                            # delivers GeneratorExit so the handler's own
                            # cleanup (reverting IN_PROGRESS) still runs.
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
                        messaging_env.record_processing_timeout,
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
                failure_count, should_dead_letter = (
                    await self._increment_retry_and_check(stable_message_id)
                )
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
            if parsing_held:
                if distributed_leases.discard(parse_lease_pool) is not None:
                    await self._release_distributed_slot(parse_lease_pool, lease_owner)
                concurrency.release_admission(parsing_admission)
                parsing_admission = None
            if indexing_held:
                if distributed_leases.discard(index_lease_pool) is not None:
                    await self._release_distributed_slot(index_lease_pool, lease_owner)
                concurrency.release_admission(index_admission)

            for pool, owner in distributed_leases.snapshot():
                distributed_leases.discard(pool)
                await self._release_distributed_slot(pool, owner)
