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


def kill_worker_loop(worker: WorkerLoop, error: BaseException | None = None) -> None:
    """Exit the loop from inside, with no stop() request, and wait for its thread."""
    loop, thread = worker.loop, worker._thread
    assert loop is not None and thread is not None

    def die() -> None:
        if error is None:
            loop.stop()
        else:
            raise error

    loop.call_soon_threadsafe(die)
    thread.join(5)
    assert loop.is_closed()


# The loop's thread re-raises what killed it, as an unhandled thread exception.
_DIES = pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")


@_DIES
def test_an_unrequested_exit_is_recorded_logged_and_stops_the_listeners(caplog) -> None:
    """Left running, consumers hand each message to the closed loop and re-claim the same
    entries until the delivery backstop dead-letters healthy records."""
    worker = WorkerLoop(_LOGGER)
    called_on: list[str] = []
    worker.add_exit_listener(lambda: called_on.append(threading.current_thread().name))
    worker.start()
    error = SystemExit("boom")

    with caplog.at_level(logging.CRITICAL, logger=_LOGGER.name):
        kill_worker_loop(worker, error)

    assert worker.failure is error
    assert called_on == ["indexing-worker"]
    critical = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert len(critical) == 1
    assert critical[0].exc_info is not None and critical[0].exc_info[1] is error


def test_a_loop_stopped_from_inside_is_a_failure_and_skips_removed_listeners() -> None:
    worker = WorkerLoop(_LOGGER)
    kept, removed = threading.Event(), threading.Event()
    worker.add_exit_listener(kept.set)
    worker.add_exit_listener(removed.set)
    worker.remove_exit_listener(removed.set)
    worker.start()

    kill_worker_loop(worker)

    assert isinstance(worker.failure, RuntimeError)
    assert kept.is_set() and not removed.is_set()


def test_a_requested_stop_is_not_a_failure(caplog) -> None:
    worker = WorkerLoop(_LOGGER)
    called = threading.Event()
    worker.add_exit_listener(called.set)
    worker.start()

    with caplog.at_level(logging.CRITICAL, logger=_LOGGER.name):
        worker.stop()

    assert worker.failure is None
    assert not called.is_set()
    assert not [r for r in caplog.records if r.levelno == logging.CRITICAL]


@_DIES
def test_a_dead_loop_is_not_replaced() -> None:
    """A consumer starting late must not run on a second loop and hide the failure."""
    worker = WorkerLoop(_LOGGER)
    worker.start()
    kill_worker_loop(worker, SystemExit())

    with pytest.raises(RuntimeError, match="died"):
        worker.start()
    worker.stop()


def test_a_loop_exiting_on_its_own_is_not_replaced_before_its_exit_is_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Between run_forever() returning and the failure being recorded, a start() must not open
    a second loop: it would hold the lock the exiting thread needs to record the failure."""
    worker = WorkerLoop(_LOGGER)
    loop = worker.start()
    thread = worker._thread
    assert thread is not None
    exiting, release = threading.Event(), threading.Event()
    record_exit = worker._on_exit

    def held_exit(error: BaseException | None) -> None:
        exiting.set()
        release.wait(5)
        record_exit(error)

    monkeypatch.setattr(worker, "_on_exit", held_exit)
    loop.call_soon_threadsafe(loop.stop)
    assert exiting.wait(5)
    try:
        with pytest.raises(RuntimeError, match="still shutting down"):
            worker.start()
    finally:
        release.set()
    thread.join(5)

    assert isinstance(worker.failure, RuntimeError)
    with pytest.raises(RuntimeError, match="died"):
        worker.start()
    worker.stop()
