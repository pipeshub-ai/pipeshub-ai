"""The projection worker: projects the change log and publishes the gauges. Woken by NOTIFY, with a safety poll.

``pipeshub_kg_changes`` is sent at commit by a write unit that changed the graph (flush.wake, coalesced); it only wakes
the worker, which reads the log itself, so a lost notification costs at most ``poll_s``. The projection default is on (``PG_KG_PROJECT=0`` is the off switch): the connectors service starts the worker
(:func:`enabled`), which also takes each connector to ``enforce`` (:mod:`bootstrap`).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import uuid
from typing import TYPE_CHECKING

from app.core.org_context import bind, spawn_detached
from app.services.graph_db.postgres.kg import bootstrap, flush, legacy, net, restamp
from app.services.graph_db.postgres.kg.drainer import PAGE, _ctx, drain_or_retry, lag
from app.services.pg_runtime import PgListener
from app.telemetry.backend import METRICS_BACKEND

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.core.db import KernelDB

logger = logging.getLogger(__name__)
CHANNELS = ("pipeshub_kg_changes", "pipeshub_kg_restamp")

RECHECK_S = 0.3
STOP_TIMEOUT_S = 10.0                              # how long stop() lets the pass in flight end before it cancels it
MAX_DOUBLINGS = 8                                  # RECHECK_S * 2**8 is past any sensible poll interval
LOOP_FAILED = METRICS_BACKEND.counter("pipeshub_kg_worker_loop_failed_total", "Iterations of the projection worker's loop that raised and were continued", ["where"])


def enabled() -> bool:
    return flush.mode() != "off"


class KgWorker:
    """``orgs()`` names the organisations to serve (one in the open-source edition). ``dsn``: a direct address to LISTEN on; without one the worker only polls."""

    def __init__(self, db: KernelDB, orgs: Callable[[], Awaitable[list[str]]], *, dsn: str | None = None, poll_s: float = 5.0, page: int = PAGE,
                 stale_ms: int = restamp.STALE_MS, batch: int | None = None) -> None:
        self._db, self._orgs, self._dsn, self._poll, self._page, self._stale, self._batch = db, orgs, dsn, poll_s, page, stale_ms, batch
        self.id = f"kg-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self._wake = asyncio.Event()
        self._listeners: list[PgListener] = []
        self._demotes: set[asyncio.Task] = set()
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._verify_states: dict[tuple[str, str], net.VerifyState] = {}      # each enforced connector's turn and verify in progress (net)
        self._verify_task: asyncio.Task | None = None
        self._due: dict[tuple[str, str], float] = {}        # when a connector may next be taken to enforce (bootstrap)
        self.passes = 0                                    # loop iterations done (tests wait on it)

    def _on_demote(self, payload: str) -> None:
        """Another process (the query service, which cannot write kg) saw kg differ from the walk and asks for the connector to go back to the walk."""
        task = spawn_detached(net.demote_requested(self._db, self._orgs, payload), name="kg-demote")
        self._demotes.add(task)
        task.add_done_callback(self._demotes.discard)

    def _on_notify(self, _payload: str) -> None:
        self._wake.set()

    async def run_once(self) -> int:
        """One pass over every org: drain the change log, finish the claimable restamp jobs and publish the gauges. Returns the log rows projected plus the jobs finished. Each phase has
        its own error handling: a log that cannot be drained (``drain_or_retry`` counts it and leaves the rows) must not keep records hidden, and jobs that fail must not stop the projection."""
        rows = 0
        for org in await self._orgs():
            if self._stop.is_set():
                break                                      # what is left stays in the log for the next start
            try:
                rows += await net.demote_pending(self._db, org)      # before the drain: a request row is a change the drain would take
            except Exception:
                logger.exception("kg demotion requests failed for an org; retrying")
            rows += await drain_or_retry(self._db, org, page=self._page, stop=self._stop)
            if self._stop.is_set():
                break                                      # shutting down: no new unit for the gauges
            try:
                rows += await restamp.run_jobs(self._db, org, self.id, stale_ms=self._stale, batch=self._batch, stop=self._stop)
            except Exception:
                logger.exception("kg restamp jobs failed for an org; retrying")
            if self._stop.is_set():
                break                                      # shutting down: no new unit for the gauges
            try:
                rows += await bootstrap.bring_to_enforce(self._db, org, self._due, self._stop)
            except Exception:
                logger.exception("kg bootstrap failed for an org; retrying")
            if self._stop.is_set():
                break
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

    async def _verify_loop(self) -> None:
        """The periodic verify of the enforced connectors (``net.verify_due``), in a task of its own."""
        while not self._stop.is_set():
            try:
                for org in await self._orgs():
                    await net.verify_due(self._db, org, self._verify_states, self._stop)
            except Exception:
                LOOP_FAILED.inc("verify")
                logger.exception("kg verify iteration failed; continuing")
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop.wait(), net.VERIFY_TICK_S)

    def _died(self, task: asyncio.Task) -> None:
        if not task.cancelled() and task.exception() is not None:
            logger.error("kg worker task ended", exc_info=task.exception())

    def start(self) -> None:
        if self._dsn:
            self._listeners = [PgListener(self._dsn, ch, self._on_notify, on_reconnect=self._wake.set) for ch in CHANNELS]
            self._listeners.append(PgListener(self._dsn, net.CHANNEL, self._on_demote, on_reconnect=self._wake.set))
            for ln in self._listeners:
                ln.start()
        self._task = spawn_detached(self._loop(), name="kg-worker")
        self._task.add_done_callback(self._died)
        self._verify_task = spawn_detached(self._verify_loop(), name="kg-verify")       # apart from the drain: a slow verify never holds a projection

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        for ln in self._listeners:
            await ln.stop()
        for t in (*self._demotes, self._verify_task):
            if t is not None:
                t.cancel()
        await asyncio.gather(*(t for t in (*self._demotes, self._verify_task) if t is not None), return_exceptions=True)
        await net.shutdown()
        if self._task is not None:
            done, _ = await asyncio.wait({self._task}, timeout=STOP_TIMEOUT_S)      # a batch or a verify in flight ends by itself; past the limit it is cut
            if not done:
                logger.warning("kg worker did not stop within %.0f s: cancelling its pass in flight (what it had not committed is redone at the next start)", STOP_TIMEOUT_S)
                self._task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await self._task


def start_for(graph_provider: object, logger: logging.Logger) -> KgWorker | None:
    """The worker of the connectors service: started when the graph is PostgreSQL and the projection is on, else None (Neo4j and ArangoDB never reach this). It serves every org of the
    graph. Two processes (replicas) may each start one: a page of an org's log is taken under an advisory lock (``drainer.drain``), restamp jobs are claimed, and a promotion verifies under the
    connector's lock, so they never apply a change twice; the second only waits its turn."""
    from app.core.org_context import system
    from app.services.graph_db.postgres.provider import PostgresGraphProvider
    from app.services.pg_runtime import listen_dsn
    if not isinstance(graph_provider, PostgresGraphProvider) or not enabled():
        return None

    async def orgs() -> list[str]:
        with system("kg_worker"):
            docs = await graph_provider.get_all_orgs(active=False)
        return [str(o.get("_key") or o["id"]) for o in docs if o.get("_key") or o.get("id")]
    worker = KgWorker(graph_provider._db, orgs, dsn=listen_dsn())
    worker.start()
    logger.info("kg worker started (projection of the typed graph)")
    return worker
