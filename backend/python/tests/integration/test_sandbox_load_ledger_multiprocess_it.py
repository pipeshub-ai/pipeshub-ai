"""Several worker processes write their own load ledger; the alert reader sums them.

Needs no services; marked ``integration`` because it spawns and kills processes.
"""

from __future__ import annotations

import multiprocessing
import os
import signal
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from app.services.sandbox_alerts.window import read_window

if TYPE_CHECKING:
    from multiprocessing.connection import Connection
    from multiprocessing.context import SpawnContext, SpawnProcess

pytestmark = pytest.mark.integration


def _worker(state: str, waits: int, done: Connection, hang: bool) -> None:
    from app.agent_loop_lib.sandbox.capacity import HoldReason
    from app.agent_loop_lib.sandbox.load_ledger import LoadLedger

    ledger = LoadLedger(Path(state), mode="compose", backed_off=lambda: False)
    for i in range(waits):
        ledger.outcome("refused" if i % 2 else "granted", 1.0, HoldReason.SLOTS_FULL)
    ledger.flush()
    done.send(os.getpid())
    if hang:
        time.sleep(60)


def _start(ctx: SpawnContext, state: Path, waits: int, hang: bool) -> tuple[SpawnProcess, Connection]:
    # One pipe per worker, not a shared Queue: a Queue's write lock is shared by
    # every writer, and SIGKILL can land while the killed worker's feeder thread
    # still holds it, which blocks every later writer for good.
    recv, send = ctx.Pipe(duplex=False)
    worker = ctx.Process(target=_worker, args=(str(state), waits, send, hang))
    worker.start()
    send.close()  # the worker holds the only write end, so its death reads as EOF
    return worker, recv


def _flushed(worker: SpawnProcess, recv: Connection) -> int:
    try:
        return recv.recv()
    except EOFError:
        pytest.fail(f"worker exited with {worker.exitcode} before flushing its ledger")


def test_four_workers_are_summed_and_a_killed_one_keeps_its_counts(tmp_path: Path) -> None:
    ctx = multiprocessing.get_context("spawn")
    workers = [_start(ctx, tmp_path, 50, i == 0) for i in range(4)]
    pids = {_flushed(*worker) for worker in workers}
    assert len(pids) == 4
    os.kill(workers[0][0].pid, signal.SIGKILL)
    for worker, _ in workers:
        worker.join()

    totals = read_window(tmp_path / "load", time.time(), 1800)
    assert totals.files == 4
    assert totals.waited == 200
    assert totals.refused == 100

    restarted = _start(ctx, tmp_path, 10, False)
    _flushed(*restarted)
    restarted[0].join()
    totals = read_window(tmp_path / "load", time.time(), 1800)
    assert totals.files == 5
    assert totals.waited == 210
