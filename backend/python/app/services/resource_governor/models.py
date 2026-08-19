"""Frozen value types shared across the resource governor package.

Kept dependency-free (stdlib only) so ``policy.py`` can stay pure and every
other module in this package can import from here without cycles.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


class Pool(StrEnum):
    """Admission pools governed independently by the ResourceGovernor."""

    INDEX = "index"
    HEAVY_PARSE = "heavy_parse"
    LIGHT_PARSE = "light_parse"


class ParseTier(StrEnum):
    """Which parse pool a document's format routes to."""

    HEAVY = "heavy"
    LIGHT = "light"


@dataclass(frozen=True)
class ResourceSnapshot:
    """A single point-in-time read of host/cgroup resources.

    Any field the probe chain could not determine is ``None`` — callers must
    treat unknown as "assume nothing is provable", never as zero. See plan
    section 5 for the cross-platform probe chain that produces this.
    """

    cpu_quota: float
    cpu_utilisation: float | None
    cpu_throttled_ratio: float | None
    cpu_pressure: float | None
    mem_limit_bytes: int | None
    mem_working_set_bytes: int | None
    source: str
    mem_working_set_raw_bytes: int | None = None
    """Working set as read from cgroup/proc, before baseline subtraction —
    kept alongside the (possibly baseline-adjusted) ``mem_working_set_bytes``
    purely for observability/logging (plan: "Fix 1 — Baseline Memory
    Reservation"). Defaults to ``None`` so callers/tests built before this
    field existed keep working unchanged."""
    mem_baseline_bytes: int | None = None
    """Bytes subtracted from the raw working set before it became
    ``mem_working_set_bytes`` — the probe's estimate of co-located idle
    memory (e.g. a sibling Docling process's model weights) that isn't
    driven by this pool's own workload. ``None`` means no adjustment was
    applied (baseline still calibrating, or none configured)."""

    @property
    def mem_usable_bytes(self) -> int | None:
        """The limit less the baseline: memory this workload can actually
        grow into, and the denominator ``mem_pressure`` divides by."""
        if self.mem_limit_bytes is None or self.mem_limit_bytes <= 0:
            return None
        usable = self.mem_limit_bytes - (self.mem_baseline_bytes or 0)
        return usable if usable > 0 else None

    @property
    def mem_pressure(self) -> float | None:
        """Fraction of the memory available to this workload currently in
        use, or ``None`` if either the limit or the working set is unknown.

        Both sides of the ratio exclude the baseline, so a genuinely full
        cgroup still reads 1.0. Dividing the baseline-adjusted working set
        by the *raw* limit would cap the achievable reading at
        ``1 - baseline / limit``; with the multi-GB baseline of an
        all-in-one container that ceiling falls below MEM_SOFT, and no
        amount of real pressure could ever trip the brake before the kernel
        OOM-kills the container.

        Governs *growth* only. Because the baseline is credited to both
        sides, this reads lower than the cgroup's true occupancy, so the
        shrink brake reads ``mem_pressure_raw`` instead — see
        ``policy._next_pool_limit``.
        """
        if self.mem_working_set_bytes is None:
            return None
        usable = self.mem_usable_bytes
        if usable is None:
            if self.mem_limit_bytes is None or self.mem_limit_bytes <= 0:
                return None
            # A baseline swallowing the whole limit leaves no headroom to
            # reason about. Report the raw ratio (~1.0) rather than the
            # adjusted one (~0.0), which would read as idle and invite
            # growth into a cgroup that is already full.
            raw = self.mem_working_set_raw_bytes
            return (raw if raw is not None else self.mem_working_set_bytes) / self.mem_limit_bytes
        return self.mem_working_set_bytes / usable

    @property
    def mem_pressure_raw(self) -> float | None:
        """Fraction of the cgroup limit in use with no baseline credited —
        the occupancy the kernel OOM killer actually acts on.

        Always greater than or equal to ``mem_pressure``, and the gap widens
        with the baseline: crediting 3GiB of a 12GiB container reports 70%
        pressure only once the cgroup is genuinely 78% full. Growth needs
        that credit (otherwise a co-located service's idle footprint pins
        every pool at its floor forever), but a shrink decision must not
        inherit it — the container has to be able to brake while there is
        still headroom left to brake into.
        """
        if self.mem_limit_bytes is None or self.mem_limit_bytes <= 0:
            return None
        # Pre-baseline callers/tests leave the raw field unset, in which case
        # mem_working_set_bytes is itself unadjusted (mirrors
        # policy._free_memory_gb).
        resident = self.mem_working_set_raw_bytes
        if resident is None:
            resident = self.mem_working_set_bytes
        if resident is None:
            return None
        return resident / self.mem_limit_bytes


@dataclass(frozen=True)
class Ceilings:
    """Derived upper bounds, resolved once at startup (policy.resolve_ceilings).

    ``light`` gets more slots per CPU than ``heavy``: a light parse is
    milliseconds of CPU on a few KB and must not be limited to what a
    Docling conversion costs.

    ``index`` bounds the active pipeline — heavy and light records together,
    not one each — and, unlike the parse ceilings, is also the pool's
    effective limit for the life of the process, since the control law does
    not adapt it (policy ``_is_index_pool``).
    """

    heavy: int
    light: int
    index: int


@dataclass(frozen=True)
class Limits:
    """Current effective limit per pool.

    Value type: treated as immutable everywhere in this package. Use
    ``with_update`` to derive a new instance rather than mutating ``values``.
    """

    values: Mapping[Pool, int]

    def get(self, pool: Pool) -> int:
        return self.values[pool]

    def with_update(self, pool: Pool, value: int) -> "Limits":
        updated = dict(self.values)
        updated[pool] = value
        return Limits(values=updated)


@dataclass(frozen=True)
class PoolDemand:
    """Demand accumulated by an ``AdmissionGate`` over one sample interval.

    Built from running totals folded on every acquire/release rather than a
    point sample — see plan section 4.1. ``blocked_acquires`` alone proves
    demand existed even if it had fully drained before the next sample, and
    ``permit_seconds`` gives a true mean occupancy immune to short hold
    times (thousands of millisecond-scale Jira/Confluence block parses would
    otherwise be invisible to a periodic sampler).
    """

    permit_seconds: float = 0.0
    blocked_acquires: int = 0
    total_wait_seconds: float = 0.0
    completions: int = 0
    max_in_use: int = 0
    rate_limited_acquires: int = 0
    """Acquires denied purely by a ``StartRateLimiter`` while capacity was
    otherwise free — the diagnostic signal that separates "genuinely at the
    concurrency limit" from "throttled by the burst smoother regardless of
    limit" (see gate.py ``AdmissionGate._try_admit``)."""

    @staticmethod
    def empty() -> "PoolDemand":
        return PoolDemand()

    def utilisation(self, limit: int, interval: float) -> float:
        """Mean occupancy over the interval, immune to hold time."""
        if limit <= 0 or interval <= 0:
            return 0.0
        return min(1.0, self.permit_seconds / (limit * interval))

    def has_demand(self, limit: int, interval: float, *, threshold: float = 0.7) -> bool:
        """Whether this pool showed real contention during the interval."""
        return self.blocked_acquires > 0 or self.utilisation(limit, interval) >= threshold


@dataclass(frozen=True)
class PoolState:
    """Controller memory for one pool, carried between samples.

    ``in_slow_start``/``slow_start_step`` implement TCP-slow-start-inspired
    exponential ramp for count pools (policy.py ``_growth_step``): the step
    doubles on every grow whose resource impact was small, reaching a
    ceiling of e.g. 1000 in ~10 intervals instead of ~1000. Any shrink resets
    both fields so recovery after a pressure incident is exponential too,
    not the linear +1/interval a fresh floor start would otherwise take.

    ``prev_grow_mem_pressure``/``prev_grow_cpu_utilisation`` are the
    resource snapshot recorded at the *previous* grow step — the baseline
    ``_growth_step`` diffs against to size the *next* step (plan section 4,
    "resource-delta probing").

    Carried for every pool, but only read for the adapted ones: the index
    pool holds its limit for the life of the process (policy.py
    ``_is_index_pool``), so its state is never advanced.
    """

    healthy_streak: int = 0
    cooldown_until: float = 0.0
    in_slow_start: bool = True
    slow_start_step: int = 1
    prev_grow_mem_pressure: float | None = None
    prev_grow_cpu_utilisation: float | None = None


@dataclass(frozen=True)
class ControllerState:
    """All ``PoolState``, keyed by pool.

    Value type: replaced wholesale by ``policy.next_limits`` every sample,
    never mutated in place.
    """

    pools: Mapping[Pool, PoolState]

    @staticmethod
    def initial() -> "ControllerState":
        return ControllerState(pools={pool: PoolState() for pool in Pool})

    def get(self, pool: Pool) -> PoolState:
        return self.pools.get(pool, PoolState())

    def with_update(self, pool: Pool, state: PoolState) -> "ControllerState":
        updated = dict(self.pools)
        updated[pool] = state
        return ControllerState(pools=updated)
