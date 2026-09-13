"""CrossLoopSemaphore: one cap across event loops, no permit lost or duplicated on cancellation."""

import asyncio
import threading
from contextlib import suppress

import pytest

from app.services.llm_gateway.limiter import CrossLoopSemaphore


def test_the_cap_holds_across_two_event_loops() -> None:
    sem = CrossLoopSemaphore(3)
    lock = threading.Lock()
    running, peak, done = 0, 0, 0

    async def work() -> None:
        nonlocal running, peak, done
        async with sem.slot():
            with lock:
                running += 1
                peak = max(peak, running)
            await asyncio.sleep(0.002)
            with lock:
                running -= 1
                done += 1

    async def burst() -> None:
        await asyncio.gather(*(work() for _ in range(100)))

    threads = [threading.Thread(target=asyncio.run, args=(burst(),)) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert done == 200
    assert peak <= 3
    assert sem.in_use == 0 and sem.waiting == 0


@pytest.mark.asyncio
async def test_waiters_are_served_in_arrival_order() -> None:
    sem = CrossLoopSemaphore(1)
    await sem.acquire()
    order: list[int] = []

    async def wait(n: int) -> None:
        async with sem.slot():
            order.append(n)

    tasks = [asyncio.create_task(wait(n)) for n in range(5)]
    await asyncio.sleep(0)
    sem.release()
    await asyncio.gather(*tasks)
    assert order == [0, 1, 2, 3, 4]


@pytest.mark.asyncio
async def test_a_cancelled_waiter_leaves_no_trace() -> None:
    sem = CrossLoopSemaphore(1)
    await sem.acquire()
    waiter = asyncio.create_task(sem.acquire())
    await asyncio.sleep(0)
    waiter.cancel()
    with suppress(asyncio.CancelledError):
        await waiter
    assert sem.waiting == 0
    sem.release()
    await asyncio.wait_for(sem.acquire(), 1)
    assert sem.in_use == 1


@pytest.mark.asyncio
async def test_a_permit_handed_to_a_waiter_cancelled_meanwhile_is_passed_on() -> None:
    sem = CrossLoopSemaphore(1)
    await sem.acquire()
    waiter = asyncio.create_task(sem.acquire())
    await asyncio.sleep(0)
    sem.release()  # hands the permit to the waiter; the grant has not run yet
    waiter.cancel()
    with suppress(asyncio.CancelledError):
        await waiter
    await asyncio.sleep(0)
    assert sem.in_use == 0
    await asyncio.wait_for(sem.acquire(), 1)


def test_releasing_more_than_was_acquired_is_refused() -> None:
    sem = CrossLoopSemaphore(2)
    with pytest.raises(ValueError, match="more permits"):
        sem.release()


def test_at_least_one_permit() -> None:
    with pytest.raises(ValueError):
        CrossLoopSemaphore(0)
