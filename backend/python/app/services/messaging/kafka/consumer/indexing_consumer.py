import asyncio
import json
import ssl
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from logging import Logger
from typing import Any, Optional, override

from aiokafka import AIOKafkaConsumer  # type: ignore
from aiokafka.structs import ConsumerRecord  # type: ignore

from app.services.messaging.config import (
    MAX_CONCURRENT_INDEXING,
    MAX_CONCURRENT_PARSING,
    MAX_PENDING_INDEXING_TASKS,
    SHUTDOWN_TASK_TIMEOUT,
    IndexingEvent,
    IndexingMessageHandler,
    StreamMessage,
)
from app.services.messaging.interface.consumer import IMessagingConsumer
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig

FUTURE_CLEANUP_INTERVAL = 100  # Cleanup completed futures every N messages


class IndexingKafkaConsumer(IMessagingConsumer):
    """Kafka consumer with dual-semaphore control for indexing pipeline.

    This consumer is designed for the indexing service where messages go through
    two phases: parsing and indexing. Each phase has its own semaphore to control
    concurrency independently.

    The message handler must be an async generator that yields events:
    - {'event': 'parsing_complete', ...} - when parsing phase is done
    - {'event': 'indexing_complete', ...} - when indexing phase is done
    """

    def __init__(self,
                logger: Logger,
                kafka_config: KafkaConsumerConfig) -> None:
        self.logger = logger
        self.consumer: AIOKafkaConsumer | None = None
        self.running = False
        self.kafka_config = kafka_config
        self.consume_task = None
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
            self.parsing_semaphore = asyncio.Semaphore(MAX_CONCURRENT_PARSING)
            self.indexing_semaphore = asyncio.Semaphore(MAX_CONCURRENT_INDEXING)

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

        self.logger.info(f"Waiting for {len(futures_to_wait)} active tasks to complete (timeout: {SHUTDOWN_TASK_TIMEOUT}s)")

        completed = 0
        timed_out = 0
        errored = 0

        for future in futures_to_wait:
            try:
                future.result(timeout=SHUTDOWN_TASK_TIMEOUT)
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

            if not self.consumer:
                await self.initialize()

            self.consume_task = asyncio.create_task(self.__consume_loop())
            self.logger.info(
                f"Started Kafka consumer task with parsing_slots={MAX_CONCURRENT_PARSING}, "
                f"indexing_slots={MAX_CONCURRENT_INDEXING}, max_pending_tasks={MAX_PENDING_INDEXING_TASKS}"
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

        if active_count >= MAX_PENDING_INDEXING_TASKS:
            # Pause partitions that aren't already paused
            assigned = self.consumer.assignment()
            not_paused = assigned - self.consumer.paused()
            if not_paused:
                self.consumer.pause(*not_paused)
            if not self._backpressure_logged:
                self.logger.warning(
                    f"Backpressure engaged: {active_count} active tasks queued; "
                    f"pausing Kafka partition reads at cap {MAX_PENDING_INDEXING_TASKS}"
                )
                self._backpressure_logged = True
        else:
            # Resume any paused partitions
            paused = self.consumer.paused()
            if paused:
                self.consumer.resume(*paused)
            if self._backpressure_logged:
                self.logger.info(
                    f"Backpressure cleared: active tasks back to {active_count}/{MAX_PENDING_INDEXING_TASKS}"
                )
                self._backpressure_logged = False

    async def __consume_loop(self) -> None:
        """Main consumption loop with dual semaphore control"""
        try:
            self.logger.info("Starting Kafka consumer loop")
            while self.running:
                try:
                    self.__apply_backpressure()


                    message_batch = await self.consumer.getmany(timeout_ms=1000, max_records=1)  # type: ignore

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

    async def __process_message_wrapper(self, message: ConsumerRecord) -> bool:
        """Wrapper to handle async task cleanup and semaphore release based on yielded events.

        Iterates over events yielded by the message handler:
        - 'parsing_complete': releases parsing semaphore
        - 'indexing_complete': releases indexing semaphore

        Ensures semaphores are released even on error via finally block.
        """
        topic = message.topic
        partition = message.partition
        offset = message.offset
        message_id = f"{topic}-{partition}-{offset}"

        parsing_held = False
        indexing_held = False

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
                return False

            if self.message_handler:
                async for event in self.message_handler(parsed_message):
                    if event.event == IndexingEvent.PARSING_COMPLETE and parsing_held and self.parsing_semaphore:
                        self.parsing_semaphore.release()
                        parsing_held = False
                        self.logger.debug(f"Released parsing semaphore for {message_id}")
                    elif event.event == IndexingEvent.INDEXING_COMPLETE and indexing_held and self.indexing_semaphore:
                        self.indexing_semaphore.release()
                        indexing_held = False
                        self.logger.debug(f"Released indexing semaphore for {message_id}")
            else:
                self.logger.error(f"No message handler available for {message_id}")
                return False

            return True

        except Exception as e:
            self.logger.error(f"Error in process_message_wrapper for {message_id}: {e}")
            return False
        finally:
            # Ensure semaphores are released even on error
            if parsing_held and self.parsing_semaphore:
                self.parsing_semaphore.release()
                self.logger.debug(f"Released parsing semaphore in finally for {message_id}")

            if indexing_held and self.indexing_semaphore:
                self.indexing_semaphore.release()
                self.logger.debug(f"Released indexing semaphore in finally for {message_id}")


