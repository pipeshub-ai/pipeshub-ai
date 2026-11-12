"""``PARSE_LANES`` off (or unset) leaves the memory gates exactly as they were (PX-2e, T86).

``fixtures/policy_off_trace.json`` was recorded by ``policy_off_trace`` from the sources before the
per-kind charges existed: the working-set and cap functions over an environment grid,
``record_memory_cost`` and ``parse_cost``, the calls ``reserve_record_memory`` and
``acquire_parsing_slot`` make, and what a governor's limits and ``stats()`` show over a few
samples. The functions that 2e had to edit are pinned by those behaviours; every other top-level
statement, function and method of the four modules is pinned by a hash of its source tree.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.resource_governor.kinds import ParseKind
from tests.unit.services.resource_governor import policy_off_trace as trace

RECORDED = json.loads(trace.FIXTURE.read_text())
ROOT = Path(__file__).resolve().parents[4]

# Entries that differ from the recorded source, and why. Each one's behaviour is pinned below.
EDITED = {
    "policy.py": {
        "heavy_memory_cap", "cgroup_heavy_parse_limit", "_target_for", "_next_pool_limit", "next_limits",  # an optional figure, None = legacy
        "docling_working_set_gb", "resolve_heavy_working_set", "class HeavyWorkingSet", "HeavyWorkingSet.gb",  # new
        "HeavyWorkingSet.source", "HeavyWorkingSet.as_dict",
        "assign:PDF_PARENT_WORKING_SET_GB", "assign:DOCLING_WORKING_SET_DEFAULT_GB",
    },
    "memory_budget.py": {"kind_reservation_cost"},  # new; record_memory_cost itself is unchanged
    "consumer_concurrency.py": {
        "reserve_record_memory", "acquire_parsing_slot",  # optional kind; no kind or no excess = the old path
        "record_parse_kind",
        "RequeueWithoutAttempt.pauses_consumer",  # read only by the PostgreSQL consumer's stage admission, not by a memory gate
        "class ProgressRequeue", "ProgressRequeue.__init__", "ProgressRequeue.counts_toward_backstop",  # new exception, raised after the handler has unwound and its memory is released
        "ProgressRequeue:Expr", "requeue_after_productive_cut",  # new; runs in the consumers' timeout handler and reserves nothing
        "if:TYPE_CHECKING",  # one more type-only import
    },
    "controller.py": {
        "ResourceGovernor.__init__", "ResourceGovernor._sample_once", "ResourceGovernor.stats",
        "ResourceGovernor.try_acquire_heavy_parse_slots", "ResourceGovernor.heavy_working_set",
        "ResourceGovernor._policy_working_set", "ResourceGovernor._measured_base_applies",
        "ResourceGovernor._resolve_heavy_working_set",
    },
}


def _changed(name: str) -> set[str]:
    current = trace.unedited_hashes(ROOT / trace.MODULES[name])
    recorded = RECORDED["hashes"][name]
    return {k for k in recorded.keys() | current.keys() if recorded.get(k) != current.get(k)}


@pytest.mark.parametrize("name", sorted(trace.MODULES))
def test_off_pin_covers_whole_module_except_allowlist(name: str) -> None:
    unexpected = {k for k in _changed(name) if not k.startswith("import:")} - EDITED[name]
    assert not unexpected, f"{name} changed outside the allowlist: {sorted(unexpected)}"


@pytest.mark.parametrize("name", sorted(trace.MODULES))
def test_off_pin_allowlist_is_not_padded(name: str) -> None:
    padded = EDITED[name] - _changed(name)
    assert not padded, f"{name}: entries that did not change: {sorted(padded)}"


def test_record_memory_cost_and_parse_cost_are_unchanged() -> None:
    assert trace.cost_grid() == RECORDED["cost"]


def test_policy_functions_give_main_s_answers_over_the_environment_grid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PARSE_LANES", raising=False)
    now = trace.policy_grid()
    recorded = RECORDED["policy"]
    assert now.keys() == recorded.keys()
    differing = {k: (now[k], recorded[k]) for k in now if now[k] != recorded[k]}
    assert not differing, dict(list(differing.items())[:5])
    assert len(now) > 400, "the grid is not a handful of points"


@pytest.mark.parametrize("mode", ["off", "", "junk", "yes"])
def test_the_policy_grid_does_not_depend_on_the_mode_value(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    monkeypatch.setenv("PARSE_LANES", mode)
    assert trace.policy_grid() == RECORDED["policy"]


def test_consumer_helpers_make_the_same_calls_and_admissions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PARSE_LANES", raising=False)
    import asyncio

    now = asyncio.run(trace._consumer_trace())
    recorded = RECORDED["consumer"]
    assert now.keys() == recorded.keys()
    assert not {k for k in now if json.loads(json.dumps(now[k], default=str)) != recorded[k]}


@pytest.mark.parametrize("kind", [k.value for k in ParseKind])
def test_consumer_trace_with_a_kind_is_the_same_in_off(monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    """A kind passed in off changes no call and no Admission field: the excess is zero and nothing is reserved."""
    import asyncio
    import logging
    from types import SimpleNamespace

    from app.services.messaging import consumer_concurrency as cc
    from app.services.resource_governor.models import ParseTier
    from tests.unit.services.messaging.governor_test_helpers import make_test_governor

    monkeypatch.delenv("PARSE_LANES", raising=False)

    async def run(kind: ParseKind | None) -> list[object]:
        governor = make_test_governor(logger_name="test.off.kind")
        host = SimpleNamespace(governor=governor, logger=logging.getLogger("test.off.kind"))
        calls: list[object] = []
        real = governor.memory_budget.reserve

        async def reserve(n, timeout=None, *, held=0):  # noqa: ANN202
            calls.append(("reserve", n, held))
            return await real(n, timeout, held=held)

        governor.memory_budget.reserve = reserve  # type: ignore[method-assign]
        out: list[object] = []
        for size in (None, 10 * 2**20, 100 * 2**20):
            for tier in (ParseTier.HEAVY, ParseTier.LIGHT):
                memory = await cc.reserve_record_memory(host, tier, size, kind=kind)
                slot = await cc.acquire_parsing_slot(host, tier, size, kind=kind)
                out.append((None if memory is None else memory.cost, slot.cost, sorted(slot.__dict__)))
                cc.release_admission(slot)
                cc.release_admission(memory)
        governor.close()
        return [out, calls]

    assert asyncio.run(run(ParseKind(kind))) == asyncio.run(run(None))


def test_governor_limits_and_health_fields_are_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PARSE_LANES", raising=False)
    now = json.loads(json.dumps(trace.governor_trace(), default=str))
    for value in (*now.values(), *RECORDED["governor"].values()):
        value["lanes"] = {"mode": value["lanes"]["mode"]}  # the plan reads this host's CPU count
    assert now == RECORDED["governor"]
    for value in now.values():
        assert "heavy_working_set" not in value["keys"]


def test_off_creates_no_ledger_v3_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("PARSE_LANES", raising=False)
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    from app.services.resource_governor import lanes as lanes_module
    from tests.unit.services.resource_governor.test_heavy_working_set import (
        make,
        sample,
    )

    governor = make()
    sample(governor)
    assert governor.lanes is None
    governor.close()
    assert not [p for p in tmp_path.iterdir() if "cpu-lanes" in p.name]
    assert lanes_module.open_lane_budget(
        lanes_module.plan_lanes(cpus=4, heavy=4), local_share=1, environ={"PARSE_LANES": "off"}
    ) is None


def test_off_never_asks_the_memory_budget_for_an_excess_on_the_route(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.resource_governor.kinds import ParseKind, permit_excess_bytes
    from app.services.resource_governor.policy import resolve_heavy_working_set

    monkeypatch.delenv("PARSE_LANES", raising=False)
    ws = resolve_heavy_working_set()
    for kind in ParseKind:
        assert permit_excess_bytes(kind, ws.gb, ws.source) == 0


def test_governor_trace_ignores_downstream_feedback_left_by_other_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Throttles reported to the process-wide feedback by an earlier test must not narrow the trace."""
    from app.services.resource_governor.feedback import (
        DownstreamFeedback,
        set_default_downstream_feedback,
    )

    monkeypatch.delenv("PARSE_LANES", raising=False)
    left_over = DownstreamFeedback()
    for _ in range(50):
        left_over.report_throttle("embedding")
        left_over.report_timeout("vector_db")
    set_default_downstream_feedback(left_over)
    try:
        now = json.loads(json.dumps(trace.governor_trace(), default=str))
    finally:
        set_default_downstream_feedback(None)
    for value in (*now.values(), *RECORDED["governor"].values()):
        value["lanes"] = {"mode": value["lanes"]["mode"]}
    assert now == RECORDED["governor"]
