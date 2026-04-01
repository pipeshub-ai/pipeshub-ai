from logging import Logger
from typing import Optional, override

from redis.asyncio import Redis

from app.services.messaging.config import REQUIRED_TOPICS, RedisStreamsConfig
from app.services.messaging.interface.admin import IMessageAdmin

_ADMIN_INIT_GROUP = "admin_init"
_STREAM_TYPE = "stream"


class RedisStreamsAdmin(IMessageAdmin):
    """Redis Streams implementation of message broker administration"""

    def __init__(self, logger: Logger, config: RedisStreamsConfig) -> None:
        self.logger = logger
        self.config = config

    @override
    async def ensure_topics_exist(
        self, topics: Optional[list[str]] = None
    ) -> None:
        topic_list = topics or REQUIRED_TOPICS
        redis: Optional[Redis] = None
        try:
            redis = Redis(
                host=self.config.host,
                port=self.config.port,
                password=self.config.password,
                db=self.config.db,
                decode_responses=True,
            )

            for topic in topic_list:
                try:
                    exists = await redis.exists(topic)
                    if not exists:
                        await redis.xgroup_create(  # type: ignore
                            topic,
                            _ADMIN_INIT_GROUP,
                            id="$",
                            mkstream=True,
                        )
                        await redis.xgroup_destroy(topic, _ADMIN_INIT_GROUP)  # type: ignore
                        self.logger.info("Created Redis stream: %s", topic)
                    else:
                        self.logger.debug("Redis stream already exists: %s", topic)
                except Exception as e:
                    self.logger.warning(
                        "Error ensuring Redis stream %s: %s", topic, e
                    )

            self.logger.info("All required Redis streams verified")
        except Exception as e:
            self.logger.error("Failed to ensure Redis streams exist: %s", e)
            raise
        finally:
            if redis:
                await redis.close()

    @override
    async def list_topics(self) -> list[str]:
        redis: Optional[Redis] = None
        try:
            redis = Redis(
                host=self.config.host,
                port=self.config.port,
                password=self.config.password,
                db=self.config.db,
                decode_responses=True,
            )
            streams = []
            async for key in redis.scan_iter():
                key_type = await redis.type(key)  # type: ignore
                if key_type == _STREAM_TYPE:
                    streams.append(key)
            return streams
        finally:
            if redis:
                await redis.close()
