"""The governor under ``PIPESHUB_CAPACITY_PROFILE=auto``: ceilings from the plan, one container controller, the flag-off path unchanged."""

from __future__ import annotations

import dataclasses
import itertools
import json
import logging
from typing import TYPE_CHECKING

import pytest

from app.capacity import runtime
from app.capacity.member_budget import STATE_BYTES
from app.services.resource_governor import container_link as cl
from app.services.resource_governor.controller import ResourceGovernor
from app.services.resource_governor.models import (
    ControllerState,
    Limits,
    Pool,
    PoolState,
    ResourceSnapshot,
)
from app.services.resource_governor.policy import resolve_ceilings

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


def snap(cpus: float, mem_gb: float = 64.0, ws_gb: float = 4.0) -> ResourceSnapshot:
    return ResourceSnapshot(
        cpu_quota=cpus, cpu_utilisation=0.2, cpu_throttled_ratio=0.0, cpu_pressure=0.0,
        mem_limit_bytes=int(mem_gb * 2**30), mem_working_set_bytes=int(ws_gb * 2**30), source="test",
    )


class Probe:
    def __init__(self, s: ResourceSnapshot) -> None:
        self.s = s

    def snapshot(self) -> ResourceSnapshot:
        return self.s


@pytest.fixture(autouse=True)
def _clean_runtime() -> Iterator[None]:
    runtime.reset_for_tests()
    yield
    runtime.reset_for_tests()


# ---- resolve_ceilings -------------------------------------------------------------------------------


@pytest.mark.parametrize("cpus", [0.5, 1, 2, 4, 8, 12, 16])
def test_auto_equals_legacy_ceilings_up_to_16_cpus(cpus: float) -> None:
    for env_parse, env_index, reserve, workers in itertools.product([None, 1, 3, 40], [None, 1, 2, 30, 500], [False, True], [1, 2]):
        s = snap(cpus)
        legacy = resolve_ceilings(s, env_parse, env_index, workers, reserve_embedding_cpus=reserve)
        auto = resolve_ceilings(s, env_parse, env_index, workers, reserve_embedding_cpus=reserve, cells=1, profile="auto")
        assert auto == legacy, (cpus, env_parse, env_index, reserve, workers)


def test_auto_lifts_the_total_and_scales_the_per_cell_caps_above_16_cpus() -> None:
    legacy = resolve_ceilings(snap(128), None, None, reserve_embedding_cpus=True)
    auto = resolve_ceilings(snap(128), None, None, reserve_embedding_cpus=True, cells=8, profile="auto")
    assert (legacy.heavy, legacy.light, legacy.index_heavy, legacy.index_light) == (64, 32, 64, 32)
    assert auto.index_heavy + auto.index_light > legacy.index_heavy + legacy.index_light
    assert auto.heavy >= 100


def test_the_default_profile_is_legacy() -> None:
    s = snap(128)
    assert resolve_ceilings(s, None, None) == resolve_ceilings(s, None, None, cells=8, profile="legacy")


# ---- state in the ledger ---------------------------------------------------------------------------


def _full_state() -> ControllerState:
    return ControllerState(pools={
        p: PoolState(healthy_streak=123456, cooldown_until=123456.789012, in_slow_start=False, slow_start_step=64,
                     prev_grow_mem_pressure=0.123456789, prev_grow_cpu_utilisation=0.987654321,
                     hold_ewma=12345.678901, hold_baseline=9876.54321, downstream_holdoff=17)
        for p in Pool
    })


def test_the_persisted_state_fits_its_block_and_round_trips() -> None:
    limits = Limits(values={Pool.HEAVY_PARSE: 112, Pool.LIGHT_PARSE: 544, Pool.INDEX_HEAVY: 224, Pool.INDEX_LIGHT: 544})
    raw = cl.encode_state(limits, _full_state())
    assert len(json.dumps(raw, separators=(",", ":"))) <= STATE_BYTES // 2  # at least twice the room it needs
    back = cl.decode_state(json.loads(json.dumps(raw)))
    assert back is not None and back[0] == limits
    assert back[1].get(Pool.HEAVY_PARSE).slow_start_step == 64 and back[1].get(Pool.INDEX_LIGHT).downstream_holdoff == 17


@pytest.mark.parametrize("raw", [None, {}, {"v": 2}, {"v": 1}, {"v": 1, "l": {}, "p": {}}, {"v": 1, "l": "x", "p": 1}])
def test_unusable_state_is_ignored(raw) -> None:
    assert cl.decode_state(raw) is None


# ---- the governor ------------------------------------------------------------------------------------


def _governor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, auto: bool, cpus: float = 8.0, workers: int = 1) -> ResourceGovernor:
    if auto:
        monkeypatch.setenv("PIPESHUB_CAPACITY_PROFILE", "auto")
        monkeypatch.setenv("PIPESHUB_CAPACITY_DIR", str(tmp_path / "cap"))
        monkeypatch.setenv("PIPESHUB_CPU_BUDGET", str(cpus))
        monkeypatch.setenv("PIPESHUB_MEMORY_BUDGET", str(64 * 2**30))
        monkeypatch.setenv("PIPESHUB_SERVICE", "indexing")
    else:
        monkeypatch.delenv("PIPESHUB_CAPACITY_PROFILE", raising=False)
    return ResourceGovernor(logger=logging.getLogger("t"), probe=Probe(snap(cpus)), worker_count=workers, sample_interval=1.0)


def test_flag_off_builds_no_link_and_creates_no_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIPESHUB_CAPACITY_DIR", str(tmp_path / "cap"))
    g = _governor(tmp_path, monkeypatch, auto=False)
    assert g._container is None
    assert "capacity" not in g.stats()
    assert not (tmp_path / "cap").exists()


def test_flag_off_ceilings_are_the_legacy_ones_at_any_size(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    g = _governor(tmp_path, monkeypatch, auto=False, cpus=128.0)
    legacy = resolve_ceilings(snap(128.0), None, None, 1)
    assert g._fleet_ceilings == legacy


@pytest.mark.asyncio
async def test_auto_governor_is_the_controller_and_persists_its_ramp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    g = _governor(tmp_path, monkeypatch, auto=True)
    assert g._container is not None
    await g._sample_once()
    stats = g.stats()["capacity"]
    assert stats["role"] == "controller" and stats["controller"]["epoch"] == 1
    budget = runtime.member_budget()
    persisted = cl.decode_state(budget.controller()["state"])
    assert persisted is not None
    assert persisted[0].get(Pool.HEAVY_PARSE) == g._registry.snapshot().get(Pool.HEAVY_PARSE)  # one cell: its share is the whole


@pytest.mark.asyncio
async def test_a_second_governor_resumes_the_persisted_ramp_instead_of_warm_starting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = _governor(tmp_path, monkeypatch, auto=True)
    for _ in range(4):
        await first._sample_once()
    before = cl.decode_state(runtime.member_budget().controller()["state"])
    # A successor process: a new handle on the same ledger takes over once the first is gone.
    runtime.reset_for_tests()
    second = _governor(tmp_path, monkeypatch, auto=True)
    await second._sample_once()
    after = cl.decode_state(runtime.member_budget().controller()["state"])
    assert after is not None and before is not None
    assert runtime.member_budget().controller()["epoch"] == 2
    assert after[0].get(Pool.LIGHT_PARSE) >= before[0].get(Pool.LIGHT_PARSE)  # resumed from the ledger, not reset


@pytest.mark.asyncio
async def test_a_failing_controller_step_keeps_the_governors_own_limits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    g = _governor(tmp_path, monkeypatch, auto=True)

    def boom(*a: object, **k: object) -> None:
        raise OSError("ledger gone")

    monkeypatch.setattr(g._container.controller, "tick", boom)
    await g._sample_once()  # does not raise
    assert "keeping this process's own limits" in caplog.text
    assert g._registry.snapshot().get(Pool.HEAVY_PARSE) >= 1


def test_the_docling_governor_holds_heavy_to_its_service_share(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIPESHUB_SERVICE", "docling")
    g = _governor(tmp_path, monkeypatch, auto=True, cpus=16.0)
    monkeypatch.setenv("PIPESHUB_SERVICE", "docling")
    runtime.reset_for_tests()
    g2 = ResourceGovernor(logger=logging.getLogger("t"), probe=Probe(snap(16.0)), sample_interval=1.0)
    plan = runtime.current_plan()
    assert g2._fleet_ceilings.heavy <= plan.widths.docling_workers_max
    assert g._container is not None


def test_each_cell_takes_its_share_of_the_container_limits(tmp_path: Path) -> None:
    """The controller's limits are the container's; a cell applies its share, bounded by its own ceilings."""
    from app.capacity.budget_policy import AdmissionPolicy, plan_floors
    from app.capacity.controller import ElectedController
    from app.capacity.member_budget import MemberBudget
    from app.services.resource_governor.models import Ceilings

    d = tmp_path / "ledger"
    d.mkdir(mode=0o700)
    b = MemberBudget(d, cpu_tokens=160, mem_bytes=256 << 30, floors=plan_floors(128, 160, None, 1.0, AdmissionPolicy()))
    fleet = Ceilings(112, 544, 224, 544)
    link = cl.ContainerLink(b, ElectedController(b, 15), fleet, 4)
    container = Limits(values={Pool.HEAVY_PARSE: 40, Pool.LIGHT_PARSE: 8, Pool.INDEX_HEAVY: 400, Pool.INDEX_LIGHT: 0})
    own = (Limits(values=dict.fromkeys(Pool, 1)), ControllerState.initial())
    epoch = b.elect_controller(15)
    b.controller_beat(epoch, cl.encode_state(container, ControllerState.initial()))
    per_cell = Ceilings(28, 136, 56, 136)
    limits, _ = link.step(
        own=own, snap=snap(128), ceilings=per_cell, demand={}, now=0.0, interval=15.0, feedback=None,
        heavy_working_set_gb=1.5,
    )
    # the step just advanced the persisted state once; it never exceeds the cell's ceilings, and 4 cells' shares are the whole
    assert limits.get(Pool.HEAVY_PARSE) <= 28
    assert limits.get(Pool.HEAVY_PARSE) * 4 <= container.get(Pool.HEAVY_PARSE) + 4 * 3
    assert limits.get(Pool.INDEX_LIGHT) == 0 and limits.get(Pool.HEAVY_PARSE) >= 1


def test_the_container_controller_ramps_against_the_whole_fleet_not_one_services_share(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Whichever service wins the election persists limits every follower applies; a Docling controller
    must not carry Docling's own heavy cap into the container-wide limits."""
    _governor(tmp_path, monkeypatch, auto=True, cpus=16.0)
    monkeypatch.setenv("PIPESHUB_SERVICE", "docling")
    runtime.reset_for_tests()
    g = ResourceGovernor(logger=logging.getLogger("t"), probe=Probe(snap(16.0)), sample_interval=1.0)
    whole = resolve_ceilings(snap(16.0), None, None, 1, reserve_embedding_cpus=False, cells=1, profile="auto")
    assert g._container is not None
    assert g._fleet_ceilings.heavy < whole.heavy  # the service's own gates stay held to its share
    assert g._container.fleet == whole  # the shared ramp is sized from the whole container


def test_the_container_link_uses_the_plans_fleet_and_the_running_workers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """At 128 CPUs the plan's fleet is not what one cell resolves on its own (light, index): the shared ramp
    must use the plan's, shared by the plan's cells, whichever service holds the role."""
    _governor(tmp_path, monkeypatch, auto=True, cpus=128.0, workers=8)
    monkeypatch.setenv("PIPESHUB_SERVICE", "docling")  # the service that wins the election in the all-in-one container
    runtime.reset_for_tests()
    g = ResourceGovernor(logger=logging.getLogger("t"), probe=Probe(snap(128.0)), sample_interval=1.0)
    plan = runtime.current_plan()
    assert plan is not None and plan.fleet_ceilings is not None and plan.cells > 1
    assert g._container is not None
    assert dataclasses.astuple(g._container.fleet) == tuple(plan.fleet_ceilings)
    assert g._container.cells == 1  # one Docling process: shared by the processes that run, not the plan's proposal


def test_an_indexing_process_alone_applies_its_own_cells_cap_not_the_plans_cell_share(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One indexing process at 128 CPUs (the plan proposes 8 cells): its share of the container limits is the
    whole, bounded by its own cells=1 ceilings, not 1/8 of them."""
    g = _governor(tmp_path, monkeypatch, auto=True, cpus=128.0, workers=1)
    plan = runtime.current_plan()
    assert plan is not None and plan.cells > 1 and g._container is not None
    assert g._container.cells == 1


def test_the_shared_ramp_is_the_same_whichever_service_is_controller(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The indexing governor (several cells, embedding reservation) and the Docling governor build the container
    ramp on the same fleet, so the elected service never changes the limits every follower applies."""
    _governor(tmp_path, monkeypatch, auto=True, cpus=64.0, workers=4)
    fleets = {}
    for service, workers in (("indexing", 4), ("docling", 1)):
        monkeypatch.setenv("PIPESHUB_SERVICE", service)
        runtime.reset_for_tests()
        g = ResourceGovernor(logger=logging.getLogger("t"), probe=Probe(snap(64.0)), worker_count=workers, sample_interval=1.0)
        assert g._container is not None
        fleets[service] = g._container.fleet
    assert fleets["indexing"] == fleets["docling"]


def test_a_local_memory_incident_outlasts_the_shared_ramp(tmp_path: Path) -> None:
    """The heavy pool was halved by an incident and is cooling down: a healthy shared ramp must not lift it early."""
    import dataclasses

    from app.capacity.budget_policy import AdmissionPolicy, plan_floors
    from app.capacity.controller import ElectedController
    from app.capacity.member_budget import MemberBudget
    from app.services.resource_governor.models import Ceilings

    d = tmp_path / "ledger"
    d.mkdir(mode=0o700)
    b = MemberBudget(d, cpu_tokens=160, mem_bytes=256 << 30, floors=plan_floors(128, 160, None, 1.0, AdmissionPolicy()))
    link = cl.ContainerLink(b, ElectedController(b, 15), Ceilings(112, 544, 224, 544), 1)
    container = Limits(values={Pool.HEAVY_PARSE: 8, Pool.LIGHT_PARSE: 8, Pool.INDEX_HEAVY: 40, Pool.INDEX_LIGHT: 40})
    epoch = b.elect_controller(15)
    b.controller_beat(epoch, cl.encode_state(container, ControllerState.initial()))
    state = ControllerState.initial().with_update(
        Pool.HEAVY_PARSE, dataclasses.replace(ControllerState.initial().get(Pool.HEAVY_PARSE), cooldown_until=500.0)
    )
    own = (Limits(values={Pool.HEAVY_PARSE: 4, Pool.LIGHT_PARSE: 8, Pool.INDEX_HEAVY: 40, Pool.INDEX_LIGHT: 40}), state)

    def run(now: float) -> int:
        limits, _ = link.step(
            own=own, snap=snap(128), ceilings=Ceilings(112, 544, 224, 544), demand={}, now=now, interval=15.0,
            feedback=None, heavy_working_set_gb=1.5,
        )
        return limits.get(Pool.HEAVY_PARSE)

    assert run(100.0) <= 4  # cooling down: the incident's halved limit holds
    assert run(600.0) > 4  # cooldown over: the shared ramp applies again


@pytest.mark.asyncio
async def test_a_slow_ledger_step_does_not_stall_the_event_loop_or_an_incident_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    import time

    g = _governor(tmp_path, monkeypatch, auto=True)
    real_step = g._container.step
    release = {"at": 0.0}

    def slow_step(**kw: object) -> object:
        time.sleep(0.4)  # a ledger lock held by a slow process
        release["at"] = time.monotonic()
        return real_step(**kw)

    monkeypatch.setattr(g._container, "step", slow_step)
    gaps: list[float] = []

    async def ticker() -> None:
        last = time.monotonic()
        while not release["at"]:
            await asyncio.sleep(0.01)
            now = time.monotonic()
            gaps.append(now - last)
            last = now

    incident_took: list[float] = []

    async def incident() -> None:
        await asyncio.sleep(0.1)  # while the step runs
        started = time.monotonic()
        await asyncio.to_thread(g.report_memory_incident, "test")
        incident_took.append(time.monotonic() - started)

    await asyncio.gather(g._sample_once(), ticker(), incident())
    assert max(gaps) < 0.2, max(gaps)  # the loop kept running during the 0.4 s step
    assert incident_took[0] < 0.2  # the state lock was not held across the step
    # the incident's state stands after the step's result is applied
    assert g._state.get(Pool.HEAVY_PARSE).cooldown_until > 0


def test_a_service_that_parses_in_another_pod_keeps_the_lifted_ceilings_and_joins_no_shared_ramp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With remote parse slots the heavy ceiling is the remote pool's width; the member budget's local partition and
    the container ramp (sized from this container's CPUs) must not cap it."""
    monkeypatch.setenv("PIPESHUB_CAPACITY_PROFILE", "auto")
    monkeypatch.setenv("PIPESHUB_CAPACITY_DIR", str(tmp_path / "cap"))
    monkeypatch.setenv("PIPESHUB_CPU_BUDGET", "8")
    monkeypatch.setenv("PIPESHUB_MEMORY_BUDGET", str(64 * 2**30))
    monkeypatch.setenv("PIPESHUB_SERVICE", "indexing")
    g = ResourceGovernor(
        logger=logging.getLogger("t"), probe=Probe(snap(8.0)), sample_interval=1.0, remote_parse_slots=40
    )
    assert g._fleet_ceilings.heavy >= 40
    assert g._container is None


def _link_with_shared(tmp_path: Path, shared: Limits, fleet: object = None) -> cl.ContainerLink:
    from app.capacity.budget_policy import AdmissionPolicy, plan_floors
    from app.capacity.controller import ElectedController
    from app.capacity.member_budget import MemberBudget
    from app.services.resource_governor.models import Ceilings

    d = tmp_path / "ledger"
    d.mkdir(mode=0o700)
    b = MemberBudget(d, cpu_tokens=160, mem_bytes=256 << 30, floors=plan_floors(128, 160, None, 1.0, AdmissionPolicy()))
    link = cl.ContainerLink(b, ElectedController(b, 15), fleet or Ceilings(112, 544, 224, 544), 1)
    epoch = b.elect_controller(15)
    b.controller_beat(epoch, cl.encode_state(shared, ControllerState.initial()))
    return link


def _step(link: cl.ContainerLink, own: tuple, ceilings: object) -> Limits:
    limits, _ = link.step(
        own=own, snap=snap(128), ceilings=ceilings, demand={}, now=100.0, interval=15.0, feedback=None, heavy_working_set_gb=1.5
    )
    return limits


def test_a_local_index_latency_hold_outlasts_the_shared_ramp(tmp_path: Path) -> None:
    import dataclasses

    from app.services.resource_governor.models import Ceilings, LimitRule

    shared = Limits(values={Pool.HEAVY_PARSE: 8, Pool.LIGHT_PARSE: 8, Pool.INDEX_HEAVY: 100, Pool.INDEX_LIGHT: 100})
    link = _link_with_shared(tmp_path, shared)
    state = ControllerState.initial().with_update(
        Pool.INDEX_HEAVY, dataclasses.replace(ControllerState.initial().get(Pool.INDEX_HEAVY), rule=LimitRule.LATENCY_HOLD)
    )
    own = (Limits(values={Pool.HEAVY_PARSE: 8, Pool.LIGHT_PARSE: 8, Pool.INDEX_HEAVY: 10, Pool.INDEX_LIGHT: 100}), state)
    limits = _step(link, own, Ceilings(112, 544, 224, 544))
    assert limits.get(Pool.INDEX_HEAVY) <= 10  # held by the local latency view, not lifted by the shared ramp
    assert limits.get(Pool.INDEX_LIGHT) > 10  # a pool that is not held follows the shared limit


def test_a_pool_this_process_runs_without_stays_without_when_the_shared_plan_has_it(tmp_path: Path) -> None:
    from app.services.resource_governor.models import Ceilings

    shared = Limits(values={Pool.HEAVY_PARSE: 8, Pool.LIGHT_PARSE: 8, Pool.INDEX_HEAVY: 100, Pool.INDEX_LIGHT: 100})
    link = _link_with_shared(tmp_path, shared)
    own = (Limits(values={Pool.HEAVY_PARSE: 8, Pool.LIGHT_PARSE: 8, Pool.INDEX_HEAVY: 10, Pool.INDEX_LIGHT: 0}), ControllerState.initial())
    limits = _step(link, own, Ceilings(112, 544, 224, 0))  # the local index_light ceiling is zero (collapsed tier)
    assert limits.get(Pool.INDEX_LIGHT) == 0
