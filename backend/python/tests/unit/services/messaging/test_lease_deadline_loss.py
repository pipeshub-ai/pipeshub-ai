"""Leases lost to the renewal deadline are distinguishable from leases
Redis actively refused, because the consumers treat them differently: a
deadline loss means Redis has been failing for the whole lease TTL, and the
leases have expired server-side, so releasing them is doomed traffic."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.messaging.lease import DEADLINE_LOSS_REASON, LeaseHandle, LeaseRenewer


class TestLostToDeadline:
    def test_a_fresh_handle_is_not_lost(self) -> None:
        handle = LeaseHandle("owner-1")
        assert not handle.lost.is_set()
        assert not handle.lost_to_deadline

    def test_a_lease_redis_refused_is_not_a_deadline_loss(self) -> None:
        handle = LeaseHandle("owner-1")
        handle.mark_lost("Lost distributed parsing concurrency lease")
        assert handle.lost.is_set()
        assert not handle.lost_to_deadline

    @pytest.mark.asyncio
    async def test_the_renewer_deadline_marks_every_holder_as_a_deadline_loss(self) -> None:
        clock = [0.0]
        renewer = LeaseRenewer(
            MagicMock(), AsyncMock(), lease_seconds=120.0, interval_seconds=30.0,
            clock=lambda: clock[0],
        )
        first = renewer.register("owner-1")
        second = renewer.register("owner-2")
        first.pools.add("indexing")
        second.pools.add("indexing")

        clock[0] = 89.0
        renewer._note_failure(ConnectionError("blip"))
        assert not first.lost.is_set(), "one interval short of the lease is still a blip"

        clock[0] = 90.0
        renewer._note_failure(ConnectionError("still down"))
        for handle in (first, second):
            assert handle.lost.is_set()
            assert handle.reason == DEADLINE_LOSS_REASON
            assert handle.lost_to_deadline
