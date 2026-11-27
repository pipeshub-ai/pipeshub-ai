"""CF-*: the conformance suite on ``FakeEngine``, the reference adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from harness_core.testing.engines.contract import (
    EngineAdapterContract,
    EngineConformanceHarness,
    new_engine_sandbox,
)
from harness_core.testing.engines.fake_engine import NATIVE_NAMES, FakeEngineAdapter
from harness_core.testing.ids import SeededIds

if TYPE_CHECKING:
    from harness_core.engines.policy import NativeTool
    from harness_core.engines.types import EngineVersion
    from harness_core.testing.sandbox.egress import FakeEgress
    from harness_core.testing.sandbox.fake import FakeSandboxHandle


@dataclass
class FakeEngineHarness:
    adapter: FakeEngineAdapter
    ids: SeededIds = field(default_factory=SeededIds)

    @property
    def version(self) -> EngineVersion:
        return self.adapter.version

    def native_names(self, tool: NativeTool) -> frozenset[str]:
        return frozenset({NATIVE_NAMES[tool]})

    @property
    def plan_names(self) -> frozenset[str]:
        return frozenset()

    def exposed_name(self, granted: str) -> str:
        return granted

    async def new_sandbox(self, egress: FakeEgress) -> FakeSandboxHandle:
        return await new_engine_sandbox(egress, ids=self.ids)

    def secrets(self) -> frozenset[str]:
        return frozenset()


class TestFakeEngineConformance(EngineAdapterContract):
    @pytest.fixture
    def engine_harness(self) -> EngineConformanceHarness:
        return FakeEngineHarness(FakeEngineAdapter())


class TestFakeEngineWithoutSteeringOrWithAToolLimit(EngineAdapterContract):
    @pytest.fixture
    def engine_harness(self) -> EngineConformanceHarness:
        return FakeEngineHarness(FakeEngineAdapter(can_steer=False, max_tools=40))
