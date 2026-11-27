"""A task spawned inside a port call must not stay untimed forever."""

import asyncio

from app.utils import store_timing
from tests.unit.telemetry.test_db_metrics import _count, hist  # noqa: F401  (fixture)


class Store:
    def __init__(self) -> None:
        self.task = None

    async def get_document(self, key) -> str:
        return key

    async def subscribe(self) -> asyncio.Task[str]:
        # like RedisStore.subscribe_cache_invalidation: returns a long-lived task
        async def loop() -> str:
            await asyncio.sleep(0)
            return await self.get_document("k")  # port call from the listener
        self.task = asyncio.create_task(loop())
        return self.task


def test_spawned_long_lived_task_is_still_timed(hist) -> None:  # noqa: F811
    async def run() -> None:
        s = store_timing.announce_store_calls(Store(), "kv")
        await s.subscribe()
        await s.task
    asyncio.run(run())
    assert _count(hist, "get_document") == 1


def test_call_into_a_different_store_is_timed(hist) -> None:  # noqa: F811
    class Graph:
        def __init__(self, kv) -> None:
            self.kv = kv

        async def get_document(self, key) -> str:
            return await self.kv.get_key(key)

    class Kv:
        async def get_key(self, key) -> str:
            return key

    async def run() -> None:
        kv = store_timing.announce_store_calls(Kv(), "kv")
        await store_timing.announce_store_calls(Graph(kv), "graph").get_document("k")
    asyncio.run(run())
    assert _count(hist, "get_document") == 1
    assert _count(hist, "get_key") == 1
