import asyncio
import threading
import time
from unittest.mock import MagicMock

import pytest

from app.connectors.core.thread_pool import (
    SharedConnectorThreadPool,
    acquire_connector_lease,
    get_shared_connector_thread_pool,
)


def test_cap_enforced_under_over_submission() -> None:
    """A lease never occupies more of the pool than its cap, however wide it fans out."""
    pool = SharedConnectorThreadPool(max_workers=8, thread_name_prefix="test-cap")
    lease = pool.lease(max_concurrency=2, label="cap")
    counter_lock = threading.Lock()
    current = 0
    high_water = 0

    def work() -> None:
        nonlocal current, high_water
        with counter_lock:
            current += 1
            high_water = max(high_water, current)
        time.sleep(0.05)
        with counter_lock:
            current -= 1

    try:
        futures = [lease.submit(work) for _ in range(20)]
        for future in futures:
            future.result(timeout=20)
    finally:
        pool.shutdown(wait=True)

    assert high_water == 2
    assert lease.inflight == 0
    assert lease.queued == 0


def test_submit_does_not_block_caller() -> None:
    """Over-cap work queues in the lease instead of blocking the submitting thread.

    Also exercises the dispatch trampoline: 200 fast calls drain through
    _finish -> _start in one chain once the blocker releases.
    """
    pool = SharedConnectorThreadPool(max_workers=4, thread_name_prefix="test-nonblock")
    lease = pool.lease(max_concurrency=1, label="nonblock")
    release = threading.Event()

    try:
        blocker = lease.submit(release.wait, 20)
        start = time.monotonic()
        queued = [lease.submit(int) for _ in range(200)]
        elapsed = time.monotonic() - start

        # A blocking implementation would have waited on the blocker for 20s.
        assert elapsed < 1.0
        assert not any(future.done() for future in queued)
        assert lease.queued == 200

        release.set()
        assert blocker.result(timeout=20) is True
        for future in queued:
            assert future.result(timeout=20) == 0
    finally:
        release.set()
        pool.shutdown(wait=True)


async def test_drain_does_not_shut_down_shared_pool() -> None:
    """Draining one connector's lease must not disturb any other connector."""
    pool = SharedConnectorThreadPool(max_workers=4, thread_name_prefix="test-drain")
    lease_a = pool.lease(max_concurrency=2, label="a")
    lease_b = pool.lease(max_concurrency=2, label="b")

    try:
        await lease_a.shutdown_and_drain()
        assert lease_a.closed

        with pytest.raises(RuntimeError, match="is closed"):
            lease_a.submit(int)

        assert not lease_b.closed
        assert lease_b.submit(lambda: 7).result(timeout=20) == 7

        loop = asyncio.get_running_loop()
        assert await loop.run_in_executor(lease_b, lambda: 9) == 9
    finally:
        pool.shutdown(wait=True)


async def test_drain_cancels_queued_and_awaits_inflight() -> None:
    pool = SharedConnectorThreadPool(max_workers=4, thread_name_prefix="test-cancel")
    lease = pool.lease(max_concurrency=1, label="cancel")
    release = threading.Event()

    try:
        blocker = lease.submit(release.wait, 20)
        queued = [lease.submit(int) for _ in range(3)]

        drain = asyncio.create_task(lease.shutdown_and_drain(timeout=20))
        await asyncio.sleep(0)  # let _close() run before the blocker is released
        release.set()
        await drain

        assert all(future.cancelled() for future in queued)
        assert blocker.result(timeout=20) is True
    finally:
        release.set()
        pool.shutdown(wait=True)


def test_lease_is_loop_agnostic() -> None:
    """One lease serves several event loops.

    The connectors process runs more than one loop (uvicorn plus the consumers),
    and the dispatch pump re-enters submit() from pool worker threads where no
    loop is running at all. An asyncio.Semaphore-based cap would fail here.
    """
    pool = SharedConnectorThreadPool(max_workers=4, thread_name_prefix="test-loops")
    lease = pool.lease(max_concurrency=2, label="loops")
    results: list[int] = []
    errors: list[BaseException] = []

    def run_on_own_loop(value: int) -> None:
        loop = asyncio.new_event_loop()
        try:
            results.append(
                loop.run_until_complete(
                    loop.run_in_executor(lease, lambda: value * 2)
                )
            )
        except BaseException as exc:  # reported back to the main thread
            errors.append(exc)
        finally:
            loop.close()

    try:
        threads = [
            threading.Thread(target=run_on_own_loop, args=(value,))
            for value in (1, 2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
    finally:
        pool.shutdown(wait=True)

    assert errors == []
    assert sorted(results) == [2, 4]


async def test_run_in_executor_propagates_exceptions() -> None:
    pool = SharedConnectorThreadPool(max_workers=2, thread_name_prefix="test-exc")
    lease = pool.lease(max_concurrency=1, label="exc")

    def boom() -> None:
        raise ValueError("upstream blew up")

    try:
        loop = asyncio.get_running_loop()
        with pytest.raises(ValueError, match="upstream blew up"):
            await loop.run_in_executor(lease, boom)
    finally:
        pool.shutdown(wait=True)


def test_no_injection_falls_back_to_shared_thread_pool() -> None:
    """A connector with no pool injected (mocks, direct construction) falls back
    to the process-wide shared thread pool from get_shared_connector_thread_pool().
    """
    lease = acquire_connector_lease(MagicMock(), 2, label="fallback")
    assert lease.submit(lambda: "ok").result(timeout=20) == "ok"
    assert lease._pool is get_shared_connector_thread_pool()
