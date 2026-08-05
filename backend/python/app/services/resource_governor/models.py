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
    DOWNLOAD_BYTES = "download_bytes"
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
    def mem_pressure(self) -> float | None:
        """Fraction of the memory limit currently in use, or ``None`` if
        either the limit or the working set is unknown.

        Uses ``mem_working_set_bytes`` — already baseline-adjusted by the
        probe when auto-calibration or ``GOVERNOR_BASELINE_MEMORY_MB`` is in
        effect — not the raw cgroup reading."""
        if self.mem_limit_bytes is None or self.mem_working_set_bytes is None:
            return None
        if self.mem_limit_bytes <= 0:
            return None
        return self.mem_working_set_bytes / self.mem_limit_bytes


@dataclass(frozen=True)
class Ceilings:
    """Operator/derived upper bounds, resolved once at startup.

    ``light`` is sized independently of ``heavy`` (plan section 4.2) — light
    parses are milliseconds of CPU on a few KB and must never be capped by
    heavy-parse memory sizing.
    """

    heavy: int
    light: int
    index: int
    bytes_max: int


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
    otherwise be invisible to a 5s sampler).
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

    def mean_wait_seconds(self) -> float:
        if self.blocked_acquires <= 0:
            return 0.0
        return self.total_wait_seconds / self.blocked_acquires

    def completions_per_second(self, interval: float) -> float:
        if interval <= 0:
            return 0.0
        return self.completions / interval


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
    """

    healthy_streak: int = 0
    cooldown_until: float = 0.0
    gradient_baseline_completions_per_sec: float | None = None
    gradient_baseline_wait_seconds: float | None = None
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
