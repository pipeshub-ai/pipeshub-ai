"""Pure control-law functions: no I/O, no clock reads.

Callers (``controller.py``) inject the current time and a resource snapshot
so this module is fully deterministic and unit-testable without mocking the
filesystem or the clock (plan section 6, LLD).

Constants below are deliberately conservative for a shared cgroup with an
OOM killer: an eviction/kill is unrecoverable, so shrink reacts fast and
growth ramps slowly (plan section 4).
"""
from __future__ import annotations

import math
import os
from dataclasses import replace
from typing import TYPE_CHECKING

from app.services.resource_governor.models import (
    Ceilings,
    ControllerState,
    Limits,
    Pool,
    PoolDemand,
    PoolState,
    ResourceSnapshot,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def _env_float(name: str, default: float, *, low: float, high: float) -> float:
    """Read a float constant from the environment, falling back to
    *default* on a missing/malformed value and clamping to ``[low, high]``
    so a typo'd override can't push the governor into a degenerate always-
    grow or always-shrink state."""
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(low, min(high, value))


SAMPLE_INTERVAL_SECONDS = 15.0
SAMPLE_JITTER_SECONDS = 1.0

HEAVY_PARSE_WORKING_SET_GB = 1.5

# Overridable per-deployment (plan: "Fix 3 — Make MEM_SOFT/MEM_HARD/GROW_BAND
# configurable"): a shared-container deployment can carry a fixed baseline
# (e.g. Docling's idle model weights) that BaselineMemoryTracker
# (probe.py) may not fully net out — GOVERNOR_MEM_SOFT/HARD let an operator
# raise the thresholds directly rather than editing code, while still
# defaulting to the original conservative values everywhere else.
#
# MEM_HARD/GROW_BAND are re-clamped against MEM_SOFT *after* their own env
# read (not just via _env_float's low/high) so the invariant MEM_HARD >
# MEM_SOFT > MEM_SOFT - GROW_BAND holds even when only one of the three is
# overridden and the others fall back to their un-clamped defaults.
MEM_SOFT = _env_float("GOVERNOR_MEM_SOFT", 0.70, low=0.10, high=0.95)
MEM_HARD = max(_env_float("GOVERNOR_MEM_HARD", 0.80, low=0.11, high=0.99), MEM_SOFT + 0.01)
GROW_BAND = min(_env_float("GOVERNOR_GROW_BAND", 0.05, low=0.0, high=0.90), MEM_SOFT - 0.01)
GROW_CONFIRM_SAMPLES = 3
# Light records are milliseconds on a few KB; the heavy confirm window would
# pin Jira/Slack/Markdown at the floor for ~45s waiting for Docling-grade
# proof that the cgroup can absorb another permit.
LIGHT_GROW_CONFIRM_SAMPLES = 1
SHRINK_COOLDOWN_SECONDS = 30.0
INCIDENT_COOLDOWN_SECONDS = 60.0

CPU_BRAKE_UTILISATION = 0.85
CPU_BRAKE_PRESSURE_AVG10 = 0.60
CPU_BRAKE_THROTTLED_RATIO = 0.20

HEAVY_START_INTERVAL_SECONDS = 2.0
HEAVY_START_BUCKET_CAPACITY = 2
# StartRateLimiter's *sustained* admission rate is exactly 1/interval
# (capacity only bounds burst size — see StartRateLimiter.try_consume), so a
# fixed HEAVY_START_INTERVAL_SECONDS caps admissions at 0.5/sec forever no
# matter how high an operator sets MAX_CONCURRENT_PARSING/INDEXING. Dividing
# the pool's own ceiling by this constant scales the sustained rate with
# that ceiling instead — small/derived ceilings (2-12) stay close to the
# original conservative rate, while an explicit ceiling of 1000 yields ~50
# admits/sec so real resource pressure (not this burst smoother) becomes the
# actual bottleneck.
HEAVY_START_RATE_CEILING_DIVISOR = 20.0

DEMAND_UTILISATION_THRESHOLD = 0.7
LIGHT_DEMAND_UTILISATION_THRESHOLD = 0.3
GRADIENT_IMPROVEMENT_THRESHOLD = 0.05
GRADIENT_LATENCY_REGRESSION_RATIO = 1.5

# Resource-delta thresholds for slow-start step sizing (plan section 4,
# "resource-delta probing"): how much mem/cpu moved between the previous
# grow step and this sample.
RESOURCE_DELTA_LOW = 0.05
RESOURCE_DELTA_MODERATE = 0.20

# Startup ceiling bounds (plan section 4, "Ceiling resolution").
_PARSE_CEILING_MIN, _PARSE_CEILING_MAX = 2, 12
_INDEX_CEILING_MIN, _INDEX_CEILING_MAX = 4, 48
_LIGHT_CEILING_MIN, _LIGHT_CEILING_MAX = 8, 64
_LIGHT_CPU_MULTIPLIER = 8
_INDEX_CEILING_MULTIPLIER = 3
# Lighter than heavy's 3x: a light record's active-pipeline hold is still
# mostly downstream (extraction/embedding/vectordb), same reasoning as
# _INDEX_CEILING_PARSE_MULTIPLIER, but light parses are cheap enough that
# doubling the parse-tier ceiling is already generous headroom.
_LIGHT_INDEX_CEILING_MULTIPLIER = 2

_BYTES_FLOOR = 256 * 1024 * 1024
_BYTES_BUDGET_FRACTION = 0.25

COUNT_POOL_FLOOR = 2


def _is_light_pool(pool: Pool) -> bool:
    return pool is Pool.LIGHT_PARSE or pool is Pool.LIGHT_INDEX


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _free_memory_gb(snap: ResourceSnapshot) -> float | None:
    """Memory in the cgroup not already spoken for, in GiB.

    Uses the *raw* working set, never the baseline-adjusted one: the
    question here is how much of the cgroup is physically free, and memory
    held by a co-located service is unavailable to a parse slot no matter
    which process it is attributed to.
    """
    if snap.mem_limit_bytes is None:
        return None
    resident = snap.mem_working_set_raw_bytes
    if resident is None:
        resident = snap.mem_working_set_bytes or 0
    return max(0.0, (snap.mem_limit_bytes - resident) / (1024 ** 3))


# ---------------------------------------------------------------------------
# Ceilings — resolved once at startup
# ---------------------------------------------------------------------------


def resolve_ceilings(
    snap: ResourceSnapshot,
    env_parse: int | None,
    env_index: int | None,
    worker_count: int = 1,
    env_light: int | None = None,
    env_light_index: int | None = None,
) -> Ceilings:
    """Resolve operator ceilings once at startup.

    ``env_parse``/``env_index``/``env_light``/``env_light_index`` are the
    resolved ``MAX_CONCURRENT_PARSING`` / ``MAX_CONCURRENT_INDEXING`` /
    ``MAX_CONCURRENT_LIGHT_PARSING`` / ``MAX_CONCURRENT_LIGHT_INDEXING``
    values — ``None`` means "derive".
    Explicit operator values are honoured as the process-wide total (see
    corner-case table), but heavy is still capped at the CPU quota: heavy
    parses are CPU-bound, so admitting more of them than there are cores
    only adds contention, never throughput. ``MAX_CONCURRENT_LIGHT_PARSING``
    has no such cap since light parses are not CPU-bound. When
    ``worker_count > 1`` the (already CPU-capped) total is then divided
    across workers below, since each worker process runs its own governor.
    """
    workers = max(1, worker_count)
    # Never hand out more heavy-parse slots than CPUs actually available —
    # a sub-2-core cgroup (fractional cpu.max, e.g. cpu_quota=1.0) would
    # otherwise oversubscribe CPU-bound work. Floored at 1 so there's always
    # room for at least one heavy-parse slot even below 1 CPU.
    cpu_cap = max(1, math.floor(snap.cpu_quota))
    # Sized from memory that is actually free at startup, not the whole
    # limit: in the all-in-one container six other services (including
    # Docling's and the embedding server's model weights) are already
    # resident before the first document arrives, so limit / per-parse cost
    # would hand the parse pool a budget the cgroup cannot honour.
    free_gb = _free_memory_gb(snap)

    if env_parse is not None:
        parse_ceiling = min(max(1, env_parse), cpu_cap)
        # An explicit heavy ceiling caps light too: an operator pinning
        # MAX_CONCURRENT_PARSING=1 wants one parse in flight, not one heavy
        # parse plus a burst of light ones. MAX_CONCURRENT_LIGHT_PARSING
        # (below) is the escape hatch when they want the opposite.
    else:
        if free_gb is not None:
            derived = min(snap.cpu_quota, free_gb / HEAVY_PARSE_WORKING_SET_GB)
        else:
            derived = snap.cpu_quota
        # _PARSE_CEILING_MIN guarantees useful concurrency on a derived
        # (non-overridden) ceiling, but must never hand out more heavy-parse
        # slots than CPUs actually available — cap the floor itself by the
        # CPU-derived bound.
        parse_ceiling = int(derived)
        # Sized off CPU alone, never off heavy: a light parse is milliseconds
        # on a few KB, so heavy-parse memory sizing must not cap it.

    # Last word, so "few heavy parses, many light ones" is expressible without
    # dropping MAX_CONCURRENT_PARSING back to the derived path: the two tiers
    # cost wildly different amounts of memory, and the coupling above only
    # makes sense as a default. Not re-capped by the derived/heavy-coupled
    # light_ceiling above — that would make this override a no-op whenever
    # the operator's intent is exactly to raise light past it.
    if env_light is not None:
        light_ceiling = min(max(1, env_light),int(snap.cpu_quota * _LIGHT_CPU_MULTIPLIER))
    else:
        light_ceiling = int(snap.cpu_quota * _LIGHT_CPU_MULTIPLIER)

    if env_index is not None:
        index_ceiling = max(1, env_index)
    else:
        derived_index = parse_ceiling * _INDEX_CEILING_MULTIPLIER
        index_ceiling = int(derived_index)

    # Independent of index_ceiling for the same reason light is independent
    # of heavy: a light record's active-pipeline hold must never be capped
    # by the heavy-parse-derived sizing above, or a Jira/Slack/Markdown
    # record queues for the same gate a Docling PDF is holding for minutes.
    if env_light_index is not None:
        light_index_ceiling = max(1, env_light_index)
    else:
        derived_light_index = light_ceiling * _LIGHT_CPU_MULTIPLIER
        light_index_ceiling = int(derived_light_index)

    if snap.mem_limit_bytes is not None:
        bytes_max = max(_BYTES_FLOOR, int(snap.mem_limit_bytes * _BYTES_BUDGET_FRACTION))
    else:
        bytes_max = _BYTES_FLOOR

    if workers > 1:
        parse_ceiling = max(1, parse_ceiling // workers)
        index_ceiling = max(1, index_ceiling // workers)
        light_ceiling = max(1, light_ceiling // workers)
        light_index_ceiling = max(1, light_index_ceiling // workers)
        bytes_max = max(_BYTES_FLOOR, bytes_max // workers)

    return Ceilings(
        heavy=parse_ceiling,
        light=light_ceiling,
        index=index_ceiling,
        light_index=light_index_ceiling,
        bytes_max=bytes_max,
    )


def start_rate_limiter_params(reference_ceiling: int) -> tuple[float, int]:
    """``(interval, capacity)`` for a pool's ``StartRateLimiter``.

    ``reference_ceiling`` is the *count* ceiling that should drive how fast
    this pool admits new work — ``ceilings.heavy`` for ``HEAVY_PARSE``, and
    ``ceilings.index`` for ``DOWNLOAD_BYTES`` (every record passes through
    download before any parse/index slot is even requested, so its
    admission rate should track overall record throughput, not the
    byte-sized ``ceilings.bytes_max``).

    The sustained rate (``1 / interval``) is ``max(1 / HEAVY_START_INTERVAL_
    SECONDS, reference_ceiling / HEAVY_START_RATE_CEILING_DIVISOR)`` —
    never slower than the original default, faster once the ceiling implies
    it should be. Capacity (burst size) scales the same way.
    """
    base_rate = 1.0 / HEAVY_START_INTERVAL_SECONDS
    scaled_rate = max(0, reference_ceiling) / HEAVY_START_RATE_CEILING_DIVISOR
    rate = max(base_rate, scaled_rate)
    capacity = max(
        HEAVY_START_BUCKET_CAPACITY, int(reference_ceiling // HEAVY_START_RATE_CEILING_DIVISOR)
    )
    return 1.0 / rate, capacity


def floor_for(pool: Pool, ceiling: int) -> int:
    """Warm-start / minimum-under-pressure limit for a pool.

    Count pools floor at ``min(2, ceiling)`` so an explicit ceiling of 1 is
    honoured exactly (never forced up to 2). The bytes pool floors at a
    fixed minimum useful budget.
    """
    if pool is Pool.DOWNLOAD_BYTES:
        return min(_BYTES_FLOOR, ceiling)
    if pool is Pool.LIGHT_PARSE:
        return min(10, ceiling)
    if pool is Pool.LIGHT_INDEX:
        return min(20, ceiling)
    return min(COUNT_POOL_FLOOR, ceiling)


def warm_start_limits(ceilings: Ceilings) -> Limits:
    """Starting limits: every pool begins at its conservative floor and ramps
    toward its ceiling as samples prove the headroom is real.

    An explicit ``MAX_CONCURRENT_*`` used to start *at* the ceiling, on the
    grounds that the operator had expressed informed intent. But a limit only
    bounds new admissions — the governor cannot revoke a permit it already
    granted — so starting wide open lets the first burst commit more memory
    than the cgroup can hold before the first sample even runs, and the OOM
    killer wins that race (``MAX_CONCURRENT_PARSING=1000`` admitted a
    thousand Docling parses and took the container down). An explicit value
    still raises the ceiling the ramp climbs toward; it no longer skips the
    ramp.
    """
    return Limits(values={
        Pool.HEAVY_PARSE: floor_for(Pool.HEAVY_PARSE, ceilings.heavy),
        Pool.LIGHT_PARSE: floor_for(Pool.LIGHT_PARSE, ceilings.light),
        Pool.INDEX: floor_for(Pool.INDEX, ceilings.index),
        Pool.LIGHT_INDEX: floor_for(Pool.LIGHT_INDEX, ceilings.light_index),
        Pool.DOWNLOAD_BYTES: floor_for(Pool.DOWNLOAD_BYTES, ceilings.bytes_max),
    })


def _ceiling_for(pool: Pool, ceilings: Ceilings) -> int:
    return {
        Pool.HEAVY_PARSE: ceilings.heavy,
        Pool.LIGHT_PARSE: ceilings.light,
        Pool.INDEX: ceilings.index,
        Pool.LIGHT_INDEX: ceilings.light_index,
        Pool.DOWNLOAD_BYTES: ceilings.bytes_max,
    }[pool]


def _target_for(pool: Pool, snap: ResourceSnapshot, ceilings: Ceilings) -> int:
    """Section 4 "Targets per sample" — the value growth ramps toward, never
    a value jumped to directly."""
    free_gb = _free_memory_gb(snap)
    avail_bytes = None if free_gb is None else int(free_gb * 1024 ** 3)

    if pool is Pool.HEAVY_PARSE:
        if free_gb is not None:
            bound = min(ceilings.heavy, snap.cpu_quota, free_gb / HEAVY_PARSE_WORKING_SET_GB)
        else:
            bound = min(ceilings.heavy, snap.cpu_quota)
        floor = floor_for(pool, ceilings.heavy)
        return int(_clamp(math.floor(bound), floor, ceilings.heavy))

    if pool is Pool.LIGHT_PARSE:
        # Not derived from heavy_target — see plan section 4.2. This is a
        # runaway bound, not a throughput throttle, so it's simply the
        # ceiling.
        return ceilings.light

    if pool is Pool.INDEX or pool is Pool.LIGHT_INDEX:
        # Deliberately just the ceiling, not a cpu_quota expression (section
        # 4.2). INDEX is stopped by the growth preconditions (pressure /
        # demand / gradient); LIGHT_INDEX uses the looser light gates.
        return ceilings.light_index if pool is Pool.LIGHT_INDEX else ceilings.index

    if pool is Pool.DOWNLOAD_BYTES:
        if avail_bytes is not None and snap.mem_limit_bytes:
            target = _clamp(
                avail_bytes * _BYTES_BUDGET_FRACTION, _BYTES_FLOOR, snap.mem_limit_bytes * _BYTES_BUDGET_FRACTION,
            )
        else:
            target = ceilings.bytes_max
        floor = floor_for(pool, ceilings.bytes_max)
        return int(_clamp(target, floor, ceilings.bytes_max))

    raise AssertionError(f"unhandled pool {pool!r}")  # exhaustive over Pool StrEnum


def _step_for(pool: Pool, ceiling: int) -> int:
    """How much a single shrink transition (or a post-slow-start linear
    grow transition) moves the limit.

    Count pools move by one permit. The bytes pool moves by a percentage of
    its ceiling so a multi-gigabyte budget doesn't take hundreds of
    intervals to reach a useful size, while still ramping rather than
    jumping (plan principle 6). Growth for count pools while still in slow
    start uses ``_growth_step`` instead — see that function.
    """
    if pool is Pool.DOWNLOAD_BYTES:
        return max(64 * 1024 * 1024, ceiling // 20)
    return 1


def _growth_step(
    pool: Pool, ceiling: int, state: PoolState, snap: ResourceSnapshot,
) -> tuple[int, PoolState]:
    """Size one grow transition, TCP-slow-start style (plan section 4).

    Returns ``(step, updated_state)``. A ``step`` of ``0`` means "hold this
    round" — the caller must not advance the limit, only persist the state.

    ``DOWNLOAD_BYTES`` and any pool that has already exited slow start
    (``in_slow_start=False``) use the fixed linear ``_step_for`` step
    unconditionally — the percentage-of-ceiling sizing for bytes already
    ramps fast as a fraction of a multi-GB budget, and "exited slow start"
    means a previous grow already found the knee of the resource-usage
    curve for this pool.

    While in slow start, the step doubles on every grow whose resource
    impact (vs. the snapshot recorded at the *previous* grow) was small,
    giving convergence to the true capacity in ``O(log2(ceiling))``
    intervals instead of ``O(ceiling)``. A moderate-impact grow switches
    permanently to linear probing for this pool (until a future shrink
    re-arms slow start). A large-impact grow holds without abandoning slow
    start — a single noisy sample shouldn't discard already-confirmed
    headroom, so the next healthy sample can resume doubling from the same
    step.
    """
    linear_step = _step_for(pool, ceiling)
    if pool is Pool.DOWNLOAD_BYTES or not state.in_slow_start:
        return linear_step, state

    # Light cost is noise next to a co-located heavy parse; a process-wide
    # mem/cpu delta would otherwise freeze LIGHT_PARSE/LIGHT_INDEX whenever
    # Docling happens to allocate in the same interval.
    if not _is_light_pool(pool):
        prev_mem = state.prev_grow_mem_pressure
        mem_now = snap.mem_pressure
        if prev_mem is not None and mem_now is not None:
            prev_cpu = state.prev_grow_cpu_utilisation
            cpu_now = snap.cpu_utilisation
            mem_delta = abs(mem_now - prev_mem)
            # Cannot prove a CPU delta without both samples — treat as "no
            # evidence of impact" rather than assuming 0% or 100% (models.py
            # convention: unknown is never treated as a provable zero, but here
            # it must not veto growth either, so it simply drops out of the
            # max()).
            cpu_delta = abs(cpu_now - prev_cpu) if (cpu_now is not None and prev_cpu is not None) else 0.0
            impact = max(mem_delta, cpu_delta)

            if impact >= RESOURCE_DELTA_MODERATE:
                return 0, state
            if impact >= RESOURCE_DELTA_LOW:
                return linear_step, replace(state, in_slow_start=False, slow_start_step=1)

    used_step = state.slow_start_step
    next_step = min(used_step * 2, max(1, ceiling))
    return used_step, replace(state, slow_start_step=next_step)


def _record_grow(state: PoolState, snap: ResourceSnapshot) -> PoolState:
    """Baseline the resource snapshot at a successful grow, for the next
    ``_growth_step`` delta comparison."""
    return replace(
        state,
        prev_grow_mem_pressure=snap.mem_pressure,
        prev_grow_cpu_utilisation=snap.cpu_utilisation,
    )


def _reset_for_shrink(state: PoolState, *, cooldown_until: float) -> PoolState:
    """Any shrink clears slow-start memory: the resource baseline it was
    comparing against no longer describes this pool (the limit just moved),
    and fast exponential recovery back toward the last healthy level is
    preferable to a linear +1/interval crawl (plan section 4, "Reset to
    slow-start")."""
    return replace(
        state,
        healthy_streak=0,
        cooldown_until=cooldown_until,
        in_slow_start=True,
        slow_start_step=1,
        prev_grow_mem_pressure=None,
        prev_grow_cpu_utilisation=None,
    )


def _cpu_brake_active(snap: ResourceSnapshot) -> bool:
    """Independent of memory pressure — a CPU-bound host must shrink even
    when RAM is fine."""
    if snap.cpu_utilisation is not None and snap.cpu_utilisation > CPU_BRAKE_UTILISATION:
        return True
    if snap.cpu_pressure is not None and snap.cpu_pressure > CPU_BRAKE_PRESSURE_AVG10:
        return True
    if snap.cpu_throttled_ratio is not None and snap.cpu_throttled_ratio > CPU_BRAKE_THROTTLED_RATIO:
        return True
    return False


def _gradient_permits_growth(
    pool: Pool, demand: PoolDemand, state: PoolState, interval: float,
) -> tuple[bool, bool, PoolState]:
    """Throughput-gradient gate for INDEX (plan section 4.2).

    Returns ``(may_grow, must_shrink, updated_state)``. Every other pool is
    unconditionally allowed to grow — the gradient only matters for a pool
    whose bottleneck can be a *downstream* service (embeddings, vector
    upserts, LLM table enrichment) rather than local CPU/RAM, where blindly
    growing to the ceiling would just move the queue instead of clearing it.
    LIGHT_INDEX is excluded: a light record's local cost is negligible, and
    sharing this gate with INDEX pins Jira/Slack/Markdown at the floor
    whenever embeddings are already the heavy-path bottleneck.
    """
    if pool is not Pool.INDEX:
        return True, False, state

    completions_per_sec = demand.completions_per_second(interval)
    mean_wait = demand.mean_wait_seconds()
    baseline_completions = state.gradient_baseline_completions_per_sec
    baseline_wait = state.gradient_baseline_wait_seconds

    if baseline_completions is None:
        # No prior grow-step to compare against yet — bootstrap the
        # baseline and allow this first growth unconditionally.
        return True, False, replace(
            state,
            gradient_baseline_completions_per_sec=completions_per_sec,
            gradient_baseline_wait_seconds=mean_wait,
        )

    if baseline_wait and baseline_wait > 0 and mean_wait / baseline_wait > GRADIENT_LATENCY_REGRESSION_RATIO:
        return False, True, replace(
            state,
            gradient_baseline_completions_per_sec=completions_per_sec,
            gradient_baseline_wait_seconds=mean_wait,
        )

    if baseline_completions <= 0:
        return True, False, replace(
            state,
            gradient_baseline_completions_per_sec=completions_per_sec,
            gradient_baseline_wait_seconds=mean_wait,
        )

    improvement = (completions_per_sec - baseline_completions) / baseline_completions
    if improvement > GRADIENT_IMPROVEMENT_THRESHOLD:
        return True, False, replace(
            state,
            gradient_baseline_completions_per_sec=completions_per_sec,
            gradient_baseline_wait_seconds=mean_wait,
        )
    # Flat: hold at the current limit, but keep the existing baseline so a
    # later interval that does show improvement is measured against the
    # same reference point rather than a moving target.
    return False, False, state


def _next_pool_limit(
    pool: Pool,
    current: int,
    snap: ResourceSnapshot,
    ceilings: Ceilings,
    state: PoolState,
    demand: PoolDemand,
    now: float,
    interval: float,
) -> tuple[int, PoolState]:
    ceiling = _ceiling_for(pool, ceilings)
    floor = floor_for(pool, ceiling)
    shrink_step = _step_for(pool, ceiling)
    target = _target_for(pool, snap, ceilings)
    pressure = snap.mem_pressure
    # Asymmetric on purpose: shrink on the cgroup's true occupancy, grow on
    # the baseline-credited reading. Braking on the credited reading pushes
    # the effective MEM_SOFT/MEM_HARD trip points up by the baseline's share
    # of the limit (a 3GiB baseline in a 12GiB container turns 70%/80% into
    # 78%/85% real), which leaves too little headroom for the brake to matter:
    # one Docling batch can cover the remaining gap well inside a sample
    # interval, and an OOM kill is unrecoverable. Growth still needs the
    # credit — see ResourceSnapshot.mem_pressure_raw.
    brake_pressure = snap.mem_pressure_raw
    cooling_down = now < state.cooldown_until

    if brake_pressure is not None and brake_pressure >= MEM_HARD:
        return max(floor, current // 2), _reset_for_shrink(
            state, cooldown_until=now + INCIDENT_COOLDOWN_SECONDS,
        )

    # CPU brake is for CPU-bound heavy work. Light records are not, and
    # shrinking them because Docling is saturating cores only queues cheap
    # Jira/Slack work behind a problem they are not causing.
    cpu_brake = (not _is_light_pool(pool)) and _cpu_brake_active(snap)
    if (brake_pressure is not None and brake_pressure >= MEM_SOFT) or cpu_brake:
        return max(floor, current - shrink_step), _reset_for_shrink(
            state, cooldown_until=max(state.cooldown_until, now + SHRINK_COOLDOWN_SECONDS),
        )

    if pressure is None:
        # Cannot prove there is memory headroom to grow into — freeze.
        # (The CPU brake above still applies independently of this branch.)
        return current, replace(state, healthy_streak=0)

    # Light may grow right up to MEM_SOFT; heavy still needs the extra
    # GROW_BAND of headroom so a Docling batch cannot close the gap inside
    # one sample interval.
    grow_threshold = MEM_SOFT if _is_light_pool(pool) else MEM_SOFT - GROW_BAND
    confirm_samples = LIGHT_GROW_CONFIRM_SAMPLES if _is_light_pool(pool) else GROW_CONFIRM_SAMPLES
    demand_threshold = (
        LIGHT_DEMAND_UTILISATION_THRESHOLD if _is_light_pool(pool) else DEMAND_UTILISATION_THRESHOLD
    )
    if pressure < grow_threshold and not cooling_down:
        healthy_streak = state.healthy_streak + 1
        if healthy_streak >= confirm_samples and demand.has_demand(
            current, interval, threshold=demand_threshold
        ):
            may_grow, must_shrink, gradient_state = _gradient_permits_growth(pool, demand, state, interval)
            next_state = replace(gradient_state, healthy_streak=healthy_streak)
            if must_shrink:
                return max(floor, current - shrink_step), _reset_for_shrink(
                    next_state, cooldown_until=max(state.cooldown_until, now + SHRINK_COOLDOWN_SECONDS),
                )
            if may_grow:
                grow_step, grow_state = _growth_step(pool, ceiling, next_state, snap)
                if grow_step <= 0:
                    # Large resource impact from the last grow — hold this
                    # round without abandoning slow start.
                    return current, grow_state
                return min(target, current + grow_step), _record_grow(grow_state, snap)
            # Gradient flat: throughput isn't improving as this pool grows
            # (its bottleneck is downstream) — hold, and fall back to
            # linear probing since slow start already found the knee.
            return current, replace(next_state, in_slow_start=False, slow_start_step=1)
        return current, replace(state, healthy_streak=healthy_streak)

    # Pressure is fine but not yet confirmed-healthy, or still cooling down.
    return current, replace(state, healthy_streak=0 if cooling_down else state.healthy_streak)


def next_limits(
    current: Limits,
    snap: ResourceSnapshot,
    ceilings: Ceilings,
    state: ControllerState,
    demand: Mapping[Pool, PoolDemand],
    now: float,
    interval: float = SAMPLE_INTERVAL_SECONDS,
) -> tuple[Limits, ControllerState]:
    """Advance every pool's limit by at most one step toward its target.

    Pure and deterministic given its inputs — the caller supplies ``now``
    and the sampled ``demand`` so this can be exercised in tests without a
    clock or a running event loop.
    """
    new_values: dict[Pool, int] = {}
    new_states: dict[Pool, PoolState] = {}
    for pool in Pool:
        pool_demand = demand.get(pool, PoolDemand.empty())
        new_limit, new_state = _next_pool_limit(
            pool, current.get(pool), snap, ceilings, state.get(pool), pool_demand, now, interval,
        )
        new_values[pool] = new_limit
        new_states[pool] = new_state
    return Limits(values=new_values), ControllerState(pools=new_states)
