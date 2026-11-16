"""The plan's sizes, and garbage in every capacity variable: no process stops, the thread env stays 2."""

from __future__ import annotations

import pytest

from app.capacity.apply import apply_process_env, log_line
from app.capacity.facts import CapacityFacts
from app.capacity.policy import plan_capacity


def _facts(cpus: float | None, affinity: int = 64, mem: int | None = 64 << 30) -> CapacityFacts:
    return CapacityFacts(cpu_quota=cpus, cpu_affinity=affinity, cpu_host=affinity, mem_limit=mem, mem_host=256 << 30,
                         cgroup="v2", member_id="m")


@pytest.mark.parametrize("cpus", [0.25, 0.5, 1.5, 16, 17, 64, 128, 512, None])
def test_threads_do_not_depend_on_size(cpus) -> None:
    """The thread env is 2 at every size, above 16 CPUs too (the plan sizes no threads yet)."""
    plan = plan_capacity(_facts(cpus, affinity=max(1, int(cpus or 512))), {"PIPESHUB_OMP_DEFAULT": "2"})
    assert {s.threads_env["OMP_NUM_THREADS"] for s in plan.services.values()} == {"2"}
    assert plan.profile == "legacy" and plan.cells == 1


def test_cpuset_narrower_than_quota_is_the_budget() -> None:
    plan = plan_capacity(_facts(8.0, affinity=2), {})
    assert plan.budget.cpus == 2.0


GARBAGE = ["", " ", "abc", "-1", "nan", "inf", "0", "1e400", "4Gi", "1.5", "0x10", "١٢"]


@pytest.mark.parametrize("name", ["PIPESHUB_CPU_BUDGET", "PIPESHUB_MEMORY_BUDGET", "INDEXING_UVICORN_WORKERS",
                                  "PIPESHUB_CPU_REQUEST_MILLI", "PIPESHUB_MEMORY_REQUEST", "PIPESHUB_OMP_DEFAULT",
                                  "PIPESHUB_CAPACITY_FACTS", "PIPESHUB_SERVICE"])
@pytest.mark.parametrize("value", GARBAGE)
def test_garbage_overrides_never_stop_a_process(name: str, value: str) -> None:
    env = {name: value}
    plan = apply_process_env(env)
    assert env["OMP_NUM_THREADS"] == "2" and env["MKL_NUM_THREADS"] == "2"
    line = log_line(plan, env)
    assert line.startswith("capacity: service=")
