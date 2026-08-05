"""Unit tests for acquire_gate_with_backpressure — the shared warn-then-wait
admission helper used by both the Parsing Service and Docling Service HTTP
routes (Phase 4/5 of the adaptive-concurrency plan)."""
from __future__ import annotations

import asyncio
import logging

import pytest

from app.services.resource_governor.admission import acquire_gate_with_backpressure
from app.services.resource_governor.gate import AdmissionGate
from app.services.resource_governor.models import Limits, ParseTier, Pool
from app.services.resource_governor.registry import LimitRegistry

_LOGGER = logging.getLogger("test.admission")


def _gate(limit: int, pool: Pool = Pool.HEAVY_PARSE) -> AdmissionGate:
    registry = LimitRegistry(Limits(values={p: (limit if p == pool else 1) for p in Pool}))
    return AdmissionGate(pool, registry)


@pytest.mark.asyncio
class TestAcquireGateWithBackpressure:
    async def test_admits_immediately_when_room_available(self) -> None:
        gate = _gate(limit=2)

        admitted = await acquire_gate_with_backpressure(
            gate, 1, ParseTier.HEAVY, "msg-1", logger=_LOGGER, log_prefix="test",
        )

        assert admitted is True
        assert gate.in_use == 1

    async def test_waits_past_warn_threshold_then_admits(self) -> None:
        gate = _gate(limit=1)
        await gate.acquire(cost=1)  # saturate the only slot

        async def _release_soon() -> None:
            await asyncio.sleep(0.05)
            gate.release(cost=1)

        release_task = asyncio.ensure_future(_release_soon())
        admitted = await acquire_gate_with_backpressure(
            gate, 1, ParseTier.HEAVY, "msg-2",
            logger=_LOGGER, log_prefix="test",
            queue_wait_warn_seconds=0.01,
            gate_timeout_seconds=2.0,
        )
        await release_task

        assert admitted is True
        assert gate.in_use == 1  # the second acquire now holds it

    async def test_returns_false_after_gate_timeout_without_leaking_a_permit(self) -> None:
        gate = _gate(limit=1)
        await gate.acquire(cost=1)  # never released — saturates the gate for the whole test

        admitted = await acquire_gate_with_backpressure(
            gate, 1, ParseTier.LIGHT, "msg-3",
            logger=_LOGGER, log_prefix="test",
            queue_wait_warn_seconds=0.01,
            gate_timeout_seconds=0.05,
        )

        assert admitted is False
        assert gate.in_use == 1  # only the original acquire, no leaked permit

    async def test_admitted_within_warn_window_skips_warn_branch(self) -> None:
        """A generous warn threshold relative to how fast the slot frees means
        the caller never even logs a saturation warning — regression guard
        for the shield/timeout wiring, not just the end-to-end outcome."""
        gate = _gate(limit=1)

        admitted = await acquire_gate_with_backpressure(
            gate, 1, ParseTier.HEAVY, "msg-4",
            logger=_LOGGER, log_prefix="test",
            queue_wait_warn_seconds=5.0,
            gate_timeout_seconds=5.0,
        )

        assert admitted is True
