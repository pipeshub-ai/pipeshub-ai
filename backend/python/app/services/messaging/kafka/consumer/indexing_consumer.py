import asyncio
import json
import os
from logging import Logger
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, Set

from aiokafka import AIOKafkaConsumer, TopicPartition  # type: ignore

from app.services.messaging.interface.consumer import IMessagingConsumer
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig

# Concurrency control settings - read from environment variables
MAX_CONCURRENT_PARSING = int(os.getenv('MAX_CONCURRENT_PARSING', '5'))
MAX_CONCURRENT_INDEXING = int(os.getenv('MAX_CONCURRENT_INDEXING', '100'))


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
        self.processed_messages: Dict[str, List[int]] = {}
        self.consume_task = None
        # Dual semaphores for parsing and indexing phases
        self.parsing_semaphore = asyncio.Semaphore(MAX_CONCURRENT_PARSING)
        self.indexing_semaphore = asyncio.Semaphore(MAX_CONCURRENT_INDEXING)
        self.message_handler: Optional[Callable[[Dict[str, Any]], AsyncGenerator[Dict[str, Any], None]]] = None
        self.active_tasks: Set[asyncio.Task] = set()
        self.max_concurrent_parsing = MAX_CONCURRENT_PARSING
        self.max_concurrent_indexing = MAX_CONCURRENT_INDEXING

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

    async def initialize(self) -> None:
        """Initialize the Kafka consumer"""
        try:
            if not self.kafka_config:
                raise ValueError("Kafka configuration is not valid")

            kafka_dict = IndexingKafkaConsumer.kafka_config_to_dict(self.kafka_config)
            topics = kafka_dict.pop('topics')

            self.consumer = AIOKafkaConsumer(
                *topics,
                **kafka_dict
            )

            await self.consumer.start()  # type: ignore
            self.logger.info("Successfully initialized aiokafka consumer for indexing")
        except Exception as e:
            self.logger.error(f"Failed to create consumer: {e}")
            raise

    async def cleanup(self) -> None:
        """Stop the Kafka consumer and clean up resources"""
        try:
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

        if self.consumer:
            await self.consumer.stop()
            self.logger.info("✅ Kafka consumer stopped")

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

                    for topic_partition, messages in message_batch.items():
                        for message in messages:
                            try:
                                self.logger.info(f"Received message: topic={message.topic}, partition={message.partition}, offset={message.offset}")
                                await self.__start_processing_task(message, topic_partition)

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

    async def __start_processing_task(self, message, topic_partition: TopicPartition) -> None:
        """Start a new task for processing a message with dual semaphore control.
        
        Acquires BOTH parsing and indexing semaphores before starting processing.
        This ensures that when an event is consumed, it has a guaranteed path
        through both parsing and indexing phases.
        """
        # Acquire both semaphores - ensures slots available for both phases
        await self.parsing_semaphore.acquire()
        await self.indexing_semaphore.acquire()

        self.logger.info(
            f"Semaphores acquired for message. "
            f"Parsing available: {self.parsing_semaphore._value}, "
            f"Indexing available: {self.indexing_semaphore._value}"
        )

        task = asyncio.create_task(self.__process_message_wrapper(message, topic_partition))
        self.active_tasks.add(task)

        self.__cleanup_completed_tasks()

        self.logger.debug(
            f"Active tasks: {len(self.active_tasks)}, parsing_slots_available, indexing_slots_available"
        )

    async def __process_message_wrapper(self, message, topic_partition: TopicPartition) -> None:
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

        parsing_released = False
        indexing_released = False

        try:
            parsed_message = self.__parse_message(message)
            if parsed_message is None:
                self.logger.warning(f"Failed to parse message {message_id}, committing offset to skip")
                self.__mark_message_processed(message_id)
                if self.consumer:
                    await self.consumer.commit({topic_partition: message.offset + 1})
                return

            if self.__is_message_processed(message_id):
                self.logger.info(f"Message {message_id} already processed, skipping")
                return

            if self.message_handler:
                async for event in self.message_handler(parsed_message):
                    event_type = event.get("event")

                    if event_type == IndexingEvent.PARSING_COMPLETE and not parsing_released:
                        self.parsing_semaphore.release()
                        parsing_released = True
                        self.logger.debug(f"Released parsing semaphore for {message_id}")

                    elif event_type == IndexingEvent.INDEXING_COMPLETE and not indexing_released:
                        self.indexing_semaphore.release()
                        indexing_released = True
                        self.logger.debug(f"Released indexing semaphore for {message_id}")

                self.__mark_message_processed(message_id)
                if self.consumer:
                    await self.consumer.commit({topic_partition: message.offset + 1})
                    self.logger.info(f"Committed offset for {message_id} in background task.")
            else:
                self.logger.error(f"No message handler available for {message_id}")

        except Exception as e:
            self.logger.error(f"Error in process_message_wrapper for {message_id}: {e}")
        finally:
            # Ensure semaphores are released even on error
            if not parsing_released:
                self.parsing_semaphore.release()
                self.logger.debug(f"Released parsing semaphore in finally for {message_id}")
            if not indexing_released:
                self.indexing_semaphore.release()
                self.logger.debug(f"Released indexing semaphore in finally for {message_id}")

    def __cleanup_completed_tasks(self) -> None:
        """Remove completed tasks from the active tasks set"""
        done_tasks = {task for task in self.active_tasks if task.done()}
        self.active_tasks -= done_tasks

        for task in done_tasks:
            if task.exception():
                self.logger.error(f"Task completed with exception: {task.exception()}")

