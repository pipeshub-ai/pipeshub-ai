import asyncio
import threading
import time

from app.connectors.core.thread_pool import SharedConnectorThreadPool


def test_cap_enforced_under_over_submission() -> None:
    """A lease never occupies more of the pool than its cap, however wide it fans out."""
    pool = SharedConnectorThreadPool(max_workers=8, thread_name_prefix="test-cap")
    lease = pool.lease(max_concurrency=2, label="cap", connector_type="TEST")
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


async def test_drain_cancels_queued_and_awaits_inflight() -> None:
    pool = SharedConnectorThreadPool(max_workers=4, thread_name_prefix="test-cancel")
    lease = pool.lease(max_concurrency=1, label="cancel", connector_type="TEST")
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
        assert lease.closed
        assert lease.inflight == 0
        assert lease.queued == 0
    finally:
        release.set()
        pool.shutdown(wait=True)
