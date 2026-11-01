"""The lane plan: how many parse lanes a container can carry and how many
threads each gets.

Pure arithmetic over numbers the caller already has. Nothing here opens a
file, a descriptor or a thread, so it is safe to call from ``/health`` in any
mode.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.services.resource_governor.controller import ResourceGovernor

# Upper bound on lanes in one container (the legacy pool capped itself at 4).
MAX_LANES = 64
CPUS_PER_LANE = 2
MAX_THREADS_PER_LANE = 4

# Nothing consumes the plan yet, so the mode is always "off".
LANES_MODE = "off"


@dataclass(frozen=True)
class LanePlan:
    cpus: int
    heavy: int
    lanes: int
    threads_per_lane: int
    cpus_per_lane: int
    source: str


def threads_rule(cpus: int, lanes: int) -> int:
    """Placeholder until the measured table replaces it: keeps
    ``lanes x threads <= cpus`` with 1..4 threads per lane."""
    return max(1, min(MAX_THREADS_PER_LANE, cpus // max(1, lanes)))


def plan_lanes(
    *,
    cpus: int,
    heavy: int,
    cpus_per_lane: int = CPUS_PER_LANE,
    max_lanes: int = MAX_LANES,
    explicit_threads: int | None = None,
) -> LanePlan:
    """Lanes follow the heavy ceiling (``heavy // cpus_per_lane``, at least 1).

    Explicit pool sizes (``PARSE_POOL_WORKERS`` and friends) are private pools
    and deliberately have no parameter here. An explicit thread count wins over
    the rule, as ``PDF_TABLE_MODEL_THREADS`` does today.
    """
    # Never more lanes than CPUs, even if an explicit parse cap lifts heavy above them.
    lanes = max(1, min(heavy // max(1, cpus_per_lane), max_lanes, cpus))
    if explicit_threads is not None and explicit_threads > 0:
        return LanePlan(cpus, heavy, lanes, explicit_threads, cpus_per_lane, "explicit_threads")
    return LanePlan(cpus, heavy, lanes, threads_rule(cpus, lanes), cpus_per_lane, "rule")


def lanes_health(governor: ResourceGovernor | None, cpus: int) -> dict[str, Any]:
    """The ``lanes`` block of ``/health``: mode and plan, nothing opened."""
    if governor is None:
        return {"mode": LANES_MODE, "plan": None, "in_use_domain": None}
    try:
        explicit = int(os.environ.get("PDF_TABLE_MODEL_THREADS", ""))
    except ValueError:
        explicit = None
    plan = plan_lanes(cpus=cpus, heavy=governor.fleet_ceilings.heavy, explicit_threads=explicit)
    return {"mode": LANES_MODE, "plan": asdict(plan), "in_use_domain": None}
