"""Unit tests for `RedisRateLimiter` -- Phase 8's webhook-ingress rate gate.
`fakeredis[lua]` (already a dev dependency, used by the trigger-store
contract suite's Lua scripts) backs the fixed-window `INCR`+`EXPIRE` script.
"""
from __future__ import annotations

import pytest
from fakeredis import aioredis as fake_aioredis

from app.services.tasks.adapters.redis.rate_limiter import RedisRateLimiter


@pytest.fixture
async def redis_client() -> fake_aioredis.FakeRedis:
    client = fake_aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


class TestAllow:
    async def test_allows_calls_under_the_limit(self, redis_client: fake_aioredis.FakeRedis) -> None:
        limiter = RedisRateLimiter(redis_client)
        for _ in range(3):
            assert await limiter.allow("key-1", limit=3, window_seconds=60) is True

    async def test_rejects_calls_over_the_limit(self, redis_client: fake_aioredis.FakeRedis) -> None:
        limiter = RedisRateLimiter(redis_client)
        for _ in range(3):
            await limiter.allow("key-1", limit=3, window_seconds=60)
        assert await limiter.allow("key-1", limit=3, window_seconds=60) is False

    async def test_different_keys_have_independent_limits(self, redis_client: fake_aioredis.FakeRedis) -> None:
        limiter = RedisRateLimiter(redis_client)
        for _ in range(3):
            await limiter.allow("key-1", limit=3, window_seconds=60)
        assert await limiter.allow("key-2", limit=3, window_seconds=60) is True
