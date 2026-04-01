import asyncio
import json
from logging import Logger
from typing import Optional, override

from pydantic import JsonValue
from redis.asyncio import Redis

from app.services.messaging.config import RedisStreamsConfig
from app.services.messaging.interface.producer import IMessagingProducer
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class RedisStreamsProducer(IMessagingProducer):
    """Redis Streams implementation of messaging producer"""

    def __init__(self, logger: Logger, config: RedisStreamsConfig) -> None:
        self.logger = logger
        self.config = config
        self.redis: Optional[Redis] = None
        self._lock = asyncio.Lock()

    @override
    async def initialize(self) -> None:
        if self.redis is not None:
            return

        async with self._lock:
            if self.redis is not None:
                return

            try:
                self.redis = Redis(
                    host=self.config.host,
                    port=self.config.port,
                    password=self.config.password,
                    db=self.config.db,
                    decode_responses=True,
                )
                await self.redis.ping()
                self.logger.info(
                    "Redis Streams producer initialized at %s:%s",
                    self.config.host,
                    self.config.port,
                )
            except Exception as e:
                self.redis = None
                self.logger.error("Failed to initialize Redis Streams producer: %s", e)
                raise

    @override
    async def cleanup(self) -> None:
        async with self._lock:
            if self.redis:
                try:
                    await self.redis.close()
                    self.redis = None
                    self.logger.info("Redis Streams producer stopped successfully")
                except Exception as e:
                    self.logger.error("Error stopping Redis Streams producer: %s", e)

    @override
    async def start(self) -> None:
        if self.redis is None:
            await self.initialize()

    @override
    async def stop(self) -> None:
        await self.cleanup()

    @override
    async def send_message(
        self,
        topic: str,
        message: dict[str, JsonValue],
        key: Optional[str] = None,
    ) -> bool:
        try:
            if self.redis is None:
                await self.initialize()

            fields: dict[str, str] = {
                "value": json.dumps(message),
            }
            if key:
                fields["key"] = key

            await self.redis.xadd(  # type: ignore
                topic,
                fields,
                maxlen=self.config.max_len,
                approximate=True,
            )

            self.logger.info("Message successfully published to Redis stream %s", topic)
            return True

        except Exception as e:
            self.logger.error("Failed to send message to Redis stream: %s", e)
            raise

    @override
    async def send_event(
        self,
        topic: str,
        event_type: str,
        payload: dict[str, JsonValue],
        key: Optional[str] = None,
    ) -> bool:
        message: dict[str, JsonValue] = {
            "eventType": event_type,
            "payload": payload,
            "timestamp": get_epoch_timestamp_in_ms(),
        }

        await self.send_message(topic=topic, message=message, key=key)
        self.logger.info(
            "Successfully sent event with type: %s to topic: %s",
            event_type,
            topic,
        )
        return True
