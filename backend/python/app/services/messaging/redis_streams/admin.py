from logging import Logger
from typing import TYPE_CHECKING, override

from app.services.messaging.config import (
    REQUIRED_TOPICS,
    MessageBrokerType,
    RedisStreamsConfig,
)
from app.services.messaging.interface.admin import IMessageAdmin
from app.services.redis.config import ClientOptions, RedisConnectionConfig
from app.services.redis.connection_provider_factory import get_redis_provider

if TYPE_CHECKING:
    from app.services.redis.connection_provider import IRedisConnectionProvider, RedisClient

_ADMIN_INIT_GROUP = "admin_init"
_STREAM_TYPE = "stream"


class RedisStreamsAdmin(IMessageAdmin):
    """Redis Streams implementation of message broker administration"""

    def __init__(
        self, logger: Logger, config: RedisStreamsConfig, provider: "IRedisConnectionProvider | None" = None
    ) -> None:
        self.logger = logger
        self.config = config
        self._provider: "IRedisConnectionProvider" = provider or get_redis_provider(
            RedisConnectionConfig.from_redis_config(config)
        )

    @override
    async def ensure_topics_exist(
        self, topics: list[str] | None = None
    ) -> None:
        # Lanes are separate streams, so a laned install needs each
        # record-events.N pre-created -- not for correctness (XGROUP CREATE
        # MKSTREAM would make them lazily) but so lag dashboards and the
        # consumer's own subscription see them from t=0 rather than after the
        # first message lands on each.
        from app.services.messaging.messaging_factory import lane_topics_for

        topic_list = topics or [
            lane
            for topic in REQUIRED_TOPICS
            for lane in lane_topics_for(topic, MessageBrokerType.REDIS)
        ]
        redis: "RedisClient | None" = None
        try:
            redis = self._provider.create_client(ClientOptions(decode_responses=True))

            failures: list[str] = []
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
                    self.logger.error(
                        "Failed to ensure Redis stream %s: %s", topic, e
                    )
                    failures.append(topic)

            if failures:
                raise RuntimeError(
                    f"Failed to ensure {len(failures)} Redis stream(s): {', '.join(failures)}"
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
        # provider.scan_keys(), not a raw scan_iter() (R2): ioredis-style
        # Cluster.scan() only reaches one random node, and this must see
        # every stream regardless of which shard it lives on.
        redis = self._provider.get_client()
        streams = []
        async for key in self._provider.scan_keys("*"):
            key_type = await redis.type(key)  # type: ignore
            if key_type == _STREAM_TYPE:
                streams.append(key)
        return streams
