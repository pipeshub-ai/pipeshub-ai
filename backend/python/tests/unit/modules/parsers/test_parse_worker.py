"""The parse worker process on its own: what it does between jobs and when its parent dies."""
from __future__ import annotations

import os
import pickle
import select
import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from app.modules.parsers import parse_worker


def _alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False


def test_trims_the_heap_after_each_job() -> None:
    request_r, request_w = os.pipe()
    reply_r, reply_w = os.pipe()
    with os.fdopen(request_w, "wb") as requests:
        parse_worker.write_frame(requests, (sum, ([1, 2],)))
        parse_worker.write_frame(requests, (sum, ([3, 4],)))
    # main() ignores SIGINT for good; pytest's own handler must survive it.
    with patch.object(parse_worker, "release_freed_memory") as trim, \
            patch.object(parse_worker.threading, "Thread"), \
            patch.object(parse_worker.signal, "signal"):
        parse_worker.main(request_r, reply_w)

    with os.fdopen(reply_r, "rb") as replies:
        parse_worker.read_frame(replies)  # the worker's pid
        assert [pickle.loads(parse_worker.read_frame(replies))[1] for _ in range(2)] == [3, 7]
    assert trim.call_count == 2


def test_exits_once_its_parent_is_gone() -> None:
    with patch.object(parse_worker.os, "getppid", side_effect=[10, 10, 1]), \
            patch.object(parse_worker.time, "sleep"), \
            patch.object(parse_worker.os, "getpgrp", return_value=-1), \
            patch.object(parse_worker.os, "_exit") as exit_:
        parse_worker._exit_with_parent(10)
    exit_.assert_called_once_with(0)


def test_takes_its_process_group_along_when_it_leads_one() -> None:
    with patch.object(parse_worker.os, "getppid", side_effect=[10, 1]), \
            patch.object(parse_worker.time, "sleep"), \
            patch.object(parse_worker.os, "getpgrp", return_value=4242), \
            patch.object(parse_worker.os, "getpid", return_value=4242), \
            patch.object(parse_worker.os, "killpg") as killpg, \
            patch.object(parse_worker.os, "_exit"):
        parse_worker._exit_with_parent(10)
    killpg.assert_called_once_with(4242, parse_worker.signal.SIGKILL)


def test_leaves_a_group_it_does_not_lead_alone() -> None:
    with patch.object(parse_worker.os, "getppid", side_effect=[10, 1]), \
            patch.object(parse_worker.time, "sleep"), \
            patch.object(parse_worker.os, "getpgrp", return_value=1), \
            patch.object(parse_worker.os, "killpg") as killpg, \
            patch.object(parse_worker.os, "_exit"):
        parse_worker._exit_with_parent(10)
    killpg.assert_not_called()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")
def test_a_worker_mid_job_exits_when_its_parent_is_killed() -> None:
    # The parent starts a worker, hands it a job that would run for ten
    # minutes, and is SIGKILLed while the worker is busy with it.
    code = (
        "import os, pickle, subprocess, sys, time\n"
        "from app.modules.parsers.parse_worker import read_frame, write_frame\n"
        "req_r, req_w = os.pipe(); rep_r, rep_w = os.pipe()\n"
        "subprocess.Popen([sys.executable, '-m', 'app.modules.parsers.parse_worker',"
        " str(req_r), str(rep_w)], pass_fds=(req_r, rep_w))\n"
        "replies = os.fdopen(rep_r, 'rb')\n"
        "print(pickle.loads(read_frame(replies)), flush=True)\n"
        "write_frame(os.fdopen(req_w, 'wb'), (time.sleep, (600,)))\n"
        "time.sleep(600)\n"
    )
    parent = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=Path(parse_worker.__file__).parents[3],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        ready, _, _ = select.select([parent.stdout], [], [], 20)
        assert ready, "the worker never reported its pid"
        worker_pid = int(parent.stdout.readline())
        assert _alive(worker_pid)
    finally:
        parent.kill()
        parent.wait()
        parent.stdout.close()

    try:
        deadline = time.monotonic() + 10
        while _alive(worker_pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not _alive(worker_pid)
    finally:
        if _alive(worker_pid):  # a regression must not leave it sleeping for ten minutes
            os.kill(worker_pid, signal.SIGKILL)
