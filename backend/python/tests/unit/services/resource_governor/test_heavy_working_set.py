"""The heavy permit's working set and where it comes from (PX-2e, T81, T82, T90, R6-4, R6-6).

The retune from 1.5 GiB to the measured PdfPlumber parent base (0.25 GiB) is right only where a
shared lane budget charges the worker's memory. Each test builds a real ``ResourceGovernor``
with a scripted probe, so the working set the controller resolves is the one the limits use.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import pytest

from app.services.resource_governor import lanes as lanes_module
from app.services.resource_governor import policy
from app.services.resource_governor.controller import ResourceGovernor
from app.services.resource_governor.models import Ceilings, Pool, ResourceSnapshot

if TYPE_CHECKING:
    from pathlib import Path

GIB = 2**30


class Probe:
    def __init__(self, limit_gb: float = 16.0, raw_gb: float = 2.0) -> None:
        self.snap = ResourceSnapshot(
            cpu_quota=16.0, cpu_utilisation=0.1, cpu_throttled_ratio=0.0, cpu_pressure=0.0,
            mem_limit_bytes=int(limit_gb * GIB), mem_working_set_bytes=int(raw_gb * GIB),
            mem_working_set_raw_bytes=int(raw_gb * GIB), source="test",
        )

    def snapshot(self) -> ResourceSnapshot:
        return self.snap


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in (
        "GOVERNOR_HEAVY_PARSE_WORKING_SET_GB", "GOVERNOR_DOCLING_WORKING_SET_GB", "PARSE_POOL_WORKERS",
        "GOVERNOR_LANE_WORKING_SET_GB", "PARSE_LANES",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(policy, "HEAVY_PARSE_WORKING_SET_GB", 1.5)
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))


def make(*, lanes: str | None = None, probe: Probe | None = None, **kwargs) -> ResourceGovernor:
    return ResourceGovernor(
        logger=logging.getLogger("test.heavy_ws"), probe=probe or Probe(), sample_interval=1.0,
        env_index=24, **kwargs,
    )


def sample(governor: ResourceGovernor) -> None:
    asyncio.run(governor._sample_once())


@pytest.fixture
def on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PARSE_LANES", "on")


# -- the pure order -----------------------------------------------------------------------


def test_heavy_ws_resolution_order(monkeypatch: pytest.MonkeyPatch) -> None:
    both = {"measured_base": True, "docling_gb": 1.5}
    assert policy.resolve_heavy_working_set(**both) == (1.5, "docling")
    assert policy.resolve_heavy_working_set(measured_base=True) == (0.25, "measured")
    assert policy.resolve_heavy_working_set() == (1.5, "fallback")
    monkeypatch.setenv("GOVERNOR_HEAVY_PARSE_WORKING_SET_GB", "3.0")
    monkeypatch.setattr(policy, "HEAVY_PARSE_WORKING_SET_GB", 3.0)
    assert policy.resolve_heavy_working_set(**both) == (3.0, "explicit")


def test_explicit_wins_exactly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOVERNOR_HEAVY_PARSE_WORKING_SET_GB", "0.6")
    monkeypatch.setattr(policy, "HEAVY_PARSE_WORKING_SET_GB", 0.6)
    monkeypatch.setattr(policy, "excel_document_memory_bytes", lambda: 2 * GIB)  # above the setting: still used as given
    resolved = policy.resolve_heavy_working_set(measured_base=True)
    assert resolved == (0.6, "explicit")
    # And the permit reserves no per-kind excess for an explicit figure.
    from app.services.resource_governor.kinds import ParseKind, permit_excess_bytes

    assert permit_excess_bytes(ParseKind.DOCLING_DOCX, resolved.gb, resolved.source) == 0


def test_explicit_equal_to_the_default_is_still_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOVERNOR_HEAVY_PARSE_WORKING_SET_GB", "1.5")
    assert policy.resolve_heavy_working_set(measured_base=True).source == "explicit"


def test_off_keeps_the_old_constant_for_the_working_set_functions(monkeypatch: pytest.MonkeyPatch) -> None:
    snap = Probe(8.0, 1.0).snap
    assert policy.heavy_memory_cap(snap) == policy.heavy_memory_cap(snap, 1, None) == int(7 / 1.5)
    assert policy.heavy_memory_cap(snap, 1, 0.25) == 28
    ceilings = Ceilings(heavy=8, light=40, index_heavy=12, index_light=24)
    assert policy.cgroup_heavy_parse_limit(snap, ceilings) == 4
    assert policy.cgroup_heavy_parse_limit(snap, ceilings, 0.25) == 8
    assert policy._target_for(Pool.HEAVY_PARSE, snap, ceilings) == 4
    assert policy._target_for(Pool.HEAVY_PARSE, snap, ceilings, 1, 0.25) == 8


# -- the controller's resolution ----------------------------------------------------------


def test_off_stays_on_the_fallback_and_shows_no_new_field() -> None:
    governor = make()
    sample(governor)
    assert governor.heavy_working_set == (1.5, "fallback")
    assert "heavy_working_set" not in governor.stats()
    assert governor._policy_working_set() is None
    governor.close()


def test_off_ignores_a_lane_budget_that_exists() -> None:
    """Even with a shared budget open in the process, off keeps the old constant and shows no new field."""
    governor = make()
    plan = lanes_module.plan_lanes(cpus=16, heavy=16)
    budget = lanes_module.LaneBudget.open(plan, governor_dir(governor))
    governor._lanes, governor._lanes_built = budget, True
    assert budget.shared()
    sample(governor)
    assert governor.heavy_working_set == (1.5, "fallback")
    assert "heavy_working_set" not in governor.stats()
    governor.close()


def governor_dir(governor: ResourceGovernor) -> Path:
    import tempfile
    from pathlib import Path

    return Path(tempfile.mkdtemp(prefix="lanes-off-", dir=tempfile.gettempdir())) / "d"


def test_measured_base_needs_a_budgeted_pool(on: None) -> None:
    governor = make()
    sample(governor)
    assert governor.heavy_working_set.source == "fallback", "no lane budget has been built yet"
    assert governor.lanes is not None and governor.lanes.shared()
    sample(governor)
    assert governor.heavy_working_set == (0.25, "measured")
    assert governor.stats()["heavy_working_set"] == {"gb": 0.25, "source": "measured"}
    governor.close()


@pytest.mark.parametrize("workers", ["0", "1", "4", "16"])
def test_an_explicit_pool_size_is_private_or_absent_and_keeps_the_old_constant(
    on: None, monkeypatch: pytest.MonkeyPatch, workers: str
) -> None:
    monkeypatch.setenv("PARSE_POOL_WORKERS", workers)
    governor = make()
    assert governor.lanes is not None
    sample(governor)
    assert governor.heavy_working_set == (1.5, "fallback")
    governor.close()


def test_a_junk_pool_size_is_not_explicit(on: None, monkeypatch: pytest.MonkeyPatch) -> None:
    for junk in ("", "x", "-3", "auto"):
        monkeypatch.setenv("PARSE_POOL_WORKERS", junk)
        governor = make()
        assert governor.lanes is not None
        sample(governor)
        assert governor.heavy_working_set.source == "measured", junk
        governor.close()


def test_explicit_setting_beats_the_retune(on: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOVERNOR_HEAVY_PARSE_WORKING_SET_GB", "2.0")
    monkeypatch.setattr(policy, "HEAVY_PARSE_WORKING_SET_GB", 2.0)
    governor = make()
    assert governor.lanes is not None
    sample(governor)
    assert governor.heavy_working_set == (2.0, "explicit")
    assert governor._policy_working_set() is None, "the legacy rule already returns it exactly"
    governor.close()


def test_retune_reverts_when_budget_degraded_or_local(on: None, monkeypatch: pytest.MonkeyPatch) -> None:
    # A local budget (the shared directory was unusable) protects this process alone.
    def broken(*a: object, **k: object) -> None:
        raise OSError("read-only")

    with monkeypatch.context() as scoped:
        scoped.setattr(lanes_module.LaneBudget, "open", classmethod(lambda cls, *a, **k: broken()))
        local = make()
        assert local.lanes is not None and local.lanes.stats()["slots"] == "local"
        sample(local)
        assert local.heavy_working_set == (1.5, "fallback")
        local.close()

    # A shared budget whose ledger has gone bad.
    governor = make()
    monkeypatch.setenv("PARSE_LANES", "on")
    budget = governor.lanes
    assert budget is not None
    sample(governor)
    assert governor.heavy_working_set.source == "measured"
    monkeypatch.setattr(type(budget._ledger), "degraded", property(lambda self: True))
    sample(governor)
    assert governor.heavy_working_set == (1.5, "fallback")
    governor.close()


def test_heavy_ws_returns_to_the_old_constant_without_a_pool(on: None) -> None:
    governor = make()
    assert governor.lanes is not None
    sample(governor)
    assert governor.heavy_working_set.source == "measured"
    governor.close()  # the budget goes away
    sample(governor)
    assert governor.heavy_working_set == (1.5, "fallback")
    assert governor._policy_working_set() is None


def test_unknown_mode_values_are_off(monkeypatch: pytest.MonkeyPatch) -> None:
    for value in ("", "yes", "1", "true"):
        monkeypatch.setenv("PARSE_LANES", value)
        governor = make()
        sample(governor)
        assert governor.heavy_working_set.source == "fallback", value
        assert "heavy_working_set" not in governor.stats()
        governor.close()


def test_heavy_limit_follows_the_resolved_working_set(on: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """A 16 GiB box with 14 GiB free and a CPU ceiling of 16: 1.5 GiB each admits 9, 0.25 GiB admits 16."""
    probe = Probe(16.0, 2.0)
    governor = make(probe=probe)
    assert governor.lanes is not None
    ceilings = governor.ceilings
    sample(governor)
    sample(governor)
    assert governor.heavy_working_set.source == "measured"
    after = policy.heavy_memory_cap(probe.snap, 1, governor._policy_working_set())
    legacy = policy.heavy_memory_cap(probe.snap, 1)
    assert after > legacy
    assert policy._target_for(Pool.HEAVY_PARSE, probe.snap, ceilings, 1, governor._policy_working_set()) == ceilings.heavy
    governor.close()


def test_retune_step_after_first_pool_use_is_bounded(on: None) -> None:
    """The step from the old to the new figure happens at the first sample after the pool is first used,
    and the limit then moves by the usual growth step, not in one jump."""
    probe = Probe(16.0, 2.0)
    governor = make(probe=probe)
    sample(governor)
    before = governor.limit(Pool.HEAVY_PARSE)
    assert governor.heavy_working_set.source == "fallback"
    assert governor.lanes is not None  # first use of the pool
    sample(governor)
    assert governor.heavy_working_set.source == "measured"
    after = governor.limit(Pool.HEAVY_PARSE)
    assert after <= before + 1 or after <= policy.floor_for(Pool.HEAVY_PARSE, governor.ceilings.heavy) + 2
    governor.close()


# -- T82: Docling keeps its own constant -------------------------------------------------------


def test_docling_permit_cost_independent_of_pdfplumber_working_set(
    on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = Probe(16.0, 2.0)
    governor = make(probe=probe, heavy_working_set_gb=policy.docling_working_set_gb())
    assert governor.lanes is not None
    sample(governor)
    assert governor.heavy_working_set == (1.5, "docling")
    expected = policy.heavy_memory_cap(probe.snap, 1, 1.5)
    # Lowering the PdfPlumber base, or the flat lane figure, changes nothing for this governor.
    monkeypatch.setattr(policy, "PDF_PARENT_WORKING_SET_GB", 0.1)
    monkeypatch.setenv("GOVERNOR_LANE_WORKING_SET_GB", "0.2")
    sample(governor)
    assert governor.heavy_working_set == (1.5, "docling")
    assert policy.heavy_memory_cap(probe.snap, 1, governor._policy_working_set()) == expected == int(14 / 1.5)
    governor.close()


def test_docling_working_set_is_its_own_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    assert policy.docling_working_set_gb() == 1.5
    monkeypatch.setenv("GOVERNOR_DOCLING_WORKING_SET_GB", "2.25")
    assert policy.docling_working_set_gb() == 2.25
    monkeypatch.setenv("GOVERNOR_DOCLING_WORKING_SET_GB", "junk")
    assert policy.docling_working_set_gb() == 1.5


def test_docling_governor_in_off_keeps_the_legacy_rule() -> None:
    governor = make(heavy_working_set_gb=3.0)
    sample(governor)
    assert governor.heavy_working_set == (1.5, "fallback")
    governor.close()


def test_docling_governor_never_takes_the_measured_base(on: None) -> None:
    governor = make(heavy_working_set_gb=1.5)
    assert governor.lanes is not None and governor.lanes.shared()
    sample(governor)
    assert governor.heavy_working_set.source == "docling"
    governor.close()
