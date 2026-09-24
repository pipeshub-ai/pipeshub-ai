"""Pluggable Redis connection layer.

Every Redis-backed feature (KV store, Streams messaging, caching, distributed
leases/retry tracking) depends on :class:`IRedisConnectionProvider`, never on
``redis.asyncio.Redis`` / ``RedisCluster`` directly. OSS ships
``StandaloneRedisProvider`` (mode ``standalone``, the default) and
``ClusterRedisProvider`` (mode ``cluster``, plain Redis Cluster). A separate
EE repo can add AWS MemoryDB support by implementing
:class:`IRedisConnectionProvider` (or subclassing ``ClusterRedisProvider``)
and registering it with :class:`RedisConnectionProviderFactory` -- no core
file in this package needs to change.
"""

from app.services.redis.config import ClientOptions, RedisConnectionConfig
from app.services.redis.connection_provider import IRedisConnectionProvider, RedisClient
from app.services.redis.connection_provider_factory import (
    RedisConnectionProviderFactory,
    get_prepared_redis_provider,
    get_redis_provider,
    reset_redis_provider_registry,
)

__all__ = [
    "ClientOptions",
    "IRedisConnectionProvider",
    "RedisClient",
    "RedisConnectionConfig",
    "RedisConnectionProviderFactory",
    "get_prepared_redis_provider",
    "get_redis_provider",
    "reset_redis_provider_registry",
]
