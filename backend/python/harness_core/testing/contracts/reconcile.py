"""Contract suites for ``Reconciler`` and ``ReconcileLookup`` adapters (P2.9, R-31).

Subclass ``ReconcilerContract`` with a ``reconcile_harness`` fixture, or
``ReconcileLookupContract`` with a ``lookup_harness`` fixture. A harness
drives a provider fake that refuses what the provider refuses, a
``ManualClock`` whose ``sleep`` advances it, and the write the tool makes
(stamped with the effect's tag) so a lookup has something to find.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Protocol

import pytest

from harness_core.effects.reconcile import (
    LookupAbsent,
    LookupFound,
    UnknownEffect,
    reconciling,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from pydantic import JsonValue

    from harness_core.effects.reconcile import (
        ReconcileLookup,
        Reconciler,
        ReconcileTiming,
    )
    from harness_core.testing.clock import ManualClock

LEASE_S = 30.0


class ProviderHarness(Protocol):
    clock: ManualClock
    tool_id: str
    tool_name: str

    @property
    def timing(self) -> ReconcileTiming: ...

    def arguments(self) -> Mapping[str, JsonValue]:
        """Arguments of a call the hook can decide."""
        ...

    async def apply(self, effect_key: str) -> None:
        """Make the write the tool makes for ``effect_key`` (stamped with its tag)."""
        ...

    def fail_lookups(self, count: int) -> None: ...

    def lookup_times(self) -> list[datetime]: ...

    def writes(self) -> int:
        """Writes the provider fake accepted so far."""
        ...


class ReconcileHarness(ProviderHarness, Protocol):
    reconciler: Reconciler


class LookupHarness(ProviderHarness, Protocol):
    lookup: ReconcileLookup


def unknown_effect(
    harness: ProviderHarness, key: str = "org-1:run-1:t1.0", *, dispatched: datetime | None = None
) -> UnknownEffect:
    return UnknownEffect(
        run_id="run-1",
        invocation_id=key.rsplit(":", 1)[-1],
        tool_id=harness.tool_id,
        tool_name=harness.tool_name,
        arguments=harness.arguments(),
        effect_key=key,
        dispatched_at=dispatched or harness.clock.now(),
        lease_s=LEASE_S,
    )


def _ready(harness: ProviderHarness, effect: UnknownEffect) -> datetime:
    return effect.dispatched_at + timedelta(seconds=harness.timing.tool_timeout_s + effect.lease_s)


class ReconcilerContract:
    @pytest.fixture
    def reconcile_harness(self) -> ReconcileHarness:
        raise NotImplementedError("provide a reconcile_harness fixture")

    async def test_rc1_a_present_effect_is_applied_with_evidence(
        self, reconcile_harness: ReconcileHarness
    ) -> None:
        h = reconcile_harness
        effect = unknown_effect(h)
        await h.apply(effect.effect_key)
        verdict = await h.reconciler.reconcile(effect)
        assert verdict.answer == "applied" and verdict.reason == "found"
        assert verdict.evidence is not None and verdict.lookups >= 1

    async def test_rc2_rc3_an_absent_effect_is_not_applied_by_the_read_rule(
        self, reconcile_harness: ReconcileHarness
    ) -> None:
        h = reconcile_harness
        effect = unknown_effect(h)
        verdict = await h.reconciler.reconcile(effect)
        assert verdict.answer == "not_applied" and verdict.evidence is None
        times = h.lookup_times()
        if h.timing.read_after_write:
            assert len(times) == 1
        else:
            assert len(times) == 2
            assert times[1] - times[0] >= timedelta(seconds=h.timing.index_lag_s)

    async def test_rc4_no_lookup_before_the_window(self, reconcile_harness: ReconcileHarness) -> None:
        h = reconcile_harness
        effect = unknown_effect(h)
        _ = await h.reconciler.reconcile(effect)
        assert h.lookup_times() and min(h.lookup_times()) >= _ready(h, effect)

    async def test_rc4_a_window_already_past_is_not_waited_again(
        self, reconcile_harness: ReconcileHarness
    ) -> None:
        h = reconcile_harness
        effect = unknown_effect(h)
        h.clock.advance(h.timing.tool_timeout_s + LEASE_S + 1)
        before = h.clock.now()
        _ = await h.reconciler.reconcile(effect)
        assert min(h.lookup_times()) == before

    async def test_rc5_a_window_beyond_the_hold_is_still_unknown_without_a_lookup(
        self, reconcile_harness: ReconcileHarness
    ) -> None:
        h = reconcile_harness
        effect = unknown_effect(h, dispatched=h.clock.now() + timedelta(days=1))
        verdict = await h.reconciler.reconcile(effect)
        assert (verdict.answer, verdict.reason, verdict.lookups) == (
            "still_unknown",
            "window_beyond_hold",
            0,
        )
        assert h.lookup_times() == []

    async def test_rc6_failed_lookups_are_bounded_and_never_a_negative(
        self, reconcile_harness: ReconcileHarness
    ) -> None:
        h = reconcile_harness
        h.fail_lookups(100)
        verdict = await h.reconciler.reconcile(unknown_effect(h))
        assert (verdict.answer, verdict.reason) == ("still_unknown", "lookup_failed")
        assert 1 <= verdict.lookups <= 5

    async def test_rc6_a_failure_then_a_negative_decides_on_the_read_rule(
        self, reconcile_harness: ReconcileHarness
    ) -> None:
        h = reconcile_harness
        h.fail_lookups(1)
        verdict = await h.reconciler.reconcile(unknown_effect(h))
        assert verdict.answer == "not_applied"

    async def test_rc8_a_tool_without_a_hook_is_not_covered(
        self, reconcile_harness: ReconcileHarness
    ) -> None:
        h = reconcile_harness
        assert h.reconciler.covers(h.tool_id)
        assert not h.reconciler.covers("local@default/not_hooked")
        effect = unknown_effect(h)
        other = UnknownEffect(
            effect.run_id,
            effect.invocation_id,
            "local@default/not_hooked",
            "not_hooked",
            effect.arguments,
            effect.effect_key,
            effect.dispatched_at,
            effect.lease_s,
        )
        verdict = await h.reconciler.reconcile(other)
        assert (verdict.answer, verdict.reason) == ("still_unknown", "no_hook")

    async def test_rc10_reconciling_never_writes(self, reconcile_harness: ReconcileHarness) -> None:
        h = reconcile_harness
        effect = unknown_effect(h)
        await h.apply(effect.effect_key)
        writes = h.writes()
        _ = await h.reconciler.reconcile(effect)
        _ = await h.reconciler.reconcile(unknown_effect(h, "org-1:run-1:t9.9"))
        assert h.writes() == writes


class ReconcileLookupContract:
    @pytest.fixture
    def lookup_harness(self) -> LookupHarness:
        raise NotImplementedError("provide a lookup_harness fixture")

    async def _settled(self, h: LookupHarness) -> None:
        h.clock.advance(h.timing.index_lag_s + 1)

    async def test_rl1_finds_the_effect_its_tag_marks(self, lookup_harness: LookupHarness) -> None:
        h = lookup_harness
        effect = unknown_effect(h)
        await h.apply(effect.effect_key)
        await self._settled(h)
        with reconciling():
            result = await h.lookup.lookup(effect)
        assert isinstance(result, LookupFound)

    async def test_rl2_absent_when_never_written(self, lookup_harness: LookupHarness) -> None:
        h = lookup_harness
        with reconciling():
            result = await h.lookup.lookup(unknown_effect(h))
        assert isinstance(result, LookupAbsent)

    async def test_rl3_another_keys_effect_is_not_found(self, lookup_harness: LookupHarness) -> None:
        h = lookup_harness
        await h.apply("org-1:run-1:t1.1")
        await self._settled(h)
        with reconciling():
            result = await h.lookup.lookup(unknown_effect(h, "org-1:run-1:t1.0"))
        assert isinstance(result, LookupAbsent)

    async def test_rl4_a_lookup_is_read_only(self, lookup_harness: LookupHarness) -> None:
        h = lookup_harness
        effect = unknown_effect(h)
        await h.apply(effect.effect_key)
        await self._settled(h)
        writes = h.writes()
        with reconciling():
            _ = await h.lookup.lookup(effect)
            _ = await h.lookup.lookup(unknown_effect(h, "org-1:run-1:t2.0"))
        assert h.writes() == writes


__all__ = [
    "LEASE_S",
    "LookupHarness",
    "ProviderHarness",
    "ReconcileHarness",
    "ReconcileLookupContract",
    "ReconcilerContract",
    "unknown_effect",
]
