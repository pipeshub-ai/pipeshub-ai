"""The projection worker's loop (no database): one failing iteration is logged, counted and survived; the backoff is bounded."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from app.services.graph_db.postgres.kg import worker as worker_mod
from app.services.graph_db.postgres.kg.worker import KgWorker

if TYPE_CHECKING:
    import pytest


async def no_orgs() -> list[str]:
    return []


def make(poll_s: float = 0.05) -> KgWorker:
    return KgWorker(None, no_orgs, dsn=None, poll_s=poll_s)  # type: ignore[arg-type]


async def until(cond, seconds: float = 5.0) -> bool:
    for _ in range(int(seconds / 0.01)):
        if cond():
            return True
        await asyncio.sleep(0.01)
    return False


async def test_one_failing_iteration_does_not_end_the_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker_mod, "RECHECK_S", 0.01)
    w = make()
    calls = {"n": 0}

    async def more() -> bool:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("the database went away for a moment")
        return False
    monkeypatch.setattr(w, "_more", more)
    w.start()
    try:
        assert await until(lambda: calls["n"] >= 3), "the loop ended after the first error"
        assert not w._task.done()
        passes = w.passes
        assert await until(lambda: w.passes > passes)
    finally:
        await w.stop()


async def test_a_failing_org_list_does_not_end_the_worker_either(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker_mod, "RECHECK_S", 0.01)
    calls = {"n": 0}

    async def orgs() -> list[str]:
        calls["n"] += 1
        if calls["n"] <= 2:
            raise TimeoutError("the org list is not there yet")
        return []
    w = KgWorker(None, orgs, dsn=None, poll_s=0.05)  # type: ignore[arg-type]
    w.start()
    try:
        assert await until(lambda: calls["n"] >= 5 and w.passes >= 1)
        assert not w._task.done()
    finally:
        await w.stop()


def test_the_backoff_doubles_and_stays_below_the_poll_interval_however_long_the_worker_is_stuck() -> None:
    w = make(poll_s=5.0)
    waits = [w._backoff(n) for n in range(0, 12)]
    assert waits == sorted(waits) and waits[1] == 2 * waits[0] and max(waits) == 5.0
    for stuck in (1024, 10**6, 10**18):          # 2**1024 does not convert to a float: an uncapped exponent raised OverflowError after about 85 minutes
        assert w._backoff(stuck) == 5.0
