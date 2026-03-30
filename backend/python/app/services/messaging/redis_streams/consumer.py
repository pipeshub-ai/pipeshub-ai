import asyncio
import json
from logging import Logger
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set

from redis.asyncio import Redis

from app.services.messaging.config import RedisStreamsConfig
from app.services.messaging.interface.consumer import IMessagingConsumer

MAX_CONCURRENT_TASKS = 5


class RedisStreamsConsumer(IMessagingConsumer):
    """Redis Streams implementation of messaging consumer"""

    def __init__(self, logger: Logger, config: RedisStreamsConfig) -> None:
        self.logger = logger
        self.config = config
        self.redis: Optional[Redis] = None
        self.running = False
        self.consume_task: Optional[asyncio.Task] = None
        self.message_handler: Optional[Callable] = None
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)
        self.active_tasks: Set[asyncio.Task] = set()

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
                        f"Created consumer group {self.config.group_id} for stream {topic}"
                    )
                except Exception as e:
                    if "BUSYGROUP" in str(e):
                        self.logger.debug(
                            f"Consumer group {self.config.group_id} already exists for stream {topic}"
                        )
                    else:
                        raise

            self.logger.info("Successfully initialized Redis Streams consumer")
        except Exception as e:
            self.logger.error(f"Failed to create consumer: {e}")
            raise

    async def cleanup(self) -> None:
        try:
            if self.redis:
                await self.redis.close()
                self.logger.info("Redis Streams consumer stopped")
        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")

    async def start(
        self,
        message_handler: Callable[[Dict[str, Any]], Awaitable[bool]],
    ) -> None:
        try:
            self.running = True
            self.message_handler = message_handler

            if not self.redis:
                await self.initialize()

            self.consume_task = asyncio.create_task(self._consume_loop())
            self.logger.info("Started Redis Streams consumer task")
        except Exception as e:
            self.logger.error(f"Failed to start Redis Streams consumer: {str(e)}")
            raise

    async def stop(
        self,
        message_handler: Optional[Callable[[Dict[str, Any]], Awaitable[bool]]] = None,
    ) -> None:
        self.running = False
        if self.message_handler:
            await self.message_handler(None)  # type: ignore

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

    async def _consume_loop(self) -> None:
        try:
            self.logger.info("Starting Redis Streams consumer loop")
            while self.running:
                try:
                    streams = {
                        topic: ">" for topic in self.config.topics
                    }

                    results = await self.redis.xreadgroup(  # type: ignore
                        groupname=self.config.group_id,
                        consumername=self.config.client_id,
                        streams=streams,
                        count=1,
                        block=self.config.block_ms,
                    )

                    if not results:
                        continue

                    for stream_name, messages in results:
                        for message_id, fields in messages:
                            try:
                                self.logger.info(
                                    f"Received message: stream={stream_name}, id={message_id}"
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
                                        f"Acknowledged message {message_id} on stream {stream_name}"
                                    )
                                else:
                                    self.logger.warning(
                                        f"Failed to process message at id {message_id}"
                                    )

                            except Exception as e:
                                self.logger.error(
                                    f"Error processing individual message: {e}"
                                )
                                continue

                except asyncio.CancelledError:
                    self.logger.info("Redis Streams consumer task cancelled")
                    break
                except Exception as e:
                    self.logger.error(f"Error in consume_messages loop: {e}")
                    await asyncio.sleep(1)

        except Exception as e:
            self.logger.error(f"Fatal error in consume_messages: {e}")
        finally:
            await self.cleanup()

    async def _process_message(
        self, stream_name: str, message_id: str, fields: Dict[str, str]
    ) -> bool:
        try:
            if "value" not in fields:
                self.logger.debug(
                    f"Skipping message {message_id} without value field (likely init message)"
                )
                return True

            value_str = fields["value"]
            try:
                parsed_message = json.loads(value_str)
                if isinstance(parsed_message, str):
                    parsed_message = json.loads(parsed_message)
            except json.JSONDecodeError as e:
                self.logger.error(
                    f"JSON parsing failed for message {message_id}: {str(e)}"
                )
                return False

            if not self.message_handler:
                self.logger.error(f"No message handler set for {message_id}")
                return False

            if parsed_message is None:
                self.logger.error(f"Parsed message is None for {message_id}, skipping")
                return False

            try:
                return await self.message_handler(parsed_message)
            except Exception as e:
                self.logger.error(
                    f"Error in message handler for {message_id}: {str(e)}",
                    exc_info=True,
                )
                return False

        except Exception as e:
            self.logger.error(
                f"Unexpected error processing message {message_id}: {str(e)}",
                exc_info=True,
            )
            return False
