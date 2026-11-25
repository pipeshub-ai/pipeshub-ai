"""WY-1..WY-12: ``TarWorkspaceSync`` passes the sync contract on the fake broker, with both mount strategies."""

from __future__ import annotations

import pytest

from harness_core.testing.contracts.workspace_sync import (
    PersistentWorkspaceSyncContract,
    WorkspaceSyncContract,
)
from harness_core.workspace.sync import TAR, VOLUME
from tests.unit.harness_core.workspace.sync.support import (
    FakeSyncHarness,
    harness_for_broker,
)


class TestTarStrategyOnFake(WorkspaceSyncContract):
    """``TarWorkspaceSync`` over a scratch mount."""

    @pytest.fixture
    async def harness(self) -> FakeSyncHarness:  # type: ignore[override]
        return await harness_for_broker(TAR)


class TestVolumeStrategyOnFake(PersistentWorkspaceSyncContract):
    """``TarWorkspaceSync`` over the session's persistent mount."""

    @pytest.fixture
    async def harness(self) -> FakeSyncHarness:  # type: ignore[override]
        return await harness_for_broker(VOLUME)
