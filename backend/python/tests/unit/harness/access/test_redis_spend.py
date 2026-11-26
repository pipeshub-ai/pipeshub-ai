"""E3 PA-5: ``SpendCounterContract`` on ``RedisSpendCounter`` (fakeredis with Lua, and the shared Redis)."""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING

import pytest

from app.harness.access.spend import RedisSpendCounter
from harness_core.access.ports import SpendKey
from harness_core.testing.contracts.model_access import SpendCounterContract

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class TestFakeRedisSpendCounter(SpendCounterContract):
    @pytest.fixture
    async def spend_counter(self) -> AsyncIterator[RedisSpendCounter]:
        fakeredis = pytest.importorskip("fakeredis")
        pytest.importorskip("lupa")
        client = fakeredis.FakeAsyncRedis(decode_responses=True)
        yield RedisSpendCounter(client, namespace="t:")
        await client.aclose()


@pytest.mark.integration
@pytest.mark.skipif(not os.environ.get("REDIS_IT_URL"), reason="needs a Redis (REDIS_IT_URL)")
class TestRealRedisSpendCounter(SpendCounterContract):
    @pytest.fixture
    def spend_key(self) -> SpendKey:
        return SpendKey("org-it", f"run-{uuid.uuid4().hex[:8]}")

    @pytest.fixture
    async def spend_counter(self) -> AsyncIterator[RedisSpendCounter]:
        from redis.asyncio import Redis

        client = Redis.from_url(os.environ["REDIS_IT_URL"], decode_responses=True)
        namespace = f"it-{uuid.uuid4().hex[:8]}:"
        yield RedisSpendCounter(client, namespace=namespace, ttl_s=120)
        async for key in client.scan_iter(f"{namespace}*"):
            _ = await client.delete(key)
        await client.aclose()


async def test_pa5_keys_share_one_cluster_slot_and_expire() -> None:
    fakeredis = pytest.importorskip("fakeredis")
    pytest.importorskip("lupa")
    client = fakeredis.FakeAsyncRedis(decode_responses=True)
    counter = RedisSpendCounter(client, namespace="ns:", ttl_s=60)
    from harness_core.access.types import Spend

    await counter.charge(SpendKey("org-1", "run-1"), "c-1", Spend(5, 5))
    keys = sorted([k async for k in client.scan_iter("ns:*")])
    assert keys == ["ns:harness:spend:{org-1:run-1}", "ns:harness:spend:{org-1:run-1}:charge:c-1"]
    for key in keys:
        assert 0 < await client.ttl(key) <= 60
    await client.aclose()
