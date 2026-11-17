from __future__ import annotations

import multiprocessing
from pathlib import Path

from app.services.sandbox_alerts.leader import AlertLeader


def _hold(directory: str, ready, release) -> None:
    leader = AlertLeader(Path(directory))
    ready.put(leader.try_acquire())
    release.wait(10)


def test_one_process_leads_and_another_takes_over_when_it_exits(tmp_path: Path) -> None:
    state = tmp_path / "state"
    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Queue()
    release = ctx.Event()
    child = ctx.Process(target=_hold, args=(str(state), ready, release))
    child.start()
    try:
        assert ready.get(timeout=20) is True
        here = AlertLeader(state)
        assert here.try_acquire() is False
        release.set()
        child.join(10)
        assert here.try_acquire() is True
        assert here.held
        here.release()
        assert not here.held
    finally:
        release.set()
        child.join(5)
        if child.is_alive():
            child.kill()


def test_two_leaders_in_one_process_do_not_both_lead(tmp_path: Path) -> None:
    first, second = AlertLeader(tmp_path), AlertLeader(tmp_path)
    assert first.try_acquire() is True
    assert second.try_acquire() is False
    first.release()
    assert second.try_acquire() is True
    second.release()
