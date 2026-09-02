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
from app.utils.env_config import env_float as _env_float

if TYPE_CHECKING:
    from collections.abc import Mapping

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


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

# Absolute cap on the light ceiling. 10-per-CPU is the right shape on a small
# box but keeps scaling linearly: a 48-core host would derive 480 concurrent
# light parses, which is 480 simultaneous in-flight HTTP requests to (and
# response buffers from) the parsing service. The per-CPU figure stays the
# runaway bound it was; this is the ceiling on the ceiling.
LIGHT_PARSE_MAX = int(_env_float("GOVERNOR_LIGHT_PARSE_MAX", 256, low=1, high=4096))

# CPUs withheld from the heavy-parse ceiling when a local embedding model is
# configured. The default/sentence-transformers/HuggingFace providers all run
# ``model.encode()`` on CPU in the embedding server, which every shipped
# deployment co-locates in this cgroup (EMBEDDING_SERVER_URL=localhost:8002),
# and embedding sits on the critical path of every indexed record. Sizing
# heavy off the full quota lets a Docling batch take every core and leaves
# embedding to fight it for CPU, so the reservation comes off the quota
# *before* the slot count is derived. Applied to heavy only: a light parse is
# milliseconds of CPU on a few KB, so its 10-per-CPU ceiling is a runaway
# bound rather than a claim on cores.
EMBEDDING_CPU_RESERVATION = _env_float(
    "GOVERNOR_EMBEDDING_CPU_RESERVATION", 2.0, low=0.0, high=32.0
)

# The reservation is an absolute core count, so on a small box it eats the
# heavy tier alive: 4 - 2 = 2 cores gives ceilings.heavy == floor_for(heavy)
# == 2, leaving the pool no range at all for the ramp, the demand check or
# the CPU brake to work in. Capping it as a share of the quota keeps the full
# 2 cores on any host with 8+ cores while leaving a 4-core host 3.
EMBEDDING_CPU_RESERVATION_MAX_FRACTION = 0.25

# The INDEX permit is held for a record's whole lifetime (download through
# vector upsert), most of which is *not* parsing — so the active-pipeline
# pool is wider than the parse tiers to keep the pipeline full while records
# wait on downstream services.
#
# It used to be a flat multiple of the widest parse tier
# (GOVERNOR_INDEX_SLOTS_PER_PARSE_SLOT, default 100). Because the widest tier
# is light (10/CPU), that multiplied out to 1000 slots per CPU — 4,000 on a
# 4-core box and 48,000 on a 48-core one. Every one of those admitted records
# queues somewhere downstream, and each queued record was polling Redis for a
# parse lease twice a second; a production 48-core host drove Redis to its
# 10,000-client limit that way.
#
# Each tier's in-flight budget is now a small multiple of *its own* parse
# ceiling, which is the only figure that says anything about how much
# pipeline depth that tier can actually use: enough records in flight to keep
# its parse stage fed and its post-parse stages busy, and no more. Anything
# past that is a record holding a buffer while it waits.
INDEX_HEADROOM = _env_float("GOVERNOR_INDEX_HEADROOM", 2.0, low=1.0, high=16.0)

# Per-tier clamps. The floor keeps a tiny host from serialising a tier; the
# ceiling stops a very large host deriving an in-flight budget whose buffers
# nothing can hold.
INDEX_MIN_PER_TIER = 8
INDEX_MAX_PER_TIER = int(_env_float("GOVERNOR_INDEX_MAX", 512, low=1, high=100_000))

# Resident memory one in-flight record is assumed to hold — its downloaded
# buffer plus post-parse chunk/embedding state. Sizes ``index_memory_cap``
# the same way HEAVY_PARSE_WORKING_SET_GB sizes ``heavy_memory_cap``. Split by
# tier because the buffers differ by an order of magnitude: a scanned PDF and
# its rasterised pages against a few KB of Jira blocks.
INDEX_HEAVY_WORKING_SET_GB = _env_float(
    "GOVERNOR_INDEX_HEAVY_WORKING_SET_GB", 0.15, low=0.001, high=8.0
)
INDEX_LIGHT_WORKING_SET_GB = _env_float(
    "GOVERNOR_INDEX_LIGHT_WORKING_SET_GB", 0.02, low=0.001, high=8.0
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
# Default 0: heavy grows up to MEM_SOFT like light. The memory cap and
# resource-delta hold already stop a Docling batch from closing the gap
# inside one sample; this band is an optional operator tightening.
GROW_BAND = min(_env_float("GOVERNOR_GROW_BAND", 0.0, low=0.0, high=0.90), MEM_SOFT - 0.01)
GROW_CONFIRM_SAMPLES = 1
# Light used to confirm faster than heavy; both now grow on a single
# healthy+demand sample — a bad grow is caught by the next interval's
# shrink path (~15s), not a 45s proof window.
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

COUNT_POOL_FLOOR = 2
# Every pool is a count pool, and one grow/shrink transition moves one permit.
COUNT_POOL_STEP = 1


def _is_light_pool(pool: Pool) -> bool:
    return pool is Pool.LIGHT_PARSE


def _is_index_pool(pool: Pool) -> bool:
    """The active-pipeline pool.

    An INDEX permit is mostly pipeline width — what a record *computes* is
    gated elsewhere (parse CPU/RSS by HEAVY_PARSE/LIGHT_PARSE, embedding and
    LLM fan-out by MAX_CONCURRENT_INDEXING_LLM_CALLS). But it is not free:
    each permit holds a downloaded buffer and post-parse chunk state, so the
    pool is memory-bound even though it is not CPU-bound, and it is gated
    against live free memory like heavy is (``index_memory_cap``).

    This pool used to be excluded from the control law entirely, pinned at
    its ceiling, because adapting it from a floor of 2 cost ~45s of
    near-serial startup on every deploy. That cost came from the *floor*,
    not from adapting: ``floor_for`` now starts index at half its ceiling,
    so the pipeline is full from the first message and the memory brake
    still has somewhere to shrink into.
    """
    return pool in (Pool.INDEX_HEAVY, Pool.INDEX_LIGHT)


def _is_cpu_bound_pool(pool: Pool) -> bool:
    """Whether a permit in *pool* is a claim on CPU rather than on queue slots.

    Only HEAVY_PARSE is: Docling layout analysis, OCR and LibreOffice run
    flat out on a core for the whole permit. A light parse is milliseconds of
    CPU on a few KB, and an index permit is mostly waiting on downstream
    services. The distinction drives the CPU brake, the grow threshold, the
    confirm window and the resource-delta check — braking either cheap pool
    on CPU would throttle work that isn't causing the pressure.
    """
    return pool is Pool.HEAVY_PARSE


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


def index_memory_cap(snap: ResourceSnapshot, pool: Pool) -> int | None:
    """How many in-flight records of *pool*'s tier the currently free memory
    can hold, or ``None`` when the cgroup limit is unknown.

    The index ceilings are parse-derived (``resolve_ceilings``), which says
    nothing about whether the memory to hold that many downloaded buffers
    and chunk sets exists right now. This is what lets one image size itself
    correctly on a 4-core/8 GiB host and on a 48-core/96 GiB host: on the
    former the cap binds and holds the pools well under their ceilings, on
    the latter it is far above them and never binds at all.

    Floored at ``COUNT_POOL_FLOOR`` rather than 1: unlike a heavy parse, an
    index permit is mostly waiting on downstream services, so a cap of 1
    would serialise a whole tier on a transient memory spike.
    """
    free_gb = _free_memory_gb(snap)
    if free_gb is None:
        return None
    working_set = (
        INDEX_HEAVY_WORKING_SET_GB
        if pool is Pool.INDEX_HEAVY
        else INDEX_LIGHT_WORKING_SET_GB
    )
    return max(COUNT_POOL_FLOOR, int(free_gb / working_set))


# ---------------------------------------------------------------------------
# Ceilings — resolved once at startup
# ---------------------------------------------------------------------------


def resolve_ceilings(
    snap: ResourceSnapshot,
    env_parse: int | None,
    env_index: int | None,
    worker_count: int = 1,
    *,
    reserve_embedding_cpus: bool = False,
) -> Ceilings:
    """Resolve operator ceilings once at startup.

    Every ceiling derives from the CPU quota and is then capped by the
    operator's ``MAX_CONCURRENT_PARSING`` / ``MAX_CONCURRENT_INDEXING``
    (``None`` means "no cap"):

    * heavy parse — ``min(heavy_cpus * HEAVY_PARSE_SLOTS_PER_CPU, env_parse)``
    * light parse —
      ``min(cpus * LIGHT_PARSE_SLOTS_PER_CPU, LIGHT_PARSE_MAX, env_parse)``
    * index, per tier — ``clamp(parse_ceiling * INDEX_HEADROOM,
      INDEX_MIN_PER_TIER, INDEX_MAX_PER_TIER)``, with ``env_index`` capping
      the two together

    Each tier's in-flight budget derives from *its own* parse ceiling because
    that is the only figure that says how much pipeline depth the tier can
    use: enough records in flight to keep its parse stage fed and its
    post-parse stages busy. A shared budget instead lets whichever tier holds
    its permits longest crowd out the other — a queue of Docling PDFs waiting
    on the handful of heavy-parse slots would hold permits that Jira or
    Confluence records could have turned over in seconds.

    ``reserve_embedding_cpus`` makes ``heavy_cpus`` the quota less the
    embedding reservation, for the deployments where embedding runs on local
    CPU in this cgroup. Because it lands on the ceiling — which every heavy
    limit is clamped to for the life of the process — those cores stay out
    of heavy's reach at every point in the ramp, not just at startup. The
    reservation is capped at ``EMBEDDING_CPU_RESERVATION_MAX_FRACTION`` of
    the quota so it cannot flatten the heavy tier on a small host.

    Memory deliberately plays no part *here*. It is not a startup constant:
    the free memory at process start (before any model is loaded, or with
    six sibling services already resident) is a poor predictor of what is
    free five minutes into a batch. Both memory-sensitive tiers — heavy,
    whose per-slot cost is measured in GiB, and index, which holds a
    downloaded buffer per permit — are instead gated against live free
    memory on every sample (``heavy_memory_cap`` / ``index_memory_cap``), so
    they hold below these ceilings exactly as long as the memory isn't there
    and no longer.

    ``worker_count > 1`` divides the result, since each worker process runs
    its own governor against the same cgroup.
    """
    workers = max(1, worker_count)
    cpus = max(0.0, snap.cpu_quota)
    reservation = min(EMBEDDING_CPU_RESERVATION, cpus * EMBEDDING_CPU_RESERVATION_MAX_FRACTION)
    heavy_cpus = max(0.0, cpus - reservation) if reserve_embedding_cpus else cpus
    # Floored at 1 throughout: a sub-1-CPU cgroup (fractional cpu.max) must
    # still be able to run one parse at a time rather than none. That floor
    # also applies once the embedding reservation has eaten the whole quota:
    # a 2-CPU container still parses one document at a time, slowly, rather
    # than stalling every PDF forever.
    parse_cap = max(1, env_parse) if env_parse is not None else None

    heavy_parse_ceiling = max(1, math.floor(heavy_cpus * HEAVY_PARSE_SLOTS_PER_CPU))
    light_parse_ceiling = max(
        1, min(math.floor(cpus * LIGHT_PARSE_SLOTS_PER_CPU), LIGHT_PARSE_MAX)
    )
    if parse_cap is not None:
        heavy_parse_ceiling = min(heavy_parse_ceiling, parse_cap)
        light_parse_ceiling = min(light_parse_ceiling, parse_cap)

    def _index_ceiling(parse_ceiling: int) -> int:
        # The floor is bounded by the ceiling as well as by the parse tier:
        # an operator who sets GOVERNOR_INDEX_MAX below INDEX_MIN_PER_TIER
        # would otherwise invert _clamp's bounds (low=8, high=1 returns 8) and
        # get 8x what they asked for. An explicit maximum wins over a floor
        # that only exists to stop a tiny host serialising a tier.
        return int(_clamp(
            math.ceil(parse_ceiling * INDEX_HEADROOM),
            min(INDEX_MIN_PER_TIER, parse_ceiling, INDEX_MAX_PER_TIER),
            INDEX_MAX_PER_TIER,
        ))

    index_heavy_ceiling = _index_ceiling(heavy_parse_ceiling)
    index_light_ceiling = _index_ceiling(light_parse_ceiling)

    if env_index is not None:
        # MAX_CONCURRENT_INDEXING caps the *total* in-flight budget, which is
        # what an operator is reasoning about. Scale both tiers to fit rather
        # than applying it to each, which would silently double it.
        total = index_heavy_ceiling + index_light_ceiling
        allowed = max(1, env_index)
        if total > allowed:
            share = allowed / total
            index_heavy_ceiling = max(1, math.floor(index_heavy_ceiling * share))
            index_light_ceiling = max(1, math.floor(index_light_ceiling * share))
            if index_heavy_ceiling + index_light_ceiling > allowed:
                # Only reachable at allowed == 1: two tiers each floored at 1
                # cannot add up to 1. Collapse rather than overshoot — light
                # drops to zero and ``effective_index_tier`` routes every
                # record to heavy, so the cap holds exactly. Nothing is lost:
                # at a total of one there is one record in flight whatever its
                # tier, so there is no fairness left to split.
                index_heavy_ceiling = allowed
                index_light_ceiling = 0

    if workers > 1:
        heavy_parse_ceiling = max(1, heavy_parse_ceiling // workers)
        light_parse_ceiling = max(1, light_parse_ceiling // workers)
        index_heavy_ceiling = max(1, index_heavy_ceiling // workers)
        # Floor at 1 only if the tier exists at all; a collapsed light tier
        # must stay collapsed, not be resurrected to 1 by the division.
        index_light_ceiling = (
            max(1, index_light_ceiling // workers) if index_light_ceiling else 0
        )

    return Ceilings(
        heavy=heavy_parse_ceiling,
        light=light_parse_ceiling,
        index_heavy=index_heavy_ceiling,
        index_light=index_light_ceiling,
    )


def start_rate_limiter_params(reference_ceiling: int) -> tuple[float, int]:
    """``(interval, capacity)`` for a pool's ``StartRateLimiter``.

    ``reference_ceiling`` is the ceiling that should drive how fast this
    pool admits new work — ``ceilings.heavy`` for ``HEAVY_PARSE``, the only
    rate-limited pool.

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
    honoured exactly (never forced up to 2).

    Light parse and index both floor at half their ceiling instead: their
    per-slot cost is negligible next to a Docling working set, so ramping a
    Jira/Slack sync one permit at a time from 2 only adds latency. Half, not
    the full ceiling — a floor equal to the ceiling would leave the memory
    brake nothing to shrink. For index this half-floor is also what keeps
    the pool off the critical path at startup: it begins wide enough that
    the pipeline fills immediately, so making it adaptive does not
    reintroduce the near-serial first minute that pinning it at its ceiling
    was originally meant to avoid.
    """
    if _is_light_pool(pool) or _is_index_pool(pool):
        return min(max(COUNT_POOL_FLOOR, ceiling // 2), ceiling)
    return min(COUNT_POOL_FLOOR, ceiling)


def _index_pressure_floor(pool: Pool) -> int:
    """How many records of *pool*'s tier fit the shared minimum budget.

    The budget is ``COUNT_POOL_FLOOR`` heavy records' worth of memory, so the
    heavy tier keeps its familiar floor of 2 and the light tier gets the count
    that costs the same.
    """
    budget_gb = COUNT_POOL_FLOOR * INDEX_HEAVY_WORKING_SET_GB
    working_set = (
        INDEX_HEAVY_WORKING_SET_GB
        if pool is Pool.INDEX_HEAVY
        else INDEX_LIGHT_WORKING_SET_GB
    )
    return max(COUNT_POOL_FLOOR, int(budget_gb / working_set))


def pressure_floor(pool: Pool, ceiling: int) -> int:
    """Lowest limit a memory or CPU brake may shrink *pool* to.

    Distinct from ``floor_for``, which is a *warm-start* width — where the
    pool begins so it is useful from the first sample. For heavy and light
    the two coincide. For index they must not: it starts at half its ceiling
    so the pipeline fills immediately, but that figure is derived from CPU
    and says nothing about RAM, so a host whose memory cannot buffer that
    many records has to be able to shrink well past it. Without this split
    the MEM_SOFT brake clamps at the warm-start width and the pool never
    gives memory back.

    The index floors are expressed in *memory*, not in a flat count. A flat
    floor of 2 is the right depth for the heavy tier, whose records hold
    ~``INDEX_HEAVY_WORKING_SET_GB`` each — but applying the same 2 to light
    records, which this module already assumes cost 7.5x less, throws away an
    order of magnitude of throughput while saving no memory at all. Both tiers
    now floor at whatever number of *their own* records fits the same budget,
    so a memory-starved node still turns over a useful number of the cheap
    ones while heavy is held to a couple.
    """
    if _is_index_pool(pool):
        # Never above the warm-start width: brakes only ever reduce a limit, so
        # a floor above where the pool starts would let one *raise* it. On a
        # ceiling small enough for that to bind, the two coincide and the pool
        # simply has no room to shrink.
        return min(_index_pressure_floor(pool), floor_for(pool, ceiling))
    return floor_for(pool, ceiling)


def warm_start_limits(ceilings: Ceilings) -> Limits:
    """Starting limits: every adapted pool begins at its conservative floor
    and ramps toward its ceiling as samples prove the headroom is real. The
    cheap count pools (light, index) start at half theirs, which is wide
    enough that the pipeline fills immediately.

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
        Pool.INDEX_HEAVY: floor_for(Pool.INDEX_HEAVY, ceilings.index_heavy),
        Pool.INDEX_LIGHT: floor_for(Pool.INDEX_LIGHT, ceilings.index_light),
    })


def _ceiling_for(pool: Pool, ceilings: Ceilings) -> int:
    return {
        Pool.HEAVY_PARSE: ceilings.heavy,
        Pool.LIGHT_PARSE: ceilings.light,
        Pool.INDEX_HEAVY: ceilings.index_heavy,
        Pool.INDEX_LIGHT: ceilings.index_light,
    }[pool]


def _target_for(pool: Pool, snap: ResourceSnapshot, ceilings: Ceilings) -> int:
    """Section 4 "Targets per sample" — the value growth ramps toward, never
    a value jumped to directly."""
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
        # Same shape as heavy, different working set: an index permit holds a
        # downloaded buffer and its chunk/embedding state rather than a
        # Docling conversion. Clamped to ``pressure_floor``, the same bound the
        # brakes below respect — the memory gate has to be able to hold the
        # pool well under its *warm-start* width (that figure is CPU-derived
        # and says nothing about RAM), but not under the floor, or the two
        # paths disagree about how far down is too far and the walk-down
        # quietly undercuts the brake.
        ceiling = _ceiling_for(pool, ceilings)
        mem_cap = index_memory_cap(snap, pool)
        bound = ceiling if mem_cap is None else min(ceiling, mem_cap)
        return int(_clamp(bound, pressure_floor(pool, ceiling), ceiling))

    raise AssertionError(f"unhandled pool {pool!r}")  # exhaustive over Pool StrEnum


def _growth_step(
    pool: Pool, ceiling: int, state: PoolState, snap: ResourceSnapshot,
) -> tuple[int, PoolState]:
    """Size one grow transition, TCP-slow-start style (plan section 4).

    Returns ``(step, updated_state)``. A ``step`` of ``0`` means "hold this
    round" — the caller must not advance the limit, only persist the state.

    A pool that has already exited slow start (``in_slow_start=False``)
    uses the fixed linear step unconditionally — having exited means a
    previous grow already found the knee of the resource-usage curve for
    this pool.

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
    if not state.in_slow_start:
        return COUNT_POOL_STEP, state

    # A light parse or an in-flight record costs noise next to a co-located
    # heavy parse; a process-wide mem/cpu delta would otherwise freeze those
    # pools whenever Docling happens to allocate in the same interval. Their
    # own memory bound is index_memory_cap / the MEM_SOFT brake, not this.
    if _is_cpu_bound_pool(pool):
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
                return COUNT_POOL_STEP, replace(state, in_slow_start=False, slow_start_step=1)

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


def _target_shrink_step(pool: Pool, current: int, target: int) -> int:
    """How fast a pool walks down to a memory-derived target below it.

    Heavy steps by one: its ceiling is one slot per CPU, so the gap is small
    and a single sample of overshoot costs one Docling working set.

    Index halves the gap instead. Its ceiling scales into the hundreds, so
    closing a gap of 400 at one permit per ~15s sample would leave the
    container over-committed for well over an hour — on the small hosts
    where the cap actually binds, that is the whole batch. Halving converges
    in O(log) samples and cannot overshoot, because the caller floors the
    result at ``target``.
    """
    if _is_index_pool(pool):
        return max(COUNT_POOL_STEP, math.ceil((current - target) / 2))
    return COUNT_POOL_STEP


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
    floor = pressure_floor(pool, ceiling)
    shrink_step = COUNT_POOL_STEP
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

    # CPU brake is for CPU-bound heavy work. Light parses and in-flight
    # records are not, and shrinking them because Docling is saturating cores
    # only queues cheap Jira/Slack work behind a problem they are not causing.
    cpu_brake = _is_cpu_bound_pool(pool) and _cpu_brake_active(snap)

    # Collect every shrink rule that applies this sample and take the
    # tightest. Rules that fire together are describing one shortage from
    # different angles, so honouring only the gentler one would leave the
    # container over-committed for another whole interval.
    shrink_to: int | None = None
    incident = False

    if brake_pressure is not None and brake_pressure >= MEM_HARD:
        shrink_to = max(floor, current // 2)
        incident = True
    elif (brake_pressure is not None and brake_pressure >= MEM_SOFT) or cpu_brake:
        shrink_to = max(floor, current - shrink_step)

    # Heavy and index both have a target that live free memory can pull below
    # the ceiling (_target_for). Overall pressure can sit well under MEM_SOFT
    # while what's left is still too small for another Docling working set or
    # another in-flight record, so this walks the limit down to what memory
    # can actually hold without waiting for a brake that, by definition, only
    # trips once it is nearly too late. Light is unaffected: its target is
    # always the ceiling, so this can never fire for it.
    if current > target:
        walked = max(target, current - _target_shrink_step(pool, current, target))
        shrink_to = walked if shrink_to is None else min(shrink_to, walked)

    if shrink_to is not None:
        # An incident cooldown is assigned outright rather than max()'d: it is
        # the longer of the two windows, and it starts from this sample.
        cooldown_until = (
            now + INCIDENT_COOLDOWN_SECONDS
            if incident
            else max(state.cooldown_until, now + SHRINK_COOLDOWN_SECONDS)
        )
        return shrink_to, _reset_for_shrink(state, cooldown_until=cooldown_until)

    if pressure is None:
        # Cannot prove there is memory headroom to grow into — freeze.
        # (The CPU brake above still applies independently of this branch.)
        return current, replace(state, healthy_streak=0)

    # Every tier grows up to MEM_SOFT. GROW_BAND (default 0) is an optional
    # extra margin on heavy only. The cheap count pools (light, index) also
    # confirm faster and on weaker demand, because a permit they leave unused
    # costs a queued record rather than a committed GiB.
    cpu_bound = _is_cpu_bound_pool(pool)
    grow_threshold = MEM_SOFT - GROW_BAND if cpu_bound else MEM_SOFT
    confirm_samples = GROW_CONFIRM_SAMPLES if cpu_bound else LIGHT_GROW_CONFIRM_SAMPLES
    demand_threshold = (
        DEMAND_UTILISATION_THRESHOLD if cpu_bound else LIGHT_DEMAND_UTILISATION_THRESHOLD
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
