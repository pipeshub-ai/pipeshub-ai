"""Semantic caching service for plain-RAG chat.

Implements Section 4.1-4.3 of the semantic caching spec:
1. Bucket layout with `{org}` cluster-safe hash tag:
   - Bucket Hash: `pipeshub:semcache:v1:{{{org}}}:{epoch}:{user_or_shared}:{config_hash}:{context_digest}`
   - Parallel LRU Sorted Set: `...:lru`
   - Data Key: `pipeshub:semcache:v1:{{{org}}}:data:{entry_id}`
   - Epoch: `pipeshub:semcache:epoch:{{{org}}}`
2. Deterministic SHA-256 `context_digest` over sorted record tuples.
3. Deterministic `config_hash` over template/config inputs only (strictly excluding user identity, time, credentials).
4. Atomic Lua store script (no JSON parsing in Lua; caps entries and evicts LRU entries together with their data keys).
5. Read-time TTL validation to prevent busy-bucket age extension.
6. Fail-open everywhere: circuit breaker (5 consecutive failures opens for 30s) and timeouts never raise to caller.
7. Mode "off" performs zero Redis operations.
8. Strictly privacy-preserving: never logs or emits query text, answer text, or raw embeddings.
"""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false, reportMissingImports=false
from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

import numpy as np

from app.services.cache.config import (
    SemanticCacheConfig,
    get_semantic_cache_config,
)
from app.services.cache.guard import compare as guard_compare
from app.services.cache.guard import normalize_for_key, normalize_query

if TYPE_CHECKING:
    from collections.abc import Callable

    from redis.asyncio import Redis as RedisClient

    from app.config.configuration_service import ConfigurationService
    from app.services.redis.connection_provider import IRedisConnectionProvider

logger = logging.getLogger(__name__)

PROMPT_TEMPLATE_VERSION: int = 1
STORAGE_SCHEMA_VERSION: str = "v1"

# Lua script to write completion data to data key, metadata to bucket hash, update LRU score,
# evict excess entries beyond cap together with their data keys, and refresh TTL on keys.
# Does NOT parse JSON inside Lua.
LUA_STORE_SCRIPT = """
local bucket_key = KEYS[1]
local lru_key = KEYS[2]
local data_key = KEYS[3]

local entry_id = ARGV[1]
local metadata_json = ARGV[2]
local completion_json = ARGV[3]
local now_ts = tonumber(ARGV[4])
local ttl = tonumber(ARGV[5])
local cap = tonumber(ARGV[6])
local data_prefix = ARGV[7]

-- 1. Store completion data in data key with TTL
redis.call('SET', data_key, completion_json, 'EX', ttl)

-- 2. Store metadata in lookup hash
redis.call('HSET', bucket_key, entry_id, metadata_json)

-- 3. Update LRU score in sorted set
redis.call('ZADD', lru_key, now_ts, entry_id)

-- 4. Check cap and evict oldest entries if total > cap
local total = redis.call('ZCARD', lru_key)
if total > cap then
    local excess = total - cap
    local evicted = redis.call('ZRANGE', lru_key, 0, excess - 1)
    for _, eid in ipairs(evicted) do
        redis.call('ZREM', lru_key, eid)
        redis.call('HDEL', bucket_key, eid)
        local evicted_data_key = data_prefix .. eid
        redis.call('DEL', evicted_data_key)
    end
end

-- 5. Refresh TTLs on bucket and LRU keys
redis.call('EXPIRE', bucket_key, ttl)
redis.call('EXPIRE', lru_key, ttl)

return 1
"""

INCREMENT_EPOCH_LUA = """
local curr = redis.call('GET', KEYS[1])
if not curr then
    redis.call('SET', KEYS[1], 2)
    return 2
else
    return redis.call('INCR', KEYS[1])
end
"""


@dataclass(frozen=True)
class SemanticCacheConfigHashParams:
    """Inputs contributing to config_hash.

    Must hash template/config INPUTS only, never rendered prompts, and must exclude
    current time, user name/email, endpoints, and credentials.
    """

    embedding_provider: str = ""
    embedding_model: str = ""
    embedding_dimension: int = 1024
    generator_provider: str = ""
    generator_model: str = ""
    prompt_template_version: int = PROMPT_TEMPLATE_VERSION
    storage_schema_version: str = STORAGE_SCHEMA_VERSION
    reasoning_effort: str | None = None
    sampling_temperature: float | None = None
    response_language: str | None = None
    system_prompt_template_hash: str = ""
    project_instructions_hash: str = ""
    enrichment_flags: str = ""
    tools_hash: str = ""


def compute_config_hash(params: SemanticCacheConfigHashParams) -> str:
    """Compute deterministic SHA-256 over template/config inputs only.

    Strictly excludes current time, user name/email, credentials, and endpoints.
    Embedding identifier is provider + model + dimension only.
    """
    embedding_identifier = (
        f"{params.embedding_provider}:{params.embedding_model}:{params.embedding_dimension}"
    )
    sampling_temp_str = (
        "" if params.sampling_temperature is None else str(params.sampling_temperature)
    )
    items: list[tuple[str, str]] = [
        ("embedding_identifier", embedding_identifier),
        ("enrichment_flags", params.enrichment_flags),
        ("generator_model", params.generator_model),
        ("generator_provider", params.generator_provider),
        ("project_instructions_hash", params.project_instructions_hash),
        ("prompt_template_version", str(params.prompt_template_version)),
        ("reasoning_effort", str(params.reasoning_effort or "")),
        ("response_language", str(params.response_language or "")),
        ("sampling_temperature", sampling_temp_str),
        ("storage_schema_version", params.storage_schema_version),
        ("system_prompt_template_hash", params.system_prompt_template_hash),
        ("tools_hash", params.tools_hash),
    ]
    canonical = "\n".join(f"{k}={v}" for k, v in items)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_context_digest(
    records: list[tuple[str, object, object, object, object]]
    | list[dict[str, object]]
    | None,
) -> str:
    """Construct deterministic SHA-256 over sorted retrieved records.

    Formula per record:
        f"{id}|{updatedAt or ''}|{sourceUpdated or ''}|{version or ''}|{md5 or ''}"
    Sorted lexicographically so order of retrieval doesn't matter.
    Record-level (not chunk level): queries retrieving the same records with different
    chunks can share a cache bucket.
    """
    if not records:
        return hashlib.sha256(b"").hexdigest()

    lines: list[str] = []
    for rec in records:
        if isinstance(rec, tuple):
            rec_id, updated_at, source_updated, version, md5 = rec
        else:
            rec_id = str(rec.get("id") or rec.get("_key") or "")
            updated_at = rec.get("updatedAtTimestamp")
            source_updated = rec.get("sourceLastModifiedTimestamp")
            version = rec.get("version")
            md5 = rec.get("md5Checksum")
        line = f"{rec_id}|{updated_at or ''}|{source_updated or ''}|{version or ''}|{md5 or ''}"
        lines.append(line)

    lines.sort()
    content = "\n".join(lines).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def pack_embedding(vec: list[float] | np.ndarray) -> str:
    """Pack vector as float32 bytes, base64-encoded."""
    arr = np.asarray(vec, dtype="<f4")
    return base64.b64encode(arr.tobytes()).decode("ascii")


def unpack_embedding(b64_str: str) -> np.ndarray:
    """Unpack float32 bytes from base64 string."""
    raw = base64.b64decode(b64_str.encode("ascii"))
    return np.frombuffer(raw, dtype="<f4")


def cosine_similarity(u: np.ndarray, v: np.ndarray) -> float:
    """In-memory cosine similarity between two float32 vectors."""
    if u.shape != v.shape or len(u) == 0:
        return 0.0
    norm_u = float(np.linalg.norm(u))
    norm_v = float(np.linalg.norm(v))
    if norm_u == 0.0 or norm_v == 0.0:
        return 0.0
    dot = float(np.dot(u, v))
    sim = dot / (norm_u * norm_v)
    return max(-1.0, min(1.0, sim))


class CircuitBreaker:
    """Circuit breaker for Redis operations.

    5 consecutive failures opens the breaker for 30 seconds.
    After 30s, a single probe request is allowed. If successful, breaker closes.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_window_seconds: float = 30.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        super().__init__()
        self.failure_threshold = failure_threshold
        self.recovery_window_seconds = recovery_window_seconds
        self._clock = clock or time.time
        self._consecutive_failures: int = 0
        self._last_failure_time: float = 0.0
        self._state: str = "closed"

    @property
    def consecutive_failures(self) -> int:
        """Number of consecutive recorded failures."""
        return self._consecutive_failures

    @property
    def state(self) -> str:
        """Current breaker state: 'closed', 'open', or 'half-open'."""
        now = self._clock()
        if self._state == "open" and (
            now - self._last_failure_time >= self.recovery_window_seconds
        ):
            return "half-open"
        return self._state

    def is_open(self) -> bool:
        """Return True if breaker is currently open and shedding traffic."""
        now = self._clock()
        if self._state == "open":
            if now - self._last_failure_time >= self.recovery_window_seconds:
                return False  # Half-open: allow probe
            return True
        return False

    def record_success(self) -> None:
        """Record a successful operation, resetting failure counter to 0."""
        self._consecutive_failures = 0
        self._state = "closed"

    def record_failure(self) -> None:
        """Record a failure and trip breaker to open if failure threshold is reached."""
        now = self._clock()
        self._consecutive_failures += 1
        self._last_failure_time = now
        if self._consecutive_failures >= self.failure_threshold:
            self._state = "open"


@dataclass
class CacheLookupResult:
    """Result returned by SemanticCacheService.lookup()."""

    hit: bool = False
    kind: str = "none"  # "exact", "semantic", "none"
    score: float = 0.0
    entry_id: str | None = None
    completion_data: dict[str, object] | None = None
    reasons: list[str] = field(default_factory=list)
    cache_ref: dict[str, object] | None = None
    author_user_id: str | None = None


@dataclass
class SemanticCacheTelemetryCounters:
    """Telemetry counters tracked in-memory (numbers/hashes only; no content)."""

    lookups: int = 0
    exact_hits: int = 0
    semantic_hits: int = 0
    guard_rejected: int = 0
    below_threshold: int = 0
    bypasses: int = 0
    writes: int = 0
    write_skipped: int = 0
    errors: int = 0
    breaker_open: int = 0


class SemanticCacheService:
    """Multi-tenant Semantic Caching layer for plain-RAG chat."""

    def __init__(
        self,
        redis_provider: IRedisConnectionProvider | None = None,
        config_service: ConfigurationService | None = None,
        logger_instance: logging.Logger | None = None,
        *,
        clock: Callable[[], float] | None = None,
        redis_client: RedisClient | None = None,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        super().__init__()
        self._redis_provider = redis_provider
        self._config_service = config_service
        self._logger = logger_instance or logger
        self._clock = clock or time.time
        self._redis_client = redis_client
        self._circuit_breaker = circuit_breaker or CircuitBreaker(clock=self._clock)
        self.metrics = SemanticCacheTelemetryCounters()

    @property
    def circuit_breaker(self) -> CircuitBreaker:
        """Access the underlying circuit breaker instance."""
        return self._circuit_breaker

    def _get_redis(self) -> RedisClient | None:
        """Obtain active Redis client (fail-open if none available)."""
        if self._redis_client is not None:
            return self._redis_client
        if self._redis_provider is not None:
            try:
                return cast("RedisClient", self._redis_provider.get_client())
            except Exception as exc:
                self._logger.warning(
                    "SemanticCacheService: Failed to obtain client from redis_provider: %s",
                    exc,
                )
                return None
        return None

    async def get_epoch(self, org_id: str) -> int:
        """Read active epoch for organization (defaults to 1)."""
        redis = self._get_redis()
        if redis is None or self._circuit_breaker.is_open():
            return 1
        epoch_key = f"pipeshub:semcache:epoch:{{{org_id}}}"
        try:
            val = await redis.get(epoch_key)
            if val is not None:
                if isinstance(val, bytes):
                    val = val.decode("utf-8")
                return int(val)
            return 1
        except Exception as exc:
            self._logger.warning("Failed to read epoch for org: %s", type(exc).__name__)
            self._circuit_breaker.record_failure()
            self.metrics.errors += 1
            return 1

    async def increment_epoch(self, org_id: str) -> int:
        """Atomically increment organization epoch, immediately invalidating prior buckets."""
        redis = self._get_redis()
        if redis is None:
            return 1
        epoch_key = f"pipeshub:semcache:epoch:{{{org_id}}}"
        try:
            val = await cast(Any, redis).eval(INCREMENT_EPOCH_LUA, 1, epoch_key)
            self._circuit_breaker.record_success()
            return int(val)
        except Exception as exc:
            self._logger.warning("Failed to increment epoch for org: %s", type(exc).__name__)
            self._circuit_breaker.record_failure()
            self.metrics.errors += 1
            return 1

    def _build_keys(
        self,
        org_id: str,
        epoch: int,
        user_id: str,
        config_hash: str,
        context_digest: str,
        *,
        share_across_org: bool,
    ) -> tuple[str, str, str, str]:
        """Construct cluster-safe Redis keys using {org} hash tag."""
        user_or_shared = "shared" if share_across_org else user_id
        bucket_key = (
            f"pipeshub:semcache:v1:{{{org_id}}}:{epoch}:{user_or_shared}:{config_hash}:{context_digest}"
        )
        lru_key = f"{bucket_key}:lru"
        data_prefix = f"pipeshub:semcache:v1:{{{org_id}}}:data:"
        return bucket_key, lru_key, data_prefix, user_or_shared

    async def lookup(
        self,
        org_id: str,
        user_id: str,
        query: str,
        query_embedding: list[float] | None,
        context_digest: str,
        config_hash: str,
        *,
        config: SemanticCacheConfig | None = None,
    ) -> CacheLookupResult:
        """Look up cached response for query under authorized context."""
        self.metrics.lookups += 1

        # 1. Resolve organization config
        if config is None:
            config = await get_semantic_cache_config(
                self._config_service, org_id
            )
        if config.mode == "off":
            self.metrics.bypasses += 1
            return CacheLookupResult(hit=False, kind="none", reasons=["mode_off"])

        # 2. Check circuit breaker
        if self._circuit_breaker.is_open():
            self.metrics.breaker_open += 1
            return CacheLookupResult(
                hit=False, kind="none", reasons=["circuit_breaker_open"]
            )

        redis = self._get_redis()
        if redis is None:
            self.metrics.errors += 1
            return CacheLookupResult(
                hit=False, kind="none", reasons=["redis_unavailable"]
            )

        epoch = await self.get_epoch(org_id)
        bucket_key, lru_key, data_prefix, user_or_shared = self._build_keys(
            org_id=org_id,
            epoch=epoch,
            user_id=user_id,
            config_hash=config_hash,
            context_digest=context_digest,
            share_across_org=config.share_across_org,
        )

        try:
            # 3. Read lookup hash
            raw_entries_res = await cast(Any, redis).hgetall(bucket_key)
            raw_entries: dict[str | bytes, str | bytes] = cast(
                "dict[str | bytes, str | bytes]", raw_entries_res
            )
            self._circuit_breaker.record_success()
        except Exception as exc:
            self._logger.warning("Redis lookup error on bucket: %s", type(exc).__name__)
            self._circuit_breaker.record_failure()
            self.metrics.errors += 1
            return CacheLookupResult(
                hit=False, kind="none", reasons=["redis_read_error"]
            )

        if not raw_entries:
            return CacheLookupResult(hit=False, kind="none", reasons=["bucket_empty"])

        now_ts = self._clock()
        norm_query = normalize_for_key(query)
        incoming_words = norm_query.split()

        # Parse entries and filter out expired (read-time TTL check)
        active_entries: list[dict[str, object]] = []
        expired_ids: list[str] = []

        for k, v in raw_entries.items():
            field_name = k.decode("utf-8") if isinstance(k, bytes) else str(k)
            raw_str = v.decode("utf-8") if isinstance(v, bytes) else str(v)
            try:
                entry_data_raw = json.loads(raw_str)
                if not isinstance(entry_data_raw, dict):
                    _ = asyncio.create_task(
                        self._cleanup_orphan_entry(
                            redis, bucket_key, lru_key, field_name, f"{data_prefix}{field_name}"
                        )
                    )
                    continue
                entry_data = cast("dict[str, object]", entry_data_raw)
                ver = entry_data.get("schema_version")
                if ver is not None and ver != STORAGE_SCHEMA_VERSION:
                    self._logger.warning(
                        "Incompatible metadata schema version %r; evicting entry %s",
                        ver,
                        field_name,
                    )
                    _ = asyncio.create_task(
                        self._cleanup_orphan_entry(
                            redis, bucket_key, lru_key, field_name, f"{data_prefix}{field_name}"
                        )
                    )
                    continue
                created_at = float(str(entry_data.get("created_at", 0.0)))
                # READ-TIME TTL: an entry older than ttl_seconds is dropped even if bucket key is alive
                if now_ts - created_at > config.ttl_seconds:
                    expired_ids.append(field_name)
                    continue
                active_entries.append(entry_data)
            except Exception:
                _ = asyncio.create_task(
                    self._cleanup_orphan_entry(
                        redis, bucket_key, lru_key, field_name, f"{data_prefix}{field_name}"
                    )
                )
                continue

        # Best-effort async cleanup of expired entries
        if expired_ids:
            _ = asyncio.create_task(
                self._cleanup_expired_entries(
                    redis, bucket_key, lru_key, data_prefix, expired_ids
                )
            )

        if not active_entries:
            return CacheLookupResult(
                hit=False, kind="none", reasons=["all_entries_expired"]
            )

        # 4. Check for EXACT normalized string match
        for entry in active_entries:
            entry_norm = str(entry.get("norm_query", ""))
            author = str(entry.get("author_user_id", ""))
            entry_id = str(entry.get("entry_id", ""))

            # Exact match condition
            # User-scoping rule (Section 2 Decision 4):
            # Cross-user match allowed on exact match if share_across_org is True
            if entry_norm == norm_query and (author == user_id or config.share_across_org):
                data_key = f"{data_prefix}{entry_id}"
                completion_data = await self._fetch_data_key(
                    redis, bucket_key, lru_key, data_key, entry_id
                )
                if completion_data is not None:
                    self.metrics.exact_hits += 1
                    _ = asyncio.create_task(
                        self._touch_lru(redis, lru_key, entry_id, now_ts)
                    )
                    cache_ref: dict[str, object] = {
                        "orgId": org_id,
                        "epoch": epoch,
                        "userOrShared": user_or_shared,
                        "configHash": config_hash,
                        "contextDigest": context_digest,
                        "entryId": entry_id,
                    }
                    return CacheLookupResult(
                        hit=True,
                        kind="exact",
                        score=1.0,
                        entry_id=entry_id,
                        author_user_id=author,
                        completion_data=completion_data,
                        reasons=[],
                        cache_ref=cache_ref,
                    )

        # 5. Semantic similarity search
        # Rule: Queries under 5 words require an EXACT match
        if len(incoming_words) < 5:
            return CacheLookupResult(
                hit=False,
                kind="none",
                reasons=["short_query_requires_exact_match"],
            )

        if query_embedding is None:
            return CacheLookupResult(
                hit=False, kind="none", reasons=["no_query_embedding"]
            )

        u = np.asarray(query_embedding, dtype="<f4")

        # Evaluate candidate semantic hits
        for entry in active_entries:
            author = str(entry.get("author_user_id", ""))
            entry_id = str(entry.get("entry_id", ""))

            # Section 2 Decision 4: Semantic hits only for the same author!
            # (Cross-user allowed only on exact match)
            if author != user_id:
                continue

            raw_b64 = entry.get("embedding_b64")
            if not raw_b64 or not isinstance(raw_b64, str):
                continue

            try:
                v = unpack_embedding(raw_b64)
            except Exception:
                continue

            # Dimension mismatch is a miss, never an exception
            if u.shape != v.shape:
                continue

            score = cosine_similarity(u, v)
            if score < config.similarity_threshold:
                self.metrics.below_threshold += 1
                continue

            # Guard comparison (entity, negation, direction, numerical, and overlap checks)
            cached_guard_query = entry.get("guard_query")
            if not cached_guard_query or not isinstance(cached_guard_query, str):
                continue
            passed, _guard_reasons = guard_compare(
                normalize_query(query), cached_guard_query, jaccard_threshold=config.jaccard_threshold
            )
            if not passed:
                self.metrics.guard_rejected += 1
                continue

            # Passed cosine similarity & guard checks!
            data_key = f"{data_prefix}{entry_id}"
            completion_data = await self._fetch_data_key(
                redis, bucket_key, lru_key, data_key, entry_id
            )
            if completion_data is not None:
                self.metrics.semantic_hits += 1
                _ = asyncio.create_task(self._touch_lru(redis, lru_key, entry_id, now_ts))
                cache_ref = {
                    "orgId": org_id,
                    "epoch": epoch,
                    "userOrShared": user_or_shared,
                    "configHash": config_hash,
                    "contextDigest": context_digest,
                    "entryId": entry_id,
                }
                return CacheLookupResult(
                    hit=True,
                    kind="semantic",
                    score=score,
                    entry_id=entry_id,
                    author_user_id=author,
                    completion_data=completion_data,
                    reasons=[],
                    cache_ref=cache_ref,
                )

        return CacheLookupResult(
            hit=False, kind="none", reasons=["no_matching_candidate"]
        )

    async def _fetch_data_key(
        self,
        redis: RedisClient,
        bucket_key: str,
        lru_key: str,
        data_key: str,
        entry_id: str,
    ) -> dict[str, object] | None:
        """Fetch completion data from data key; validate schema, required keys and types;
        if missing, corrupt, or invalid, evict entry and return None."""
        try:
            val = await redis.get(data_key)
            if val is None:
                # Missing data key => miss plus cleanup
                _ = asyncio.create_task(
                    self._cleanup_orphan_entry(redis, bucket_key, lru_key, entry_id, data_key)
                )
                return None
            val_str = val.decode("utf-8") if isinstance(val, bytes) else str(val)
            parsed = json.loads(val_str)
            if not isinstance(parsed, dict):
                self._logger.warning("Cache data entry at %s is not a dict; evicting", data_key)
                _ = asyncio.create_task(
                    self._cleanup_orphan_entry(redis, bucket_key, lru_key, entry_id, data_key)
                )
                return None

            # 1. Schema version validation
            schema_ver = parsed.get("schema_version")
            if schema_ver is not None and schema_ver != STORAGE_SCHEMA_VERSION:
                self._logger.warning(
                    "Cache entry has incompatible schema version %r (expected %r); evicting",
                    schema_ver,
                    STORAGE_SCHEMA_VERSION,
                )
                _ = asyncio.create_task(
                    self._cleanup_orphan_entry(redis, bucket_key, lru_key, entry_id, data_key)
                )
                return None

            # 2. Required keys and types: "answer" must be present and a string
            if "answer" not in parsed or not isinstance(parsed["answer"], str):
                self._logger.warning(
                    "Cache entry missing or invalid 'answer' field; evicting entry %s",
                    entry_id,
                )
                _ = asyncio.create_task(
                    self._cleanup_orphan_entry(redis, bucket_key, lru_key, entry_id, data_key)
                )
                return None

            # 3. Optional keys type checks if present
            citations = parsed.get("citations")
            if citations is not None and not isinstance(citations, list):
                self._logger.warning(
                    "Cache entry citations is not a list; evicting entry %s",
                    entry_id,
                )
                _ = asyncio.create_task(
                    self._cleanup_orphan_entry(redis, bucket_key, lru_key, entry_id, data_key)
                )
                return None
            # Pop storage schema version envelope key so returned completion_data matches caller's payload
            _ = parsed.pop("schema_version", None)
            return cast("dict[str, object]", parsed)
        except json.JSONDecodeError as exc:
            self._logger.warning(
                "Corrupt JSON in data key %s: %s; evicting entry %s",
                data_key,
                type(exc).__name__,
                entry_id,
            )
            _ = asyncio.create_task(
                self._cleanup_orphan_entry(redis, bucket_key, lru_key, entry_id, data_key)
            )
            return None
        except Exception as exc:
            self._logger.warning(
                "Error reading data key %s: %s; evicting entry %s",
                data_key,
                type(exc).__name__,
                entry_id,
            )
            _ = asyncio.create_task(
                self._cleanup_orphan_entry(redis, bucket_key, lru_key, entry_id, data_key)
            )
            return None

    async def _touch_lru(
        self, redis: RedisClient, lru_key: str, entry_id: str, now_ts: float
    ) -> None:
        """Best-effort background ZADD updating LRU timestamp on hit."""
        with contextlib.suppress(Exception):
            await redis.zadd(lru_key, {entry_id: now_ts})

    async def _cleanup_orphan_entry(
        self,
        redis: RedisClient,
        bucket_key: str,
        lru_key: str,
        entry_id: str,
        data_key: str | None = None,
    ) -> None:
        """Remove orphaned or invalid entry from lookup hash, LRU, and data key."""
        with contextlib.suppress(Exception):
            _ = await cast(Any, redis).hdel(bucket_key, entry_id)
            _ = await cast(Any, redis).zrem(lru_key, entry_id)
            if data_key:
                _ = await cast(Any, redis).delete(data_key)

    async def _cleanup_expired_entries(
        self,
        redis: RedisClient,
        bucket_key: str,
        lru_key: str,
        data_prefix: str,
        expired_ids: list[str],
    ) -> None:
        """Remove expired entries and their data keys."""
        for eid in expired_ids:
            with contextlib.suppress(Exception):
                _ = await cast(Any, redis).hdel(bucket_key, eid)
                _ = await cast(Any, redis).zrem(lru_key, eid)
                _ = await cast(Any, redis).delete(f"{data_prefix}{eid}")

    async def store(
        self,
        org_id: str,
        user_id: str,
        query: str,
        query_embedding: list[float] | None,
        completion_data: dict[str, object],
        context_digest: str,
        config_hash: str,
        *,
        config: SemanticCacheConfig | None = None,
    ) -> bool:
        """Store completion in semantic cache via atomic Lua script."""
        if query_embedding is None:
            self.metrics.write_skipped += 1
            return False

        if config is None:
            config = await get_semantic_cache_config(
                self._config_service, org_id
            )
        if config.mode == "off":
            self.metrics.write_skipped += 1
            return False

        if self._circuit_breaker.is_open():
            self.metrics.breaker_open += 1
            self.metrics.write_skipped += 1
            return False

        redis = self._get_redis()
        if redis is None:
            self.metrics.errors += 1
            return False

        norm_query = normalize_for_key(query)

        epoch = await self.get_epoch(org_id)
        bucket_key, lru_key, data_prefix, _ = self._build_keys(
            org_id=org_id,
            epoch=epoch,
            user_id=user_id,
            config_hash=config_hash,
            context_digest=context_digest,
            share_across_org=config.share_across_org,
        )
        entry_id = hashlib.sha256(f"{bucket_key}:{norm_query}".encode("utf-8")).hexdigest()[:16]
        data_key = f"{data_prefix}{entry_id}"

        now_ts = self._clock()
        packed_emb = pack_embedding(query_embedding)

        payload = dict(completion_data)
        _ = payload.setdefault("schema_version", STORAGE_SCHEMA_VERSION)

        metadata: dict[str, object] = {
            "schema_version": STORAGE_SCHEMA_VERSION,
            "entry_id": entry_id,
            "norm_query": norm_query,
            "guard_query": normalize_query(query),
            "query_hash": hashlib.sha256(query.encode("utf-8")).hexdigest(),
            "author_user_id": user_id,
            "embedding_b64": packed_emb,
            "embedding_dim": len(query_embedding),
            "created_at": now_ts,
            "data_key": data_key,
        }

        metadata_json = json.dumps(metadata)
        completion_json = json.dumps(payload)

        try:
            _ = await cast(Any, redis).eval(  # pyright: ignore[reportUnknownMemberType]
                LUA_STORE_SCRIPT,
                3,
                bucket_key,
                lru_key,
                data_key,
                entry_id,
                metadata_json,
                completion_json,
                str(now_ts),
                str(config.ttl_seconds),
                str(config.max_entries_per_context),
                data_prefix,
            )
            self._circuit_breaker.record_success()
            self.metrics.writes += 1
            return True
        except Exception as exc:
            self._logger.warning("Failed to store entry in semantic cache: %s", type(exc).__name__)
            self._circuit_breaker.record_failure()
            self.metrics.errors += 1
            return False

    async def evict_entry(
        self,
        org_id: str,
        epoch: int,
        user_or_shared: str,
        config_hash: str,
        context_digest: str,
        entry_id: str,
    ) -> bool:
        """Evict a specific entry and delete its data key."""
        redis = self._get_redis()
        if redis is None:
            return False

        bucket_key = (
            f"pipeshub:semcache:v1:{{{org_id}}}:{epoch}:{user_or_shared}:{config_hash}:{context_digest}"
        )
        lru_key = f"{bucket_key}:lru"
        data_key = f"pipeshub:semcache:v1:{{{org_id}}}:data:{entry_id}"

        try:
            _ = await cast(Any, redis).hdel(bucket_key, entry_id)
            _ = await cast(Any, redis).zrem(lru_key, entry_id)
            _ = await cast(Any, redis).delete(data_key)
            self._circuit_breaker.record_success()
            return True
        except Exception as exc:
            self._logger.warning("Failed to evict entry: %s", type(exc).__name__)
            self._circuit_breaker.record_failure()
            self.metrics.errors += 1
            return False

    @classmethod
    async def create(
        cls,
        logger_instance: logging.Logger,
        config_service: ConfigurationService,
        *,
        clock: Callable[[], float] | None = None,
    ) -> "SemanticCacheService":
        """Async factory wiring the standalone Redis provider for the service."""
        from app.services.redis.config import RedisConnectionConfig
        from app.services.redis.connection_provider_factory import get_redis_provider

        provider = None
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
        except Exception as exc:
            logger_instance.warning(
                "SemanticCacheService: Failed to initialize Redis provider; failing open: %s",
                exc,
            )

        return cls(
            redis_provider=provider,
            config_service=config_service,
            logger_instance=logger_instance,
            clock=clock,
        )
