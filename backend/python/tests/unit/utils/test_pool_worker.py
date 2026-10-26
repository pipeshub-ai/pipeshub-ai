"""Process pool worker housekeeping: trimming after a task, exiting with the parent."""
from __future__ import annotations

import select
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from app.utils import pool_worker


def test_run_and_release_flags_the_task_for_a_trim_even_when_it_fails() -> None:
    pool_worker._task_ended.clear()
    assert pool_worker.run_and_release(sum, [1, 2]) == 3
    assert pool_worker._task_ended.is_set()

    pool_worker._task_ended.clear()
    with pytest.raises(ZeroDivisionError):
        pool_worker.run_and_release(divmod, 1, 0)
    assert pool_worker._task_ended.is_set()


def test_housekeeping_trims_twice_after_a_task_and_exits_once_orphaned() -> None:
    pool_worker._task_ended.set()
    with patch.object(pool_worker.os, "getppid", side_effect=[1, 1, 1, 1, 2]), \
            patch.object(pool_worker.time, "sleep"), \
            patch.object(pool_worker, "release_freed_memory") as trim, \
            patch.object(pool_worker.os, "_exit") as exit_, \
            patch.object(pool_worker.threading, "Thread") as thread:
        pool_worker.start_housekeeping(1)
        thread.call_args.kwargs["target"]()

    # Twice for the one task, then no more while idle.
    assert trim.call_count == 2
    exit_.assert_called_once_with(0)


def _alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")
def test_docling_pool_workers_exit_when_their_parent_is_killed() -> None:
    code = (
        "import os, time\n"
        "from app.modules.parsers.pdf import docling_processor as d\n"
        "from app.utils.pool_worker import run_and_release\n"
        "print(d._get_process_pool().submit(run_and_release, os.getpid).result(), flush=True)\n"
        "time.sleep(600)\n"
    )
    parent = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=Path(pool_worker.__file__).parents[2],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        ready, _, _ = select.select([parent.stdout], [], [], 25)
        assert ready, "the pool never reported a worker pid"
        worker_pid = int(parent.stdout.readline())
        assert _alive(worker_pid)
    finally:
        parent.kill()
        parent.wait()
        parent.stdout.close()

    deadline = time.monotonic() + 10
    while _alive(worker_pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not _alive(worker_pid)
