"""Redis-based retry tracking for message consumers.

Provides persistent retry count storage for Kafka and Redis Streams consumers.
Ensures retry counts survive restarts and are consistent across consumer instances.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from app.services.distributed.interface import IRetryTracker
from app.services.messaging.config import messaging_env
from app.services.messaging.redis_client import RedisClientRegistry

if TYPE_CHECKING:
    from logging import Logger

    from app.services.messaging.config import RedisConfig
    from app.services.redis.connection_provider import RedisClient as Redis


class RetryManager(IRetryTracker):
    """Redis-based retry tracking for message consumers.

    Stores retry counts in Redis with auto-expiring TTL to handle abandoned
    messages. Keys are explicitly deleted on successful processing or when
    max retries are reached (dead-lettering).

    Key format: messaging:retry:{message_id}
    Value: Integer retry count (1, 2, 3, ...)
    TTL: 24 hours (configurable)
    """

    KEY_PREFIX = "messaging:retry"
    DEFAULT_TTL_SECONDS = 86400  # 24 hours

    def __init__(
        self,
        logger: Logger,
        redis_client: Optional[Redis] = None,
        redis_config: Optional[RedisConfig] = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        """Initialize RetryManager.

        Args:
            logger: Logger instance
            redis_client: Existing Redis client (preferred if available)
            redis_config: Redis configuration to create new client
            ttl_seconds: TTL for retry keys in seconds (default: 24 hours)

        Raises:
            ValueError: If neither redis_client nor redis_config is provided
        """
        self.logger = logger
        self._redis: Optional[Redis] = redis_client
        self._registry: Optional[RedisClientRegistry] = None
        self._redis_config = redis_config
        self._owns_client = redis_client is None
        self.ttl_seconds = ttl_seconds
        # REDIS_KEY_NAMESPACE (R9): resolved once the provider is known
        # (`initialize()`); stays empty when a raw `redis_client` is
        # injected directly (mostly tests), same as an unset namespace.
        self._key_namespace = ""

        if redis_client is None and redis_config is None:
            raise ValueError("Either redis_client or redis_config must be provided")

    async def initialize(self) -> None:
        """Initialize Redis connection if not already provided."""
        if self._redis is not None or self._registry is not None:
            return

        if self._redis_config is None:
            raise ValueError("Redis config not available for initialization")

        # A registry rather than one client: retry counts are read and written
        # from the consumers' worker loop as well as the main loop, and a
        # redis.asyncio client binds to whichever loop first uses it. Handing
        # each loop its own removes the cross-thread hop those calls used to
        # need — the hop whose 5s deadline, when a busy loop overran it,
        # cancelled in-flight commands and forced their connections closed.
        self._registry = RedisClientRegistry(
            self.logger,
            self._redis_config,
            max_connections=messaging_env.concurrency_redis_max_connections,
            socket_timeout_seconds=messaging_env.concurrency_redis_timeout_seconds,
        )
        await self._registry.client().ping()
        self._key_namespace = self._registry.provider.key_namespace
        self.logger.info("RetryManager: Redis connection initialized")

    async def cleanup(self) -> None:
        """Close Redis connections if we own them."""
        if not self._owns_client:
            return
        if self._registry is not None:
            registry = self._registry
            self._registry = None
            self._key_namespace = ""
            await registry.aclose()
            self.logger.info("RetryManager: Redis connection closed")
        elif self._redis is not None:
            await self._redis.aclose()
            self._redis = None
            self.logger.info("RetryManager: Redis connection closed")

    def _client(self) -> Redis:
        """The client for the calling loop, or the explicitly injected one."""
        if self._redis is not None:
            return self._redis
        if self._registry is not None:
            return self._registry.client()
        raise RuntimeError("RetryManager is not initialized")

    def _build_key(self, message_id: str) -> str:
        """Build Redis key for a message.

        Args:
            message_id: Unique message identifier (e.g., "topic-partition-offset")

        Returns:
            Redis key in format: [{namespace}:]messaging:retry:{message_id}
        """
        namespace = f"{self._key_namespace}:" if self._key_namespace else ""
        return f"{namespace}{self.KEY_PREFIX}:{message_id}"

    async def increment_and_check(
        self, message_id: str, max_attempts: int
    ) -> tuple[int, bool]:
        """Increment retry count and check if max attempts reached.

        Atomically increments the retry count for a message and sets TTL.
        Returns the new count and whether the message should be dead-lettered.

        Args:
            message_id: Unique message identifier
            max_attempts: Maximum allowed delivery attempts

        Returns:
            Tuple of (current_count, should_dead_letter)
            - current_count: Number of delivery attempts (1-indexed)
            - should_dead_letter: True if count >= max_attempts

        Raises:
            RuntimeError: If Redis client is not initialized
        """
        if self._redis is None and self._registry is None:
            raise RuntimeError("RetryManager not initialized. Call initialize() first.")

        key = self._build_key(message_id)

        # INCR is atomic; creates key with value 1 if it doesn't exist
        count = await self._client().incr(key)

        # Set/refresh TTL on every increment
        await self._client().expire(key, self.ttl_seconds)

        should_dead_letter = count >= max_attempts

        if should_dead_letter:
            self.logger.warning(
                "RetryManager: Message %s reached max attempts (%d/%d), will dead-letter",
                message_id,
                count,
                max_attempts,
            )
        else:
            self.logger.debug(
                "RetryManager: Message %s attempt %d/%d",
                message_id,
                count,
                max_attempts,
            )

        return count, should_dead_letter

    async def get_count(self, message_id: str) -> int:
        """Get current retry count for a message.

        Args:
            message_id: Unique message identifier

        Returns:
            Current retry count (0 if not found)

        Raises:
            RuntimeError: If Redis client is not initialized
        """
        if self._redis is None and self._registry is None:
            raise RuntimeError("RetryManager not initialized. Call initialize() first.")

        key = self._build_key(message_id)
        value = await self._client().get(key)
        return int(value) if value else 0

    async def clear(self, message_id: str) -> None:
        """Clear retry tracking for a message after successful processing.

        Should be called when:
        - Message processed successfully
        - Message dead-lettered (max retries reached)

        Args:
            message_id: Unique message identifier

        Raises:
            RuntimeError: If Redis client is not initialized
        """
        if self._redis is None and self._registry is None:
            raise RuntimeError("RetryManager not initialized. Call initialize() first.")

        key = self._build_key(message_id)
        deleted = await self._client().delete(key)

        if deleted:
            self.logger.debug("RetryManager: Cleared retry tracking for %s", message_id)

    async def clear_batch(self, message_ids: list[str]) -> int:
        """Clear retry tracking for multiple messages.

        Args:
            message_ids: List of message identifiers to clear

        Returns:
            Number of keys deleted

        Raises:
            RuntimeError: If Redis client is not initialized
        """
        if self._redis is None and self._registry is None:
            raise RuntimeError("RetryManager not initialized. Call initialize() first.")

        if not message_ids:
            return 0

        # Pipelined per-key DELETE, not one multi-key DEL (R5): message ids
        # for the same batch routinely land in different Redis Cluster hash
        # slots, and a single `DEL k1 k2 ...` raises CROSSSLOT there.
        # redis-py's ClusterPipeline routes each command to its own node; on
        # standalone this is still one round trip.
        keys = [self._build_key(msg_id) for msg_id in message_ids]
        async with self._client().pipeline(transaction=False) as pipe:
            for key in keys:
                pipe.delete(key)
            results = await pipe.execute()
        deleted = sum(1 for r in results if r)

        self.logger.debug(
            "RetryManager: Cleared retry tracking for %d/%d messages",
            deleted,
            len(message_ids),
        )
        return deleted

    async def has_pending_retries(self, message_ids: list[str]) -> bool:
        """Check if any messages have pending retries.

        Args:
            message_ids: List of message identifiers to check

        Returns:
            True if any message has a retry count > 0

        Raises:
            RuntimeError: If Redis client is not initialized
        """
        if self._redis is None and self._registry is None:
            raise RuntimeError("RetryManager not initialized. Call initialize() first.")

        if not message_ids:
            return False

        # Pipelined per-key GET, not MGET (R5): redis-py's cluster `mget` is
        # atomic and raises CROSSSLOT across slots (unlike `delete`, which it
        # happens to split per slot); `mget_nonatomic` would dodge that but
        # a pipeline gets the same one-round-trip behaviour uniformly across
        # both cluster and standalone.
        keys = [self._build_key(msg_id) for msg_id in message_ids]
        async with self._client().pipeline(transaction=False) as pipe:
            for key in keys:
                pipe.get(key)
            values = await pipe.execute()

        return any(v is not None and int(v) > 0 for v in values)
