"""C2.19 (SBX-21): the install's ceiling on live sandboxes, counted from the backend, pure."""

from __future__ import annotations

import asyncio

import pytest

from harness_core.sandbox.capacity import SandboxCap
from harness_core.sandbox.errors import BrokerUnavailable


class _Live:
    def __init__(self, held: int) -> None:
        self.held = held
        self.reads = 0

    async def __call__(self) -> int:
        self.reads += 1
        await asyncio.sleep(0)
        return self.held


async def test_c2_19_no_ceiling_admits_without_counting() -> None:
    live = _Live(10_000)
    async with SandboxCap(None, live).admit():
        pass
    assert live.reads == 0


async def test_c2_19_below_the_ceiling_admits() -> None:
    async with SandboxCap(3, _Live(2)).admit():
        pass


async def test_c2_19_at_the_ceiling_the_broker_is_unavailable_not_refused() -> None:
    """Capacity, not a refusal: the engine kernel waits and retries a BrokerUnavailable (QA31F)."""
    with pytest.raises(BrokerUnavailable, match="3 of 3"):
        async with SandboxCap(3, _Live(3)).admit():
            pytest.fail("admitted past the ceiling")


async def test_c2_19_creates_in_flight_count_until_they_end() -> None:
    cap = SandboxCap(2, _Live(1))
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold() -> None:
        async with cap.admit():
            entered.set()
            await release.wait()

    holder = asyncio.create_task(hold())
    await entered.wait()
    with pytest.raises(BrokerUnavailable):
        async with cap.admit():
            pass
    release.set()
    await holder
    async with cap.admit():
        pass


async def test_c2_19_concurrent_creates_at_the_boundary_admit_exactly_the_room_left() -> None:
    """SBX-21: twenty parallel creates with three places left: three admitted, never more."""
    cap = SandboxCap(5, _Live(2))
    admitted = refused = 0
    gate = asyncio.Event()
    settled = asyncio.Event()

    async def create() -> bool:
        nonlocal admitted, refused
        try:
            async with cap.admit():
                admitted += 1
                if admitted + refused == 20:
                    settled.set()
                await gate.wait()
                return True
        except BrokerUnavailable:
            refused += 1
            if admitted + refused == 20:
                settled.set()
            return False

    tasks = [asyncio.create_task(create()) for _ in range(20)]
    await settled.wait()
    assert admitted == 3
    gate.set()
    assert sum(await asyncio.gather(*tasks)) == 3


async def test_c2_19_a_failed_create_gives_its_place_back() -> None:
    cap = SandboxCap(1, _Live(0))
    with pytest.raises(RuntimeError):
        async with cap.admit():
            raise RuntimeError("create failed")
    async with cap.admit():
        pass


@pytest.mark.parametrize("limit", [0, -1])
def test_c2_19_a_ceiling_is_positive(limit: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        _ = SandboxCap(limit, _Live(0))
