"""Nested port calls are timed once, the wrapped store keeps its identity, and the factories wire the timing."""

import asyncio
import logging

from app.telemetry.modules import db_metrics
from tests.unit.telemetry.test_db_metrics import _count, hist  # noqa: F401  (fixture)


class Spawner:
    """Public, non-lifecycle coroutine that spawns a task which later does a port call."""

    async def get_document(self, key) -> str:
        return key

    async def refresh(self) -> None:
        async def child() -> str:
            await asyncio.sleep(0)
            return await self.get_document("k")

        self.task = asyncio.create_task(child())


def test_spawned_task_is_timed_task_scoped(hist) -> None:  # noqa: F811
    async def run() -> None:
        s = db_metrics.instrument_store(Spawner(), "kv")
        await s.refresh()
        await s.task

    asyncio.run(run())
    assert _count(hist, "refresh") == 1
    assert _count(hist, "get_document") == 1


def test_vector_factory_wires_independently(monkeypatch, hist) -> None:  # noqa: F811
    from app.services.vector_db.vector_db_provider_factory import (
        VectorDBProviderFactory,
    )

    class V:
        async def search(self) -> None: ...

    async def make(*_a, **_k) -> V:
        return V()

    monkeypatch.setenv("VECTOR_DB_TYPE", "qdrant")
    monkeypatch.setattr(VectorDBProviderFactory, "_create_qdrant", staticmethod(make))
    vec = asyncio.run(VectorDBProviderFactory.create_provider(logging.getLogger("t"), None))
    assert "search" in vars(vec)
