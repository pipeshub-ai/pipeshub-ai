"""The ``auto`` plan: profiles, widths, Docling floor, memory and GPU edges, and the ceilings it implies."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.capacity.budget_policy import (
    AdmissionPolicy,
    Family,
    docling_design_workers,
    plan_floors,
)
from app.capacity.facts import CapacityFacts
from app.capacity.knobs import SOURCE_CLASSES
from app.capacity.policy import plan_capacity

if TYPE_CHECKING:
    from app.capacity.plan import CapacityPlan

GIB = 2**30
AUTO = {"PIPESHUB_CAPACITY_PROFILE": "auto"}


def _facts(cpus: float | None, mem_gb: float | None, **kw) -> CapacityFacts:
    base = {
        "cpu_quota": cpus, "cpu_affinity": 256, "cpu_host": 256, "mem_limit": None if mem_gb is None else int(mem_gb * GIB),
        "mem_host": 2048 * GIB, "cgroup": "v2", "member_id": "m", "local_cpu_embedding": True,
    }
    base.update(kw)
    return CapacityFacts(**base)


def _plan(cpus, mem_gb, env=None, **kw) -> CapacityPlan:
    return plan_capacity(_facts(cpus, mem_gb, **kw), {**AUTO, **(env or {})})


# (cpus, mem GB, cells, C, U, fleet h/l/ih/il)
PROFILES = [
    (4, 8, 1, 5, 1, (3, 18, 6, 18)),
    (16, 64, 1, 18, 2, (14, 68, 28, 68)),
    (24, 96, 1, 28, 2, (21, 102, 42, 102)),
    (32, 64, 2, 37, 3, (28, 136, 56, 136)),
    (64, 256, 4, 75, 5, (56, 272, 112, 272)),
    (128, 256, 8, 151, 9, (112, 544, 224, 544)),
    (256, 1024, 16, 303, 17, (224, 1088, 448, 1088)),
]


@pytest.mark.parametrize("cpus,mem,cells,c,u,fleet", PROFILES, ids=[f"{r[0]}cpu_{r[1]}gb" for r in PROFILES])
def test_profiles(cpus, mem, cells, c, u, fleet) -> None:
    plan = _plan(cpus, mem)
    assert plan.profile == "auto"
    assert (plan.cells, plan.member_budget.cpu_tokens, plan.member_budget.untracked) == (cells, c, u)
    assert plan.fleet_ceilings == fleet


def test_default_profile_is_legacy_and_unset_flag_changes_nothing() -> None:
    for env in ({}, {"PIPESHUB_CAPACITY_PROFILE": ""}, {"PIPESHUB_CAPACITY_PROFILE": "legacy"}, {"PIPESHUB_CAPACITY_PROFILE": "junk"}):
        plan = plan_capacity(_facts(128, 256), env)
        assert plan.profile == "legacy" and plan.cells == 1 and plan.member_budget is None and plan.widths is None


# Contract widths for CPU-only sizing (memory large enough not to bind): C, U, held floors, widths.
CONTRACT = {
    4: {"c": 5, "u": 1, "floors_sum": 4, "heavy": 3, "inspect": 1, "raster": 2, "embed_slots": 2, "embed_procs": 1, "warm": 1, "dmax": 2, "llm": 24},
    16: {"c": 18, "u": 2, "floors_sum": 14, "heavy": 14, "inspect": 2, "raster": 2, "embed_slots": 8, "embed_procs": 1, "warm": 4, "dmax": 7, "llm": 24},
    128: {"c": 151, "u": 9, "floors_sum": 82, "heavy": 14, "inspect": 2, "raster": 2, "embed_slots": 74, "embed_procs": 3, "warm": 31, "dmax": 60, "llm": 24},
}


@pytest.mark.parametrize("cpus", sorted(CONTRACT))
def test_widths_contract(cpus) -> None:
    want = CONTRACT[cpus]
    plan = _plan(cpus, 1024)
    m, w = plan.member_budget, plan.widths
    assert (m.cpu_tokens, m.untracked, sum(m.floors.values())) == (want["c"], want["u"], want["floors_sum"])
    assert (w.heavy_permits, w.parse_executor_slots) == (want["heavy"], want["heavy"])
    assert (w.inspect_workers, w.raster_workers) == (want["inspect"], want["raster"])
    assert (w.embed_slots, w.embed_processes, w.embed_bulk_limit) == (want["embed_slots"], want["embed_procs"], want["embed_slots"])
    assert (w.docling_workers_warm, w.docling_workers_max) == (want["warm"], want["dmax"])
    assert w.llm_permits == want["llm"]


def test_held_floor_rows_at_16_and_128() -> None:
    assert _plan(16, 64).member_budget.floors == {"docling": 8, "embed": 2, "query_embed": 2, "inspect": 1, "parse": 1}
    assert _plan(128, 1024).member_budget.floors == {"docling": 62, "embed": 16, "query_embed": 2, "inspect": 1, "parse": 1}
    four = _plan(4, 64)
    assert four.member_budget.floors == {"docling": 2, "embed": 2, "query_embed": 0, "inspect": 0, "parse": 0}
    assert "critical_floor_over_capacity" in four.warnings


def test_docling_floor_and_max() -> None:
    assert [docling_design_workers(c) for c in (4, 16, 128)] == [1, 4, 31]
    policy = AdmissionPolicy()
    f16 = plan_floors(16, 18, None, 2.0, policy)
    assert f16.desired[Family.DOCLING] == 16  # 8 workers x 2 tokens
    assert f16.by_family[Family.DOCLING] == 14 and "critical_floor_over_capacity" in f16.warnings
    assert f16.docling_max_workers == 7
    assert "critical_floor_over_capacity" not in plan_floors(16, 18, None, 1.0, policy).warnings


def test_llm_member_cap_is_per_llm_calling_service() -> None:
    assert _plan(16, 64).widths.llm_permits == 24
    assert _plan(128, 256, {"USE_PARSING_SERVICE": "true"}).widths.llm_permits == 72
    assert _plan(128, 256, {"MAX_CONCURRENT_INDEXING_LLM_CALLS": "10", "USE_PARSING_SERVICE": "true"}).widths.llm_permits == 30
    assert _plan(1, 4, {"INDEXING_UVICORN_WORKERS": "8"}).widths.llm_permits == 24  # cells never multiply it


@pytest.mark.parametrize(
    "cpus,mem,env,facts_kw,check",
    [
        (0.5, 2, {}, {}, lambda p: (p.cells, p.widths.heavy_permits, p.services["indexing"].threads_env["OMP_NUM_THREADS"]) == (1, 1, "1")),
        (None, 8, {}, {"cpu_affinity": 12}, lambda p: p.budget.cpus == 12.0 and p.budget.cpu_source == "affinity"),
        (None, None, {}, {"cpu_request": 6.0, "mem_request": 24 * GIB},
         lambda p: (p.budget.cpus, p.budget.mem_bytes, "cpu_from_request" in p.warnings) == (6.0, 24 * GIB, True)),
        (128, 1.0, {}, {}, lambda p: "memory_below_one_heavy_parse" in p.warnings and p.cells == 1 and p.widths.docling_workers_max == 1),
        (128, 16, {}, {}, lambda p: p.cells < 8 and "cells_bounded_by_memory" in p.warnings),
        (16, 64, {"INDEXING_UVICORN_WORKERS": "3"}, {}, lambda p: p.cells == 3 and p.provenance["cells"].source_class == "override"),
        (16, 64, {"PIPESHUB_CAPACITY_CELL_CPUS": "4"}, {}, lambda p: p.cells == 4),
    ],
    ids=["half_cpu", "no_limit", "requests_only", "below_one_heavy_parse", "memory_bound_cells", "operator_cells", "cell_cpus_knob"],
)
def test_edges(cpus, mem, env, facts_kw, check) -> None:
    assert check(_plan(cpus, mem, env, **facts_kw))


def test_docling_memory_bounds_the_cells() -> None:
    """The Docling workers kept warm count against the memory the cells share: leaving them out plans more cells."""
    plan = _plan(128, 24)
    without_docling = int((24 * GIB - 5 * GIB) // (4 * GIB))
    assert plan.cells < without_docling


def test_gpu_free_and_busy() -> None:
    free = _plan(16, 64, gpus=((0, 24 * GIB, 1 * GIB),))
    assert free.embed_device == "cuda" and free.embedding_reserve_cpus <= 1.0 and free.widths.embed_slots == 2
    busy = _plan(16, 64, gpus=((0, 24 * GIB, 23 * GIB),))
    assert busy.embed_device == "cpu" and "gpu_busy" in busy.warnings and busy.widths.embed_slots == 8
    assert "gpu_busy" not in _plan(16, 64).warnings


def test_every_value_has_provenance() -> None:
    for cpus, mem, *_ in [*PROFILES, (0.5, 2)]:
        plan = _plan(cpus, mem)
        keys = set(plan.provenance)
        for name in plan.widths.__dict__:
            assert f"widths.{name}" in keys, (cpus, name)
        for fam in plan.member_budget.floors:
            assert f"floor.{fam}" in keys
        for name in ("cells", "cell_cpus", "member.cpu_tokens", "member.mem_bytes", "embedding_reserve_cpus", "ceilings.heavy"):
            assert name in keys
        assert {p.source_class for p in plan.provenance.values()} <= set(SOURCE_CLASSES)
        assert all(p.reason for p in plan.provenance.values())


def test_plan_json_roundtrips() -> None:
    import json

    assert json.loads(json.dumps(_plan(128, 256).as_dict(), default=str))["widths"]["embed_slots"] == 74


@pytest.mark.parametrize("mem_gb", [12, 16, 20, 24, 32, 48, 64])
def test_cells_leave_memory_for_every_embedding_process(mem_gb: int) -> None:
    """Each embedding process past the first holds its own model, so the cell count is chosen
    against the baseline that includes all of them (128 CPUs, CPU embedding)."""
    from app.capacity import auto

    plan = _plan(128, mem_gb)
    baseline = (
        auto.BASE_MEMORY
        + plan.widths.docling_workers_warm * auto.DOCLING_WORKING_SET
        + (plan.widths.embed_processes - 1) * auto.EMBEDDING_MODEL_RSS
    )
    if plan.cells > 1:
        assert baseline + plan.cells * auto.CELL_MIN_MEMORY <= mem_gb * GIB, (plan.cells, plan.widths.embed_processes)
    if plan.cells < 8:
        assert "cells_bounded_by_memory" in plan.warnings


def test_a_gpu_embedding_does_not_charge_model_copies_to_memory() -> None:
    cpu = _plan(128, 20)
    gpu = _plan(128, 20, gpus=((0, 24 * GIB, 1 * GIB), (1, 24 * GIB, 1 * GIB)))
    assert gpu.embed_device == "cuda" and gpu.cells >= cpu.cells


@pytest.mark.parametrize("cpus", [8, 16, 32, 64, 128])
def test_the_reported_reservation_is_what_the_fleet_ceiling_leaves(cpus: int) -> None:
    """With a free GPU the reserve is at most one CPU, and the heavy ceiling is the quota less that
    same value, not the reservation of a CPU-only embedding server."""
    from app.capacity.ceilings import auto_ceilings

    gpu = _plan(cpus, 256, gpus=((0, 24 * GIB, 1 * GIB),))
    assert gpu.embedding_reserve_cpus == 1.0
    assert gpu.fleet_ceilings == auto_ceilings(cpus, None, None, cells=gpu.cells, reserve_embedding_cpus=True, reservation=1.0).as_tuple()
    cpu_only = _plan(cpus, 256)
    assert gpu.fleet_ceilings[0] >= cpu_only.fleet_ceilings[0]
    if cpus >= 32:
        assert gpu.fleet_ceilings[0] > cpu_only.fleet_ceilings[0], "the freed CPUs reach the heavy ceiling"


@pytest.mark.parametrize("override", [1, 3, 12])
def test_the_embedding_slot_override_applies_with_a_gpu(override: int) -> None:
    """With a free GPU the default is two slots per GPU, but an explicit ``EMBEDDING_SERVER_MAX_CONCURRENCY`` wins."""
    gpu = {"gpus": ((0, 24 * GIB, 1 * GIB),)}
    plan = _plan(16, 64, {"EMBEDDING_SERVER_MAX_CONCURRENCY": str(override)}, **gpu)
    assert plan.embed_device == "cuda" and plan.widths.embed_slots == override
    assert plan.provenance["widths.embed_slots"].source_class == "override"
    assert _plan(16, 64, **gpu).widths.embed_slots == 2
