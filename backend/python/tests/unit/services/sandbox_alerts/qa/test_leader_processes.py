"""At most one evaluator per state directory, across processes."""

from __future__ import annotations

import multiprocessing
import os
import signal
import time
from typing import TYPE_CHECKING

from app.services.sandbox_alerts.leader import AlertLeader

if TYPE_CHECKING:
    from pathlib import Path

_CTX = multiprocessing.get_context("fork")


def _race(state: str, barrier, results, hold_s: float) -> None:
    from pathlib import Path

    leader = AlertLeader(Path(state))
    barrier.wait(timeout=30)  # a peer that never arrives breaks it, not hangs it
    won = leader.try_acquire()
    results.put((os.getpid(), won))
    time.sleep(hold_s)


def test_processes_racing_for_the_lock_elect_exactly_one(tmp_path: Path) -> None:
    n = 8
    barrier = _CTX.Barrier(n)
    results = _CTX.Queue()
    procs = [_CTX.Process(target=_race, args=(str(tmp_path), barrier, results, 1.0)) for _ in range(n)]
    try:
        for p in procs:
            p.start()
        outcomes = [results.get(timeout=30) for _ in range(n)]
        for p in procs:
            p.join(timeout=30)
    finally:
        for p in procs:
            if p.is_alive():
                p.kill()
            if p.pid is not None:
                p.join(timeout=5)
    assert sum(won for _, won in outcomes) == 1


def _hold(state: str, ready) -> None:
    from pathlib import Path

    assert AlertLeader(Path(state)).try_acquire()
    ready.set()
    time.sleep(60)


def test_a_leader_killed_mid_evaluation_hands_over_at_once(tmp_path: Path) -> None:
    ready = _CTX.Event()
    proc = _CTX.Process(target=_hold, args=(str(tmp_path), ready))
    proc.start()
    try:
        assert ready.wait(30)
        mine = AlertLeader(tmp_path)
        assert not mine.try_acquire()
        os.kill(proc.pid, signal.SIGKILL)
        proc.join(timeout=30)
        assert mine.try_acquire()
        assert mine.try_acquire()  # idempotent while held
    finally:
        if proc.is_alive():
            proc.kill()
        proc.join(timeout=5)
    mine.release()
    assert not mine.held
