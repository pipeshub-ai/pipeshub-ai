"""The ``legacy`` plan: its budget, cells and thread env, each with a source."""

from __future__ import annotations

import pytest

from app.capacity.facts import CapacityFacts
from app.capacity.knobs import SOURCE_CLASSES, drop_blank_knobs, knob_value
from app.capacity.plan import SERVICES
from app.capacity.policy import plan_capacity

GIB = 2**30


def _facts(**kw) -> CapacityFacts:
    base = {"cpu_affinity": 64, "cpu_host": 64, "mem_host": 256 * GIB, "cgroup": "none", "member_id": "m"}
    base.update(kw)
    return CapacityFacts(**base)


@pytest.mark.parametrize(
    "facts,env,cpus,cpu_source,mem,mem_source,warnings",
    [
        (_facts(cpu_quota=0.5, mem_limit=2 * GIB, cgroup="v2"), {}, 0.5, "cgroup_quota", 2 * GIB, "cgroup_limit", ()),
        (_facts(cpu_quota=16.0, mem_limit=64 * GIB), {}, 16.0, "cgroup_quota", 64 * GIB, "cgroup_limit", ()),
        (_facts(cpu_quota=200.0, cpu_affinity=8), {}, 8.0, "cgroup_quota", int(256 * GIB * 0.9), "host", ()),
        (_facts(), {}, 64.0, "affinity", int(256 * GIB * 0.9), "host", ()),
        (_facts(cpu_request=6.0, mem_request=24 * GIB), {}, 6.0, "request", 24 * GIB, "request",
         ("cpu_from_request", "memory_from_request")),
        (_facts(cpu_quota=4.0, cpu_request=6.0, mem_limit=8 * GIB, mem_request=24 * GIB), {}, 4.0, "cgroup_quota", 8 * GIB, "cgroup_limit", ()),
        (_facts(cpu_quota=4.0), {"PIPESHUB_CPU_BUDGET": "2", "PIPESHUB_MEMORY_BUDGET": str(GIB)}, 2.0, "override", GIB, "override", ()),
        (_facts(cpu_quota=4.0), {"PIPESHUB_CPU_BUDGET": "", "PIPESHUB_MEMORY_BUDGET": " "}, 4.0, "cgroup_quota", int(256 * GIB * 0.9), "host", ()),
    ],
    ids=["half_cpu", "16_64", "affinity_smaller", "no_limits", "requests_only", "limit_beats_request", "override", "blank_override"],
)
def test_budget(facts, env, cpus, cpu_source, mem, mem_source, warnings) -> None:
    plan = plan_capacity(facts, env)
    assert (plan.budget.cpus, plan.budget.cpu_source) == (cpus, cpu_source)
    assert (plan.budget.mem_bytes, plan.budget.mem_source) == (mem, mem_source)
    assert plan.warnings == warnings
    assert plan.profile == "legacy"


@pytest.mark.parametrize(
    "env,cells",
    [({}, 1), ({"INDEXING_UVICORN_WORKERS": ""}, 1), ({"INDEXING_UVICORN_WORKERS": "1"}, 1),
     ({"INDEXING_UVICORN_WORKERS": "4"}, 4), ({"INDEXING_UVICORN_WORKERS": "0"}, 1)],
)
def test_cells_are_the_operators_worker_count_else_one(env, cells) -> None:
    assert plan_capacity(_facts(cpu_quota=128.0), env).cells == cells


def test_cells_do_not_grow_with_cpus_in_legacy() -> None:
    assert plan_capacity(_facts(cpu_quota=128.0, mem_limit=256 * GIB), {}).cells == 1


def test_every_service_gets_the_same_thread_env_as_before() -> None:
    plan = plan_capacity(_facts(cpu_quota=4.0), {})
    assert set(plan.services) == set(SERVICES)
    for service in plan.services.values():
        assert dict(service.threads_env) == {
            "OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2", "MKL_NUM_THREADS": "2", "TOKENIZERS_PARALLELISM": "false",
        }


def test_every_value_has_a_provenance() -> None:
    plan = plan_capacity(_facts(cpu_quota=4.0), {"INDEXING_UVICORN_WORKERS": "2", "OMP_NUM_THREADS": "3"})
    assert plan.provenance, "the plan states no sources"
    for key, prov in plan.provenance.items():
        assert prov.source_class in SOURCE_CLASSES, key
        assert str(prov.reason).strip(), key
    assert plan.provenance["cells"].source_class == "override"
    assert plan.provenance["omp_threads"].source_class == "override"


def test_the_plan_is_deterministic_and_does_not_touch_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    monkeypatch.setenv("OMP_NUM_THREADS", "9")
    before = dict(os.environ)
    one = plan_capacity(_facts(cpu_quota=4.0), {})
    two = plan_capacity(_facts(cpu_quota=4.0), {})
    assert one == two and dict(os.environ) == before
    assert one.as_dict()["budget"]["cpus"] == 4.0


def test_knob_helpers() -> None:
    env = {"MAX_CONCURRENT_PARSING": " 5 ", "GOVERNOR_INDEX_MAX": "", "GOVERNOR_INDEX_HEADROOM": "2.5", "X": ""}
    assert knob_value(env, "MAX_CONCURRENT_PARSING") == 5
    assert knob_value(env, "GOVERNOR_INDEX_MAX") is None
    assert knob_value(env, "GOVERNOR_INDEX_HEADROOM") == 2.5
    assert knob_value(env, "MAX_CONCURRENT_INDEXING") is None
    with pytest.raises(ValueError):
        knob_value({"MAX_CONCURRENT_PARSING": "many"}, "MAX_CONCURRENT_PARSING")
    assert drop_blank_knobs(env) == ("GOVERNOR_INDEX_MAX",)
    assert "X" in env and "GOVERNOR_INDEX_MAX" not in env
