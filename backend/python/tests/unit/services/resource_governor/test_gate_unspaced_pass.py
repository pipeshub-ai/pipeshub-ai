"""An unspaced acquire (light work) is not held behind a head that waits only for a start token."""

from __future__ import annotations

import asyncio

import pytest

from app.services.resource_governor.gate import AdmissionGate, StartRateLimiter
from app.services.resource_governor.models import Limits, Pool
from app.services.resource_governor.registry import LimitRegistry


def _gate(limit: int, clock: list[float]) -> AdmissionGate:
    pool = Pool.HEAVY_PARSE
    registry = LimitRegistry(Limits(values={p: (limit if p == pool else 1) for p in Pool}))
    return AdmissionGate(pool, registry, rate_limiter=StartRateLimiter(2.0, 1, clock=lambda: clock[0]))


@pytest.mark.asyncio
async def test_an_unspaced_acquire_passes_a_head_blocked_only_by_the_rate_limiter() -> None:
    clock = [0.0]
    gate = _gate(4, clock)
    assert await gate.acquire()  # drains the only start token
    head = asyncio.create_task(gate.acquire())  # spaced: room is free, no token
    await asyncio.sleep(0.02)
    assert gate.head_block == "rate"

    assert await asyncio.wait_for(gate.acquire(timeout=1.0, spaced=False), 3) is True
    assert gate.head_block == "rate"  # the head still waits for its token
    assert not head.done()

    clock[0] += 10.0
    assert await asyncio.wait_for(head, 3) is True


@pytest.mark.asyncio
async def test_an_unspaced_waiter_queued_behind_the_head_is_admitted_without_a_token() -> None:
    clock = [0.0]
    gate = _gate(2, clock)
    assert await gate.acquire()
    assert await gate.acquire(spaced=False)  # the pool is now full
    head = asyncio.create_task(gate.acquire())
    await asyncio.sleep(0.02)
    small = asyncio.create_task(gate.acquire(timeout=3.0, spaced=False))
    await asyncio.sleep(0.02)
    assert not small.done()
    clock[0] += 10.0  # a token is there, but room is not
    gate.release()  # room for exactly one: the head takes it with the token
    assert await asyncio.wait_for(head, 3) is True
    gate.release()
    assert await asyncio.wait_for(small, 3) is True


@pytest.mark.asyncio
async def test_an_unspaced_acquire_does_not_pass_a_head_that_waits_for_room() -> None:
    clock = [0.0]
    gate = _gate(1, clock)
    assert await gate.acquire()
    head = asyncio.create_task(gate.acquire())
    await asyncio.sleep(0.02)
    assert gate.head_block == "room"
    assert await gate.acquire(timeout=0.1, spaced=False) is False
    head.cancel()
    with pytest.raises(asyncio.CancelledError):
        await head


@pytest.mark.asyncio
async def test_a_newer_unspaced_call_queues_behind_an_older_unspaced_waiter() -> None:
    clock = [0.0]
    gate = _gate(2, clock)
    assert await gate.acquire()  # drains the start token
    assert await gate.acquire(spaced=False)  # the pool is now full
    head = asyncio.create_task(gate.acquire())  # spaced, no token: blocked on room, then on rate
    await asyncio.sleep(0.02)
    older = asyncio.create_task(gate.acquire(timeout=3.0, spaced=False))
    await asyncio.sleep(0.02)
    gate.release()  # one permit frees; the head is blocked only by the rate limiter
    await asyncio.sleep(0)  # the older waiter has not yet polled
    newer = asyncio.create_task(gate.acquire(timeout=3.0, spaced=False))
    done, _ = await asyncio.wait({older, newer}, return_when=asyncio.FIRST_COMPLETED, timeout=3.0)
    assert older in done and newer not in done
    clock[0] += 10.0
    gate.release()
    assert await asyncio.wait_for(head, 3) is True
    gate.release()
    assert await asyncio.wait_for(newer, 3) is True
