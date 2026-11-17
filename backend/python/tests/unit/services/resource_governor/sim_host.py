"""A deterministic host on which indexing and code sandboxes both adapt.

The real ``policy.next_limits`` (indexing) and the real ``live_limit.step``
(code sandboxes) run in lockstep on a one-second fake clock, with no files.
What the two controllers would otherwise read from a real machine is modelled:

* indexing in flight holds memory per permit (the policy's own working sets)
  while a sync keeps its pools saturated; memory it frees returns to the host
  over ``release_samples`` samples, because permits are never revoked and
  parse workers stay resident;
* each running sandbox uses ``run_used`` bytes (less than its full limit);
* chat users run code, wait for a sandbox, run for a while and think, all
  from one seeded ``random.Random``.

A run is admitted the way the sandbox governor admits it: below the live
limit, then only if free memory stays above the hard floor. A run that waits
for memory for ``YIELD_MIN_WAIT_S`` refreshes the yield signal at most every
``YIELD_REFRESH_S``; indexing reads it on its own sample.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from app.agent_loop_lib.sandbox.host_reading import HostSample
from app.agent_loop_lib.sandbox.live_limit import host_profile, initial_record, step
from app.agent_loop_lib.sandbox.live_settings import LiveSettings
from app.agent_loop_lib.sandbox.live_yield import YIELD_MIN_WAIT_S, YIELD_REFRESH_S
from app.services.resource_governor import policy
from app.services.resource_governor.models import (
    Ceilings,
    ControllerState,
    Limits,
    Pool,
    PoolDemand,
    ResourceSnapshot,
)

if TYPE_CHECKING:
    from app.agent_loop_lib.sandbox.live_record import LiveRecord

GiB = 2**30
MiB = 2**20
INTERVAL = 15
RUN_MEMORY = 512 * MiB
SLOT_WAIT_S = 120.0
YIELD_TTL_S = 20.0


@dataclass(frozen=True)
class Host:
    total: int
    cpus: float
    other_used: int  # stores, broker and everything else on the host
    app_base: int  # the app container without indexing's in-flight work
    app_limit: int  # the app container's memory limit, indexing's view
    ceilings: Ceilings
    sandbox_ceiling: int
    sandbox_floor: int = 2


HOST_16 = Host(
    total=16 * GiB, cpus=4, other_used=4 * GiB, app_base=2 * GiB, app_limit=12 * GiB,
    ceilings=Ceilings(heavy=3, light=12, index_heavy=8, index_light=24), sandbox_ceiling=6,
)
HOST_192 = Host(
    total=192 * GiB, cpus=48, other_used=16 * GiB, app_base=4 * GiB, app_limit=96 * GiB,
    ceilings=Ceilings(heavy=24, light=96, index_heavy=64, index_light=128), sandbox_ceiling=72,
)


@dataclass(frozen=True)
class Scenario:
    host: Host
    hours: float = 2.0
    sync: bool = True
    users: int = 0
    # Extra users during [burst_start, burst_end) seconds.
    burst_users: int = 0
    burst_start: float = 0.0
    burst_end: float = 0.0
    run_mean_s: float = 15.0
    think_mean_s: float = 45.0
    run_used: int = 300 * MiB
    release_samples: int = 4
    yield_enabled: bool = True
    seed: int = 7


def users_for(share_of_ceiling: float, host: Host, run_mean_s: float = 15.0, think_mean_s: float = 45.0) -> int:
    """Users whose runs keep about *share_of_ceiling* of the ceiling busy."""
    return max(1, round(share_of_ceiling * host.sandbox_ceiling * (run_mean_s + think_mean_s) / run_mean_s))


@dataclass
class Result:
    index_limits: list[int] = field(default_factory=list)  # index_heavy per indexing sample
    final_limits: dict[Pool, int] = field(default_factory=dict)
    live_limits: list[int] = field(default_factory=list)
    yield_episodes: int = 0
    waits: list[float] = field(default_factory=list)
    refused: int = 0
    unsafe_starts: int = 0

    def p95_wait(self) -> float:
        if not self.waits:
            return 0.0
        ordered = sorted(self.waits)
        return ordered[min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1)]


def reversals_per_window(values: list[int], window: int) -> int:
    """The most direction changes within any *window* consecutive samples."""
    turns: list[int] = []
    direction = 0
    for i in range(1, len(values)):
        delta = values[i] - values[i - 1]
        if delta == 0:
            continue
        sign = 1 if delta > 0 else -1
        if direction and sign != direction:
            turns.append(i)
        direction = sign
    worst, start = 0, 0
    for end, turn in enumerate(turns):
        while turn - turns[start] >= window:
            start += 1
        worst = max(worst, end - start + 1)
    return worst


@dataclass
class _User:
    next_at: float
    waiting_since: float | None = None
    running_until: float | None = None


def _indexing_bytes(limits: Limits) -> float:
    return GiB * (
        limits.get(Pool.HEAVY_PARSE) * policy.HEAVY_PARSE_WORKING_SET_GB
        + limits.get(Pool.INDEX_HEAVY) * policy.INDEX_HEAVY_WORKING_SET_GB
        + limits.get(Pool.INDEX_LIGHT) * policy.INDEX_LIGHT_WORKING_SET_GB
    )


def run(scenario: Scenario) -> Result:
    host = scenario.host
    rng = random.Random(scenario.seed)
    profile = host_profile(LiveSettings())
    m = float(RUN_MEMORY)
    hard_floor, _ = profile.memory_lines(host.total, m)
    result = Result()

    limits = policy.warm_start_limits(host.ceilings)
    state = ControllerState.initial()
    index_used = _indexing_bytes(limits) if scenario.sync else 0.0
    rec: LiveRecord | None = None
    yield_until = -math.inf
    refreshed_at = -math.inf
    users: list[_User] = []
    running: list[float] = []  # end times

    def available() -> int:
        used = host.other_used + host.app_base + index_used + len(running) * scenario.run_used
        return int(host.total - used)

    def ensure_users(now: float) -> None:
        wanted = scenario.users + (
            scenario.burst_users if scenario.burst_start <= now < scenario.burst_end else 0
        )
        while len(users) < wanted:
            users.append(_User(next_at=now + rng.expovariate(1 / scenario.think_mean_s)))
        # Burst users leave once idle.
        while len(users) > wanted:
            idle = [u for u in users if u.waiting_since is None and u.running_until is None]
            if not idle:
                break
            users.remove(idle[-1])

    seconds = int(scenario.hours * 3600)
    for now in range(seconds):
        t = float(now)
        running = [end for end in running if end > t]
        for user in users:
            if user.running_until is not None and user.running_until <= t:
                user.running_until = None
                user.next_at = t + rng.expovariate(1 / scenario.think_mean_s)
        ensure_users(t)
        for user in users:
            if user.waiting_since is None and user.running_until is None and user.next_at <= t:
                user.waiting_since = t

        limit = host.sandbox_ceiling if rec is None or not rec.initialised else rec.limit
        for user in sorted((u for u in users if u.waiting_since is not None), key=lambda u: u.waiting_since):
            waited = t - user.waiting_since
            reason = None
            if len(running) >= limit:
                if limit >= host.sandbox_ceiling:
                    reason = "slots_full"
                elif rec is not None and rec.shrink_cause == "cpu":
                    reason = "live_cpu"
                else:
                    reason = "live_memory"
            elif available() - m < hard_floor:
                reason = "host_memory_floor"
            if reason is None:
                result.waits.append(waited)
                runtime = max(1.0, rng.expovariate(1 / scenario.run_mean_s))
                running.append(t + runtime)
                user.waiting_since, user.running_until = None, t + runtime
                if available() < hard_floor:
                    result.unsafe_starts += 1
                continue
            if waited >= SLOT_WAIT_S:
                result.waits.append(SLOT_WAIT_S)
                result.refused += 1
                user.waiting_since = None
                user.next_at = t + rng.expovariate(1 / scenario.think_mean_s)
                continue
            if rec is not None and rec.initialised:
                field_name = "last_wait_mem_at" if reason == "host_memory_floor" else "last_wait_limit_at"
                rec = replace(rec, **{field_name: t})
            for_memory = reason == "host_memory_floor" or (
                reason == "live_memory" and rec is not None and rec.target is not None and rec.target <= rec.limit
            )
            if (
                scenario.yield_enabled and for_memory and waited >= YIELD_MIN_WAIT_S
                and t - refreshed_at >= YIELD_REFRESH_S
            ):
                if t >= yield_until:
                    result.yield_episodes += 1
                yield_until, refreshed_at = t + YIELD_TTL_S, t

        if now % INTERVAL == 0:
            sample = HostSample(
                mem_total=host.total, mem_available=available(), source="host", cpu_util=0.3,
                cpu_psi_some_avg10=0.0,
            )
            kwargs = {
                "profile": profile, "m": m, "ceiling": host.sandbox_ceiling, "floor": host.sandbox_floor,
                "held": len(running), "now": t,
            }
            rec = initial_record(rec, sample, pid=1, **kwargs) if rec is None else step(rec, sample, **kwargs)
            result.live_limits.append(rec.limit)

        if now % INTERVAL == 7:
            demand = {
                pool: PoolDemand(permit_seconds=limits.get(pool) * INTERVAL, blocked_acquires=3, completions=10)
                if scenario.sync else PoolDemand.empty()
                for pool in Pool
            }
            working_set = host.app_base + index_used
            snap = ResourceSnapshot(
                cpu_quota=host.cpus, cpu_utilisation=0.5, cpu_throttled_ratio=0.0, cpu_pressure=0.0,
                mem_limit_bytes=host.app_limit, mem_working_set_bytes=int(working_set),
                mem_working_set_raw_bytes=int(working_set), source="sim",
            )
            limits, state = policy.next_limits(
                limits, snap, host.ceilings, state, demand, now=t, interval=INTERVAL,
                host_yield=t < yield_until,
            )
            result.index_limits.append(limits.get(Pool.INDEX_HEAVY))

        if scenario.sync:
            wanted = _indexing_bytes(limits)
            if wanted >= index_used:
                index_used = wanted
            else:
                index_used -= (index_used - wanted) / (scenario.release_samples * INTERVAL)

    result.final_limits = {pool: limits.get(pool) for pool in Pool}
    return result
