import asyncio
import json
import ssl
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

from app.services.messaging import consumer_concurrency as concurrency
from app.services.messaging.config import (
    IndexingEvent,
    IndexingMessageHandler,
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
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.lease import LeaseRenewer
from app.services.messaging.scheduling.drr_scheduler import DRRScheduler
from app.services.messaging.scheduling.interface import (
    EnqueueResult,
    FairnessKey,
    FairnessKeyExtractor,
    FairSchedulerConfig,
    WeightProvider,
)
from app.services.messaging.scheduling.key_extractors import CompositeKeyExtractor
from app.services.messaging.scheduling.offset_tracker import PartitionOffsetTracker
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
    from app.services.messaging.interface.producer import IMessagingProducer
    from app.services.messaging.retry_manager import RetryManager
    from app.services.resource_governor import ResourceGovernor

FUTURE_CLEANUP_INTERVAL = 100  # Cleanup completed futures every N messages
_MAIN_LOOP_OP_TIMEOUT = 5.0
# How often the retry-backoff wait re-checks self.running, so a shutdown
# request can interrupt a long (up to 300s) wait instead of holding an
# active-future slot — and blocking graceful shutdown — for the full delay.
_DELAY_POLL_INTERVAL_SECONDS = 1.0
# How often the consume loop checks for offsets held past the dwell budget.
# Cheap (a scan of the outstanding sets) but pointless every iteration.
_DWELL_SWEEP_INTERVAL_SECONDS = 30.0
# Poll timeout used while the scheduler still has buffered work to dispatch.
# Short on purpose: the loop has something to do, so waiting for new messages
# is pure added latency on everything already read.
_BUSY_POLL_TIMEOUT_MS = 50
# Sentinel CompositeKeyExtractor uses for an absent fairness field.
_DEFAULT_KEY_LEVEL = "__default__"

# Re-exported for backwards compatibility with existing call sites/tests in
# this module; canonical definition lives in app.services.messaging.config
# so the Redis Streams consumer can share the same backoff schedule.
_compute_retry_backoff_seconds = compute_retry_backoff_seconds



def _loads_possibly_double_encoded(value: str) -> object:
    """Decode an envelope that producers sometimes JSON-encode twice."""
    parsed = json.loads(value)
    if isinstance(parsed, str):
        parsed = json.loads(parsed)
    return parsed


class _InFlightOffset:
    """One buffered offset's outstanding claim on the commit watermark.

    ``__process_message_wrapper`` has several paths that deliberately return
    without committing (shutdown, a contended record lease). Committing
    ``offset + 1`` per message let a later message cover those; a watermark
    does not, so an offset nobody resolves stalls every later commit on its
    partition. Each path therefore states which end it reached, and the
    future's done-callback resolves anything that forgot as ``redeliver`` --
    a stalled watermark is never the silent default.
    """

    __slots__ = ("tp", "offset", "resolved")

    def __init__(self, tp: "TopicPartition", offset: int) -> None:
        self.tp = tp
        self.offset = offset
        self.resolved = False


class _ReadOutcome:
    """Result of enqueuing one read message (see ``__enqueue_message``)."""

    BUFFERED = "buffered"
    RESOLVED = "resolved"       # terminal inline, e.g. an unparseable message
    PARKED = "parked"           # key is capped; held in memory, keep reading
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
    ) -> None:
        self.logger = logger
        self.consumer: AIOKafkaConsumer | None = None
        self.running = False
        self.kafka_config = kafka_config
        self.consume_task = None
        self.retry_manager = retry_manager
        # Told about every message this consumer gives up on, before the commit
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
        # app.services.messaging.backpressure. __apply_backpressure() also
        # pauses partitions whenever any of them last saw a 429+Retry-After,
        # instead of pulling more work a saturated downstream would just
        # reject again.
        self.backpressure_coordinator = backpressure_coordinator
        self._consumer_instance_id = uuid.uuid4().hex
        self._distributed_log_times: dict[str, float] = {}
        # Worker thread infrastructure
        self.worker_executor: ThreadPoolExecutor | None = None
        self.worker_loop: asyncio.AbstractEventLoop | None = None
        self.worker_loop_ready = threading.Event()  # Signal when worker loop is ready
        self.main_loop: asyncio.AbstractEventLoop | None = None
        # Nested active-pipeline and parsing gates (created in worker thread).
        # Legacy fallback only: unused (stay None) once a governor is set.
        self.parsing_semaphore: asyncio.Semaphore | None = None
        self.indexing_semaphore: Any = None
        # One renewer for every lease this process holds, started on the
        # worker loop beside the records it guards (see lease.LeaseRenewer).
        self.lease_renewer: LeaseRenewer | None = None
        self.message_handler: IndexingMessageHandler | None = None
        # Track active futures for proper cleanup
        self._active_futures: set[Future[bool]] = set()
        self._futures_lock = threading.Lock()
        self._gate_waiters = 0
        self._backpressure_logged = False
        self._partition_lock = threading.Lock()
        self._in_flight_partitions: set[TopicPartition] = set()
        self._deferred_partition_offsets: dict[TopicPartition, int] = {}

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
        # Messages read but with no buffer room for their key yet. Held in
        # memory rather than seeked back: their offsets stay tracked, so the
        # watermark still floors at them, and re-reading them would only
        # re-hit the same cap. Counted against the buffer budget.
        # Partitions that returned records on the most recent poll; see
        # __other_lane_is_readable.
        self._partitions_with_data: set[TopicPartition] = set()
        self._deferred_messages: deque[
            tuple[TopicPartition, ConsumerRecord, StreamMessage, FairnessKey]
        ] = deque()
        if self.fair_scheduler_config.enabled:
            self._scheduler = DRRScheduler(self.fair_scheduler_config, self.weight_provider)
            self._offset_tracker = PartitionOffsetTracker(logger=logger)

    @staticmethod
    def kafka_config_to_dict(kafka_config: KafkaConsumerConfig) -> dict[str, Any]:
        """Convert KafkaConsumerConfig dataclass to dictionary format for aiokafka consumer"""
        config: dict[str, Any] = {
            'bootstrap_servers': ",".join(kafka_config.bootstrap_servers),
            'group_id': kafka_config.group_id,
            'auto_offset_reset': kafka_config.auto_offset_reset,
            'enable_auto_commit': kafka_config.enable_auto_commit,
            'client_id': kafka_config.client_id,
            'topics': kafka_config.topics,
            'session_timeout_ms': kafka_config.session_timeout_ms,
            'heartbeat_interval_ms': kafka_config.heartbeat_interval_ms,
            'max_poll_interval_ms': kafka_config.max_poll_interval_ms,
            'rebalance_timeout_ms': kafka_config.rebalance_timeout_ms,
        }

        # Add SSL/SASL configuration for AWS MSK
        if kafka_config.ssl:
            config["ssl_context"] = ssl.create_default_context()
            sasl_config = kafka_config.sasl or {}
            if sasl_config.get("username"):
                config["security_protocol"] = "SASL_SSL"
                config["sasl_mechanism"] = sasl_config.get("mechanism", "SCRAM-SHA-512").upper()
                config["sasl_plain_username"] = sasl_config["username"]
                config["sasl_plain_password"] = sasl_config["password"]
            else:
                config["security_protocol"] = "SSL"

        return config

    def __start_worker_thread(self) -> None:
        """Start the worker thread with its own event loop"""
        def run_worker_loop() -> None:
            """Run the event loop in the worker thread"""
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
                # Legacy static semaphores, created in the worker thread's event loop.
                self.parsing_semaphore = asyncio.Semaphore(messaging_env.max_concurrent_parsing)
                self.indexing_semaphore = asyncio.Semaphore(messaging_env.max_concurrent_indexing)
                self.logger.info("Worker thread event loop started with semaphores initialized")

            # Signal that the worker loop is ready
            if self.concurrency_manager is not None:
                self.lease_renewer = LeaseRenewer(
                    self.logger,
                    self.concurrency_manager,
                    lease_seconds=messaging_env.concurrency_lease_seconds,
                    interval_seconds=messaging_env.concurrency_renew_interval_seconds,
                )
                self.worker_loop.call_soon(self.lease_renewer.start)
            self.worker_loop_ready.set()

            # Run the event loop until stopped
            try:
                self.worker_loop.run_forever()
            finally:
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

        if self.worker_loop and self.worker_loop.is_running():
            # Stop the event loop (the finally block in run_worker_loop will handle cleanup)
            self.worker_loop.call_soon_threadsafe(self.worker_loop.stop)
            self.logger.info("Worker thread event loop stop requested")

        # Shutdown the executor and wait for thread to finish
        if self.worker_executor:
            self.worker_executor.shutdown(wait=True)
            self.logger.info("Worker thread executor shut down")
            self.worker_executor = None
            self.worker_loop = None

        # Clear tracking state
        with self._futures_lock:
            self._active_futures.clear()

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

    def _get_gate_waiter_count(self) -> int:
        return concurrency.get_gate_waiter_count(self)

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

            if not self.consumer:
                await self.initialize()

            self.consume_task = asyncio.create_task(self.__consume_loop())
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
        self.logger.info("🛑 Stopping Kafka consumer...")
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
                self.logger.info("✅ Kafka consumer stopped")
            except Exception as e:
                self.logger.error(f"Error stopping Kafka consumer: {e}")
        self._lane_paused.clear()
        self._deferred_messages.clear()
        with self._partition_lock:
            self._in_flight_partitions.clear()
            self._deferred_partition_offsets.clear()
            self._in_flight_records.clear()

        if self._scheduler is not None:
            drained = self._scheduler.drain_all()
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

        purged = self._scheduler.purge(lambda item: item[0] in revoked_set)
        for tp in revoked_set:
            self._lane_paused.pop(tp, None)
        self._deferred_messages = deque(
            entry for entry in self._deferred_messages if entry[0] not in revoked_set
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
        from being returned when at capacity OR when any downstream service
        (parsing/docling/embedding) last signalled 429+Retry-After via
        ``backpressure_coordinator`` — pulling more work in that case would
        just queue up behind the same saturated service.
        """
        waiter_count = self._get_gate_waiter_count()
        pending_ceiling = concurrency.pending_task_ceiling(self)
        downstream_paused = (
            self.backpressure_coordinator is not None
            and self.backpressure_coordinator.is_paused()
        )

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
        if (
            waiter_count >= pending_ceiling
            or downstream_paused
            or saturated
            or scheduler_full
        ):
            # Pause partitions that aren't already paused
            assigned = self.consumer.assignment()
            not_paused = assigned - self.consumer.paused()
            if not_paused:
                self.consumer.pause(*not_paused)
            if not self._backpressure_logged:
                if downstream_paused:
                    self.logger.warning(
                        "Downstream backpressure from %s: pausing Kafka "
                        "partition reads for %.1fs",
                        ", ".join(sorted(self.backpressure_coordinator.paused_services)),
                        self.backpressure_coordinator.pause_remaining(),
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
                        + len(self._deferred_messages),
                        self.fair_scheduler_config.max_buffered_messages,
                    )
                else:
                    self.logger.warning(
                        f"Backpressure engaged: {waiter_count} tasks waiting for "
                        f"indexing admission; pausing Kafka partition reads at cap {pending_ceiling}"
                    )
                self._backpressure_logged = True
        else:
            # A partition remains paused while one of its messages is in flight;
            # this preserves Kafka's per-partition processing/commit order.
            paused = self.consumer.paused()
            with self._partition_lock:
                in_flight_partitions = set(self._in_flight_partitions)
            # Lane pauses outlive a global backpressure clear: the buffer as a
            # whole having room says nothing about whether the key that
            # blocked this particular lane has drained.
            resumable = paused - in_flight_partitions - set(self._lane_paused)
            if resumable:
                self.consumer.resume(*resumable)
            if self._backpressure_logged:
                self.logger.info(
                    f"Backpressure cleared: waiters back to {waiter_count}/{pending_ceiling}"
                )
                self._backpressure_logged = False

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
        return str(
            parsed.payload.get("recordId")
            or self._get_stable_message_id(message, parsed)
        )

    def __reserve_record(self, record_key: str) -> bool:
        """Claim a record for dispatch, so no second event for the same
        record runs beside it.

        This replaces -- rather than merely relaxes -- the per-partition
        rule. ``record_lease_wait_seconds`` documents that the cluster-wide
        ``record:`` lease is "only contended by duplicate in-flight
        deliveries of the same record", and the loser of that contention is
        dropped as already-handled. That assumption holds today *because* a
        partition only ever has one message in flight. Letting a partition
        run several at once without this check would put two genuinely
        different events for one record (a create and its update) into that
        contention, and silently discard one.
        """
        with self._partition_lock:
            if record_key in self._in_flight_records:
                return False
            self._in_flight_records.add(record_key)
        return True

    def __reserve_partition(self, message: ConsumerRecord) -> bool:
        # Only one message per partition is ever in flight at a time (Kafka
        # ordering), so real concurrency is capped by min(MAX_CONCURRENT_*,
        # partition count) — raising the semaphore limits without also
        # increasing the topic's partition count won't raise throughput.
        topic_partition = TopicPartition(message.topic, message.partition)
        with self._partition_lock:
            if topic_partition in self._in_flight_partitions:
                current = self._deferred_partition_offsets.get(topic_partition)
                self._deferred_partition_offsets[topic_partition] = (
                    message.offset
                    if current is None
                    else min(current, message.offset)
                )
                return False
            self._in_flight_partitions.add(topic_partition)

        self.consumer.pause(topic_partition)
        return True

    def __finish_partition(
        self,
        message: ConsumerRecord,
        retry_current: bool,
        record_key: str | None = None,
    ) -> None:
        if record_key is not None:
            # Parallel dispatch: the partition was never reserved or paused
            # for this message, so there is nothing to resume. Lane pauses and
            # global backpressure own the read side.
            with self._partition_lock:
                self._in_flight_records.discard(record_key)
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
        downstream_paused = (
            self.backpressure_coordinator is not None
            and self.backpressure_coordinator.is_paused()
        )
        if (
            self.running
            and not downstream_paused
            and topic_partition not in self._lane_paused
            and self._get_gate_waiter_count()
            < concurrency.pending_task_ceiling(self)
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

                    available_capacity = max(
                        1,
                        concurrency.pending_task_ceiling(self)
                        - self._get_gate_waiter_count(),
                    )

                    message_batch = await self.consumer.getmany(
                        timeout_ms=messaging_env.message_timeout_ms,
                        max_records=min(
                            max(1, messaging_env.message_batch_size_indexing),
                            available_capacity,
                        ),
                    )  # type: ignore

                    if not message_batch:
                        continue

                    for messages in message_batch.values():
                        for message in messages:
                            # Check if we should stop before processing
                            if not self.running:
                                self.logger.info("Consumer stopping, skipping remaining messages in batch")
                                break

                            try:
                                self.logger.debug(f"Received message: topic={message.topic}, partition={message.partition}, offset={message.offset}")
                                deferred, parsed_message = (
                                    await self.__defer_if_retry_not_ready(message)
                                )
                                if deferred:
                                    # Not ready: seeked back already. Kafka's
                                    # per-partition ordering means we can't
                                    # skip ahead to later messages in this
                                    # partition anyway, so stop draining this
                                    # partition's batch and let the next
                                    # getmany() poll re-check it — without
                                    # pausing the partition or spending a
                                    # worker-thread/active-task slot on a
                                    # multi-minute sleep in the meantime.
                                    break
                                if not self.__reserve_partition(message):
                                    continue
                                await self.__start_processing_task(
                                    message, parsed_message
                                )
                            except Exception as e:
                                self.__finish_partition(
                                    message,
                                    retry_current=True,
                                )
                                self.logger.error(f"Error processing individual message: {e}")
                                continue

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
            self.logger.info(f"🛑 Consume loop exited. Active tasks remaining: {active_count}")

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
        self.__drain_deferred()
        buffer_room = max(
            0,
            self.fair_scheduler_config.max_buffered_messages
            - scheduler.pending_count
            - len(self._deferred_messages),
        )
        # getmany() is called on every iteration even with no buffer room --
        # it is what resets max_poll_interval_ms, and skipping it would have
        # the group evict this consumer while it drains a full buffer.
        # __apply_backpressure has paused every partition in that case, so
        # the call returns nothing; the floor of 1 only matters if a
        # partition slips through, and that message is seeked back.
        # Do not block for new messages while the buffer already holds work.
        # Read and dispatch alternate, so a full-length poll here delays the
        # dispatch of everything already buffered by the whole timeout --
        # which caps throughput at (partitions / timeout) records per second
        # no matter how much is waiting.
        poll_timeout_ms = (
            _BUSY_POLL_TIMEOUT_MS
            if scheduler.pending_count
            else messaging_env.message_timeout_ms
        )
        message_batch = await self.consumer.getmany(
            timeout_ms=poll_timeout_ms,
            max_records=max(
                1,
                min(
                    max(1, messaging_env.message_batch_size_indexing),
                    buffer_room,
                ),
            ),
        )  # type: ignore
        self._partitions_with_data = {
            tp for tp, messages in (message_batch or {}).items() if messages
        }
        if not message_batch:
            return

        for tp, messages in message_batch.items():
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
                try:
                    outcome, blocked_key = await self.__enqueue_message(tp, message)
                except Exception as e:
                    # The offset is already tracked, so leaving it unresolved
                    # would pin the watermark; hand it back for redelivery.
                    self.logger.error(
                        f"Error enqueuing message {tp}-{message.offset} for "
                        f"fair scheduling: {e}"
                    )
                    await self.__resolve_offset(
                        _InFlightOffset(tp, message.offset),
                        done=False,
                        awaiting_reread=True,
                    )
                    self.__seek_back(tp, message.offset)
                    break
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
        held = scheduler.pending_count + len(self._deferred_messages)
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
        if not parked or self._scheduler is None:
            return
        still_full: set[FairnessKey] = set()
        kept: deque[
            tuple[TopicPartition, ConsumerRecord, StreamMessage, FairnessKey]
        ] = deque()
        for entry in parked:
            tp, message, parsed, key = entry
            if key in still_full:
                kept.append(entry)
                continue
            result = self._scheduler.enqueue(
                key, (tp, message, parsed), not_before=self.__retry_not_before(parsed)
            )
            if result == EnqueueResult.ACCEPTED:
                continue
            still_full.add(key)
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
        for tp, key in list(self._lane_paused.items()):
            if not buffer_has_room or scheduler.pending_count_for(key) >= cap:
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
        work."""
        if self.consumer is None:
            return
        try:
            self.consumer.seek(tp, offset)
        except Exception as e:
            self.logger.error(f"Failed to seek {tp} back to {offset}: {e}")

    async def __enqueue_message(
        self, tp: TopicPartition, message: ConsumerRecord
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

        parsed = await self.__parse_message(message)
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
        result = scheduler.enqueue(key, (tp, message, parsed), not_before=not_before)
        if result == EnqueueResult.ACCEPTED:
            return _ReadOutcome.BUFFERED, None

        # BUFFER_FULL or ENTITY_FULL: no room. The offset stays tracked and
        # unresolved on purpose -- it floors the watermark until the seek-back
        # re-reads it, which is exactly the guarantee that makes it safe to
        # drop the message here.
        metrics.record_deferred("kafka", result.value)
        if result == EnqueueResult.ENTITY_FULL and self.__buffer_has_room():
            # This key is capped but the buffer as a whole is not. Park the
            # message and keep reading: reading is the only way to reach a key
            # that is *not* backed up, and stopping here caps read-ahead at
            # one key's share of the buffer. On a single-lane topic that means
            # a large backlog at the head is never read past and every key
            # behind it starves -- the problem fair scheduling exists to fix.
            self._deferred_messages.append((tp, message, parsed, key))
            return _ReadOutcome.PARKED, key
        return _ReadOutcome.STOP_PARTITION, key

    def __retry_not_before(self, parsed: StreamMessage) -> float | None:
        not_before = parsed.payload.get("_retry_not_before")
        if not not_before:
            return None
        try:
            return float(not_before)
        except (TypeError, ValueError):
            return None

    async def __resolve_offset(
        self,
        in_flight: "_InFlightOffset | None",
        *,
        done: bool,
        awaiting_reread: bool = False,
    ) -> None:
        """Settle a buffered offset's claim on the commit watermark, exactly
        once, and commit if that let the watermark advance.

        ``done=True`` means this delivery is finished with (processed,
        re-queued, dead-lettered, or superseded by a duplicate that holds the
        record lease) and the watermark may pass it. ``done=False`` means it
        was not processed and must be redelivered, so it becomes a floor the
        watermark stops at until the offset is read again.

        Bridged onto the main loop as one unit (mirroring ``_commit_offset``)
        so the offset tracker, like the scheduler, is only ever touched from
        one thread -- safe to call from the main loop (read/dispatch phase)
        or the worker loop (processing wrapper).
        """
        if in_flight is None or in_flight.resolved:
            return
        in_flight.resolved = True

        offset_tracker = self._offset_tracker
        if offset_tracker is None or self.consumer is None:
            return
        tp, offset = in_flight.tp, in_flight.offset

        async def do_resolve() -> None:
            if self.consumer is None:
                return
            watermark = (
                offset_tracker.mark_done(tp, offset)
                if done
                else offset_tracker.mark_redeliver(
                    tp, offset, awaiting_reread=awaiting_reread
                )
            )
            if watermark is not None:
                await self.consumer.commit({tp: watermark})  # type: ignore

        await self._run_on_main_loop(do_resolve())

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

        parallel = self.__parallel_dispatch()

        def can_dispatch(
            item: tuple[TopicPartition, ConsumerRecord, StreamMessage],
        ) -> bool:
            tp, message, parsed = item
            with self._partition_lock:
                if parallel:
                    return (
                        self.__record_key(message, parsed)
                        not in self._in_flight_records
                    )
                return tp not in self._in_flight_partitions

        while self.running:
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

            key, (tp, message, parsed) = dispatched
            metrics.record_dispatch("kafka", key[0] if key else "unknown")
            in_flight = _InFlightOffset(tp, message.offset)
            record_key = self.__record_key(message, parsed) if parallel else None
            reserved = (
                self.__reserve_record(record_key)
                if record_key is not None
                else self.__reserve_partition(message)
            )
            if not reserved:
                # can_dispatch above already checked this partition wasn't in
                # flight, and nothing awaits between that check and this
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
                    message, parsed, in_flight, record_key
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
        out.

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
        if parsed is None:
            return False, None
        not_before = parsed.payload.get("_retry_not_before")
        if not not_before:
            return False, parsed
        try:
            remaining = float(not_before) - time.time()
        except (TypeError, ValueError):
            return False, parsed
        if remaining <= 0:
            return False, parsed

        self.consumer.seek(
            TopicPartition(message.topic, message.partition), message.offset
        )
        return True, parsed

    async def __parse_message(self, message: ConsumerRecord) -> StreamMessage | None:
        """Parse the Kafka message value into a StreamMessage.

        Handles bytes decoding, JSON parsing, and double-encoded JSON.

        Returns:
            StreamMessage or None if parsing fails.
        """
        message_id = f"{message.topic}-{message.partition}-{message.offset}"
        message_value = message.value

        try:
            if isinstance(message_value, bytes):
                message_value = message_value.decode("utf-8")
                self.logger.debug(f"Decoded bytes message for {message_id}")

            if isinstance(message_value, str):
                try:
                    # Offloaded above a size threshold: a connector can emit a
                    # record whose whole body rides in the envelope, and
                    # parsing it inline blocks every other in-flight record on
                    # this one worker loop.
                    parsed = await offload_if_large(
                        _loads_possibly_double_encoded, message_value
                    )
                    self.logger.debug(
                        f"Parsed message {message_id}: type={type(parsed)}"
                    )
                    return StreamMessage(**parsed)
                except json.JSONDecodeError as e:
                    self.logger.error(
                        f"JSON parsing failed for message {message_id}: {str(e)}\n"
                        f"Raw message: {message_value[:1000]}..."
                    )
                    return None
            else:
                self.logger.error(
                    f"Unexpected message value type for {message_id}: {type(message_value)}"
                )
                return None

        except UnicodeDecodeError as e:
            self.logger.error(
                f"Failed to decode message {message_id}: {str(e)}\n"
                f"Raw bytes: {str(message_value)[:100]}..."
            )
            return None

    async def __start_processing_task(
        self,
        message: ConsumerRecord,
        parsed_message: "StreamMessage | None" = None,
        in_flight: "_InFlightOffset | None" = None,
        record_key: str | None = None,
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
        waiter_token = concurrency.GateWaiterToken(self)
        processing_coro = self.__process_message_wrapper(
            message, waiter_token, parsed_message, in_flight
        )
        try:
            future = asyncio.run_coroutine_threadsafe(
                processing_coro,
                self.worker_loop,
            )
        except BaseException:
            processing_coro.close()
            waiter_token.release()
            raise

        # Track the future for cleanup during shutdown
        with self._futures_lock:
            self._active_futures.add(future)

        # Add callback to remove future from tracking when done
        def on_future_done(f: Future[bool]) -> None:
            waiter_token.release()
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
                self.logger.error(f"Task completed with unhandled exception: {exc}")
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

    async def _requeue_message(
        self, topic: str, message: StreamMessage, stable_message_id: str, retry_count: int = 1
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
        """
        if not self.producer:
            raise RuntimeError("No producer available for re-queue")

        try:
            payload = dict(message.payload)
            payload["_retry_tracking_id"] = stable_message_id
            backoff_seconds = _compute_retry_backoff_seconds(retry_count)
            payload["_retry_not_before"] = time.time() + backoff_seconds

            await self._run_on_main_loop(
                self.producer.send_event(
                    topic=topic,
                    event_type=message.eventType,
                    payload=payload,
                )
            )
            self.logger.debug(
                f"Re-queued {stable_message_id} with {backoff_seconds:.0f}s backoff (attempt {retry_count})"
            )
        except Exception as e:
            self.logger.error(f"Failed to re-queue message to {topic}: {e}")
            raise

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
        self.logger.warning(
            "Dead-lettered %s (%s, tracking id %s): %s",
            message_id,
            describe_message(parsed_message),
            tracking_id,
            reason,
        )
        await self._clear_retry_tracking(tracking_id)

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

        if success:
            self.logger.info(f"Message {message_id} processed successfully")
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
            else:
                # RE-QUEUE: Publish back to same topic for retry
                try:
                    await self._requeue_message(message.topic, parsed_message, stable_message_id, retry_count=count)
                    self.logger.info(
                        f"Re-queued {message_id} (tracking ID: {stable_message_id}) for retry (attempt {count}/"
                        f"{messaging_env.max_delivery_attempts})"
                    )
                except Exception as e:
                    self.logger.error(f"Failed to re-queue {message_id}: {e}")
                    raise
        else:
            self.logger.warning(
                f"Message {message_id} failed, no retry manager or unparseable, committing"
            )

        # ALWAYS commit - message is either done, dead-lettered, or re-queued
        try:
            if self._offset_tracker is not None:
                await self.__resolve_offset(
                    in_flight
                    or _InFlightOffset(
                        TopicPartition(message.topic, message.partition),
                        message.offset,
                    ),
                    done=True,
                )
            else:
                await self._commit_offset(message)
            self.logger.info(f"Committed offset for {message_id}")
        except Exception as e:
            self.logger.error(f"Failed to commit offset for {message_id}: {e}")
            raise

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

        parsing_held = False
        indexing_held = False
        shutting_down = False
        parse_lease_pool = "parsing"
        index_lease_pool = "indexing"
        parsing_admission: concurrency.Admission | None = None
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

        # Parse (and, for re-queued messages, wait out any backoff) before
        # acquiring the parsing semaphore. This way a retry waiting for a
        # downed service to recover only occupies a pending-task slot
        # (counted against backpressure), never a parsing/indexing semaphore
        # slot — the exact resource a sibling record needs to make progress.
        # Reuses the parse __defer_if_retry_not_ready already did; only a
        # caller that skipped that check (tests, direct invocation) parses here.
        if parsed_message is None:
            parsed_message = await self.__parse_message(message)
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
        record_lock_id = (
            parsed_message.payload.get("recordId") or stable_message_id
        )
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
                    # Capacity leases only give up when self.running flips,
                    # so this is a clean shutdown: redeliver, do not commit.
                    await self.__resolve_offset(in_flight, done=False)
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
                            f"Record lease contended for {message_id}; another "
                            "in-flight duplicate delivery already owns it, so "
                            "this delivery is finished with"
                        )
                    # Resolved as done, not redelivered: the duplicate holding
                    # the lease is processing this record, and if it fails it
                    # re-queues under the same tracking id. Leaving it
                    # unresolved would pin the commit watermark for the whole
                    # partition -- the pre-watermark code relied on a later
                    # message's offset+1 commit covering this one.
                    await self.__resolve_offset(in_flight, done=True)
                    return False

            parsed_message.payload["_processing_started_at"] = int(time.time() * 1000)

            # Check current retry count to predict if this will be the final attempt on failure
            current_retry_count = await self._get_retry_count(stable_message_id)

            will_be_final_on_failure = (
                not self.retry_manager
                or current_retry_count >= messaging_env.max_delivery_attempts - 1
            )

            # Set flag on message so handler knows whether to update DB status on failure
            parsed_message.is_final_failure = will_be_final_on_failure

            success = False
            if self.message_handler:
                # Carry the producer's trace id into indexing logs.
                ctx = context_from_envelope({"requestId": parsed_message.requestId})
                token = set_context(ctx.root_id)

                async def consume_handler_events() -> None:
                    nonlocal parsing_held, indexing_held, success, shutting_down, parsing_admission, parse_lease_pool
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
                                    self.logger.debug(
                                        f"Acquired parsing slot for {message_id}"
                                    )
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
                                    indexing_held = False
                                    self.logger.debug(
                                        f"Released indexing gate for {message_id}"
                                    )
                                    success = True
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
                        f"Record processing timed out after {messaging_env.record_processing_timeout}s "
                        f"for {message_id}"
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
            else:
                self.logger.error(f"No message handler available for {message_id}")
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
                self.logger.info(
                    f"Consumer stopping, abandoning {message_id} without commit"
                )
                await self.__resolve_offset(in_flight, done=False)
                return False

            # Commit based on success
            await self.__commit_if_appropriate(
                message, parsed_message, success=success, in_flight=in_flight
            )
            return success

        except asyncio.CancelledError:
            # A BaseException, so the recovery below cannot see it: no failure
            # is counted, nothing is re-queued and nothing is committed. That is
            # the correct outcome — the offset resolves as redeliver — but it
            # has to be visible, and it has to name the record, because the
            # handler has already written a status on the way out.
            self.logger.warning(
                "Processing of %s (%s) was cancelled; leaving the offset "
                "uncommitted for redelivery",
                message_id,
                describe_message(parsed_message),
            )
            raise
        except Exception as e:
            # Log the full exception chain for debugging
            exception_chain = format_exception_chain(e)
            self.logger.error(
                f"Error in process_message_wrapper for {message_id}:\n{exception_chain}"
            )
            concurrency.report_memory_incident_if_applicable(self, message_id, e)

            # Classify the exception to determine if we should retry
            error_type = MessageErrorClassifier.classify_by_exception(e)
            is_terminal = error_type == MessageErrorType.TERMINAL

            # Update is_final_failure on the message for terminal errors
            # (it was already set for transient based on retry count prediction)
            if is_terminal and parsed_message:
                parsed_message.is_final_failure = True

            if is_terminal:
                self.logger.warning(
                    f"Terminal error for {message_id}, committing to skip: {type(e).__name__}"
                )
            else:
                self.logger.warning(
                    f"Transient error for {message_id}, checking retry count: {type(e).__name__}"
                )

            await self.__commit_if_appropriate(
                message, parsed_message, success=False,
                is_terminal_error=is_terminal, in_flight=in_flight,
            )
            return False
        finally:
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

            if parsing_held:
                if distributed_leases.discard(parse_lease_pool) is not None:
                    await self._release_distributed_slot(parse_lease_pool, lease_owner)
                concurrency.release_admission(parsing_admission)
                parsing_admission = None
                self.logger.debug(f"Released parsing slot in finally for {message_id}")

            if indexing_held:
                if distributed_leases.discard(index_lease_pool) is not None:
                    await self._release_distributed_slot(index_lease_pool, lease_owner)
                concurrency.release_admission(index_admission)
                self.logger.debug(f"Released indexing gate in finally for {message_id}")

            for pool, owner in distributed_leases.snapshot():
                distributed_leases.discard(pool)
                await self._release_distributed_slot(pool, owner)


