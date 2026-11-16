"""The ``auto`` plan: every cap derived from the container's CPUs, memory or cell count (pure).

Stdlib only. Called by :func:`app.capacity.policy.plan_capacity` when
``PIPESHUB_CAPACITY_PROFILE=auto``. Every value in the plan has a ``Provenance``.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from app.capacity import ceilings as ceil_mod
from app.capacity import numa
from app.capacity.budget_policy import (
    DOCLING_WEIGHT,
    QUERY_EMBED_WEIGHT,
    AdmissionPolicy,
    plan_floors,
    slots,
)
from app.capacity.knobs import knob_value, positive_float
from app.capacity.plan import (
    SERVICES,
    BudgetPlan,
    CapacityPlan,
    CpuMem,
    GovernorInputs,
    Provenance,
    ServicePlan,
    Widths,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.capacity.facts import CapacityFacts

GIB = 1 << 30
BASE_MEMORY = 5 * GIB  # Node, connectors, query and one embedding model
CELL_MIN_MEMORY = 4 * GIB  # one indexing process, its index pools and one heavy parse
DOCLING_WORKING_SET = int(1.5 * GIB)
EMBEDDING_MODEL_RSS = 2 * GIB
DOCLING_MEMORY_SHARE = 0.25
HEADROOM_SHARE = 0.05
HEADROOM_MIN = 512 * (1 << 20)
GPU_MODEL_BYTES = 2 * GIB
EMBED_SLOTS_PER_PROCESS = 32
LLM_PER_SERVICE = 24
STAGE_PERMITS = 24
DEFAULT_OMP = 2
MAX_CELLS = 64


def _int(env: Mapping[str, str], name: str) -> int | None:
    v = knob_value(env, name)
    return None if v is None else int(v)  # type: ignore[call-overload]


def _float(env: Mapping[str, str], name: str, default: float, warnings: list[str], *,
           minimum: float = 0.0, maximum: float = math.inf) -> float:
    """A calibration value: positive and finite (and within ``minimum`` .. ``maximum``), else the default with a warning."""
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    value = positive_float(raw, maximum=maximum)
    if value is None or value < minimum:
        warnings.append(f"invalid_{name}")
        return default
    return value


def untracked_threads(cpus: float, cells: int) -> int:
    """``U``: the event loops and the service main threads that are not tokens."""
    return cells + 1 if cpus >= 8 else cells


def cpu_tokens(cpus: float, cells: int, oversub: float) -> int:
    u = untracked_threads(cpus, cells)
    if cpus >= 8:
        return max(math.floor(oversub * cpus + 1e-9) - u, 4)
    return max(math.ceil(cpus) + 2 - u, 4)


def _gpu(facts: CapacityFacts, warnings: list[str]) -> int:
    """GPUs with room for the embedding model. A GPU that is present and full is ``gpu_busy``."""
    if not facts.gpus:
        return 0
    free = [g for g in facts.gpus if g[1] - g[2] >= GPU_MODEL_BYTES]
    if not free:
        warnings.append("gpu_busy")
    return len(free)


def _omp(env: Mapping[str, str], cpus: float) -> tuple[str, str]:
    operator = (env.get("OMP_NUM_THREADS") or "").strip()
    if operator:
        return operator, "override"
    if cpus < 2:
        return "1", "cpu"
    image = (env.get("PIPESHUB_OMP_DEFAULT") or "").strip()
    return (image, "unit") if image.isascii() and image.isdigit() and int(image) > 0 else (str(DEFAULT_OMP), "unit")


def _embedding_widths(c: int, omp_n: int, override: int | None, *, cuda: bool, gpus: int) -> tuple[int, int]:
    """``(slots, processes)`` of the embedding service for ``c`` CPU tokens."""
    if cuda:
        return override or 2 * gpus, gpus
    slots = override or max(2, (c - QUERY_EMBED_WEIGHT) // max(1, omp_n))
    return slots, max(1, math.ceil(slots / EMBED_SLOTS_PER_PROCESS))


def plan_auto(facts: CapacityFacts, env: Mapping[str, str], budget: CpuMem, warnings: list[str]) -> CapacityPlan:
    cpus, mem = budget.cpus, budget.mem_bytes
    oversub = _float(env, "PIPESHUB_CPU_OVERSUB", 1.25, warnings, maximum=16.0)
    cell_cpus_knob = _float(env, "PIPESHUB_CAPACITY_CELL_CPUS", 16.0, warnings, minimum=1.0)
    policy = AdmissionPolicy()
    workers = _int(env, "INDEXING_UVICORN_WORKERS")
    if workers is not None and workers > MAX_CELLS:
        warnings.append("cells_capped")  # more workers run than the plan has cells for
    gpus = _gpu(facts, warnings)
    cuda = gpus > 0
    reserve_embedding = facts.local_cpu_embedding or cuda
    env_parse = _int(env, "MAX_CONCURRENT_PARSING")
    env_index = _int(env, "MAX_CONCURRENT_INDEXING")
    s_d = 1.0
    omp, omp_source = _omp(env, cpus)
    omp_n = int(omp) if omp.isascii() and omp.isdigit() else DEFAULT_OMP
    embed_override = _int(env, "EMBEDDING_SERVER_MAX_CONCURRENCY")

    headroom = max(int(mem * HEADROOM_SHARE), HEADROOM_MIN)
    big_mem = max(0, mem - headroom)
    cpu_cells = max(1, math.floor(cpus / cell_cpus_knob + 1e-9))
    cells = max(1, min(workers, MAX_CELLS)) if workers is not None else min(cpu_cells, MAX_CELLS)
    docling_cap_mem: int | None = None
    for _ in range(4):  # cells -> C -> floors -> Docling memory -> cells settles in a step or two
        c = cpu_tokens(cpus, cells, oversub)
        base_no_docling = BASE_MEMORY
        docling_cap_mem = max(1, int(max(0, mem - base_no_docling) * DOCLING_MEMORY_SHARE // DOCLING_WORKING_SET))
        floors = plan_floors(cpus, c, None, s_d, policy, docling_workers_cap=docling_cap_mem)
        # Every embedding process beyond the first holds its own model copy in memory (a GPU holds it on the device).
        embed_procs = _embedding_widths(c, omp_n, embed_override, cuda=cuda, gpus=gpus)[1]
        baseline = (
            BASE_MEMORY
            + floors.docling_warm_workers * DOCLING_WORKING_SET
            + (0 if cuda else (embed_procs - 1) * EMBEDDING_MODEL_RSS)
        )
        mem_cells = max(1, int(max(0, mem - baseline) // CELL_MIN_MEMORY))
        if workers is not None:
            break
        wanted = max(1, min(cpu_cells, mem_cells, MAX_CELLS))
        if wanted == cells:
            break
        cells = wanted
    c = cpu_tokens(cpus, cells, oversub)
    u = untracked_threads(cpus, cells)
    if c < cells:
        warnings.append("cpu_tokens_below_cells")
    docling_cap_mem = max(1, int(max(0, mem - BASE_MEMORY) * DOCLING_MEMORY_SHARE // DOCLING_WORKING_SET))
    floors = plan_floors(cpus, c, None, s_d, policy, docling_workers_cap=docling_cap_mem)
    warnings.extend(floors.warnings)
    if mem < DOCLING_WORKING_SET:
        warnings.append("memory_below_one_heavy_parse")
    if workers is None and mem_cells < cpu_cells:
        warnings.append("cells_bounded_by_memory")
    cell_cpus = cpus / cells

    # Local embedding keeps CPUs back from parsing; a free GPU needs at most one.
    reserve_cpus = ceil_mod.embedding_reservation(ceil_mod.ceiling_cpus(cpus)) if reserve_embedding else 0.0
    if cuda:
        reserve_cpus = min(1.0, reserve_cpus)
    constants = ceil_mod.Constants(
        heavy_slots_per_cpu=_float(env, "GOVERNOR_HEAVY_PARSE_SLOTS_PER_CPU", 1.0, warnings),
        light_slots_per_cpu=_float(env, "GOVERNOR_LIGHT_PARSE_SLOTS_PER_CPU", 10.0, warnings),
        light_parse_max=_int(env, "GOVERNOR_LIGHT_PARSE_MAX") or 256,
        index_headroom=_float(env, "GOVERNOR_INDEX_HEADROOM", 2.0, warnings),
        index_max_per_tier=_int(env, "GOVERNOR_INDEX_MAX") or 512,
    )
    fleet = ceil_mod.auto_ceilings(
        cpus, env_parse, env_index, cells=cells, reserve_embedding_cpus=reserve_embedding, c=constants,
        reservation=reserve_cpus,
    )
    per = ceil_mod.per_cell(fleet, cells)

    embed_slots, embed_processes = _embedding_widths(c, omp_n, embed_override, cuda=cuda, gpus=gpus)
    llm_services = 1 + (2 if (env.get("USE_PARSING_SERVICE") or "").strip().lower() == "true" else 0)
    llm_each = _int(env, "MAX_CONCURRENT_INDEXING_LLM_CALLS") or LLM_PER_SERVICE
    widths = Widths(
        heavy_permits=per.heavy,
        parse_executor_slots=per.heavy,
        inspect_workers=max(1, min(4, int(cell_cpus // 8))),
        raster_workers=max(1, min(int(cell_cpus), 2)),
        docling_workers_warm=floors.docling_warm_workers,
        docling_workers_max=floors.docling_max_workers,
        embed_slots=embed_slots,
        embed_processes=embed_processes,
        embed_executor_threads=embed_slots + 1 + 2,
        embed_bulk_limit=embed_slots,
        llm_permits=llm_each * llm_services,
        stage_permits=STAGE_PERMITS,
        lease_redis_pool=max(32, math.ceil((per.index_heavy + per.index_light) / 3)),
    )
    member = BudgetPlan(
        cpu_tokens=c,
        untracked=u,
        mem_bytes=big_mem,
        large_min_tokens=slots(policy.large_min, c),
        floors={f.value: v for f, v in floors.by_family.items()},
        desired_floors={f.value: v for f, v in floors.desired.items()},
        docling_floor_workers=floors.docling_warm_workers,
    )
    governors = {
        "indexing": GovernorInputs(cpus, reserve_embedding, cells),
        "docling": GovernorInputs(float(floors.docling_max_workers * DOCLING_WEIGHT), False, 1, floors.docling_max_workers),
        "query": GovernorInputs(cpus, False, 1, max(1, min(4, int(0.1 * cpus)))),
        "parsing": GovernorInputs(cpus, False, 1),
    }
    thread_env = {
        "OMP_NUM_THREADS": omp, "OPENBLAS_NUM_THREADS": omp, "MKL_NUM_THREADS": omp, "TOKENIZERS_PARALLELISM": "false",
    }
    services = {n: ServicePlan(threads_env=dict(thread_env), governor=governors.get(n)) for n in SERVICES}
    one = {k: "1" if k.endswith("THREADS") else v for k, v in thread_env.items()}
    services["parse_worker"] = ServicePlan(threads_env=one)

    ov = lambda present: "override" if present else None  # noqa: E731
    prov: dict[str, Provenance] = {
        "budget.cpus": Provenance(cpus, "override" if budget.cpu_source == "override" else "cpu", budget.cpu_source),
        "budget.mem_bytes": Provenance(mem, "override" if budget.mem_source == "override" else "memory", budget.mem_source),
        "cells": Provenance(cells, ov(workers is not None) or ("memory" if cells < cpu_cells else "cell"),
                            "INDEXING_UVICORN_WORKERS" if workers is not None else f"min(cpus/{cell_cpus_knob:g}, memory)"),
        "cell_cpus": Provenance(cell_cpus, "cell", "cpus / cells"),
        "embedding_reserve_cpus": Provenance(reserve_cpus, "cpu", "CPUs kept for the local embedding model"),
        "member.cpu_tokens": Provenance(c, "cpu", f"floor({oversub:g} x cpus) - U"),
        "member.untracked": Provenance(u, "unit", "event loops plus the service main threads"),
        "member.mem_bytes": Provenance(big_mem, "memory", "memory budget less 5 % headroom"),
        "member.large_min_tokens": Provenance(member.large_min_tokens, "cpu", "20 % of C kept for large work"),
        "omp_threads": Provenance(omp, omp_source, "threads of one ML library call"),
    }
    for f, v in floors.by_family.items():
        prov[f"floor.{f.value}"] = Provenance(v, "cpu", "design arrival rate x service time / 0.55, in tokens")
    for name, value in widths.__dict__.items():
        if name == "llm_permits":
            prov[f"widths.{name}"] = Provenance(value, "override" if _int(env, "MAX_CONCURRENT_INDEXING_LLM_CALLS") else "downstream",
                                                f"{llm_each} per LLM-calling service x {llm_services}")
        elif name in ("stage_permits", "lease_redis_pool"):
            prov[f"widths.{name}"] = Provenance(value, "downstream" if name == "stage_permits" else "cell", name)
        elif name.startswith("embed") and embed_override:
            prov[f"widths.{name}"] = Provenance(value, "override", "EMBEDDING_SERVER_MAX_CONCURRENCY")
        elif name.startswith("docling_workers") and "docling_floor_capped" in floors.warnings:
            prov[f"widths.{name}"] = Provenance(value, "memory", "bounded by 25 % of memory")
        else:
            prov[f"widths.{name}"] = Provenance(value, "cpu" if name != "inspect_workers" else "cell", name)
    for name, value in zip(("heavy", "light", "index_heavy", "index_light"), fleet.as_tuple(), strict=True):
        prov[f"ceilings.{name}"] = Provenance(value, "cell" if name in ("light",) else "cpu", f"fleet {name} ceiling")
    return CapacityPlan(
        profile="auto",
        budget=budget,
        cells=cells,
        services=services,
        provenance=prov,
        warnings=tuple(dict.fromkeys(warnings)),
        cell_cpus=cell_cpus,
        embedding_reserve_cpus=reserve_cpus,
        member_budget=member,
        widths=widths,
        fleet_ceilings=fleet.as_tuple(),
        embed_device="cuda" if cuda else "cpu",
        numa_masks=numa.masks(facts, cells) if numa.enabled(env) else (),
    )
