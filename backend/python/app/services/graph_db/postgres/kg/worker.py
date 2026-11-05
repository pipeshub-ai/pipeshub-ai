"""The projection worker: projects the change log and publishes the gauges. Woken by NOTIFY, with a safety poll.

``pipeshub_kg_changes`` is sent at commit by a write unit that changed the graph (flush.wake, coalesced); it only wakes
the worker, which reads the log itself, so a lost notification costs at most ``poll_s``. The projection default is off (``PG_KG_PROJECT``): nothing starts a worker
unless it is set to 1, see :func:`enabled`.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import uuid
from typing import TYPE_CHECKING

from app.core.org_context import bind, spawn_detached
from app.services.graph_db.postgres.kg import legacy
from app.services.graph_db.postgres.kg.drainer import PAGE, _ctx, drain_or_retry, lag
from app.services.pg_runtime import PgListener
from app.telemetry.backend import METRICS_BACKEND

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.core.db import KernelDB

logger = logging.getLogger(__name__)
CHANNELS = ("pipeshub_kg_changes",)
RECHECK_S = 0.3
MAX_DOUBLINGS = 8                                  # RECHECK_S * 2**8 is past any sensible poll interval
LOOP_FAILED = METRICS_BACKEND.counter("pipeshub_kg_worker_loop_failed_total", "Iterations of the projection worker's loop that raised and were continued", ["where"])


def enabled() -> bool:
    return os.environ.get("PG_KG_PROJECT") == "1"


class KgWorker:
    """``orgs()`` names the organisations to serve (one in the open-source edition). ``dsn``: a direct address to LISTEN on; without one the worker only polls."""

    def __init__(self, db: KernelDB, orgs: Callable[[], Awaitable[list[str]]], *, dsn: str | None = None, poll_s: float = 5.0, page: int = PAGE) -> None:
        self._db, self._orgs, self._dsn, self._poll, self._page = db, orgs, dsn, poll_s, page
        self.id = f"kg-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self._wake = asyncio.Event()
        self._listeners: list[PgListener] = []
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.passes = 0                                    # loop iterations done (tests wait on it)

    def _on_notify(self, _payload: str) -> None:
        self._wake.set()

    async def run_once(self) -> int:
        """One pass over every org: drain the change log and publish the gauges. Returns the log rows projected. Each phase has its own error handling: a log that cannot be
        drained (``drain_or_retry`` counts it and leaves the rows) must not stop the gauges."""
        rows = 0
        for org in await self._orgs():
            if self._stop.is_set():
                break                                      # what is left stays in the log for the next start
            rows += await drain_or_retry(self._db, org, page=self._page, stop=self._stop)
            try:
                with bind(_ctx(org)):
                    async with self._db.uow() as tx:
                        await legacy.publish(tx, org)
            except Exception:
                logger.exception("kg gauges failed for an org; retrying")
        return rows

    async def _more(self) -> bool:
        for org in await self._orgs():
            if await lag(self._db, org):
                return True
        return False

    def _backoff(self, stuck: int) -> float:
        """Seconds to wait after ``stuck`` passes in a row that found rows and took none: doubling from RECHECK_S, never past ``poll_s`` (the exponent is capped, so it cannot overflow)."""
        return min(RECHECK_S * 2 ** min(stuck, MAX_DOUBLINGS), self._poll)

    async def _pause(self, seconds: float) -> None:
        """Sleep, unless the worker is stopped meanwhile (a wake-up from the listener does not end it)."""
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._stop.wait(), seconds)

    async def _once(self, stuck: int) -> int:
        """One iteration of the loop; returns the new ``stuck``."""
        self._wake.clear()
        rows = await self.run_once()
        self.passes += 1
        if rows:
            return 0                                   # more may have arrived while this pass ran
        await self._pause(RECHECK_S)                   # a wake-up the writers coalesced away (flush.wake) is covered by one more look
        if self._wake.is_set():
            return stuck
        if await self._more():                         # rows are waiting and the pass took none: it gave up (or failed); try again, further apart each time
            await self._pause(self._backoff(stuck + 1))
            return stuck + 1
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._wake.wait(), self._poll)
        return 0

    async def _loop(self) -> None:
        stuck = 0
        while not self._stop.is_set():
            try:
                stuck = await self._once(stuck)
            except Exception:
                LOOP_FAILED.inc("worker")
                logger.exception("kg worker iteration failed; continuing")
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), self._poll)

    def _died(self, task: asyncio.Task) -> None:
        if not task.cancelled() and task.exception() is not None:
            logger.error("kg worker task ended", exc_info=task.exception())

    def start(self) -> None:
        if self._dsn:
            self._listeners = [PgListener(self._dsn, ch, self._on_notify, on_reconnect=self._wake.set) for ch in CHANNELS]
            for ln in self._listeners:
                ln.start()
        self._task = spawn_detached(self._loop(), name="kg-worker")
        self._task.add_done_callback(self._died)

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        for ln in self._listeners:
            await ln.stop()
        if self._task is not None:
            await self._task
