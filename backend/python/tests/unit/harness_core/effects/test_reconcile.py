"""RC-1..RC-11, RL-1..RL-4: ``TimedReconciler`` over the in-memory lookup (P2.9, R-31)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from harness_core.effects.memory import InMemoryReconcileLookup
from harness_core.effects.reconcile import (
    LookupAbsent,
    ReconcileEvidence,
    ReconcileTiming,
    ReconcileWriteRefused,
    TimedReconciler,
    effect_tag,
    reconciling,
)
from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.reconcile import (
    ReconcileLookupContract,
    ReconcilerContract,
    unknown_effect,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from pydantic import JsonValue

    from harness_core.effects.reconcile import LookupResult, UnknownEffect

TOOL = "fake@default/send_note"


def advancing(clock: ManualClock):  # noqa: ANN201 - a sleep matching ``asyncio.sleep``
    async def sleep(seconds: float) -> None:
        clock.advance(seconds)

    return sleep


@dataclass
class Harness:
    timing: ReconcileTiming
    clock: ManualClock = field(default_factory=ManualClock)
    tool_id: str = TOOL
    tool_name: str = "send_note"
    lookup: InMemoryReconcileLookup = field(init=False)
    reconciler: TimedReconciler = field(init=False)

    def __post_init__(self) -> None:
        self.lookup = InMemoryReconcileLookup(self.clock, self.timing)
        self.reconciler = TimedReconciler({TOOL: self.lookup}, self.clock, advancing(self.clock), retry_s=2.0)

    def arguments(self) -> Mapping[str, JsonValue]:
        return {"text": "hello"}

    async def apply(self, effect_key: str) -> None:
        self.lookup.write(effect_key, ReconcileEvidence({"posted": effect_tag(effect_key)}, {"id": "n-1"}))

    def fail_lookups(self, count: int) -> None:
        self.lookup.fail_next = count

    def lookup_times(self) -> list[datetime]:
        return list(self.lookup.lookups)

    def writes(self) -> int:
        return self.lookup.writes


READ_AFTER_WRITE = ReconcileTiming(tool_timeout_s=60.0, read_after_write=True)
SEARCH = ReconcileTiming(tool_timeout_s=60.0, read_after_write=False, index_lag_s=30.0)


@pytest.fixture(params=[READ_AFTER_WRITE, SEARCH], ids=["read_after_write", "search"])
def harness(request: pytest.FixtureRequest) -> Harness:
    return Harness(request.param)


class TestInMemoryReconciler(ReconcilerContract):
    @pytest.fixture
    def reconcile_harness(self, harness: Harness) -> Harness:
        return harness


class TestInMemoryReconcileLookup(ReconcileLookupContract):
    @pytest.fixture
    def lookup_harness(self, harness: Harness) -> Harness:
        return harness


async def test_rc3_a_search_sees_a_write_only_after_its_index_lag() -> None:
    h = Harness(SEARCH)
    effect = unknown_effect(h)
    h.clock.advance(SEARCH.tool_timeout_s + 30.0 - 10.0)
    await h.apply(effect.effect_key)
    verdict = await h.reconciler.reconcile(effect)
    # First lookup at the window's end sees nothing (written 10 s before, lag 30 s); the second sees it.
    assert verdict.answer == "applied" and verdict.lookups == 2


async def test_rc7_a_hanging_lookup_is_bounded_and_counts_as_a_failure() -> None:
    clock = ManualClock()

    class Hanging:
        timing = READ_AFTER_WRITE
        calls = 0

        async def lookup(self, effect: UnknownEffect) -> LookupResult:
            self.calls += 1
            await asyncio.Event().wait()
            return LookupAbsent()

    hook = Hanging()
    reconciler = TimedReconciler(
        {TOOL: hook}, clock, advancing(clock), max_lookups=2, lookup_timeout_s=0.01, retry_s=0.0
    )
    effect = unknown_effect(Harness(READ_AFTER_WRITE, clock=clock))
    verdict = await reconciler.reconcile(effect)
    assert (verdict.answer, verdict.reason, hook.calls) == ("still_unknown", "lookup_failed", 2)


async def test_rc9_an_undecidable_call_is_still_unknown() -> None:
    h = Harness(READ_AFTER_WRITE)
    h.lookup.undecidable = frozenset({"text"})
    verdict = await h.reconciler.reconcile(unknown_effect(h))
    assert (verdict.answer, verdict.reason) == ("still_unknown", "undecidable")


async def test_rc10_a_lookup_that_writes_is_refused_and_still_unknown() -> None:
    h = Harness(READ_AFTER_WRITE)

    class Writing:
        timing = READ_AFTER_WRITE

        async def lookup(self, effect: UnknownEffect) -> LookupResult:
            h.lookup.write(effect.effect_key, ReconcileEvidence("probe"))
            return LookupAbsent()

    reconciler = TimedReconciler({TOOL: Writing()}, h.clock, advancing(h.clock))
    verdict = await reconciler.reconcile(unknown_effect(h))
    assert (verdict.answer, verdict.reason) == ("still_unknown", "write_refused")
    assert h.lookup.writes == 0


def test_rc10_writes_outside_reconciling_are_allowed() -> None:
    h = Harness(READ_AFTER_WRITE)
    h.lookup.write("k", ReconcileEvidence("x"))
    with reconciling(), pytest.raises(ReconcileWriteRefused):
        h.lookup.write("k2", ReconcileEvidence("y"))
    assert h.lookup.writes == 1


def test_rc11_the_effect_tag_is_stable_and_opaque() -> None:
    tag = effect_tag("org-1:run-1:t1.0")
    assert tag == effect_tag("org-1:run-1:t1.0")
    assert tag.startswith("pxh-e-") and len(tag) == len("pxh-e-") + 24
    assert all(c in "0123456789abcdef" for c in tag.removeprefix("pxh-e-"))
    assert tag != effect_tag("org-1:run-1:t1.1")
    assert "org-1" not in tag and "run-1" not in tag
