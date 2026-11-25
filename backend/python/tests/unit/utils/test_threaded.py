"""`to_thread_settled`: a cancelled caller waits for its thread, and the thread's outcome is always read,
so a failure after the cancel never logs "Task exception was never retrieved".

Thread ordering uses `threading.Event`; no test depends on the clock.
"""

from __future__ import annotations

import asyncio
import gc
import threading
from typing import TYPE_CHECKING

import pytest

from app.utils.threaded import to_thread_settled

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

_WAIT = 10


@pytest.fixture
async def loop_errors() -> AsyncIterator[list[str]]:
    """What the loop's exception handler reports, e.g. "Task exception was never retrieved"."""
    errors: list[str] = []
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(lambda _loop, ctx: errors.append(ctx["message"]))
    yield errors
    loop.set_exception_handler(None)


async def _collected() -> None:
    for _ in range(5):
        await asyncio.sleep(0)
    gc.collect()
    await asyncio.sleep(0)


async def _started(
    fn: Callable[[], None], started: threading.Event
) -> asyncio.Future[None]:
    caller = asyncio.ensure_future(to_thread_settled(fn))
    await asyncio.to_thread(started.wait, _WAIT)
    return caller


async def test_a_cancelled_caller_whose_thread_then_fails_leaves_nothing_unretrieved(
    loop_errors: list[str],
) -> None:
    started, go = threading.Event(), threading.Event()

    def sdk_call() -> None:
        started.set()
        go.wait(_WAIT)
        raise RuntimeError("throttled after the caller gave up")

    caller = await _started(sdk_call, started)
    caller.cancel()
    await asyncio.sleep(0)
    go.set()

    with pytest.raises(asyncio.CancelledError):
        await caller
    await _collected()

    assert loop_errors == []


async def test_a_cancelled_caller_is_handed_back_only_after_its_thread_ends() -> None:
    started, go, finished = threading.Event(), threading.Event(), threading.Event()

    def sdk_call() -> None:
        started.set()
        go.wait(_WAIT)
        finished.set()

    caller = await _started(sdk_call, started)
    caller.cancel()
    for _ in range(3):
        await asyncio.sleep(0)
    assert not caller.done()

    go.set()
    with pytest.raises(asyncio.CancelledError):
        await caller

    assert finished.is_set()


async def test_a_thread_failure_reaches_a_caller_that_was_not_cancelled(
    loop_errors: list[str],
) -> None:
    def sdk_call() -> None:
        raise RuntimeError("throttled")

    with pytest.raises(RuntimeError, match="throttled"):
        await to_thread_settled(sdk_call)
    await _collected()

    assert loop_errors == []


async def test_the_thread_result_is_returned() -> None:
    assert await to_thread_settled(lambda: 42) == 42


async def test_a_thread_task_cancelled_at_shutdown_reports_nothing(
    loop_errors: list[str],
) -> None:
    started, go = threading.Event(), threading.Event()

    def sdk_call() -> None:
        started.set()
        go.wait(_WAIT)

    caller = await _started(sdk_call, started)
    (inner,) = [
        t for t in asyncio.all_tasks() if t.get_name() == "image_embedding_thread"
    ]
    inner.cancel()  # what asyncio.run does to every task at shutdown

    with pytest.raises(asyncio.CancelledError):
        await caller
    go.set()
    await _collected()

    assert loop_errors == []


async def test_a_twice_cancelled_caller_whose_thread_then_fails_leaves_nothing_unretrieved(
    loop_errors: list[str],
) -> None:
    started, go = threading.Event(), threading.Event()

    def sdk_call() -> None:
        started.set()
        go.wait(_WAIT)
        raise RuntimeError("throttled after the caller gave up")

    caller = await _started(sdk_call, started)
    (inner,) = [
        t for t in asyncio.all_tasks() if t.get_name() == "image_embedding_thread"
    ]
    caller.cancel()
    for _ in range(3):
        await asyncio.sleep(0)
    caller.cancel()  # a second cancel leaves the wait early (F61b)
    with pytest.raises(asyncio.CancelledError):
        await caller
    go.set()
    await asyncio.wait({inner})
    del inner  # let the finished task be collected
    await _collected()

    assert loop_errors == []
