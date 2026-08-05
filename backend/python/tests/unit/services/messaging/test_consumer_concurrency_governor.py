"""Unit tests for the ResourceGovernor-backed helpers added to
consumer_concurrency.py in Phase 1 of the adaptive-concurrency plan:
index_ceiling/parse_ceiling (resolved-ceiling lease sizing) and
acquire_parsing_slot/release_parsing_slot (tier-routed admission with a
legacy-semaphore fallback when no governor is configured).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.services.messaging import consumer_concurrency as concurrency
from app.services.messaging.config import messaging_env
from app.services.resource_governor import Pool
from app.services.resource_governor.models import ParseTier
from app.services.resource_governor.tiers import XL_HEAVY_BYTES
from tests.unit.services.messaging.governor_test_helpers import make_test_governor


def _make_governor(*, env_parse: int = 4, env_index: int = 8):
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

    def test_uses_resolved_ceiling_with_governor(self) -> None:
        governor = _make_governor(env_parse=4, env_index=8)
        host = _host(governor=governor)
        assert concurrency.index_ceiling(host) == 8
        assert concurrency.parse_ceiling(host) == 4

    def test_ceiling_unaffected_by_adaptive_shrink(self) -> None:
        """The resolved ceiling is fixed at startup; index_ceiling/
        parse_ceiling must keep returning it even after the node-local
        limit has adapted downward — only the AdmissionGate is affected by
        that, never the cluster-wide lease size."""
        governor = _make_governor(env_parse=4, env_index=8)
        governor._registry.set(Pool.INDEX, 1)
        governor._registry.set(Pool.HEAVY_PARSE, 1)
        host = _host(governor=governor)
        assert concurrency.index_ceiling(host) == 8
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
        assert concurrency.pending_task_ceiling(host) == max(8, 4) * 4

    def test_explicit_env_override_wins_over_governor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAX_PENDING_INDEXING_TASKS", "17")
        governor = _make_governor(env_parse=4, env_index=8)
        host = _host(governor=governor)
        assert concurrency.pending_task_ceiling(host) == 17

    def test_unaffected_by_adaptive_shrink(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MAX_PENDING_INDEXING_TASKS", raising=False)
        governor = _make_governor(env_parse=4, env_index=8)
        governor._registry.set(Pool.INDEX, 1)
        governor._registry.set(Pool.HEAVY_PARSE, 1)
        host = _host(governor=governor)
        assert concurrency.pending_task_ceiling(host) == max(8, 4) * 4


class TestAcquireReleaseParsingSlot:
    @pytest.mark.asyncio
    async def test_legacy_fallback_uses_semaphore_with_cost_one(self) -> None:
        semaphore = asyncio.Semaphore(1)
        host = _host(governor=None, parsing_semaphore=semaphore)

        admission = await concurrency.acquire_parsing_slot(host, ParseTier.HEAVY, 999_999_999)
        assert admission.cost == 1
        assert semaphore._value == 0

        concurrency.release_parsing_slot(admission)
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

        concurrency.release_parsing_slot(admission)
        assert governor.gate(Pool.HEAVY_PARSE).in_use == 0

    @pytest.mark.asyncio
    async def test_governor_routes_light_tier_to_light_parse_gate(self) -> None:
        governor = _make_governor()
        host = _host(governor=governor)

        admission = await concurrency.acquire_parsing_slot(host, ParseTier.LIGHT, 128)
        assert admission.cost == 1
        assert governor.gate(Pool.LIGHT_PARSE).in_use == 1
        assert governor.gate(Pool.HEAVY_PARSE).in_use == 0

        concurrency.release_parsing_slot(admission)
        assert governor.gate(Pool.LIGHT_PARSE).in_use == 0

    @pytest.mark.asyncio
    async def test_governor_defaults_missing_tier_to_heavy(self) -> None:
        governor = _make_governor()
        host = _host(governor=governor)

        admission = await concurrency.acquire_parsing_slot(host, None, None)
        assert governor.gate(Pool.HEAVY_PARSE).in_use == 1
        concurrency.release_parsing_slot(admission)

    @pytest.mark.asyncio
    async def test_governor_xl_heavy_document_costs_two_permits(self) -> None:
        governor = _make_governor()
        host = _host(governor=governor)

        admission = await concurrency.acquire_parsing_slot(
            host, ParseTier.HEAVY, XL_HEAVY_BYTES + 1
        )
        assert admission.cost == 2
        assert governor.gate(Pool.HEAVY_PARSE).in_use == 2

        concurrency.release_parsing_slot(admission)
        assert governor.gate(Pool.HEAVY_PARSE).in_use == 0

    def test_release_is_noop_for_none_admission(self) -> None:
        # Must not raise: called unconditionally from finally blocks.
        concurrency.release_parsing_slot(None)
