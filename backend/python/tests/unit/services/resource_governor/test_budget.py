from __future__ import annotations

import asyncio

import pytest

import app.services.resource_governor.budget as budget_mod
from app.services.resource_governor.budget import (
    CONTENT_LENGTH_RESERVE_MULTIPLIER,
    DEFAULT_RESERVE_BYTES,
    GatedBytesBudget,
    estimate_reservation,
)
from app.services.resource_governor.gate import AdmissionGate
from app.services.resource_governor.models import Limits, Pool
from app.services.resource_governor.registry import LimitRegistry


def _registry(limit: int) -> LimitRegistry:
    return LimitRegistry(Limits(values={p: (limit if p == Pool.DOWNLOAD_BYTES else 1) for p in Pool}))


class TestEstimateReservation:
    def test_unknown_content_length_uses_default(self) -> None:
        assert estimate_reservation(None) == DEFAULT_RESERVE_BYTES
        assert estimate_reservation(0) == DEFAULT_RESERVE_BYTES
        assert estimate_reservation(-1) == DEFAULT_RESERVE_BYTES

    def test_small_content_length_still_clamped_to_default(self) -> None:
        # A tiny file must not reserve less than the default floor.
        assert estimate_reservation(1024) == DEFAULT_RESERVE_BYTES

    def test_large_content_length_scales_by_multiplier(self) -> None:
        content_length = 100 * 1024 * 1024
        expected = int(content_length * CONTENT_LENGTH_RESERVE_MULTIPLIER)
        assert estimate_reservation(content_length) == expected


@pytest.mark.asyncio
class TestGatedBytesBudgetNoGate:
    """No governor configured — every method must be a safe no-op."""

    async def test_reserve_ensure_release_are_noops(self) -> None:
        budget = GatedBytesBudget(None)
        await budget.reserve(1_000_000)
        await budget.ensure(50_000_000)
        assert budget.reserved_bytes == 0
        budget.release()  # must not raise


@pytest.mark.asyncio
class TestGatedBytesBudgetWithGate:
    async def test_reserve_acquires_estimated_cost(self) -> None:
        registry = _registry(limit=1024 * 1024 * 1024)
        gate = AdmissionGate(Pool.DOWNLOAD_BYTES, registry)
        budget = GatedBytesBudget(gate)

        content_length = 10 * 1024 * 1024
        await budget.reserve(content_length)

        expected = estimate_reservation(content_length)
        assert budget.reserved_bytes == expected
        assert gate.in_use == expected

    async def test_release_gives_back_exactly_what_was_reserved(self) -> None:
        registry = _registry(limit=1024 * 1024 * 1024)
        gate = AdmissionGate(Pool.DOWNLOAD_BYTES, registry)
        budget = GatedBytesBudget(gate)

        await budget.reserve(10 * 1024 * 1024)
        budget.release()

        assert budget.reserved_bytes == 0
        assert gate.in_use == 0

    async def test_release_without_reserve_is_noop(self) -> None:
        registry = _registry(limit=1024)
        gate = AdmissionGate(Pool.DOWNLOAD_BYTES, registry)
        budget = GatedBytesBudget(gate)

        budget.release()
        assert gate.in_use == 0

    async def test_ensure_tops_up_once_actual_bytes_approach_estimate(self) -> None:
        registry = _registry(limit=1024 * 1024 * 1024)
        gate = AdmissionGate(Pool.DOWNLOAD_BYTES, registry)
        budget = GatedBytesBudget(gate)

        # No Content-Length -> reserve the conservative default.
        await budget.reserve(None)
        assert budget.reserved_bytes == DEFAULT_RESERVE_BYTES

        # Actual bytes are still well under 90% of the reservation -> no top-up.
        await budget.ensure(DEFAULT_RESERVE_BYTES // 2)
        assert budget.reserved_bytes == DEFAULT_RESERVE_BYTES

        # Actual bytes now exceed the reservation -> grow to the new estimate.
        grown_total = DEFAULT_RESERVE_BYTES * 3
        await budget.ensure(grown_total)
        expected = estimate_reservation(grown_total)
        assert budget.reserved_bytes == expected
        assert gate.in_use == expected

    async def test_ensure_never_double_counts_across_multiple_top_ups(self) -> None:
        registry = _registry(limit=1024 * 1024 * 1024)
        gate = AdmissionGate(Pool.DOWNLOAD_BYTES, registry)
        budget = GatedBytesBudget(gate)

        await budget.reserve(None)
        await budget.ensure(DEFAULT_RESERVE_BYTES * 2)
        await budget.ensure(DEFAULT_RESERVE_BYTES * 4)
        await budget.ensure(DEFAULT_RESERVE_BYTES * 4)  # repeat: no further growth

        assert budget.reserved_bytes == gate.in_use
        assert budget.reserved_bytes == estimate_reservation(DEFAULT_RESERVE_BYTES * 4)

    async def test_oversized_single_reservation_admitted_alone(self) -> None:
        """A request bigger than the whole budget must still be admitted when
        nothing else is in flight, so it can't deadlock forever (plan
        section 8, 'Reservation larger than the whole bytes budget')."""
        small_limit = 1024
        registry = _registry(limit=small_limit)
        gate = AdmissionGate(Pool.DOWNLOAD_BYTES, registry)
        budget = GatedBytesBudget(gate)

        huge_content_length = 500 * 1024 * 1024
        await budget.reserve(huge_content_length)

        assert budget.reserved_bytes == estimate_reservation(huge_content_length)
        assert gate.in_use == budget.reserved_bytes

    async def test_second_reservation_blocks_until_first_releases(self) -> None:
        registry = _registry(limit=DEFAULT_RESERVE_BYTES)
        gate = AdmissionGate(Pool.DOWNLOAD_BYTES, registry)
        first = GatedBytesBudget(gate)
        second = GatedBytesBudget(gate)

        await first.reserve(None)  # consumes the whole budget

        second_reserved = False

        async def _reserve_second() -> None:
            nonlocal second_reserved
            await second.reserve(None)
            second_reserved = True

        task = asyncio.create_task(_reserve_second())
        try:
            await asyncio.sleep(0.05)
            assert second_reserved is False

            first.release()
            await asyncio.wait_for(task, timeout=1.0)
            assert second_reserved is True
        finally:
            if not task.done():
                task.cancel()

    async def test_reserve_raises_timeout_error_instead_of_hanging_forever(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A leaked DOWNLOAD_BYTES reservation elsewhere must surface as a
        retryable failure here, not an indefinite hang with no error and no
        log — see acquire_gate_with_backpressure, which every other governor
        call site uses for the same reason."""
        monkeypatch.setattr(budget_mod, "DEFAULT_GATE_TIMEOUT_SECONDS", 0.05)
        registry = _registry(limit=DEFAULT_RESERVE_BYTES)
        gate = AdmissionGate(Pool.DOWNLOAD_BYTES, registry)
        holder = GatedBytesBudget(gate)
        waiter = GatedBytesBudget(gate)

        await holder.reserve(None)  # consumes the whole budget, never released

        with pytest.raises(TimeoutError):
            await waiter.reserve(None)

        # A denied acquire never incremented in_use — nothing was reserved
        # to leak, and the holder's reservation is untouched.
        assert waiter.reserved_bytes == 0
        assert gate.in_use == holder.reserved_bytes

    async def test_ensure_raises_timeout_error_instead_of_hanging_forever(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Room for exactly two default-sized reservations, so the second
        # one (holder's) saturates the gate and leaves nothing free for
        # holder's later top-up to acquire.
        registry = _registry(limit=DEFAULT_RESERVE_BYTES * 2)
        gate = AdmissionGate(Pool.DOWNLOAD_BYTES, registry)
        holder = GatedBytesBudget(gate)
        other = GatedBytesBudget(gate)

        await holder.reserve(None)
        await other.reserve(None)  # never released — saturates the gate

        monkeypatch.setattr(budget_mod, "DEFAULT_GATE_TIMEOUT_SECONDS", 0.05)
        with pytest.raises(TimeoutError):
            await holder.ensure(DEFAULT_RESERVE_BYTES * 3)

        # holder's own top-up was denied, so its reservation must stay at
        # what it already held — not partially bumped, not lost.
        assert holder.reserved_bytes == DEFAULT_RESERVE_BYTES
