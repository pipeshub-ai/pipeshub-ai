"""The in-process LiveBus passes the shared LiveBus contract suite (L-17)."""

from __future__ import annotations

import pytest

from harness_core.journal import InMemoryLiveBus
from harness_core.testing.contracts.livebus import LiveBusContract


class TestInMemoryLiveBusContract(LiveBusContract):
    @pytest.fixture
    def bus(self) -> InMemoryLiveBus:
        return InMemoryLiveBus(maxlen=self.MAXLEN)
