"""CPD-6 the production live layer cache beyond its contract (LL-1..5 run in ``test_runtime_ports.py``): what it
passes the route, a layer it cannot read, the model-access narrowing, and reads while a reload is in flight."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

import pytest

from app.control_plane.runtime.live_layer import (
    ReloadingLiveLayerCache,
    folded_live_layer,
)
from app.control_plane.testing.contracts import (
    ORG,
    LiveLayerCacheContract,
    change_event,
)
from app.control_plane.testing.memory import (
    InMemoryLiveLayerCache,
    InMemoryLiveLayerSource,
    live_approvals,
)
from harness_core.access.vendor_rules import OrgAccessPolicy
from harness_core.policy.orders import InvalidPolicyValue
from harness_core.policy.resolver import DimensionPolicyResolver
from harness_core.testing.clock import ManualClock

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic import JsonValue

    from app.control_plane.contracts.generated.live_layer import (
        LiveLayer as LiveLayerDocument,
    )
    from harness_core.policy.types import FoldedPrefix


@dataclass
class _RouteHarness:
    clock: ManualClock = field(default_factory=ManualClock)
    backstop: InMemoryLiveLayerSource = field(default_factory=InMemoryLiveLayerSource)

    def __post_init__(self) -> None:
        self.cache = ReloadingLiveLayerCache(self.backstop, self.clock, bound=2)

    def loads(self) -> int:
        return len(self.backstop.calls)


class TestReloadingLiveLayerCacheContract(LiveLayerCacheContract):
    @pytest.fixture
    def live_harness(self) -> _RouteHarness:
        return _RouteHarness()


def _models(agent_key: str | None = None, **models: JsonValue) -> JsonValue:
    scoped: dict[str, JsonValue] = {"layer": {"schemaVersion": 1, "scope": {"kind": "live"}, "models": models}}
    if agent_key is not None:
        scoped["agentKey"] = agent_key
    return scoped


async def test_cpd6_41_a_ttl_reload_asks_only_for_what_changed_and_a_marked_one_for_the_whole_layer() -> None:
    clock = ManualClock()
    cache = InMemoryLiveLayerCache(clock)
    cache.source.put(ORG, 10, live_approvals({"writes": "ask"}))

    _ = await cache.current(ORG)
    _ = clock.advance(30.0)
    unchanged = await cache.current(ORG)
    cache.on_change(change_event(ORG, 11))
    _ = await cache.current(ORG)

    assert cache.source.calls == [(ORG, None), (ORG, 10), (ORG, None)]
    assert (unchanged.revision, unchanged.stale) == (10, False)


async def test_cpd6_41_an_unchanged_answer_confirms_the_layer_for_another_time_to_live() -> None:
    clock = ManualClock()
    cache = InMemoryLiveLayerCache(clock)
    cache.source.put(ORG, 10)
    _ = await cache.current(ORG)
    _ = clock.advance(30.0)
    _ = await cache.current(ORG)

    cache.source.unreachable = True
    _ = clock.advance(29.0)

    assert not (await cache.current(ORG)).stale
    assert len(cache.source.calls) == 2


class _Unreadable(DimensionPolicyResolver):
    """A runtime older than the layer it is handed: its orders cannot read a value Node's can."""

    def live_fold(self, layers: Sequence[object], start: FoldedPrefix | None = None) -> FoldedPrefix:  # type: ignore[override]
        if any("budgets" in cast("dict[str, object]", layer) for layer in layers):
            raise InvalidPolicyValue("not a number")
        return super().live_fold(layers) if start is None else super().live_fold(layers, start)


async def test_cpd6_31_a_layer_the_runtime_cannot_read_is_not_applied_and_goes_stale(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    clock = ManualClock()
    source = InMemoryLiveLayerSource()
    cache = ReloadingLiveLayerCache(source, clock, resolver=_Unreadable())
    source.put(ORG, 10, live_approvals({"writes": "ask"}))
    _ = await cache.current(ORG)

    budget: JsonValue = {"layer": {"schemaVersion": 1, "scope": {"kind": "live"}, "budgets": {"maxTurns": 3}}}
    source.put(ORG, 11, live_approvals({"writes": "allow"}), budget)
    cache.on_change(change_event(ORG, 11))
    held = await cache.current(ORG)
    _ = clock.advance(30.0)
    stale = await cache.current(ORG)

    assert (held.revision, held.stale, stale.revision, stale.stale) == (10, False, 10, True)
    lines = [r.getMessage() for r in caplog.records if "live_layer.reload_failed" in r.getMessage()]
    assert len(lines) == 2
    assert all("live_layer_invalid" in line and ORG in line for line in lines)


def _document(source: InMemoryLiveLayerSource) -> LiveLayerDocument:
    return source.documents[ORG]


def test_mac_g2_model_access_rows_off_in_any_layer_stay_off_and_consumer_plans_need_every_layer() -> None:
    source = InMemoryLiveLayerSource()

    source.put(ORG, 1)
    assert folded_live_layer(_document(source)).access is None

    source.put(ORG, 2, _models(disabledRules=["r1"], consumerPlans=True), _models(disabledRules=["r2"]))
    assert folded_live_layer(_document(source)).access == OrgAccessPolicy(frozenset({"r1", "r2"}), consumer_plans=True)

    source.put(ORG, 3, _models(consumerPlans=True), _models(consumerPlans=False))
    assert folded_live_layer(_document(source)).access == OrgAccessPolicy(frozenset(), consumer_plans=False)

    source.put(ORG, 4, _models("a7", disabledRules=["r9"]))
    assert folded_live_layer(_document(source)).access is None


async def test_cpd6_41_an_event_during_a_reload_is_not_lost() -> None:
    clock = ManualClock()
    release = asyncio.Event()

    class _Slow(InMemoryLiveLayerSource):
        async def load(self, org_id: str, since: int | None) -> LiveLayerDocument | None:
            answered = await super().load(org_id, since)
            if len(self.calls) == 2:
                _ = await release.wait()
            return answered

    source = _Slow()
    cache = ReloadingLiveLayerCache(source, clock)
    source.put(ORG, 10)
    _ = await cache.current(ORG)

    cache.on_change(change_event(ORG, 11))
    source.put(ORG, 11)
    reading = asyncio.ensure_future(cache.current(ORG))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    source.put(ORG, 12, live_approvals({"writes": "deny"}))
    cache.on_change(change_event(ORG, 12))
    release.set()
    during = await reading

    assert during.revision == 11
    assert (await cache.current(ORG)).revision == 12


async def test_a_source_bug_is_not_read_as_an_outage() -> None:
    class _Broken(InMemoryLiveLayerSource):
        async def load(self, org_id: str, since: int | None) -> LiveLayerDocument | None:
            raise KeyError("bug")

    cache = ReloadingLiveLayerCache(_Broken(), ManualClock())
    with pytest.raises(KeyError):
        _ = await cache.current(ORG)
