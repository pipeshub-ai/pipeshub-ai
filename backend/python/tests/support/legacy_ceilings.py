"""A frozen copy of the governor's ``resolve_ceilings`` chain as it was before the capacity profile.

``tests/unit/capacity/test_legacy_equivalence.py`` compares the live function with this copy over
a grid. The copy takes its constants as arguments so the grid can vary them; its default values
are today's. Do not edit it to follow a change in the governor: a difference is the finding.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Constants:
    heavy_slots_per_cpu: float = 1.0
    light_slots_per_cpu: float = 10.0
    light_parse_max: int = 256
    embedding_reservation: float = 2.0
    embedding_reservation_max_fraction: float = 0.25
    index_headroom: float = 2.0
    index_min_per_tier: int = 8
    index_max_per_tier: int = 512
    index_slots_per_cpu: float = 6.0
    index_total_min: int = 24
    index_total_max: int = 96
    index_heavy_max_share: float = 2.0 / 3.0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _worker_share(total: int, worker_count: int) -> int:
    workers = max(1, worker_count)
    return total if workers == 1 else max(1, total // workers)


def ceiling_cpus(cpu_quota: float) -> float:
    if cpu_quota < 1.0:
        return max(0.0, cpu_quota)
    return float(math.floor(cpu_quota))


def resolve(
    cpu_quota: float,
    env_parse: int | None,
    env_index: int | None,
    worker_count: int = 1,
    *,
    reserve_embedding_cpus: bool = False,
    c: Constants = Constants(),
) -> tuple[int, int, int, int]:
    """``(heavy, light, index_heavy, index_light)``."""
    cpus = ceiling_cpus(cpu_quota)
    reservation = min(c.embedding_reservation, cpus * c.embedding_reservation_max_fraction)
    heavy_cpus = max(0.0, cpus - reservation) if reserve_embedding_cpus else cpus
    parse_cap = max(1, env_parse) if env_parse is not None else None

    heavy_parse_ceiling = max(1, math.floor(heavy_cpus * c.heavy_slots_per_cpu))
    light_parse_ceiling = max(1, min(math.floor(cpus * c.light_slots_per_cpu), c.light_parse_max))
    if parse_cap is not None:
        heavy_parse_ceiling = min(heavy_parse_ceiling, parse_cap)
        light_parse_ceiling = min(light_parse_ceiling, parse_cap)

    def _index_ceiling(parse_ceiling: int) -> int:
        return int(_clamp(
            math.ceil(parse_ceiling * c.index_headroom),
            min(c.index_min_per_tier, parse_ceiling, c.index_max_per_tier),
            c.index_max_per_tier,
        ))

    index_heavy_ceiling = _index_ceiling(heavy_parse_ceiling)
    index_light_ceiling = _index_ceiling(light_parse_ceiling)

    if env_index is not None:
        allowed = max(1, env_index)
    else:
        allowed = int(_clamp(cpus * c.index_slots_per_cpu, c.index_total_min, c.index_total_max))
    if index_heavy_ceiling + index_light_ceiling > allowed:
        light_reserve = min(c.index_min_per_tier, index_light_ceiling, allowed // 2)
        heavy_cap = max(1, int(allowed * c.index_heavy_max_share))
        index_heavy_ceiling = min(index_heavy_ceiling, allowed - light_reserve, heavy_cap)
        index_light_ceiling = min(index_light_ceiling, allowed - index_heavy_ceiling)

    heavy_parse_ceiling = max(1, min(heavy_parse_ceiling, index_heavy_ceiling))
    light_parse_ceiling = (
        max(1, min(light_parse_ceiling, index_light_ceiling)) if index_light_ceiling else 1
    )

    return (
        _worker_share(heavy_parse_ceiling, worker_count),
        _worker_share(light_parse_ceiling, worker_count),
        _worker_share(index_heavy_ceiling, worker_count),
        _worker_share(index_light_ceiling, worker_count) if index_light_ceiling else 0,
    )
