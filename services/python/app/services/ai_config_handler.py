import asyncio
import json
from typing import Dict, List

from confluent_kafka import Consumer, KafkaError

from app.config.configuration_service import KafkaConfig, config_node_constants
from app.modules.retrieval.retrieval_service import RetrievalService


class RetrievalAiConfigHandler:
    def __init__(self, logger, config_service, retrieval_service: RetrievalService):
        """Initialize the LLM config handler with required services

        Args:
            config_service: Configuration service instance
            retrieval_service: RetrievalService instance to update
        """
        self.consumer = None
        self.running = False
        self.logger = logger
        self.config_service = config_service

        self.retrieval_service = retrieval_service
        self.processed_messages: Dict[str, List[int]] = {}

    async def create_consumer(self):
        """Initialize the Kafka consumer"""
        try:

            async def get_kafka_config():
                kafka_config = await self.config_service.get_config(
                    config_node_constants.KAFKA.value
                )
                brokers = kafka_config["brokers"]

                return {
                    "bootstrap.servers": ",".join(brokers),
                    "group.id": "llm_config_consumer_group",
                    "auto.offset.reset": "earliest",
                    "enable.auto.commit": True,
                    "isolation.level": "read_committed",
                    "enable.partition.eof": False,
                    "client.id": KafkaConfig.CLIENT_ID_LLM.value,
                }

            KAFKA_CONFIG = await get_kafka_config()

            self.consumer = Consumer(KAFKA_CONFIG)
            # Subscribe to entity-events topic
            self.consumer.subscribe(["entity-events"])
            self.logger.info("Successfully subscribed to topic: entity-events")
        except Exception as e:
            self.logger.error(f"Failed to create consumer: {e}")
            raise

    def is_message_processed(self, message_id: str) -> bool:
        """Check if a message has already been processed."""
        topic_partition = "-".join(message_id.split("-")[:-1])
        offset = int(message_id.split("-")[-1])
        return (
            topic_partition in self.processed_messages
            and offset in self.processed_messages[topic_partition]
        )

    def mark_message_processed(self, message_id: str):
        """Mark a message as processed."""
        topic_partition = "-".join(message_id.split("-")[:-1])
        offset = int(message_id.split("-")[-1])
        if topic_partition not in self.processed_messages:
            self.processed_messages[topic_partition] = []
        self.processed_messages[topic_partition].append(offset)

    async def handle_llm_configured(self) -> bool:
        """Handle LLM configuration update

        Args:
            payload (dict): Event payload containing credentialsRoute

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            self.logger.info("📥 Processing LLM configured event")

            await self.retrieval_service.get_llm_instance()

            self.logger.info(
                "✅ Successfully updated LLM configuration in all services"
            )
            return True

        except Exception as e:
            self.logger.error(f"❌ Failed to fetch AI configuration: {str(e)}")
            return False

    async def handle_embedding_model_configured(self) -> bool:
        try:
            self.logger.info("📥 Processing embedding model configured event")

            await self.retrieval_service.get_embedding_model_instance()
            self.logger.info("✅ Successfully updated embedding model in all services")
            return True
        except Exception as e:
            self.logger.error(f"❌ Failed to fetch embedding model: {str(e)}")
            return False

    async def process_message(self, message):
        """Process incoming Kafka messages"""
        message_id = None
        try:
            message_id = f"{message.topic()}-{message.partition()}-{message.offset()}"
            self.logger.debug(f"Processing AI config message {message_id}")

            if self.is_message_processed(message_id):
                self.logger.info(f"Message {message_id} already processed, skipping")
                return True

            message_value = message.value()
            value = None
            event_type = None

            # Message decoding and parsing
            try:
                if isinstance(message_value, bytes):
                    message_value = message_value.decode("utf-8")
                    self.logger.debug(f"Decoded bytes message for {message_id}")

                if isinstance(message_value, str):
                    try:
                        value = json.loads(message_value)
                        # Handle double-encoded JSON
                        if isinstance(value, str):
                            value = json.loads(value)
                            self.logger.debug("Handled double-encoded JSON message")

                        event_type = value.get("eventType")
                        self.logger.debug(
                            f"Parsed message {message_id}: type={type(value)}, event_type={event_type}"
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

            # Handle AI configuration events
            try:
                if event_type == "llmConfigured":
                    self.logger.info(
                        f"Processing LLM configuration update: {message_id}"
                    )
                    return await self.handle_llm_configured()
                elif event_type == "embeddingModelConfigured":
                    self.logger.info(
                        f"Processing embedding model configuration update: {message_id}"
                    )
                    return await self.handle_embedding_model_configured()
                else:
                    self.logger.warning(
                        f"Unhandled event type '{event_type}' in message {message_id}"
                    )
                    return False

            except asyncio.TimeoutError:
                self.logger.error(
                    f"Timeout while processing {event_type} configuration in message {message_id}"
                )
                return False
            except ValueError as e:
                self.logger.error(
                    f"Validation error processing {event_type} configuration: {str(e)}"
                )
                return False
            except Exception as e:
                self.logger.error(
                    f"Error processing {event_type} configuration in message {message_id}: {str(e)}",
                    exc_info=True,
                )
                return False

        except Exception as e:
            self.logger.error(
                f"Unexpected error processing message {message_id if message_id else 'unknown'}: {str(e)}",
                exc_info=True,
            )
            return False
        finally:
            if message_id:
                self.mark_message_processed(message_id)

    async def consume_messages(self):
        """Main consumption loop."""
        try:
            self.logger.info("Starting LLM config consumer loop")
            while self.running:
                try:
                    message = self.consumer.poll(1.0)

                    if message is None:
                        await asyncio.sleep(0.1)
                        continue

                    if message.error():
                        error_code = message.error().code()
                        if error_code == KafkaError._PARTITION_EOF:
                            self.logger.debug("Reached end of partition")
                            continue
                        else:
                            self.logger.error(
                                f"Kafka error: {message.error()}, Code: {error_code}"
                            )
                            continue

                    try:
                        success = await self.process_message(message)
                        if success:
                            self.consumer.commit(message)
                            self.logger.debug("Successfully committed message")
                    except Exception as e:
                        self.logger.error(
                            f"Failed to process/commit message: {str(e)}", exc_info=True
                        )
                        await asyncio.sleep(1)

                except asyncio.CancelledError:
                    self.logger.info("LLM config consumer task cancelled")
                    break
                except Exception as e:
                    self.logger.error(
                        f"Error in message consumption loop: {str(e)}", exc_info=True
                    )
                    await asyncio.sleep(1)

        except Exception as e:
            self.logger.error(
                f"Fatal error in consume_messages: {str(e)}", exc_info=True
            )
        finally:
            if self.consumer:
                try:
                    self.consumer.close()
                    self.logger.info("LLM config consumer closed successfully")
                except Exception as e:
                    self.logger.error(f"Error closing consumer: {str(e)}")

    async def start(self):
        """Start the consumer."""
        self.running = True
        await self.create_consumer()

    def stop(self):
        """Stop the consumer."""
        self.running = False
