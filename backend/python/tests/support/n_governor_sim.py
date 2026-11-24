"""N governors on one container: the real ``next_limits``, the real elected controller and ledger, one shared model.

Only heavy parses carry load. A unit runs ``HOLD_S`` seconds and allocates gradually (7 % of its working set
at the first sample, linear to all of it over ``RAMP_S``); permits are never revoked, so a limit that shrinks
only stops new admissions. CPU use is one core per running unit.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.capacity.ceilings import auto_ceilings, per_cell
from app.capacity.controller import ElectedController
from app.capacity.member_budget import MemberBudget
from app.services.resource_governor.container_link import ContainerLink
from app.services.resource_governor.models import (
    Ceilings,
    ControllerState,
    Limits,
    Pool,
    PoolDemand,
    ResourceSnapshot,
)
from app.services.resource_governor.policy import next_limits, warm_start_limits

if TYPE_CHECKING:
    from pathlib import Path

GIB = 1 << 30
INTERVAL = 15.0
HOLD_S = 120.0
RAMP_S = 60.0


@dataclass
class Cell:
    index: int
    ceilings: Ceilings
    limits: Limits
    state: ControllerState = field(default_factory=ControllerState.initial)
    units: deque = field(default_factory=deque)  # start times
    loaded: bool = True
    link: ContainerLink | None = None
    done: int = 0

    @property
    def running(self) -> int:
        return len(self.units)


@dataclass
class Result:
    ticks: list[dict]

    def heavy_total(self) -> list[int]:
        return [t["heavy_total"] for t in self.ticks]

    def peak_raw(self) -> float:
        return max(t["mem_raw"] for t in self.ticks)

    def mean_util(self, last: int = 20) -> float:
        tail = self.ticks[-last:]
        return sum(t["cpu_util"] for t in tail) / len(tail)

    def amplitude(self, last: int = 40) -> int:
        tail = self.heavy_total()[-last:]
        return max(tail) - min(tail)

    def first_at_or_above(self, level: float) -> int | None:
        for i, t in enumerate(self.ticks):
            if t["heavy_total"] >= level:
                return i
        return None


def _fleet(cpus: int, cells: int) -> Ceilings:
    f = auto_ceilings(float(cpus), None, None, cells=cells, reserve_embedding_cpus=True)
    return Ceilings(*f.as_tuple())


def run(
    cells: int,
    cpus: int,
    mem_gb: float,
    ticks: int,
    *,
    ws_real_gb: float = 1.5,
    base_gb: float = 5.0,
    directory: Path | None = None,
    idle_controller: bool = False,
    use_link: bool | None = None,
    heavy_working_set_gb: float = 1.5,
) -> Result:
    """``cells == 1`` and ``use_link`` False is the single-governor reference."""
    use_link = cells > 1 if use_link is None else use_link
    fleet = _fleet(cpus, cells)
    per = per_cell(fleet, cells) if cells > 1 else fleet
    per_c = Ceilings(per.heavy, per.light, per.index_heavy, per.index_light)
    members: list[Cell] = []
    budgets: list[MemberBudget] = []
    for i in range(cells):
        c = Cell(i, per_c, warm_start_limits(per_c))
        if use_link:
            assert directory is not None
            b = MemberBudget(directory, cpu_tokens=cpus, mem_bytes=int(mem_gb * GIB), llm_cap=24, service=f"cell{i}")
            budgets.append(b)
            c.link = ContainerLink(b, ElectedController(b, INTERVAL), fleet, cells)
        members.append(c)
    if idle_controller:
        members[0].loaded = False
        assert members[0].link is not None
        members[0].link.controller.tick(lambda s, d: {})  # the idle process is the elected one from the start
    out: list[dict] = []
    now = 1000.0
    for _ in range(ticks):
        now += INTERVAL
        now_ms = int(now * 1000)
        # units finish; loaded cells start new ones up to their limit (a limit never revokes a running unit)
        completed = {}
        for c in members:
            n_done = 0
            while c.units and now - c.units[0] >= HOLD_S:
                c.units.popleft()
                n_done += 1
            completed[c.index] = n_done
            if c.loaded:
                while c.running < c.limits.get(Pool.HEAVY_PARSE):
                    c.units.append(now)
        total_running = sum(c.running for c in members)
        ws = base_gb
        for c in members:
            for started in c.units:
                age = now - started
                ws += ws_real_gb * min(1.0, 0.07 + 0.93 * age / RAMP_S)
        snap = ResourceSnapshot(
            cpu_quota=float(cpus), cpu_utilisation=min(1.0, total_running / cpus), cpu_throttled_ratio=0.0,
            cpu_pressure=0.0, mem_limit_bytes=int(mem_gb * GIB), mem_working_set_bytes=int(ws * GIB), source="sim",
        )
        for c in members:
            demand = {p: PoolDemand() for p in Pool}
            if c.loaded:
                demand[Pool.HEAVY_PARSE] = PoolDemand(
                    permit_seconds=c.running * INTERVAL, blocked_acquires=1 if c.running >= c.limits.get(Pool.HEAVY_PARSE) else 0,
                    completions=completed[c.index], max_in_use=c.running,
                )
            own_limits, own_state = next_limits(
                current=c.limits, snap=snap, ceilings=c.ceilings, state=c.state, demand=demand, now=now,
                interval=INTERVAL, worker_count=cells, heavy_working_set_gb=heavy_working_set_gb,
            )
            if c.link is not None:
                own_limits, own_state = c.link.step(
                    own=(own_limits, own_state), snap=snap, ceilings=c.ceilings, demand=demand, now=now,
                    interval=INTERVAL, feedback=None, heavy_working_set_gb=heavy_working_set_gb, now_ms=now_ms,
                )
            c.limits, c.state = own_limits, own_state
        out.append({
            "t": now, "heavy_total": sum(c.limits.get(Pool.HEAVY_PARSE) for c in members),
            "running": total_running, "cpu_util": min(1.0, total_running / cpus),
            "mem_raw": ws / mem_gb,
        })
    for b in budgets:
        b.close()
    return Result(out)


__all__ = ["Result", "run"]
