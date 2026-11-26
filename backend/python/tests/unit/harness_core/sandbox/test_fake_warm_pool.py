"""WP-1..WP-10 on the in-memory broker (L1), and SB-1..SB-30 with its pool on (WP-8)."""

from __future__ import annotations

import contextlib
import uuid
from typing import TYPE_CHECKING, override

import pytest

from harness_core.sandbox.pool import WarmPoolConfig, shape_of
from harness_core.sandbox.types import Resources, SandboxSpec
from harness_core.testing.clock import ManualClock
from harness_core.testing.sandbox import (
    FakeContractEnv,
    FakeEgressContractEnv,
    FakeSandboxBroker,
    SandboxBrokerContract,
)
from harness_core.testing.sandbox.contract import WORK
from harness_core.testing.sandbox.pool_contract import (
    WarmPoolContract,
    WarmPoolContractEnv,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

SLOT_MB = 512
RESOURCES = Resources(cpus=0.5, memory_mb=SLOT_MB, pids=64, scratch_mb=64)


class FakePoolEnv(WarmPoolContractEnv):
    proxied = True

    def __init__(self) -> None:
        self.clock = ManualClock()
        self._programs = FakeContractEnv(self.clock)
        self.egress = FakeEgressContractEnv(self.clock)

    @property
    @override
    def programs(self) -> FakeContractEnv:
        return self._programs

    @override
    def spec(self, labels: Mapping[str, str] | None = None) -> SandboxSpec:
        base = self._programs.spec(labels=labels)
        return SandboxSpec(
            image_digest=base.image_digest,
            mode=base.mode,
            resources=RESOURCES,
            egress=self.egress.policy(f"pxh-{uuid.uuid4().hex}"),
            mounts=base.mounts,
            labels=base.labels,
        )

    @contextlib.asynccontextmanager
    async def broker(  # type: ignore[override]
        self, *, ceiling: int, slots: int, fraction: float = 1.0, max_age_s: int = 3600
    ) -> AsyncIterator[FakeSandboxBroker]:
        shape = shape_of(self.spec())
        assert shape is not None
        config = WarmPoolConfig(
            ceiling=ceiling, shapes=(shape,), fraction=fraction, reserve_fraction=0.0, max_age_s=max_age_s
        )
        broker = FakeSandboxBroker(
            clock=self.clock, egress_proxy=self.egress.proxy, warm_pool=config, memory_mb=slots * SLOT_MB
        )
        try:
            yield broker
        finally:
            await broker.pool.aclose()

    @override
    async def egress_bound(self, sandbox_id: str) -> bool:
        return sandbox_id in await self.egress.bound()

    @override
    def advance(self, seconds: float) -> None:
        self.clock.advance(seconds)


class TestFakeWarmPool(WarmPoolContract):
    @pytest.fixture
    def pool_env(self) -> FakePoolEnv:
        return FakePoolEnv()


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
def sandbox_env(clock: ManualClock) -> FakeContractEnv:
    return FakeContractEnv(clock)


@pytest.fixture
async def broker(clock: ManualClock, sandbox_env: FakeContractEnv) -> AsyncIterator[FakeSandboxBroker]:
    shape = shape_of(sandbox_env.spec())
    assert shape is not None
    pooled = FakeSandboxBroker(clock=clock, warm_pool=WarmPoolConfig(ceiling=2, shapes=(shape,)))
    assert await pooled.fill_warm() == 2
    yield pooled
    await pooled.pool.aclose()


class TestFakeBrokerWithPoolOn(SandboxBrokerContract):
    """WP-8: the whole broker contract holds when acquires are served warm."""

    @pytest.fixture
    def egress_env(self, clock: ManualClock) -> FakeEgressContractEnv:
        return FakeEgressContractEnv(clock)

    @pytest.fixture
    async def egress_broker(self, clock: ManualClock, egress_env: FakeEgressContractEnv) -> AsyncIterator[FakeSandboxBroker]:
        spec = SandboxSpec(
            image_digest="fake@sha256:" + "0" * 64,
            mode=FakeContractEnv(clock).spec().mode,
            egress=egress_env.policy("x" * 16),
            mounts=FakeContractEnv(clock).spec().mounts,
        )
        shape = shape_of(spec)
        assert shape is not None
        pooled = FakeSandboxBroker(clock=clock, egress_proxy=egress_env.proxy, warm_pool=WarmPoolConfig(ceiling=2, shapes=(shape,)))
        _ = await pooled.fill_warm()
        yield pooled
        await pooled.pool.aclose()


async def test_the_warm_sandbox_of_the_pooled_contract_is_used(broker: FakeSandboxBroker, sandbox_env: FakeContractEnv) -> None:
    from harness_core.sandbox.types import LeaseRef

    warm = broker.warm_ids()
    handle = await broker.acquire(sandbox_env.spec(), lease=LeaseRef("w", 1), idempotency_key="k")
    assert handle.id in warm
    assert ("acquire_warm", handle.id, ()) in broker.calls
    assert WORK in handle.state.dirs
