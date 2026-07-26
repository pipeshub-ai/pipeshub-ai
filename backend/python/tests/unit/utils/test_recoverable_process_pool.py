"""Tests for RecoverableProcessPool — recovery from crashed worker processes."""

import logging
import os
import pathlib
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool

import pytest

from app.utils.recoverable_process_pool import RecoverableProcessPool


def _square(x: int) -> int:
    return x * x


def _kill_worker() -> None:
    # os._exit skips cleanup, like an OOM kill; this permanently breaks a
    # plain ProcessPoolExecutor.
    os._exit(1)


def _crash_once(flag_path: str) -> str:
    if not os.path.exists(flag_path):
        pathlib.Path(flag_path).write_text("crashed")
        os._exit(1)
    return "recovered"


def _raise_value_error() -> None:
    raise ValueError("boom")


@pytest.fixture
def pool() -> Iterator[RecoverableProcessPool]:
    p = RecoverableProcessPool(max_workers=1, name="test")
    yield p
    p.shutdown()


async def test_run_executes_in_pool(pool) -> None:
    assert await pool.run(_square, 4) == 16


async def test_run_recovers_after_worker_crash(pool) -> None:
    with pytest.raises(BrokenProcessPool):
        await pool.run(_kill_worker)

    assert await pool.run(_square, 3) == 9


def test_submit_and_wait_recovers_after_worker_crash(pool) -> None:
    assert pool.submit_and_wait(_square, 5) == 25

    with pytest.raises(BrokenProcessPool):
        pool.submit_and_wait(_kill_worker)

    assert pool.submit_and_wait(_square, 6) == 36


async def test_run_retries_transparently_on_transient_crash(
    pool, tmp_path: pathlib.Path
) -> None:
    flag = str(tmp_path / "crash_flag")
    assert await pool.run(_crash_once, flag) == "recovered"


async def test_task_errors_propagate_without_pool_replacement(pool) -> None:
    with pytest.raises(ValueError, match="boom"):
        await pool.run(_raise_value_error)

    executor_after_error = pool._pool
    assert await pool.run(_square, 2) == 4
    assert pool._pool is executor_after_error


def test_concurrent_callers_survive_worker_crash(
    pool, tmp_path: pathlib.Path
) -> None:
    # The crash is transient so the crasher's own retry also succeeds; only
    # the pool replacement race is under test.
    flag = str(tmp_path / "crash_flag")
    with ThreadPoolExecutor(max_workers=6) as tp:
        crash_future = tp.submit(pool.submit_and_wait, _crash_once, flag)
        square_futures = [
            tp.submit(pool.submit_and_wait, _square, i) for i in range(5)
        ]
        assert crash_future.result(timeout=120) == "recovered"
        assert [f.result(timeout=120) for f in square_futures] == [
            i * i for i in range(5)
        ]


async def test_recreation_after_worker_kill_is_logged(pool, caplog) -> None:
    # SIGKILL-style worker death (what the OOM killer does) must produce the
    # discard warning and a new executor-created log with a higher generation.
    with caplog.at_level(
        logging.INFO, logger="app.utils.recoverable_process_pool"
    ):
        with pytest.raises(BrokenProcessPool):
            await pool.run(_kill_worker)
        assert await pool.run(_square, 3) == 9

    messages = [r.message for r in caplog.records]
    assert any(
        "destroyed: worker process died abruptly" in m for m in messages
    )
    assert any(
        "aborted: worker process died mid-execution" in m for m in messages
    )
    created = [m for m in messages if "'test' executor created" in m]
    assert len(created) >= 2
    assert "generation=1" in created[0]
    assert pool._generation >= 2


async def test_shutdown_is_logged(pool, caplog) -> None:
    with caplog.at_level(
        logging.INFO, logger="app.utils.recoverable_process_pool"
    ):
        await pool.run(_square, 2)
        assert pool.shutdown() is True

    assert any(
        "'test' shut down" in r.message for r in caplog.records
    )


async def test_shutdown_lifecycle(pool) -> None:
    assert pool.shutdown() is False

    await pool.run(_square, 2)
    assert pool.shutdown() is True
    assert pool.shutdown() is False

    assert await pool.run(_square, 7) == 49
