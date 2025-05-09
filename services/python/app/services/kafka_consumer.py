import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Set

import aiohttp
from confluent_kafka import Consumer, KafkaError
from jose import jwt
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config.configuration_service import (
    ConfigurationService,
    KafkaConfig,
    config_node_constants,
)
from app.config.utils.named_constants.arangodb_constants import (
    CollectionNames,
    EventTypes,
    ExtensionTypes,
    MimeTypes,
    ProgressStatus,
)
from app.exceptions.indexing_exceptions import IndexingError

# Concurrency control settings
MAX_CONCURRENT_TASKS = 5  # Maximum number of messages to process concurrently
RATE_LIMIT_PER_SECOND = 2  # Maximum number of new tasks to start per second


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=15))
async def make_api_call(signed_url_route: str, token: str) -> dict:
    """
    Make an API call with the JWT token.

    Args:
        signed_url_route (str): The route to send the request to
        token (str): The JWT token to use for authentication

    Returns:
        dict: The response from the API
    """
    try:
        async with aiohttp.ClientSession() as session:
            url = signed_url_route

            # Add the JWT to the Authorization header
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }

            # Make the request
            async with session.get(url, headers=headers) as response:
                content_type = response.headers.get("Content-Type", "").lower()

                if response.status == 200 and "application/json" in content_type:
                    data = await response.json()
                    return {"is_json": True, "data": data}
                else:
                    data = await response.read()
                    return {"is_json": False, "data": data}
    except Exception:
        raise


class KafkaConsumerManager:
    def __init__(self, logger, config_service: ConfigurationService, event_processor, redis_scheduler):
        self.logger = logger
        self.consumer = None
        self.running = False
        self.event_processor = event_processor
        self.config_service = config_service
        # Concurrency control
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)
        self.active_tasks: Set[asyncio.Task] = set()
        self.rate_limiter = RateLimiter(RATE_LIMIT_PER_SECOND)

        # Message tracking
        self.processed_messages: Dict[str, List[int]] = {}

        self.redis_scheduler = redis_scheduler
        # Create task for processing scheduled updates
        self.scheduled_update_task = None

    async def create_consumer(self):
        try:

            async def get_kafka_config():
                kafka_config = await self.config_service.get_config(
                    config_node_constants.KAFKA.value
                )
                brokers = kafka_config["brokers"]

                return {
                    "bootstrap.servers": ",".join(brokers),
                    "group.id": "record_consumer_group",
                    "auto.offset.reset": "earliest",
                    "enable.auto.commit": True,
                    "isolation.level": "read_committed",
                    "enable.partition.eof": False,
                    "max.poll.interval.ms": 900000,
                    "client.id": KafkaConfig.CLIENT_ID_MAIN.value,
                }

            KAFKA_CONFIG = await get_kafka_config()
            # Topic to consume from
            KAFKA_TOPIC = "record-events"

            self.consumer = Consumer(KAFKA_CONFIG)
            self.consumer.subscribe([KAFKA_TOPIC])
            self.logger.info(f"Successfully subscribed to topic: {KAFKA_TOPIC}")
        except Exception as e:
            self.logger.error(f"Failed to create consumer: {e}")
            self.logger.info(
                "Please ensure the topic 'record-events' exists on the Kafka broker"
            )
            raise

    async def process_message_wrapper(self, message):
        """Wrapper to handle async task cleanup and semaphore release"""
        # Extract message identifiers for logging
        topic = message.topic()
        partition = message.partition()
        offset = message.offset()
        message_id = f"{topic}-{partition}-{offset}"

        try:
            self.logger.info(f"Starting to process message: {message_id}")
            success = await self._process_message(message)
            self.logger.info(
                f"Finished processing message {message_id}: {'Success' if success else 'Failed'}"
            )
            return success
        except Exception as e:
            self.logger.error(f"Error in process_message_wrapper for {message_id}: {e}")
            return False
        finally:
            # Release the semaphore to allow a new task to start
            self.semaphore.release()

    async def _process_message(self, message):
        start_time = datetime.now()
        topic_partition = f"{message.topic()}-{message.partition()}"
        offset = message.offset()
        message_id = f"{topic_partition}-{offset}"
        record_id = None
        error_occurred = False
        error_msg = None

        self.logger.info(f"Starting processing of message {message_id}")

        try:
            if self.is_message_processed(topic_partition, offset):
                self.logger.info(f"Message {message_id} already processed, skipping")
                return True

            # Message parsing
            try:
                message_value = message.value()
                if isinstance(message_value, bytes):
                    message_value = message_value.decode("utf-8")
                    self.logger.debug(f"Decoded message {message_id} from bytes")

                data = json.loads(message_value)
                if isinstance(data, str):
                    data = json.loads(data)
                    self.logger.debug(
                        f"Handled double-encoded JSON for message {message_id}"
                    )
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                self.logger.error(
                    f"Failed to parse message {message_id}: {str(e)}\n"
                    f"Raw value: {message_value[:1000]}..."
                )
                raise

            # Event processing
            event_type = data.get("eventType")
            if not event_type:
                raise ValueError(f"Missing event_type in message {message_id}")

            payload_data = data.get("payload", {})
            record_id = payload_data.get("recordId")
            extension = payload_data.get("extension", "unknown")
            mime_type = payload_data.get("mimeType", "unknown")
            virtual_record_id = payload_data.get("virtualRecordId")

            self.logger.info(
                f"Processing record {record_id} with event type: {event_type}. "
                f"Message ID: {message_id} Virtual Record ID: {virtual_record_id}"
                f"Message ID: {message_id}, Extension: {extension}, Mime Type: {mime_type}"
            )

            # Handle delete event
            if event_type == EventTypes.DELETE_RECORD.value:
                self.logger.info(f"🗑️ Deleting embeddings for record {record_id}")
                await self.event_processor.processor.indexing_pipeline.delete_embeddings(record_id, virtual_record_id)
                return True

            if event_type == EventTypes.UPDATE_RECORD.value:
                await self.redis_scheduler.schedule_update(data)
                self.logger.info(f"Scheduled update for record {record_id}")
                record = await self.event_processor.arango_service.get_document(
                record_id, CollectionNames.RECORDS.value
                )
                if record is None:
                    self.logger.error(f"❌ Record {record_id} not found in database")
                    return
                doc = dict(record)

                doc.update({"isDirty": True})

                docs = [doc]
                await self.event_processor.arango_service.batch_upsert_nodes(
                    docs, CollectionNames.RECORDS.value
                )
                return True

            if extension is None and mime_type != "text/gmail_content":
                extension = payload_data["recordName"].split(".")[-1]

            self.logger.info("🚀 Checking for mime_type")
            self.logger.info("🚀 mime_type: %s", mime_type)
            self.logger.info("🚀 extension: %s", extension)

            record = await self.event_processor.arango_service.get_document(
                record_id, CollectionNames.RECORDS.value
            )
            if record is None:
                self.logger.error(f"❌ Record {record_id} not found in database")
                return
            doc = dict(record)

            supported_mime_types = [
                MimeTypes.GMAIL.value,
                MimeTypes.GOOGLE_SLIDES.value,
                MimeTypes.GOOGLE_DOCS.value,
                MimeTypes.GOOGLE_SHEETS.value,
            ]

            supported_extensions = [
                ExtensionTypes.PDF.value,
                ExtensionTypes.DOCX.value,
                ExtensionTypes.DOC.value,
                ExtensionTypes.XLSX.value,
                ExtensionTypes.XLS.value,
                ExtensionTypes.CSV.value,
                ExtensionTypes.HTML.value,
                ExtensionTypes.PPTX.value,
                ExtensionTypes.PPT.value,
                ExtensionTypes.MD.value,
                ExtensionTypes.MDX.value,
                ExtensionTypes.TXT.value,
            ]

            if (
                mime_type not in supported_mime_types
                and extension not in supported_extensions
            ):
                self.logger.info(
                    f"🔴🔴🔴 Unsupported file: Mime Type: {mime_type}, Extension: {extension} 🔴🔴🔴"
                )

                doc.update(
                    {
                        "indexingStatus": ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value,
                        "extractionStatus": ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value,
                    }
                )
                docs = [doc]
                await self.event_processor.arango_service.batch_upsert_nodes(
                    docs, CollectionNames.RECORDS.value
                )

                return True

            # Update with new metadata fields
            doc.update(
                {
                    "indexingStatus": ProgressStatus.IN_PROGRESS.value,
                    "extractionStatus": ProgressStatus.IN_PROGRESS.value,
                }
            )

            docs = [doc]
            await self.event_processor.arango_service.batch_upsert_nodes(
                docs, CollectionNames.RECORDS.value
            )

            # Signed URL handling
            if payload_data and payload_data.get("signedUrlRoute"):
                try:
                    payload = {
                        "orgId": payload_data["orgId"],
                        "scopes": ["storage:token"],
                    }
                    token = await self.generate_jwt(payload)
                    self.logger.debug(f"Generated JWT token for message {message_id}")

                    response = await make_api_call(
                        payload_data["signedUrlRoute"], token
                    )
                    self.logger.debug(
                        f"Received signed URL response for message {message_id}"
                    )

                    if response.get("is_json"):
                        signed_url = response["data"]["signedUrl"]
                        payload_data["signedUrl"] = signed_url
                    else:
                        payload_data["buffer"] = response["data"]
                    data["payload"] = payload_data

                    await self.event_processor.on_event(data)
                    processing_time = (datetime.now() - start_time).total_seconds()
                    self.logger.info(
                        f"✅ Successfully processed document for event: {event_type}. "
                        f"Record: {record_id}, Time: {processing_time:.2f}s"
                    )
                    self.mark_message_processed(topic_partition, offset)
                    return True
                except Exception as e:
                    error_occurred = True
                    error_msg = f"Failed to process signed URL: {str(e)}"
                    raise
            else:
                raise ValueError(
                    f"No signedUrlRoute found in payload for message {message_id}"
                )

        except IndexingError as e:
            error_occurred = True
            error_msg = f"❌ Indexing error for record {record_id}: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            raise
        except Exception as e:
            error_occurred = True
            error_msg = f"Error processing message {message_id}: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            raise
        finally:
            processing_time = (datetime.now() - start_time).total_seconds()
            self.logger.info(
                f"Message {message_id} processing completed in {processing_time:.2f}s. "
                f"Success: {not error_occurred}"
            )

            if error_occurred and record_id:
                await self._update_document_status(
                    record_id=record_id,
                    indexing_status=ProgressStatus.FAILED.value,
                    extraction_status=ProgressStatus.FAILED.value,
                    reason=error_msg,
                )
                return False

    def is_message_processed(self, topic_partition: str, offset: int) -> bool:
        """Check if a message has already been processed."""
        return (
            topic_partition in self.processed_messages
            and offset in self.processed_messages[topic_partition]
        )

    def mark_message_processed(self, topic_partition: str, offset: int):
        """Mark a message as processed."""
        if topic_partition not in self.processed_messages:
            self.processed_messages[topic_partition] = []
        self.processed_messages[topic_partition].append(offset)

    def cleanup_completed_tasks(self):
        """Remove completed tasks from the active tasks set"""
        done_tasks = {task for task in self.active_tasks if task.done()}
        self.active_tasks -= done_tasks

        # Check for exceptions in completed tasks
        for task in done_tasks:
            if task.exception():
                self.logger.error(f"Task completed with exception: {task.exception()}")

    async def start_processing_task(self, message):
        """Start a new task for processing a message with semaphore control"""
        # Wait for the rate limiter
        await self.rate_limiter.wait()

        # Wait for a semaphore slot to become available
        await self.semaphore.acquire()

        # Create and start a new task
        task = asyncio.create_task(self.process_message_wrapper(message))
        self.active_tasks.add(task)

        # Clean up completed tasks
        self.cleanup_completed_tasks()

        # Log current task count
        self.logger.debug(
            f"Active tasks: {len(self.active_tasks)}/{MAX_CONCURRENT_TASKS}"
        )

    async def consume_messages(self):
        """Main consumption loop."""
        start_time = datetime.now()
        processed_count = 0
        error_count = 0

        try:
            self.logger.info("Starting Kafka consumer loop")
            while self.running:
                try:
                    message = self.consumer.poll(0.1)

                    if message is None:
                        await asyncio.sleep(0.1)
                        continue

                    if message.error():
                        if message.error().code() == KafkaError._PARTITION_EOF:
                            self.logger.debug("Reached end of partition")
                            continue
                        else:
                            error_count += 1
                            self.logger.error(
                                f"Kafka error: {message.error()}, "
                                f"Code: {message.error().code()}"
                            )
                            continue

                    await self.start_processing_task(message)
                    processed_count += 1

                    # Log statistics periodically
                    if processed_count % 100 == 0:
                        runtime = (datetime.now() - start_time).total_seconds()
                        self.logger.info(
                            f"Processing statistics: "
                            f"Messages: {processed_count}, "
                            f"Errors: {error_count}, "
                            f"Runtime: {runtime:.2f}s, "
                            f"Rate: {processed_count/runtime:.2f} msg/s"
                        )

                except asyncio.CancelledError:
                    self.logger.info("Kafka consumer task cancelled")
                    break
                except Exception as e:
                    error_count += 1
                    self.logger.error(
                        f"Error in consume_messages loop: {str(e)}", exc_info=True
                    )
                    await asyncio.sleep(1)

        except Exception as e:
            self.logger.error(
                f"Fatal error in consume_messages: {str(e)}", exc_info=True
            )
        finally:
            runtime = (datetime.now() - start_time).total_seconds()
            self.logger.info(
                f"Consumer shutting down. Final statistics: "
                f"Messages: {processed_count}, "
                f"Errors: {error_count}, "
                f"Runtime: {runtime:.2f}s, "
                f"Average rate: {processed_count/runtime:.2f} msg/s"
            )

            if self.active_tasks:
                self.logger.info(
                    f"Waiting for {len(self.active_tasks)} active tasks to complete..."
                )
                await asyncio.gather(*self.active_tasks, return_exceptions=True)

            if self.consumer:
                self.consumer.close()
                self.logger.info("Kafka consumer closed")

    async def generate_jwt(self, token_payload: dict) -> str:
        """
        Generate a JWT token using the jose library.

        Args:
            token_payload (dict): The payload to include in the JWT

        Returns:
            str: The generated JWT token
        """
        # Get the JWT secret from environment variable
        secret_keys = await self.config_service.get_config(
            config_node_constants.SECRET_KEYS.value
        )
        scoped_jwt_secret = secret_keys.get("scopedJwtSecret")
        if not scoped_jwt_secret:
            raise ValueError("SCOPED_JWT_SECRET environment variable is not set")

        # Add standard claims if not present
        if "exp" not in token_payload:
            # Set expiration to 1 hour from now
            token_payload["exp"] = datetime.now(timezone.utc) + timedelta(hours=1)

        if "iat" not in token_payload:
            # Set issued at to current time
            token_payload["iat"] = datetime.now(timezone.utc)

        # Generate the JWT token using jose
        token = jwt.encode(token_payload, scoped_jwt_secret, algorithm="HS256")

        return token

    async def process_scheduled_updates(self):
        """Process any scheduled updates that are ready"""
        while self.running:
            try:
                # Get ready events
                ready_events = await self.redis_scheduler.get_ready_events()

                for event in ready_events:
                    try:
                        # Process the event
                        payload_data = event.get("payload", {})
                        record_id = payload_data.get("recordId")
                        extension = payload_data.get("extension", "unknown")
                        mime_type = payload_data.get("mimeType", "unknown")

                        if extension is None and mime_type != "text/gmail_content":
                            extension = payload_data["recordName"].split(".")[-1]

                        self.logger.info(
                            f"Processing update for record {record_id}"
                            f"Extension: {extension}, Mime Type: {mime_type}"
                        )

                        record = await self.event_processor.arango_service.get_document(
                            record_id, CollectionNames.RECORDS.value
                        )
                        if record is None:
                            self.logger.error(f"❌ Record {record_id} not found in database")
                            return
                        doc = dict(record)

                        # Update with new metadata fields
                        doc.update(
                            {
                                "indexingStatus": ProgressStatus.IN_PROGRESS.value,
                                "extractionStatus": ProgressStatus.IN_PROGRESS.value,
                            }
                        )

                        docs = [doc]
                        await self.event_processor.arango_service.batch_upsert_nodes(
                            docs, CollectionNames.RECORDS.value
                        )

                        if payload_data and payload_data.get("signedUrlRoute"):
                            try:
                                payload = {
                                    "orgId": payload_data["orgId"],
                                    "scopes": ["storage:token"],
                                }
                                token = await self.generate_jwt(payload)
                                self.logger.debug(f"Generated JWT token for record {record_id}")

                                response = await make_api_call(
                                    payload_data["signedUrlRoute"], token
                                )
                                self.logger.debug(
                                    f"Received signed URL response for record {record_id}"
                                )

                                if response.get("is_json"):
                                    signed_url = response["data"]["signedUrl"]
                                    payload_data["signedUrl"] = signed_url
                                else:
                                    payload_data["buffer"] = response["data"]
                                event["payload"] = payload_data

                                await self.event_processor.on_event(event)

                            except Exception as e:
                                self.logger.error(f"Error processing signed URL: {str(e)}")
                                raise

                        # Remove processed event
                        await self.redis_scheduler.remove_processed_event(event)

                        self.logger.info(
                            f"Processed scheduled update for record "
                            f"{event.get('payload', {}).get('recordId')}"
                        )
                    except Exception as e:
                        self.logger.error(f"Error processing scheduled update: {str(e)}")

                # Wait before next check
                await asyncio.sleep(60)  # Check every minute

            except Exception as e:
                self.logger.error(f"Error in scheduled update processor: {str(e)}")
                await asyncio.sleep(60)

    async def start(self):
        """Start the consumer and scheduled update processor."""
        self.running = True
        await self.create_consumer()
        # Start scheduled update processing
        self.scheduled_update_task = asyncio.create_task(self.process_scheduled_updates())

    def stop(self):
        """Stop the consumer and scheduled update processor."""
        self.running = False
        if self.scheduled_update_task:
            self.scheduled_update_task.cancel()

    async def _update_document_status(
        self,
        record_id: str,
        indexing_status: str,
        extraction_status: str,
        reason: str = None,
    ):
        """Update document status in Arango"""
        try:
            record = await self.event_processor.arango_service.get_document(
                record_id, CollectionNames.RECORDS.value
            )
            if not record:
                self.logger.error(f"❌ Record {record_id} not found for status update")
                return

            doc = dict(record)
            if doc.get("extractionStatus") == ProgressStatus.COMPLETED.value:
                extraction_status = ProgressStatus.COMPLETED.value
            doc.update(
                {
                    "indexingStatus": indexing_status,
                    "extractionStatus": extraction_status,
                }
            )

            if reason:
                doc["reason"] = reason

            docs = [doc]
            await self.event_processor.arango_service.batch_upsert_nodes(
                docs, CollectionNames.RECORDS.value
            )
            self.logger.info(f"✅ Updated document status for record {record_id}")

        except Exception as e:
            self.logger.error(f"❌ Failed to update document status: {str(e)}")


class RateLimiter:
    """Simple rate limiter to control how many tasks start per second"""

    def __init__(self, rate_limit_per_second):
        self.rate = rate_limit_per_second
        self.last_check = datetime.now()
        self.tokens = rate_limit_per_second
        self.lock = asyncio.Lock()

    async def wait(self):
        """Wait until a token is available"""
        async with self.lock:
            while True:
                now = datetime.now()
                time_passed = (now - self.last_check).total_seconds()

                # Add new tokens based on time passed
                self.tokens += time_passed * self.rate
                self.last_check = now

                # Cap tokens at the maximum rate
                if self.tokens > self.rate:
                    self.tokens = self.rate

                if self.tokens >= 1:
                    # Consume a token
                    self.tokens -= 1
                    break

                # Wait for some tokens to accumulate
                await asyncio.sleep(1.0 / self.rate)
