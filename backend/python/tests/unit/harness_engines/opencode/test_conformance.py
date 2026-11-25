"""CF-*: the conformance suite on the opencode adapter against the recorded fake opencode."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.testing.engines.contract import (
    EngineAdapterContract,
    EngineConformanceHarness,
)

if TYPE_CHECKING:
    from tests.unit.harness_engines.opencode.conftest import OpencodeHarness


class TestOpencodeConformance(EngineAdapterContract):
    @pytest.fixture
    def engine_harness(self, opencode_harness: OpencodeHarness) -> EngineConformanceHarness:
        return opencode_harness
