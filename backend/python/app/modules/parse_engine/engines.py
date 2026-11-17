"""The Docling engine: how many conversions run, and what slows them (no I/O of its own).

Three parts:

* ``DemandEwma`` and ``docling_floor``: the floor of Docling conversions is sized from measured demand, the
  smallest width that keeps utilisation at or below 0.55 for the arrivals and service times seen over the last
  five minutes, never below a static minimum. The ledger's ``plan_floors`` is the authority when present; it
  is passed in as ``floor_fn`` and this module's formula is the same one, used when it is absent.
* ``DoclingEngine``: in-flight conversions never exceed the current limit. A 429 halves the limit (min 1) and
  honours ``Retry-After``; calm successes raise it by one. It knows nothing of any other pool, so a Docling
  throttle slows Docling calls only and is never reported to the consumer coordinator.
* The per-revision engine decision: the Docling deadline and reuse of a recorded engine (``decide_engine``).
"""
from __future__ import annotations

import asyncio
import contextlib
import math
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.modules.parse_engine.cost import CostClass, CostEstimate, Engine

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

__all__ = [
    "DemandEwma",
    "DoclingEngine",
    "EngineDecision",
    "decide_engine",
    "docling_deadline_s",
    "docling_floor",
]

TARGET_RHO = 0.55
EWMA_WINDOW_S = 300.0


class DemandEwma:
    """EWMA of arrival rate (per second) and of service seconds, with a 5-minute time constant.

    Arrivals are counted into the interval since the last fold, so a quiet spell decays the rate
    instead of freezing it.
    """

    def __init__(
        self,
        *,
        initial_rate: float = 0.0,
        initial_service_s: float = 1.0,
        window_s: float = EWMA_WINDOW_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._rate = initial_rate
        self._service = initial_service_s
        self._window = window_s
        self._clock = clock
        self._last = clock()
        self._pending = 0

    def arrival(self) -> None:
        self._pending += 1

    def service(self, seconds: float) -> None:
        if seconds > 0:
            self._service += 0.1 * (seconds - self._service)

    def _fold(self) -> None:
        now = self._clock()
        dt = now - self._last
        if dt <= 0:
            return
        alpha = 1.0 - math.exp(-dt / self._window)
        self._rate += alpha * (self._pending / dt - self._rate)
        self._pending = 0
        self._last = now

    @property
    def rate(self) -> float:
        self._fold()
        return self._rate

    @property
    def service_s(self) -> float:
        return self._service


def docling_floor(rate_per_s: float, service_s: float, *, static_min: int = 1, max_workers: int | None = None) -> int:
    """``max(static_min, ceil(rate x service / 0.55))`` conversions, capped at ``max_workers`` when given."""
    wanted = math.ceil(max(0.0, rate_per_s) * max(0.0, service_s) / TARGET_RHO - 1e-9)
    floor = max(static_min, wanted)
    return floor if max_workers is None else max(1, min(floor, max_workers))


@dataclass
class _Throttle:
    since: float | None = None  # first 429 of the current spell


class DoclingEngine:
    """Concurrency control for Docling conversions: a limit between 1 and ``max_workers``."""

    def __init__(
        self,
        *,
        max_workers: int,
        static_min: int = 1,
        floor_fn: Callable[[float, float], int] | None = None,
        demand: DemandEwma | None = None,
        increase_after: int = 8,
        fallback_after_s: float = 120.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be at least 1")
        self._max = max_workers
        self._static_min = max(1, min(static_min, max_workers))
        self._floor_fn = floor_fn
        self.demand = demand or DemandEwma(clock=clock)
        self._increase_after = increase_after
        self._fallback_after = fallback_after_s
        self._clock = clock
        self._limit = max_workers
        self._in_flight = 0
        self._calm = 0
        self._blocked_until = float("-inf")
        self._throttle = _Throttle()
        self.throttle_count = 0
        self.max_in_flight = 0

    # -- sizing ---------------------------------------------------------

    @property
    def floor(self) -> int:
        """The conversions the small-file path holds ahead of demand."""
        rate, svc = self.demand.rate, self.demand.service_s
        if self._floor_fn is not None:
            wanted = self._floor_fn(rate, svc)
        else:
            wanted = docling_floor(rate, svc, static_min=self._static_min)
        return max(self._static_min, min(wanted, self._max))

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def max_workers(self) -> int:
        return self._max

    @property
    def in_flight(self) -> int:
        return self._in_flight

    # -- throttle feedback ----------------------------------------------

    def on_throttled(self, retry_after_s: float | None = None) -> None:
        """A 429: halve the limit (min 1), hold new calls for ``Retry-After``."""
        now = self._clock()
        self.throttle_count += 1
        self._calm = 0
        self._limit = max(1, self._limit // 2)
        if self._throttle.since is None:
            self._throttle.since = now
        if retry_after_s is not None and retry_after_s > 0:
            self._blocked_until = max(self._blocked_until, now + min(retry_after_s, 60.0))

    def on_success(self) -> None:
        self._calm += 1
        self._throttle.since = None
        if self._calm >= self._increase_after and self._limit < self._max:
            self._calm = 0
            self._limit += 1

    def unavailable(self) -> bool:
        """Throttled continuously for ``fallback_after_s``: the caller should fall back for small work."""
        since = self._throttle.since
        return since is not None and self._clock() - since >= self._fallback_after

    # -- slots ----------------------------------------------------------

    @contextlib.asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        """One conversion. In-flight never exceeds the limit, which a 429 can lower under a running call."""
        while True:
            now = self._clock()
            if now >= self._blocked_until and self._in_flight < self._limit:
                break
            wait = 0.05
            if now < self._blocked_until:
                wait = min(0.25, self._blocked_until - now)
            await asyncio.sleep(wait)
        self._in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self._in_flight)
        started = self._clock()
        self.demand.arrival()
        try:
            yield
        finally:
            self._in_flight -= 1
            self.demand.service(self._clock() - started)


def docling_deadline_s(est_seconds: float, *, max_s: float = 120.0, min_s: float = 60.0) -> float:
    """``min(max_s, max(min_s, 4 x est))``, measured from the start of the conversion's execution."""
    return min(max_s, max(min_s, 4.0 * est_seconds))


@dataclass(frozen=True)
class EngineDecision:
    engine: Engine
    reason: str  # estimate | deadline | unavailable | recorded


def decide_engine(
    est: CostEstimate, *, recorded: EngineDecision | None = None, docling_unavailable: bool = False
) -> EngineDecision:
    """The engine for this (record, revision), decided once.

    A recorded decision is returned as it is, so a retry, a replay and a forced reindex of the same revision
    never call Docling again to find out. Without one, the estimate rule applies, except that a Docling that has
    been unavailable for long enough sends small work to the CPU engine.
    """
    if recorded is not None:
        return EngineDecision(recorded.engine, "recorded")
    if est.engine is Engine.DOCLING and docling_unavailable and est.cls is CostClass.SMALL:
        return EngineDecision(Engine.CPU, "unavailable")
    return EngineDecision(est.engine, "estimate")


_SHARED: list[DoclingEngine] = []


def shared_docling_engine() -> DoclingEngine:
    """The process's Docling limiter, sized by ``PARSE_DOCLING_WIDTH`` (default 8): the client's calls go through
    it, and a 429 lowers its limit and nothing else."""
    if not _SHARED:
        from app.utils.env_config import env_int

        _SHARED.append(DoclingEngine(max_workers=max(1, env_int("PARSE_DOCLING_WIDTH", 8))))
    return _SHARED[0]


def reset_shared_docling_engine() -> None:
    _SHARED.clear()
