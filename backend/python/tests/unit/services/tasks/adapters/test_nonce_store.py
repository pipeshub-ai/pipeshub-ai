"""Unit tests for `RedisNonceStore` -- Phase 8's webhook replay-protection
primitive. `fakeredis` (already a dev dependency for the trigger/run store
contract suites) supports `SET NX EX` directly, so no Lua/`lupa` is needed
here."""
from __future__ import annotations

import pytest
from fakeredis import aioredis as fake_aioredis

from app.services.tasks.adapters.redis.nonce_store import RedisNonceStore


@pytest.fixture
async def redis_client() -> fake_aioredis.FakeRedis:
    client = fake_aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


class TestCheckAndSet:
    async def test_first_use_of_a_nonce_is_allowed(self, redis_client: fake_aioredis.FakeRedis) -> None:
        store = RedisNonceStore(redis_client)
        assert await store.check_and_set("webhook-1", "nonce-a", ttl_seconds=60) is True

    async def test_replayed_nonce_is_rejected(self, redis_client: fake_aioredis.FakeRedis) -> None:
        store = RedisNonceStore(redis_client)
        await store.check_and_set("webhook-1", "nonce-a", ttl_seconds=60)
        assert await store.check_and_set("webhook-1", "nonce-a", ttl_seconds=60) is False

    async def test_same_nonce_in_different_scopes_is_independent(self, redis_client: fake_aioredis.FakeRedis) -> None:
        """Scoping by `webhook_id` -- two different webhooks reusing the same
        nonce value (e.g. both senders happen to generate "abc") must not
        collide."""
        store = RedisNonceStore(redis_client)
        assert await store.check_and_set("webhook-1", "nonce-a", ttl_seconds=60) is True
        assert await store.check_and_set("webhook-2", "nonce-a", ttl_seconds=60) is True

    async def test_different_nonces_in_same_scope_both_allowed(self, redis_client: fake_aioredis.FakeRedis) -> None:
        store = RedisNonceStore(redis_client)
        assert await store.check_and_set("webhook-1", "nonce-a", ttl_seconds=60) is True
        assert await store.check_and_set("webhook-1", "nonce-b", ttl_seconds=60) is True
