"""The four governors share one cgroup: their guaranteed shares fit, and NUMA pinning is off unless asked."""

from __future__ import annotations

import ast
import itertools
import os
import sys
from pathlib import Path

import pytest

from app.capacity import numa
from app.capacity.facts import CapacityFacts
from app.capacity.policy import plan_capacity

GIB = 2**30
AUTO = {"PIPESHUB_CAPACITY_PROFILE": "auto"}


def _facts(cpus, mem_gb, **kw) -> CapacityFacts:
    base = {"cpu_quota": float(cpus), "cpu_affinity": 512, "cpu_host": 512, "mem_limit": int(mem_gb * GIB),
            "mem_host": 4096 * GIB, "cgroup": "v2", "member_id": "m", "local_cpu_embedding": True}
    base.update(kw)
    return CapacityFacts(**base)


@pytest.mark.parametrize("cpus,mem", list(itertools.product([2, 4, 8, 16, 24, 32, 64, 128, 256], [8, 64, 512])))
def test_guaranteed_shares_fit(cpus, mem) -> None:
    plan = plan_capacity(_facts(cpus, mem), AUTO)
    m, w = plan.member_budget, plan.widths
    gov = {name: s.governor for name, s in plan.services.items() if s.governor}
    assert set(gov) == {"indexing", "docling", "query", "parsing"}
    # Docling's governor is sized from its own workers, never the whole box (I2).
    assert gov["docling"].cpus == w.docling_workers_max * 2 <= max(2, m.cpu_tokens - m.large_min_tokens)
    assert gov["docling"].heavy_cap == w.docling_workers_max
    assert gov["query"].heavy_cap == max(1, min(4, int(0.1 * cpus)))
    assert gov["indexing"].workers == plan.cells and gov["indexing"].cpus == plan.budget.cpus
    # What is held for the latency class fits beside the large-work guarantee, unless the plan says it does not.
    held = sum(m.floors.values())
    assert held <= max(0, m.cpu_tokens - m.large_min_tokens)
    assert (sum(m.desired_floors.values()) > held) == ("critical_floor_over_capacity" in plan.warnings)
    # The reserve for the local embedding model is taken out of the indexing governor's CPUs, so it fits inside them.
    assert 0 <= plan.embedding_reserve_cpus <= gov["indexing"].cpus
    assert gov["indexing"].reserve_embedding_cpus is True


def test_a_docling_governor_with_the_whole_box_would_break_the_share() -> None:
    plan = plan_capacity(_facts(16, 64), AUTO)
    assert plan.services["docling"].governor.cpus < plan.budget.cpus


NODES = ((0, tuple(range(0, 32))), (1, tuple(range(32, 64))))


def test_masks() -> None:
    f = _facts(64, 256, numa=NODES)
    assert numa.masks(f, 4) == (NODES[0][1], NODES[1][1], NODES[0][1], NODES[1][1])
    assert numa.masks(f, 1) == ()  # fewer cells than nodes: nothing to pin
    assert numa.masks(_facts(64, 256, numa=NODES[:1]), 4) == ()
    assert numa.masks(_facts(64, 256), 4) == ()


def test_default_off() -> None:
    f = _facts(64, 256, numa=NODES)
    assert plan_capacity(f, AUTO).numa_masks == ()
    assert plan_capacity(f, {**AUTO, "PIPESHUB_NUMA_PINNING": "off"}).numa_masks == ()
    assert len(plan_capacity(f, {**AUTO, "PIPESHUB_NUMA_PINNING": "auto"}).numa_masks) == 4
    assert plan_capacity(f, {"PIPESHUB_NUMA_PINNING": "auto"}).numa_masks == ()  # legacy never pins


@pytest.mark.skipif(not hasattr(os, "sched_setaffinity"), reason="no sched_setaffinity")
def test_pin_restricts_this_process_and_restores() -> None:
    before = os.sched_getaffinity(0)
    cpu = min(before)
    try:
        env: dict[str, str] = {}  # the pin records the pre-pin CPU count here, not in os.environ
        assert numa.pin(0, ((cpu,),), env)
        assert os.sched_getaffinity(0) == {cpu}
        assert env[numa.CONTAINER_CPUS_ENV] == str(len(before))
        assert numa.pin(0, (), env) is False
    finally:
        os.sched_setaffinity(0, before)


def test_pin_before_imports() -> None:
    """``runtime_threads`` runs the plan and pin, standard library only, before it could import anything heavy."""
    root = Path(__file__).resolve().parents[3] / "app"
    stdlib = set(sys.stdlib_module_names)
    for path in [root / "utils/runtime_threads.py", *sorted((root / "capacity").glob("*.py"))]:
        for node in ast.walk(ast.parse(path.read_text())):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else (
                [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            for name in names:
                top = name.split(".")[0]
                assert top in stdlib or top == "app", f"{path.name} imports {name}"
