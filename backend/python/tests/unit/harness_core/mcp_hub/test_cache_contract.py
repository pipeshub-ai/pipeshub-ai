"""SC-1..SC-4 on ``InMemoryToolSchemaCache``; SC-8 the list TTL (P2.8)."""

from __future__ import annotations

import pytest

from harness_core.mcp_hub import InMemoryToolSchemaCache, list_ttl_s
from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.mcp_hub import ToolSchemaCacheContract


class InMemoryHarness:
    def __init__(self) -> None:
        self._clock = ManualClock()
        self.cache = InMemoryToolSchemaCache(self._clock)

    async def elapse(self, seconds: float) -> None:
        _ = self._clock.advance(seconds)


class TestInMemoryToolSchemaCache(ToolSchemaCacheContract):
    @pytest.fixture
    def harness(self) -> InMemoryHarness:
        return InMemoryHarness()


@pytest.mark.parametrize(
    ("ttl_ms", "expected"),
    [(None, 600.0), (30_000, 30.0), (0, 0.0), (10_000_000, 600.0), (-5, 600.0), ("60000", 600.0), (True, 600.0)],
)
def test_sc8_a_list_results_ttl_is_honoured_under_the_ceiling(ttl_ms: object, expected: float) -> None:
    assert list_ttl_s(ttl_ms, 600.0) == expected
