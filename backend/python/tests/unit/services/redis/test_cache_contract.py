"""One contract, every cache transport (Phase 6).

`AccessibleRecordsCache.invalidate_connector` deletes two keys
(`capp:`/`cusr:`) that land in different hash slots on a real cluster or
MemoryDB (R5) -- a regression back to a single multi-key `DEL` only shows up
under the cluster parametrization here, exactly like the CROSSSLOT
regressions covered for the lease manager and retry tracker.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from app.services.cache.accessible_records_cache import AccessibleRecordsCache
from app.services.cache.redis_signed_url_cache import RedisSignedUrlCache
from tests.support.fake_cluster_redis import FakeClusterRedis

fakeredis_aioredis = pytest.importorskip("fakeredis.aioredis")


def _standalone_client() -> "fakeredis_aioredis.FakeRedis":
    return fakeredis_aioredis.FakeRedis(decode_responses=True)


def _cluster_client() -> FakeClusterRedis:
    return FakeClusterRedis()


TRANSPORTS = [
    pytest.param(_standalone_client, id="standalone"),
    pytest.param(_cluster_client, id="cluster"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("make_client", TRANSPORTS)
class TestAccessibleRecordsCacheContract:
    async def test_get_or_compute_calls_loader_once_then_serves_from_cache(
        self, make_client
    ) -> None:
        cache = AccessibleRecordsCache(MagicMock(), make_client(), ttl_seconds=60, enabled=True)
        calls = 0

        async def loader() -> dict[str, str]:
            nonlocal calls
            calls += 1
            return {"vr-1": "r-1"}

        first = await cache.get_or_compute_kb("org-1", "kb-1", loader)
        second = await cache.get_or_compute_kb("org-1", "kb-1", loader)

        assert first == {"vr-1": "r-1"}
        assert second == {"vr-1": "r-1"}
        assert calls == 1

    async def test_invalidate_connector_drops_both_slot_spanning_keys(
        self, make_client
    ) -> None:
        """`capp:` and `cusr:` keys for the same connector must both be gone
        after invalidation, even when they hash to different cluster
        slots -- this is the R5 CROSSSLOT regression test."""
        cache = AccessibleRecordsCache(MagicMock(), make_client(), ttl_seconds=60, enabled=True)

        async def app_loader() -> dict[str, str]:
            return {"vr-app": "r-app"}

        async def user_loader() -> dict[str, str]:
            return {"vr-user": "r-user"}

        await cache.get_or_compute_app_connector("org-1", "conn-1", app_loader)
        await cache.get_or_compute_user_connector("org-1", "conn-1", "user-1", user_loader)

        await cache.invalidate_connector("org-1", "conn-1")

        calls = 0

        async def recompute() -> dict[str, str]:
            nonlocal calls
            calls += 1
            return {"vr-app": "r-app"}

        await cache.get_or_compute_app_connector("org-1", "conn-1", recompute)
        assert calls == 1  # cache miss after invalidation, loader ran again

    async def test_ttl_expiry_forces_recompute(self, make_client) -> None:
        cache = AccessibleRecordsCache(MagicMock(), make_client(), ttl_seconds=1, enabled=True)
        calls = 0

        async def loader() -> dict[str, str]:
            nonlocal calls
            calls += 1
            return {"vr-1": "r-1"}

        await cache.get_or_compute_kb("org-1", "kb-1", loader)
        await asyncio.sleep(1.2)
        await cache.get_or_compute_kb("org-1", "kb-1", loader)

        assert calls == 2

    async def test_disabled_cache_always_calls_the_loader(self, make_client) -> None:
        cache = AccessibleRecordsCache(MagicMock(), make_client(), ttl_seconds=60, enabled=False)
        calls = 0

        async def loader() -> dict[str, str]:
            nonlocal calls
            calls += 1
            return {}

        await cache.get_or_compute_kb("org-1", "kb-1", loader)
        await cache.get_or_compute_kb("org-1", "kb-1", loader)

        assert calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("make_client", TRANSPORTS)
class TestSignedUrlCacheContract:
    async def test_get_on_a_miss_returns_none(self, make_client) -> None:
        cache = RedisSignedUrlCache(make_client())
        assert await cache.get("missing-key") is None

    async def test_set_then_get_round_trips(self, make_client) -> None:
        cache = RedisSignedUrlCache(make_client())

        await cache.set("record-1", "https://example.com/signed", ttl_seconds=60)

        assert await cache.get("record-1") == "https://example.com/signed"

    async def test_ttl_expiry_evicts_the_entry(self, make_client) -> None:
        cache = RedisSignedUrlCache(make_client())

        await cache.set("record-1", "https://example.com/signed", ttl_seconds=1)
        await asyncio.sleep(1.2)

        assert await cache.get("record-1") is None
