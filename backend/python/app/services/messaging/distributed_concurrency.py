from __future__ import annotations

from threading import Lock
from typing import TYPE_CHECKING

from redis.asyncio import Redis

if TYPE_CHECKING:
    from logging import Logger

    from redis.commands.core import AsyncScript

    from app.services.messaging.config import RedisConfig


_ACQUIRE_SCRIPT = """
local key = KEYS[1]
local owner = ARGV[1]
local lease_ms = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local key_ttl_ms = tonumber(ARGV[4])
local redis_time = redis.call("TIME")
local now_ms = (tonumber(redis_time[1]) * 1000)
    + math.floor(tonumber(redis_time[2]) / 1000)
local expires_ms = now_ms + lease_ms

redis.call("ZREMRANGEBYSCORE", key, "-inf", now_ms)

if redis.call("ZSCORE", key, owner) then
    redis.call("ZADD", key, expires_ms, owner)
    redis.call("PEXPIRE", key, key_ttl_ms)
    return 1
end

if redis.call("ZCARD", key) >= limit then
    return 0
end

redis.call("ZADD", key, expires_ms, owner)
redis.call("PEXPIRE", key, key_ttl_ms)
return 1
"""

_RENEW_SCRIPT = """
local key = KEYS[1]
local owner = ARGV[1]
local lease_ms = tonumber(ARGV[2])
local key_ttl_ms = tonumber(ARGV[3])
local redis_time = redis.call("TIME")
local now_ms = (tonumber(redis_time[1]) * 1000)
    + math.floor(tonumber(redis_time[2]) / 1000)
local expires_ms = now_ms + lease_ms

redis.call("ZREMRANGEBYSCORE", key, "-inf", now_ms)
if not redis.call("ZSCORE", key, owner) then
    return 0
end

redis.call("ZADD", key, expires_ms, owner)
redis.call("PEXPIRE", key, key_ttl_ms)
return 1
"""

class DistributedLeaseSet:
    """Thread-safe view of leases guarded by a consumer's main-loop heartbeat."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._leases: dict[str, str] = {}

    def add(self, pool: str, owner: str) -> None:
        with self._lock:
            self._leases[pool] = owner

    def discard(self, pool: str) -> str | None:
        with self._lock:
            return self._leases.pop(pool, None)

    def owns(self, pool: str, owner: str) -> bool:
        with self._lock:
            return self._leases.get(pool) == owner

    def snapshot(self) -> list[tuple[str, str]]:
        with self._lock:
            return list(self._leases.items())


class DistributedConcurrencyManager:
    """Redis-backed expiring leases shared by all indexing replicas."""

    KEY_PREFIX = "pipeshub:indexing:concurrency"

    def __init__(
        self,
        logger: Logger,
        redis_config: RedisConfig,
        key_prefix: str = KEY_PREFIX,
        operation_timeout_seconds: float = 5.0,
    ) -> None:
        self.logger = logger
        self.redis_config = redis_config
        self.key_prefix = key_prefix
        self.operation_timeout_seconds = max(0.1, operation_timeout_seconds)
        self._redis: Redis | None = None
        self._acquire_script: AsyncScript | None = None
        self._renew_script: AsyncScript | None = None

    async def initialize(self) -> None:
        if self._redis is not None:
            return
        self._redis = Redis(
            host=self.redis_config.host,
            port=self.redis_config.port,
            password=self.redis_config.password,
            db=self.redis_config.db,
            decode_responses=True,
            socket_timeout=self.operation_timeout_seconds,
            socket_connect_timeout=self.operation_timeout_seconds,
        )
        try:
            await self._redis.ping()
        except BaseException:
            client = self._redis
            self._redis = None
            await client.aclose()
            raise
        # register_script caches the SHA and calls EVALSHA (falling back to
        # EVAL once on NOSCRIPT), instead of re-sending the full script body
        # on every acquire/renew call, which happens on every consumed message.
        self._acquire_script = self._redis.register_script(_ACQUIRE_SCRIPT)
        self._renew_script = self._redis.register_script(_RENEW_SCRIPT)

    async def cleanup(self) -> None:
        client = self._redis
        self._redis = None
        self._acquire_script = None
        self._renew_script = None
        if client is not None:
            await client.aclose()

    def _key(self, pool: str) -> str:
        return f"{self.key_prefix}:{pool}"

    def _client(self) -> Redis:
        if self._redis is None:
            raise RuntimeError(
                "DistributedConcurrencyManager is not initialized"
            )
        return self._redis

    def _script(self, script: AsyncScript | None) -> AsyncScript:
        if self._redis is None or script is None:
            raise RuntimeError("DistributedConcurrencyManager is not initialized")
        return script

    async def try_acquire(
        self,
        pool: str,
        owner: str,
        limit: int,
        lease_seconds: float,
    ) -> bool:
        if limit < 1:
            raise ValueError("Distributed concurrency limit must be positive")
        lease_ms = max(1, int(lease_seconds * 1000))
        result = await self._script(self._acquire_script)(
            keys=[self._key(pool)],
            args=[owner, lease_ms, limit, lease_ms * 2],
        )
        return bool(result)

    async def renew(
        self,
        pool: str,
        owner: str,
        lease_seconds: float,
    ) -> bool:
        lease_ms = max(1, int(lease_seconds * 1000))
        result = await self._script(self._renew_script)(
            keys=[self._key(pool)],
            args=[owner, lease_ms, lease_ms * 2],
        )
        return bool(result)

    async def release(self, pool: str, owner: str) -> None:
        # Plain ZREM is already atomic; no Lua script needed here.
        await self._client().zrem(self._key(pool), owner)
