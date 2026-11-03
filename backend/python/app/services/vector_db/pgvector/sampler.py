"""The online recall sampler: a monitor, never part of an answer.

About ``sampler_rate`` of the dense legs are re-run exactly over the same filtered candidates (the same :class:`dense.Query`, so
the same ``visibility`` fragment and the same space) after the response is on its way, and compared with what the leg returned.
Recall is ``|live and exact| / min(leg, |exact|)``, published by tier, size band, share band and leg band. The query vector never
leaves the process. The sample runs in this process on a bounded queue (dropped when full), takes a background (``bg``) admission slot or is
skipped, and is rate-limited; its own database time is throttled to a share of the instance so it cannot become the load it watches.
"""

from __future__ import annotations

import asyncio
import collections
import dataclasses
import logging
import os
import random
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.core import org_context
from app.core.db import DbError
from app.services.vector_db.pgvector import dense
from app.services.vector_db.pgvector.scope import org_scope
from app.telemetry.modules import pgvector_search_metrics as telemetry

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.core.db import KernelDB
    from app.services.vector_db.pgvector.tuning import Tuning

logger = logging.getLogger(__name__)
RECALL_FLOOR = 0.95
MAX_ORGS = 1024           # orgs whose recent samples are kept (the oldest is dropped)
ORG_WINDOW = 100          # samples kept per org
ORG_MIN_SAMPLES = 20      # an org's mean counts once it has this many
CPU_SHARE = 0.05          # the sampler's database time may use this share of the instance's cores
RATE_FLOOR = 0.1          # ... but the rate never falls below this fraction of the configured one
_THROTTLE_WINDOW_S = 60.0


@dataclass(frozen=True)
class Sample:
    query: dense.Query
    live: frozenset[int]
    tier: str                 # the tier that served the leg, or "degraded"
    share_band: str
    n_hat: int


def recall(live: frozenset[int], exact: list[int], leg: int) -> float | None:
    """``|live and exact| / min(leg, |exact|)``; None when the exact scan found nothing (nothing to compare)."""
    want = exact[:leg]
    return len(live & set(want)) / len(want) if want else None


class Sampler:
    def __init__(self, db: KernelDB, t: Tuning, *, rng: Callable[[], float] = random.random,
                 clock: Callable[[], float] = time.monotonic, cores: int | None = None) -> None:
        self._db, self._t, self._rng, self._clock = db, t, rng, clock
        self._cores = cores or os.cpu_count() or 1
        self._queue: asyncio.Queue[Sample] = asyncio.Queue(maxsize=t.sampler_queue)
        self._worker: asyncio.Task | None = None
        self._stamps: collections.deque[float] = collections.deque(maxlen=max(1, t.sampler_max_per_s))
        self._busy: collections.deque[tuple[float, float]] = collections.deque()      # (finished at, seconds spent)
        self._orgs: dict[str, collections.deque[float]] = {}

    # ---- what the request path calls: synchronous, never waits ----
    def roll(self) -> bool:
        """The dice of :meth:`offer`, thrown early: the fused search statement returns the dense leg's rows only when this is True."""
        rate = self._t.sampler_rate * self._throttle()
        return rate > 0 and self._rng() < rate

    def offer(self, sample: Sample, *, rolled: bool = False) -> bool:
        """Maybe take ``sample`` (``rolled``: :meth:`roll` already said yes). True when it was queued."""
        if not rolled and not self.roll():
            return False
        now = self._clock()
        if len(self._stamps) == self._stamps.maxlen and now - self._stamps[0] < 1.0:
            telemetry.SAMPLE_DROPPED.inc("rate")
            return False
        try:
            self._queue.put_nowait(sample)
        except asyncio.QueueFull:
            telemetry.SAMPLE_DROPPED.inc("queue_full")
            return False
        self._stamps.append(now)
        if self._worker is None or self._worker.done():
            self._worker = org_context.spawn_detached(self._run(), name="pgvector-recall-sampler")
        return True

    def _throttle(self) -> float:
        """1.0 while the sampler's database time stays within ``CPU_SHARE`` of the instance, less above it (never below the floor)."""
        now = self._clock()
        while self._busy and self._busy[0][0] < now - _THROTTLE_WINDOW_S:
            self._busy.popleft()
        spent = sum(s for _, s in self._busy)
        allowed = CPU_SHARE * self._cores * _THROTTLE_WINDOW_S
        return 1.0 if spent <= allowed else max(RATE_FLOOR, allowed / spent)

    # ---- the worker ----
    async def _run(self) -> None:
        while True:
            sample = await self._queue.get()
            try:
                await self.measure(sample)
            except Exception:
                logger.exception("recall sample failed")
            finally:
                self._queue.task_done()

    async def measure(self, s: Sample) -> float | None:
        q = dataclasses.replace(s.query, anti=True)          # a background scan must stay bounded whatever the tombstone count
        started = self._clock()
        try:
            with org_scope(q.org):
                rows = await dense.exact(self._db, q, timeout_ms=self._t.sampler_timeout_ms, t=self._t)
        except dense.Rejected:
            telemetry.SAMPLE_DROPPED.inc("no_slot")
            return None
        except DbError:
            telemetry.SAMPLE_DROPPED.inc("timeout")
            return None
        finally:
            self._busy.append((self._clock(), self._clock() - started))
        r = recall(s.live, [sk for sk, _ in rows], q.leg)
        if r is None:
            return None
        telemetry.RECALL.observe(s.tier, dense.size_band(s.n_hat, self._t.n_e0), s.share_band, telemetry.leg_band(q.leg), value=r)
        if s.tier == "degraded":           # overload is not a property of the org's data
            return r
        if len(self._orgs) >= MAX_ORGS and q.org not in self._orgs:
            self._orgs.pop(next(iter(self._orgs)))
        recent = self._orgs.setdefault(q.org, collections.deque(maxlen=ORG_WINDOW))
        recent.append(r)
        if len(recent) >= ORG_MIN_SAMPLES and sum(recent) / len(recent) < RECALL_FLOOR:
            telemetry.ORG_RECALL_LOW.inc("recent")
            logger.warning("sampled dense recall of an organisation is %.3f over its last %d samples (floor %.2f)",
                           sum(recent) / len(recent), len(recent), RECALL_FLOOR)
        if s.tier == dense.Tier.E0 and r < 1.0:
            logger.warning("an exact tier answered with recall %.3f: a write between the answer and the sample can cause this, a repeat means a bug", r)
        return r

    def org_recall(self, org: str) -> tuple[float, int] | None:
        """(mean, samples) of the org's recent samples, for the promotion report."""
        recent = self._orgs.get(org)
        return (sum(recent) / len(recent), len(recent)) if recent else None

    async def drain(self) -> None:
        """Wait until every queued sample was measured (tests, shutdown)."""
        await self._queue.join()

    async def close(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
            self._worker = None
