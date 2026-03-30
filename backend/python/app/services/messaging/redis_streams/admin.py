from logging import Logger
from typing import List, Optional

from redis.asyncio import Redis

from app.services.messaging.config import REQUIRED_TOPICS, RedisStreamsConfig
from app.services.messaging.interface.admin import IMessageAdmin


class RedisStreamsAdmin(IMessageAdmin):
    """Redis Streams implementation of message broker administration"""

    def __init__(self, logger: Logger, config: RedisStreamsConfig) -> None:
        self.logger = logger
        self.config = config

    async def ensure_topics_exist(
        self, topics: Optional[List[str]] = None
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
                            "admin_init",
                            id="$",
                            mkstream=True,
                        )
                        await redis.xgroup_destroy(topic, "admin_init")  # type: ignore
                        self.logger.info(f"Created Redis stream: {topic}")
                    else:
                        self.logger.debug(f"Redis stream already exists: {topic}")
                except Exception as e:
                    self.logger.warning(
                        f"Error ensuring Redis stream {topic}: {str(e)}"
                    )

            self.logger.info("All required Redis streams verified")
        except Exception as e:
            self.logger.error(f"Failed to ensure Redis streams exist: {str(e)}")
            raise
        finally:
            if redis:
                await redis.close()

    async def list_topics(self) -> List[str]:
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
                if key_type == "stream":
                    streams.append(key)
            return streams
        finally:
            if redis:
                await redis.close()
