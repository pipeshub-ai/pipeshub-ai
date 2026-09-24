"""Tests for SandboxResourceGovernor — process-wide sandbox slot accounting."""

from __future__ import annotations

import asyncio

import pytest

from app.agent_loop_lib.sandbox.governor import (
    GovernorLimits,
    SandboxLimitExceeded,
    SandboxResourceGovernor,
)


@pytest.fixture
def governor() -> SandboxResourceGovernor:
    return SandboxResourceGovernor()


class TestSandboxResourceGovernor:
    async def test_acquire_and_release(self, governor: SandboxResourceGovernor) -> None:
        lease = await governor.acquire(org_id="o1")
        assert governor.snapshot()["total"] == 1

        await governor.release(lease)
        assert governor.snapshot()["total"] == 0

    async def test_total_limit_enforced(self) -> None:
        gov = SandboxResourceGovernor(GovernorLimits(max_total_sandboxes=2))
        l1 = await gov.acquire()
        l2 = await gov.acquire()
        assert gov.snapshot()["total"] == 2

        with pytest.raises(SandboxLimitExceeded):
            await gov.acquire()

        await gov.release(l1)
        await gov.release(l2)

    async def test_per_org_limit_enforced(self) -> None:
        gov = SandboxResourceGovernor(GovernorLimits(max_per_org=1))
        l1 = await gov.acquire(org_id="a")

        with pytest.raises(SandboxLimitExceeded):
            await gov.acquire(org_id="a")

        l2 = await gov.acquire(org_id="b")
        assert gov.snapshot()["total"] == 2

        await gov.release(l1)
        await gov.release(l2)

    async def test_release_is_idempotent(self, governor: SandboxResourceGovernor) -> None:
        lease = await governor.acquire(org_id="x")
        await governor.release(lease)
        await governor.release(lease)
        assert governor.snapshot()["total"] == 0

    async def test_snapshot_shows_per_org_counts(self, governor: SandboxResourceGovernor) -> None:
        lx = await governor.acquire(org_id="x")
        ly = await governor.acquire(org_id="y")

        snap = governor.snapshot()
        assert snap["per_org"] == {"x": 1, "y": 1}

        await governor.release(lx)
        await governor.release(ly)

    async def test_release_cleans_up_zero_orgs(self, governor: SandboxResourceGovernor) -> None:
        lease = await governor.acquire(org_id="a")
        await governor.release(lease)
        assert "a" not in governor.snapshot()["per_org"]

    async def test_default_limits(self) -> None:
        limits = GovernorLimits()
        assert limits.max_total_sandboxes == 50
        assert limits.max_per_org == 10

    async def test_none_limits_mean_unlimited(self) -> None:
        gov = SandboxResourceGovernor(
            GovernorLimits(max_total_sandboxes=None, max_per_org=None)
        )
        leases = [await gov.acquire(org_id="org") for _ in range(100)]
        assert gov.snapshot()["total"] == 100

        for lease in leases:
            await gov.release(lease)

    async def test_governor_lease_release_method(self, governor: SandboxResourceGovernor) -> None:
        lease = await governor.acquire(org_id="z")
        assert governor.snapshot()["total"] == 1

        await lease.release()
        assert governor.snapshot()["total"] == 0

    async def test_concurrent_acquire_release(self) -> None:
        gov = SandboxResourceGovernor(GovernorLimits(max_total_sandboxes=None, max_per_org=None))

        async def _cycle() -> None:
            lease = await gov.acquire(org_id="c")
            await asyncio.sleep(0)
            await gov.release(lease)

        await asyncio.gather(*[_cycle() for _ in range(20)])
        assert gov.snapshot()["total"] == 0
