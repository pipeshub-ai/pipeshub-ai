"""Properties of the pure ``auto`` plan on generated grids. ``PLAN_GRID_SEED`` fixes the seed (it is printed)."""

from __future__ import annotations

import os
import random

import pytest

from app.capacity import ceilings as auto_ceil
from app.capacity.facts import CapacityFacts
from app.capacity.policy import plan_capacity
from app.services.resource_governor import policy as gov_policy
from app.services.resource_governor.models import ResourceSnapshot
from tests.support import legacy_ceilings as frozen

GIB = 1 << 30
SEED = int(os.environ.get("PLAN_GRID_SEED", random.SystemRandom().randrange(1 << 30)))
AUTO = {"PIPESHUB_CAPACITY_PROFILE": "auto"}


def _facts(cpus: float, mem_gib: float, **kw) -> CapacityFacts:
    return CapacityFacts(cpu_quota=cpus, cpu_affinity=max(1, int(cpus + 0.999)), cpu_host=1024,
                         mem_limit=int(mem_gib * GIB), mem_host=8192 * GIB, cgroup="v2", member_id="m", **kw)


def _snap(cpus: float) -> ResourceSnapshot:
    return ResourceSnapshot(cpu_quota=cpus, cpu_utilisation=0.0, cpu_throttled_ratio=0.0, cpu_pressure=0.0,
                            mem_limit_bytes=64 * GIB, mem_working_set_bytes=0, source="grid")


def test_auto_equals_the_live_governor_up_to_16_cpus_on_a_fresh_grid() -> None:
    """Against the live ``resolve_ceilings`` (not only the frozen copy), fractional quotas included."""
    print(f"PLAN_GRID_SEED={SEED}")
    rng = random.Random(SEED)
    bad = []
    for _ in range(4000):
        cpus = round(rng.uniform(0.05, 16.0), rng.choice([0, 1, 2, 3]))
        cpus = max(0.05, cpus)
        env_parse = rng.choice([None, 0, 1, 2, 7, 33, 500])
        env_index = rng.choice([None, 0, 1, 9, 50, 1000])
        workers = rng.choice([1, 2, 3, 4])
        reserve = rng.choice([False, True])
        live = gov_policy.resolve_ceilings(_snap(cpus), env_parse, env_index, workers, reserve_embedding_cpus=reserve)
        fleet = auto_ceil.auto_ceilings(cpus, env_parse, env_index, cells=workers, reserve_embedding_cpus=reserve)
        got = auto_ceil.per_cell(fleet, workers).as_tuple()
        want = (live.heavy, live.light, live.index_heavy, live.index_light)
        if got != want:
            bad.append((cpus, env_parse, env_index, workers, reserve, got, want))
    assert not bad, f"{len(bad)} rows differ (seed {SEED}), first: {bad[:3]}"


def test_auto_equals_frozen_at_legacy_one_cell() -> None:
    rng = random.Random(SEED + 1)
    for _ in range(1500):
        cpus = rng.uniform(0.05, 16.0)
        reserve = rng.choice([False, True])
        got = auto_ceil.auto_ceilings(cpus, None, None, cells=1, reserve_embedding_cpus=reserve).as_tuple()
        assert got == frozen.resolve(cpus, None, None, 1, reserve_embedding_cpus=reserve), cpus


def test_fleet_and_tokens_monotone_in_cpus_with_memory_following() -> None:
    """16..512 CPUs in 0.1 steps, 4 GiB per CPU: C, fleet ceilings, embed slots never go down."""
    prev = None
    for i in range(0, 4961, 7):
        cpus = 16 + i * 0.1
        plan = plan_capacity(_facts(cpus, cpus * 4), AUTO)
        cur = (plan.member_budget.cpu_tokens, *plan.fleet_ceilings, plan.widths.embed_slots)
        if prev is not None:
            assert all(c >= p for c, p in zip(cur, prev, strict=True)), (cpus, prev, cur)
        prev = cur


def test_tokens_monotone_in_memory() -> None:
    """More memory never lowers the Docling max at a fixed CPU count.

    ``C`` and ``cells`` are not asserted: more memory can allow more cells, and each cell adds one to
    the untracked threads ``U`` that ``C = floor(1.25 * cpus) - U`` subtracts, so ``C`` may fall.
    """
    for cpus in (8, 16, 64, 128):
        prev = None
        for mem in range(1, 1025, 3):
            plan = plan_capacity(_facts(cpus, mem), AUTO)
            cur = plan.widths.docling_workers_max
            if prev is not None:
                assert cur >= prev, (cpus, mem, prev, cur)
            prev = cur


def test_held_floors_never_exceed_c_minus_large_min() -> None:
    from app.capacity.budget_policy import AdmissionPolicy, slots

    rng = random.Random(SEED + 2)
    for _ in range(800):
        cpus = rng.choice([0.25, 0.5, 1, 2, 3, 4, 6, 8, 12, 16, 24, 48, 96, 128, 200, 512])
        mem = rng.choice([0.5, 1, 2, 4, 8, 32, 128, 512, 2048])
        env = dict(AUTO)
        if rng.random() < 0.3:
            env["INDEXING_UVICORN_WORKERS"] = str(rng.choice([1, 2, 9, 64, 200]))
        plan = plan_capacity(_facts(cpus, mem), env)
        m = plan.member_budget
        assert sum(m.floors.values()) <= m.cpu_tokens - slots(AdmissionPolicy().large_min, m.cpu_tokens), (cpus, mem, m)
        assert 1 <= plan.cells <= 64
        assert plan.widths.docling_workers_warm <= plan.widths.docling_workers_max


GARBAGE = ["", " ", "abc", "-1", "0", "nan", "inf", "1e400", "0.0001", "١٢"]


@pytest.mark.parametrize("name", ["PIPESHUB_CAPACITY_CELL_CPUS", "PIPESHUB_CPU_OVERSUB", "INDEXING_UVICORN_WORKERS",
                                  "PIPESHUB_CPU_BUDGET", "PIPESHUB_MEMORY_BUDGET", "PIPESHUB_CAPACITY_PROFILE",
                                  "GOVERNOR_INDEX_HEADROOM", "GOVERNOR_LIGHT_PARSE_MAX", "EMBEDDING_SERVER_MAX_CONCURRENCY"])
@pytest.mark.parametrize("value", GARBAGE)
def test_garbage_under_auto_never_stops_a_process(name: str, value: str) -> None:
    from app.capacity.apply import apply_process_env

    env = {**AUTO, name: value, "PIPESHUB_SERVICE": "docling"}
    apply_process_env(env)
    assert env["OMP_NUM_THREADS"].isdigit()
