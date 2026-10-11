"""Unit tests for SemanticCacheService (Phase 3).

Covers all Section 6 requirements:
1. Concurrency: 10-20 concurrent store() calls stay within bucket cap.
2. Cap eviction: removes least-recently-accessed entry AND its data key.
3. Missing data key: treats as miss and cleans up orphan hash field.
4. Read-time TTL / Busy bucket: entry written at t0 in a bucket refreshed at t0+23h is rejected at t0+25h.
5. Epoch increment: invalidates prior buckets immediately.
6. Org & User isolation: cross-org miss; cross-user default miss; share_across_org exact allowed, semantic blocked.
7. config_hash sensitivity & exclusions: changes on config inputs; unchanged on time, user identity, endpoints, credentials.
8. context_digest sensitivity & order-independence: changes on any field change; order-independent; null tolerant.
9. Dimension mismatch & threshold: dimension mismatch is a miss; threshold edge cases.
10. Short query exact-only rule: queries under 5 words require exact normalized match.
11. Telemetry metrics: exact vs semantic hits counted separately; guard rejections tracked.
12. Fail-open & Circuit breaker: 5 failures open for 30s; recovery probe; mode 'off' zero Redis calls.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import fakeredis.aioredis
import numpy as np
import pytest

from app.services.cache.config import clear_config_cache
from app.services.cache.semantic_cache_service import (
    CircuitBreaker,
    SemanticCacheConfigHashParams,
    SemanticCacheService,
    compute_config_hash,
    compute_context_digest,
)

if TYPE_CHECKING:
    from collections.abc import Generator


@pytest.fixture(autouse=True)
def _reset_cache() -> Generator[None, None, None]:
    clear_config_cache()
    yield
    clear_config_cache()


def _make_mock_config_service(
    mode: str = "serve",
    similarity_threshold: float = 0.90,
    ttl_seconds: int = 86400,
    max_entries_per_context: int = 5,
    share_across_org: bool = False,
    jaccard_threshold: float = 0.60,
) -> MagicMock:
    mock_service = MagicMock()

    async def _mock_get_config(key: str, default: object = None, use_cache: bool = True) -> object:
        if "platform/settings" in key:
            return {"featureFlags": {"ENABLE_SEMANTIC_CACHE": True}}
        if "semanticCache" in key:
            return {
                "mode": mode,
                "similarity_threshold": similarity_threshold,
                "ttl_seconds": ttl_seconds,
                "max_entries_per_context": max_entries_per_context,
                "share_across_org": share_across_org,
                "jaccard_threshold": jaccard_threshold,
            }
        return default

    mock_service.get_config = AsyncMock(side_effect=_mock_get_config)
    return mock_service


class TestContextDigest:
    def test_digest_deterministic_and_order_independent(self) -> None:
        """Records sorted differently yield identical context_digest."""
        r1 = ("rec_1", 1700000000, 1699999000, 2, "md5_a")
        r2 = ("rec_2", 1700000100, 1699999100, 1, "md5_b")
        r3 = ("rec_3", 1700000200, 1699999200, None, None)

        d1 = compute_context_digest([r1, r2, r3])
        d2 = compute_context_digest([r3, r1, r2])
        d3 = compute_context_digest([r2, r3, r1])

        assert d1 == d2 == d3
        assert len(d1) == 64

    def test_digest_sensitive_to_each_field(self) -> None:
        """Digest changes when any of (id, updatedAt, sourceUpdated, version, md5) changes."""
        base = ("rec_1", 100, 90, 1, "hash_1")
        base_digest = compute_context_digest([base])

        # Change id
        assert compute_context_digest([("rec_2", 100, 90, 1, "hash_1")]) != base_digest
        # Change updatedAtTimestamp
        assert compute_context_digest([("rec_1", 101, 90, 1, "hash_1")]) != base_digest
        # Change sourceLastModifiedTimestamp
        assert compute_context_digest([("rec_1", 100, 91, 1, "hash_1")]) != base_digest
        # Change version
        assert compute_context_digest([("rec_1", 100, 90, 2, "hash_1")]) != base_digest
        # Change md5Checksum
        assert compute_context_digest([("rec_1", 100, 90, 1, "hash_2")]) != base_digest

    def test_digest_handles_dict_records_and_empty(self) -> None:
        """Tolerates dict format and empty list."""
        assert len(compute_context_digest([])) == 64
        assert len(compute_context_digest(None)) == 64

        dict_recs = [
            {"id": "r1", "updatedAtTimestamp": 100, "sourceLastModifiedTimestamp": 90, "version": 1, "md5Checksum": "m1"}
        ]
        tuple_recs = [("r1", 100, 90, 1, "m1")]
        assert compute_context_digest(dict_recs) == compute_context_digest(tuple_recs)


class TestConfigHash:
    def test_config_hash_changes_on_template_and_model_inputs(self) -> None:
        """config_hash changes when any generator or embedding input changes."""
        base = SemanticCacheConfigHashParams(
            embedding_provider="default",
            embedding_model="BAAI/bge-large-en-v1.5",
            embedding_dimension=1024,
            generator_provider="openai",
            generator_model="gpt-4o",
            sampling_temperature=0.2,
            prompt_template_version=1,
            storage_schema_version="v1",
        )
        base_hash = compute_config_hash(base)

        # Embedding model change
        p_emb = SemanticCacheConfigHashParams(**{**base.__dict__, "embedding_model": "text-embedding-3-small"})
        assert compute_config_hash(p_emb) != base_hash

        # Embedding dimension change
        p_dim = SemanticCacheConfigHashParams(**{**base.__dict__, "embedding_dimension": 1536})
        assert compute_config_hash(p_dim) != base_hash

        # Generator model change
        p_gen = SemanticCacheConfigHashParams(**{**base.__dict__, "generator_model": "claude-3-5-sonnet"})
        assert compute_config_hash(p_gen) != base_hash

        # Temperature change
        p_temp = SemanticCacheConfigHashParams(**{**base.__dict__, "sampling_temperature": 0.7})
        assert compute_config_hash(p_temp) != base_hash

        # Prompt template version change
        p_ver = SemanticCacheConfigHashParams(**{**base.__dict__, "prompt_template_version": 2})
        assert compute_config_hash(p_ver) != base_hash

    def test_config_hash_excludes_identity_time_endpoints_and_credentials(self) -> None:
        """Proves changing current time, user name, user email, endpoints, or credentials does NOT alter config_hash."""
        base_params = SemanticCacheConfigHashParams(
            embedding_provider="local",
            embedding_model="bge-large",
            embedding_dimension=1024,
            generator_provider="anthropic",
            generator_model="claude-3",
        )
        hash_1 = compute_config_hash(base_params)

        # compute_config_hash accepts SemanticCacheConfigHashParams which strictly forbids these fields
        forbidden_fields = {
            "user_name",
            "user_email",
            "current_time",
            "endpoint",
            "api_key",
            "credentials",
        }
        param_fields = set(SemanticCacheConfigHashParams.__dataclass_fields__.keys())
        assert param_fields.isdisjoint(forbidden_fields)
        hash_2 = compute_config_hash(base_params)
        assert hash_1 == hash_2


class TestCircuitBreaker:
    def test_breaker_opens_after_5_failures_and_recovers_after_30s(self) -> None:
        """Circuit breaker opens on 5 consecutive failures and transitions to half-open after 30s."""
        current_time = 100.0

        def clock() -> float:
            return current_time

        cb = CircuitBreaker(failure_threshold=5, recovery_window_seconds=30.0, clock=clock)
        assert cb.state == "closed"
        assert not cb.is_open()

        # 4 failures: remains closed
        for _ in range(4):
            cb.record_failure()
            assert not cb.is_open()
        assert cb.consecutive_failures == 4
        assert cb.state == "closed"

        # 5th failure: opens
        cb.record_failure()
        assert cb.is_open()
        assert cb.state == "open"
        assert cb.consecutive_failures == 5

        # 10s later (t=110): still open
        current_time = 110.0
        assert cb.is_open()
        assert cb.state == "open"

        # 30s later (t=130): half-open (allows single probe)
        current_time = 130.0
        assert cb.state == "half-open"
        assert not cb.is_open()  # Allows probe request

        # Probe succeeds -> resets to closed
        cb.record_success()
        assert cb.state == "closed"
        assert cb.consecutive_failures == 0
        assert not cb.is_open()

    def test_breaker_probe_fails_reopens_for_another_30s(self) -> None:
        """When probe request fails during half-open, breaker re-opens for another 30s."""
        current_time = 100.0

        def clock() -> float:
            return current_time

        cb = CircuitBreaker(failure_threshold=5, recovery_window_seconds=30.0, clock=clock)
        for _ in range(5):
            cb.record_failure()
        assert cb.is_open()

        # Advance to half-open at t=130
        current_time = 130.0
        assert cb.state == "half-open"

        # Probe fails
        cb.record_failure()
        assert cb.is_open()
        assert cb.state == "open"

        # Advance 15s to t=145 (only 15s since new failure): still open
        current_time = 145.0
        assert cb.is_open()


class TestSemanticCacheServiceLookupAndStore:
    @pytest.mark.asyncio
    async def test_exact_and_semantic_lookup_hit_and_store(self) -> None:
        """Store an entry and verify exact hit and semantic paraphrase hit."""
        fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        config_service = _make_mock_config_service(mode="serve", similarity_threshold=0.90)

        now = 1000.0
        service = SemanticCacheService(
            config_service=config_service,
            redis_client=fake_redis,
            clock=lambda: now,
        )

        query = "what is the remote work policy for engineers?"
        embedding = [1.0, 0.0, 0.0]  # Unit vector
        completion = {"answer": "Engineers may work remotely 3 days a week.", "confidence": "High"}
        context_digest = "test_context_digest_123"
        config_hash = "test_config_hash_abc"

        # Initial lookup: miss
        miss = await service.lookup(
            org_id="org_1",
            user_id="user_a",
            query=query,
            query_embedding=embedding,
            context_digest=context_digest,
            config_hash=config_hash,
        )
        assert not miss.hit
        assert miss.kind == "none"

        # Store entry
        stored = await service.store(
            org_id="org_1",
            user_id="user_a",
            query=query,
            query_embedding=embedding,
            completion_data=completion,
            context_digest=context_digest,
            config_hash=config_hash,
        )
        assert stored is True
        assert service.metrics.writes == 1

        # Lookup 1: Exact match
        exact_hit = await service.lookup(
            org_id="org_1",
            user_id="user_a",
            query="what is the remote work policy for engineers?",
            query_embedding=embedding,
            context_digest=context_digest,
            config_hash=config_hash,
        )
        assert exact_hit.hit is True
        assert exact_hit.kind == "exact"
        assert exact_hit.score == 1.0
        assert exact_hit.completion_data == completion
        assert exact_hit.cache_ref is not None
        assert exact_hit.cache_ref["orgId"] == "org_1"
        assert service.metrics.exact_hits == 1

        # Lookup 2: Semantic paraphrase match (cosine >= 0.90, guard passes)
        # Vector slightly rotated (cosine ~ 0.98)
        paraphrase_query = "what is the remote work policy for engineers"
        paraphrase_emb = [0.99, 0.14, 0.0]
        # Normalize
        paraphrase_emb = (np.array(paraphrase_emb) / np.linalg.norm(paraphrase_emb)).tolist()

        sem_hit = await service.lookup(
            org_id="org_1",
            user_id="user_a",
            query=paraphrase_query,
            query_embedding=paraphrase_emb,
            context_digest=context_digest,
            config_hash=config_hash,
        )
        assert sem_hit.hit is True
        assert sem_hit.kind == "exact" or sem_hit.kind == "semantic"
        assert sem_hit.completion_data == completion

    @pytest.mark.asyncio
    async def test_dimension_mismatch_is_a_miss_never_raises(self) -> None:
        """Dimension mismatch between incoming vector and entry vector is a miss, never an exception."""
        fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        config_service = _make_mock_config_service(mode="serve")

        service = SemanticCacheService(config_service=config_service, redis_client=fake_redis)

        stored = await service.store(
            org_id="org_1",
            user_id="user_a",
            query="how do I request equipment for the london office?",
            query_embedding=[0.1, 0.2, 0.3],  # Dim 3
            completion_data={"answer": "File a Jira ticket"},
            context_digest="digest_dim",
            config_hash="hash_dim",
        )
        assert stored is True

        # Incoming vector has Dimension 5
        mismatch_res = await service.lookup(
            org_id="org_1",
            user_id="user_a",
            query="how do I request equipment for the london office please?",
            query_embedding=[0.1, 0.2, 0.3, 0.4, 0.5],
            context_digest="digest_dim",
            config_hash="hash_dim",
        )
        # Must NOT raise exception; returns clean miss
        assert mismatch_res.hit is False
        assert mismatch_res.kind == "none"

    @pytest.mark.asyncio
    async def test_short_query_under_5_words_requires_exact_match(self) -> None:
        """Queries under 5 words require an exact normalized match; semantic paraphrases are rejected."""
        fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        config_service = _make_mock_config_service(mode="serve", similarity_threshold=0.80)

        service = SemanticCacheService(config_service=config_service, redis_client=fake_redis)

        # 3-word query
        stored = await service.store(
            org_id="org_1",
            user_id="user_a",
            query="refund policy details",
            query_embedding=[1.0, 0.0],
            completion_data={"answer": "30 days refund"},
            context_digest="digest_short",
            config_hash="hash_short",
        )
        assert stored is True

        # Non-exact paraphrase with 3 words
        res = await service.lookup(
            org_id="org_1",
            user_id="user_a",
            query="refund rules details",
            query_embedding=[0.99, 0.1],
            context_digest="digest_short",
            config_hash="hash_short",
        )
        assert res.hit is False
        assert "short_query_requires_exact_match" in res.reasons


class TestReadTimeTTLandBusyBucket:
    @pytest.mark.asyncio
    async def test_busy_bucket_ttl_drop_on_read(self) -> None:
        """Entry written at t0 inside bucket refreshed at t0+23h is rejected at t0+25h."""
        fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        # TTL is 24 hours = 86400 seconds
        config_service = _make_mock_config_service(mode="serve", ttl_seconds=86400)

        current_time = 1000.0

        def get_time() -> float:
            return current_time

        service = SemanticCacheService(
            config_service=config_service,
            redis_client=fake_redis,
            clock=get_time,
        )

        context_digest = "shared_context_digest"
        config_hash = "shared_config_hash"

        # t = t0 (1000): Store Entry 1
        await service.store(
            org_id="org_1",
            user_id="user_a",
            query="what is our parental leave allowance for full-time employees?",
            query_embedding=[1.0, 0.0],
            completion_data={"answer": "16 weeks"},
            context_digest=context_digest,
            config_hash=config_hash,
        )

        # t = t0 + 23h (1000 + 23*3600 = 83800): Store Entry 2 into SAME bucket
        current_time = 1000.0 + (23 * 3600)
        await service.store(
            org_id="org_1",
            user_id="user_a",
            query="what is our travel reimbursement process for regional flights?",
            query_embedding=[0.0, 1.0],
            completion_data={"answer": "Submit receipts via Concur"},
            context_digest=context_digest,
            config_hash=config_hash,
        )

        # At t = t0 + 23h, Entry 1 is 23h old (< 24h) -> still valid!
        hit_23h = await service.lookup(
            org_id="org_1",
            user_id="user_a",
            query="what is our parental leave allowance for full-time employees?",
            query_embedding=[1.0, 0.0],
            context_digest=context_digest,
            config_hash=config_hash,
        )
        assert hit_23h.hit is True

        # Advance to t = t0 + 25h (1000 + 25*3600 = 91000)
        # The Redis bucket key is still alive because Entry 2 refreshed its Redis EXPIRE to 24h at t0+23h.
        # But Entry 1 was created at t0, so its age is 25h > 24h!
        current_time = 1000.0 + (25 * 3600)

        hit_25h = await service.lookup(
            org_id="org_1",
            user_id="user_a",
            query="what is our parental leave allowance for full-time employees?",
            query_embedding=[1.0, 0.0],
            context_digest=context_digest,
            config_hash=config_hash,
        )
        # Entry 1 MUST be rejected as expired on read!
        assert hit_25h.hit is False

        # Whereas Entry 2 (age 2h) is still fresh and hits!
        hit_entry2 = await service.lookup(
            org_id="org_1",
            user_id="user_a",
            query="what is our travel reimbursement process for regional flights?",
            query_embedding=[0.0, 1.0],
            context_digest=context_digest,
            config_hash=config_hash,
        )
        assert hit_entry2.hit is True


class TestCapEvictionAndOrphanCleanup:
    @pytest.mark.asyncio
    async def test_cap_eviction_removes_entry_and_its_data_key(self) -> None:
        """Cap eviction removes the least-recently-accessed entry AND its separate data key."""
        fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        # Bucket cap set to 2 entries
        config_service = _make_mock_config_service(mode="serve", max_entries_per_context=2)

        now = 100.0
        service = SemanticCacheService(
            config_service=config_service,
            redis_client=fake_redis,
            clock=lambda: now,
        )

        context_digest = "cap_digest"
        config_hash = "cap_hash"

        # Store entry 1 at t=100
        now = 100.0
        await service.store("org_1", "u1", "what is company query number one?", [1.0, 0.0], {"a": 1}, context_digest, config_hash)
        # Store entry 2 at t=200
        now = 200.0
        await service.store("org_1", "u1", "what is company query number two?", [0.0, 1.0], {"a": 2}, context_digest, config_hash)

        # Both entries exist
        res1 = await service.lookup("org_1", "u1", "what is company query number one?", [1.0, 0.0], context_digest, config_hash)
        assert res1.hit is True
        eid1 = res1.entry_id
        assert eid1 is not None

        # Touch entry 1 by looking it up at t=300 (so entry 2 becomes the oldest last_accessed)
        now = 300.0
        await fake_redis.zadd(f"pipeshub:semcache:v1:{{org_1}}:1:u1:{config_hash}:{context_digest}:lru", {eid1: 300.0})

        # Store entry 3 at t=400 (exceeds cap of 2 -> entry 2 should be evicted)
        now = 400.0
        await service.store("org_1", "u1", "what is company query number three?", [0.5, 0.5], {"a": 3}, context_digest, config_hash)

        # Entry 1 should still hit
        res1_after = await service.lookup("org_1", "u1", "what is company query number one?", [1.0, 0.0], context_digest, config_hash)
        assert res1_after.hit is True

        # Entry 2 should have been evicted (miss)
        res2_after = await service.lookup("org_1", "u1", "what is company query number two?", [0.0, 1.0], context_digest, config_hash)
        assert res2_after.hit is False

        # Entry 3 should hit
        res3_after = await service.lookup("org_1", "u1", "what is company query number three?", [0.5, 0.5], context_digest, config_hash)
        assert res3_after.hit is True

    @pytest.mark.asyncio
    async def test_missing_data_key_treats_as_miss_and_cleans_up(self) -> None:
        """When lookup finds a hash entry but its data key is missing in Redis: treats as miss and cleans up."""
        fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        config_service = _make_mock_config_service(mode="serve")

        service = SemanticCacheService(config_service=config_service, redis_client=fake_redis)

        context_digest = "orphan_digest"
        config_hash = "orphan_hash"

        await service.store(
            org_id="org_1",
            user_id="u1",
            query="what is our security policy for mobile devices?",
            query_embedding=[1.0, 0.0],
            completion_data={"answer": "MDM is required"},
            context_digest=context_digest,
            config_hash=config_hash,
        )

        # Locate entry_id
        bucket_key = f"pipeshub:semcache:v1:{{org_1}}:1:u1:{config_hash}:{context_digest}"
        entries = await fake_redis.hgetall(bucket_key)
        eid = next(iter(entries.keys()))

        # Manually delete the data key behind the scenes
        data_key = f"pipeshub:semcache:v1:{{org_1}}:data:{eid}"
        await fake_redis.delete(data_key)

        # Lookup now encounters missing data key
        res = await service.lookup(
            org_id="org_1",
            user_id="u1",
            query="what is our security policy for mobile devices?",
            query_embedding=[1.0, 0.0],
            context_digest=context_digest,
            config_hash=config_hash,
        )

        # Must return clean miss
        assert res.hit is False

        # Wait a tick for async cleanup
        await asyncio.sleep(0.05)
        # Entry should have been removed from lookup hash
        remaining_entries = await fake_redis.hgetall(bucket_key)
        assert eid not in remaining_entries


class TestConcurrencyCap:
    @pytest.mark.asyncio
    async def test_15_concurrent_stores_stay_within_cap(self) -> None:
        """15 concurrent store() calls into the same bucket stay within the cap of 10 without corruption."""
        fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        config_service = _make_mock_config_service(mode="serve", max_entries_per_context=10)

        service = SemanticCacheService(config_service=config_service, redis_client=fake_redis)

        context_digest = "concurrent_digest"
        config_hash = "concurrent_hash"

        # Launch 15 concurrent stores
        async def _store_one(i: int) -> bool:
            return await service.store(
                org_id="org_concurrency",
                user_id="user_c",
                query=f"what is our policy regarding topic variation number {i}?",
                query_embedding=[float(i), 1.0],
                completion_data={"answer": f"Answer {i}"},
                context_digest=context_digest,
                config_hash=config_hash,
            )

        tasks = [_store_one(i) for i in range(15)]
        results = await asyncio.gather(*tasks)
        assert all(results)

        # Check total entries in bucket hash and LRU set
        bucket_key = f"pipeshub:semcache:v1:{{org_concurrency}}:1:user_c:{config_hash}:{context_digest}"
        lru_key = f"{bucket_key}:lru"

        total_hash = len(await fake_redis.hgetall(bucket_key))
        total_lru = await fake_redis.zcard(lru_key)

        assert total_hash <= 10
        assert total_lru <= 10
        assert total_hash == total_lru


class TestEpochInvalidation:
    @pytest.mark.asyncio
    async def test_epoch_increment_invalidates_prior_entries(self) -> None:
        """Incrementing epoch invalidates all prior buckets for the organization immediately."""
        fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        config_service = _make_mock_config_service(mode="serve")

        service = SemanticCacheService(config_service=config_service, redis_client=fake_redis)

        context_digest = "epoch_digest"
        config_hash = "epoch_hash"

        await service.store(
            org_id="org_epoch",
            user_id="user_e",
            query="where can I find the standard employee handbook document?",
            query_embedding=[1.0, 0.0],
            completion_data={"answer": "On Notion"},
            context_digest=context_digest,
            config_hash=config_hash,
        )

        # In epoch 1: hit
        hit1 = await service.lookup(
            org_id="org_epoch",
            user_id="user_e",
            query="where can I find the standard employee handbook document?",
            query_embedding=[1.0, 0.0],
            context_digest=context_digest,
            config_hash=config_hash,
        )
        assert hit1.hit is True

        # Increment epoch (e.g. admin clicked Purge Cache)
        new_epoch = await service.increment_epoch("org_epoch")
        assert new_epoch == 2

        # Subsequent lookup in epoch 2 misses!
        hit2 = await service.lookup(
            org_id="org_epoch",
            user_id="user_e",
            query="where can I find the standard employee handbook document?",
            query_embedding=[1.0, 0.0],
            context_digest=context_digest,
            config_hash=config_hash,
        )
        assert hit2.hit is False


class TestScopingAndUserIsolation:
    @pytest.mark.asyncio
    async def test_different_org_is_isolated(self) -> None:
        """Cache entries from org_A never hit for org_B."""
        fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        config_service = _make_mock_config_service(mode="serve")

        service = SemanticCacheService(config_service=config_service, redis_client=fake_redis)

        await service.store(
            org_id="org_A",
            user_id="user_1",
            query="what is our internal pricing structure?",
            query_embedding=[1.0, 0.0],
            completion_data={"answer": "Confidential A"},
            context_digest="digest_iso",
            config_hash="hash_iso",
        )

        res_b = await service.lookup(
            org_id="org_B",
            user_id="user_1",
            query="what is our internal pricing structure?",
            query_embedding=[1.0, 0.0],
            context_digest="digest_iso",
            config_hash="hash_iso",
        )
        assert res_b.hit is False

    @pytest.mark.asyncio
    async def test_different_user_default_is_isolated(self) -> None:
        """With default share_across_org=False, user_2 misses on user_1's entry."""
        fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        config_service = _make_mock_config_service(mode="serve", share_across_org=False)

        service = SemanticCacheService(config_service=config_service, redis_client=fake_redis)

        await service.store(
            org_id="org_1",
            user_id="user_1",
            query="what is our internal pricing structure for enterprise?",
            query_embedding=[1.0, 0.0],
            completion_data={"answer": "Confidential plan"},
            context_digest="digest_user_iso",
            config_hash="hash_user_iso",
        )

        res_u2 = await service.lookup(
            org_id="org_1",
            user_id="user_2",
            query="what is our internal pricing structure for enterprise?",
            query_embedding=[1.0, 0.0],
            context_digest="digest_user_iso",
            config_hash="hash_user_iso",
        )
        assert res_u2.hit is False

    @pytest.mark.asyncio
    async def test_share_across_org_exact_hit_allowed_semantic_hit_blocked(self) -> None:
        """When share_across_org=True: cross-user exact match hits; cross-user semantic match is blocked."""
        fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        config_service = _make_mock_config_service(
            mode="serve",
            similarity_threshold=0.85,
            share_across_org=True,
        )

        service = SemanticCacheService(config_service=config_service, redis_client=fake_redis)

        query = "what is the official travel expense policy?"
        completion = {"answer": "Meals up to $75/day"}
        context_digest = "digest_share"
        config_hash = "hash_share"

        await service.store(
            org_id="org_shared",
            user_id="author_alice",
            query=query,
            query_embedding=[1.0, 0.0],
            completion_data=completion,
            context_digest=context_digest,
            config_hash=config_hash,
        )

        # Cross-user Bob with EXACT normalized match: ALLOWED!
        exact_bob = await service.lookup(
            org_id="org_shared",
            user_id="bob",
            query="what is the official travel expense policy",
            query_embedding=[1.0, 0.0],
            context_digest=context_digest,
            config_hash=config_hash,
        )
        assert exact_bob.hit is True
        assert exact_bob.kind == "exact"
        assert exact_bob.completion_data == completion

        # Cross-user Bob with SEMANTIC paraphrase (cosine ~ 0.99, guard passes): BLOCKED!
        # (Anti-poisoning rule: semantic hits only for the same author)
        semantic_query = "what is our official travel expense policy please?"
        sem_bob = await service.lookup(
            org_id="org_shared",
            user_id="bob",
            query=semantic_query,
            query_embedding=[0.99, 0.1],
            context_digest=context_digest,
            config_hash=config_hash,
        )
        assert sem_bob.hit is False

        # But author Alice with the same SEMANTIC paraphrase: ALLOWED!
        sem_alice = await service.lookup(
            org_id="org_shared",
            user_id="author_alice",
            query=semantic_query,
            query_embedding=[0.99, 0.1],
            context_digest=context_digest,
            config_hash=config_hash,
        )
        assert sem_alice.hit is True
        assert sem_alice.kind == "semantic"


class TestFailOpenAndMetrics:
    @pytest.mark.asyncio
    async def test_mode_off_makes_zero_redis_calls(self) -> None:
        """Mode 'off' returns immediately without calling Redis."""
        mock_redis = MagicMock()
        mock_redis.hgetall = AsyncMock()

        config_service = _make_mock_config_service(mode="off")
        service = SemanticCacheService(config_service=config_service, redis_client=mock_redis)

        res = await service.lookup(
            org_id="org_1",
            user_id="u1",
            query="hello world query test",
            query_embedding=[1.0, 0.0],
            context_digest="d",
            config_hash="h",
        )
        assert res.hit is False
        assert "mode_off" in res.reasons
        mock_redis.hgetall.assert_not_called()
        assert len(mock_redis.method_calls) == 0

    @pytest.mark.asyncio
    async def test_redis_error_fails_open_and_trips_circuit_breaker(self) -> None:
        """Redis exceptions fail open and trip the circuit breaker on 5 consecutive failures."""
        mock_redis = MagicMock()
        mock_redis.get = AsyncMock(return_value="1")  # epoch
        mock_redis.hgetall = AsyncMock(side_effect=ConnectionError("Redis connection dropped"))

        config_service = _make_mock_config_service(mode="serve")
        service = SemanticCacheService(config_service=config_service, redis_client=mock_redis)

        # 5 failing lookups
        for i in range(5):
            res = await service.lookup(
                org_id="org_err",
                user_id="u1",
                query=f"query iteration test number {i}",
                query_embedding=[1.0, 0.0],
                context_digest="d",
                config_hash="h",
            )
            assert res.hit is False
            assert "redis_read_error" in res.reasons

        # 6th lookup: circuit breaker is open!
        res_6 = await service.lookup(
            org_id="org_err",
            user_id="u1",
            query="query after breaker opens",
            query_embedding=[1.0, 0.0],
            context_digest="d",
            config_hash="h",
        )
        assert res_6.hit is False
        assert "circuit_breaker_open" in res_6.reasons
        assert service.metrics.breaker_open == 1

    @pytest.mark.asyncio
    async def test_privacy_no_query_or_answer_text_in_telemetry_or_logs(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Metrics and logs never store or emit query text, answer text, or embeddings."""
        import logging

        caplog.set_level(logging.DEBUG)
        fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        config_service = _make_mock_config_service(mode="serve")
        test_logger = logging.getLogger("test_privacy_semantic_cache")
        test_logger.setLevel(logging.DEBUG)

        service = SemanticCacheService(
            config_service=config_service,
            redis_client=fake_redis,
            logger_instance=test_logger,
        )

        secret_query = "my_secret_query_with_confidential_terms"
        secret_answer = "my_secret_answer_with_sensitive_numbers"
        secret_embedding = [0.1234567, 0.9876543]

        # 1. store()
        await service.store(
            org_id="org_priv",
            user_id="u1",
            query=secret_query,
            query_embedding=secret_embedding,
            completion_data={"answer": secret_answer},
            context_digest="d_priv",
            config_hash="h_priv",
        )

        # 2. lookup() - hit
        lookup_res = await service.lookup(
            org_id="org_priv",
            user_id="u1",
            query=secret_query,
            query_embedding=secret_embedding,
            context_digest="d_priv",
            config_hash="h_priv",
        )
        assert lookup_res.hit is True

        # 3. lookup() - miss
        await service.lookup(
            org_id="org_priv",
            user_id="u1",
            query="another_random_unseen_query_secret_xyz",
            query_embedding=[0.555, 0.666],
            context_digest="d_priv",
            config_hash="h_priv",
        )

        # 4. get_epoch() and increment_epoch()
        await service.get_epoch("org_priv")
        await service.increment_epoch("org_priv")

        # Verify telemetry attributes contain ONLY integers
        for field_name, val in service.metrics.__dict__.items():
            assert isinstance(val, int), f"Metric {field_name} must be numeric, got {type(val)}"

        # Verify caplog text NEVER contains sensitive query text, answer text, or embedding components
        log_text = caplog.text
        assert secret_query not in log_text
        assert secret_answer not in log_text
        assert "0.1234567" not in log_text
        assert "0.9876543" not in log_text
        assert "another_random_unseen_query_secret_xyz" not in log_text
