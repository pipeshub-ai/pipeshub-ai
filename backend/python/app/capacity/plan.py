"""``CapacityPlan``: the sizes of one container's services. Data only; see :mod:`.policy`."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

SERVICES = ("indexing", "query", "connectors", "embedding", "docling", "parsing", "extraction")


@dataclass(frozen=True)
class Provenance:
    """Why a value is what it is: ``cpu``, ``memory``, ``cell``, ``downstream``, ``unit`` or ``override``."""

    value: object
    source_class: str
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {"value": self.value, "source_class": self.source_class, "reason": self.reason}


@dataclass(frozen=True)
class CpuMem:
    cpus: float
    mem_bytes: int
    cpu_source: str
    mem_source: str


@dataclass(frozen=True)
class GovernorInputs:
    """What one service's resource governor is built from (``auto``)."""

    cpus: float
    reserve_embedding_cpus: bool
    workers: int  # governor processes sharing these CPUs (cells for indexing)
    heavy_cap: int | None = None


@dataclass(frozen=True)
class Widths:
    """Upper bounds only: warmth and memory, never the CPU control. Per cell unless marked."""

    heavy_permits: int
    parse_executor_slots: int
    inspect_workers: int
    raster_workers: int
    docling_workers_warm: int  # per container, Docling service only
    docling_workers_max: int
    embed_slots: int  # sum over embedding processes
    embed_processes: int
    embed_executor_threads: int
    embed_bulk_limit: int  # per container
    llm_permits: int  # per member
    stage_permits: int
    lease_redis_pool: int


@dataclass(frozen=True)
class BudgetPlan:
    cpu_tokens: int  # C
    untracked: int  # U
    mem_bytes: int  # M
    large_min_tokens: int
    floors: Mapping[str, int]
    desired_floors: Mapping[str, int]
    docling_floor_workers: int


@dataclass(frozen=True)
class ServicePlan:
    threads_env: Mapping[str, str] = field(default_factory=dict)
    governor: GovernorInputs | None = None


@dataclass(frozen=True)
class CapacityPlan:
    profile: str
    budget: CpuMem
    cells: int
    services: Mapping[str, ServicePlan]
    provenance: Mapping[str, Provenance]
    warnings: tuple[str, ...] = ()
    cell_cpus: float = 0.0
    embedding_reserve_cpus: float = 0.0
    member_budget: BudgetPlan | None = None
    widths: Widths | None = None
    fleet_ceilings: tuple[int, int, int, int] | None = None  # heavy, light, index_heavy, index_light
    embed_device: str = "cpu"
    numa_masks: tuple[tuple[int, ...], ...] = ()

    def as_dict(self) -> dict[str, object]:
        out = self._legacy_dict()
        if self.profile != "legacy":
            out.update({
                "cell_cpus": self.cell_cpus,
                "embedding_reserve_cpus": self.embedding_reserve_cpus,
                "embed_device": self.embed_device,
                "fleet_ceilings": list(self.fleet_ceilings or ()),
                "member_budget": None if self.member_budget is None else self.member_budget.__dict__ | {
                    "floors": dict(self.member_budget.floors), "desired_floors": dict(self.member_budget.desired_floors)},
                "widths": None if self.widths is None else dict(self.widths.__dict__),
                "numa_masks": [list(m) for m in self.numa_masks],
            })
        return out

    def _legacy_dict(self) -> dict[str, object]:
        return {
            "profile": self.profile,
            "budget": {
                "cpus": self.budget.cpus,
                "mem_bytes": self.budget.mem_bytes,
                "cpu_source": self.budget.cpu_source,
                "mem_source": self.budget.mem_source,
            },
            "cells": self.cells,
            "services": {n: {"threads_env": dict(s.threads_env)} for n, s in self.services.items()},
            "provenance": {k: v.as_dict() for k, v in self.provenance.items()},
            "warnings": list(self.warnings),
        }
