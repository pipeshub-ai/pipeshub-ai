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
    """A worker with a pipe of its own. A shared Queue's feeder thread holds the queue's write lock until after the
    reader has the message, so SIGKILLing a worker that just reported could leave that lock held and block every
    later put: the restarted worker's report never arrived (_queue.Empty)."""
    receive, send = ctx.Pipe(duplex=False)
    worker = ctx.Process(target=_worker, args=(str(state), waits, send, hang))
    worker.start()
    send.close()
    return worker, receive


def _report(worker: SpawnProcess, receive: Connection) -> int:
    assert receive.poll(60), "the worker did not report within 60 s"
    try:
        return receive.recv()
    except EOFError:
        # The worker holds the only write end, so a worker that died before flushing reads as EOF.
        worker.join(5)
        pytest.fail(f"the worker exited with {worker.exitcode} before flushing its ledger")


def test_four_workers_are_summed_and_a_killed_one_keeps_its_counts(tmp_path: Path) -> None:
    ctx = multiprocessing.get_context("spawn")
    started = [_start(ctx, tmp_path, 50, i == 0) for i in range(4)]
    pids = {_report(worker, receive) for worker, receive in started}
    assert len(pids) == 4
    os.kill(started[0][0].pid, signal.SIGKILL)
    for worker, _ in started:
        worker.join(30)

    totals = read_window(tmp_path / "load", time.time(), 1800)
    assert totals.files == 4
    assert totals.waited == 200
    assert totals.refused == 100

    restarted, receive = _start(ctx, tmp_path, 10, False)
    _report(restarted, receive)
    restarted.join(30)
    totals = read_window(tmp_path / "load", time.time(), 1800)
    assert totals.files == 5
    assert totals.waited == 210
