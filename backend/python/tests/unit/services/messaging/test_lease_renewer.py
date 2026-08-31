from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock

import pytest

from app.services.messaging.distributed_concurrency import DistributedLeaseSet
from app.services.messaging.lease import LeaseRenewer

LEASE_SECONDS = 1.2
INTERVAL = 0.02


class _Clock:
    """Injected monotonic clock, so the safety deadline can be crossed without
    the test actually waiting a lease out."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def logger() -> logging.Logger:
    return logging.getLogger("test_lease_renewer")


def _manager(**kwargs: object) -> AsyncMock:
    manager = AsyncMock()
    manager.renew_many = AsyncMock(**kwargs)
    return manager


def _renewer(logger: logging.Logger, manager: AsyncMock, clock: _Clock) -> LeaseRenewer:
    return LeaseRenewer(
        logger,
        manager,
        lease_seconds=LEASE_SECONDS,
        interval_seconds=INTERVAL,
        clock=clock,
    )


async def _settle(renewer: LeaseRenewer, rounds: int = 1) -> None:
    """Let the renewer complete roughly ``rounds`` renew cycles."""
    for _ in range(rounds * 6):
        await asyncio.sleep(INTERVAL)


class TestLeaseRenewer:
    @pytest.mark.asyncio
    async def test_one_task_and_one_round_trip_for_every_owner(self, logger) -> None:
        """The whole point: N in-flight records used to mean N renewal tasks
        each issuing their own Redis call, so load on Redis scaled with how
        busy the node was."""
        manager = _manager(return_value={})
        renewer = _renewer(logger, manager, _Clock())
        for i in range(50):
            renewer.add(f"owner-{i}", "indexing")

        renewer.start()
        try:
            await _settle(renewer)
        finally:
            await renewer.stop()

        assert manager.renew_many.await_count >= 1
        # Every owner rides in one call, not one call each.
        first_batch = manager.renew_many.await_args_list[0].args[0]
        assert len(first_batch) == 50

    @pytest.mark.asyncio
    async def test_lost_lease_marks_only_its_own_owner(self, logger) -> None:
        """A lease lost by one record must not abort every other in-flight
        record — the old per-message task raised into its own record only, and
        collapsing to one shared task must not lose that isolation."""
        manager = _manager(
            return_value={("indexing", "a"): False, ("indexing", "b"): True}
        )
        renewer = _renewer(logger, manager, _Clock())
        handle_a = renewer.register("a")
        handle_b = renewer.register("b")
        renewer.add("a", "indexing")
        renewer.add("b", "indexing")

        renewer.start()
        try:
            await asyncio.wait_for(handle_a.lost.wait(), timeout=2)
        finally:
            await renewer.stop()

        assert handle_a.lost.is_set()
        assert not handle_b.lost.is_set()
        assert "indexing" in (handle_a.reason or "")

    @pytest.mark.asyncio
    async def test_a_released_lease_is_not_reported_lost(self, logger) -> None:
        """A record that released its lease between the batch being built and
        the reply arriving has not lost anything, and must not be aborted."""
        manager = _manager(return_value={("indexing", "a"): False})
        renewer = _renewer(logger, manager, _Clock())
        handle = renewer.register("a")
        renewer.add("a", "indexing")
        renewer.discard("a", "indexing")

        renewer.start()
        try:
            await _settle(renewer)
        finally:
            await renewer.stop()

        assert not handle.lost.is_set()

    @pytest.mark.asyncio
    async def test_transient_failures_are_tolerated_below_the_deadline(
        self, logger
    ) -> None:
        """A Redis blip must not abort in-flight records: the lease still has
        time on it, so the next round can recover."""
        clock = _Clock()
        manager = _manager(side_effect=ConnectionError("blip"))
        renewer = _renewer(logger, manager, clock)
        handle = renewer.register("a")
        renewer.add("a", "indexing")

        renewer.start()
        try:
            await _settle(renewer, rounds=2)
            assert not handle.lost.is_set()
        finally:
            await renewer.stop()

    @pytest.mark.asyncio
    async def test_sustained_failure_past_the_deadline_releases_holders(
        self, logger
    ) -> None:
        """Past the safety deadline Redis would have expired these leases
        anyway, so continuing means working without a lease the rest of the
        fleet can see.

        The owner holds what a real one holds: a capacity pool *and* its
        per-record exclusivity pool. ``LeaseKind``'s fail-open governs
        *acquiring* a capacity lease — proceeding under the node-local gate
        when the cluster-wide cap cannot be read. It cannot apply here.
        ``_note_failure`` only runs when the whole renewal round raised, and
        that round renews every pool of every owner in one pipeline, so a
        raise means ``record:r1`` went unrenewed too. Sparing the capacity
        pool would leave the record processing with no exclusivity lease, and
        that is how one record gets indexed twice.
        """
        clock = _Clock()
        manager = _manager(side_effect=ConnectionError("down"))
        renewer = _renewer(logger, manager, clock)
        handle = renewer.register("a")
        renewer.add("a", "indexing")
        renewer.add("a", "record:r1")

        renewer.start()
        try:
            clock.advance(LEASE_SECONDS * 2)
            await asyncio.wait_for(handle.lost.wait(), timeout=2)
        finally:
            await renewer.stop()

        assert "safety deadline" in (handle.reason or "")

    @pytest.mark.asyncio
    async def test_idle_stretch_does_not_expire_the_next_lease(self, logger) -> None:
        """With nothing held there is nothing to prove, so an idle consumer
        must not accumulate 'time since last success' and instantly fail the
        first lease it takes afterwards."""
        clock = _Clock()
        manager = _manager(return_value={})
        renewer = _renewer(logger, manager, clock)

        renewer.start()
        try:
            clock.advance(LEASE_SECONDS * 5)
            await _settle(renewer)
            handle = renewer.register("a")
            renewer.add("a", "indexing")
            await _settle(renewer)
            assert not handle.lost.is_set()
        finally:
            await renewer.stop()

    @pytest.mark.asyncio
    async def test_unregister_is_idempotent_and_stops_renewals(self, logger) -> None:
        manager = _manager(return_value={})
        renewer = _renewer(logger, manager, _Clock())
        renewer.add("a", "indexing")
        renewer.unregister("a")
        renewer.unregister("a")

        renewer.start()
        try:
            await _settle(renewer)
        finally:
            await renewer.stop()

        for call in manager.renew_many.await_args_list:
            assert call.args[0] == []

    @pytest.mark.asyncio
    async def test_stop_is_safe_before_start(self, logger) -> None:
        await _renewer(logger, _manager(return_value={}), _Clock()).stop()


class TestLeaseSetMirrorsIntoRenewer:
    """The consumers add and discard leases from a dozen places; deriving the
    renewed set from that bookkeeping is what stops the two drifting."""

    @pytest.mark.asyncio
    async def test_add_and_discard_are_mirrored(self, logger) -> None:
        renewer = _renewer(logger, _manager(return_value={}), _Clock())
        leases = DistributedLeaseSet(renewer=renewer)

        leases.add("indexing", "owner-1")
        leases.add("parsing:light", "owner-1")
        assert renewer.register("owner-1").pools == {"indexing", "parsing:light"}

        leases.discard("parsing:light")
        assert renewer.register("owner-1").pools == {"indexing"}

    @pytest.mark.asyncio
    async def test_discarding_an_unheld_pool_is_a_no_op(self, logger) -> None:
        renewer = _renewer(logger, _manager(return_value={}), _Clock())
        leases = DistributedLeaseSet(renewer=renewer)

        assert leases.discard("indexing") is None
        assert renewer.register("owner-1").pools == set()

    def test_works_without_a_renewer(self) -> None:
        """The legacy path (no distributed concurrency) still uses the set for
        its own bookkeeping."""
        leases = DistributedLeaseSet()
        leases.add("indexing", "owner-1")
        assert leases.snapshot() == [("indexing", "owner-1")]
        assert leases.discard("indexing") == "owner-1"
