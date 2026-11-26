"""The switch, the class of a record, and the pools' inputs (``app.modules.parse_engine.wiring`` and ``pools``)."""
from __future__ import annotations

import pytest

from app.capacity.classes import CostClass
from app.modules.parse_engine import pools, wiring
from app.modules.parse_engine.budget_port import Family
from app.services.resource_governor.kinds import ParseKind
from app.services.resource_governor.models import ParseTier

MIB = 1 << 20


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PARSE_ADMISSION", raising=False)
    monkeypatch.delenv("PARSE_LANES", raising=False)
    wiring.reset_for_tests()


def test_the_default_is_legacy_and_only_classes_turns_it_on(monkeypatch: pytest.MonkeyPatch) -> None:
    assert not wiring.classes_enabled()
    for value, on in (("classes", True), (" Classes ", True), ("legacy", False), ("on", False), ("", False)):
        monkeypatch.setenv("PARSE_ADMISSION", value)
        assert wiring.classes_enabled() is on


def test_lanes_and_classes_are_refused_together_naming_both(monkeypatch: pytest.MonkeyPatch) -> None:
    """A permit handed over keeps its owner."""
    monkeypatch.setenv("PARSE_ADMISSION", "classes")
    monkeypatch.setenv("PARSE_LANES", "on")
    with pytest.raises(wiring.AdmissionConfigError) as err:
        wiring.assert_compatible()
    assert "PARSE_LANES" in str(err.value) and "PARSE_ADMISSION" in str(err.value)
    monkeypatch.setenv("PARSE_LANES", "off")
    wiring.assert_compatible()
    monkeypatch.setenv("PARSE_ADMISSION", "legacy")
    monkeypatch.setenv("PARSE_LANES", "on")
    wiring.assert_compatible()  # lanes alone is the existing mode


def test_set_resource_governor_refuses_the_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.parsers import parse_pool

    monkeypatch.setenv("PARSE_ADMISSION", "classes")
    monkeypatch.setenv("PARSE_LANES", "on")
    with pytest.raises(wiring.AdmissionConfigError):
        parse_pool.set_resource_governor(None)


@pytest.mark.parametrize(
    ("ext", "size", "level"),
    [
        ("md", 5 * MIB, ParseTier.LIGHT),  # a light format has no class
        ("pdf", None, CostClass.SMALL),  # size unknown: biased small
        ("pdf", 19 * MIB, CostClass.SMALL),
        ("pdf", 150 * MIB, CostClass.LARGE),  # only a size that alone proves it
        ("xlsx", 30 * MIB, CostClass.SMALL),
    ],
)
def test_the_level_of_a_record(ext: str, size: int | None, level: object, monkeypatch: pytest.MonkeyPatch) -> None:
    assert wiring.level_for(ext, "", size) == level


def test_a_class_level_is_heavy_work_and_a_tier_is_left_alone() -> None:
    assert wiring.as_tier(CostClass.LARGE) is ParseTier.HEAVY
    assert wiring.as_tier(ParseTier.LIGHT) is ParseTier.LIGHT
    assert wiring.as_tier(None) is None


def test_the_class_context_is_none_with_classes_off_and_carries_the_org_otherwise(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"orgId": "o1", "extension": "pdf", "mimeType": "application/pdf", "parseEnqueuedAtMs": 1_700_000_000_000}
    assert wiring.class_context(payload) is None
    monkeypatch.setenv("PARSE_ADMISSION", "classes")
    ctx = wiring.class_context(payload, priority=True)
    assert ctx is not None
    assert (ctx.org, ctx.extension, ctx.priority, ctx.first_seen_ms) == ("o1", "pdf", True, 1_700_000_000_000)
    assert wiring.class_context({"parseEnqueuedAtMs": "x"}).first_seen_ms is None  # type: ignore[union-attr]
    assert wiring.class_context({"parseEnqueuedAtMs": True}).first_seen_ms is None  # type: ignore[union-attr]


def test_the_lane_reserves_an_inspect_token_for_an_inspection_and_the_records_class_otherwise() -> None:
    d = wiring.lane_demand(ParseKind.INSPECT, CostClass.LARGE)
    assert d.family is Family.INSPECT
    d = wiring.lane_demand(ParseKind.PDF_LAYOUT, CostClass.HUGE)
    assert (d.family, d.cls, d.cpu) == (Family.PARSE, CostClass.HUGE, 1)
    assert wiring.lane_demand(None, None).cls == CostClass.SMALL  # unscoped is small


def test_pool_width_is_the_legacy_width_without_a_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PARSE_POOL_WORKERS", raising=False)
    assert wiring.pool_width(8) == 4
    assert wiring.pool_width(3) == 1
    monkeypatch.setenv("PARSE_POOL_WORKERS", "2")
    assert wiring.pool_width(8) == 2


def test_routes() -> None:
    assert pools.route(ParseKind.INSPECT, CostClass.LARGE) is pools.Route.INSPECT
    assert pools.route(ParseKind.PDF_LAYOUT, CostClass.LARGE) is pools.Route.BIG
    assert pools.route(ParseKind.PDF_LAYOUT, CostClass.HUGE) is pools.Route.BIG
    assert pools.route(ParseKind.PDF_LAYOUT, CostClass.SMALL) is pools.Route.SMALL


def test_an_unscoped_job_goes_small_and_is_counted() -> None:
    before = pools.unscoped_total()
    assert pools.route(ParseKind.TEXT, None) is pools.Route.SMALL
    assert pools.unscoped_total() == before + 1


def test_a_borrowed_permit_is_adopted_and_never_released_by_the_lane() -> None:
    class P:
        family, cpu, mem_bytes, cls = Family.PARSE, 1, 0, CostClass.LARGE
        released = 0

        def release(self) -> None:
            P.released += 1

    owner = P()
    with pools.parse_scope(CostClass.LARGE, owner):
        borrowed = pools.lane_permit(ParseKind.PDF_LAYOUT)
        assert borrowed is not None
        assert borrowed.adopt() is borrowed
        borrowed.release()
        borrowed.release()
        assert P.released == 0  # the admission that took it releases it
        assert pools.lane_permit(ParseKind.INSPECT) is None  # an inspection reserves its own
    assert pools.lane_permit(ParseKind.PDF_LAYOUT) is None  # the scope ended
    assert pools.current_class() is None


def test_the_width_budget_caps_live_workers_and_hands_a_slot_on() -> None:
    b = pools.WidthBudget(2)
    assert b.acquire(0.1) and b.acquire(0.1)
    assert not b.acquire(0.05)  # a third waits, then gives up
    b.release()
    assert b.acquire(0.1)
    assert b.live == 2 and b.peak == 2


def test_a_waiting_acquire_stops_when_abandoned() -> None:
    b = pools.WidthBudget(1)
    assert b.acquire(0.1)
    assert not b.acquire(5.0, abandoned=lambda: True)


def test_a_classified_job_is_not_counted_as_unscoped() -> None:
    before = pools.unscoped_total()
    pools.route(ParseKind.PDF_LAYOUT, CostClass.LARGE)
    pools.route(ParseKind.INSPECT, None)
    assert pools.unscoped_total() == before
