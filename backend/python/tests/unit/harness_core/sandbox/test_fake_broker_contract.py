"""SB-1..SB-30 on the in-memory broker (L1); SB-27..SB-30 with the fake egress proxy."""

from __future__ import annotations

import pytest

from harness_core.testing.clock import ManualClock
from harness_core.testing.sandbox import (
    FakeContractEnv,
    FakeEgressContractEnv,
    FakeSandboxBroker,
    SandboxBrokerContract,
)
from harness_core.testing.sandbox.erase_contract import SessionFolderEraserContract


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
def broker(clock: ManualClock) -> FakeSandboxBroker:
    return FakeSandboxBroker(clock=clock)


@pytest.fixture
def sandbox_env(clock: ManualClock) -> FakeContractEnv:
    return FakeContractEnv(clock)


class TestFakeBroker(SandboxBrokerContract):
    @pytest.fixture
    def egress_env(self, clock: ManualClock) -> FakeEgressContractEnv:
        return FakeEgressContractEnv(clock)

    @pytest.fixture
    def egress_broker(self, clock: ManualClock, egress_env: FakeEgressContractEnv) -> FakeSandboxBroker:
        return FakeSandboxBroker(clock=clock, egress_proxy=egress_env.proxy)


class TestFakeSessionFolderEraser(SessionFolderEraserContract):
    @pytest.fixture
    def eraser(self, clock: ManualClock) -> FakeSandboxBroker:
        return FakeSandboxBroker(clock=clock, persistent_mounts=True)
