import asyncio
import json
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from logging import Logger
from typing import Any, Optional, override

from pydantic import ValidationError
from redis.asyncio import Redis

from app.services.messaging.config import (
    IndexingEvent,
    IndexingMessageHandler,
    RedisStreamsConfig,
    StreamMessage,
    messaging_env,
)
from app.services.messaging.error_classifier import MessageErrorClassifier, MessageErrorType, format_exception_chain
from app.services.messaging.interface.consumer import IMessagingConsumer
from app.services.messaging.interface.producer import IMessagingProducer
from app.utils.request_context import (
    context_from_envelope,
    reset_context,
    set_context,
)
from app.services.messaging.retry_manager import RetryManager

_BUSYGROUP_ERROR = "BUSYGROUP"
_MESSAGE_VALUE_FIELD = "value"
_MAIN_LOOP_OP_TIMEOUT = 5.0


class IndexingRedisStreamsConsumer(IMessagingConsumer):
    """Redis Streams consumer with dual-semaphore control for indexing pipeline.

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
        retry_manager: Optional[RetryManager] = None,
        producer: Optional[IMessagingProducer] = None,
    ) -> None:
        self.logger = logger
        self.config = config
        self.retry_manager = retry_manager
        self.producer = producer
        self.redis: Optional[Redis] = None
        self.running = False
        self.consume_task: Optional[asyncio.Task] = None
        self.worker_executor: Optional[ThreadPoolExecutor] = None
        self.worker_loop: Optional[asyncio.AbstractEventLoop] = None
        self.worker_loop_ready = threading.Event()
        self.main_loop: Optional[asyncio.AbstractEventLoop] = None
        self.parsing_semaphore: Optional[asyncio.Semaphore] = None
        self.indexing_semaphore: Optional[asyncio.Semaphore] = None
        self.message_handler: Optional[IndexingMessageHandler] = None
        self._active_futures: set[Future[bool]] = set()
        self._futures_lock = threading.Lock()
        self._backpressure_active = False
        self._consecutive_empty_polls = 0
        self._idle_threshold = 3  # Drain pending after N consecutive empty polls
        self._in_flight_message_ids: set[str] = set()
        self._in_flight_lock = threading.Lock()


    @override
    async def initialize(self) -> None:
        try:
            self._start_worker_thread()

            if not self.worker_loop_ready.wait(timeout=60.0):
                raise RuntimeError("Worker thread event loop not initialized in time")

            if not self.worker_loop or not self.worker_loop.is_running():
                raise RuntimeError("Worker thread event loop failed to start")

            self.redis = Redis(
                host=self.config.host,
                port=self.config.port,
                password=self.config.password,
                db=self.config.db,
                decode_responses=True,
            )
            await self.redis.ping()

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

    def _start_worker_thread(self) -> None:
        def run_worker_loop() -> None:
            self.worker_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.worker_loop)
            self.parsing_semaphore = asyncio.Semaphore(messaging_env.max_concurrent_parsing)
            self.indexing_semaphore = asyncio.Semaphore(messaging_env.max_concurrent_indexing)
            self.logger.info(
                "Worker thread event loop started with semaphores initialized"
            )
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
            self._stop_worker_thread()
            if self.redis:
                await self.redis.close()
                self.logger.info("Redis Streams consumer stopped")
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
                "Started Redis Streams consumer task with parsing_slots=%d, indexing_slots=%d",
                messaging_env.max_concurrent_parsing,
                messaging_env.max_concurrent_indexing,
            )
        except Exception as e:
            self.logger.error("Failed to start Redis Streams consumer: %s", e)
            raise

    async def stop(  # type: ignore[override]
        self,
        message_handler: Optional[IndexingMessageHandler] = None,
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
        await loop.run_in_executor(None, self._stop_worker_thread)

        if self.redis:
            try:
                await self.redis.close()
                self.logger.info("Redis Streams consumer stopped")
            except Exception as e:
                self.logger.error("Error stopping Redis Streams consumer: %s", e)

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

    def _wait_for_active_futures(self) -> None:
        with self._futures_lock:
            futures_to_wait = list(self._active_futures)
        if not futures_to_wait:
            return

        self.logger.info(
            "Waiting for %d active tasks to complete", len(futures_to_wait)
        )
        for future in futures_to_wait:
            try:
                future.result(timeout=messaging_env.shutdown_task_timeout)
            except TimeoutError:
                future.cancel()
            except Exception as e:
                self.logger.warning("Task errored during shutdown: %s", e)

    def _get_active_task_count(self) -> int:
        with self._futures_lock:
            return len(self._active_futures)

    def _is_in_flight(self, message_id: str) -> bool:
        with self._in_flight_lock:
            return message_id in self._in_flight_message_ids

    def _mark_in_flight(self, message_id: str) -> None:
        with self._in_flight_lock:
            self._in_flight_message_ids.add(message_id)

    def _unmark_in_flight(self, message_id: str) -> None:
        with self._in_flight_lock:
            self._in_flight_message_ids.discard(message_id)

    async def _should_dead_letter(self, topic: str, message_id: str) -> bool:
        """Check if message should be dead-lettered based on failure retry count.

        Uses RetryManager (actual transient failures), not Redis times_delivered
        which increments on every XREADGROUP delivery including PEL re-reads.
        """
        max_attempts = messaging_env.max_delivery_attempts

        try:
            if self.retry_manager is not None:
                failure_count = await self._get_retry_count(message_id)
                if failure_count >= max_attempts:
                    await self.redis.xack(topic, self.config.group_id, message_id)  # type: ignore
                    await self._clear_retry_tracking(message_id)
                    self.logger.warning(
                        "Dead-lettered %s after %d transient failures (max %d)",
                        message_id,
                        failure_count,
                        max_attempts,
                    )
                    return True
                return False

            details = await self.redis.xpending_range(  # type: ignore
                topic,
                self.config.group_id,
                min=message_id,
                max=message_id,
                count=1,
            )
            if details:
                times_delivered = details[0].get("times_delivered", 0)
                if times_delivered >= max_attempts:
                    await self.redis.xack(topic, self.config.group_id, message_id)  # type: ignore
                    self.logger.warning(
                        "Dead-lettered %s after %d transient failures (max %d)",
                        message_id,
                        times_delivered,
                        max_attempts,
                    )
                    return True
        except Exception as e:
            self.logger.error("Error checking delivery count: %s", e)

        return False

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
                active_count = self._get_active_task_count()
                if active_count >= messaging_env.max_pending_indexing_tasks:
                    await asyncio.sleep(0.5)
                    continue
                try:
                    result = await self.redis.xautoclaim(  # type: ignore
                        topic,
                        self.config.group_id,
                        self.config.client_id,
                        min_idle_time=self.config.claim_min_idle_ms,
                        start_id=start_id,
                        count=10,
                    )
                    next_id, claimed, _deleted = result
                    if not claimed:
                        break
                    for message_id, fields in claimed:
                        if not self.running:
                            return processed_any
                        if self._is_in_flight(message_id):
                            continue
                        try:
                            if await self._should_dead_letter(topic, message_id):
                                continue
                            processed_any = True
                            self.logger.info(
                                "Recovering pending message: stream=%s, id=%s",
                                topic,
                                message_id,
                            )
                            await self._start_processing_task(topic, message_id, fields)
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

            # Phase 2: read messages already in THIS consumer's PEL
            last_pending_id = "0"
            while self.running:
                active_count = self._get_active_task_count()
                if active_count >= messaging_env.max_pending_indexing_tasks:
                    await asyncio.sleep(0.5)
                    continue
                try:
                    results = await self.redis.xreadgroup(  # type: ignore
                        groupname=self.config.group_id,
                        consumername=self.config.client_id,
                        streams={topic: last_pending_id},
                        count=self.config.batch_size,
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
                            drained_any = True
                            last_pending_id = message_id
                            try:
                                if await self._should_dead_letter(topic, message_id):
                                    continue
                                processed_any = True
                                self.logger.info(
                                    "Recovering own pending message: stream=%s, id=%s",
                                    topic,
                                    message_id,
                                )
                                await self._start_processing_task(topic, message_id, fields)
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

        if processed_any:
            self.logger.info("Processed pending messages from PEL")
        return processed_any

    async def _consume_loop(self) -> None:
        """Main consumption loop with idle-based pending drain.

        New messages are processed first. When no new messages arrive for
        several consecutive polls (idle), pending messages from the PEL
        are processed (retry of failed messages).
        """
        try:
            self.logger.info("Starting Redis Streams consumer loop")
            # Initial drain on startup
            await self._drain_pending()
            while self.running:
                try:
                    active_count = self._get_active_task_count()
                    if active_count >= messaging_env.max_pending_indexing_tasks:
                        if not self._backpressure_active:
                            self.logger.warning(
                                "Backpressure engaged: %d active tasks",
                                active_count,
                            )
                            self._backpressure_active = True
                        await asyncio.sleep(0.5)
                        continue
                    elif self._backpressure_active:
                        self.logger.info(
                            "Backpressure cleared: %d/%d",
                            active_count,
                            messaging_env.max_pending_indexing_tasks,
                        )
                        self._backpressure_active = False

                    streams = dict.fromkeys(self.config.topics, ">")
                    results = await self.redis.xreadgroup(  # type: ignore
                        groupname=self.config.group_id,
                        consumername=self.config.client_id,
                        streams=streams,
                        count=self.config.batch_size,
                        block=self.config.block_ms,
                    )

                    if not results:
                        # No new messages - increment idle counter
                        self._consecutive_empty_polls += 1
                        if self._consecutive_empty_polls >= self._idle_threshold:
                            # Idle: process pending messages
                            await self._drain_pending()
                            self._consecutive_empty_polls = 0
                        continue

                    # Reset idle counter when new messages arrive
                    self._consecutive_empty_polls = 0

                    for stream_name, messages in results:
                        for message_id, fields in messages:
                            if not self.running:
                                break
                            try:
                                self.logger.info(
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

    def _parse_message(
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
            raw = json.loads(value_str)
            if isinstance(raw, str):
                raw = json.loads(raw)
            return StreamMessage(**raw)
        except (json.JSONDecodeError, ValidationError, TypeError) as e:
            self.logger.error(
                "Failed to parse message %s as StreamMessage: %s", message_id, e
            )
            return None

    async def _start_processing_task(
        self, stream_name: str, message_id: str, fields: dict[str, str]
    ) -> None:
        if not self.worker_loop:
            self.logger.error("Worker loop not initialized, cannot process message")
            return
        if not self.running:
            return

        self._mark_in_flight(message_id)
        future = asyncio.run_coroutine_threadsafe(
            self._process_message_wrapper(stream_name, message_id, fields),
            self.worker_loop,
        )
        with self._futures_lock:
            self._active_futures.add(future)

        def on_future_done(f: Future[bool]) -> None:
            self._unmark_in_flight(message_id)
            with self._futures_lock:
                self._active_futures.discard(f)
            try:
                _ = f.result()
            except Exception as exc:
                self.logger.error("Task completed with unhandled exception: %s", exc)

        future.add_done_callback(on_future_done)

    async def _run_on_main_loop(self, coro: Any) -> Any:
        """Run a coroutine on the main loop (safe when called from the worker loop)."""
        if (
            self.main_loop
            and self.main_loop.is_running()
            and asyncio.get_running_loop() is not self.main_loop
        ):
            future = asyncio.run_coroutine_threadsafe(coro, self.main_loop)
            return await asyncio.wait_for(
                asyncio.wrap_future(future), timeout=_MAIN_LOOP_OP_TIMEOUT
            )
        return await coro

    async def _clear_retry_tracking(self, message_id: str) -> None:
        if not self.retry_manager:
            return
        try:
            await self._run_on_main_loop(self.retry_manager.clear(message_id))
        except Exception as e:
            self.logger.error(
                "Failed to clear retry tracking for %s: %s", message_id, e
            )

    async def _get_retry_count(self, message_id: str) -> int:
        if not self.retry_manager:
            return 0
        return int(
            await self._run_on_main_loop(self.retry_manager.get_count(message_id))
        )

    async def _increment_retry_and_check(
        self, message_id: str
    ) -> tuple[int, bool]:
        if not self.retry_manager:
            return 0, False
        return await self._run_on_main_loop(
            self.retry_manager.increment_and_check(
                message_id, messaging_env.max_delivery_attempts
            )
        )

    async def _ack_message(self, stream_name: str, message_id: str) -> None:
        """Acknowledge ``message_id`` so it leaves the consumer group's PEL.

        Processing runs on the worker loop, but ``self.redis`` is bound to the
        main loop where it was created — so the XACK is scheduled there and
        awaited via ``wrap_future`` so the worker loop is never blocked.
        """
        if self.redis and self.main_loop and self.main_loop.is_running():
            ack_future = asyncio.run_coroutine_threadsafe(
                self.redis.xack(stream_name, self.config.group_id, message_id),  # type: ignore
                self.main_loop,
            )
            try:
                await asyncio.wait_for(asyncio.wrap_future(ack_future), timeout=5)
            except (asyncio.TimeoutError, TimeoutError):
                self.logger.warning(
                    "Timed out waiting for xack on %s, will be re-delivered",
                    message_id,
                )
        elif not self.running:
            self.logger.debug("Skipping xack for %s during shutdown", message_id)

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
        self, stream_name: str, message: StreamMessage, stable_message_id: str
    ) -> None:
        """Re-publish a failed message to the same stream for retry.
        
        The message goes to the end of the queue, allowing transient errors
        to resolve before retry. The original message is acknowledged.
        
        Preserves the stable message ID in the payload for retry tracking.
        
        Args:
            stream_name: Stream to re-queue to
            message: The message to re-queue
            stable_message_id: Stable ID for retry tracking (preserved across re-queues)
        """
        if not self.producer:
            self.logger.error("No producer available for re-queue")
            return
        
        try:
            payload = dict(message.payload)
            payload["_retry_tracking_id"] = stable_message_id
            
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

    async def _process_message_wrapper(
        self, stream_name: str, message_id: str, fields: dict[str, str]
    ) -> bool:
        """Process message with dual semaphore control.

        Error classification is based purely on exception type:
        - TERMINAL: ACK immediately (parsing errors, validation errors)
        - TRANSIENT: Don't ACK, let PEL retry
        """
        parsing_held = False
        indexing_held = False
        acked = False

        if not self.parsing_semaphore or not self.indexing_semaphore:
            self.logger.error("Semaphores not initialized for %s", message_id)
            return False

        try:
            await self.parsing_semaphore.acquire()
            parsing_held = True

            await self.indexing_semaphore.acquire()
            indexing_held = True

            parsed_message = self._parse_message(message_id, fields)
            if parsed_message is None:
                # Poison message: it can never become valid on retry, so ACK it
                # to remove it from the PEL instead of recovering it forever.
                self.logger.warning(
                    "Dropping unparseable message %s from stream %s "
                    "(acknowledged, not retried)",
                    message_id,
                    stream_name,
                )
                await self._ack_message(stream_name, message_id)
                acked = True
                await self._clear_retry_tracking(message_id)
                return False

            # Get stable message ID for retry tracking (preserves across re-queues)
            stable_message_id = self._get_stable_message_id(message_id, parsed_message)

            # Check current retry count to predict if this will be the final attempt on failure
            current_retry_count = await self._get_retry_count(stable_message_id)
            
            # This will be final if: count is already at max-1 (next increment reaches max)
            # OR if we don't have retry manager (always final)
            will_be_final_on_failure = (
                not self.retry_manager or 
                current_retry_count >= messaging_env.max_delivery_attempts - 1
            )
            
            # Set flag on message so handler knows whether to update DB status on failure
            parsed_message.is_final_failure = will_be_final_on_failure

            if self.message_handler:
                # Carry the producer's trace id into indexing logs.
                ctx = context_from_envelope({"requestId": parsed_message.requestId})
                token = set_context(ctx.root_id)
                try:
                    async for event in self.message_handler(parsed_message):
                        if (
                            event.event == IndexingEvent.PARSING_COMPLETE
                            and parsing_held
                            and self.parsing_semaphore
                        ):
                            self.parsing_semaphore.release()
                            parsing_held = False
                        elif (
                            event.event == IndexingEvent.INDEXING_COMPLETE
                            and indexing_held
                            and self.indexing_semaphore
                        ):
                            self.indexing_semaphore.release()
                            indexing_held = False
                finally:
                    reset_context(token)

                await self._ack_message(stream_name, message_id)
                acked = True
                await self._clear_retry_tracking(stable_message_id)
            else:
                self.logger.error("No message handler available for %s", message_id)
                return False

            return True

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

            # Classify the exception to determine if we should retry
            error_type = MessageErrorClassifier.classify_by_exception(e)

            if error_type == MessageErrorType.TERMINAL:
                # Update is_final_failure for terminal errors
                if parsed_message:
                    parsed_message.is_final_failure = True
                # Terminal error: ACK immediately to skip this message
                self.logger.warning(
                    "Terminal error for %s, ACK'ing to skip: %s",
                    message_id,
                    type(e).__name__,
                )
                await self._ack_message(stream_name, message_id)
                acked = True
                await self._clear_retry_tracking(stable_message_id)
            elif self.retry_manager is not None and parsed_message:
                failure_count, should_dead_letter = (
                    await self._increment_retry_and_check(stable_message_id)
                )
                if should_dead_letter:
                    await self._ack_message(stream_name, message_id)
                    acked = True
                    await self._clear_retry_tracking(stable_message_id)
                    self.logger.warning(
                        "Dead-lettered %s (tracking ID: %s) after %d transient failures (max %d): %s",
                        message_id,
                        stable_message_id,
                        failure_count,
                        messaging_env.max_delivery_attempts,
                        type(e).__name__,
                    )
                else:
                    # RE-QUEUE: Publish back to same stream for retry, then ACK
                    try:
                        await self._requeue_message(stream_name, parsed_message, stable_message_id)
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
            if parsing_held and self.parsing_semaphore:
                self.parsing_semaphore.release()
            if indexing_held and self.indexing_semaphore:
                self.indexing_semaphore.release()
