"""``plan_capacity``: pure and deterministic. No I/O, no clock, no globals."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.capacity.knobs import capacity_profile, knob_value, parse_bytes, positive_float
from app.capacity.plan import SERVICES, CapacityPlan, CpuMem, Provenance, ServicePlan

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from app.capacity.facts import CapacityFacts

DEFAULT_OMP = 2
_HOST_MEMORY_SHARE = 0.9


def _override(env: Mapping[str, str], name: str, parse: Callable[[object], float | None], warnings: list[str]) -> float | None:
    """A budget override that parses to a positive finite number; anything else is ignored with a warning."""
    raw = (env.get(name) or "").strip()
    if not raw:
        return None
    value = parse(raw)
    if value is None:
        warnings.append(f"invalid_{name}")
    return value


def _budget(facts: CapacityFacts, env: Mapping[str, str], warnings: list[str]) -> CpuMem:
    cpu_override = _override(env, "PIPESHUB_CPU_BUDGET", positive_float, warnings)
    if cpu_override is not None:
        cpus, cpu_source = float(cpu_override), "override"
    elif facts.cpu_quota:
        cpus, cpu_source = min(facts.cpu_quota, float(facts.cpu_affinity)), "cgroup_quota"
    elif facts.cpu_request:
        cpus, cpu_source = min(facts.cpu_request, float(facts.cpu_affinity)), "request"
        warnings.append("cpu_from_request")
    else:
        cpus, cpu_source = float(facts.cpu_affinity), "affinity"
    mem_override = _override(env, "PIPESHUB_MEMORY_BUDGET", parse_bytes, warnings)
    if mem_override is not None:
        mem, mem_source = int(mem_override), "override"
    elif facts.mem_limit:
        mem, mem_source = facts.mem_limit, "cgroup_limit"
    elif facts.mem_request:
        mem, mem_source = facts.mem_request, "request"
        warnings.append("memory_from_request")
    else:
        mem, mem_source = int(facts.mem_host * _HOST_MEMORY_SHARE), "host"
    return CpuMem(cpus=cpus, mem_bytes=mem, cpu_source=cpu_source, mem_source=mem_source)


def _omp(env: Mapping[str, str]) -> tuple[str, str]:
    """``(threads, source)``: the operator's ``OMP_NUM_THREADS``, else the image default, else 2."""
    operator = (env.get("OMP_NUM_THREADS") or "").strip()
    if operator:
        return operator, "override"
    image = (env.get("PIPESHUB_OMP_DEFAULT") or "").strip()
    if image.isascii() and image.isdigit() and int(image) > 0:
        return image, "unit"
    return str(DEFAULT_OMP), "unit"


def plan_capacity(
    facts: CapacityFacts, env: Mapping[str, str], roles: frozenset[str] | None = None
) -> CapacityPlan:
    """The plan: ``legacy`` is what today's code does; ``auto`` (``PIPESHUB_CAPACITY_PROFILE``) is ``auto.py``. Each value has a source."""
    warnings: list[str] = []
    budget = _budget(facts, env, warnings)
    if capacity_profile(env) == "auto":
        from app.capacity.auto import plan_auto

        return plan_auto(facts, env, budget, warnings)
    workers = knob_value(env, "INDEXING_UVICORN_WORKERS")
    cells = max(1, int(workers)) if workers is not None else 1  # type: ignore[call-overload]
    omp, omp_source = _omp(env)
    provenance = {
        "cells": Provenance(
            cells,
            "override" if workers is not None else "cell",
            "INDEXING_UVICORN_WORKERS" if workers is not None else "one indexing process",
        ),
        "omp_threads": Provenance(omp, omp_source, "threads of one ML library call"),
        "budget.cpus": Provenance(budget.cpus, "override" if budget.cpu_source == "override" else "cpu", budget.cpu_source),
        "budget.mem_bytes": Provenance(
            budget.mem_bytes, "override" if budget.mem_source == "override" else "memory", budget.mem_source
        ),
    }
    services = {
        name: ServicePlan(
            threads_env={
                "OMP_NUM_THREADS": omp,
                "OPENBLAS_NUM_THREADS": omp,
                "MKL_NUM_THREADS": omp,
                "TOKENIZERS_PARALLELISM": "false",
            }
        )
        for name in SERVICES
    }
    return CapacityPlan(
        profile="legacy",
        budget=budget,
        cells=cells,
        services=services,
        provenance=provenance,
        warnings=tuple(warnings),
    )
