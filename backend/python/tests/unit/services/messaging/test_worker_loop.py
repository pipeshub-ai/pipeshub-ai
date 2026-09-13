"""WorkerLoop: one handler loop per process, shared by every indexing consumer."""

import asyncio
import logging
import threading

import pytest

from app.services.messaging.worker_loop import WorkerLoop

_LOGGER = logging.getLogger("worker-loop-test")


def test_start_runs_one_loop_and_is_idempotent() -> None:
    worker = WorkerLoop(_LOGGER)
    try:
        loop = worker.start()
        assert loop.is_running()
        assert worker.start() is loop
    finally:
        worker.stop()


def test_call_runs_on_the_loops_thread_and_returns_its_result() -> None:
    worker = WorkerLoop(_LOGGER)
    try:
        loop = worker.start()

        def where() -> tuple[asyncio.AbstractEventLoop, str]:
            return asyncio.get_running_loop(), threading.current_thread().name

        assert worker.call(where) == (loop, "indexing-worker")
    finally:
        worker.stop()


def test_call_raises_what_the_callable_raised() -> None:
    worker = WorkerLoop(_LOGGER)
    try:
        worker.start()

        def boom() -> None:
            raise ValueError("setup failed")

        with pytest.raises(ValueError, match="setup failed"):
            worker.call(boom)
    finally:
        worker.stop()


def test_stop_cancels_what_still_runs_and_closes_the_loop() -> None:
    worker = WorkerLoop(_LOGGER)
    loop = worker.start()
    started, cancelled = threading.Event(), threading.Event()

    async def forever() -> None:
        started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    asyncio.run_coroutine_threadsafe(forever(), loop)
    # A task stopped before its first step never reaches its try block.
    assert started.wait(5)
    worker.stop()
    assert cancelled.wait(5)
    assert loop.is_closed()
    assert worker.loop is None


def test_the_loop_cannot_be_used_once_stopped() -> None:
    worker = WorkerLoop(_LOGGER)
    worker.start()
    worker.stop()
    with pytest.raises(RuntimeError, match="not running"):
        worker.call(lambda: None)
    worker.stop()  # idempotent


@pytest.mark.asyncio
async def test_aclose_does_not_block_the_calling_loop() -> None:
    worker = WorkerLoop(_LOGGER)
    loop = worker.start()
    await worker.aclose()
    assert loop.is_closed()


def test_a_loop_that_has_not_finished_stopping_cannot_be_started_again() -> None:
    """A second loop beside one still shutting down would split the shared clients."""
    worker = WorkerLoop(_LOGGER)
    loop = worker.start()
    started = threading.Event()

    async def slow_to_cancel() -> None:
        started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            await asyncio.sleep(0.5)  # cleanup that outlasts stop()'s wait
            raise

    asyncio.run_coroutine_threadsafe(slow_to_cancel(), loop)
    assert started.wait(5)
    worker.stop(timeout=0.05)
    with pytest.raises(RuntimeError, match="still shutting down"):
        worker.start()
    with pytest.raises(RuntimeError, match="not running"):
        worker.call(lambda: None)

    worker.stop(timeout=5)  # waits for the thread this time
    assert loop.is_closed() and worker.loop is None
    new_loop = worker.start()
    try:
        assert new_loop is not loop
    finally:
        worker.stop()
