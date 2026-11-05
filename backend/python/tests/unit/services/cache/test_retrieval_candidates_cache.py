from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from app.services.cache.interface import (
    NoopRetrievalCandidatesCache,
    RetrievalCandidate,
    SemanticQueryEntry,
    SemanticQueryScope,
)
from app.services.cache.retrieval_candidates_cache import RetrievalCandidatesCache
from app.services.redis.loop_clients import LoopBoundClients


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expires: dict[str, int] = {}
        self.sorted_sets: dict[str, dict[str, float]] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(
        self,
        key: str,
        value: str,
        ex: int | None = None,  # noqa: ARG002
    ) -> bool:
        self.values[key] = value
        if ex is not None:
            self.expires[key] = ex
        return True

    async def ping(self) -> bool:
        return True

    def pipeline(self, *, transaction: bool) -> "FakePipeline":
        _ = transaction
        return FakePipeline(self)

    async def zadd(self, key: str, values: dict[str, float]) -> int:
        sorted_set = self.sorted_sets.setdefault(key, {})
        added = sum(member not in sorted_set for member in values)
        sorted_set.update(values)
        return added

    async def zrange(self, key: str, start: int, end: int) -> list[str]:
        values = sorted(
            self.sorted_sets.get(key, {}).items(), key=lambda item: (item[1], item[0])
        )
        if start < 0:
            start += len(values)
        if end < 0:
            end += len(values)
        return [member for member, _ in values[max(start, 0) : end + 1]]

    async def zremrangebyrank(self, key: str, start: int, end: int) -> int:
        values = self.sorted_sets.get(key, {})
        ordered_members = [
            member
            for member, _ in sorted(values.items(), key=lambda item: (item[1], item[0]))
        ]
        if start < 0:
            start += len(ordered_members)
        if end < 0:
            end += len(ordered_members)
        removed = ordered_members[max(start, 0) : end + 1]
        for member in removed:
            values.pop(member, None)
        return len(removed)

    async def zrem(self, key: str, *members: str) -> int:
        values = self.sorted_sets.get(key, {})
        return sum(values.pop(member, None) is not None for member in members)

    async def eval(self, script: str, numkeys: int, *args: str | float | int) -> int:
        _ = script
        assert numkeys == 2
        entry_key, index_key, payload, score, entry_id, limit, ttl = args
        await self.set(str(entry_key), str(payload), ex=int(ttl))
        await self.zadd(str(index_key), {str(entry_id): float(score)})
        await self.zremrangebyrank(str(index_key), int(limit), -1)
        await self.expire(str(index_key), int(ttl))
        return 1

    async def expire(self, key: str, seconds: int) -> bool:
        self.expires[key] = seconds
        return True

    async def aclose(self) -> None:
        return None


class FakePipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self.redis = redis
        self.commands: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    async def __aenter__(self) -> "FakePipeline":
        return self

    async def __aexit__(self, *_args: object) -> None:
        _ = _args

    def set(self, key: str, value: str, ex: int) -> "FakePipeline":
        self.commands.append(("set", (key, value), {"ex": ex}))
        return self

    def get(self, key: str) -> "FakePipeline":
        self.commands.append(("get", (key,), {}))
        return self

    def zadd(self, key: str, values: dict[str, float]) -> "FakePipeline":
        self.commands.append(("zadd", (key, values), {}))
        return self

    def zremrangebyrank(self, key: str, start: int, end: int) -> "FakePipeline":
        self.commands.append(("zremrangebyrank", (key, start, end), {}))
        return self

    def expire(self, key: str, seconds: int) -> "FakePipeline":
        self.commands.append(("expire", (key, seconds), {}))
        return self

    async def execute(self) -> list[object]:
        results = []
        for method_name, args, kwargs in self.commands:
            method = getattr(self.redis, method_name)
            results.append(await method(*args, **kwargs))
        return results


class BrokenRedis(FakeRedis):
    async def get(self, key: str) -> str | None:  # noqa: ARG002
        _ = key
        raise ConnectionError("Redis unavailable")

    async def set(
        self,
        key: str,  # noqa: ARG002
        value: str,  # noqa: ARG002
        ex: int | None = None,  # noqa: ARG002
    ) -> bool:
        _ = (key, value, ex)
        raise ConnectionError("Redis unavailable")


def _cache(redis: FakeRedis | None = None, ttl: int = 120) -> RetrievalCandidatesCache:
    client = redis or FakeRedis()
    clients = LoopBoundClients(lambda: client)
    return RetrievalCandidatesCache(
        MagicMock(),
        clients,
        ttl_seconds=ttl,
        enabled=True,
        key_namespace="deployment-a",
    )


def _candidate(
    point_id: str = "point-1", virtual_record_id: str = "vr-1"
) -> RetrievalCandidate:
    return RetrievalCandidate(
        collection_name="records",
        point_id=point_id,
        virtual_record_id=virtual_record_id,
        score=0.93,
    )


def _scope(
    org_id: str = "org-1", embedding_config_hash: str = "model-v1"
) -> SemanticQueryScope:
    return SemanticQueryScope(
        org_id=org_id,
        collection_name="records",
        embedding_config_hash=embedding_config_hash,
        vector_provider="qdrant",
    )


def _semantic_entry(
    query: str = "leave policy",
    embedding: tuple[float, ...] = (1.0, 0.0),
    *,
    scope: SemanticQueryScope | None = None,
    created_at: float = 100.0,
) -> SemanticQueryEntry:
    return SemanticQueryEntry(
        scope=scope or _scope(),
        normalized_query=query,
        embedding=embedding,
        candidate_cache_key=f"exact:{query}",
        created_at=created_at,
    )


class TestRetrievalCandidatesCache:
    async def test_round_trips_candidates_with_expiry(self) -> None:
        redis = FakeRedis()
        cache = _cache(redis, ttl=45)

        await cache.set_candidates("org=org-1&query=policy", [_candidate()])

        assert await cache.get_candidates("org=org-1&query=policy") == [_candidate()]
        redis_key = next(iter(redis.values))
        assert redis.expires[redis_key] == 45

    async def test_cache_key_is_namespaced_and_does_not_expose_input(self) -> None:
        cache = _cache()

        key = cache._redis_key("org=org-1&query=private question")

        assert key.startswith("deployment-a:pipeshub:retrieval_candidates:v1:")
        assert "private question" not in key
        assert cache._redis_key("org=org-1&query=private question") == key
        assert cache._redis_key("org=org-2&query=private question") != key

    async def test_missing_entry_is_a_cache_miss(self) -> None:
        assert await _cache().get_candidates("missing") is None

    async def test_invalid_cached_payload_is_ignored(self) -> None:
        redis = FakeRedis()
        cache = _cache(redis)
        redis.values[cache._redis_key("search")] = json.dumps(
            {"v": 1, "candidates": [{"point_id": "missing-fields"}]}
        )

        assert await cache.get_candidates("search") is None

    async def test_write_bounds_number_of_candidates(self) -> None:
        redis = FakeRedis()
        cache = _cache(redis)
        candidates = [
            _candidate(point_id=f"point-{index}", virtual_record_id=f"vr-{index}")
            for index in range(cache.MAX_CANDIDATES + 1)
        ]

        await cache.set_candidates("search", candidates)

        cached = await cache.get_candidates("search")
        assert cached is not None
        assert len(cached) == cache.MAX_CANDIDATES
        assert cached[0].point_id == "point-0"

    async def test_redis_failure_bypasses_cache_without_raising(self) -> None:
        cache = _cache(BrokenRedis())

        assert await cache.get_candidates("search") is None
        await cache.set_candidates("search", [_candidate()])
        assert cache.enabled is False

    async def test_empty_key_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            await _cache().get_candidates("")

    async def test_semantic_entry_round_trips_and_finds_cosine_nearest_match(
        self,
    ) -> None:
        redis = FakeRedis()
        cache = _cache(redis, ttl=45)
        entry = _semantic_entry()

        await cache.set_semantic_entry(entry)
        matches = await cache.get_similar_entries(_scope(), [0.8, 0.2])

        assert len(matches) == 1
        assert matches[0].entry == entry
        assert matches[0].similarity == pytest.approx(0.9701425)
        assert redis.expires[cache._semantic_index_key(_scope())] == 45
        assert (
            redis.expires[cache._semantic_entry_key(entry.scope, entry.entry_id)] == 45
        )

    async def test_semantic_entries_are_isolated_by_scope(self) -> None:
        cache = _cache()
        await cache.set_semantic_entry(_semantic_entry())

        assert await cache.get_similar_entries(_scope(org_id="org-2"), [1.0, 0.0]) == []
        assert (
            await cache.get_similar_entries(
                _scope(embedding_config_hash="model-v2"), [1.0, 0.0]
            )
            == []
        )

    async def test_semantic_index_keeps_only_the_most_recent_100_entries(self) -> None:
        redis = FakeRedis()
        cache = _cache(redis)
        scope = _scope()

        for index in range(cache.MAX_SEMANTIC_ENTRIES + 2):
            await cache.set_semantic_entry(
                _semantic_entry(
                    query=f"query {index}",
                    created_at=float(index),
                    scope=scope,
                )
            )

        ids = await redis.zrange(cache._semantic_index_key(scope), 0, -1)
        assert len(ids) == cache.MAX_SEMANTIC_ENTRIES
        assert _semantic_entry(query="query 0").entry_id not in ids
        assert _semantic_entry(query="query 1").entry_id not in ids
        assert _semantic_entry(query="query 101").entry_id in ids

    async def test_semantic_lookup_prunes_stale_index_member(self) -> None:
        redis = FakeRedis()
        cache = _cache(redis)
        entry = _semantic_entry()
        await cache.set_semantic_entry(entry)
        redis.values.pop(cache._semantic_entry_key(entry.scope, entry.entry_id))

        assert await cache.get_similar_entries(entry.scope, [1.0, 0.0]) == []
        assert await redis.zrange(cache._semantic_index_key(entry.scope), 0, -1) == []

    async def test_semantic_query_validation_rejects_bad_vectors_and_limits(
        self,
    ) -> None:
        cache = _cache()

        with pytest.raises(ValueError, match="non-zero norm"):
            await cache.get_similar_entries(_scope(), [0.0, 0.0])
        with pytest.raises(ValueError, match="limit must be positive"):
            await cache.get_similar_entries(_scope(), [1.0, 0.0], limit=0)


class TestNoopRetrievalCandidatesCache:
    async def test_reads_miss_and_writes_are_ignored(self) -> None:
        cache = NoopRetrievalCandidatesCache()

        assert await cache.get_candidates("search") is None
        await cache.set_candidates("search", [_candidate()])
        await cache.set_semantic_entry(_semantic_entry())
        assert await cache.get_similar_entries(_scope(), [1.0, 0.0]) == []
        assert cache.enabled is False

    async def test_create_respects_disabled_environment_setting(
        self, monkeypatch
    ) -> None:
        monkeypatch.setenv(RetrievalCandidatesCache.ENV_ENABLED, "false")

        cache = await RetrievalCandidatesCache.create(MagicMock(), MagicMock())

        assert isinstance(cache, NoopRetrievalCandidatesCache)
