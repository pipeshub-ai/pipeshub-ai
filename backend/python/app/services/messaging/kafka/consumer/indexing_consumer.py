import asyncio
import json
import ssl
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from logging import Logger
from typing import TYPE_CHECKING, Any, Optional, override

from aiokafka import AIOKafkaConsumer, TopicPartition  # type: ignore
from aiokafka.structs import ConsumerRecord  # type: ignore

from app.services.messaging.config import (
    IndexingEvent,
    IndexingMessageHandler,
    StreamMessage,
    messaging_env,
)
from app.services.messaging.error_classifier import MessageErrorClassifier, MessageErrorType, format_exception_chain
from app.services.messaging.interface.consumer import IMessagingConsumer
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.utils.request_context import (
    context_from_envelope,
    reset_context,
    set_context,
)

if TYPE_CHECKING:
    from app.services.messaging.interface.producer import IMessagingProducer
    from app.services.messaging.retry_manager import RetryManager

FUTURE_CLEANUP_INTERVAL = 100  # Cleanup completed futures every N messages
_MAIN_LOOP_OP_TIMEOUT = 5.0


class IndexingKafkaConsumer(IMessagingConsumer):
    """Kafka consumer with dual-semaphore control for indexing pipeline.

    This consumer is designed for the indexing service where messages go through
    two phases: parsing and indexing. Each phase has its own semaphore to control
    concurrency independently.

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
    ) -> None:
        self.logger = logger
        self.consumer: AIOKafkaConsumer | None = None
        self.running = False
        self.kafka_config = kafka_config
        self.consume_task = None
        self.retry_manager = retry_manager
        self.producer = producer
        # Worker thread infrastructure
        self.worker_executor: ThreadPoolExecutor | None = None
        self.worker_loop: asyncio.AbstractEventLoop | None = None
        self.worker_loop_ready = threading.Event()  # Signal when worker loop is ready
        self.main_loop: asyncio.AbstractEventLoop | None = None
        # Dual semaphores for parsing and indexing phases (created in worker thread)
        self.parsing_semaphore: asyncio.Semaphore | None = None
        self.indexing_semaphore: asyncio.Semaphore | None = None
        self.message_handler: Optional[IndexingMessageHandler] = None
        # Track active futures for proper cleanup
        self._active_futures: set[Future[bool]] = set()
        self._futures_lock = threading.Lock()
        self._backpressure_logged = False

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
        """Wait for all active futures to complete with a timeout"""
        with self._futures_lock:
            futures_to_wait = list(self._active_futures)

        if not futures_to_wait:
            self.logger.info("No active futures to wait for during shutdown")
            return

        self.logger.info(f"Waiting for {len(futures_to_wait)} active tasks to complete (timeout: {messaging_env.shutdown_task_timeout}s)")

        completed = 0
        timed_out = 0
        errored = 0

        for future in futures_to_wait:
            try:
                future.result(timeout=messaging_env.shutdown_task_timeout)
                completed += 1
            except TimeoutError:
                timed_out += 1
                self.logger.warning("Task timed out during shutdown")
                future.cancel()
            except Exception as e:
                errored += 1
                self.logger.warning(f"Task errored during shutdown: {e}")

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
            # Stop worker thread first
            self.__stop_worker_thread()

            if self.consumer:
                await self.consumer.stop()
                self.logger.info("Kafka consumer stopped")
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

        # Stop worker thread (this waits for active futures)
        self.__stop_worker_thread()

        # Stop the Kafka consumer last
        if self.consumer:
            try:
                await self.consumer.stop()
                self.logger.info("✅ Kafka consumer stopped")
            except Exception as e:
                self.logger.error(f"Error stopping Kafka consumer: {e}")

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
            # Resume any paused partitions
            paused = self.consumer.paused()
            if paused:
                self.consumer.resume(*paused)
            if self._backpressure_logged:
                self.logger.info(
                    f"Backpressure cleared: active tasks back to {active_count}/{messaging_env.max_pending_indexing_tasks}"
                )
                self._backpressure_logged = False

    async def __consume_loop(self) -> None:
        """Main consumption loop with dual semaphore control"""
        try:
            self.logger.info("Starting Kafka consumer loop")
            while self.running:
                try:
                    self.__apply_backpressure()


                    message_batch = await self.consumer.getmany(
                        timeout_ms=messaging_env.message_timeout_ms,
                        max_records=messaging_env.message_batch_size_indexing
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
                                await self.__start_processing_task(message)
                            except Exception as e:
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
            self.logger.error("Worker loop not initialized, cannot process message")
            return

        if not self.running:
            self.logger.warning("Consumer is stopping, skipping message processing")
            return


        # Submit coroutine to worker thread's event loop and track the future
        future = asyncio.run_coroutine_threadsafe(
            self.__process_message_wrapper(message),
            self.worker_loop
        )

        # Track the future for cleanup during shutdown
        with self._futures_lock:
            self._active_futures.add(future)

        # Add callback to remove future from tracking when done
        def on_future_done(f: Future[bool]) -> None:
            with self._futures_lock:
                self._active_futures.discard(f)

            try:
                _ = f.result()
            except Exception as exc:
                self.logger.error(f"Task completed with unhandled exception: {exc}")

        future.add_done_callback(on_future_done)

    async def _run_on_main_loop(self, coro: Any) -> Any:
        """Run a coroutine on the main loop (safe when called from the worker loop)."""
        current_loop = asyncio.get_running_loop()
        main_loop = self.main_loop
        needs_bridge = (
            main_loop is not None
            and main_loop.is_running()
            and current_loop is not main_loop
        )
        if needs_bridge:
            future = asyncio.run_coroutine_threadsafe(coro, main_loop)
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

    async def _increment_retry_and_check(
        self, message_id: str
    ) -> tuple[int, bool]:
        if not self.retry_manager:
            return 0, False
        result = await self._run_on_main_loop(
            self.retry_manager.increment_and_check(
                message_id, messaging_env.max_delivery_attempts
            )
        )
        return result

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
        self, topic: str, message: StreamMessage, stable_message_id: str
    ) -> None:
        """Re-publish a failed message to the same topic for retry.
        
        The message goes to the end of the queue, allowing transient errors
        to resolve before retry. The original offset is committed.
        
        Preserves the stable message ID in the payload for retry tracking.
        
        Args:
            topic: Topic to re-queue to
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
                    topic=topic,
                    event_type=message.eventType,
                    payload=payload,
                )
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
                    await self._requeue_message(message.topic, parsed_message, stable_message_id)
                    self.logger.info(
                        f"Re-queued {message_id} (tracking ID: {stable_message_id}) for retry (attempt {count}/"
                        f"{messaging_env.max_delivery_attempts})"
                    )
                except Exception as e:
                    self.logger.error(f"Failed to re-queue {message_id}: {e}")
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

    async def __process_message_wrapper(self, message: ConsumerRecord) -> bool:
        """Wrapper to handle async task cleanup and semaphore release based on yielded events.

        Iterates over events yielded by the message handler:
        - 'parsing_complete': releases parsing semaphore
        - 'indexing_complete': releases indexing semaphore

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
        parsed_message: StreamMessage | None = None

        if not self.parsing_semaphore or not self.indexing_semaphore:
            self.logger.error(f"Semaphores not initialized for {message_id}")
            return False

        try:
            await self.parsing_semaphore.acquire()
            parsing_held = True

            await self.indexing_semaphore.acquire()
            indexing_held = True

            parsed_message = self.__parse_message(message)
            if parsed_message is None:
                self.logger.warning(f"Failed to parse message {message_id}, skipping")
                await self.__commit_if_appropriate(message, None, success=False, is_terminal_error=True)
                return False

            # Get stable message ID for retry tracking (preserves across re-queues)
            stable_message_id = self._get_stable_message_id(message, parsed_message)

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
                try:
                    async with asyncio.timeout(messaging_env.record_processing_timeout):
                        async for event in self.message_handler(parsed_message):
                            if event.event == IndexingEvent.PARSING_COMPLETE and parsing_held and self.parsing_semaphore:
                                self.parsing_semaphore.release()
                                parsing_held = False
                                self.logger.debug(f"Released parsing semaphore for {message_id}")
                            elif event.event == IndexingEvent.INDEXING_COMPLETE and indexing_held and self.indexing_semaphore:
                                self.indexing_semaphore.release()
                                indexing_held = False
                                self.logger.debug(f"Released indexing semaphore for {message_id}")
                                success = True  # Both events completed successfully
                except TimeoutError:
                    self.logger.error(
                        f"Record processing timed out after {messaging_env.record_processing_timeout}s "
                        f"for {message_id}"
                    )
                    raise
                finally:
                    reset_context(token)
            else:
                self.logger.error(f"No message handler available for {message_id}")
                await self.__commit_if_appropriate(message, parsed_message, success=False, is_terminal_error=True)
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
            if parsing_held and self.parsing_semaphore:
                self.parsing_semaphore.release()
                self.logger.debug(f"Released parsing semaphore in finally for {message_id}")

            if indexing_held and self.indexing_semaphore:
                self.indexing_semaphore.release()
                self.logger.debug(f"Released indexing semaphore in finally for {message_id}")


