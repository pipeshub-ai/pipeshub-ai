"""Redis cache of query -> search-results, keyed by org and permission scope.

`search_with_filters` embeds every query and runs a hybrid Qdrant search on
every call, even when a near-identical question was just asked. This cache
sits in front of `RetrievalService._execute_parallel_searches` and serves a
cosine-similar prior query's results instead of re-running the search.

Permission-aware by construction, not by filtering after the fact: entries
are scoped by `(org_id, acl_signature)`, where `acl_signature` is a hash of
the caller's accessible-virtual-record-id set -- the same set already
computed for the live permission filter on every search (see
`compute_acl_signature`). Two users only ever share an entry if they have
identical access; any ACL difference is a different cache partition, so a
similarity hit can never leak another user's search results.

Qdrant has no physical multi-tenancy for this collection (org is a payload
filter, not a separate collection), so this cache deliberately does not use
Qdrant for the similarity lookup -- that would reopen the same isolation
problem this design is working around. Instead each `(org, acl_signature)`
partition is a bounded Redis hash of recent query embeddings, scanned with a
plain cosine comparison, following the same in-memory-scan approach
`SemanticSkillIndex` already uses elsewhere in this codebase for a smaller,
structurally similar problem.

Unlike `AccessibleRecordsCache`, there is no event-driven invalidation here.
That cache invalidates by connector/KB because writes naturally know which
connector or KB changed. A write here would need to know which
`(org, acl_signature)` partitions it affects, which is exactly the expensive
computation this cache exists to avoid recomputing -- so precise
invalidation would cost more than the cache saves. The TTL is deliberately
short (60s default, vs. 300s for the permission cache) because query
*results* go stale faster than permission *maps* do: new documents get
indexed continuously, but a user's access rarely changes minute to minute.

Redis is never allowed to break or stall a search: any error falls through
to a live search and trips a short circuit-breaker so the next requests skip
Redis entirely instead of paying a timeout each.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import time
import zlib
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from redis.asyncio import Redis

if TYPE_CHECKING:
    from logging import Logger

    from app.config.configuration_service import ConfigurationService

__all__ = ["SemanticQueryCache", "compute_acl_signature"]

Loader = Callable[[], Awaitable[list[dict[str, Any]]]]

_DISABLED_VALUES = {"0", "off", "false", "no"}


def compute_acl_signature(accessible_virtual_id_to_record_id: dict[str, str]) -> str:
    """Hash of the caller's accessible-virtual-record-id set.

    Deterministic and order-independent so the same access grants always
    produce the same signature. This is a permission-*partition* key, not a
    secret -- collisions would only merge two callers' cache entries when
    they already have provably identical access.
    """
    joined = ",".join(sorted(accessible_virtual_id_to_record_id.keys()))
    return hashlib.sha256(joined.encode()).hexdigest()[:24]


def _cache_enabled_from_env() -> bool:
    raw = os.getenv(SemanticQueryCache.ENV_ENABLED)
    return raw is None or raw.strip().lower() not in _DISABLED_VALUES


def _ttl_from_env(default: int) -> int:
    raw = os.getenv(SemanticQueryCache.ENV_TTL)
    if raw is None:
        return default
    try:
        return max(int(raw), 1)
    except ValueError:
        return default


def _threshold_from_env(default: float) -> float:
    raw = os.getenv(SemanticQueryCache.ENV_SIMILARITY_THRESHOLD)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if 0.0 < value <= 1.0 else default


class SemanticQueryCache:
    """Read-through cache of query embeddings and their search results."""

    KEY_PREFIX = "pipeshub:semantic_query_cache:v1"
    ENV_ENABLED = "PIPESHUB_SEMANTIC_CACHE"
    ENV_TTL = "PIPESHUB_SEMANTIC_CACHE_TTL"
    ENV_SIMILARITY_THRESHOLD = "PIPESHUB_SEMANTIC_CACHE_SIMILARITY_THRESHOLD"
    DEFAULT_TTL_SECONDS = 60
    DEFAULT_SIMILARITY_THRESHOLD = 0.97
    OP_TIMEOUT_SECONDS = 2.0
    DOWN_BACKOFF_SECONDS = 30.0
    LOCK_STRIPES = 1024
    # Bounds the per-lookup cosine scan and the size of one (org, acl) partition.
    # Not precise LRU -- entries beyond this are simply skipped on read, and the
    # TTL is what actually bounds a partition's lifetime. A follow-up could trim
    # a partition on write if this turns out to matter in practice.
    MAX_CANDIDATES_SCANNED = 200

    def __init__(
        self,
        logger: "Logger",
        redis_client: Redis | None,
        ttl_seconds: int,
        similarity_threshold: float,
        enabled: bool,  # noqa: FBT001 - positional keeps the test fakes terse
    ) -> None:
        self.logger = logger
        self._redis = redis_client
        self._ttl = ttl_seconds
        self._threshold = similarity_threshold
        self._enabled = enabled and redis_client is not None
        self._down_until = 0.0
        self._locks: tuple[asyncio.Lock, ...] = tuple(
            asyncio.Lock() for _ in range(self.LOCK_STRIPES)
        )

    @classmethod
    async def create(
        cls, logger: "Logger", config_service: "ConfigurationService"
    ) -> "SemanticQueryCache":
        """Build a cache client. Never raises -- a failure yields a disabled cache."""
        ttl = _ttl_from_env(cls.DEFAULT_TTL_SECONDS)
        threshold = _threshold_from_env(cls.DEFAULT_SIMILARITY_THRESHOLD)
        if not _cache_enabled_from_env():
            logger.info("Semantic query cache disabled via %s", cls.ENV_ENABLED)
            return cls(logger, None, ttl, threshold, enabled=False)

        try:
            redis_config = await config_service.get_redis_config()
            client = Redis(
                host=redis_config.host,
                port=redis_config.port,
                password=redis_config.password,
                db=redis_config.db,
                decode_responses=True,
                socket_timeout=cls.OP_TIMEOUT_SECONDS,
                socket_connect_timeout=cls.OP_TIMEOUT_SECONDS,
            )
            await client.ping()
        except Exception as e:
            logger.warning(
                "Semantic query cache unavailable (%s); falling back to live search", str(e)
            )
            return cls(logger, None, ttl, threshold, enabled=False)

        logger.info(
            "Semantic query cache ready (ttl=%ss, similarity_threshold=%s)", ttl, threshold
        )
        return cls(logger, client, ttl, threshold, enabled=True)

    @property
    def enabled(self) -> bool:
        """False while disabled, unconfigured, or inside the post-failure backoff."""
        if not self._enabled:
            return False
        return time.monotonic() >= self._down_until

    async def close(self) -> None:
        client, self._redis = self._redis, None
        self._enabled = False
        if client is not None:
            try:
                await client.aclose()
            except Exception as e:
                self.logger.debug("Error closing semantic query cache: %s", str(e))

    # ---- keys -----------------------------------------------------------

    def _partition_key(self, org_id: str, acl_signature: str) -> str:
        return f"{self.KEY_PREFIX}:{org_id}:{acl_signature}"

    @staticmethod
    def _field_for(query: str) -> str:
        """Same query text overwrites its own entry instead of accumulating
        duplicates as the partition sees repeat traffic."""
        return hashlib.sha256(query.encode()).hexdigest()[:16]

    # ---- read-through -----------------------------------------------------

    async def get_or_compute(
        self,
        org_id: str,
        acl_signature: str,
        query: str,
        query_embedding: list[float],
        limit: int,
        loader: Loader,
    ) -> list[dict[str, Any]]:
        if not self.enabled:
            return await loader()

        key = self._partition_key(org_id, acl_signature)
        cached = await self._find_similar(key, query_embedding, limit)
        if cached is not None:
            return cached
        # A failed read has already tripped the breaker; avoid paying a
        # second timeout on the write below.
        if not self.enabled:
            return await loader()

        lock = self._lock_for(key)
        async with lock:
            if not self.enabled:
                return await loader()
            # Another coroutine may have populated a matching entry while we queued.
            cached = await self._find_similar(key, query_embedding, limit)
            if cached is not None:
                return cached

            results = await loader()
            if self.enabled:
                await self._store(key, query, query_embedding, limit, results)
            return results

    def _lock_for(self, key: str) -> asyncio.Lock:
        """crc32 rather than hash() so the mapping is stable across processes
        and test runs."""
        return self._locks[zlib.crc32(key.encode()) % len(self._locks)]

    async def _find_similar(
        self, key: str, query_embedding: list[float], limit: int
    ) -> list[dict[str, Any]] | None:
        try:
            raw_entries = await self._redis.hgetall(key)
        except Exception as e:
            self._mark_down("read", e)
            return None

        if not raw_entries:
            return None

        now = time.time()
        best_score = self._threshold
        best_results: list[dict[str, Any]] | None = None
        for i, raw in enumerate(raw_entries.values()):
            if i >= self.MAX_CANDIDATES_SCANNED:
                break
            try:
                envelope = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if not isinstance(envelope, dict):
                continue
            written_at = envelope.get("t")
            embedding = envelope.get("e")
            cached_limit = envelope.get("limit")
            results = envelope.get("r")
            if (
                not isinstance(written_at, (int, float))
                or not isinstance(embedding, list)
                or not isinstance(cached_limit, int)
                or not isinstance(results, list)
            ):
                continue
            if now - written_at > self._ttl:
                continue
            if cached_limit < limit:
                # Cached entry has fewer results than this request wants.
                continue
            score = self._cosine_similarity(query_embedding, embedding)
            if score >= best_score:
                best_score = score
                best_results = results[:limit]

        return best_results

    async def _store(
        self,
        key: str,
        query: str,
        query_embedding: list[float],
        limit: int,
        results: list[dict[str, Any]],
    ) -> None:
        try:
            envelope = json.dumps(
                {"t": time.time(), "q": query, "e": query_embedding, "limit": limit, "r": results},
                separators=(",", ":"),
            )
            field = self._field_for(query)
            await self._redis.hset(key, field, envelope)
            await self._redis.expire(key, self._ttl)
        except Exception as e:
            self._mark_down("write", e)

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return dot / (norm_a * norm_b)

    # ---- failure handling ---------------------------------------------

    def _mark_down(self, op: str, error: Exception) -> None:
        """Skip Redis for a while so a dead server costs one timeout, not one per call."""
        first = time.monotonic() >= self._down_until
        self._down_until = time.monotonic() + self.DOWN_BACKOFF_SECONDS
        if first:
            self.logger.warning(
                "Semantic query cache %s failed (%s); bypassing cache for %ss",
                op, str(error), self.DOWN_BACKOFF_SECONDS,
            )
