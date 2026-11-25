"""G-45 PA-1..PA-6: ``ProviderAdmissionContract`` against the process gate and the deployment's shared Redis gate.

The shared gate runs on the clock it runs on in production (Redis TIME, real sleeps between asks), over a fake Redis.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pytest

from app.harness.provider_gate import RedisProviderGate
from harness_core.gateway.priority_gate import PriorityGate
from harness_core.testing.contracts.provider_admission import ProviderAdmissionContract
from tests.support.fake_redis_connection_provider import FakeRedisConnectionProvider

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable


class TestPriorityGate(ProviderAdmissionContract):
    @pytest.fixture
    def admit(self) -> Callable[[int], PriorityGate]:
        return lambda limit: PriorityGate(limit, floor_every=0, monotonic=time.monotonic)


class TestRedisProviderGate(ProviderAdmissionContract):
    @pytest.fixture
    async def admit(self) -> AsyncIterator[Callable[[int], RedisProviderGate]]:
        redis = FakeRedisConnectionProvider(is_cluster=True)
        made: list[RedisProviderGate] = []

        def build(limit: int) -> RedisProviderGate:
            gate = RedisProviderGate(redis, limit, floor_every=0, monotonic=time.monotonic)
            made.append(gate)
            return gate

        yield build
        for gate in made:
            await gate.aclose()
