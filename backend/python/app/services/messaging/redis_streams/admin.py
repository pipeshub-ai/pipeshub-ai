from logging import Logger
from typing import Optional, override

from app.services.messaging.config import (
    REQUIRED_TOPICS,
    RedisStreamsConfig,
    stream_key,
    topic_from_stream_key,
)
from app.services.messaging.interface.admin import IMessageAdmin
from app.utils.redis_util import RedisClient, build_redis_client, cluster_aware_scan_iter

_ADMIN_INIT_GROUP = "admin_init"
_STREAM_TYPE = "stream"


def _config_dict(config: RedisStreamsConfig) -> dict:
    return config.model_dump() if hasattr(config, "model_dump") else config.__dict__


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
        redis: Optional[RedisClient] = None
        try:
            redis = build_redis_client(_config_dict(self.config), decode_responses=True)

            failures: list[str] = []
            for topic in topic_list:
                key = stream_key(self.config, topic)
                try:
                    exists = await redis.exists(key)
                    if not exists:
                        await redis.xgroup_create(  # type: ignore
                            key,
                            _ADMIN_INIT_GROUP,
                            id="$",
                            mkstream=True,
                        )
                        await redis.xgroup_destroy(key, _ADMIN_INIT_GROUP)  # type: ignore
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
        redis: Optional[RedisClient] = None
        try:
            redis = build_redis_client(_config_dict(self.config), decode_responses=True)
            streams = []
            async for key in cluster_aware_scan_iter(redis):
                key_type = await redis.type(key)  # type: ignore
                if key_type == _STREAM_TYPE:
                    # Unwrap so callers see topic names, not Redis-internal keys.
                    streams.append(topic_from_stream_key(self.config, key))
            return streams
        finally:
            if redis:
                await redis.close()
