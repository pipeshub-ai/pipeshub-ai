"""CH-16: a sandbox provider that is slow or erroring on create is retried with backoff, then the next provider the
deployment configured serves the run; a refusal is never retried; each sandbox goes back to the provider that made it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.sandbox.errors import BrokerUnavailable, ImageNotAllowed
from harness_core.sandbox.failover import (
    CREATE_BACKOFF_S,
    FailoverSandboxBroker,
    SandboxProvider,
)
from harness_core.sandbox.types import BrokerHealth, LeaseRef
from harness_core.testing.clock import ManualClock
from harness_core.testing.sandbox import (
    FakeContractEnv,
    FakeEgressContractEnv,
    FakeSandboxBroker,
    SandboxBrokerContract,
)

if TYPE_CHECKING:
    from harness_core.sandbox.types import SandboxSpec
    from harness_core.testing.sandbox.fake import FakeSandboxHandle

LEASE = LeaseRef("worker-1", 1)


class _Flaky(FakeSandboxBroker):
    """A provider whose first ``failures`` creates fail as an outage, or whose creates all refuse with ``refusal``."""

    def __init__(self, clock: ManualClock, *, failures: int = 0, refusal: Exception | None = None) -> None:
        super().__init__(clock=clock)
        self.failures = failures
        self.refusal = refusal
        self.creates = 0
        self.healthy = True

    async def acquire(self, spec: SandboxSpec, *, lease: LeaseRef, idempotency_key: str) -> FakeSandboxHandle:
        self.creates += 1
        if self.refusal is not None:
            raise self.refusal
        if self.failures > 0:
            self.failures -= 1
            raise BrokerUnavailable("provider answered 503")
        return await super().acquire(spec, lease=lease, idempotency_key=idempotency_key)

    async def health(self) -> BrokerHealth:
        if not self.healthy:
            raise BrokerUnavailable("provider unreachable")
        return await super().health()


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
def env(clock: ManualClock) -> FakeContractEnv:
    return FakeContractEnv(clock)


def _failover(*brokers: FakeSandboxBroker, slept: list[float]) -> FailoverSandboxBroker:
    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    return FailoverSandboxBroker(
        [SandboxProvider(f"p{i}", b) for i, b in enumerate(brokers)], sleep=sleep
    )


async def test_ch16_a_create_that_errors_is_retried_with_backoff_on_the_same_provider(
    clock: ManualClock, env: FakeContractEnv
) -> None:
    primary, slept = _Flaky(clock, failures=2), []
    broker = _failover(primary, slept=slept)
    handle = await broker.acquire(env.spec(), lease=LEASE, idempotency_key="k1")
    assert primary.creates == 3 and slept == list(CREATE_BACKOFF_S)
    assert handle.id in primary.sandboxes


async def test_ch16_a_provider_that_stays_down_fails_over_to_the_next_one(
    clock: ManualClock, env: FakeContractEnv
) -> None:
    primary, fallback, slept = _Flaky(clock, failures=99), _Flaky(clock), []
    broker = _failover(primary, fallback, slept=slept)
    handle = await broker.acquire(env.spec(), lease=LEASE, idempotency_key="k1")
    assert primary.creates == len(CREATE_BACKOFF_S) + 1 and fallback.creates == 1
    assert handle.id in fallback.sandboxes and handle.id not in primary.sandboxes
    await broker.release(handle)
    assert handle.id not in fallback.sandboxes


async def test_ch16_every_provider_down_is_an_outage_the_run_waits_out(
    clock: ManualClock, env: FakeContractEnv
) -> None:
    broker = _failover(_Flaky(clock, failures=99), _Flaky(clock, failures=99), slept=[])
    with pytest.raises(BrokerUnavailable):
        await broker.acquire(env.spec(), lease=LEASE, idempotency_key="k1")


async def test_ch16_a_refusal_is_raised_at_once_and_never_tried_elsewhere(
    clock: ManualClock, env: FakeContractEnv
) -> None:
    primary = _Flaky(clock, refusal=ImageNotAllowed("not on the allowlist", reason="image_not_allowed"))
    fallback, slept = _Flaky(clock), []
    broker = _failover(primary, fallback, slept=slept)
    with pytest.raises(ImageNotAllowed):
        await broker.acquire(env.spec(), lease=LEASE, idempotency_key="k1")
    assert (primary.creates, fallback.creates, slept) == (1, 0, [])


async def test_ch16_a_retried_create_is_one_sandbox_and_health_reports_the_providers_that_answer(
    clock: ManualClock, env: FakeContractEnv
) -> None:
    primary, fallback = _Flaky(clock), _Flaky(clock)
    broker = _failover(primary, fallback, slept=[])
    first = await broker.acquire(env.spec(), lease=LEASE, idempotency_key="k1")
    again = await broker.acquire(env.spec(), lease=LEASE, idempotency_key="k1")
    assert first.id == again.id and len(primary.sandboxes) == 1
    primary.healthy = False
    health = await broker.health()
    assert health.ok and health.modes == fallback.modes()


class TestFailoverSandboxBroker(SandboxBrokerContract):
    """SB-1..SB-30 through the failover broker over the in-memory one: a provider wrapped in it is still a broker."""

    @pytest.fixture
    def broker(self, clock: ManualClock) -> FailoverSandboxBroker:
        return FailoverSandboxBroker([SandboxProvider("fake", FakeSandboxBroker(clock=clock))])

    @pytest.fixture
    def sandbox_env(self, clock: ManualClock) -> FakeContractEnv:
        return FakeContractEnv(clock)

    @pytest.fixture
    def egress_env(self, clock: ManualClock) -> FakeEgressContractEnv:
        return FakeEgressContractEnv(clock)

    @pytest.fixture
    def egress_broker(self, clock: ManualClock, egress_env: FakeEgressContractEnv) -> FailoverSandboxBroker:
        fake = FakeSandboxBroker(clock=clock, egress_proxy=egress_env.proxy)
        return FailoverSandboxBroker([SandboxProvider("fake", fake)])
