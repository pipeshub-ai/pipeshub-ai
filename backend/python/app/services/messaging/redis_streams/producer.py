import asyncio
import json
from logging import Logger
from typing import Any, Dict, Optional

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
                    f"Redis Streams producer initialized at {self.config.host}:{self.config.port}"
                )
            except Exception as e:
                self.redis = None
                self.logger.error(f"Failed to initialize Redis Streams producer: {str(e)}")
                raise

    async def cleanup(self) -> None:
        async with self._lock:
            if self.redis:
                try:
                    await self.redis.close()
                    self.redis = None
                    self.logger.info("Redis Streams producer stopped successfully")
                except Exception as e:
                    self.logger.error(f"Error stopping Redis Streams producer: {str(e)}")

    async def start(self) -> None:
        if self.redis is None:
            await self.initialize()

    async def stop(self) -> None:
        await self.cleanup()

    async def send_message(
        self,
        topic: str,
        message: Dict[str, Any],
        key: Optional[str] = None,
    ) -> bool:
        try:
            if self.redis is None:
                await self.initialize()

            fields = {
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

            self.logger.info(f"Message successfully published to Redis stream {topic}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to send message to Redis stream: {str(e)}")
            return False

    async def send_event(
        self,
        topic: str,
        event_type: str,
        payload: Dict[str, Any],
        key: Optional[str] = None,
    ) -> bool:
        try:
            message = {
                "eventType": event_type,
                "payload": payload,
                "timestamp": get_epoch_timestamp_in_ms(),
            }

            await self.send_message(topic=topic, message=message, key=key)
            self.logger.info(
                f"Successfully sent event with type: {event_type} to topic: {topic}"
            )
            return True

        except Exception as e:
            self.logger.error(f"Error sending event: {str(e)}")
            return False
