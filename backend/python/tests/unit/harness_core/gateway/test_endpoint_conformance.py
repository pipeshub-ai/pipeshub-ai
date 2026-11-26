"""CF-E: the engine conformance suite with the tool side served by the real ``RunToolEndpoint``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.testing.engines.contract import ENDPOINTS, EngineAdapterContract
from harness_core.testing.engines.endpoint_gateway import EndpointConformanceGateway
from harness_core.testing.engines.fake_engine import FakeEngineAdapter
from tests.unit.harness_core.engines.test_fake_engine_conformance import (
    FakeEngineHarness,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from harness_core.testing.engines.contract import EngineConformanceHarness
    from harness_core.testing.engines.gateway import GrantedTool


class TestFakeEngineThroughTheRealEndpoint(EngineAdapterContract):
    adapter = FakeEngineAdapter(can_steer=False, max_tools=40)

    @pytest.fixture
    def engine_harness(self) -> EngineConformanceHarness:
        return FakeEngineHarness(self.adapter)

    def make_gateway(self, grant: Sequence[GrantedTool]) -> EndpointConformanceGateway:
        return EndpointConformanceGateway(
            endpoints=ENDPOINTS, grant=grant, capabilities=self.adapter.capabilities()
        )
