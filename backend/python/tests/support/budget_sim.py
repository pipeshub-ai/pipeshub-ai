"""A discrete-time model of one member's budget driven by the real ``admit`` and ``next_grant``."""

from __future__ import annotations

import random
from collections import defaultdict, deque
from dataclasses import dataclass, field

from app.capacity.budget_policy import (
    AdmissionPolicy,
    Demand,
    Family,
    Floors,
    LedgerState,
    WaiterHead,
    admit,
    may_take,
    next_grant,
)

GIB = 2**30


@dataclass
class Stream:
    """Poisson arrivals of one kind of unit; ``service`` is seconds held, or ``(low, high)``."""

    name: str
    demand: Demand
    rate: float  # per second; 0 with ``always_waiting`` keeps ``depth`` units queued
    service: tuple[float, float]
    always_waiting: int = 0


@dataclass
class Sim:
    cpu_tokens: int
    floors: Floors
    streams: list[Stream]
    policy: AdmissionPolicy = field(default_factory=AdmissionPolicy)
    seed: int = 1
    dt: float = 0.05
    mem_avail: int = 1 << 60

    def run(self, seconds: float, *, on_step=None) -> dict[str, list[float]]:
        rng = random.Random(self.seed)
        state = LedgerState()
        now = 0.0
        ticket = 0
        queues: dict[str, deque] = defaultdict(deque)  # stream name -> (arrival, head)
        running: list[tuple[float, Demand]] = []
        waits: dict[str, list[float]] = defaultdict(list)
        by_name = {s.name: s for s in self.streams}
        peak_total = 0

        def enqueue(s: Stream, t: float) -> None:
            nonlocal ticket
            ticket += 1
            d = s.demand
            queues[s.name].append((t, WaiterHead(ticket, d.cls, d.family, d.cpu, d.mem_bytes, d.est_seconds, int(t * 1000))))

        for s in self.streams:
            for _ in range(s.always_waiting):
                enqueue(s, 0.0)
        steps = int(seconds / self.dt)
        for _ in range(steps):
            now += self.dt
            still = []
            for end, d in running:
                if end <= now:
                    key = (d.family, d.cls)
                    state.used[key] = state.used.get(key, 0) - d.cpu
                    if state.used[key] == 0:
                        del state.used[key]
                else:
                    still.append((end, d))
            running = still
            for s in self.streams:
                if s.rate and rng.random() < s.rate * self.dt:
                    enqueue(s, now)
            progress = True
            while progress:
                progress = False
                state.heads = [q[0][1] for q in queues.values() if q]
                plan = next_grant(state, self.floors, self.policy, self.cpu_tokens, now)
                for head in plan.order:
                    name = next(n for n, q in queues.items() if q and q[0][1] is head)
                    s = by_name[name]
                    if not may_take(state, head, plan, self.cpu_tokens):
                        continue
                    if not admit(state, s.demand, self.floors, self.policy, self.cpu_tokens, self.mem_avail, now).ok:
                        continue
                    arrival, _ = queues[name].popleft()
                    waits[name].append(now - arrival)
                    key = (s.demand.family, s.demand.cls)
                    state.used[key] = state.used.get(key, 0) + s.demand.cpu
                    if s.demand.cls == "small" or s.demand.family in (Family.INSPECT, Family.QUERY_EMBED):
                        state.last_critical_ms = int(now * 1000)
                    running.append((now + rng.uniform(*s.service), s.demand))
                    if s.always_waiting:
                        enqueue(s, now)
                    progress = True
                    break
            peak_total = max(peak_total, state.total())
            if on_step:
                on_step(state, now)
        waits["_peak_total"] = [float(peak_total)]
        return waits


def p95(xs: list[float]) -> float:
    xs = sorted(xs)
    return xs[int(0.95 * (len(xs) - 1))] if xs else 0.0
