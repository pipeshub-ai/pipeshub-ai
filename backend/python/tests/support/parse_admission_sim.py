"""A discrete-event model of one parse pool under ``next_grant``, for the policy property tests.

One pool of ``P`` tokens (``H`` of them held for small work), Poisson arrivals per class, bounded service times,
optional hand-backs of big waiters that keep (or, as a deliberately broken variant, reset) their first-seen time.
The model calls the real ``next_grant``; it records what a gate must never do.
"""
from __future__ import annotations

import heapq
import itertools
import math
import random
from dataclasses import dataclass, field

from app.modules.parse_engine.admission import AdmissionNumbers, ClassDemand, next_grant
from app.modules.parse_engine.cost import CostClass

S, L, H_ = CostClass.SMALL, CostClass.LARGE, CostClass.HUGE
BIG = (L, H_)


@dataclass
class World:
    capacity: int
    held: int = 0
    small_rate: float = 0.05  # arrivals per second
    small_service: float = 10.0
    large_rate: float = 0.02
    large_service: float = 120.0
    huge_rate: float = 0.0
    huge_service: float = 280.0
    unbounded_share: float = 0.0  # of large arrivals
    horizon: float = 20_000.0
    handback_after_s: float | None = None
    handback_keeps_first_seen: bool = True
    drops: tuple[tuple[float, int], ...] = ()  # (time, new capacity)
    numbers: AdmissionNumbers = field(default_factory=AdmissionNumbers)
    seed: int = 0
    small_burst: bool = False


@dataclass
class _Job:
    cls: CostClass
    service: float
    bounded: bool
    first_seen: float
    queued_at: float = 0.0
    head_since: float | None = None


@dataclass
class SimResult:
    violations: list[str] = field(default_factory=list)
    max_big_head_wait: float = 0.0
    max_service: float = 0.0
    big_granted: int = 0
    small_granted: int = 0
    big_running_samples: list[int] = field(default_factory=list)
    rule_counts: dict[str, int] = field(default_factory=dict)


def simulate(w: World) -> SimResult:
    rng = random.Random(w.seed)
    res = SimResult()
    now = 0.0
    cap = w.capacity
    seq = itertools.count()
    ev: list[tuple[float, int, str, object]] = []
    queues: dict[CostClass, list[_Job]] = {c: [] for c in CostClass}
    running: list[_Job] = []
    last_small = -math.inf

    def push(t: float, kind: str, payload: object = None) -> None:
        heapq.heappush(ev, (t, next(seq), kind, payload))

    def arrivals(cls: CostClass, rate: float) -> None:
        if rate > 0:
            push(now + rng.expovariate(rate), "arrive", cls)

    arrivals(S, w.small_rate)
    arrivals(L, w.large_rate)
    arrivals(H_, w.huge_rate)
    for t, c in w.drops:
        push(t, "cap", c)
    res.max_service = max(w.small_service, w.large_service, w.huge_service if w.huge_rate else 0.0) * 1.5

    def used(cls: CostClass | None = None) -> int:
        return sum(1 for j in running if cls is None or j.cls is cls)

    def oldest_big() -> _Job | None:
        cands = [j for c in BIG for j in queues[c]]
        return min(cands, key=lambda j: j.first_seen) if cands else None

    def mark_heads() -> None:
        j = oldest_big()
        if j is not None and j.head_since is None:
            j.head_since = now

    def pump() -> None:
        nonlocal last_small
        while True:
            d = {}
            for c in CostClass:
                q = queues[c]
                oldest = max((now - j.first_seen for j in q), default=0.0)
                bounded = any(j.bounded for j in q) if c in BIG else True
                d[c] = ClassDemand(used(c), len(q), oldest, bounded)
            if used(S) or queues[S]:
                last_small = now
            g = next_grant(w.numbers, cap, d, since_small_s=now - last_small, held_floors=w.held)
            if g is None:
                return
            if used() >= cap:
                res.violations.append(f"grant past capacity at {now:.0f}")
                return
            q = queues[g.cls]
            if g.rule in ("A2", "A4"):
                job = min((j for j in q if j.bounded), key=lambda j: j.first_seen, default=None)
                if job is None:
                    res.violations.append(f"{g.rule} with no bounded head")
                    return
            else:
                job = min(q, key=lambda j: j.first_seen)
            if g.cls in BIG:
                if g.rule == "A4" and queues[S]:
                    res.violations.append(f"A4 while small waits at {now:.0f}")
                if g.rule in ("A2", "A4") and used(L) + used(H_) + 1 > max(0, cap - w.held):
                    res.violations.append(f"{g.rule} took a held floor at {now:.0f}")
                if g.rule == "A4" and not job.bounded:
                    res.violations.append("A4 granted an unbounded job")
                if job.head_since is not None:
                    res.max_big_head_wait = max(res.max_big_head_wait, now - job.head_since)
                res.big_granted += 1
            else:
                res.small_granted += 1
            res.rule_counts[g.rule] = res.rule_counts.get(g.rule, 0) + 1
            q.remove(job)
            running.append(job)
            push(now + job.service, "done", job)
            mark_heads()

    while ev:
        now, _, kind, payload = heapq.heappop(ev)
        if now > w.horizon:
            break
        if kind == "arrive":
            cls = payload  # type: ignore[assignment]
            rate, mean = {
                S: (w.small_rate, w.small_service),
                L: (w.large_rate, w.large_service),
                H_: (w.huge_rate, w.huge_service),
            }[cls]
            service = min(mean * 1.5, max(1.0, rng.expovariate(1 / mean)))
            bounded = not (cls is L and rng.random() < w.unbounded_share)
            queues[cls].append(_Job(cls, service, bounded, now, now))
            arrivals(cls, rate * (4 if (w.small_burst and cls is S and (now // 600) % 2 == 0) else 1))
            if w.small_burst and cls is S:
                pass
        elif kind == "done":
            running.remove(payload)  # type: ignore[arg-type]
        elif kind == "cap":
            cap = payload  # type: ignore[assignment]
        if w.handback_after_s is not None:
            for c in BIG:
                if queues[c]:
                    job = min(queues[c], key=lambda j: j.first_seen)
                    if now - job.queued_at >= w.handback_after_s:
                        job.queued_at = now
                        if not w.handback_keeps_first_seen:
                            job.first_seen = now
        mark_heads()
        pump()
        res.big_running_samples.append(used(L) + used(H_))
    j = oldest_big()
    if j is not None and j.head_since is not None:
        res.max_big_head_wait = max(res.max_big_head_wait, w.horizon - j.head_since)  # still waiting at the end
    return res
