"""`wait_owned`: `asyncio.wait` for tasks the caller owns. When the caller is cancelled, each
task gets exactly one cancel and is left to finish its cleanup; that is what lets httpx close a
provider connection after a client disconnect (which cancels every layer of a turn, repeatedly).

Every test steps the loop with events and `asyncio.sleep(0)`; none depends on the clock.
"""

from __future__ import annotations

import asyncio
import gc
from typing import TYPE_CHECKING

import pytest

from app.agent_loop_lib.core.tasks import wait_owned
from tests.unit.agents.adapter.support.provider_stall import ProviderStall

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest.fixture
async def loop_errors() -> AsyncIterator[list[str]]:
    """What the loop's exception handler reports, e.g. "exception was never retrieved"."""
    errors: list[str] = []
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(lambda _loop, ctx: errors.append(ctx["message"]))
    yield errors
    loop.set_exception_handler(None)


async def _until_retrieved() -> None:
    for _ in range(5):
        await asyncio.sleep(0)
    gc.collect()
    await asyncio.sleep(0)


class TestNotCancelled:
    async def test_first_completed_returns_what_asyncio_wait_returns_and_leaves_the_rest_running(self) -> None:
        go = asyncio.Event()
        fast = asyncio.ensure_future(go.wait())
        slow = asyncio.ensure_future(asyncio.Event().wait())
        go.set()

        done, pending = await wait_owned({fast, slow}, return_when=asyncio.FIRST_COMPLETED)

        assert (done, pending) == ({fast}, {slow})
        assert not slow.cancelled()
        slow.cancel()

    async def test_first_exception_returns_the_failed_task(self) -> None:
        async def boom() -> None:
            raise ValueError("model call failed")

        failed = asyncio.ensure_future(boom())
        slow = asyncio.ensure_future(asyncio.Event().wait())

        done, pending = await wait_owned({failed, slow}, return_when=asyncio.FIRST_EXCEPTION)

        assert (done, pending) == ({failed}, {slow})
        assert isinstance(failed.exception(), ValueError)
        assert not slow.cancelled()
        slow.cancel()


class TestCallerCancelled:
    async def test_the_cancel_is_re_raised_with_every_owned_task_cancelled_and_done(self) -> None:
        first, second = ProviderStall(), ProviderStall()
        owned = {asyncio.ensure_future(first.read()), asyncio.ensure_future(second.read())}
        caller = asyncio.create_task(wait_owned(owned))
        await first.started.wait()
        await second.started.wait()

        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller

        assert all(task.done() and task.cancelled() for task in owned)
        assert (first.closed, second.closed) == (True, True)

    async def test_three_cancels_one_tick_apart_reach_the_owned_task_once(self) -> None:
        """The disconnect cascade: each layer re-cancels the caller. Forwarding every one of them
        (a plain `gather`) cuts the owned task's close short."""
        stall = ProviderStall()
        caller = asyncio.create_task(wait_owned({asyncio.ensure_future(stall.read())}))
        await stall.started.wait()

        for _ in range(3):
            caller.cancel()
            await asyncio.sleep(0)
        with pytest.raises(asyncio.CancelledError):
            await caller
        await stall.settle()

        assert stall.task is not None and stall.task.cancelled()
        assert (stall.cancels, stall.closed) == (1, True)

    async def test_a_cancel_re_delivered_during_cleanup_releases_the_caller_at_once(
        self, loop_errors: list[str],
    ) -> None:
        cleanup_running, finish_cleanup = asyncio.Event(), asyncio.Event()

        async def slow_close() -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cleanup_running.set()
                await finish_cleanup.wait()
                raise

        owned = asyncio.ensure_future(slow_close())
        caller = asyncio.create_task(wait_owned({owned}))
        await asyncio.sleep(0)
        caller.cancel()
        await cleanup_running.wait()

        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller

        assert not owned.done()
        finish_cleanup.set()
        await asyncio.gather(owned, return_exceptions=True)
        assert owned.cancelled()
        await _until_retrieved()
        assert loop_errors == []

    async def test_a_task_failing_in_its_cleanup_leaves_no_unretrieved_exception(
        self, loop_errors: list[str],
    ) -> None:
        async def bad_close() -> None:
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                raise ValueError("close failed")

        owned = asyncio.ensure_future(bad_close())
        caller = asyncio.create_task(wait_owned({owned}))
        await asyncio.sleep(0)
        caller.cancel()
        await asyncio.sleep(0)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        del owned
        await _until_retrieved()

        assert loop_errors == []

    async def test_tasks_already_done_when_the_cancel_lands_re_raise_it(self, loop_errors: list[str]) -> None:
        go = asyncio.Event()

        async def quick() -> None:
            await go.wait()
            raise ValueError("finished in the same tick")

        owned = asyncio.ensure_future(quick())
        caller = asyncio.create_task(wait_owned({owned}, return_when=asyncio.FIRST_COMPLETED))
        await asyncio.sleep(0)
        go.set()
        await asyncio.sleep(0)
        assert owned.done()

        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        await _until_retrieved()

        assert loop_errors == []

    async def test_a_timeout_around_it_raises_timeout_error_with_the_owned_task_cancelled(self) -> None:
        stall = ProviderStall()
        owned = asyncio.ensure_future(stall.read())

        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0):
                await wait_owned({owned})

        assert owned.cancelled()
        assert (stall.cancels, stall.closed) == (1, True)
        assert asyncio.current_task().cancelling() == 0
