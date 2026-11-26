"""CF-*: the shared engine conformance suite on the ACP driver (``AcpAdapter``), against the scripted fake ACP agent."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.testing.engines.contract import (
    EngineAdapterContract,
    EngineConformanceHarness,
)
from harness_engines.acp.adapter import AcpAdapter

if TYPE_CHECKING:
    from tests.unit.harness_engines.acp.conftest import AcpHarness


class TestAcpConformance(EngineAdapterContract):
    # The fake agent compacts past 200,000 counted tokens and says so with ACP's ``compaction_update``.
    auto_compacts = True
    # MG-10 (WP91-ACPCOMPACT): the fake fails the prompt on an overflow as Gemini CLI 0.63 does; the driver restarts it
    # from a summary, so cf21 holds on ACP too.

    @pytest.fixture
    def engine_harness(self, acp_harness: AcpHarness) -> EngineConformanceHarness:
        assert isinstance(acp_harness.adapter, AcpAdapter)
        assert acp_harness.adapter.kind == "acp-fake"
        return acp_harness
