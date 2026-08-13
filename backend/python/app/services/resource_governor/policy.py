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

# Resident memory one heavy parse is assumed to hold. Sizes the dynamic
# heavy-parse memory gate (``heavy_memory_cap``), so a deployment whose
# documents are much larger or smaller than the assumed Docling working set
# can retune the gate without touching the CPU-derived ceiling.
HEAVY_PARSE_WORKING_SET_GB = _env_float(
    "GOVERNOR_HEAVY_PARSE_WORKING_SET_GB", 1.5, low=0.1, high=64.0
)

# Slots per CPU per tier. A heavy parse is CPU-bound end to end (Docling
# layout analysis, OCR, LibreOffice), so one per core is the most that adds
# throughput. A light parse is milliseconds of CPU on a few KB and spends
# most of its wall time on I/O, so several per core keep the cores busy.
HEAVY_PARSE_SLOTS_PER_CPU = _env_float(
    "GOVERNOR_HEAVY_PARSE_SLOTS_PER_CPU", 1.0, low=0.1, high=32.0
)
LIGHT_PARSE_SLOTS_PER_CPU = _env_float(
    "GOVERNOR_LIGHT_PARSE_SLOTS_PER_CPU", 10.0, low=0.1, high=64.0
)

# The INDEX permit is held for a record's whole lifetime (download through
# vector upsert), most of which is *not* parsing — so the active-pipeline
# pool is sized as a multiple of the widest parse tier rather than equal to
# it. This figure is the *effective* in-flight width from the first sample,
# not a ceiling something else ramps toward (``_is_index_pool``), so it must
# stay a small multiple: it is how many records may hold a downloaded buffer
# and their post-parse chunk/embedding state at once.
INDEX_SLOTS_PER_PARSE_SLOT = _env_float(
    "GOVERNOR_INDEX_SLOTS_PER_PARSE_SLOT", 100.0, low=0.1, high=1000.0
)

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
# matter how large a pool's ceiling is. Dividing the pool's own ceiling by
# this constant scales the sustained rate with that ceiling instead — small
# ceilings (2-12) stay close to the original conservative rate, while a
# 1000-permit ceiling yields ~50 admits/sec so real resource pressure (not
# this burst smoother) becomes the actual bottleneck.
HEAVY_START_RATE_CEILING_DIVISOR = 20.0

DEMAND_UTILISATION_THRESHOLD = 0.7
LIGHT_DEMAND_UTILISATION_THRESHOLD = 0.3

# Resource-delta thresholds for slow-start step sizing (plan section 4,
# "resource-delta probing"): how much mem/cpu moved between the previous
# grow step and this sample.
RESOURCE_DELTA_LOW = 0.05
RESOURCE_DELTA_MODERATE = 0.20

_BYTES_FLOOR = 256 * 1024 * 1024
_BYTES_BUDGET_FRACTION = 0.25

COUNT_POOL_FLOOR = 2


def _is_light_pool(pool: Pool) -> bool:
    return pool is Pool.LIGHT_PARSE


def _is_index_pool(pool: Pool) -> bool:
    """The active-pipeline pool, which the control law does not adapt.

    An INDEX permit is pipeline width, not a resource reservation: what a
    record actually consumes is gated elsewhere — buffered download bytes by
    DOWNLOAD_BYTES, parse CPU/RSS by HEAVY_PARSE/LIGHT_PARSE, embedding and
    LLM fan-out by MAX_CONCURRENT_INDEXING_LLM_CALLS. Adapting it as well
    throttled the one stage whose cost is mostly waiting on those gates and
    on downstream services, and cost ~45s of near-serial startup (floor of 2
    plus the confirm window) on every deploy. It therefore sits at
    ``ceilings.index`` for the life of the process.
    """
    return pool is Pool.INDEX


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


def heavy_memory_cap(snap: ResourceSnapshot) -> int | None:
    """How many heavy parses the *currently free* memory can hold, or
    ``None`` when the cgroup limit is unknown.

    The heavy ceiling is sized off CPU alone (``resolve_ceilings``), which
    is the right steady-state number but says nothing about whether the
    memory to run that many Docling conversions exists right now — in a
    shared container it usually doesn't at startup, and may stop existing
    mid-batch. This is the gate that holds the heavy limit below its
    ceiling until the memory is actually there. Floored at 1 so heavy
    parsing can always make forward progress (a cap of 0 would stall every
    PDF indefinitely rather than slowly).
    """
    free_gb = _free_memory_gb(snap)
    if free_gb is None:
        return None
    return max(1, int(free_gb / HEAVY_PARSE_WORKING_SET_GB))


# ---------------------------------------------------------------------------
# Ceilings — resolved once at startup
# ---------------------------------------------------------------------------


def resolve_ceilings(
    snap: ResourceSnapshot,
    env_parse: int | None,
    env_index: int | None,
    worker_count: int = 1,
) -> Ceilings:
    """Resolve operator ceilings once at startup.

    Every ceiling derives from the CPU quota and is then capped by the
    operator's ``MAX_CONCURRENT_PARSING`` / ``MAX_CONCURRENT_INDEXING``
    (``None`` means "no cap"):

    * heavy parse — ``min(cpus * HEAVY_PARSE_SLOTS_PER_CPU, env_parse)``
    * index —
      ``min(INDEX_SLOTS_PER_PARSE_SLOT * max(heavy, light), env_index)``,
      the in-flight cap for heavy and light records together
    * light parse — ``min(cpus * LIGHT_PARSE_SLOTS_PER_CPU, env_parse)``

    Memory deliberately plays no part here. It is not a startup constant:
    the free memory at process start (before any model is loaded, or with
    six sibling services already resident) is a poor predictor of what is
    free five minutes into a batch. The heavy tier — the only one whose
    per-slot cost is measured in GiB — is instead gated against live free
    memory on every sample (``heavy_memory_cap``), so it holds below this
    ceiling exactly as long as the memory isn't there and no longer.

    ``worker_count > 1`` divides the result, since each worker process runs
    its own governor against the same cgroup.
    """
    workers = max(1, worker_count)
    cpus = max(0.0, snap.cpu_quota)
    # Floored at 1 throughout: a sub-1-CPU cgroup (fractional cpu.max) must
    # still be able to run one parse at a time rather than none.
    parse_cap = max(1, env_parse) if env_parse is not None else None

    heavy_parse_ceiling = max(1, math.floor(cpus * HEAVY_PARSE_SLOTS_PER_CPU))
    light_parse_ceiling = max(1, math.floor(cpus * LIGHT_PARSE_SLOTS_PER_CPU))
    if parse_cap is not None:
        heavy_parse_ceiling = min(heavy_parse_ceiling, parse_cap)
        light_parse_ceiling = min(light_parse_ceiling, parse_cap)

    index_ceiling = max(
        1, math.floor(INDEX_SLOTS_PER_PARSE_SLOT * max(heavy_parse_ceiling, light_parse_ceiling))
    )
    if env_index is not None:
        index_ceiling = min(index_ceiling, max(1, env_index))

    if snap.mem_limit_bytes is not None:
        bytes_max = max(_BYTES_FLOOR, int(snap.mem_limit_bytes * _BYTES_BUDGET_FRACTION))
    else:
        bytes_max = _BYTES_FLOOR

    if workers > 1:
        heavy_parse_ceiling = max(1, heavy_parse_ceiling // workers)
        light_parse_ceiling = max(1, light_parse_ceiling // workers)
        index_ceiling = max(1, index_ceiling // workers)
        bytes_max = max(_BYTES_FLOOR, bytes_max // workers)

    return Ceilings(
        heavy=heavy_parse_ceiling,
        light=light_parse_ceiling,
        index=index_ceiling,
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

    Light parse floors at half its ceiling instead: its per-slot cost is
    negligible, so ramping a Jira/Slack sync one permit at a time from 2
    only adds latency. Half, not the full ceiling — a floor equal to the
    ceiling would leave the memory brake nothing to shrink.

    The index pool floors *at* its ceiling: it is never adapted (see
    ``_is_index_pool``), so its minimum and maximum are the same value.
    """
    if pool is Pool.DOWNLOAD_BYTES:
        return min(_BYTES_FLOOR, ceiling)
    if _is_index_pool(pool):
        return ceiling
    if _is_light_pool(pool):
        return min(max(COUNT_POOL_FLOOR, ceiling // 2), ceiling)
    return min(COUNT_POOL_FLOOR, ceiling)


def warm_start_limits(ceilings: Ceilings) -> Limits:
    """Starting limits: every adapted pool begins at its conservative floor
    and ramps toward its ceiling as samples prove the headroom is real. The
    index pool, which is never adapted, starts at its ceiling.

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
        Pool.DOWNLOAD_BYTES: floor_for(Pool.DOWNLOAD_BYTES, ceilings.bytes_max),
    })


def _ceiling_for(pool: Pool, ceilings: Ceilings) -> int:
    return {
        Pool.HEAVY_PARSE: ceilings.heavy,
        Pool.LIGHT_PARSE: ceilings.light,
        Pool.INDEX: ceilings.index,
        Pool.DOWNLOAD_BYTES: ceilings.bytes_max,
    }[pool]


def _target_for(pool: Pool, snap: ResourceSnapshot, ceilings: Ceilings) -> int:
    """Section 4 "Targets per sample" — the value growth ramps toward, never
    a value jumped to directly."""
    free_gb = _free_memory_gb(snap)
    avail_bytes = None if free_gb is None else int(free_gb * 1024 ** 3)

    if pool is Pool.HEAVY_PARSE:
        mem_cap = heavy_memory_cap(snap)
        bound = ceilings.heavy if mem_cap is None else min(ceilings.heavy, mem_cap)
        # Clamped to 1, not to floor_for: the memory gate must be able to
        # hold heavy *below* its warm-start floor when the cgroup genuinely
        # cannot hold that many concurrent Docling working sets.
        return int(_clamp(bound, 1, ceilings.heavy))

    if pool is Pool.LIGHT_PARSE:
        # Not derived from heavy_target — see plan section 4.2. This is a
        # runaway bound, not a throughput throttle, so it's simply the
        # ceiling.
        return ceilings.light

    if _is_index_pool(pool):
        return ceilings.index

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
    # mem/cpu delta would otherwise freeze LIGHT_PARSE whenever Docling
    # happens to allocate in the same interval.
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
    slow-start").
    """
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
    if _is_index_pool(pool):
        return ceiling, state

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

    # Heavy's target is the CPU-derived ceiling gated by live free memory
    # (_target_for). Overall pressure can sit well under MEM_SOFT while
    # what's left is still too small for another Docling working set, so
    # this walks heavy down to what memory can actually hold — below the
    # warm-start floor if that's what it takes — without waiting for a
    # brake that, by definition, only trips once it is nearly too late.
    if pool is Pool.HEAVY_PARSE and current > target:
        return max(target, current - shrink_step), _reset_for_shrink(
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
            next_state = replace(state, healthy_streak=healthy_streak)
            grow_step, grow_state = _growth_step(pool, ceiling, next_state, snap)
            if grow_step <= 0:
                # Large resource impact from the last grow — hold this
                # round without abandoning slow start.
                return current, grow_state
            return min(target, current + grow_step), _record_grow(grow_state, snap)
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
    """Advance every adapted pool's limit by at most one step toward its
    target, and hold the index pool at its ceiling (``_is_index_pool``).

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
