"""SemanticQueryCache: ACL-signature isolation, similarity matching, TTL, and
the guarantee that a broken Redis can never fail or stall a search."""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.cache.semantic_query_cache import (
    SemanticQueryCache,
    compute_acl_signature,
)

ORG = "org-1"
ACL_A = "acl-a"
ACL_B = "acl-b"

EMB_1 = [1.0, 0.0]
EMB_1_NEAR = [1.0, 0.1]  # cosine(EMB_1, EMB_1_NEAR) ~= 0.995, above default threshold
EMB_ORTHOGONAL = [0.0, 1.0]  # cosine == 0.0, clearly dissimilar

RESULTS_A = [{"metadata": {"virtualRecordId": "vr-1"}}]
RESULTS_B = [{"metadata": {"virtualRecordId": "vr-2"}}]


class FakeRedis:
    """In-memory stand-in for the handful of commands the cache uses."""

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.expires: dict[str, int] = {}
        self.calls: list[tuple] = []

    async def hgetall(self, key):
        self.calls.append(("hgetall", key))
        return dict(self.hashes.get(key, {}))

    async def hset(self, key, field, value):
        self.calls.append(("hset", key, field))
        self.hashes.setdefault(key, {})[field] = value
        return 1

    async def expire(self, key, ttl):
        self.calls.append(("expire", key, ttl))
        self.expires[key] = ttl
        return True

    async def ping(self):
        return True

    async def aclose(self):
        return None


class BrokenRedis(FakeRedis):
    async def hgetall(self, key):
        raise ConnectionError("redis down")

    async def hset(self, key, field, value):
        raise ConnectionError("redis down")

    async def expire(self, key, ttl):
        raise ConnectionError("redis down")


def _cache(redis=None, ttl=60, threshold=0.97, enabled=True) -> SemanticQueryCache:
    return SemanticQueryCache(
        MagicMock(), redis if redis is not None else FakeRedis(), ttl, threshold, enabled
    )


def _loader(value, counter=None):
    async def load():
        if counter is not None:
            counter.append(1)
        return value
    return load


class TestACLSignature:
    def test_deterministic(self) -> None:
        ids = {"vr-1": "rec-1", "vr-2": "rec-2"}
        assert compute_acl_signature(ids) == compute_acl_signature(ids)

    def test_order_independent(self) -> None:
        a = compute_acl_signature({"vr-1": "rec-1", "vr-2": "rec-2"})
        b = compute_acl_signature({"vr-2": "rec-2", "vr-1": "rec-1"})
        assert a == b

    def test_different_access_sets_produce_different_signatures(self) -> None:
        a = compute_acl_signature({"vr-1": "rec-1"})
        b = compute_acl_signature({"vr-1": "rec-1", "vr-2": "rec-2"})
        assert a != b

    def test_empty_access_set_does_not_raise(self) -> None:
        assert isinstance(compute_acl_signature({}), str)


class TestKeySchema:
    def test_orgs_are_isolated(self) -> None:
        cache = _cache()
        assert cache._partition_key("org-a", ACL_A) != cache._partition_key("org-b", ACL_A)

    def test_acl_signatures_are_isolated(self) -> None:
        cache = _cache()
        assert cache._partition_key(ORG, ACL_A) != cache._partition_key(ORG, ACL_B)


class TestReadThrough:
    async def test_miss_then_hit_for_identical_embedding(self) -> None:
        redis = FakeRedis()
        cache = _cache(redis)
        calls: list = []

        first = await cache.get_or_compute(ORG, ACL_A, "q", EMB_1, 5, _loader(RESULTS_A, calls))
        second = await cache.get_or_compute(ORG, ACL_A, "q", EMB_1, 5, _loader(RESULTS_A, calls))

        assert first == RESULTS_A
        assert second == RESULTS_A
        assert len(calls) == 1, "second call must be served from Redis"

    async def test_similar_embedding_above_threshold_is_a_hit(self) -> None:
        redis = FakeRedis()
        cache = _cache(redis)
        calls: list = []

        await cache.get_or_compute(ORG, ACL_A, "q1", EMB_1, 5, _loader(RESULTS_A, calls))
        out = await cache.get_or_compute(ORG, ACL_A, "q2", EMB_1_NEAR, 5, _loader(RESULTS_B, calls))

        assert out == RESULTS_A, "a cosine-similar query should reuse the cached answer"
        assert len(calls) == 1

    async def test_dissimilar_embedding_is_a_miss(self) -> None:
        redis = FakeRedis()
        cache = _cache(redis)
        calls: list = []

        await cache.get_or_compute(ORG, ACL_A, "q1", EMB_1, 5, _loader(RESULTS_A, calls))
        out = await cache.get_or_compute(
            ORG, ACL_A, "q2", EMB_ORTHOGONAL, 5, _loader(RESULTS_B, calls)
        )

        assert out == RESULTS_B
        assert len(calls) == 2, "an unrelated query must not reuse another query's results"

    async def test_different_acl_signature_never_shares_an_entry(self) -> None:
        """The core permission-safety guarantee: identical org, identical query,
        different access grants must never cross-hit."""
        redis = FakeRedis()
        cache = _cache(redis)
        calls: list = []

        await cache.get_or_compute(ORG, ACL_A, "q", EMB_1, 5, _loader(RESULTS_A, calls))
        out = await cache.get_or_compute(ORG, ACL_B, "q", EMB_1, 5, _loader(RESULTS_B, calls))

        assert out == RESULTS_B
        assert len(calls) == 2

    async def test_different_org_never_shares_an_entry(self) -> None:
        redis = FakeRedis()
        cache = _cache(redis)
        calls: list = []

        await cache.get_or_compute("org-a", ACL_A, "q", EMB_1, 5, _loader(RESULTS_A, calls))
        out = await cache.get_or_compute("org-b", ACL_A, "q", EMB_1, 5, _loader(RESULTS_B, calls))

        assert out == RESULTS_B
        assert len(calls) == 2

    async def test_cached_entry_with_smaller_limit_is_a_miss(self) -> None:
        """A cached top-5 answer must not silently serve a request that wants 10."""
        redis = FakeRedis()
        cache = _cache(redis)
        calls: list = []

        await cache.get_or_compute(ORG, ACL_A, "q", EMB_1, 5, _loader(RESULTS_A, calls))
        out = await cache.get_or_compute(ORG, ACL_A, "q", EMB_1, 10, _loader(RESULTS_B, calls))

        assert out == RESULTS_B
        assert len(calls) == 2

    async def test_cached_entry_with_larger_limit_is_truncated(self) -> None:
        redis = FakeRedis()
        cache = _cache(redis)
        wide = [{"metadata": {"virtualRecordId": f"vr-{i}"}} for i in range(5)]
        calls: list = []

        await cache.get_or_compute(ORG, ACL_A, "q", EMB_1, 5, _loader(wide, calls))
        out = await cache.get_or_compute(ORG, ACL_A, "q", EMB_1, 2, _loader(wide, calls))

        assert out == wide[:2]
        assert len(calls) == 1, "a narrower request should still be served from the wider cache entry"

    async def test_corrupt_entry_is_treated_as_a_miss(self) -> None:
        redis = FakeRedis()
        cache = _cache(redis)
        redis.hashes[cache._partition_key(ORG, ACL_A)] = {"field-1": "not-json"}

        out = await cache.get_or_compute(ORG, ACL_A, "q", EMB_1, 5, _loader(RESULTS_A))
        assert out == RESULTS_A


class TestTTL:
    async def test_stale_entry_is_not_served(self) -> None:
        redis = FakeRedis()
        cache = _cache(redis, ttl=60)
        key = cache._partition_key(ORG, ACL_A)
        redis.hashes[key] = {
            "field-1": json.dumps(
                {"t": time.time() - 3600, "e": EMB_1, "limit": 5, "r": RESULTS_A}
            )
        }
        calls: list = []

        out = await cache.get_or_compute(ORG, ACL_A, "q", EMB_1, 5, _loader(RESULTS_B, calls))

        assert out == RESULTS_B
        assert len(calls) == 1

    async def test_fresh_entry_is_served(self) -> None:
        redis = FakeRedis()
        cache = _cache(redis, ttl=60)
        key = cache._partition_key(ORG, ACL_A)
        redis.hashes[key] = {
            "field-1": json.dumps({"t": time.time(), "e": EMB_1, "limit": 5, "r": RESULTS_A})
        }
        calls: list = []

        out = await cache.get_or_compute(ORG, ACL_A, "q", EMB_1, 5, _loader(RESULTS_B, calls))

        assert out == RESULTS_A
        assert not calls

    async def test_write_refreshes_the_partition_ttl(self) -> None:
        redis = FakeRedis()
        cache = _cache(redis, ttl=77)
        await cache.get_or_compute(ORG, ACL_A, "q", EMB_1, 5, _loader(RESULTS_A))
        assert redis.expires[cache._partition_key(ORG, ACL_A)] == 77


class TestSingleFlight:
    async def test_concurrent_misses_run_the_loader_once(self) -> None:
        redis = FakeRedis()
        cache = _cache(redis)
        calls: list = []

        async def slow_loader():
            calls.append(1)
            await asyncio.sleep(0.02)
            return RESULTS_A

        results = await asyncio.gather(
            *[cache.get_or_compute(ORG, ACL_A, "q", EMB_1, 5, slow_loader) for _ in range(10)]
        )

        assert all(r == RESULTS_A for r in results)
        assert len(calls) == 1

    async def test_distinct_partitions_are_not_serialized(self) -> None:
        cache = _cache(FakeRedis())
        started: list = []

        def make(acl_id):
            async def load():
                started.append(acl_id)
                await asyncio.sleep(0.02)
                return RESULTS_A
            return load

        await asyncio.gather(
            cache.get_or_compute(ORG, "acl-a", "q", EMB_1, 5, make("a")),
            cache.get_or_compute(ORG, "acl-b", "q", EMB_1, 5, make("b")),
        )
        assert set(started) == {"a", "b"}

    async def test_lock_table_never_grows(self) -> None:
        cache = _cache(FakeRedis())
        before = len(cache._locks)
        for i in range(400):
            await cache.get_or_compute(ORG, f"acl-{i}", "q", EMB_1, 5, _loader(RESULTS_A))
        assert len(cache._locks) == before == cache.LOCK_STRIPES

    async def test_same_key_maps_to_one_stripe_and_is_stable(self) -> None:
        cache = _cache(FakeRedis())
        key = cache._partition_key(ORG, ACL_A)
        assert cache._lock_for(key) is cache._lock_for(key)
        other = _cache(FakeRedis())
        assert cache._locks.index(cache._lock_for(key)) == other._locks.index(
            other._lock_for(key)
        )


class TestOutageCost:
    """One Redis outage must cost one timeout per call, not two."""

    class DeadRedis:
        def __init__(self) -> None:
            self.ops: list[str] = []

        async def _fail(self, name: str) -> None:
            self.ops.append(name)
            raise ConnectionError("connection refused")

        async def hgetall(self, *a, **k) -> None:
            await self._fail("hgetall")

        async def hset(self, *a, **k) -> None:
            await self._fail("hset")

        async def expire(self, *a, **k) -> None:
            await self._fail("expire")

    async def test_one_redis_op_per_call_when_down(self) -> None:
        dead = self.DeadRedis()
        cache = _cache(dead)
        result = await cache.get_or_compute(ORG, ACL_A, "q", EMB_1, 5, _loader(RESULTS_A))

        assert result == RESULTS_A, "caller must still get correct data"
        assert dead.ops == ["hgetall"], (
            f"expected a single Redis attempt, got {dead.ops} - a failed read "
            f"must short-circuit instead of re-reading and writing"
        )

    async def test_breaker_still_skips_redis_on_later_calls(self) -> None:
        dead = self.DeadRedis()
        cache = _cache(dead)
        await cache.get_or_compute(ORG, ACL_A, "q", EMB_1, 5, _loader(RESULTS_A))
        before = len(dead.ops)
        for _ in range(5):
            await cache.get_or_compute(ORG, ACL_A, "q", EMB_1, 5, _loader(RESULTS_A))
        assert len(dead.ops) == before, "backoff should skip Redis entirely"

    async def test_write_is_skipped_when_the_locked_read_trips_the_breaker(self) -> None:
        dead = self.DeadRedis()
        cache = _cache(dead)
        assert cache.enabled
        result = await cache.get_or_compute(ORG, ACL_A, "q", EMB_1, 5, _loader(RESULTS_A))
        assert result == RESULTS_A
        assert "hset" not in dead.ops, f"write attempted after breaker tripped: {dead.ops}"


class TestKillSwitch:
    async def test_disabled_cache_always_calls_the_loader(self) -> None:
        redis = FakeRedis()
        cache = _cache(redis, enabled=False)
        calls: list = []

        await cache.get_or_compute(ORG, ACL_A, "q", EMB_1, 5, _loader(RESULTS_A, calls))
        await cache.get_or_compute(ORG, ACL_A, "q", EMB_1, 5, _loader(RESULTS_A, calls))

        assert len(calls) == 2
        assert not redis.calls, "a disabled cache must not touch Redis at all"

    async def test_create_honours_the_env_kill_switch(self, monkeypatch) -> None:
        monkeypatch.setenv(SemanticQueryCache.ENV_ENABLED, "off")
        config = MagicMock()
        config.get_redis_config = AsyncMock()

        cache = await SemanticQueryCache.create(MagicMock(), config)

        assert cache.enabled is False
        config.get_redis_config.assert_not_called()

    async def test_create_survives_an_unreachable_redis(self, monkeypatch) -> None:
        monkeypatch.delenv(SemanticQueryCache.ENV_ENABLED, raising=False)
        config = MagicMock()
        config.get_redis_config = AsyncMock(side_effect=RuntimeError("no redis config"))

        cache = await SemanticQueryCache.create(MagicMock(), config)

        assert cache.enabled is False
        assert await cache.get_or_compute(ORG, ACL_A, "q", EMB_1, 5, _loader(RESULTS_A)) == RESULTS_A

    async def test_ttl_env_override(self, monkeypatch) -> None:
        monkeypatch.setenv(SemanticQueryCache.ENV_ENABLED, "off")
        monkeypatch.setenv(SemanticQueryCache.ENV_TTL, "45")
        cache = await SemanticQueryCache.create(MagicMock(), MagicMock())
        assert cache._ttl == 45

    async def test_invalid_ttl_env_falls_back(self, monkeypatch) -> None:
        monkeypatch.setenv(SemanticQueryCache.ENV_ENABLED, "off")
        monkeypatch.setenv(SemanticQueryCache.ENV_TTL, "soon")
        cache = await SemanticQueryCache.create(MagicMock(), MagicMock())
        assert cache._ttl == SemanticQueryCache.DEFAULT_TTL_SECONDS

    async def test_similarity_threshold_env_override(self, monkeypatch) -> None:
        monkeypatch.setenv(SemanticQueryCache.ENV_ENABLED, "off")
        monkeypatch.setenv(SemanticQueryCache.ENV_SIMILARITY_THRESHOLD, "0.5")
        cache = await SemanticQueryCache.create(MagicMock(), MagicMock())
        assert cache._threshold == 0.5

    async def test_invalid_similarity_threshold_falls_back(self, monkeypatch) -> None:
        monkeypatch.setenv(SemanticQueryCache.ENV_ENABLED, "off")
        monkeypatch.setenv(SemanticQueryCache.ENV_SIMILARITY_THRESHOLD, "not-a-float")
        cache = await SemanticQueryCache.create(MagicMock(), MagicMock())
        assert cache._threshold == SemanticQueryCache.DEFAULT_SIMILARITY_THRESHOLD

    async def test_out_of_range_similarity_threshold_falls_back(self, monkeypatch) -> None:
        monkeypatch.setenv(SemanticQueryCache.ENV_ENABLED, "off")
        monkeypatch.setenv(SemanticQueryCache.ENV_SIMILARITY_THRESHOLD, "1.5")
        cache = await SemanticQueryCache.create(MagicMock(), MagicMock())
        assert cache._threshold == SemanticQueryCache.DEFAULT_SIMILARITY_THRESHOLD


class TestRedisDown:
    async def test_read_failure_falls_through_to_the_loader(self) -> None:
        cache = _cache(BrokenRedis())
        assert await cache.get_or_compute(ORG, ACL_A, "q", EMB_1, 5, _loader(RESULTS_A)) == RESULTS_A

    async def test_failure_trips_the_backoff(self) -> None:
        redis = BrokenRedis()
        cache = _cache(redis)

        await cache.get_or_compute(ORG, ACL_A, "q", EMB_1, 5, _loader(RESULTS_A))
        assert cache.enabled is False

        redis.calls.clear()
        assert await cache.get_or_compute(ORG, ACL_A, "q", EMB_1, 5, _loader(RESULTS_A)) == RESULTS_A
        assert not redis.calls, "while down, Redis must not be contacted at all"

    async def test_backoff_expires(self) -> None:
        cache = _cache(BrokenRedis())
        await cache.get_or_compute(ORG, ACL_A, "q", EMB_1, 5, _loader(RESULTS_A))
        assert cache.enabled is False

        cache._down_until = 0.0
        assert cache.enabled is True

    async def test_loader_exceptions_propagate_unchanged(self) -> None:
        cache = _cache(FakeRedis())

        async def failing():
            raise RuntimeError("search backend exploded")

        with pytest.raises(RuntimeError, match="search backend exploded"):
            await cache.get_or_compute(ORG, ACL_A, "q", EMB_1, 5, failing)


class TestCosineSimilarity:
    def test_identical_vectors_score_one(self) -> None:
        assert SemanticQueryCache._cosine_similarity(EMB_1, EMB_1) == pytest.approx(1.0)

    def test_orthogonal_vectors_score_zero(self) -> None:
        assert SemanticQueryCache._cosine_similarity(EMB_1, EMB_ORTHOGONAL) == pytest.approx(0.0)

    def test_zero_vector_scores_zero_not_a_division_error(self) -> None:
        assert SemanticQueryCache._cosine_similarity([0.0, 0.0], EMB_1) == 0.0

    def test_mismatched_dimensions_score_zero(self) -> None:
        assert SemanticQueryCache._cosine_similarity([1.0], [1.0, 0.0]) == 0.0


class TestClose:
    async def test_close_disables_and_releases(self) -> None:
        redis = FakeRedis()
        redis.aclose = AsyncMock()
        cache = _cache(redis)

        await cache.close()

        assert cache.enabled is False
        redis.aclose.assert_awaited_once()

    async def test_close_is_idempotent(self) -> None:
        cache = _cache(FakeRedis())
        await cache.close()
        await cache.close()
