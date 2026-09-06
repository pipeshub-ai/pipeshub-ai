"""Redis cache for the `virtualRecordId -> recordId` maps that gate every search.

`get_accessible_virtual_record_ids` recomputes a user's entire accessible
corpus on every search call — an 8-path permission traversal per connector plus
a KB traversal, each returning every accessible record. That was ~8% of the
query service's CPU, repeated once per search per turn even though the answer
almost never changes between turns.

The maps are cached at the granularity they are computed, not as one merged
blob, so a single connector's sync only drops that connector's entry:

* KB and app-level connectors produce **user-independent** maps (access to the
  KB/app implies access to its records), so those entries are shared by every
  user in the org. Which KBs and apps a given user may reach is still resolved
  live on every request — only the contents are cached.
* Record-level connectors sync real per-record ACLs, so their entries are keyed
  per user. They live in one hash per connector (field = user id) so a single
  DEL invalidates every user at once, with no SCAN and no set-index.

Every entry carries a TTL. Event-driven invalidation is best-effort by design —
it must never fail a sync or a delete — so the TTL is the backstop that bounds
staleness when an invalidation is lost.

Redis is never allowed to break or stall a search: any error falls through to
the live query and trips a short circuit-breaker so the next requests skip
Redis entirely instead of paying a timeout each.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import zlib
from typing import TYPE_CHECKING

from app.services.cache.interface import (
    IAccessibleRecordsCache,
    Loader,
    NoopAccessibleRecordsCache,
)
from app.services.redis.config import ClientOptions, RedisConnectionConfig
from app.services.redis.connection_provider_factory import get_redis_provider

if TYPE_CHECKING:
    from logging import Logger

    from app.config.configuration_service import ConfigurationService
    from app.config.constants.arangodb import Connectors
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
    from app.services.redis.connection_provider import RedisClient

__all__ = ["AccessibleRecordsCache", "AccessibleRecordsInvalidator"]

_DISABLED_VALUES = {"0", "off", "false", "no"}


def _cache_enabled_from_env() -> bool:
    raw = os.getenv(AccessibleRecordsCache.ENV_ENABLED)
    return raw is None or raw.strip().lower() not in _DISABLED_VALUES


def _ttl_from_env(default: int) -> int:
    raw = os.getenv(AccessibleRecordsCache.ENV_TTL)
    if raw is None:
        return default
    try:
        return max(int(raw), 1)
    except ValueError:
        return default


class AccessibleRecordsCache(IAccessibleRecordsCache):
    """Read-through cache of accessible-record maps, shared across workers."""

    KEY_PREFIX = "pipeshub:accessible_records:v1"
    ENV_ENABLED = "PIPESHUB_ACCESSIBLE_RECORDS_CACHE"
    ENV_TTL = "PIPESHUB_ACCESSIBLE_RECORDS_CACHE_TTL"
    DEFAULT_TTL_SECONDS = 300
    OP_TIMEOUT_SECONDS = 2.0
    DOWN_BACKOFF_SECONDS = 30.0
    # Striped locks, not one per key. A per-key table could only be trimmed of
    # *unlocked* entries, so under enough concurrent misses on distinct keys it
    # grew without limit. A fixed stripe array is bounded by construction; two
    # unrelated keys sharing a stripe just serialise their (already expensive)
    # miss, which is the point of the lock anyway.
    LOCK_STRIPES = 1024

    def __init__(
        self,
        logger: "Logger",
        redis_client: "RedisClient | None",
        ttl_seconds: int,
        enabled: bool,  # noqa: FBT001 - positional keeps the test fakes terse
        key_namespace: str = "",
    ) -> None:
        self.logger = logger
        self._redis = redis_client
        self._ttl = ttl_seconds
        self._enabled = enabled and redis_client is not None
        self._down_until = 0.0
        # REDIS_KEY_NAMESPACE (R9): set by `create()` from the provider;
        # stays empty when a raw `redis_client` is injected directly without
        # a namespace (mostly tests), same as an unset namespace.
        self._key_namespace = key_namespace
        self._locks: tuple[asyncio.Lock, ...] = tuple(
            asyncio.Lock() for _ in range(self.LOCK_STRIPES)
        )

    @classmethod
    async def create(
        cls, logger: "Logger", config_service: "ConfigurationService"
    ) -> IAccessibleRecordsCache:
        """Build a cache. Never raises — a failure yields a disabled cache.

        Returns ``NoopAccessibleRecordsCache`` rather than a
        ``redis_client=None`` instance of this class when the cache is off:
        the disabled behaviour (every read falls through to ``loader()``) has
        nothing to do with Redis, and callers depend on
        ``IAccessibleRecordsCache``, not this implementation.
        """
        ttl = _ttl_from_env(cls.DEFAULT_TTL_SECONDS)
        if not _cache_enabled_from_env():
            logger.info("Accessible-records cache disabled via %s", cls.ENV_ENABLED)
            return NoopAccessibleRecordsCache()

        client = None
        try:
            redis_config = await config_service.get_redis_config()
            provider = get_redis_provider(
                RedisConnectionConfig.from_host_port(
                    host=redis_config.host,
                    port=redis_config.port,
                    password=redis_config.password,
                    db=redis_config.db,
                    tls=redis_config.tls,
                )
            )
            client = provider.create_client(
                ClientOptions(
                    decode_responses=True,
                    socket_timeout_seconds=cls.OP_TIMEOUT_SECONDS,
                    socket_connect_timeout_seconds=cls.OP_TIMEOUT_SECONDS,
                )
            )
            await client.ping()
        except Exception as e:
            logger.warning(
                "Accessible-records cache unavailable (%s); falling back to live queries", str(e)
            )
            # `create_client()` hands out a caller-owned client (not the
            # provider's shared one) -- release it ourselves on failure, or
            # the ping-that-never-succeeded connection leaks for good.
            if client is not None:
                try:
                    await client.aclose()
                except Exception as close_error:
                    logger.debug(
                        "Error closing accessible-records cache client after setup failure: %s",
                        str(close_error),
                    )
            return NoopAccessibleRecordsCache()

        logger.info("Accessible-records cache ready (ttl=%ss)", ttl)
        return cls(logger, client, ttl, enabled=True, key_namespace=provider.key_namespace)

    @property
    def enabled(self) -> bool:
        """False while disabled, unconfigured, or inside the post-failure backoff."""
        if not self._enabled:
            return False
        return time.monotonic() >= self._down_until

    @property
    def ttl_seconds(self) -> int:
        return self._ttl

    async def close(self) -> None:
        client, self._redis = self._redis, None
        self._enabled = False
        if client is not None:
            try:
                await client.aclose()
            except Exception as e:
                self.logger.debug("Error closing accessible-records cache: %s", str(e))

    # ---- keys ---------------------------------------------------------

    def _namespaced_prefix(self) -> str:
        return f"{self._key_namespace}:{self.KEY_PREFIX}" if self._key_namespace else self.KEY_PREFIX

    def _kb_key(self, org_id: str, kb_id: str) -> str:
        return f"{self._namespaced_prefix()}:kb:{org_id}:{kb_id}"

    def _app_connector_key(self, org_id: str, connector_id: str) -> str:
        return f"{self._namespaced_prefix()}:capp:{org_id}:{connector_id}"

    def _user_connector_key(self, org_id: str, connector_id: str) -> str:
        return f"{self._namespaced_prefix()}:cusr:{org_id}:{connector_id}"

    # ---- read-through -------------------------------------------------

    async def get_or_compute_kb(
        self, org_id: str, kb_id: str, loader: Loader
    ) -> dict[str, str]:
        return await self._get_or_compute(self._kb_key(org_id, kb_id), None, loader)

    async def get_or_compute_app_connector(
        self, org_id: str, connector_id: str, loader: Loader
    ) -> dict[str, str]:
        return await self._get_or_compute(
            self._app_connector_key(org_id, connector_id), None, loader
        )

    async def get_or_compute_user_connector(
        self, org_id: str, connector_id: str, user_id: str, loader: Loader
    ) -> dict[str, str]:
        return await self._get_or_compute(
            self._user_connector_key(org_id, connector_id), user_id, loader
        )

    async def _get_or_compute(
        self, key: str, field: str | None, loader: Loader
    ) -> dict[str, str]:
        if not self.enabled:
            return await loader()

        cached = await self._read(key, field)
        if cached is not None:
            return cached
        # A failed read has already tripped the breaker. Every further Redis
        # call in this same request would wait out its own timeout, so one
        # outage cost three of them (read, re-read, write) on a single search.
        if not self.enabled:
            return await loader()

        lock_key = key if field is None else f"{key}#{field}"
        lock = self._lock_for(lock_key)

        async with lock:
            if not self.enabled:
                return await loader()
            # Another coroutine may have populated the entry while we queued.
            cached = await self._read(key, field)
            if cached is not None:
                return cached

            value = await loader()
            if self.enabled:
                await self._write(key, field, value)
            return value

    def _lock_for(self, lock_key: str) -> asyncio.Lock:
        """Stripe for this key. crc32 rather than hash() so the mapping is
        stable across processes and test runs."""
        return self._locks[zlib.crc32(lock_key.encode()) % len(self._locks)]

    async def _read(self, key: str, field: str | None) -> dict[str, str] | None:
        try:
            raw = await (
                self._redis.get(key) if field is None else self._redis.hget(key, field)
            )
        except Exception as e:
            self._mark_down("read", e)
            return None

        if raw is None:
            return None

        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return None

        if field is None:
            return payload if isinstance(payload, dict) else None

        # Hash fields carry their own timestamp: Redis expires whole keys only,
        # and the key's TTL is refreshed by every other user's write, so a field
        # would otherwise live forever under steady traffic.
        if not isinstance(payload, dict):
            return None
        written_at = payload.get("t")
        stored = payload.get("m")
        if not isinstance(written_at, (int, float)) or not isinstance(stored, dict):
            return None
        if time.time() - written_at > self._ttl:
            return None
        return stored

    async def _write(self, key: str, field: str | None, value: dict[str, str]) -> None:
        try:
            if field is None:
                await self._redis.set(key, json.dumps(value, separators=(",", ":")), ex=self._ttl)
            else:
                envelope = json.dumps({"t": int(time.time()), "m": value}, separators=(",", ":"))
                await self._redis.hset(key, field, envelope)
                await self._redis.expire(key, self._ttl)
        except Exception as e:
            self._mark_down("write", e)

    # ---- invalidation -------------------------------------------------

    async def invalidate_connector(self, org_id: str, connector_id: str) -> None:
        """Drop a connector's entry regardless of its permission model."""
        await self._delete(
            self._app_connector_key(org_id, connector_id),
            self._user_connector_key(org_id, connector_id),
        )

    async def invalidate_kb(self, org_id: str, kb_id: str) -> None:
        await self._delete(self._kb_key(org_id, kb_id))

    async def _delete(self, *keys: str) -> None:
        """Delete every key. Pipelined, one command per key (R5): a single
        multi-key ``DEL`` raises CROSSSLOT on a Redis Cluster/MemoryDB
        whenever the keys land in different hash slots, which
        ``invalidate_connector``'s two keys (``capp:`` and ``cusr:``) do.
        redis-py's ``ClusterPipeline`` routes each command to the right
        node; on standalone this is one round trip either way."""
        if not self.enabled:
            return
        try:
            async with self._redis.pipeline(transaction=False) as pipe:
                for key in keys:
                    pipe.delete(key)
                await pipe.execute()
        except Exception as e:
            self._mark_down("delete", e)

    # ---- failure handling ---------------------------------------------

    def _mark_down(self, op: str, error: Exception) -> None:
        """Skip Redis for a while so a dead server costs one timeout, not one per call."""
        first = time.monotonic() >= self._down_until
        self._down_until = time.monotonic() + self.DOWN_BACKOFF_SECONDS
        if first:
            self.logger.warning(
                "Accessible-records cache %s failed (%s); bypassing cache for %ss",
                op, str(error), self.DOWN_BACKOFF_SECONDS,
            )



class AccessibleRecordsInvalidator:
    """Invalidation façade for the services that write records.

    Resolves the owning org when the caller does not have it, and swallows every
    error: dropping a cache entry must never fail a sync, a delete, or the
    indexing pipeline. The cache TTL covers whatever this misses.
    """

    def __init__(
        self,
        logger: "Logger",
        cache: AccessibleRecordsCache,
        graph_provider: "IGraphDBProvider",
    ) -> None:
        self.logger = logger
        self.cache = cache
        self.graph_provider = graph_provider

    async def on_connector_sync_completed(
        self, connector_id: str, org_id: str | None = None
    ) -> None:
        try:
            if not connector_id:
                return
            org_id = org_id or await self._org_for_app(connector_id)
            if not org_id:
                # Returning silently here hid a total failure: the cache keeps
                # serving the pre-sync permission map until the TTL expires.
                self.logger.warning(
                    "Skipping accessible-records cache invalidation for connector %s: "
                    "could not resolve its org",
                    connector_id,
                )
                return
            await self.cache.invalidate_connector(org_id, connector_id)
        except Exception as e:
            self.logger.warning(
                "Could not invalidate accessible-records cache for connector %s: %s",
                connector_id, str(e),
            )

    async def on_kb_records_changed(self, kb_id: str, org_id: str | None = None) -> None:
        """No-op unless `kb_id` really is a KB — the generic delete paths this
        hangs off also fire for ordinary connectors, which invalidate on sync
        completion instead."""
        try:
            if not kb_id:
                return
            app = await self._app_doc(kb_id)
            if app is None:
                return
            from app.config.constants.arangodb import Connectors

            if app.get("type") != Connectors.KNOWLEDGE_BASE.value:
                return
            org_id = org_id or app.get("orgId")
            if not org_id:
                return
            await self.cache.invalidate_kb(org_id, kb_id)
        except Exception as e:
            self.logger.warning(
                "Could not invalidate accessible-records cache for KB %s: %s", kb_id, str(e)
            )

    async def on_record_indexed(
        self,
        connector_name: "Connectors | str | None" = None,
        connector_id: str | None = None,
        external_record_group_id: str | None = None,
        org_id: str | None = None,
    ) -> None:
        """Invalidate when a KB record becomes searchable.

        Deliberately KB-only. Connector records also flip to COMPLETED here, but
        a full sync does that thousands of times in a burst; dropping the
        connector entry per record would leave the cache empty exactly when the
        graph is busiest. Connectors invalidate once, on sync completion.
        """
        try:
            from app.config.constants.arangodb import Connectors

            name = getattr(connector_name, "value", connector_name)
            if name != Connectors.KNOWLEDGE_BASE.value:
                return
            kb_id = connector_id or external_record_group_id
            if not kb_id:
                return
            if not org_id:
                app = await self._app_doc(kb_id)
                org_id = (app or {}).get("orgId")
            if not org_id:
                return
            await self.cache.invalidate_kb(org_id, kb_id)
        except Exception as e:
            self.logger.warning(
                "Could not invalidate accessible-records cache after indexing: %s", str(e)
            )

    async def _app_doc(self, app_id: str) -> dict | None:
        from app.config.constants.arangodb import CollectionNames

        return await self.graph_provider.get_document(app_id, CollectionNames.APPS.value)

    async def _org_for_app(self, app_id: str) -> str | None:
        """The org that owns this app.

        Connector apps do not carry `orgId` as a property -- only KB apps do --
        so the ORG_APP_RELATION edge is the answer for every connector, not a
        rare fallback. Reading only the property meant every connector-scoped
        invalidation resolved to None and silently did nothing.
        """
        from app.config.constants.arangodb import CollectionNames

        app = await self._app_doc(app_id)
        org_id = (app or {}).get("orgId")
        if org_id:
            return org_id

        edges = await self.graph_provider.get_edges_to_node(
            f"{CollectionNames.APPS.value}/{app_id}",
            CollectionNames.ORG_APP_RELATION.value,
        )
        for edge in edges or []:
            # A malformed entry must cost only itself: letting .get() raise here
            # would abort the loop and skip invalidation altogether.
            if not isinstance(edge, dict):
                continue
            # neo4j returns a bare id in `from_id`; arango returns a document
            # handle in `_from` ("organizations/<key>").
            raw = str(edge.get("from_id") or edge.get("_from") or "")
            if raw:
                return raw.rsplit("/", 1)[-1]
        return None
