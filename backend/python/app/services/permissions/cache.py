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

    def _ver_key(self, org_id: str) -> str:
        return f"{self._prefix}:ver:{org_id}"

    def _key(self, org_id: str, user_key: str, version: int) -> str:
        return f"{self._prefix}:vs:{org_id}:{user_key}:{version}"

    @classmethod
    async def create(cls, config_service: ConfigurationService, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> ViewerScopeCache:
        """A cache on the deployment's Redis. Never raises: when Redis cannot be reached the cache has no client
        and every lookup bypasses it, until a later use connects."""
        from app.services.redis.config import RedisConnectionConfig
        from app.services.redis.connection_provider_factory import get_redis_provider

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
            await self._client().incr(self._ver_key(target))
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
                    await self._client().incr(self._ver_key(target))
                except Exception:  # still down
                    break
                self._unbumped.pop(target, None)

    async def aclose(self) -> None:
        if self._retry is not None and not self._retry.done():
            self._retry.cancel()


def count(result: str) -> None:
    SCOPE_CACHE.inc(result)
