"""The parse pools of ``PARSE_ADMISSION=classes``: a small pool, a big pool and an inspect pool.

A lane is the same thread-and-worker pair as in ``parse_pool``. What differs is how many may be alive and
who waits for whom:

* ``small`` and ``big`` have ``W`` lanes each, but one ``WidthBudget`` caps the worker processes alive
  across both at ``W``: the pools are warm caches, and the member budget (not the pool) decides how many
  parses run. A big job queued behind a small one waits in its own pool, never in the small one's queue.
* ``inspect`` has ``inspect_workers`` lanes of its own, outside that count, so a PDF inspection starts
  while every big worker runs a long parse.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import TYPE_CHECKING

from app.modules.parse_engine.pools import Route, WidthBudget
from app.modules.parsers import parse_pool as base
from app.utils.env_config import env_int

if TYPE_CHECKING:
    from app.services.resource_governor import ResourceGovernor

_logger = logging.getLogger(__name__)

INSPECT_WORKERS_ENV = "PARSE_INSPECT_WORKERS"


class _BudgetedLane(base._Lane):
    """A lane whose live worker counts against the shared width.

    A lane with nothing to do keeps its worker for ``IDLE_SECONDS``. When another lane needs a slot and the
    width is spent, it takes the worker of an idle lane (one that is not running a job), so a warm small
    worker never keeps a big job waiting, and the reverse.
    """

    def __init__(self, pool: _ClassPool) -> None:
        super().__init__(pool)
        self._budget = pool.budget
        self._busy = threading.Lock()  # held while a job runs
        self._wlock = threading.Lock()  # the worker reference and its slot change together
        _Pools.register(self)

    def _run(self, job: base._Job) -> object:
        with self._busy:
            return super()._run(job)

    def _start_worker(self, job: base._Job) -> base._Worker:
        budget = self._budget
        if budget is not None:
            # The slot is busy while another worker parses, which can take as long as a job may: the wait is bounded by
            # this job's own timeout, not by the time a worker needs to start.
            end = time.monotonic() + (job.timeout if job.timeout is not None else base.messaging_env.record_processing_timeout)
            while not budget.acquire(0.05, lambda: self._abandoned(job)):
                if self._abandoned(job):
                    raise base.ParseAbandonedError("parse abandoned while it waited for a worker slot")
                if _Pools.reclaim_idle(self):
                    continue
                if time.monotonic() >= end:
                    raise base._WorkerUnavailable("no worker slot within the pool width")
        try:
            worker = super()._start_worker(job)
        except BaseException:
            if budget is not None:
                budget.release()
            raise
        with self._wlock:
            self._worker = worker
        return worker

    def _drop_worker(self) -> None:
        with self._wlock:
            worker, self._worker = self._worker, None
        if worker is not None:
            worker.stop()
            if self._budget is not None:
                self._budget.release()

    def drop_if_idle(self) -> bool:
        """Stop this lane's worker when no job is running on it; True when a slot came free."""
        if self._worker is None or not self._busy.acquire(blocking=False):
            return False
        try:
            if self._worker is None:
                return False
            self._drop_worker()
            return True
        finally:
            self._busy.release()


class _ClassPool(base._ParsePool):
    def __init__(self, name: str, workers: int, budget: WidthBudget | None) -> None:
        self.name = name
        self.budget = budget
        super().__init__(workers)

    def _make_lane(self) -> base._Lane:
        return _BudgetedLane(self)


class _Pools:
    lock = threading.RLock()  # a pool built under it registers its lanes under it
    pools: dict[Route, _ClassPool] = {}
    budget: WidthBudget | None = None
    lanes: list[_BudgetedLane] = []

    @classmethod
    def register(cls, lane: _BudgetedLane) -> None:
        with cls.lock:
            cls.lanes.append(lane)

    @classmethod
    def reclaim_idle(cls, requester: _BudgetedLane) -> bool:
        with cls.lock:
            others = [lane for lane in cls.lanes if lane is not requester and lane._budget is not None]
        return any(lane.drop_if_idle() for lane in others)


def inspect_workers(governor: ResourceGovernor | None = None) -> int:
    configured = env_int(INSPECT_WORKERS_ENV, 0)
    if configured > 0:
        return configured
    try:
        from app.capacity.runtime import current_plan

        plan = current_plan()
        if plan is not None and plan.widths is not None:
            return max(1, plan.widths.inspect_workers)
    except Exception:  # the plan is an input, never a requirement
        _logger.debug("no capacity plan for the inspect pool", exc_info=True)
    return 1


def get_pool(route: Route, governor: ResourceGovernor) -> _ClassPool | None:
    width = base.parse_pool_workers()
    if width <= 0 or os.name != "posix":
        return None
    with _Pools.lock:
        pool = _Pools.pools.get(route)
        if pool is not None:
            return pool
        if _Pools.budget is None:
            _Pools.budget = WidthBudget(width)
        if route is Route.INSPECT:
            pool = _ClassPool("inspect", inspect_workers(governor), None)
        else:
            pool = _ClassPool(route.value, width, _Pools.budget)
        _Pools.pools[route] = pool
        _logger.info("Parse pool %s ready: %d lane(s), width %d", route.value, pool.workers, width)
        return pool


def shutdown() -> bool:
    with _Pools.lock:
        pools, _Pools.pools = list(_Pools.pools.values()), {}
        _Pools.budget = None
        _Pools.lanes = []
    for pool in pools:
        pool.shutdown()
    return bool(pools)


def stats() -> dict[str, object]:
    budget = _Pools.budget
    return {
        "mode": "classes",
        "width": budget.limit if budget else None,
        "live_workers": budget.live if budget else 0,
        "pools": {r.value: {"lanes": p.workers, "queued": p.jobs.qsize()} for r, p in _Pools.pools.items()},
    }
