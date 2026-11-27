"""PX-3 measurement: a tick-count loop-lag probe and phase-interval attribution (M0 to M5).

``LoopProbe`` runs a coroutine that sleeps 1 ms and stamps a preallocated array. A freeze is a gap
between two stamps well above 1 ms. Per run it reports the max single freeze, the number of freezes
over 50, 100 and 250 ms, the total time above 100 ms, p99 of the gaps and callbacks per second
(throughput of the loop). Unlike a sampler thread, it sees a C call that holds the GIL
(``json.loads``, ``pickle.loads``, a gen-2 collection) as what it is: a gap.

``PhaseRing`` records ``(name, start, end)`` for each parse phase in a preallocated ring (no
allocation on the hot path beyond the tuple) and ``attribute`` assigns each freeze to the phase
intervals it overlaps, read after the run. Phases are wrapped around the real functions with
``instrument`` so the parse itself is unchanged.

Stdlib only. Smoke: ``python -m tests.perf.parse_bench.loop_probe`` (parses a tiny payload with a
deliberate 150 ms stall, as the negative control showing the probe sees it).
"""
from __future__ import annotations

import asyncio
import functools
import inspect
import time
from array import array
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

TICK_SECONDS = 0.001
FREEZE_FLOOR_SECONDS = 0.010  # a gap above this is recorded as a freeze (tick is 1 ms)
THRESHOLDS_MS = (50, 100, 250)


@dataclass
class Freeze:
    start: float
    end: float

    @property
    def seconds(self) -> float:
        return self.end - self.start


class LoopProbe:
    """Use as ``async with LoopProbe() as probe:``; read ``probe.summary()`` afterwards."""

    def __init__(self, capacity: int = 2_000_000) -> None:
        self._stamps = array("d", bytes(8 * capacity))
        self._n = 0
        self._capacity = capacity
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self.started = 0.0
        self.stopped = 0.0

    async def _beat(self) -> None:
        stamps, cap = self._stamps, self._capacity
        clock = time.perf_counter
        while self._running:
            if self._n < cap:
                stamps[self._n] = clock()
                self._n += 1
            await asyncio.sleep(TICK_SECONDS)

    async def __aenter__(self) -> LoopProbe:
        self._running = True
        self.started = time.perf_counter()
        self._task = asyncio.create_task(self._beat())
        await asyncio.sleep(0)
        return self

    async def __aexit__(self, *exc: object) -> None:
        self._running = False
        assert self._task is not None
        await self._task
        self.stopped = time.perf_counter()
        if self._n < self._capacity:
            self._stamps[self._n] = self.stopped  # the last gap runs to the end of the window
            self._n += 1

    def gaps(self) -> list[float]:
        s = self._stamps
        return [s[i + 1] - s[i] for i in range(self._n - 1)]

    def freezes(self) -> list[Freeze]:
        s = self._stamps
        return [
            Freeze(s[i], s[i + 1]) for i in range(self._n - 1) if s[i + 1] - s[i] > FREEZE_FLOOR_SECONDS
        ]

    def summary(self) -> dict[str, Any]:
        gaps = sorted(self.gaps())
        freezes = self.freezes()
        wall = max(self.stopped - self.started, 1e-9)
        out: dict[str, Any] = {
            "wall_s": round(wall, 4),
            "ticks": self._n,
            "callbacks_per_s": round(self._n / wall, 1),
            "p99_gap_ms": round(gaps[int(0.99 * (len(gaps) - 1))] * 1000, 2) if gaps else 0.0,
            "max_freeze_ms": round(max((f.seconds for f in freezes), default=0.0) * 1000, 2),
            "total_above_100ms_ms": round(sum(f.seconds for f in freezes if f.seconds > 0.100) * 1000, 2),
        }
        for t in THRESHOLDS_MS:
            out[f"freezes_over_{t}ms"] = sum(1 for f in freezes if f.seconds * 1000 > t)
        return out


@dataclass
class PhaseRing:
    """Preallocated ring of phase intervals. Overwrites the oldest when full (reported)."""

    capacity: int = 100_000
    names: list[str | None] = field(init=False)
    starts: array = field(init=False)
    ends: array = field(init=False)
    n: int = 0

    def __post_init__(self) -> None:
        self.names = [None] * self.capacity
        self.starts = array("d", bytes(8 * self.capacity))
        self.ends = array("d", bytes(8 * self.capacity))

    def open(self, name: str) -> int:
        slot = self.n % self.capacity
        self.n += 1
        self.names[slot] = name
        self.starts[slot] = time.perf_counter()
        self.ends[slot] = 0.0
        return slot

    def close(self, slot: int) -> None:
        self.ends[slot] = time.perf_counter()

    @contextmanager
    def phase(self, name: str) -> Any:  # noqa: ANN401
        slot = self.open(name)
        try:
            yield
        finally:
            self.close(slot)

    @property
    def overwritten(self) -> int:
        return max(0, self.n - self.capacity)

    def intervals(self) -> list[tuple[str, float, float]]:
        return [
            (self.names[i], self.starts[i], self.ends[i] or time.perf_counter())  # type: ignore[misc]
            for i in range(min(self.n, self.capacity))
        ]


def attribute(freezes: Iterable[Freeze], ring: PhaseRing, floor_s: float = 0.0) -> dict[str, dict[str, float]]:
    """Per phase name: number of freezes it overlaps and the freeze time inside its intervals.

    A freeze overlapping several phases (a nested phase and its parent) counts for each; the
    overlap in seconds is the time of the freeze that falls inside the interval.
    """
    intervals = ring.intervals()
    out: dict[str, dict[str, float]] = {}
    for f in freezes:
        if f.seconds <= floor_s:
            continue
        hit = False
        for name, a, b in intervals:
            overlap = min(f.end, b) - max(f.start, a)
            if overlap > 0.001:  # under 1 ms is the tick, not the phase
                row = out.setdefault(name, {"freezes": 0, "overlap_ms": 0.0, "max_freeze_ms": 0.0})
                row["freezes"] += 1
                row["overlap_ms"] = round(row["overlap_ms"] + overlap * 1000, 2)
                row["max_freeze_ms"] = max(row["max_freeze_ms"], round(f.seconds * 1000, 2))
                hit = True
        if not hit:
            row = out.setdefault("(no parse phase: sink, GC or other)", {"freezes": 0, "overlap_ms": 0.0, "max_freeze_ms": 0.0})
            row["freezes"] += 1
            row["overlap_ms"] = round(row["overlap_ms"] + f.seconds * 1000, 2)
            row["max_freeze_ms"] = max(row["max_freeze_ms"], round(f.seconds * 1000, 2))
    return out


def instrument(ring: PhaseRing, target: Any, attr: str, name: str | None = None) -> Callable[[], None]:  # noqa: ANN401
    """Wrap ``target.attr`` (a function, sync or async) in a phase. Returns the undo function."""
    static = isinstance(inspect.getattr_static(target, attr), staticmethod)
    original = getattr(target, attr)
    label = name or f"{getattr(target, '__name__', type(target).__name__)}.{attr}"
    if inspect.iscoroutinefunction(original):

        @functools.wraps(original)
        async def awrapped(*a: Any, **k: Any) -> Any:  # noqa: ANN401
            slot = ring.open(label)
            try:
                return await original(*a, **k)
            finally:
                ring.close(slot)

        wrapped: Any = awrapped
    else:

        @functools.wraps(original)
        def swrapped(*a: Any, **k: Any) -> Any:  # noqa: ANN401
            slot = ring.open(label)
            try:
                return original(*a, **k)
            finally:
                ring.close(slot)

        wrapped = swrapped
    setattr(target, attr, staticmethod(wrapped) if static else wrapped)
    return lambda: setattr(target, attr, staticmethod(original) if static else original)


async def _smoke() -> dict[str, Any]:
    ring = PhaseRing(capacity=64)

    async def work() -> None:
        with ring.phase("sleepy_thread_work"):
            await asyncio.to_thread(time.sleep, 0.05)
        with ring.phase("stall_on_loop"):
            time.sleep(0.15)  # the deliberate freeze: the negative control

    async with LoopProbe(capacity=10_000) as probe:
        await work()
    return {"summary": probe.summary(), "attribution": attribute(probe.freezes(), ring, 0.1)}


if __name__ == "__main__":
    import json

    result = asyncio.run(_smoke())
    print(json.dumps(result, indent=1))
    assert result["summary"]["max_freeze_ms"] >= 140, "the probe did not see the 150 ms stall"
    assert "stall_on_loop" in result["attribution"], "the stall was not attributed to its phase"
