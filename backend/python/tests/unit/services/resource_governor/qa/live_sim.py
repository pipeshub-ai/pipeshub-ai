"""A closed loop of the real live limit, its per-run check and the real yield.

Unlike ``sim_host`` (the pure step and policy only), this drives the real
``LiveLimit`` (in-process record, fake clock): ``sample_once`` every 15 s,
``view`` and ``_admit_locked`` with their burst charges for every start, and
``waiting`` feeding the real ``IndexingYield`` writer on a real
``YieldSignal`` file. One or more indexing workers run the real
``policy.next_limits`` with ``host_yield`` read back from that file.

The host adds what the other model leaves out: a run's memory comes in over
``ramp_s`` seconds after it starts (a container starting), so a burst is
only caught by the charges; memory used outside the app can grow (a leak);
chat can arrive in bursts. ``overcommitted`` counts starts after which the
host, once every running run's memory is in, would sit below the hard floor.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.agent_loop_lib.sandbox.capacity import (
    CapacitySettings,
    HoldReason,
    MachineSize,
    SandboxCapacity,
)
from app.agent_loop_lib.sandbox.host_reading import HostSample, LiveMode
from app.agent_loop_lib.sandbox.live_limit import LiveLimit
from app.agent_loop_lib.sandbox.live_settings import LiveSettings
from app.agent_loop_lib.sandbox.live_yield import IndexingYield
from app.services.resource_governor import policy
from app.services.resource_governor.models import (
    Ceilings,
    ControllerState,
    Limits,
    Pool,
    PoolDemand,
    ResourceSnapshot,
)
from app.services.resource_governor.yield_signal import YieldSignal

if TYPE_CHECKING:
    from pathlib import Path

GiB = 2**30
MiB = 2**20
INTERVAL = 15
SLOT_WAIT_S = 120.0


@dataclass(frozen=True)
class Host:
    total: int
    cpus: float
    other_used: int
    app_base: int
    app_limit: int
    # The pools for every indexing worker together.
    ceilings: Ceilings


HOST_4 = Host(
    total=4 * GiB, cpus=2, other_used=int(1.2 * GiB), app_base=int(1.0 * GiB), app_limit=3 * GiB,
    ceilings=Ceilings(heavy=1, light=2, index_heavy=2, index_light=4),
)
HOST_16 = Host(
    total=16 * GiB, cpus=4, other_used=4 * GiB, app_base=2 * GiB, app_limit=12 * GiB,
    ceilings=Ceilings(heavy=3, light=12, index_heavy=8, index_light=24),
)
HOST_192 = Host(
    total=192 * GiB, cpus=48, other_used=16 * GiB, app_base=4 * GiB, app_limit=96 * GiB,
    ceilings=Ceilings(heavy=24, light=96, index_heavy=64, index_light=128),
)


@dataclass(frozen=True)
class Scenario:
    host: Host
    hours: float = 1.0
    sync: bool = True
    users: int = 0
    # (start_s, end_s, extra users)
    bursts: tuple[tuple[float, float, int], ...] = ()
    leak_bytes: int = 0
    index_workers: int = 1
    run_used: int = 300 * MiB
    ramp_s: float = 3.0
    run_mean_s: float = 15.0
    think_mean_s: float = 45.0
    release_samples: int = 4
    seed: int = 7
    overcommit: float = 1.0
    # With several indexing workers: hold their heavy parses together to one
    # worker's floor while the yield or its holdoff lasts, as
    # ``ResourceGovernor.try_acquire_heavy_parse_slots`` does.
    yield_heavy_cap: bool = True


@dataclass
class Result:
    ceiling: int = 0
    floor: int = 0
    live_limits: list[int] = field(default_factory=list)
    index_limits: list[int] = field(default_factory=list)
    # Heavy parses the workers may run together, per indexing sample.
    heavy_totals: list[int] = field(default_factory=list)
    yield_episodes: int = 0
    waits: list[float] = field(default_factory=list)
    starts: int = 0
    refused: int = 0
    overcommitted: int = 0
    min_available: int = 2**62

    def p95_wait(self) -> float:
        if not self.waits:
            return 0.0
        ordered = sorted(self.waits)
        return ordered[min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1)]


@dataclass
class _User:
    next_at: float
    waiting_since: float | None = None
    running_until: float | None = None


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _Reader:
    source = "host"

    def __init__(self, available: object, total: int) -> None:
        self._available, self._total = available, total

    def read(self) -> HostSample:
        return HostSample(
            mem_total=self._total, mem_available=self._available(), cpu_util=0.3, cpu_psi_some_avg10=0.0,  # type: ignore[operator]
            source="host",
        )

    def memory(self) -> tuple[int | None, int | None]:
        return self._available(), self._total  # type: ignore[operator]


def _in_flight(index: list[Limits], fleet: Ceilings, heavy_cap: int | None) -> dict[Pool, int]:
    """What the workers run together: each up to its own limit, and all of
    them up to the shared heavy slots and the fleet-wide index leases."""
    total = {pool: sum(limits.get(pool) for limits in index) for pool in Pool}
    if len(index) > 1:
        total[Pool.INDEX_HEAVY] = min(total[Pool.INDEX_HEAVY], fleet.index_heavy)
        total[Pool.INDEX_LIGHT] = min(total[Pool.INDEX_LIGHT], fleet.index_light)
        if heavy_cap is not None:
            total[Pool.HEAVY_PARSE] = min(total[Pool.HEAVY_PARSE], heavy_cap)
    return total


def _indexing_bytes(total: dict[Pool, int]) -> float:
    return GiB * (
        total[Pool.HEAVY_PARSE] * policy.HEAVY_PARSE_WORKING_SET_GB
        + total[Pool.INDEX_HEAVY] * policy.INDEX_HEAVY_WORKING_SET_GB
        + total[Pool.INDEX_LIGHT] * policy.INDEX_LIGHT_WORKING_SET_GB
    )


def run(scenario: Scenario, state_dir: Path) -> Result:
    host = scenario.host
    rng = random.Random(scenario.seed)
    clock = _Clock()
    start = clock.now
    seconds = int(scenario.hours * 3600)
    workers = scenario.index_workers

    # Each worker gets its share of the pools, as the controller divides them.
    share = policy.split_ceilings(host.ceilings, workers)
    index = [policy.warm_start_limits(share) for _ in range(workers)]
    last_snap: ResourceSnapshot | None = None

    def heavy_cap(yield_on: bool) -> int:
        cap = policy.cgroup_heavy_parse_limit(last_snap, host.ceilings) if last_snap else host.ceilings.heavy
        held = yield_on or any(st.get(Pool.HEAVY_PARSE).yield_holdoff > 0 for st in states)
        if scenario.yield_heavy_cap and held:
            cap = min(cap, policy.floor_for(Pool.HEAVY_PARSE, host.ceilings.heavy))
        return cap

    states = [ControllerState.initial() for _ in range(workers)]
    index_used = _indexing_bytes(_in_flight(index, host.ceilings, None)) if scenario.sync else 0.0
    runs: list[tuple[float, float]] = []  # (start, end)
    users: list[_User] = []

    def other() -> float:
        return host.other_used + scenario.leak_bytes * (clock.now - start) / max(1, seconds)

    def run_memory(t: float, *, settled: bool = False) -> float:
        return sum(
            scenario.run_used * (1.0 if settled else min(1.0, (t - began) / scenario.ramp_s))
            for began, _ in runs
        )

    def available(*, settled: bool = False) -> int:
        return int(host.total - other() - host.app_base - index_used - run_memory(clock.now, settled=settled))

    capacity = SandboxCapacity("compose", CapacitySettings(), slot_files=256)
    capacity._apply(MachineSize(host.total, host.cpus, None, "docker_info"), None)
    mode = LiveMode(mode="on:host", reader=_Reader(available, host.total), profile="host")  # type: ignore[arg-type]
    settings = LiveSettings(memory_overcommit=scenario.overcommit)
    live = LiveLimit(capacity, None, settings=settings, resolver=lambda: mode, directory=None, clock=clock)
    live.apply_mode(mode)
    signal = YieldSignal(state_dir, clock=clock)
    yielder = IndexingYield(live, signal, settings, clock=clock)
    live.on_memory_wait = yielder.on_memory_wait
    hard_floor = live.profile.memory_lines(host.total, live._run_bytes(1.0))[0]  # type: ignore[union-attr]
    result = Result(ceiling=capacity.ceiling(), floor=capacity.floor())
    was_active = False

    def wanted_users(t: float) -> int:
        return scenario.users + sum(extra for begin, end, extra in scenario.bursts if begin <= t - start < end)

    try:
        for _ in range(seconds):
            clock.now += 1.0
            t = clock.now
            runs = [(began, end) for began, end in runs if end > t]
            for user in users:
                if user.running_until is not None and user.running_until <= t:
                    user.running_until = None
                    user.next_at = t + rng.expovariate(1 / scenario.think_mean_s)
            while len(users) < wanted_users(t):
                users.append(_User(next_at=t + rng.expovariate(1 / scenario.think_mean_s)))
            while len(users) > wanted_users(t):
                idle = [u for u in users if u.waiting_since is None and u.running_until is None]
                if not idle:
                    break
                users.remove(idle[-1])
            for user in users:
                if user.waiting_since is None and user.running_until is None and user.next_at <= t:
                    user.waiting_since = t

            for user in sorted((u for u in users if u.waiting_since is not None), key=lambda u: u.waiting_since):
                waited = t - user.waiting_since  # type: ignore[operator]
                view = live.view()
                reason: HoldReason | None
                if len(runs) >= view.limit:
                    reason = (
                        HoldReason.SLOTS_FULL if view.limit >= view.ceiling
                        else HoldReason.LIVE_CPU if view.shrink_cause == "cpu" else HoldReason.LIVE_MEMORY
                    )
                else:
                    reason = live._admit_locked()
                if reason is None:
                    result.waits.append(waited)
                    result.starts += 1
                    runtime = max(1.0, rng.expovariate(1 / scenario.run_mean_s))
                    runs.append((t, t + runtime))
                    user.waiting_since, user.running_until = None, t + runtime
                    if available(settled=True) < hard_floor:
                        result.overcommitted += 1
                    continue
                if waited >= SLOT_WAIT_S:
                    result.waits.append(SLOT_WAIT_S)
                    result.refused += 1
                    user.waiting_since = None
                    user.next_at = t + rng.expovariate(1 / scenario.think_mean_s)
                    continue
                live.waiting(reason, waited)

            if (t - start) % INTERVAL == 0:
                live.sample_once()
                result.live_limits.append(live.view(fresh=True).limit)

            active = signal.read_state(t).active
            result.yield_episodes += active and not was_active
            was_active = active
            for k in range(workers):
                if (t - start) % INTERVAL != (7 + 3 * k) % INTERVAL:
                    continue
                demand = {
                    pool: PoolDemand(permit_seconds=index[k].get(pool) * INTERVAL, blocked_acquires=3, completions=10)
                    if scenario.sync else PoolDemand.empty()
                    for pool in Pool
                }
                working_set = host.app_base + index_used
                snap = last_snap = ResourceSnapshot(
                    cpu_quota=host.cpus, cpu_utilisation=0.5, cpu_throttled_ratio=0.0, cpu_pressure=0.0,
                    mem_limit_bytes=host.app_limit, mem_working_set_bytes=int(working_set),
                    mem_working_set_raw_bytes=int(working_set), source="sim",
                )
                index[k], states[k] = policy.next_limits(
                    index[k], snap, share, states[k], demand, now=t, interval=INTERVAL,
                    worker_count=workers, host_yield=active,
                )
                if k == 0:
                    flight = _in_flight(index, host.ceilings, heavy_cap(active))
                    result.index_limits.append(flight[Pool.INDEX_HEAVY])
                    result.heavy_totals.append(flight[Pool.HEAVY_PARSE])

            if scenario.sync:
                wanted = _indexing_bytes(_in_flight(index, host.ceilings, heavy_cap(active)))
                if wanted >= index_used:
                    index_used = wanted
                else:
                    index_used -= (index_used - wanted) / (scenario.release_samples * INTERVAL)
            result.min_available = min(result.min_available, available())
    finally:
        signal.close()
    return result
