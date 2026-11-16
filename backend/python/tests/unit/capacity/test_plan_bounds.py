"""Bounds the plan keeps: calibration values, worker counts, the budget overrides and the process claim."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.capacity import cells
from app.capacity.apply import apply_process_env, log_line
from app.capacity.auto import MAX_CELLS
from app.capacity.facts import CapacityFacts
from app.capacity.policy import plan_capacity

GIB = 2**30
AUTO = {"PIPESHUB_CAPACITY_PROFILE": "auto"}


def _facts(cpus: float, mem_gib: float = 256, **kw) -> CapacityFacts:
    return CapacityFacts(cpu_quota=cpus, cpu_affinity=max(1, int(cpus + 0.999)), cpu_host=1024,
                         mem_limit=int(mem_gib * GIB), mem_host=8192 * GIB, cgroup="v2", member_id="m", **kw)


@pytest.mark.parametrize("name,value", [
    ("PIPESHUB_CPU_OVERSUB", "0"), ("PIPESHUB_CPU_OVERSUB", "-1"), ("PIPESHUB_CPU_OVERSUB", "abc"),
    ("PIPESHUB_CPU_OVERSUB", "nan"), ("PIPESHUB_CPU_OVERSUB", "inf"), ("PIPESHUB_CPU_OVERSUB", "100"),
    ("PIPESHUB_CAPACITY_CELL_CPUS", "0.0001"), ("PIPESHUB_CAPACITY_CELL_CPUS", "-4"), ("PIPESHUB_CAPACITY_CELL_CPUS", "0"),
    ("PIPESHUB_CAPACITY_CELL_CPUS", "0.5"), ("PIPESHUB_CAPACITY_CELL_CPUS", "inf"), ("PIPESHUB_CAPACITY_CELL_CPUS", "x"),
    ("GOVERNOR_INDEX_HEADROOM", "-2"), ("GOVERNOR_LIGHT_PARSE_SLOTS_PER_CPU", "nan"),
] + [
    (name, bad)
    for name in ("GOVERNOR_HEAVY_PARSE_SLOTS_PER_CPU", "GOVERNOR_LIGHT_PARSE_SLOTS_PER_CPU", "GOVERNOR_INDEX_HEADROOM")
    for bad in ("0", "-1", "abc", "nan", "inf")
])
def test_nonsense_calibration_keeps_the_default_and_warns(name: str, value: str) -> None:
    base = plan_capacity(_facts(64), AUTO)
    plan = plan_capacity(_facts(64), {**AUTO, name: value})
    assert (plan.member_budget.cpu_tokens, plan.cells, plan.fleet_ceilings) == (
        base.member_budget.cpu_tokens, base.cells, base.fleet_ceilings)
    assert f"invalid_{name}" in plan.warnings and f"invalid_{name}" not in base.warnings


def test_a_plausible_calibration_that_starves_the_tokens_warns() -> None:
    plan = plan_capacity(_facts(64), {**AUTO, "PIPESHUB_CAPACITY_CELL_CPUS": "1"})
    assert plan.cells > 32 and plan.member_budget.cpu_tokens < plan.cells
    assert "cpu_tokens_below_cells" in plan.warnings
    assert "cpu_tokens_below_cells" not in plan_capacity(_facts(64), AUTO).warnings


def test_more_workers_than_the_plan_has_cells_for_is_capped_and_warned() -> None:
    plan = plan_capacity(_facts(64), {**AUTO, "INDEXING_UVICORN_WORKERS": "100"})
    assert plan.cells == MAX_CELLS and "cells_capped" in plan.warnings
    plan = plan_capacity(_facts(64), {**AUTO, "INDEXING_UVICORN_WORKERS": str(MAX_CELLS)})
    assert plan.cells == MAX_CELLS and "cells_capped" not in plan.warnings


@pytest.mark.parametrize("value", ["-1", "nan", "inf", "-inf", "1e400", "0", "abc", "4Gi"])
def test_a_nonsense_cpu_budget_is_ignored_with_a_warning(value: str) -> None:
    plan = plan_capacity(_facts(4.0), {"PIPESHUB_CPU_BUDGET": value})
    assert plan.budget.cpus == 4.0 and plan.budget.cpu_source == "cgroup_quota"
    assert "invalid_PIPESHUB_CPU_BUDGET" in plan.warnings


@pytest.mark.parametrize("value", ["-5", "-1073741824", "0", "nan", "inf", "1.5", "١٢", "-4Gi", "abc"])
def test_a_nonsense_memory_budget_is_ignored_with_a_warning(value: str) -> None:
    plan = plan_capacity(_facts(4.0, 64), {"PIPESHUB_MEMORY_BUDGET": value})
    assert plan.budget.mem_bytes == 64 * GIB and plan.budget.mem_source == "cgroup_limit"
    assert "invalid_PIPESHUB_MEMORY_BUDGET" in plan.warnings


def test_good_budget_overrides_are_used() -> None:
    plan = plan_capacity(_facts(4.0, 64), {"PIPESHUB_CPU_BUDGET": "2.5", "PIPESHUB_MEMORY_BUDGET": "4Gi"})
    assert (plan.budget.cpus, plan.budget.mem_bytes) == (2.5, 4 * GIB)
    assert plan.budget.cpu_source == plan.budget.mem_source == "override" and not plan.warnings


@pytest.mark.parametrize("profile", [{}, AUTO])
@pytest.mark.parametrize("value", ["١٢", "0", "-2", "abc", "2.5", "\u00b2"])
def test_the_default_omp_must_be_a_positive_ascii_int(profile: dict[str, str], value: str) -> None:
    env = {**profile, "PIPESHUB_OMP_DEFAULT": value}
    # A fixed four-CPU container, whatever the host is: below two CPUs the auto plan picks one thread first.
    with patch("app.capacity.apply.resolve_facts", return_value=_facts(4.0, 64)):
        apply_process_env(env)
    assert env["OMP_NUM_THREADS"] == "2"


def test_a_good_default_omp_is_used() -> None:
    env = {"PIPESHUB_OMP_DEFAULT": "3"}
    with patch("app.capacity.apply.resolve_facts", return_value=_facts(4.0, 64)):
        apply_process_env(env)
    assert env["OMP_NUM_THREADS"] == "3"


def test_the_log_line_names_the_requested_profile_and_the_reason_when_auto_cannot_be_planned() -> None:
    env = {**AUTO, "PIPESHUB_SERVICE": "indexing"}
    with patch("app.capacity.apply.plan_capacity", side_effect=RuntimeError("boom\nwith lines")):
        plan = apply_process_env(env)
    assert plan is None and env["OMP_NUM_THREADS"] == "2"
    line = log_line(plan, env)
    assert "profile=auto plan=unavailable reason=RuntimeError: boom with lines" in line and "\n" not in line


def test_the_log_line_shows_warnings_of_an_auto_plan_and_invalid_values_only() -> None:
    env = {**AUTO, "PIPESHUB_SERVICE": "indexing", "PIPESHUB_CAPACITY_CELL_CPUS": "-1"}
    assert "warnings=invalid_PIPESHUB_CAPACITY_CELL_CPUS" in log_line(apply_process_env(env), env)
    legacy = {"PIPESHUB_SERVICE": "indexing"}
    assert "warnings" not in log_line(apply_process_env(legacy), legacy)


def test_the_process_claim_is_one_claim_and_one_lock(tmp_path) -> None:
    """NUMA pinning claims at start and the service claims again for its stats file: both get the same claim."""
    if not cells._OFD:
        pytest.skip("cell claims need Linux open-file-description locks")
    cells._PROCESS.clear()
    first = cells.process_claim(tmp_path, 2)
    second = cells.process_claim(tmp_path, 2)
    try:
        assert first is not None and second is first
        other = cells.claim_cell(tmp_path, 2)
        assert other is not None and other.index == 1, "the process claim took two indexes"
        other.release()
    finally:
        first.release()
    assert cells._PROCESS == []


@pytest.mark.parametrize("cpus", [0.5, 1.0, 1.9])
def test_the_auto_plan_uses_one_thread_below_two_cpus(cpus: float) -> None:
    env = dict(AUTO)
    with patch("app.capacity.apply.resolve_facts", return_value=_facts(cpus, 64)):
        apply_process_env(env)
    assert env["OMP_NUM_THREADS"] == "1"
