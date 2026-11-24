"""Several worker processes write their own load ledger; the alert reader sums them.

Needs no services; marked ``integration`` because it spawns and kills processes.
"""

from __future__ import annotations

import multiprocessing
import os
import signal
import time
from pathlib import Path

import pytest

from app.services.sandbox_alerts.window import read_window

pytestmark = pytest.mark.integration


def _worker(state: str, waits: int, done, hang) -> None:
    from app.agent_loop_lib.sandbox.capacity import HoldReason
    from app.agent_loop_lib.sandbox.load_ledger import LoadLedger

    ledger = LoadLedger(Path(state), mode="compose", backed_off=lambda: False)
    for i in range(waits):
        ledger.outcome("refused" if i % 2 else "granted", 1.0, HoldReason.SLOTS_FULL)
    ledger.flush()
    done.put(os.getpid())
    if hang:
        time.sleep(60)


def test_four_workers_are_summed_and_a_killed_one_keeps_its_counts(tmp_path: Path) -> None:
    ctx = multiprocessing.get_context("spawn")
    done = ctx.Queue()
    workers = [ctx.Process(target=_worker, args=(str(tmp_path), 50, done, i == 0)) for i in range(4)]
    for worker in workers:
        worker.start()
    pids = {done.get(timeout=60) for _ in workers}
    assert len(pids) == 4
    os.kill(workers[0].pid, signal.SIGKILL)
    for worker in workers:
        worker.join(30)

    totals = read_window(tmp_path / "load", time.time(), 1800)
    assert totals.files == 4
    assert totals.waited == 200
    assert totals.refused == 100

    restarted = ctx.Process(target=_worker, args=(str(tmp_path), 10, done, False))
    restarted.start()
    done.get(timeout=60)
    restarted.join(30)
    totals = read_window(tmp_path / "load", time.time(), 1800)
    assert totals.files == 5
    assert totals.waited == 210
