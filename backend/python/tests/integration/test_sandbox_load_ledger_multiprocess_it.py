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


def _worker(state: str, waits: int, hang: bool) -> None:
    from app.agent_loop_lib.sandbox.capacity import HoldReason
    from app.agent_loop_lib.sandbox.load_ledger import LoadLedger

    ledger = LoadLedger(Path(state), mode="compose", backed_off=lambda: False)
    for i in range(waits):
        ledger.outcome("refused" if i % 2 else "granted", 1.0, HoldReason.SLOTS_FULL)
    ledger.flush()
    if hang:
        time.sleep(60)


def _ledgers(directory: Path, files: int, waited: int) -> None:
    """Wait until ``files`` ledgers on disk hold ``waited`` waits between them (a ledger may also be flushed part way). The workers report through
    their files, not a queue: a worker killed while its queue feeder still held the queue's shared write lock leaves the lock held, and every
    later put hangs."""
    deadline = time.monotonic() + 60
    while (totals := read_window(directory, time.time(), 1800)).files < files or totals.waited < waited:
        assert time.monotonic() < deadline, f"{totals.files} of {files} ledgers, {totals.waited} of {waited} waits written in 60 s"
        time.sleep(0.05)


def test_four_workers_are_summed_and_a_killed_one_keeps_its_counts(tmp_path: Path) -> None:
    ctx = multiprocessing.get_context("spawn")
    # daemon: a worker left running by a failed assertion is killed at exit instead of keeping the test process alive
    workers = [ctx.Process(target=_worker, args=(str(tmp_path), 50, i == 0), daemon=True) for i in range(4)]
    try:
        for worker in workers:
            worker.start()
        _ledgers(tmp_path / "load", 4, 200)
        os.kill(workers[0].pid, signal.SIGKILL)
        for worker in workers:
            worker.join(30)

        totals = read_window(tmp_path / "load", time.time(), 1800)
        assert totals.files == 4
        assert totals.waited == 200
        assert totals.refused == 100

        restarted = ctx.Process(target=_worker, args=(str(tmp_path), 10, False), daemon=True)
        workers.append(restarted)
        restarted.start()
        restarted.join(60)
        assert restarted.exitcode == 0
        totals = read_window(tmp_path / "load", time.time(), 1800)
        assert totals.files == 5
        assert totals.waited == 210
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.kill()
