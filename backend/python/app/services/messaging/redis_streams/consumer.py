import asyncio
import json
from logging import Logger
from typing import Optional

from redis.asyncio import Redis

from app.services.messaging.config import (
    MessageHandler,
    RedisStreamsConfig,
    StreamMessage,
)
from app.services.messaging.interface.consumer import IMessagingConsumer

MAX_CONCURRENT_TASKS = 5

_BUSYGROUP_ERROR = "BUSYGROUP"
_MESSAGE_VALUE_FIELD = "value"


class RedisStreamsConsumer(IMessagingConsumer):
    """Redis Streams implementation of messaging consumer"""

    def __init__(self, logger: Logger, config: RedisStreamsConfig) -> None:
        self.logger = logger
        self.config = config
        self.redis: Optional[Redis] = None
        self.running = False
        self.consume_task: Optional[asyncio.Task] = None
        self.message_handler: Optional[MessageHandler] = None
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)
        self.active_tasks: set[asyncio.Task] = set()

    async def initialize(self) -> None:
        try:
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
                            "Consumer group %s already exists for stream %s",
                            self.config.group_id,
                            topic,
                        )
                    else:
                        raise

            self.logger.info("Successfully initialized Redis Streams consumer")
        except Exception as e:
            self.logger.error("Failed to create consumer: %s", e)
            raise

    async def cleanup(self) -> None:
        try:
            if self.redis:
                await self.redis.close()
                self.logger.info("Redis Streams consumer stopped")
        except Exception as e:
            self.logger.error("Error during cleanup: %s", e)

    async def start(
        self,
        message_handler: MessageHandler,
    ) -> None:
        try:
            self.running = True
            self.message_handler = message_handler

            if not self.redis:
                await self.initialize()

            self.consume_task = asyncio.create_task(self._consume_loop())
            self.logger.info("Started Redis Streams consumer task")
        except Exception as e:
            self.logger.error("Failed to start Redis Streams consumer: %s", e)
            raise

    async def stop(
        self,
        message_handler: Optional[MessageHandler] = None,
    ) -> None:
        self.running = False

        if self.consume_task:
            self.consume_task.cancel()
            try:
                await self.consume_task
            except asyncio.CancelledError:
                pass

        if self.redis:
            await self.redis.close()
            self.logger.info("Redis Streams consumer stopped")

    def is_running(self) -> bool:
        return self.running

    async def _drain_pending(self) -> None:
        """Re-process messages left in the Pending Entries List (PEL).

        Uses XAUTOCLAIM to steal idle messages from any consumer in the group
        (including crashed ones), then XREADGROUP with id "0" for our own
        pending messages.
        """
        self.logger.info("Draining pending messages from PEL")

        # Phase 1: claim idle messages from other (possibly crashed) consumers
        for topic in self.config.topics:
            start_id = "0-0"
            while self.running:
                try:
                    result = await self.redis.xautoclaim(  # type: ignore
                        topic,
                        self.config.group_id,
                        self.config.client_id,
                        min_idle_time=0,
                        start_id=start_id,
                        count=10,
                    )
                    next_id, claimed, _deleted = result
                    if not claimed:
                        break
                    for message_id, fields in claimed:
                        try:
                            success = await self._process_message(
                                topic, message_id, fields
                            )
                            if success:
                                await self.redis.xack(  # type: ignore
                                    topic, self.config.group_id, message_id,
                                )
                                self.logger.info(
                                    "Recovered pending message %s on stream %s",
                                    message_id, topic,
                                )
                        except Exception as e:
                            self.logger.error(
                                "Error recovering pending message %s: %s",
                                message_id, e,
                            )
                    start_id = next_id
                    if next_id == b"0-0" or next_id == "0-0":
                        break
                except Exception as e:
                    self.logger.error("Error during XAUTOCLAIM on %s: %s", topic, e)
                    break

        self.logger.info("PEL drained, switching to new messages")

    async def _consume_loop(self) -> None:
        try:
            self.logger.info("Starting Redis Streams consumer loop")
            await self._drain_pending()
            while self.running:
                try:
                    streams = dict.fromkeys(self.config.topics, ">")

                    results = await self.redis.xreadgroup(  # type: ignore
                        groupname=self.config.group_id,
                        consumername=self.config.client_id,
                        streams=streams,
                        count=self.config.batch_size,
                        block=self.config.block_ms,
                    )

                    if not results:
                        continue

                    for stream_name, messages in results:
                        for message_id, fields in messages:
                            try:
                                self.logger.info(
                                    "Received message: stream=%s, id=%s",
                                    stream_name,
                                    message_id,
                                )
                                success = await self._process_message(
                                    stream_name, message_id, fields
                                )
                                if success:
                                    await self.redis.xack(  # type: ignore
                                        stream_name,
                                        self.config.group_id,
                                        message_id,
                                    )
                                    self.logger.info(
                                        "Acknowledged message %s on stream %s",
                                        message_id,
                                        stream_name,
                                    )
                                else:
                                    self.logger.warning(
                                        "Failed to process message at id %s",
                                        message_id,
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
                    await asyncio.sleep(1)

        except Exception as e:
            self.logger.error("Fatal error in consume_messages: %s", e)
        finally:
            await self.cleanup()

    async def _process_message(
        self, stream_name: str, message_id: str, fields: dict[str, str]
    ) -> bool:
        try:
            if _MESSAGE_VALUE_FIELD not in fields:
                self.logger.debug(
                    "Skipping message %s without value field (likely init message)",
                    message_id,
                )
                return True

            value_str = fields[_MESSAGE_VALUE_FIELD]
            try:
                raw = json.loads(value_str)
                if isinstance(raw, str):
                    raw = json.loads(raw)
            except json.JSONDecodeError as e:
                self.logger.error(
                    "JSON parsing failed for message %s: %s", message_id, e
                )
                return False

            if not self.message_handler:
                self.logger.error("No message handler set for %s", message_id)
                return False

            if raw is None:
                self.logger.error(
                    "Parsed message is None for %s, skipping", message_id
                )
                return False

            parsed_message = StreamMessage(**raw)

            try:
                return await self.message_handler(parsed_message)
            except Exception as e:
                self.logger.error(
                    "Error in message handler for %s: %s",
                    message_id,
                    e,
                    exc_info=True,
                )
                return False

        except Exception as e:
            self.logger.error(
                "Unexpected error processing message %s: %s",
                message_id,
                e,
                exc_info=True,
            )
            return False
