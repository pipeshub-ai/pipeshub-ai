from __future__ import annotations

import asyncio
from enum import Enum
from threading import Lock
from typing import TYPE_CHECKING

from redis.exceptions import NoScriptError

from app.services.distributed.interface import IDistributedLeaseManager
from app.services.messaging.redis_client import RedisClientRegistry

if TYPE_CHECKING:
    from collections.abc import Sequence
    from logging import Logger

    from app.services.messaging.config import RedisConfig
    from app.services.messaging.lease import LeaseRenewer
    from app.services.redis.connection_provider import RedisClient as Redis


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


class DistributedConcurrencyManager(IDistributedLeaseManager):
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
        # SHAs, not registered Script objects (R6): `renew_many` pipelines
        # these scripts across keys that can land on different cluster
        # nodes, and redis-py's `ClusterPipeline` does not implement the
        # NOSCRIPT-then-reload recovery that a `Script` object relies on
        # (that recovery lives in the standalone `Pipeline` only). Loading
        # explicitly through the provider -- which loads onto every master
        # in cluster mode -- and issuing raw EVALSHA sidesteps that gap; a
        # NOSCRIPT hit (a master added after the initial load, or a
        # resharding mid-flight) is handled explicitly below instead.
        self._acquire_sha: str | None = None
        self._renew_sha: str | None = None
        # REDIS_KEY_NAMESPACE (R9): resolved once the provider is known, in
        # `initialize()`.
        self._key_namespace = ""

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
            self._acquire_sha = await registry.provider.load_script(_ACQUIRE_SCRIPT)
            self._renew_sha = await registry.provider.load_script(_RENEW_SCRIPT)
        except BaseException:
            await registry.aclose()
            raise
        self._registry = registry
        self._key_namespace = registry.provider.key_namespace

    async def cleanup(self) -> None:
        registry = self._registry
        self._registry = None
        self._acquire_sha = None
        self._renew_sha = None
        self._key_namespace = ""
        if registry is not None:
            await registry.aclose()

    def _key(self, pool: str) -> str:
        namespace = f"{self._key_namespace}:" if self._key_namespace else ""
        return f"{namespace}{self.key_prefix}:{pool}"

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

    async def _ensure_scripts_loaded(self) -> tuple[str, str]:
        if self._acquire_sha is None or self._renew_sha is None:
            if self._registry is None:
                raise RuntimeError("DistributedConcurrencyManager is not initialized")
            self._acquire_sha = await self._registry.provider.load_script(_ACQUIRE_SCRIPT)
            self._renew_sha = await self._registry.provider.load_script(_RENEW_SCRIPT)
        return self._acquire_sha, self._renew_sha

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
        acquire_sha, _ = await self._ensure_scripts_loaded()
        client = self._client()
        args = [self._key(pool), owner, lease_ms, limit, lease_ms * 2]
        try:
            result = await client.evalsha(acquire_sha, 1, *args)
        except NoScriptError:
            acquire_sha = await self._reload(_ACQUIRE_SCRIPT, is_acquire=True)
            result = await client.evalsha(acquire_sha, 1, *args)
        return bool(result)

    async def renew(
        self,
        pool: str,
        owner: str,
        lease_seconds: float,
    ) -> bool:
        lease_ms = max(1, int(lease_seconds * 1000))
        _, renew_sha = await self._ensure_scripts_loaded()
        client = self._client()
        args = [self._key(pool), owner, lease_ms, lease_ms * 2]
        try:
            result = await client.evalsha(renew_sha, 1, *args)
        except NoScriptError:
            renew_sha = await self._reload(_RENEW_SCRIPT, is_acquire=False)
            result = await client.evalsha(renew_sha, 1, *args)
        return bool(result)

    async def _reload(self, body: str, *, is_acquire: bool) -> str:
        if self._registry is None:
            raise RuntimeError("DistributedConcurrencyManager is not initialized")
        sha = await self._registry.provider.load_script(body)
        if is_acquire:
            self._acquire_sha = sha
        else:
            self._renew_sha = sha
        return sha

    async def renew_many(
        self,
        leases: "Sequence[tuple[str, str]]",
        lease_seconds: float,
    ) -> dict[tuple[str, str], bool]:
        """Renew every ``(pool, owner)`` in one batch instead of one renew
        call per background task.

        The renewal loop used to run per-message, so N in-flight records meant
        N background tasks each issuing their own renew every interval. At the
        in-flight widths this runs at that is a standing load on Redis
        proportional to how busy the node is — precisely when it can least
        afford it.

        On standalone that batch is still one pipelined round trip. On cluster
        it cannot be: redis-py's ``ClusterPipeline`` rejects EVALSHA outright
        (``RedisClusterException: Calling pipelined function evalsha is blocked
        when running redis in cluster mode``, confirmed against a live 3-master
        cluster), so leases are renewed with ``asyncio.gather`` bounded by the
        registry's pool size — an unbounded gather would exhaust the
        ``BlockingConnectionPool`` and time out waiting on itself.

        Errors propagate. A transient Redis failure must reach
        ``LeaseRenewer._run``, which has a deadline before it gives up on the
        held leases; mapping the failure to ``renewed=False`` here would make
        ``_renew_once`` drop every lease on the first blip.
        """
        if not leases:
            return {}
        lease_ms = max(1, int(lease_seconds * 1000))
        _, renew_sha = await self._ensure_scripts_loaded()
        client = self._client()

        try:
            results = await self._run_renewals(client, renew_sha, leases, lease_ms)
        except NoScriptError:
            renew_sha = await self._reload(_RENEW_SCRIPT, is_acquire=False)
            results = await self._run_renewals(client, renew_sha, leases, lease_ms)

        # strict: a reply shorter than the batch would otherwise drop leases
        # from the map, and a missing entry reads as "renewed" in
        # _renew_once — fail-open for an exclusivity lease. Redis returns one
        # result per queued command, so a mismatch is a bug worth raising.
        return {
            lease: bool(result)
            for lease, result in zip(leases, results, strict=True)
        }

    async def _run_renewals(
        self,
        client: "Redis",
        sha: str,
        leases: "Sequence[tuple[str, str]]",
        lease_ms: int,
    ) -> list:
        args = [
            (self._key(pool), owner, lease_ms, lease_ms * 2) for pool, owner in leases
        ]
        if not self._is_cluster():
            async with client.pipeline(transaction=False) as pipe:
                for key, owner, ms, ttl in args:
                    pipe.evalsha(sha, 1, key, owner, ms, ttl)
                return await pipe.execute()

        # Cluster: EVALSHA cannot be pipelined, so issue them concurrently but
        # never more at once than the pool can hand out connections.
        limit = asyncio.Semaphore(
            self._registry.max_connections if self._registry is not None else 1
        )

        async def _one(key: str, owner: str, ms: int, ttl: int) -> object:
            async with limit:
                return await client.evalsha(sha, 1, key, owner, ms, ttl)

        # `return_exceptions=True` so every EVALSHA finishes before this
        # returns. Without it, a single failure (e.g. NOSCRIPT) makes
        # `gather` raise while sibling calls are still in flight; the caller
        # then reloads the script and issues a *second* concurrent batch
        # on top of the first, doubling the in-flight EVALSHA count past
        # the pool-sized semaphore this fan-out exists to respect.
        results = await asyncio.gather(*(_one(*a) for a in args), return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result
        return list(results)

    def _is_cluster(self) -> bool:
        return self._registry is not None and self._registry.provider.is_cluster

    async def release(self, pool: str, owner: str) -> None:
        # Plain ZREM is already atomic; no Lua script needed here.
        await self._client().zrem(self._key(pool), owner)
