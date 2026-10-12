"""Tests for dispatching coroutines to the loop that owns their client."""

from __future__ import annotations

import asyncio

import pytest

from app.utils.loop_bridge import run_on_loop
from tests.support.loop_topology import on_loop, worker_loop


async def _answer() -> str:
    return "answer"


class TestRunOnLoop:
    async def test_runs_on_the_current_loop_when_no_owner_is_given(self) -> None:
        assert await run_on_loop(None, _answer()) == "answer"

    async def test_runs_on_the_current_loop_when_it_is_the_owner(self) -> None:
        loop = asyncio.get_running_loop()

        assert await run_on_loop(loop, _answer()) == "answer"

    async def test_closes_the_coroutine_for_a_stopped_owner_loop(self) -> None:
        loop = asyncio.new_event_loop()
        coroutine = _answer()
        try:
            with pytest.raises(
                RuntimeError,
                match="The event loop that owns this client is not running",
            ):
                await run_on_loop(loop, coroutine)

            assert coroutine.cr_frame is None
        finally:
            loop.close()

    async def test_returns_a_result_from_a_different_running_loop(self) -> None:
        with worker_loop() as worker:
            assert await run_on_loop(worker, _answer()) == "answer"

    async def test_timeout_does_not_cancel_work_on_the_owner_loop(self) -> None:
        with worker_loop() as worker:

            async def make_events() -> tuple[asyncio.Event, asyncio.Event, asyncio.Event]:
                return asyncio.Event(), asyncio.Event(), asyncio.Event()

            release, started, finished = await on_loop(worker, make_events())

            async def work() -> None:
                started.set()
                await release.wait()
                finished.set()

            with pytest.raises(TimeoutError):
                await run_on_loop(worker, work(), timeout=0.01)

            await asyncio.wait_for(on_loop(worker, started.wait()), timeout=1)
            worker.call_soon_threadsafe(release.set)
            await asyncio.wait_for(on_loop(worker, finished.wait()), timeout=1)
