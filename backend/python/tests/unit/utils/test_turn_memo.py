from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from app.utils import turn_memo

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


@pytest.mark.asyncio
async def test_concurrent_callers_in_a_turn_share_one_call() -> None:
    calls = 0

    async def compute() -> dict:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return {"n": calls}

    async def turn() -> list:
        turn_memo.begin_turn()
        return await asyncio.gather(*(turn_memo.memo("k", compute) for _ in range(5)))

    results = await asyncio.create_task(turn())
    assert calls == 1
    assert all(r == {"n": 1} for r in results)


@pytest.mark.asyncio
async def test_nothing_survives_into_the_next_turn() -> None:
    calls = 0

    async def compute() -> int:
        nonlocal calls
        calls += 1
        return calls

    async def turn() -> int:
        turn_memo.begin_turn()
        return await turn_memo.memo("k", compute)

    assert await asyncio.create_task(turn()) == 1
    assert await asyncio.create_task(turn()) == 2


@pytest.mark.asyncio
async def test_a_failed_call_is_retried_not_cached() -> None:
    attempts = 0

    async def compute() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("graph down")
        return "ok"

    async def turn() -> str:
        turn_memo.begin_turn()
        with pytest.raises(RuntimeError):
            await turn_memo.memo("k", compute)
        return await turn_memo.memo("k", compute)

    assert await asyncio.create_task(turn()) == "ok"
    assert attempts == 2


@pytest.mark.asyncio
async def test_outside_a_turn_every_call_computes() -> None:
    calls = 0

    async def compute() -> int:
        nonlocal calls
        calls += 1
        return calls

    async def no_turn() -> list:
        return [await turn_memo.memo("k", compute), await turn_memo.memo("k", compute)]

    assert await asyncio.create_task(no_turn()) == [1, 2]


@pytest.mark.asyncio
async def test_ending_the_turn_stops_a_lookup_still_running() -> None:
    started = asyncio.Event()
    cancelled = False

    async def compute() -> None:
        nonlocal cancelled
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled = True
            raise

    async def turn() -> None:
        lookups = turn_memo.begin_turn()
        waiter = asyncio.ensure_future(turn_memo.memo("k", compute))
        await started.wait()
        waiter.cancel()
        await turn_memo.end_turn(lookups)

    await asyncio.create_task(turn())
    assert cancelled


@pytest.mark.asyncio
async def test_closing_the_stream_early_stops_lookups_after_the_inner_stream_ends() -> None:
    order: list[str] = []
    started = asyncio.Event()

    async def compute() -> None:
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            order.append("lookup cancelled")
            raise

    @turn_memo.per_turn_lookups
    async def stream() -> AsyncGenerator[str, None]:
        try:
            lookup = asyncio.ensure_future(turn_memo.memo("k", compute))
            await started.wait()
            lookup.cancel()
            yield "first"
            yield "never reached"
        finally:
            order.append("inner finished")

    chunks = stream()
    assert await chunks.__anext__() == "first"
    await chunks.aclose()
    assert order == ["inner finished", "lookup cancelled"]
