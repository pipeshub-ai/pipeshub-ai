"""Unit tests for the ResourceGovernor-backed helpers added to
consumer_concurrency.py in Phase 1 of the adaptive-concurrency plan:
index_ceiling/parse_ceiling (resolved-ceiling lease sizing) and
acquire_parsing_slot/release_admission (tier-routed admission with a
legacy-semaphore fallback when no governor is configured).
"""
from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services.messaging import consumer_concurrency as concurrency
from app.services.messaging.config import messaging_env
from app.services.resource_governor import Pool
from app.services.resource_governor.models import ParseTier
from app.services.resource_governor.tiers import XL_HEAVY_BYTES
from tests.unit.services.messaging.governor_test_helpers import make_test_governor


def _make_governor(*, env_parse: int | None = None, env_index: int = 8):
    return make_test_governor(
        env_parse=env_parse, env_index=env_index, logger_name="test.consumer_concurrency.governor",
    )


def _host(*, governor=None, parsing_semaphore=None) -> SimpleNamespace:
    """Minimal stand-in satisfying the ConcurrencyHost protocol surface
    these helpers actually read."""
    return SimpleNamespace(governor=governor, parsing_semaphore=parsing_semaphore)


class TestIndexAndParseCeiling:
    def test_falls_back_to_env_var_without_governor(self) -> None:
        host = _host(governor=None)
        assert concurrency.index_ceiling(host) == messaging_env.max_concurrent_indexing
        assert concurrency.parse_ceiling(host) == messaging_env.max_concurrent_parsing

    def test_uses_resolved_ceiling_with_governor(self, monkeypatch) -> None:
        monkeypatch.setenv("INDEXING_SPLIT_LEASE_POOLS", "true")
        governor = _make_governor(env_parse=4, env_index=8)
        host = _host(governor=governor)
        # env_index caps the two tiers together, so each gets a share.
        assert (
            concurrency.index_ceiling(host, ParseTier.HEAVY)
            + concurrency.index_ceiling(host, ParseTier.LIGHT)
        ) <= 8
        assert concurrency.parse_ceiling(host) == 4
        assert concurrency.parse_ceiling(host, ParseTier.HEAVY) == 4
        assert concurrency.parse_ceiling(host, ParseTier.LIGHT) == governor.ceilings.light

    def test_light_parse_lease_is_split_from_heavy(self) -> None:
        assert concurrency.parse_lease_pool(ParseTier.HEAVY) == "parsing"
        assert concurrency.parse_lease_pool(None) == "parsing"
        assert concurrency.parse_lease_pool(ParseTier.LIGHT) == "parsing:light"

    def test_light_parse_ceiling_is_not_the_heavy_ceiling(self) -> None:
        """The bug this guards: parse_ceiling used to return ceilings.heavy
        for every tier, so a cluster-wide Redis lease of 4 (Docling-sized)
        serialized Jira/Slack parses and the local LIGHT_PARSE gate never
        saw demand."""
        # A total wide enough that light keeps more than its reserve: an
        # explicit 8 on this 4-CPU probe splits 4/4, which is a legitimate
        # cap but says nothing about the tier routing under test.
        governor = _make_governor(env_index=24)
        host = _host(governor=governor)
        assert concurrency.parse_ceiling(host, ParseTier.LIGHT) > concurrency.parse_ceiling(host, ParseTier.HEAVY)
        assert concurrency.parse_ceiling(host, ParseTier.LIGHT) == governor.ceilings.light

    def test_explicit_max_concurrent_parsing_caps_both_tiers(self) -> None:
        """MAX_CONCURRENT_PARSING is a cap on parsing, not on heavy parsing:
        an operator pinning it to 2 wants two parses in flight, not two
        Docling conversions plus a burst of light ones."""
        governor = _make_governor(env_parse=2)
        host = _host(governor=governor)
        assert concurrency.parse_ceiling(host, ParseTier.HEAVY) == 2
        assert concurrency.parse_ceiling(host, ParseTier.LIGHT) == 2

    def test_ceiling_unaffected_by_adaptive_shrink(self, monkeypatch) -> None:
        """The resolved ceiling is fixed at startup; index_ceiling/
        parse_ceiling must keep returning it even after the node-local
        limit has adapted downward — only the AdmissionGate is affected by
        that, never the cluster-wide lease size."""
        monkeypatch.setenv("INDEXING_SPLIT_LEASE_POOLS", "true")
        governor = _make_governor(env_parse=4, env_index=8)
        governor._registry.set(Pool.INDEX_HEAVY, 1)
        governor._registry.set(Pool.INDEX_LIGHT, 1)
        governor._registry.set(Pool.HEAVY_PARSE, 1)
        host = _host(governor=governor)
        # env_index caps the two tiers together, so each gets a share.
        assert (
            concurrency.index_ceiling(host, ParseTier.HEAVY)
            + concurrency.index_ceiling(host, ParseTier.LIGHT)
        ) <= 8
        assert concurrency.parse_ceiling(host) == 4


class TestPendingTaskCeiling:
    """Phase 6: the in-flight-task cap derives from the resolved governor
    ceilings (when present) instead of the static MAX_CONCURRENT_* env
    defaults, unless the operator pinned MAX_PENDING_INDEXING_TASKS
    explicitly."""

    def test_falls_back_to_env_var_without_governor(self) -> None:
        host = _host(governor=None)
        assert concurrency.pending_task_ceiling(host) == messaging_env.max_pending_indexing_tasks

    def test_derives_from_resolved_ceilings_with_governor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MAX_PENDING_INDEXING_TASKS", raising=False)
        governor = _make_governor(env_parse=4, env_index=8)
        host = _host(governor=governor)
        # Derived from the resolved total index budget, then clamped: the
        # read-ahead floor keeps a small host from starving its own loop.
        assert concurrency.pending_task_ceiling(host) == 64

    def test_explicit_env_override_wins_over_governor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAX_PENDING_INDEXING_TASKS", "17")
        governor = _make_governor(env_parse=4, env_index=8)
        host = _host(governor=governor)
        assert concurrency.pending_task_ceiling(host) == 17

    def test_pending_ceiling_follows_adaptive_limits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The read-ahead cap tracks the limits the gates admit against, so a
        governor that has shrunk its index pools also stops the consumer
        pulling records ahead of them. (The distributed lease is the one that
        stays sized to the fixed ceiling — see TestIndexAndParseCeiling.)"""
        monkeypatch.delenv("MAX_PENDING_INDEXING_TASKS", raising=False)
        governor = _make_governor(env_parse=None, env_index=200)
        host = _host(governor=governor)
        governor._registry.set(Pool.INDEX_HEAVY, 40)
        governor._registry.set(Pool.INDEX_LIGHT, 20)
        assert concurrency.pending_task_ceiling(host) == 120

        governor._registry.set(Pool.INDEX_HEAVY, 1)
        governor._registry.set(Pool.INDEX_LIGHT, 1)
        # Shrunk to the floor: the cap is clamped there rather than tracking
        # the pools all the way down, so a small host can still fill its loop.
        assert concurrency.pending_task_ceiling(host) == 64


def _waiter_host() -> SimpleNamespace:
    return SimpleNamespace(gate_waiters=concurrency.GateWaiters())


class TestGateWaiterToken:
    """A task counts toward pending_task_ceiling from spawn until it is
    admitted through the local indexing gate/semaphore — not for its whole
    in-flight lifetime."""

    def test_counts_from_construction_until_admit(self) -> None:
        host = _waiter_host()
        token = concurrency.GateWaiterToken(host, ParseTier.LIGHT)
        assert concurrency.get_gate_waiter_count(host) == 1
        token.admit()
        assert concurrency.get_gate_waiter_count(host) == 0

    def test_release_after_admit_is_a_noop(self) -> None:
        host = _waiter_host()
        token = concurrency.GateWaiterToken(host, ParseTier.LIGHT)
        token.admit()
        token.release()
        assert concurrency.get_gate_waiter_count(host) == 0

    def test_release_without_admit_decrements_once(self) -> None:
        """A task that errors out (or is cancelled) before ever acquiring
        the gate must still stop counting as a waiter, and calling
        release() more than once must not double-decrement."""
        host = _waiter_host()
        token = concurrency.GateWaiterToken(host, ParseTier.HEAVY)
        token.release()
        token.release()
        assert concurrency.get_gate_waiter_count(host) == 0

    def test_admitted_tasks_dont_block_new_waiters_at_the_ceiling(self) -> None:
        """N admitted (in-progress) tasks plus N-1 waiters must still be
        under a ceiling of N; the Nth waiter is what engages backpressure."""
        host = _waiter_host()
        ceiling = 3

        admitted_tokens = [
            concurrency.GateWaiterToken(host, ParseTier.HEAVY) for _ in range(ceiling)
        ]
        for token in admitted_tokens:
            token.admit()

        waiting_tokens = [
            concurrency.GateWaiterToken(host, ParseTier.HEAVY) for _ in range(ceiling - 1)
        ]
        assert concurrency.get_gate_waiter_count(host) < ceiling

        extra_waiter = concurrency.GateWaiterToken(host, ParseTier.HEAVY)
        assert concurrency.get_gate_waiter_count(host) >= ceiling

        for token in waiting_tokens:
            token.release()
        extra_waiter.release()
        for token in admitted_tokens:
            token.release()
        assert concurrency.get_gate_waiter_count(host) == 0


class TestAcquireReleaseParsingSlot:
    @pytest.mark.asyncio
    async def test_legacy_fallback_uses_semaphore_with_cost_one(self) -> None:
        semaphore = asyncio.Semaphore(1)
        host = _host(governor=None, parsing_semaphore=semaphore)

        admission = await concurrency.acquire_parsing_slot(host, ParseTier.HEAVY, 999_999_999)
        assert admission.cost == 1
        assert semaphore._value == 0

        concurrency.release_admission(admission)
        assert semaphore._value == 1

    @pytest.mark.asyncio
    async def test_legacy_fallback_raises_without_semaphore(self) -> None:
        host = _host(governor=None, parsing_semaphore=None)
        with pytest.raises(RuntimeError, match="parsing concurrency"):
            await concurrency.acquire_parsing_slot(host, None, None)

    @pytest.mark.asyncio
    async def test_governor_routes_heavy_tier_to_heavy_parse_gate(self) -> None:
        governor = _make_governor()
        host = _host(governor=governor)

        admission = await concurrency.acquire_parsing_slot(host, ParseTier.HEAVY, 1024)
        assert admission.cost == 1
        assert governor.gate(Pool.HEAVY_PARSE).in_use == 1
        assert governor.gate(Pool.LIGHT_PARSE).in_use == 0

        concurrency.release_admission(admission)
        assert governor.gate(Pool.HEAVY_PARSE).in_use == 0

    @pytest.mark.asyncio
    async def test_governor_routes_light_tier_to_light_parse_gate(self) -> None:
        governor = _make_governor()
        host = _host(governor=governor)

        admission = await concurrency.acquire_parsing_slot(host, ParseTier.LIGHT, 128)
        assert admission.cost == 1
        assert governor.gate(Pool.LIGHT_PARSE).in_use == 1
        assert governor.gate(Pool.HEAVY_PARSE).in_use == 0

        concurrency.release_admission(admission)
        assert governor.gate(Pool.LIGHT_PARSE).in_use == 0

    @pytest.mark.asyncio
    async def test_governor_defaults_missing_tier_to_heavy(self) -> None:
        governor = _make_governor()
        host = _host(governor=governor)

        admission = await concurrency.acquire_parsing_slot(host, None, None)
        assert governor.gate(Pool.HEAVY_PARSE).in_use == 1
        concurrency.release_admission(admission)

    @pytest.mark.asyncio
    async def test_governor_xl_heavy_document_costs_two_permits(self) -> None:
        governor = _make_governor()
        host = _host(governor=governor)

        admission = await concurrency.acquire_parsing_slot(
            host, ParseTier.HEAVY, XL_HEAVY_BYTES + 1
        )
        assert admission.cost == 2
        assert governor.gate(Pool.HEAVY_PARSE).in_use == 2

        concurrency.release_admission(admission)
        assert governor.gate(Pool.HEAVY_PARSE).in_use == 0

    def test_release_is_noop_for_none_admission(self) -> None:
        # Must not raise: called unconditionally from finally blocks.
        concurrency.release_admission(None)


class TestReportMemoryIncidentIfApplicable:
    def test_memory_error_with_governor_triggers_incident(self) -> None:
        governor = MagicMock()
        host = _host(governor=governor)

        concurrency.report_memory_incident_if_applicable(host, "msg-1", MemoryError("oom"))

        governor.report_memory_incident.assert_called_once()
        assert "msg-1" in governor.report_memory_incident.call_args.args[0]

    def test_non_memory_error_does_not_trigger_incident(self) -> None:
        governor = MagicMock()
        host = _host(governor=governor)

        concurrency.report_memory_incident_if_applicable(host, "msg-1", ValueError("boom"))

        governor.report_memory_incident.assert_not_called()

    def test_memory_error_without_governor_is_a_noop(self) -> None:
        host = _host(governor=None)

        # Must not raise: called unconditionally from the outer except block.
        concurrency.report_memory_incident_if_applicable(host, "msg-1", MemoryError("oom"))


class TestParseAdmissionWait:
    """Queue time is bounded and reported as its own outcome, never as a
    processing failure."""

    @pytest.mark.asyncio
    async def test_a_full_gate_raises_parse_admission_timeout(self) -> None:
        governor = _make_governor()
        host = _host(governor=governor)
        gate = governor.gate(Pool.LIGHT_PARSE)
        for _ in range(gate.limit):
            assert await gate.acquire()
        with pytest.raises(concurrency.ParseAdmissionTimeout) as info:
            await concurrency.acquire_parsing_slot(host, ParseTier.LIGHT, 10, timeout=0.02)
        assert info.value.pool == Pool.LIGHT_PARSE.value

    @pytest.mark.asyncio
    async def test_the_legacy_semaphore_path_times_out_the_same_way(self) -> None:
        host = _host(governor=None, parsing_semaphore=asyncio.Semaphore(0))
        with pytest.raises(concurrency.ParseAdmissionTimeout):
            await concurrency.acquire_parsing_slot(host, None, None, timeout=0.02)

    @pytest.mark.asyncio
    async def test_the_record_clock_is_paused_while_waiting(self) -> None:
        host = _host(governor=None)
        host.logger = MagicMock()
        async with asyncio.timeout(0.5) as budget:
            before = budget.when()
            async with concurrency.parse_admission_wait(host, budget, "m1") as wait:
                assert budget.when() is None, "the clock is off while queued"
                assert wait.remaining() > 0
                await asyncio.sleep(0.1)
            after = budget.when()
        assert after is not None and before is not None
        assert after - before == pytest.approx(0.1, abs=0.05)

    @pytest.mark.asyncio
    async def test_a_long_wait_is_logged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        host = _host(governor=None)
        host.logger = MagicMock()
        monkeypatch.setattr(concurrency, "_PARSE_WAIT_LOG_SECONDS", 0.0)
        async with concurrency.parse_admission_wait(host, None, "m1"):
            await asyncio.sleep(0.01)
        host.logger.info.assert_called_once()
        assert "waited" in host.logger.info.call_args.args[0]
