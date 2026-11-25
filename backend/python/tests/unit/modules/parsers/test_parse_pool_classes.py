"""The class pools: routing, the one width budget, and an inspection that never waits for big work ."""
from __future__ import annotations

import asyncio
import threading
import time
from typing import TYPE_CHECKING

import pytest

from app.capacity.classes import CostClass
from app.modules.parse_engine import pools, wiring
from app.modules.parsers import parse_pool, parse_pool_classes
from app.services.resource_governor.kinds import ParseKind
from tests.unit.services.messaging.governor_test_helpers import make_test_governor

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def classes(monkeypatch: pytest.MonkeyPatch) -> Iterator[object]:
    monkeypatch.setenv("PARSE_ADMISSION", "classes")
    monkeypatch.delenv("PARSE_LANES", raising=False)
    monkeypatch.setenv("PARSE_POOL_WORKERS", "2")
    monkeypatch.setenv("PARSE_INSPECT_WORKERS", "1")
    wiring.reset_for_tests()
    governor = make_test_governor(logger_name="test.pool")
    parse_pool.set_resource_governor(governor)
    yield governor
    parse_pool.shutdown_parse_pool()
    parse_pool.set_resource_governor(None)


def test_default_lane_factory_is_the_legacy_lane() -> None:
    pool = parse_pool._ParsePool(1)
    try:
        assert type(pool._make_lane()) is parse_pool._Lane
    finally:
        pool.shutdown()


def test_class_routing_and_budget(classes) -> None:
    """a job goes to the pool of its class and kind; one width budget caps live workers."""
    small = parse_pool_classes.get_pool(pools.Route.SMALL, classes)
    big = parse_pool_classes.get_pool(pools.Route.BIG, classes)
    inspect = parse_pool_classes.get_pool(pools.Route.INSPECT, classes)
    assert len({id(small), id(big), id(inspect)}) == 3
    assert (small.workers, big.workers, inspect.workers) == (2, 2, 1)
    assert parse_pool._pool(kind=ParseKind.INSPECT) is inspect
    with pools.parse_scope(CostClass.LARGE):
        assert parse_pool._pool(kind=ParseKind.PDF_LAYOUT) is big
    with pools.parse_scope(CostClass.SMALL):
        assert parse_pool._pool(kind=ParseKind.PDF_LAYOUT) is small
    assert parse_pool._pool(kind=ParseKind.TEXT) is small  # unscoped
    assert small.budget is big.budget and inspect.budget is None
    assert small.budget.limit == 2


def test_live_workers_never_exceed_the_width(classes) -> None:
    """Σ live workers <= W across the small and the big pool, four jobs on two slots."""
    width = parse_pool_classes.get_pool(pools.Route.SMALL, classes).budget

    async def run() -> list[object]:
        jobs = []
        for cls in (CostClass.SMALL, CostClass.LARGE, CostClass.SMALL, CostClass.LARGE):
            with pools.parse_scope(cls):
                jobs.append(asyncio.create_task(parse_pool.submit(time.sleep, 0.4, label="t", size=1)))
                await asyncio.sleep(0)
        return await asyncio.gather(*jobs, return_exceptions=True)

    results = asyncio.run(run())
    assert all(r is None for r in results), results
    assert width.peak <= 2


def test_an_inspection_runs_while_every_big_worker_is_busy(classes) -> None:
    """the inspect pool starts a job at once while both big lanes run a long parse."""
    started = threading.Event()

    async def run() -> float:
        long = []
        for _ in range(2):
            with pools.parse_scope(CostClass.LARGE):
                long.append(asyncio.create_task(parse_pool.submit(time.sleep, 1.5, label="big", size=1)))
        await asyncio.sleep(0.6)  # both big workers are up and sleeping
        started.set()
        t = time.monotonic()
        await parse_pool.submit(time.sleep, 0.0, label="inspect", size=1, kind=ParseKind.INSPECT)
        waited = time.monotonic() - t
        await asyncio.gather(*long)
        return waited

    waited = asyncio.run(run())
    assert started.is_set()
    # wall-clock-ok: upstream's bound: a big job would hold the slot for the 0.9 s left on the long parses plus a spawn
    assert waited < 1.0, waited  # a big job would have held it for the 0.9 s left on the long parses (plus a spawn)


def test_the_pool_stats_name_the_mode(classes) -> None:
    parse_pool_classes.get_pool(pools.Route.SMALL, classes)
    stats = parse_pool.pool_stats()
    assert stats["mode"] == "classes" and "small" in stats["pools"]  # type: ignore[operator]


def test_with_classes_off_nothing_is_routed_by_class(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PARSE_ADMISSION", raising=False)
    assert parse_pool.pool_stats() == {}
    assert wiring.classes_enabled() is False  # the legacy pool is built by `_pool` as before (test_parse_pool_off_pin)


def test_a_job_waits_for_a_busy_width_slot_as_long_as_its_own_timeout(classes, monkeypatch: pytest.MonkeyPatch) -> None:
    """Both slots are held by long parses; the third job's wait is its own timeout, not the 120 s a worker needs to start."""
    monkeypatch.setattr(parse_pool, "WORKER_START_SECONDS", 0.2)
    budget = parse_pool_classes.get_pool(pools.Route.SMALL, classes).budget
    assert budget.acquire(0.1, lambda: False) and budget.acquire(0.1, lambda: False)
    threading.Timer(0.8, budget.release).start()  # a slot frees after the start window but inside the job's timeout
    with pools.parse_scope(CostClass.SMALL):
        assert parse_pool.run_blocking(time.sleep, 0.0, label="third", size=1, timeout=10) is None
    budget.release()
