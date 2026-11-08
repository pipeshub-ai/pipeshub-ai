"""The projection worker's loop (no database): one failing iteration is logged, counted and survived; the backoff is bounded."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from app.services.graph_db.postgres.kg import drainer, legacy, restamp
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


async def test_stop_does_not_wait_out_the_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """A worker backing off because rows wait and its pass took none stops at once, not when the backoff ends."""
    monkeypatch.setattr(worker_mod, "RECHECK_S", 0.01)
    w = make(poll_s=3600.0)
    monkeypatch.setattr(w, "_backoff", lambda _stuck: 3600.0)

    async def more() -> bool:
        return True
    monkeypatch.setattr(w, "_more", more)
    w.start()
    assert await until(lambda: w.passes >= 1)
    await asyncio.sleep(0.1)                            # it is in the backoff now: the pass is over and `_more` said rows wait
    await asyncio.wait_for(w.stop(), 5.0)
    assert w._task.done()


async def test_stop_does_not_wait_for_a_drain_that_a_steady_writer_keeps_going(monkeypatch: pytest.MonkeyPatch) -> None:
    """While writes keep the log non-empty a pass would take page after page for ever: stop is looked at between pages, and a page already started is finished."""
    pages = {"started": 0, "finished": 0}

    async def one_page(_db: object, _org: str, *, limit: int) -> int:
        pages["started"] += 1
        await asyncio.sleep(0.01)                       # a page is a unit of its own: it runs to its end
        pages["finished"] += 1
        return 1                                        # and the log is never empty

    async def orgs() -> list[str]:
        return ["org-a"]

    async def no_publish(_tx: object, _org: str) -> None:
        return None
    monkeypatch.setattr(drainer, "drain", one_page)
    monkeypatch.setattr(legacy, "publish", no_publish)
    w = KgWorker(_NoUow(), orgs, dsn=None, poll_s=0.05)  # type: ignore[arg-type]
    w.start()
    try:
        assert await until(lambda: pages["finished"] >= 3), "the drain never started"
        await asyncio.wait_for(w.stop(), 5.0)
        assert w._task.done()
        assert pages["started"] == pages["finished"]    # no page was cut in the middle
    finally:
        if w._task is not None and not w._task.done():
            w._task.cancel()


class _NoUow:
    """The gauges are not under test: a unit of work that opens and does nothing."""

    opened = 0

    def uow(self) -> object:
        _NoUow.opened += 1
        class Unit:
            async def __aenter__(self) -> object:
                return object()

            async def __aexit__(self, *_a: object) -> None:
                return None
        return Unit()


async def test_the_drainers_own_loop_stops_between_pages_too(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = {"started": 0, "finished": 0}

    async def one_page(_db: object, _org: str, *, limit: int) -> int:
        pages["started"] += 1
        await asyncio.sleep(0.01)
        pages["finished"] += 1
        return 1

    async def orgs() -> list[str]:
        return ["org-a", "org-b"]
    monkeypatch.setattr(drainer, "drain", one_page)
    stop = asyncio.Event()
    task = asyncio.create_task(drainer.run(None, orgs, idle_s=0.01, stop=stop))       # type: ignore[arg-type]
    try:
        assert await until(lambda: pages["finished"] >= 3), "the drain never started"
        stop.set()
        await asyncio.wait_for(task, 5.0)
        assert pages["started"] == pages["finished"]
    finally:
        if not task.done():
            task.cancel()


async def test_no_unit_is_opened_for_the_gauges_once_the_worker_is_stopping(monkeypatch: pytest.MonkeyPatch) -> None:
    published = {"n": 0}
    _NoUow.opened = 0
    holder: dict[str, KgWorker] = {}

    async def stop_during_the_page(_db: object, _org: str, *, limit: int) -> int:
        holder["w"]._stop.set()                         # the stop arrives while a page is running
        await asyncio.sleep(0.01)
        return 1

    async def orgs() -> list[str]:
        return ["org-a"]

    async def publish(_tx: object, _org: str) -> None:
        published["n"] += 1
    monkeypatch.setattr(drainer, "drain", stop_during_the_page)
    monkeypatch.setattr(legacy, "publish", publish)
    w = KgWorker(_NoUow(), orgs, dsn=None, poll_s=0.05)  # type: ignore[arg-type]
    holder["w"] = w
    await w.run_once()
    assert published["n"] == 0 and _NoUow.opened == 0


async def test_a_stop_during_a_restamp_batch_ends_the_job_between_batches_and_opens_no_unit_for_the_gauges(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"claim": 0, "batch": 0}
    published = {"n": 0}
    _NoUow.opened = 0
    holder: dict[str, KgWorker] = {}
    job = restamp.Job(1, 5, "rows", "reach", True, 0, 1, "7")

    async def no_rows(_db: object, _org: str, *, limit: int) -> int:
        return 0

    async def claim(_db: object, _org: str, _worker: str, **_k: object) -> restamp.Job:
        calls["claim"] += 1
        return job

    async def one_batch(_db: object, _org: str, j: restamp.Job, _worker: str, **_k: object) -> tuple[bool, restamp.Job]:
        calls["batch"] += 1
        holder["w"]._stop.set()                         # the stop arrives while a batch is running: the batch ends, no other starts
        await asyncio.sleep(0.01)
        return False, j

    async def orgs() -> list[str]:
        return ["org-a", "org-b"]

    async def publish(_tx: object, _org: str) -> None:
        published["n"] += 1
    monkeypatch.setattr(drainer, "drain", no_rows)
    monkeypatch.setattr(restamp, "claim", claim)
    monkeypatch.setattr(restamp, "run_batch", one_batch)
    monkeypatch.setattr(legacy, "publish", publish)
    w = KgWorker(_NoUow(), orgs, dsn=None, poll_s=0.05)  # type: ignore[arg-type]
    holder["w"] = w
    await w.run_once()
    assert calls == {"claim": 1, "batch": 1}                      # one job claimed, one batch, no second batch, no second organisation
    assert published["n"] == 0 and _NoUow.opened == 0
