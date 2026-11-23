"""A paid result is kept once its call has returned, whoever is cancelled meanwhile; the deadlines are enforced."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.paid_cache import cache as cache_module
from app.services.paid_cache import locks as locks_module
from app.services.paid_cache.cache import PaidResultCache, _Held
from app.services.paid_cache.keys import CacheKey
from app.services.paid_cache.locks import InProcessLocks
from tests.support.paid_cache import MemoryResultStore

KEY = CacheKey("org-1", "rec-1", "ocr_page", 1, "m", "p", "q", "i")


def _heartbeats() -> list[asyncio.Task]:
    return [t for t in asyncio.all_tasks() if t.get_name() == "paid-cache-lock-renewal" and not t.done()]


async def _compute() -> str:
    return "text"


async def test_a_caller_cancelled_while_the_heartbeat_stops_still_stores_the_paid_result(monkeypatch: pytest.MonkeyPatch) -> None:
    store, locks = MemoryResultStore(), InProcessLocks()
    cache = PaidResultCache(store, locks)
    stopping, gate = asyncio.Event(), asyncio.Event()
    real_stop = _Held.stop_heartbeat

    async def slow_stop(self: _Held) -> None:
        stopping.set()
        await gate.wait()
        await real_stop(self)

    monkeypatch.setattr(_Held, "stop_heartbeat", slow_stop)
    caller = asyncio.ensure_future(cache.get_or_compute(KEY, _compute))
    await stopping.wait()                       # the model has answered; the lock's timer is being stopped
    caller.cancel()                             # a sibling's outage cancels this caller's batch
    await asyncio.sleep(0)
    gate.set()

    with pytest.raises(asyncio.CancelledError):  # the cancellation is let through ...
        await caller
    assert len(store.results("org-1", "rec-1")) == 1   # ... after the paid result was stored
    assert _heartbeats() == []
    assert await locks.acquire(f"pc:lock:{{org-1:rec-1}}:{KEY.digest()}", 1000) is not None  # and the lock was released


async def test_a_caller_cancelled_during_the_store_still_stores_the_paid_result() -> None:
    store, locks = MemoryResultStore(), InProcessLocks()
    real_put = store.put
    putting, gate = asyncio.Event(), asyncio.Event()

    async def slow_put(*args: Any) -> bool:  # noqa: ANN401
        putting.set()
        await gate.wait()
        return await real_put(*args)

    store.put = slow_put  # type: ignore[method-assign]
    cache = PaidResultCache(store, locks)
    caller = asyncio.ensure_future(cache.get_or_compute(KEY, _compute))
    await putting.wait()
    caller.cancel()
    await asyncio.sleep(0)
    gate.set()

    with pytest.raises(asyncio.CancelledError):
        await caller
    assert len(store.results("org-1", "rec-1")) == 1


async def test_a_caller_cancelled_during_the_call_stops_the_timer_and_stores_nothing() -> None:
    store, locks = MemoryResultStore(), InProcessLocks()
    cache = PaidResultCache(store, locks)
    started = asyncio.Event()

    async def hung() -> str:
        started.set()
        await asyncio.sleep(60)
        return "never"

    caller = asyncio.ensure_future(cache.get_or_compute(KEY, hung))
    await started.wait()
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller

    assert store.results("org-1", "rec-1") == []
    assert _heartbeats() == []


async def test_a_write_slower_than_the_write_deadline_is_abandoned(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(locks_module, "WRITE_DEADLINE_S", 0.05)
    store, locks = MemoryResultStore(), InProcessLocks()
    real_put = store.put

    async def slow_put(*args: Any) -> bool:  # noqa: ANN401
        await asyncio.sleep(0.5)
        return await real_put(*args)

    store.put = slow_put  # type: ignore[method-assign]
    cache = PaidResultCache(store, locks)

    with caplog.at_level(logging.WARNING, logger=cache.logger.name):
        assert await cache.get_or_compute(KEY, _compute) == "text"   # the caller still gets its result

    assert store.results("org-1", "rec-1") == []
    assert len([r for r in caplog.records if "did not finish" in r.getMessage()]) == 1


async def test_a_result_computed_for_longer_than_the_stage_deadline_is_not_stored_and_logged_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    clock = [1000.0]
    monkeypatch.setattr(cache_module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    store, locks = MemoryResultStore(), InProcessLocks()
    cache = PaidResultCache(store, locks)

    async def slow() -> str:
        clock[0] += locks_module.stage_deadline_s() + 1      # the call ran past the stage's deadline
        return "text"

    with caplog.at_level(logging.WARNING, logger=cache.logger.name):
        assert await cache.get_or_compute(KEY, slow) == "text"

    assert store.results("org-1", "rec-1") == []
    assert len([r for r in caplog.records if "stage deadline" in r.getMessage()]) == 1


async def test_a_result_inside_the_stage_deadline_is_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [1000.0]
    monkeypatch.setattr(cache_module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    store = MemoryResultStore()

    async def fine() -> str:
        clock[0] += locks_module.stage_deadline_s() - 1
        return "text"

    await PaidResultCache(store, InProcessLocks()).get_or_compute(KEY, fine)

    assert len(store.results("org-1", "rec-1")) == 1
