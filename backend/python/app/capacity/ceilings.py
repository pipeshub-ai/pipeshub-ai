"""The ``auto`` governor ceilings: every cap derived from CPUs, memory or cells, with its source.

Pure and stdlib only, so the plan can state the ceilings without importing the governor, and
``resource_governor.policy.resolve_ceilings(profile="auto")`` delegates here.

At 16 CPUs or fewer, and with one cell, the result equals the ``legacy`` chain's. Above that:

* the in-flight total is ``max(INDEX_TOTAL_MIN, 6 x cpus)``: the fixed 96 is gone (class ``cpu``);
* the light parse cap and the per-tier cap are per event loop, so they are multiplied by the
  number of cells (class ``cell``);
* the local embedding model keeps ``max(min(2, 0.25 x cpus), cpus / 8)`` CPUs (class ``cpu``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Constants:
    """The governor's tunables. The defaults are the governor's own (a test keeps them equal)."""

    heavy_slots_per_cpu: float = 1.0
    light_slots_per_cpu: float = 10.0
    light_parse_max: int = 256  # per cell
    embedding_reservation: float = 2.0
    embedding_reservation_max_fraction: float = 0.25
    embedding_reservation_cpus_per_slot: float = 8.0  # cpus / 8 above the fixed reservation
    index_headroom: float = 2.0
    index_min_per_tier: int = 8
    index_max_per_tier: int = 512  # per cell
    index_slots_per_cpu: float = 6.0
    index_total_min: int = 24
    index_heavy_max_share: float = 2.0 / 3.0


@dataclass(frozen=True)
class FleetCeilings:
    heavy: int
    light: int
    index_heavy: int
    index_light: int

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (self.heavy, self.light, self.index_heavy, self.index_light)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def ceiling_cpus(cpu_quota: float) -> float:
    """``floor(quota)`` from one CPU up; below one the fraction is kept (as the governor does)."""
    if cpu_quota < 1.0:
        return max(0.0, cpu_quota)
    return float(math.floor(cpu_quota))


def embedding_reservation(cpus: float, c: Constants = Constants()) -> float:
    return max(
        min(c.embedding_reservation, cpus * c.embedding_reservation_max_fraction),
        cpus / c.embedding_reservation_cpus_per_slot,
    )


def auto_ceilings(
    cpu_quota: float,
    env_parse: int | None,
    env_index: int | None,
    *,
    cells: int = 1,
    reserve_embedding_cpus: bool = False,
    c: Constants = Constants(),
    reservation: float | None = None,
) -> FleetCeilings:
    """Fleet ceilings (all cells together); the governor splits them per cell.

    ``reservation`` is the CPUs kept for the embedding model when ``reserve_embedding_cpus`` is set;
    the default is ``embedding_reservation`` of the quota. A plan that knows better (a free GPU needs
    at most one) passes its own, so the reported reservation and the ceilings agree.
    """
    cells = max(1, cells)
    cpus = ceiling_cpus(cpu_quota)
    reservation = embedding_reservation(cpus, c) if reservation is None else reservation
    heavy_cpus = max(0.0, cpus - reservation) if reserve_embedding_cpus else cpus
    light_max = c.light_parse_max * cells
    tier_max = c.index_max_per_tier * cells
    parse_cap = max(1, env_parse) if env_parse is not None else None

    heavy_parse = max(1, math.floor(heavy_cpus * c.heavy_slots_per_cpu))
    light_parse = max(1, min(math.floor(cpus * c.light_slots_per_cpu), light_max))
    if parse_cap is not None:
        heavy_parse = min(heavy_parse, parse_cap)
        light_parse = min(light_parse, parse_cap)

    def index_ceiling(parse_ceiling: int) -> int:
        return int(_clamp(
            math.ceil(parse_ceiling * c.index_headroom),
            min(c.index_min_per_tier, parse_ceiling, tier_max),
            tier_max,
        ))

    index_heavy = index_ceiling(heavy_parse)
    index_light = index_ceiling(light_parse)

    if env_index is not None:
        allowed = max(1, env_index)
    else:
        allowed = max(c.index_total_min, int(cpus * c.index_slots_per_cpu))
    if index_heavy + index_light > allowed:
        light_reserve = min(c.index_min_per_tier, index_light, allowed // 2)
        heavy_cap = max(1, int(allowed * c.index_heavy_max_share))
        index_heavy = min(index_heavy, allowed - light_reserve, heavy_cap)
        index_light = min(index_light, allowed - index_heavy)

    heavy_parse = max(1, min(heavy_parse, index_heavy))
    light_parse = max(1, min(light_parse, index_light)) if index_light else 1
    return FleetCeilings(heavy_parse, light_parse, index_heavy, index_light)


def share(total: int, workers: int) -> int:
    workers = max(1, workers)
    return total if workers == 1 else max(1, total // workers)


def per_cell(fleet: FleetCeilings, cells: int) -> FleetCeilings:
    return FleetCeilings(
        share(fleet.heavy, cells),
        share(fleet.light, cells),
        share(fleet.index_heavy, cells),
        share(fleet.index_light, cells) if fleet.index_light else 0,
    )
