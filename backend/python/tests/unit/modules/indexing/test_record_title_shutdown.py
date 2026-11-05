"""Shutdown while the idle timer is mid-flush must not lose the batch it already took."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from tests.unit.modules.indexing.test_record_title_consumer import (
    FakeGraph,
    FakeStore,
    _msg,
    _record,
    _writer,
)

pytestmark = pytest.mark.asyncio


async def test_closing_during_an_idle_flush_still_writes_the_batch() -> None:
    graph, store = FakeGraph(), FakeStore()
    graph.records["r1"] = _record()
    gate = asyncio.Event()
    real = store.upsert_entities_batch

    async def slow(*a: Any, **k: Any) -> Any:  # noqa: ANN401
        await gate.wait()
        return await real(*a, **k)

    store.upsert_entities_batch = slow  # type: ignore[method-assign]
    requeued: list = []

    async def requeue(messages: list) -> bool:
        requeued.extend(messages)
        return True

    writer = _writer(graph, store, flush_seconds=0.05, requeue=requeue)
    await writer.start()
    await writer.handle(_msg("r1"))
    await asyncio.sleep(0.3)  # timer fired, flush is blocked inside the write
    assert not writer._pending
    closing = asyncio.create_task(writer.aclose())
    await asyncio.sleep(0.05)
    gate.set()
    await closing
    assert store.written() or requeued, "event taken by the timer flush was lost on shutdown"
