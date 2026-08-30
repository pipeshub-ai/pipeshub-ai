from __future__ import annotations

from enum import Enum
from threading import Lock
from typing import TYPE_CHECKING
from weakref import WeakKeyDictionary

from app.services.messaging.redis_client import RedisClientRegistry

if TYPE_CHECKING:
    from collections.abc import Sequence
    from logging import Logger

    from redis.asyncio import Redis
    from redis.commands.core import AsyncScript

    from app.services.messaging.config import RedisConfig
    from app.services.messaging.lease import LeaseRenewer


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

class LeaseKind(Enum):
    """What a lease is protecting, which decides how it fails.

    ``CAPACITY`` leases (``indexing``, ``indexing:light``, ``parsing``,
    ``parsing:light``) are a *cluster-wide cap* layered over node-local
    admission gates. If Redis becomes unreachable, continuing under the local
    gate alone over-admits across the fleet but keeps every node working — a
    bounded degradation, and exactly what ``DISTRIBUTED_INDEXING_CONCURRENCY=false``
    already does deliberately. So they fail **open**.

    ``EXCLUSIVITY`` leases (``record:<id>``, ``recovery``) are mutual
    exclusion: two holders means one record indexed twice, or two replicas
    running stale-record recovery at once. They fail **closed** — the work is
    left for a retry that can take the lease properly.
    """

    CAPACITY = "capacity"
    EXCLUSIVITY = "exclusivity"


def lease_kind(pool: str) -> LeaseKind:
    """Classify a lease pool name. Anything per-record or the recovery lock is
    mutual exclusion; the fixed capacity pools are everything else."""
    if pool.startswith("record:") or pool == "recovery":
        return LeaseKind.EXCLUSIVITY
    return LeaseKind.CAPACITY


class DistributedLeaseSet:
    """Thread-safe record of which leases one message currently holds.

    Optionally mirrors every change into a process-wide ``LeaseRenewer``, so
    the set of leases being renewed is derived from this bookkeeping rather
    than maintained alongside it. The consumers add and discard leases from
    roughly a dozen places across their handler pump and their teardown
    paths; keeping two structures in step by hand across all of them is how a
    lease ends up renewed after release, or dropped while still held.
    """

    def __init__(self, renewer: "LeaseRenewer | None" = None) -> None:
        self._lock = Lock()
        self._leases: dict[str, str] = {}
        self._renewer = renewer

    def add(self, pool: str, owner: str) -> None:
        # The mirror runs under the same lock as the mutation it mirrors. A
        # discard landing between the two would pop the pool before the
        # renewer had it, so its own mirror would find nothing to remove and
        # this add would then register a lease that is already released —
        # renewed until its owner is unregistered. The renewer's own state is
        # plain dict/set work that takes no lock and never calls back in
        # here, so holding this one across it cannot deadlock.
        with self._lock:
            self._leases[pool] = owner
            if self._renewer is not None:
                self._renewer.add(owner, pool)

    def discard(self, pool: str) -> str | None:
        with self._lock:
            owner = self._leases.pop(pool, None)
            if owner is not None and self._renewer is not None:
                self._renewer.discard(owner, pool)
        return owner

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
        operation_timeout_seconds: float = 2.0,
        max_connections: int = 32,
    ) -> None:
        self.logger = logger
        self.redis_config = redis_config
        self.key_prefix = key_prefix
        self.operation_timeout_seconds = max(0.1, operation_timeout_seconds)
        self.max_connections = max(1, max_connections)
        self._registry: RedisClientRegistry | None = None
        # Scripts are registered against the client that will run them, so a
        # second event loop's client gets its own registration rather than
        # reusing a Script object bound to another loop's connection pool.
        #
        # Keyed weakly by the client object, not by id(): the registry drops a
        # client whose loop has closed (a worker thread restarted between a
        # stop() and a start()), and a fresh client allocated at that same
        # address would otherwise be handed Scripts still bound to the closed
        # one. Weak keys also let the entry disappear with the client instead
        # of accumulating one per restart.
        self._scripts_by_client: "WeakKeyDictionary[Redis, tuple[AsyncScript, AsyncScript]]" = (
            WeakKeyDictionary()
        )

    async def initialize(self) -> None:
        if self._registry is not None:
            return
        registry = RedisClientRegistry(
            self.logger,
            self.redis_config,
            max_connections=self.max_connections,
            socket_timeout_seconds=self.operation_timeout_seconds,
        )
        client = registry.client()
        try:
            await client.ping()
        except BaseException:
            await registry.aclose()
            raise
        self._registry = registry

    async def cleanup(self) -> None:
        registry = self._registry
        self._registry = None
        self._scripts_by_client.clear()
        if registry is not None:
            await registry.aclose()

    def _key(self, pool: str) -> str:
        return f"{self.key_prefix}:{pool}"

    def _client(self) -> Redis:
        """The Redis client bound to the calling event loop.

        Callers reach Redis directly from whichever loop they run on — the
        consumers' worker loop included — rather than hopping onto the main
        loop, so no command is ever cancelled by a cross-loop deadline.
        """
        if self._registry is None:
            raise RuntimeError(
                "DistributedConcurrencyManager is not initialized"
            )
        return self._registry.client()

    def _scripts(self) -> tuple[AsyncScript, AsyncScript]:
        """``(acquire, renew)`` registered against this loop's client.

        ``register_script`` caches the SHA and calls EVALSHA (falling back to
        EVAL once on NOSCRIPT) instead of re-sending the script body on every
        call, which happens on every consumed message.
        """
        client = self._client()
        scripts = self._scripts_by_client.get(client)
        if scripts is None:
            scripts = (
                client.register_script(_ACQUIRE_SCRIPT),
                client.register_script(_RENEW_SCRIPT),
            )
            self._scripts_by_client[client] = scripts
        return scripts

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
        acquire_script, _ = self._scripts()
        result = await acquire_script(
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
        _, renew_script = self._scripts()
        result = await renew_script(
            keys=[self._key(pool)],
            args=[owner, lease_ms, lease_ms * 2],
        )
        return bool(result)

    async def renew_many(
        self,
        leases: "Sequence[tuple[str, str]]",
        lease_seconds: float,
    ) -> dict[tuple[str, str], bool]:
        """Renew every ``(pool, owner)`` in one pipelined round trip.

        The renewal loop used to run per-message, so N in-flight records meant
        N background tasks each issuing their own renew every interval. At the
        in-flight widths this pipeline runs at that is a standing load on
        Redis proportional to how busy the node is — precisely when it can
        least afford it. One round trip for the whole set is O(1) instead.
        """
        if not leases:
            return {}
        lease_ms = max(1, int(lease_seconds * 1000))
        _, renew_script = self._scripts()
        client = self._client()
        async with client.pipeline(transaction=False) as pipe:
            for pool, owner in leases:
                # Awaited even though it only buffers: AsyncScript.__call__ is
                # a coroutine, and leaving it unawaited silently queues
                # nothing, so execute() would come back empty.
                await renew_script(
                    keys=[self._key(pool)],
                    args=[owner, lease_ms, lease_ms * 2],
                    client=pipe,
                )
            results = await pipe.execute()
        # strict: a reply shorter than the batch would otherwise drop leases
        # from the map, and a missing entry reads as "renewed" in
        # _renew_once — fail-open for an exclusivity lease. Redis returns one
        # result per queued command, so a mismatch is a bug worth raising.
        return {
            lease: bool(result) for lease, result in zip(leases, results, strict=True)
        }

    async def release(self, pool: str, owner: str) -> None:
        # Plain ZREM is already atomic; no Lua script needed here.
        await self._client().zrem(self._key(pool), owner)
