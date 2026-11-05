"""Redis-backed cache for ranked vector-search candidate references."""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
from typing import TYPE_CHECKING

from app.services.cache.interface import (
    IRetrievalCandidatesCache,
    NoopRetrievalCandidatesCache,
    RetrievalCandidate,
    SemanticQueryEntry,
    SemanticQueryMatch,
    SemanticQueryScope,
    validate_semantic_embedding,
)
from app.services.redis.config import ClientOptions, RedisConnectionConfig
from app.services.redis.connection_provider_factory import get_redis_provider
from app.services.redis.loop_clients import LoopBoundClients

if TYPE_CHECKING:
    import logging

    from app.config.configuration_service import ConfigurationService
    from app.services.redis.connection_provider import RedisClient

__all__ = ["RetrievalCandidatesCache"]

_DISABLED_VALUES = {"0", "off", "false", "no"}

_STORE_SEMANTIC_ENTRY_LUA = """
redis.call('SET', KEYS[1], ARGV[1], 'EX', tonumber(ARGV[5]))
redis.call('ZADD', KEYS[2], ARGV[2], ARGV[3])
redis.call('ZREMRANGEBYRANK', KEYS[2], tonumber(ARGV[4]), -1)
redis.call('EXPIRE', KEYS[2], tonumber(ARGV[5]))
return 1
"""


def _cache_enabled_from_env() -> bool:
    raw = os.getenv(RetrievalCandidatesCache.ENV_ENABLED)
    return raw is None or raw.strip().lower() not in _DISABLED_VALUES


def _ttl_from_env(default: int) -> int:
    raw = os.getenv(RetrievalCandidatesCache.ENV_TTL)
    if raw is None:
        return default
    try:
        return max(int(raw), 1)
    except ValueError:
        return default


class RetrievalCandidatesCache(IRetrievalCandidatesCache):
    """Best-effort shared candidate cache; Redis failures are cache misses."""

    KEY_PREFIX = "pipeshub:retrieval_candidates:v1"
    ENV_ENABLED = "PIPESHUB_RETRIEVAL_CANDIDATES_CACHE"
    ENV_TTL = "PIPESHUB_RETRIEVAL_CANDIDATES_CACHE_TTL"
    DEFAULT_TTL_SECONDS = 120
    OP_TIMEOUT_SECONDS = 2.0
    DOWN_BACKOFF_SECONDS = 30.0
    MAX_SEMANTIC_ENTRIES = 100
    MAX_CANDIDATES = 300
    MAX_PAYLOAD_BYTES = 512 * 1024

    def __init__(
        self,
        logger: logging.Logger,
        redis_clients: LoopBoundClients["RedisClient"] | None,
        ttl_seconds: int,
        enabled: bool,
        key_namespace: str = "",
    ) -> None:
        self.logger = logger
        self._redis_clients = redis_clients
        self._ttl = ttl_seconds
        self._enabled = enabled and redis_clients is not None
        self._down_until = 0.0
        self._key_namespace = key_namespace

    @classmethod
    async def create(
        cls, logger: logging.Logger, config_service: "ConfigurationService"
    ) -> IRetrievalCandidatesCache:
        ttl = _ttl_from_env(cls.DEFAULT_TTL_SECONDS)
        if not _cache_enabled_from_env():
            logger.info("Retrieval-candidates cache disabled via %s", cls.ENV_ENABLED)
            return NoopRetrievalCandidatesCache()

        clients: LoopBoundClients[RedisClient] | None = None
        try:
            redis_config = await config_service.get_redis_config()
            provider = get_redis_provider(
                RedisConnectionConfig.from_redis_config(redis_config)
            )
            options = ClientOptions(
                decode_responses=True,
                socket_timeout_seconds=cls.OP_TIMEOUT_SECONDS,
                socket_connect_timeout_seconds=cls.OP_TIMEOUT_SECONDS,
            )
            clients = LoopBoundClients(lambda: provider.create_client(options))
            await clients.get().ping()
        except Exception as exc:
            logger.warning(
                "Retrieval-candidates cache unavailable (%s); continuing without it",
                str(exc),
            )
            if clients is not None:
                await clients.aclose()
            return NoopRetrievalCandidatesCache()

        logger.info("Retrieval-candidates cache ready (ttl=%ss)", ttl)
        return cls(
            logger, clients, ttl, enabled=True, key_namespace=provider.key_namespace
        )

    @property
    def enabled(self) -> bool:
        return self._enabled and time.monotonic() >= self._down_until

    @property
    def ttl_seconds(self) -> int:
        return self._ttl

    async def close(self) -> None:
        clients, self._redis_clients = self._redis_clients, None
        self._enabled = False
        if clients is not None:
            await clients.aclose()

    def _redis_key(self, key: str) -> str:
        if not key:
            raise ValueError("Retrieval-candidate cache key must not be empty")
        namespace = f"{self._key_namespace}:" if self._key_namespace else ""
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return f"{namespace}{self.KEY_PREFIX}:{digest}"

    def _semantic_scope_digest(self, scope: SemanticQueryScope) -> str:
        scope_json = json.dumps(scope.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(scope_json.encode("utf-8")).hexdigest()

    def _semantic_index_key(self, scope: SemanticQueryScope) -> str:
        namespace = f"{self._key_namespace}:" if self._key_namespace else ""
        scope_digest = self._semantic_scope_digest(scope)
        return f"{namespace}{self.KEY_PREFIX}:semantic:{{{scope_digest}}}:index"

    def _semantic_entry_key(self, scope: SemanticQueryScope, entry_id: str) -> str:
        namespace = f"{self._key_namespace}:" if self._key_namespace else ""
        scope_digest = self._semantic_scope_digest(scope)
        return (
            f"{namespace}{self.KEY_PREFIX}:semantic:{{{scope_digest}}}:entry:{entry_id}"
        )

    async def get_candidates(self, key: str) -> list[RetrievalCandidate] | None:
        redis_key = self._redis_key(key)
        if not self.enabled:
            return None

        try:
            clients = self._redis_clients
            if clients is None:
                return None
            raw = await clients.get().get(redis_key)
        except Exception as exc:
            self._mark_down("read", exc)
            return None

        if raw is None:
            return None
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict) or payload.get("v") != 1:
                raise ValueError("Unsupported candidate-cache payload")
            values = payload.get("candidates")
            if not isinstance(values, list) or len(values) > self.MAX_CANDIDATES:
                raise ValueError("Invalid candidate-cache list")
            return [RetrievalCandidate.from_dict(value) for value in values]
        except (TypeError, ValueError) as exc:
            self.logger.warning(
                "Ignoring invalid retrieval-candidates cache entry: %s", exc
            )
            return None

    async def set_candidates(
        self, key: str, candidates: list[RetrievalCandidate]
    ) -> None:
        redis_key = self._redis_key(key)
        if not self.enabled:
            return

        if len(candidates) > self.MAX_CANDIDATES:
            self.logger.warning(
                "Truncating retrieval-candidates cache entry from %s to %s candidates",
                len(candidates),
                self.MAX_CANDIDATES,
            )
        bounded_candidates = candidates[: self.MAX_CANDIDATES]
        payload = json.dumps(
            {
                "v": 1,
                "candidates": [candidate.to_dict() for candidate in bounded_candidates],
            },
            separators=(",", ":"),
            allow_nan=False,
        )
        if len(payload.encode("utf-8")) > self.MAX_PAYLOAD_BYTES:
            self.logger.warning(
                "Skipping retrieval-candidates cache write: payload exceeds %s bytes",
                self.MAX_PAYLOAD_BYTES,
            )
            return

        try:
            clients = self._redis_clients
            if clients is None:
                return
            await clients.get().set(redis_key, payload, ex=self._ttl)
        except Exception as exc:
            self._mark_down("write", exc)

    async def set_semantic_entry(self, entry: SemanticQueryEntry) -> None:
        if not self.enabled:
            return
        payload = json.dumps(entry.to_dict(), separators=(",", ":"), allow_nan=False)
        if len(payload.encode("utf-8")) > self.MAX_PAYLOAD_BYTES:
            self.logger.warning(
                "Skipping semantic query cache write: payload exceeds %s bytes",
                self.MAX_PAYLOAD_BYTES,
            )
            return

        index_key = self._semantic_index_key(entry.scope)
        entry_key = self._semantic_entry_key(entry.scope, entry.entry_id)
        try:
            clients = self._redis_clients
            if clients is None:
                return
            await clients.get().eval(
                _STORE_SEMANTIC_ENTRY_LUA,
                2,
                entry_key,
                index_key,
                payload,
                -entry.created_at,
                entry.entry_id,
                self.MAX_SEMANTIC_ENTRIES,
                self._ttl,
            )
        except Exception as exc:
            self._mark_down("semantic write", exc)

    async def get_similar_entries(
        self,
        scope: SemanticQueryScope,
        embedding: list[float],
        limit: int = 1,
    ) -> list[SemanticQueryMatch]:
        if limit < 1:
            raise ValueError("Semantic query match limit must be positive")
        query_embedding = validate_semantic_embedding(embedding)
        if not self.enabled:
            return []

        index_key = self._semantic_index_key(scope)
        try:
            clients = self._redis_clients
            if clients is None:
                return []
            client = clients.get()
            entry_ids = await client.zrange(index_key, 0, -1)
            if not entry_ids:
                return []
            async with client.pipeline(transaction=False) as pipe:
                for entry_id in entry_ids:
                    pipe.get(self._semantic_entry_key(scope, entry_id))
                raw_entries = await pipe.execute()
        except Exception as exc:
            self._mark_down("semantic read", exc)
            return []

        matches: list[SemanticQueryMatch] = []
        stale_entry_ids: list[str] = []
        query_norm = math.hypot(*query_embedding)
        for entry_id, raw in zip(entry_ids, raw_entries, strict=True):
            if raw is None:
                stale_entry_ids.append(entry_id)
                continue
            try:
                entry = SemanticQueryEntry.from_dict(json.loads(raw))
                if entry.scope != scope:
                    raise ValueError("Semantic query entry scope mismatch")
            except (TypeError, ValueError) as exc:
                stale_entry_ids.append(entry_id)
                self.logger.warning(
                    "Ignoring invalid semantic query cache entry: %s", exc
                )
                continue

            if len(entry.embedding) != len(query_embedding):
                continue
            entry_norm = math.hypot(*entry.embedding)
            similarity = math.fsum(
                (left / query_norm) * (right / entry_norm)
                for left, right in zip(query_embedding, entry.embedding, strict=True)
            )
            matches.append(SemanticQueryMatch(entry=entry, similarity=similarity))

        if stale_entry_ids:
            try:
                await clients.get().zrem(index_key, *stale_entry_ids)
            except Exception as exc:
                self._mark_down("semantic stale-entry cleanup", exc)

        matches.sort(
            key=lambda match: (match.similarity, match.entry.created_at), reverse=True
        )
        return matches[: min(limit, self.MAX_SEMANTIC_ENTRIES)]

    def _mark_down(self, operation: str, error: Exception) -> None:
        first_failure = time.monotonic() >= self._down_until
        self._down_until = time.monotonic() + self.DOWN_BACKOFF_SECONDS
        if first_failure:
            self.logger.warning(
                "Retrieval-candidates cache %s failed (%s); bypassing cache for %ss",
                operation,
                str(error),
                self.DOWN_BACKOFF_SECONDS,
            )
