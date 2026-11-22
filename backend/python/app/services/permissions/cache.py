"""`ViewerScopeCache`: Redis, keyed by (org, user, acl_version). A prefilter input only; it never admits.

The ACL version is a counter per organisation plus one global counter (for events that name no organisation);
their sum is the version. A counter that does not exist is created with an epoch-microsecond base (`SET NX`), so a
counter lost to a failover, an eviction or a restore restarts far above every value it ever had and cannot make an
old entry current again. A permission event bumps it *after* its write committed, so the next lookup uses a
new key and cannot see an entry computed before the change. Nothing is deleted and nothing needs a scan.

Redis trouble never widens anything:
- the version cannot be read -> no cache is used, the caller computes from the graph;
- a bump fails -> this process stops serving cached scopes until the bump lands (retried in the background, each
  failed bump with its own deadline of one TTL; other processes are covered by the entry TTL, which is the most a
  lost bump can cost);
- an entry that does not parse is a miss.

Without a shared Redis (``REDIS_MODE=postgres``) each process keeps its own cache, and a bump reaches the other processes as a
PostgreSQL NOTIFY on its own channel (`PgCacheBus`): each one then moves its own counter. It fails closed in the same way: while the
LISTEN connection is down nothing is served from the cache, and a reconnect moves the global counter, because notifications sent while
it was away are lost. No direct address to LISTEN on (``PG_LISTEN_URL``, or ``PG_POOLER=none``) means no cache at all.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Protocol, cast

from app.core.org_context import spawn_detached

from .metrics import SCOPE_CACHE
from .scopes import ViewerScopes

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from app.config.configuration_service import ConfigurationService
    from app.services.cache.pg_invalidation import PgCacheBus

_logger = logging.getLogger(__name__)
_GLOBAL = "*"


class RedisLike(Protocol):
    """The four commands used; `redis.asyncio.Redis` (decode_responses=True) satisfies it."""

    async def get(self, name: str) -> str | None: ...
    async def mget(self, keys: Sequence[str]) -> list[str | None]: ...
    async def set(self, name: str, value: str, ex: int | None = None, *, nx: bool = False) -> object: ...
    async def incr(self, name: str) -> int: ...


class ViewerScopeCache:
    KEY_PREFIX = "pipeshub:viewer_scopes:v1"
    DEFAULT_TTL_SECONDS = 300
    PG_STATE_TTL_SECONDS = 60      # REDIS_MODE=postgres: the longest an entry can outlive a bump that was published and lost
    CHANNEL = "pipeshub_viewer_scopes"
    DOWN_BACKOFF_SECONDS = 15.0
    BUMP_RETRY_DELAYS_SECONDS = (0.5, 1.0, 2.0, 5.0, 10.0)

    def __init__(
        self, client: Callable[[], RedisLike] | None, ttl_seconds: int = DEFAULT_TTL_SECONDS, namespace: str = "",
        connect: Callable[[], Awaitable[Callable[[], RedisLike]]] | None = None,
    ) -> None:
        """`client` returns the Redis client to use now (a provider's client is bound to the running loop). Without
        one, `connect` is tried again (at most once per backoff) whenever the cache is used, so a process that began
        while Redis was down does not stay without a cache, and therefore without bumps, for its whole life."""
        self._client = client
        self._connect = connect
        self._next_connect = 0.0
        self._ttl = ttl_seconds
        self._prefix = f"{namespace}:{self.KEY_PREFIX}" if namespace else self.KEY_PREFIX
        self._down_until = 0.0
        # Orgs (or the global counter) whose bump has not landed yet, with the time their entries are all expired.
        self._unbumped: dict[str, float] = {}
        self._retry: asyncio.Task[None] | None = None
        self._bus: PgCacheBus | None = None      # REDIS_MODE=postgres: bumps reach the other processes through it

    def _ver_key(self, org_id: str) -> str:
        return f"{self._prefix}:ver:{org_id}"

    def _key(self, org_id: str, user_key: str, version: int) -> str:
        return f"{self._prefix}:vs:{org_id}:{user_key}:{version}"

    @classmethod
    async def create(cls, config_service: ConfigurationService, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> ViewerScopeCache:
        """A cache on the deployment's Redis. Never raises: when Redis cannot be reached the cache has no client
        and every lookup bypasses it, until a later use connects. With ``REDIS_MODE=postgres``, see `_with_bus`."""
        from app.services.redis.config import RedisConnectionConfig
        from app.services.redis.connection_provider_factory import (
            get_redis_provider,
            uses_pg_state,
        )

        if uses_pg_state():
            return await cls._with_bus(config_service, min(ttl_seconds, cls.PG_STATE_TTL_SECONDS))

        async def connect() -> Callable[[], RedisLike]:
            conf = await config_service.get_redis_config()
            provider = get_redis_provider(RedisConnectionConfig.from_host_port(
                host=conf.host, port=conf.port, password=conf.password, db=conf.db, tls=conf.tls))
            await provider.get_client().ping()  # pyright: ignore[reportUnknownMemberType]
            cache._prefix = f"{provider.key_namespace}:{cls.KEY_PREFIX}" if provider.key_namespace else cls.KEY_PREFIX
            return cast("Callable[[], RedisLike]", provider.get_client)

        cache = cls(None, ttl_seconds, connect=connect)
        await cache._ensure_client()
        return cache

    @classmethod
    async def _with_bus(cls, config_service: ConfigurationService, ttl_seconds: int) -> ViewerScopeCache:
        """``REDIS_MODE=postgres``: this process's own cache, its bumps sent to the others by NOTIFY. Without a way to deliver
        them, or when the channel cannot be set up, a cache with no client: every lookup computes from the graph."""
        from app.services.cache.pg_invalidation import PgCacheBus
        from app.services.redis.config import RedisConnectionConfig
        from app.services.redis.connection_provider_factory import get_redis_provider

        try:
            conf = await config_service.get_redis_config()
            provider = get_redis_provider(RedisConnectionConfig.from_host_port(
                host=conf.host, port=conf.port, password=conf.password, db=conf.db, tls=conf.tls))
            cache = cls(cast("Callable[[], RedisLike]", provider.get_client), ttl_seconds, namespace=provider.key_namespace)
            bus = PgCacheBus(cache._apply_remote, cache._flush_local, channel=cls.CHANNEL)
            if not bus.available:
                _logger.warning("viewer-scope cache off: REDIS_MODE=postgres needs a direct PostgreSQL address (PG_LISTEN_URL, or "
                                "PG_POOLER=none) to send its bumps to the other processes; computing from the graph")
                return cls(None, ttl_seconds)
            cache._bus = bus
            bus.start()
        except Exception as exc:
            _logger.warning("viewer-scope cache off: its invalidation channel could not be set up (%s); computing from the graph",
                            type(exc).__name__)
            return cls(None, ttl_seconds)
        return cache

    async def _ensure_client(self) -> None:
        if self._client is not None or self._connect is None or time.monotonic() < self._next_connect:
            return
        try:
            self._client = await self._connect()
        except Exception as exc:
            self._next_connect = time.monotonic() + self.DOWN_BACKOFF_SECONDS
            _logger.warning("viewer-scope cache unavailable (%s); computing from the graph", type(exc).__name__)

    @property
    def usable(self) -> bool:
        now = time.monotonic()
        for target in [t for t, deadline in self._unbumped.items() if deadline <= now]:
            del self._unbumped[target]  # every entry the lost bump should have dropped has expired by now
        if self._bus is not None and not self._bus.healthy():
            return False      # fail closed: another process's bump may not arrive
        return self._client is not None and now >= self._down_until and not self._unbumped

    def _trip(self, op: str, exc: Exception) -> None:
        self._down_until = time.monotonic() + self.DOWN_BACKOFF_SECONDS
        _logger.warning("viewer-scope cache %s failed (%s); computing from the graph", op, type(exc).__name__)

    async def version(self, org_id: str) -> int | None:
        """The ACL version of the organisation, or None when it cannot be trusted (Redis down, a bump pending)."""
        await self._ensure_client()
        if not self.usable:
            return None
        assert self._client is not None
        try:
            client = self._client()
            keys = [self._ver_key(_GLOBAL), self._ver_key(org_id)]
            parts = await client.mget(keys)
            if any(p is None for p in parts):
                for key, part in zip(keys, parts, strict=True):
                    if part is None:
                        await client.set(key, str(time.time_ns() // 1000), nx=True)
                parts = await client.mget(keys)
                if any(p is None for p in parts):
                    return None
            return sum(int(p) for p in parts if p is not None)
        except Exception as exc:
            self._trip("version read", exc)
            return None

    async def get(self, org_id: str, user_key: str, version: int) -> ViewerScopes | None:
        if self._client is None:
            return None
        try:
            raw = await self._client().get(self._key(org_id, user_key, version))
        except Exception as exc:
            self._trip("read", exc)
            return None
        if raw is None:
            return None
        try:
            found = ViewerScopes.from_json(raw)
        except (ValueError, KeyError, TypeError):
            return None
        # The key already names all three; a mismatch means a foreign or damaged entry.
        if (found.org_id, found.user_key, found.version) != (org_id, user_key, version):
            return None
        return found

    async def put(self, scopes: ViewerScopes) -> None:
        if self._client is None or scopes.version < 0:
            return
        try:
            await self._client().set(self._key(scopes.org_id, scopes.user_key, scopes.version), scopes.to_json(),
                                  ex=self._ttl)
        except Exception as exc:
            self._trip("write", exc)

    async def bump(self, org_id: str | None) -> None:
        """A permission event for this organisation (None: for any) committed. Never raises."""
        await self._ensure_client()
        if self._client is None:
            return
        target = org_id or _GLOBAL
        try:
            await self._bump_once(target)
        except Exception as exc:
            self._trip("bump", exc)
            self._unbumped[target] = time.monotonic() + self._ttl
            self._ensure_retry()

    def _ensure_retry(self) -> None:
        if self._retry is None or self._retry.done():
            try:
                self._retry = cast("asyncio.Task[None]", spawn_detached(self._retry_bumps(), name="viewer-scope-bump-retry"))
            except RuntimeError:  # no loop: the TTL is the backstop
                self._retry = None

    async def _retry_bumps(self) -> None:
        """Retry every pending bump until it lands or its own deadline (one TTL after it failed) has passed."""
        assert self._client is not None
        attempt = 0
        while self._unbumped:
            delays = self.BUMP_RETRY_DELAYS_SECONDS
            await asyncio.sleep(delays[min(attempt, len(delays) - 1)])
            attempt += 1
            now = time.monotonic()
            for target, deadline in list(self._unbumped.items()):
                if deadline <= now:
                    self._unbumped.pop(target, None)
                    continue
                try:
                    await self._bump_once(target)
                except Exception:  # still down
                    break
                self._unbumped.pop(target, None)

    async def _bump_once(self, target: str) -> None:
        """Move the counter here and, without a shared Redis, in every other process. A publish that fails raises, so the bump
        counts as lost: this process stops serving cached scopes and retries it, as for a Redis bump that failed."""
        assert self._client is not None
        await self._client().incr(self._ver_key(target))
        if self._bus is not None:
            await self._bus.publish("v", target, "")

    async def _apply_remote(self, kind: str, org_id: str, _scope_id: str) -> None:
        """A bump published by a process (this one included: its own notification comes back too, and one more move is harmless)."""
        if kind != "v" or self._client is None:
            await self._flush_local()
            return
        try:
            await self._client().incr(self._ver_key(org_id))
        except Exception as exc:
            self._trip("remote bump", exc)

    async def _flush_local(self) -> None:
        """Some bumps may have been missed (a reconnect, a message that could not be read): move the global counter, which is part
        of every version, so no entry cached before is used again."""
        if self._client is None:
            return
        try:
            await self._client().incr(self._ver_key(_GLOBAL))
        except Exception as exc:
            self._trip("flush", exc)

    async def aclose(self) -> None:
        if self._retry is not None and not self._retry.done():
            self._retry.cancel()
        bus, self._bus = self._bus, None
        if bus is not None:
            await bus.stop()


def count(result: str) -> None:
    SCOPE_CACHE.inc(result)
