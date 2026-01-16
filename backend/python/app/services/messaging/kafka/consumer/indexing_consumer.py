import asyncio
import concurrent.futures
import json
import os
from concurrent.futures import Future, ThreadPoolExecutor
from logging import Logger
from typing import Any, AsyncGenerator, Callable, Dict, Optional, Set

from aiokafka import AIOKafkaConsumer  # type: ignore

from app.services.messaging.interface.consumer import IMessagingConsumer
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig

# Concurrency control settings - read from environment variables
MAX_CONCURRENT_PARSING = int(os.getenv('MAX_CONCURRENT_PARSING', '5'))
MAX_CONCURRENT_INDEXING = int(os.getenv('MAX_CONCURRENT_INDEXING', '100'))
SHUTDOWN_TASK_TIMEOUT = 30.0


class IndexingEvent:
    """Event types for pipeline phase transitions"""
    PARSING_COMPLETE = "parsing_complete"
    INDEXING_COMPLETE = "indexing_complete"


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
        self.consumer: Optional[AIOKafkaConsumer] = None
        self.running = False
        self.kafka_config = kafka_config
        self.consume_task = None
        # Worker thread infrastructure
        self.worker_executor: Optional[ThreadPoolExecutor] = None
        self.worker_loop: Optional[asyncio.AbstractEventLoop] = None
        # Dual semaphores for parsing and indexing phases (created in worker thread)
        self.parsing_semaphore: Optional[asyncio.Semaphore] = None
        self.indexing_semaphore: Optional[asyncio.Semaphore] = None
        self.message_handler: Optional[Callable[[Dict[str, Any]], AsyncGenerator[Dict[str, Any], None]]] = None
        self.active_tasks: Set[Future] = set()  # Changed to Future for worker thread tasks

    @staticmethod
    def kafka_config_to_dict(kafka_config: KafkaConsumerConfig) -> Dict[str, Any]:
        """Convert KafkaConsumerConfig dataclass to dictionary format for aiokafka consumer"""
        return {
            'bootstrap_servers': ",".join(kafka_config.bootstrap_servers),
            'group_id': kafka_config.group_id,
            'auto_offset_reset': kafka_config.auto_offset_reset,
            'enable_auto_commit': kafka_config.enable_auto_commit,
            'client_id': kafka_config.client_id,
            'topics': kafka_config.topics
        }

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

        # Create executor with single worker thread
        self.worker_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="indexing-worker")
        self.worker_executor.submit(run_worker_loop)
        self.logger.info("Worker thread started")

    async def initialize(self) -> None:
        """Initialize the Kafka consumer and worker thread"""
        consumer = None
        try:
            if not self.kafka_config:
                raise ValueError("Kafka configuration is not valid")

            # Start worker thread first
            self.__start_worker_thread()

            # Wait a moment for worker thread to initialize
            for _ in range(600):  # Wait up to 60 seconds
                if self.worker_loop and self.worker_loop.is_running():
                    break
                await asyncio.sleep(0.1)
            else:
                raise RuntimeError("Worker thread event loop not initialized in time")

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
        """Stop the worker thread and its event loop"""
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

    async def start(
        self,
        message_handler: Callable[[Dict[str, Any]], AsyncGenerator[Dict[str, Any], None]]  # type: ignore
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
            self.logger.info(f"Started Kafka consumer task with parsing_slots={MAX_CONCURRENT_PARSING}, indexing_slots={MAX_CONCURRENT_INDEXING}")
        except Exception as e:
            self.logger.error(f"Failed to start Kafka consumer: {str(e)}")
            raise

    async def stop(self, message_handler: Optional[Callable[[Dict[str, Any]], AsyncGenerator[Dict[str, Any], None]]] = None) -> None:  # type: ignore
        """Stop consuming messages"""
        self.running = False

        if self.consume_task:
            self.consume_task.cancel()
            try:
                await self.consume_task
            except asyncio.CancelledError:
                pass

        # Wait for active tasks to complete (with timeout)
        if self.active_tasks:
            self.logger.info(f"Waiting for {len(self.active_tasks)} active tasks to complete...")
            loop = asyncio.get_running_loop()
            _, not_done = await loop.run_in_executor(
                None,
                lambda: concurrent.futures.wait(
                    self.active_tasks,
                    timeout=SHUTDOWN_TASK_TIMEOUT,
                    return_when=concurrent.futures.ALL_COMPLETED
                )
            )
            if not_done:
                self.logger.warning(f"{len(not_done)} tasks did not complete within timeout")

        if self.consumer:
            await self.consumer.stop()
            self.logger.info("✅ Kafka consumer stopped")

        # Stop worker thread
        self.__stop_worker_thread()

    def is_running(self) -> bool:
        """Check if consumer is running"""
        return self.running

    async def __consume_loop(self) -> None:
        """Main consumption loop with dual semaphore control"""
        try:
            self.logger.info("Starting Kafka consumer loop")
            while self.running:
                try:
                    message_batch = await self.consumer.getmany(timeout_ms=1000, max_records=1)  # type: ignore

                    if not message_batch:
                        await asyncio.sleep(0.1)
                        continue

                    for _, messages in message_batch.items():
                        for message in messages:
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
                    await asyncio.sleep(1)

        except Exception as e:
            self.logger.error(f"Fatal error in consume_messages: {e}")



    def __parse_message(self, message) -> Optional[Dict[str, Any]]:
        """Parse the Kafka message value into a dictionary.

        Handles bytes decoding, JSON parsing, and double-encoded JSON.

        Returns:
            Parsed message dictionary or None if parsing fails.
        """
        message_id = f"{message.topic}-{message.partition}-{message.offset}"
        message_value = message.value

        try:
            if isinstance(message_value, bytes):
                message_value = message_value.decode("utf-8")
                self.logger.debug(f"Decoded bytes message for {message_id}")

            if isinstance(message_value, str):
                try:
                    parsed_message = json.loads(message_value)
                    # Handle double-encoded JSON
                    if isinstance(parsed_message, str):
                        parsed_message = json.loads(parsed_message)
                        self.logger.debug("Handled double-encoded JSON message")

                    self.logger.debug(
                        f"Parsed message {message_id}: type={type(parsed_message)}"
                    )
                    return parsed_message
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

    async def __start_processing_task(self, message) -> None:
        """Start a new task for processing a message with semaphore control.
        Submits the task to the worker thread's event loop instead of the main loop.
        """
        if not self.worker_loop:
            self.logger.error("Worker loop not initialized, cannot process message")
            return

        # Submit coroutine to worker thread's event loop
        future = asyncio.run_coroutine_threadsafe(
            self.__process_message_wrapper(message),
            self.worker_loop
        )
        self.active_tasks.add(future)
        self.__cleanup_completed_tasks()

    async def __process_message_wrapper(self, message) -> None:
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

        needs_parsing_release = True
        needs_indexing_release = True

        if not self.parsing_semaphore or not self.indexing_semaphore:
            self.logger.error(f"Semaphores not initialized for {message_id}")
            return

        try:
            await self.parsing_semaphore.acquire()
            needs_parsing_release = False

            await self.indexing_semaphore.acquire()
            needs_indexing_release = False

            parsed_message = self.__parse_message(message)
            if parsed_message is None:
                self.logger.warning(f"Failed to parse message {message_id}, skipping")
                return

            if self.message_handler:
                async for event in self.message_handler(parsed_message):
                    event_type = event.get("event")

                    if event_type == IndexingEvent.PARSING_COMPLETE and not needs_parsing_release and self.parsing_semaphore:
                        self.parsing_semaphore.release()
                        needs_parsing_release = True
                        self.logger.debug(f"Released parsing semaphore for {message_id}")
                    elif event_type == IndexingEvent.INDEXING_COMPLETE and not needs_indexing_release and self.indexing_semaphore:
                        self.indexing_semaphore.release()
                        needs_indexing_release = True
                        self.logger.debug(f"Released indexing semaphore for {message_id}")
            else:
                self.logger.error(f"No message handler available for {message_id}")

        except Exception as e:
            self.logger.error(f"Error in process_message_wrapper for {message_id}: {e}")
        finally:
            # Ensure semaphores are released even on error
            if not needs_parsing_release and self.parsing_semaphore:
                self.parsing_semaphore.release()
                self.logger.debug(f"Released parsing semaphore in finally for {message_id}")

            if not needs_indexing_release and self.indexing_semaphore:
                self.indexing_semaphore.release()
                self.logger.debug(f"Released indexing semaphore in finally for {message_id}")


    def __cleanup_completed_tasks(self) -> None:
        """Remove completed tasks from the active tasks set"""
        done_tasks = {future for future in self.active_tasks if future.done()}
        self.active_tasks -= done_tasks

        for future in done_tasks:
            try:
                exception = future.exception()
                if exception:
                    self.logger.error(f"Task completed with exception: {exception}")
            except Exception as e:
                self.logger.error(f"Error checking task exception: {e}")

