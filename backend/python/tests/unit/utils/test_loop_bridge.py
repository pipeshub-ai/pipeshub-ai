"""Unit tests for app.utils.loop_bridge.run_on_loop."""

from __future__ import annotations

import asyncio
import threading

import pytest

from app.utils.loop_bridge import run_on_loop


async def _simple(value: int) -> int:
    return value


class TestRunOnLoop:
    async def test_none_loop_awaits_on_current(self) -> None:
        result = await run_on_loop(None, _simple(7))
        assert result == 7

    async def test_same_loop_awaits_on_current(self) -> None:
        loop = asyncio.get_running_loop()
        result = await run_on_loop(loop, _simple(11))
        assert result == 11

    async def test_stopped_loop_raises_and_closes_coro(self) -> None:
        other_loop = asyncio.new_event_loop()
        coro = _simple(99)
        try:
            with pytest.raises(
                RuntimeError,
                match="The event loop that owns this client is not running",
            ):
                await run_on_loop(other_loop, coro)
            assert coro.cr_frame is None
        finally:
            other_loop.close()

    async def test_cross_thread_running_loop(self) -> None:
        loop_ready = threading.Event()
        loop_box: list[asyncio.AbstractEventLoop] = []

        def thread_main() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop_box.append(loop)
            loop_ready.set()
            loop.run_forever()
            loop.close()

        thread = threading.Thread(target=thread_main, daemon=True)
        thread.start()
        assert loop_ready.wait(timeout=2.0)
        other_loop = loop_box[0]

        try:
            result = await run_on_loop(other_loop, _simple(123))
            assert result == 123
        finally:
            other_loop.call_soon_threadsafe(other_loop.stop)
            thread.join(timeout=2.0)

    async def test_timeout_shields_other_loop_coro(self) -> None:
        """Caller timeout must not cancel the coro running on the other loop.

        Completion is signaled with an asyncio.Event created on the other loop.
        """
        loop_ready = threading.Event()
        loop_box: list[asyncio.AbstractEventLoop] = []

        def thread_main() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop_box.append(loop)
            loop_ready.set()
            loop.run_forever()
            loop.close()

        thread = threading.Thread(target=thread_main, daemon=True)
        thread.start()
        assert loop_ready.wait(timeout=2.0)
        other_loop = loop_box[0]

        # Create the Event on the other loop.
        event_future = asyncio.run_coroutine_threadsafe(_make_event(), other_loop)
        done_event: asyncio.Event = event_future.result(timeout=2.0)

        async def work() -> str:
            await asyncio.sleep(0.15)
            done_event.set()
            return "finished"

        try:
            with pytest.raises((TimeoutError, asyncio.TimeoutError)):
                await run_on_loop(other_loop, work(), timeout=0.03)

            # Confirm the other-loop coroutine still ran to completion.
            wait_future = asyncio.run_coroutine_threadsafe(done_event.wait(), other_loop)
            assert wait_future.result(timeout=2.0) is True
        finally:
            other_loop.call_soon_threadsafe(other_loop.stop)
            thread.join(timeout=2.0)


async def _make_event() -> asyncio.Event:
    return asyncio.Event()
