import asyncio
import json
import ssl
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import wait as futures_wait
from logging import Logger
from typing import TYPE_CHECKING, Any, Optional, override

from aiokafka import AIOKafkaConsumer, TopicPartition  # type: ignore
from aiokafka.structs import ConsumerRecord  # type: ignore

from app.services.messaging.config import (
    IndexingEvent,
    IndexingMessageHandler,
    StreamMessage,
    compute_retry_backoff_seconds,
    messaging_env,
)
from app.services.messaging import consumer_concurrency as concurrency
from app.services.messaging.distributed_concurrency import DistributedLeaseSet
from app.services.messaging.error_classifier import (
    MessageErrorClassifier,
    MessageErrorType,
    format_exception_chain,
)
from app.services.messaging.interface.consumer import IMessagingConsumer
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.utils.request_context import (
    context_from_envelope,
    reset_context,
    set_context,
)

if TYPE_CHECKING:
    from app.services.messaging.distributed_concurrency import (
        DistributedConcurrencyManager,
    )
    from app.services.messaging.interface.producer import IMessagingProducer
    from app.services.messaging.retry_manager import RetryManager

FUTURE_CLEANUP_INTERVAL = 100  # Cleanup completed futures every N messages
_MAIN_LOOP_OP_TIMEOUT = 5.0
# How often the retry-backoff wait re-checks self.running, so a shutdown
# request can interrupt a long (up to 300s) wait instead of holding an
# active-future slot — and blocking graceful shutdown — for the full delay.
_DELAY_POLL_INTERVAL_SECONDS = 1.0

# Re-exported for backwards compatibility with existing call sites/tests in
# this module; canonical definition lives in app.services.messaging.config
# so the Redis Streams consumer can share the same backoff schedule.
_compute_retry_backoff_seconds = compute_retry_backoff_seconds


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
    ) -> None:
        self.logger = logger
        self.consumer: AIOKafkaConsumer | None = None
        self.running = False
        self.kafka_config = kafka_config
        self.consume_task = None
        self.retry_manager = retry_manager
        self.producer = producer
        self.concurrency_manager = concurrency_manager
        self._consumer_instance_id = uuid.uuid4().hex
        self._distributed_log_times: dict[str, float] = {}
        # Worker thread infrastructure
        self.worker_executor: ThreadPoolExecutor | None = None
        self.worker_loop: asyncio.AbstractEventLoop | None = None
        self.worker_loop_ready = threading.Event()  # Signal when worker loop is ready
        self.main_loop: asyncio.AbstractEventLoop | None = None
        # Nested active-pipeline and parsing gates (created in worker thread)
        self.parsing_semaphore: asyncio.Semaphore | None = None
        self.indexing_semaphore: asyncio.Semaphore | None = None
        self.message_handler: Optional[IndexingMessageHandler] = None
        # Track active futures for proper cleanup
        self._active_futures: set[Future[bool]] = set()
        self._futures_lock = threading.Lock()
        self._backpressure_logged = False
        self._partition_lock = threading.Lock()
        self._in_flight_partitions: set[TopicPartition] = set()
        self._deferred_partition_offsets: dict[TopicPartition, int] = {}

    @staticmethod
    def kafka_config_to_dict(kafka_config: KafkaConsumerConfig) -> dict[str, Any]:
        """Convert KafkaConsumerConfig dataclass to dictionary format for aiokafka consumer"""
        config: dict[str, Any] = {
            'bootstrap_servers': ",".join(kafka_config.bootstrap_servers),
            'group_id': kafka_config.group_id,
            'auto_offset_reset': kafka_config.auto_offset_reset,
            'enable_auto_commit': kafka_config.enable_auto_commit,
            'client_id': kafka_config.client_id,
            'topics': kafka_config.topics
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

            # Create semaphores in the worker thread's event loop
            self.parsing_semaphore = asyncio.Semaphore(messaging_env.max_concurrent_parsing)
            self.indexing_semaphore = asyncio.Semaphore(messaging_env.max_concurrent_indexing)

            self.logger.info("Worker thread event loop started with semaphores initialized")

            # Signal that the worker loop is ready
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
                f"Started Kafka consumer task with parsing_slots={messaging_env.max_concurrent_parsing}, "
                f"indexing_slots={messaging_env.max_concurrent_indexing}, max_pending_tasks={messaging_env.max_pending_indexing_tasks}"
            )
        except Exception as e:
            self.logger.error(f"Failed to start Kafka consumer: {str(e)}")
            raise

    @override
    async def stop(self, message_handler: Optional[IndexingMessageHandler] = None) -> None:  # type: ignore[override]
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
        with self._partition_lock:
            self._in_flight_partitions.clear()
            self._deferred_partition_offsets.clear()

        # concurrency_manager/retry_manager are injected, not owned — closing
        # them here would break a restart (start() -> stop() -> start() reuses
        # the same instances) and duplicate indexing_main's own cleanup of
        # them. The creator (start_kafka_consumers/stop_kafka_consumers) is
        # responsible for their lifecycle.

    @override
    def is_running(self) -> bool:
        """Check if consumer is running"""
        return self.running

    def __apply_backpressure(self) -> None:
        """Pause or resume Kafka partitions based on active task capacity.

        This ensures getmany() is always called (keeping the consumer alive
        and resetting max_poll_interval_ms), while preventing new messages
        from being returned when at capacity.
        """
        active_count = self._get_active_task_count()

        if active_count >= messaging_env.max_pending_indexing_tasks:
            # Pause partitions that aren't already paused
            assigned = self.consumer.assignment()
            not_paused = assigned - self.consumer.paused()
            if not_paused:
                self.consumer.pause(*not_paused)
            if not self._backpressure_logged:
                self.logger.warning(
                    f"Backpressure engaged: {active_count} active tasks queued; "
                    f"pausing Kafka partition reads at cap {messaging_env.max_pending_indexing_tasks}"
                )
                self._backpressure_logged = True
        else:
            # A partition remains paused while one of its messages is in flight;
            # this preserves Kafka's per-partition processing/commit order.
            paused = self.consumer.paused()
            with self._partition_lock:
                in_flight_partitions = set(self._in_flight_partitions)
            resumable = paused - in_flight_partitions
            if resumable:
                self.consumer.resume(*resumable)
            if self._backpressure_logged:
                self.logger.info(
                    f"Backpressure cleared: active tasks back to {active_count}/{messaging_env.max_pending_indexing_tasks}"
                )
                self._backpressure_logged = False

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
    ) -> None:
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
        if retry_offset is not None:
            self.consumer.seek(topic_partition, retry_offset)
        if (
            self.running
            and self._get_active_task_count()
            < messaging_env.max_pending_indexing_tasks
        ):
            self.consumer.resume(topic_partition)

    async def __consume_loop(self) -> None:
        """Main consumption loop with dual semaphore control"""
        try:
            self.logger.info("Starting Kafka consumer loop")
            while self.running:
                try:
                    self.__apply_backpressure()

                    available_capacity = max(
                        1,
                        messaging_env.max_pending_indexing_tasks
                        - self._get_active_task_count(),
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
                                self.logger.info(f"Received message: topic={message.topic}, partition={message.partition}, offset={message.offset}")
                                if self.__defer_if_retry_not_ready(message):
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
                                await self.__start_processing_task(message)
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



    def __defer_if_retry_not_ready(self, message: ConsumerRecord) -> bool:
        """Seek back to ``message`` and return True if its retry backoff
        (``_retry_not_before``, stamped by ``_requeue_message``) hasn't
        elapsed yet.

        Checked here — before ``__reserve_partition`` pauses the partition
        and before a worker-thread task/active-task slot is spent — so a
        single backing-off record doesn't tie up pipeline capacity for
        others while it waits out its (up to 5 minute) backoff. Ordering
        still means this partition can't skip ahead to later messages, but
        at least the wait no longer consumes real processing resources.
        """
        parsed = self.__parse_message(message)
        if parsed is None:
            return False
        not_before = parsed.payload.get("_retry_not_before")
        if not not_before:
            return False
        try:
            remaining = float(not_before) - time.time()
        except (TypeError, ValueError):
            return False
        if remaining <= 0:
            return False

        self.consumer.seek(
            TopicPartition(message.topic, message.partition), message.offset
        )
        return True

    def __parse_message(self, message: ConsumerRecord) -> StreamMessage | None:
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
                    parsed = json.loads(message_value)
                    # Handle double-encoded JSON
                    if isinstance(parsed, str):
                        parsed = json.loads(parsed)
                        self.logger.debug("Handled double-encoded JSON message")

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

    async def __start_processing_task(self, message: ConsumerRecord) -> None:
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
        processing_coro = self.__process_message_wrapper(message)
        try:
            future = asyncio.run_coroutine_threadsafe(
                processing_coro,
                self.worker_loop,
            )
        except BaseException:
            processing_coro.close()
            raise

        # Track the future for cleanup during shutdown
        with self._futures_lock:
            self._active_futures.add(future)

        # Add callback to remove future from tracking when done
        def on_future_done(f: Future[bool]) -> None:
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
    ) -> bool:
        """Try to acquire a distributed lease; see consumer_concurrency for semantics."""
        return await concurrency.acquire_distributed_slot(
            self, pool, owner, limit, deadline_seconds
        )

    async def _release_distributed_slot(self, pool: str, owner: str) -> None:
        await concurrency.release_distributed_slot(self, pool, owner)

    async def _renew_distributed_slots(
        self,
        leases: DistributedLeaseSet,
    ) -> None:
        await concurrency.renew_distributed_slots(self, leases)

    def _start_distributed_renewal(
        self,
        leases: DistributedLeaseSet,
    ) -> asyncio.Future[None]:
        return concurrency.start_distributed_renewal(self, leases)

    async def _clear_retry_tracking(self, message_id: str) -> None:
        await concurrency.clear_retry_tracking(self, message_id)

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

    async def __commit_if_appropriate(
        self,
        message: ConsumerRecord,
        parsed_message: StreamMessage | None,
        success: bool,
        is_terminal_error: bool = False,
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
            self.logger.warning(f"Terminal error for {message_id}, committing without retry")
            await self._clear_retry_tracking(stable_message_id)
        elif self.retry_manager and parsed_message:
            count, should_dead_letter = await self._increment_retry_and_check(stable_message_id)
            if should_dead_letter:
                self.logger.warning(
                    f"Dead-lettering {message_id} (tracking ID: {stable_message_id}) after {count} transient failures"
                )
                await self._clear_retry_tracking(stable_message_id)
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

    async def __process_message_wrapper(self, message: ConsumerRecord) -> bool:
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
        distributed_leases = DistributedLeaseSet()
        renewal_task: asyncio.Future[None] | None = None
        lease_owner = (
            f"{self._consumer_instance_id}:{message_id}:{uuid.uuid4().hex}"
        )

        if not self.parsing_semaphore or not self.indexing_semaphore:
            self.logger.error(f"Semaphores not initialized for {message_id}")
            return False

        # Parse (and, for re-queued messages, wait out any backoff) before
        # acquiring the parsing semaphore. This way a retry waiting for a
        # downed service to recover only occupies a pending-task slot
        # (counted against backpressure), never a parsing/indexing semaphore
        # slot — the exact resource a sibling record needs to make progress.
        parsed_message = self.__parse_message(message)
        if parsed_message is None:
            self.logger.warning(f"Failed to parse message {message_id}, skipping")
            await self.__commit_if_appropriate(message, None, success=False, is_terminal_error=True)
            return False

        if not await self.__delay_if_retry_not_ready(parsed_message, message_id):
            return False

        stable_message_id = self._get_stable_message_id(message, parsed_message)
        record_lock_id = (
            parsed_message.payload.get("recordId") or stable_message_id
        )
        record_pool = f"record:{record_lock_id}"

        try:
            # MAX_CONCURRENT_INDEXING is also the active-pipeline bound. Without
            # this outer permit, parsed records can accumulate while waiting for
            # an indexing permit and every one can remain IN_PROGRESS in the DB.
            if self.concurrency_manager is not None:
                if not await self._acquire_distributed_slot(
                    "indexing",
                    lease_owner,
                    messaging_env.max_concurrent_indexing,
                ):
                    return False
                distributed_leases.add("indexing", lease_owner)
                # Recovery treats this lease as proof of active processing, so
                # queued tasks must not own it before entering the indexing gate.
                renewal_task = self._start_distributed_renewal(
                    distributed_leases
                )

            await self.indexing_semaphore.acquire()
            indexing_held = True

            if self.concurrency_manager is not None:
                if not await self._acquire_distributed_slot(
                    record_pool,
                    lease_owner,
                    1,
                    deadline_seconds=messaging_env.record_lease_wait_seconds,
                ):
                    if self.running:
                        self.logger.debug(
                            f"Record lease contended for {message_id}; another "
                            "in-flight duplicate delivery already owns it, "
                            "dropping this one without commit (offset "
                            "advances on a later message in this partition)"
                        )
                    return False
                distributed_leases.add(record_pool, lease_owner)

            parsed_message.payload["_processing_started_at"] = int(time.time() * 1000)

            # Check current retry count to predict if this will be the final attempt on failure
            current_retry_count = 0
            if self.retry_manager:
                current_retry_count = await self._run_on_main_loop(
                    self.retry_manager.get_count(stable_message_id)
                )

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
                    nonlocal parsing_held, indexing_held, success, shutting_down
                    async with asyncio.timeout(messaging_env.record_processing_timeout):
                        event_gen = self.message_handler(parsed_message)
                        try:
                            async for event in event_gen:
                                if (
                                    event.event == IndexingEvent.START_PARSING
                                    and not parsing_held
                                    and self.parsing_semaphore
                                ):
                                    if self.concurrency_manager is not None:
                                        if not await self._acquire_distributed_slot(
                                            "parsing",
                                            lease_owner,
                                            messaging_env.max_concurrent_parsing,
                                        ):
                                            # Only reason try_acquire gives up
                                            # (no deadline here) is self.running
                                            # flipping — abort without raising
                                            # so the caller doesn't burn a
                                            # retry attempt on a clean shutdown.
                                            shutting_down = True
                                            return
                                        distributed_leases.add("parsing", lease_owner)
                                    await self.parsing_semaphore.acquire()
                                    parsing_held = True
                                    self.logger.debug(
                                        f"Acquired parsing semaphore for {message_id}"
                                    )
                                elif (
                                    event.event == IndexingEvent.PARSING_COMPLETE
                                    and parsing_held
                                    and self.parsing_semaphore
                                ):
                                    distributed_leases.discard("parsing")
                                    await self._release_distributed_slot(
                                        "parsing", lease_owner
                                    )
                                    self.parsing_semaphore.release()
                                    parsing_held = False
                                    self.logger.debug(
                                        f"Released parsing semaphore for {message_id}"
                                    )
                                elif (
                                    event.event == IndexingEvent.INDEXING_COMPLETE
                                    and indexing_held
                                    and self.indexing_semaphore
                                ):
                                    distributed_leases.discard("indexing")
                                    await self._release_distributed_slot(
                                        "indexing", lease_owner
                                    )
                                    self.indexing_semaphore.release()
                                    indexing_held = False
                                    self.logger.debug(
                                        f"Released indexing semaphore for {message_id}"
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
                            try:
                                renewal_error = renewal_task.exception()
                            except asyncio.CancelledError:
                                renewal_error = RuntimeError(
                                    "Distributed concurrency lease guard was cancelled"
                                )
                            handler_task.cancel()
                            await asyncio.gather(handler_task, return_exceptions=True)
                            raise renewal_error or RuntimeError(
                                "Distributed concurrency lease guard stopped"
                            )
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
                await self.__commit_if_appropriate(message, parsed_message, success=False, is_terminal_error=True)
                return False

            if shutting_down:
                # Consumer stopped while waiting for the parsing slot: leave
                # the offset uncommitted (redelivered on restart) instead of
                # committing/retrying, matching the indexing/record lease
                # gates above which already just return on shutdown.
                self.logger.info(
                    f"Consumer stopping, abandoning {message_id} without commit"
                )
                return False

            # Commit based on success
            await self.__commit_if_appropriate(message, parsed_message, success=success)
            return success

        except Exception as e:
            # Log the full exception chain for debugging
            exception_chain = format_exception_chain(e)
            self.logger.error(
                f"Error in process_message_wrapper for {message_id}:\n{exception_chain}"
            )

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

            await self.__commit_if_appropriate(message, parsed_message, success=False, is_terminal_error=is_terminal)
            return False
        finally:
            # Ensure semaphores are released even on error
            if renewal_task is not None:
                renewal_task.cancel()
                await asyncio.gather(renewal_task, return_exceptions=True)

            if parsing_held and self.parsing_semaphore:
                if distributed_leases.discard("parsing") is not None:
                    await self._release_distributed_slot("parsing", lease_owner)
                self.parsing_semaphore.release()
                self.logger.debug(f"Released parsing semaphore in finally for {message_id}")

            if indexing_held and self.indexing_semaphore:
                if distributed_leases.discard("indexing") is not None:
                    await self._release_distributed_slot("indexing", lease_owner)
                self.indexing_semaphore.release()
                self.logger.debug(f"Released indexing semaphore in finally for {message_id}")

            for pool, owner in distributed_leases.snapshot():
                distributed_leases.discard(pool)
                await self._release_distributed_slot(pool, owner)


