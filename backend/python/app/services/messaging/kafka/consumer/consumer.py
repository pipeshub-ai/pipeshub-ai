import asyncio
import json
import ssl
from logging import Logger
from typing import TYPE_CHECKING, Any, Optional

from aiokafka import AIOKafkaConsumer  # type: ignore

from app.services.messaging.config import MessageHandler, StreamMessage, messaging_env
from app.services.messaging.error_classifier import MessageErrorClassifier, MessageErrorType
from app.services.messaging.interface.consumer import IMessagingConsumer
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.utils.request_context import (
    context_from_envelope,
    reset_context,
    set_context,
)

if TYPE_CHECKING:
    from app.services.messaging.retry_manager import RetryManager


class KafkaMessagingConsumer(IMessagingConsumer):
    """Kafka implementation of messaging consumer.

    Uses Redis-based RetryManager for persistent retry tracking across restarts.
    Messages are processed sequentially; failed messages are not committed and
    will be redelivered by Kafka when no new messages arrive (idle-based retry).
    """

    def __init__(
        self,
        logger: Logger,
        kafka_config: KafkaConsumerConfig,
        retry_manager: Optional["RetryManager"] = None,
    ) -> None:
        self.logger = logger
        self.consumer: Optional[AIOKafkaConsumer] = None
        self.running = False
        self.kafka_config = kafka_config
        self.processed_messages: dict[str, list[int]] = {}
        self.consume_task = None
        self.message_handler = None
        self.retry_manager = retry_manager

    @staticmethod
    def kafka_config_to_dict(kafka_config: KafkaConsumerConfig) -> dict[str, Any]:
        """Convert KafkaConsumerConfig dataclass to dictionary format for aiokafka consumer"""
        config: dict[str, Any] = {
            'bootstrap_servers': ",".join(kafka_config.bootstrap_servers),
            'group_id': kafka_config.group_id,
            'auto_offset_reset': kafka_config.auto_offset_reset,
            'enable_auto_commit': kafka_config.enable_auto_commit,
            'client_id': kafka_config.client_id,
            'topics': kafka_config.topics  # Include topics in the dictionary
        }

        # Add SSL/SASL configuration
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
        message_handler: MessageHandler,
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
    async def stop(self, message_handler: Optional[MessageHandler] = None) -> None:
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

    # implementing abstract methods from IMessagingConsumer
    def is_running(self) -> bool:
        """Check if consumer is running"""
        return self.running

    async def __process_message(self, message) -> tuple[bool, Optional[Exception]]:
        """Process incoming Kafka messages using the provided handler.

        Returns:
            Tuple of (success, exception) where exception is set on failure
            for classification by the caller.
        """
        message_id = None
        try:
            message_id = f"{message.topic}-{message.partition}-{message.offset}"
            self.logger.debug(f"Processing message {message_id}")

            if self.__is_message_processed(message_id):
                self.logger.info(f"Message {message_id} already processed, skipping")
                return True, None

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
                        return False, e
                else:
                    self.logger.error(
                        f"Unexpected message value type for {message_id}: {type(message_value)}"
                    )
                    return False, ValueError(f"Unexpected message type: {type(message_value)}")

            except UnicodeDecodeError as e:
                self.logger.error(
                    f"Failed to decode message {message_id}: {str(e)}\n"
                    f"Raw bytes: {message_value[:100]}..."
                )
                return False, e

            # Carry the producer's trace id into consumer-side logs.
            if self.message_handler and parsed_message:
                envelope = parsed_message if isinstance(parsed_message, dict) else {}
                ctx = context_from_envelope(envelope)
                token = set_context(ctx.root_id)
                try:
                    stream_message = StreamMessage(**parsed_message)
                    result = await self.message_handler(stream_message)
                    return result, None
                except Exception as e:
                    self.logger.error(
                        f"Error in message handler for {message_id}: {str(e)}",
                        exc_info=True,
                    )
                    return False, e
                finally:
                    reset_context(token)
            else:
                self.logger.error(f"No message handler available for {message_id}")
                return False, ValueError("No message handler available")

        except Exception as e:
            self.logger.error(
                f"Unexpected error processing message {message_id if message_id else 'unknown'}: {str(e)}",
                exc_info=True,
            )
            return False, e

    async def __consume_loop(self) -> None:
        """Main consumption loop with Redis-based retry tracking.

        New messages are processed first. When getmany() returns empty (idle),
        Kafka will redeliver uncommitted messages on the next poll, enabling
        retry of failed messages without blocking new ones.
        """
        try:
            self.logger.info("Starting Kafka consumer loop")
            while self.running:
                try:
                    # Get messages asynchronously with timeout
                    message_batch = await self.consumer.getmany(
                        timeout_ms=messaging_env.message_timeout_ms,
                        max_records=messaging_env.message_batch_size_simple,
                    )  # type: ignore

                    if not message_batch:
                        await asyncio.sleep(0.1)
                        continue

                    # Process messages from all topic partitions
                    for topic_partition, messages in message_batch.items():
                        for message in messages:
                            try:
                                self.logger.info(
                                    f"Received message: topic={message.topic}, "
                                    f"partition={message.partition}, offset={message.offset}"
                                )
                                message_id = f"{message.topic}-{message.partition}-{message.offset}"

                                success, exc = await self.__process_message(message)

                                should_commit = False
                                if success:
                                    should_commit = True
                                    self.logger.info(f"Message {message_id} processed successfully")
                                    # Clear retry tracking on success
                                    if self.retry_manager:
                                        await self.retry_manager.clear(message_id)
                                else:
                                    # Classify exception to determine commit behavior
                                    error_type = MessageErrorType.TRANSIENT
                                    if exc:
                                        error_type = MessageErrorClassifier.classify_by_exception(exc)

                                    if error_type == MessageErrorType.TERMINAL:
                                        # Terminal error: commit immediately (no retry)
                                        should_commit = True
                                        self.logger.warning(
                                            f"Terminal error for {message_id}: {type(exc).__name__}. "
                                            "Committing without retry."
                                        )
                                        if self.retry_manager:
                                            await self.retry_manager.clear(message_id)
                                    elif self.retry_manager:
                                        # Transient error: check retry count
                                        count, should_dead_letter = await self.retry_manager.increment_and_check(
                                            message_id, messaging_env.max_delivery_attempts
                                        )
                                        if should_dead_letter:
                                            should_commit = True
                                            self.logger.warning(
                                                f"Dead-lettering {message_id} after {count} attempts"
                                            )
                                            await self.retry_manager.clear(message_id)
                                        else:
                                            self.logger.warning(
                                                f"Message {message_id} failed (attempt {count}/"
                                                f"{messaging_env.max_delivery_attempts}), will retry"
                                            )
                                    else:
                                        # No retry manager: always commit to avoid infinite loop
                                        should_commit = True
                                        self.logger.warning(
                                            f"Message {message_id} failed, no retry manager, committing"
                                        )

                                if should_commit:
                                    await self.consumer.commit(
                                        {topic_partition: message.offset + 1}
                                    )  # type: ignore
                                    self.logger.info(
                                        f"Committed offset for {message.topic}-{message.partition} "
                                        f"at offset {message.offset}"
                                    )
                                    # Mark as processed only when we commit (prevents skipped retries)
                                    self.__mark_message_processed(message_id)
                                else:
                                    # Transient failure - stop processing this partition to prevent cumulative commits
                                    self.logger.warning(
                                        f"Partition {message.topic}-{message.partition} processing "
                                        f"stopped at offset {message.offset} due to retryable failure. "
                                        f"Subsequent messages in this batch will be retried."
                                    )
                                    break  # Exit inner loop for this partition, prevent cumulative commit

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
