import asyncio
import json
from logging import Logger
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set

from aiokafka import AIOKafkaConsumer, TopicPartition  # type: ignore

from app.services.messaging.interface.consumer import IMessagingConsumer
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.rate_limiter.rate_limiter import RateLimiter

# Concurrency control settings
MAX_CONCURRENT_TASKS = 5  # Maximum number of messages to process concurrently
RATE_LIMIT_PER_SECOND = 2  # Maximum number of new tasks to start per second

class KafkaMessagingConsumer(IMessagingConsumer):
    """Kafka implementation of messaging consumer"""

    def __init__(self,
                logger: Logger,
                kafka_config: KafkaConsumerConfig,
                rate_limiter: Optional[RateLimiter] = None) -> None:
        self.logger = logger
        self.consumer: Optional[AIOKafkaConsumer] = None
        self.running = False
        self.kafka_config = kafka_config
        self.processed_messages: Dict[str, List[int]] = {}
        self.consume_task = None
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)
        self.message_handler = None
        self.rate_limiter = rate_limiter
        self.active_tasks: Set[asyncio.Task] = set()
        self.max_concurrent_tasks = MAX_CONCURRENT_TASKS

    @staticmethod
    def kafka_config_to_dict(kafka_config: KafkaConsumerConfig) -> Dict[str, Any]:
        """Convert KafkaConsumerConfig dataclass to dictionary format for aiokafka consumer"""
        return {
            'bootstrap_servers': ",".join(kafka_config.bootstrap_servers),
            'group_id': kafka_config.group_id,
            'auto_offset_reset': kafka_config.auto_offset_reset,
            'enable_auto_commit': kafka_config.enable_auto_commit,
            'client_id': kafka_config.client_id,
            'topics': kafka_config.topics  # Include topics in the dictionary
        }

    # implementing abstract methods from IMessagingConsumer
    async def initialize(self) -> None:
        """Initialize the Kafka consumer"""
        try:
            if not self.kafka_config:
                raise ValueError("Kafka configuration is not valid")

            # Convert KafkaConsumerConfig to dictionary format for aiokafka
            kafka_dict = KafkaMessagingConsumer.kafka_config_to_dict(self.kafka_config)
            topics = kafka_dict.pop('topics')

            # Initialize consumer with aiokafka
            self.consumer = AIOKafkaConsumer(
                *topics,
                **kafka_dict
            )

            await self.consumer.start() # type: ignore
            self.logger.info("Successfully initialized aiokafka consumer")
        except Exception as e:
            self.logger.error(f"Failed to create consumer: {e}")
            raise

    # implementing abstract methods from IMessagingConsumer
    async def cleanup(self) -> None:
        """Stop the Kafka consumer and clean up resources"""
        try:
            if self.consumer:
                await self.consumer.stop()
                self.logger.info("Kafka consumer stopped")
        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")

    # implementing abstract methods from IMessagingConsumer
    async def start(
        self,
        message_handler: Callable[[Dict[str, Any]], Awaitable[bool]]
    ) -> None:
        """Start consuming messages with the provided handler"""
        try:
            self.running = True
            self.message_handler = message_handler

            # initialize consumer
            if not self.consumer:
                await self.initialize()

            # create a task for consuming messages
            self.consume_task = asyncio.create_task(self.__consume_loop())
            self.logger.info("Started Kafka consumer task")
        except Exception as e:
            self.logger.error(f"Failed to start Kafka consumer: {str(e)}")
            raise

    # implementing abstract methods from IMessagingConsumer
    async def stop(self, message_handler: Optional[Callable[[Dict[str, Any]], Awaitable[bool]]] = None) -> None:
        """Stop consuming messages"""
        self.running = False
        # run the message handler
        if self.message_handler:
            await self.message_handler(None) # type: ignore

        if self.consume_task:
            self.consume_task.cancel()
            try:
                await self.consume_task
            except asyncio.CancelledError:
                pass

        if self.consumer:
            await self.consumer.stop()
            self.logger.info("✅ Kafka consumer stopped")

    # implementing abstract methods from IMessagingConsumer
    def is_running(self) -> bool:
        """Check if consumer is running"""
        return self.running

    async def __process_message(self, message) -> bool:
        """Process incoming Kafka messages using the provided handler"""
        message_id = None
        try:
            message_id = f"{message.topic}-{message.partition}-{message.offset}"
            self.logger.debug(f"Processing message {message_id}")

            if self.__is_message_processed(message_id):
                self.logger.info(f"Message {message_id} already processed, skipping")
                return True

            # Message decoding and parsing
            message_value = message.value
            parsed_message = None

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
                    except json.JSONDecodeError as e:
                        self.logger.error(
                            f"JSON parsing failed for message {message_id}: {str(e)}\n"
                            f"Raw message: {message_value[:1000]}..."
                        )
                        return False
                else:
                    self.logger.error(
                        f"Unexpected message value type for {message_id}: {type(message_value)}"
                    )
                    return False

            except UnicodeDecodeError as e:
                self.logger.error(
                    f"Failed to decode message {message_id}: {str(e)}\n"
                    f"Raw bytes: {message_value[:100]}..."
                )
                return False

            # Call the provided message handler
            if self.message_handler and parsed_message:
                try:
                    return await self.message_handler(parsed_message)
                except Exception as e:
                    self.logger.error(
                        f"Error in message handler for {message_id}: {str(e)}",
                        exc_info=True,
                    )
                    return False
            else:
                self.logger.error(f"No message handler available for {message_id}")
                return False

        except Exception as e:
            self.logger.error(
                f"Unexpected error processing message {message_id if message_id else 'unknown'}: {str(e)}",
                exc_info=True,
            )
            return False
        finally:
            if message_id:
                self.__mark_message_processed(message_id)

    async def __consume_loop(self) -> None:
        """Main consumption loop"""
        try:
            self.logger.info("Starting Kafka consumer loop")
            while self.running:
                try:
                    # Get messages asynchronously with timeout
                    message_batch = await self.consumer.getmany(timeout_ms=1000, max_records=1) # type: ignore

                    if not message_batch:
                        await asyncio.sleep(0.1)
                        continue

                    # Process messages from all topic partitions
                    for topic_partition, messages in message_batch.items():
                        for message in messages:
                            try:
                                self.logger.info(f"Received message: topic={message.topic}, partition={message.partition}, offset={message.offset}")
                                # TODO: Remove this not needed
                                if self.rate_limiter:
                                    await self.__start_processing_task(message, topic_partition)
                                else:
                                    success = await self.__process_message(message)
                                    if success:
                                        # Commit the offset for this message
                                        # Tells Kafka that this message has been successfully processed
                                        await self.consumer.commit({topic_partition: message.offset + 1}) # type: ignore
                                        self.logger.info(
                                            f"Committed offset for topic-partition {message.topic}-{message.partition} at offset {message.offset}"
                                        )
                                    else:
                                        self.logger.warning(f"Failed to process message at offset {message.offset}")

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
        finally:
            await self.cleanup()

    def __is_message_processed(self, message_id: str) -> bool:
        """Check if a message has already been processed."""
        topic_partition = "-".join(message_id.split("-")[:-1])
        offset = int(message_id.split("-")[-1])
        return (
            topic_partition in self.processed_messages
            and offset in self.processed_messages[topic_partition]
        )

    def __mark_message_processed(self, message_id: str) -> None:
        """Mark a message as processed."""
        topic_partition = "-".join(message_id.split("-")[:-1])
        offset = int(message_id.split("-")[-1])
        if topic_partition not in self.processed_messages:
            self.processed_messages[topic_partition] = []
        self.processed_messages[topic_partition].append(offset)

    async def __start_processing_task(self, message, topic_partition: TopicPartition) -> None:
        """Start a new task for processing a message with semaphore control"""
        # Wait for the rate limiter
        await self.rate_limiter.wait() # type: ignore

        # Wait for a semaphore slot to become available
        await self.semaphore.acquire()

        # Create and start a new task
        task = asyncio.create_task(self.__process_message_wrapper(message, topic_partition))
        self.active_tasks.add(task)

        # Clean up completed tasks
        self.__cleanup_completed_tasks()

        # Log current task count
        self.logger.debug(
            f"Active tasks: {len(self.active_tasks)}/{self.max_concurrent_tasks}"
        )

    async def __process_message_wrapper(self, message, topic_partition: TopicPartition) -> None:
        """Wrapper to handle async task cleanup and semaphore release"""
        # Extract message identifiers for logging
        topic = message.topic
        partition = message.partition
        offset = message.offset
        message_id = f"{topic}-{partition}-{offset}"
        try:
            success = await self.__process_message(message)
            if success:
                if self.consumer:
                    await self.consumer.commit({topic_partition: message.offset + 1})
                    self.logger.info(
                        f"Committed offset for {message_id} in background task."
                    )
            else:
                self.logger.warning(
                    f"Processing failed for {message_id}, offset will not be committed."
                )
        except Exception as e:
            self.logger.error(f"Error in process_message_wrapper for {message_id}: {e}")
        finally:
            # Release the semaphore to allow a new task to start
            self.semaphore.release()

    def __cleanup_completed_tasks(self) -> None:
        """Remove completed tasks from the active tasks set"""
        done_tasks = {task for task in self.active_tasks if task.done()}
        self.active_tasks -= done_tasks

        # Check for exceptions in completed tasks
        for task in done_tasks:
            if task.exception():
                self.logger.error(f"Task completed with exception: {task.exception()}")
