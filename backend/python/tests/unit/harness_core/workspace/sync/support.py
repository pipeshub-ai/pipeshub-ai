"""A ``WorkspaceSyncHarness`` over the fake broker and sealed in-memory stores (production's shape)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.workspace_sync import (
    CountingObjectStore,
    FakeEngineHands,
)
from harness_core.testing.sandbox import FakeSandboxBroker
from harness_core.workspace import ObjectScope
from harness_core.workspace.memory import (
    InMemoryBinaryObjectStore,
    InMemoryKeyProvider,
    InMemoryWorkspaceStore,
)
from harness_core.workspace.sealing import SealedObjectStore
from harness_core.workspace.sync import TAR, VOLUME, TarWorkspaceSync
from harness_core.workspace.sync.strategies import SyncStrategies

if TYPE_CHECKING:
    from harness_core.sandbox.types import SandboxSpec
    from harness_core.workspace.sync import SyncLimits, SyncStrategy

ROOT = "/workspace"
SESSION = "ws:session-sync"
START = datetime(2026, 10, 3, tzinfo=UTC)


class FakeSyncHarness:
    def __init__(self, strategy: SyncStrategy, *, limits: SyncLimits | None = None, references: bool = False) -> None:
        # ADR-RUN-02: the sandbox also has the references mount ``ph git clone`` checks out on.
        self.references = references
        self.clock = ManualClock(START)
        self.broker = FakeSandboxBroker(clock=self.clock, persistent_mounts=strategy is VOLUME)
        self.strategy = strategy
        self.scope = ObjectScope("tenant-a")
        self.object_scope = ObjectScope("tenant-a", SESSION)
        self.keys = InMemoryKeyProvider()
        self.blobs = InMemoryBinaryObjectStore()
        self.objects = CountingObjectStore(SealedObjectStore(self.blobs, self.keys))
        self.store = InMemoryWorkspaceStore(clock=self.clock)
        self.sync = TarWorkspaceSync(objects=self.objects, scope=self.object_scope, clock=self.clock, limits=limits)
        self.hands = FakeEngineHands()
        self.root = ROOT

    def spec(self) -> SandboxSpec:
        from harness_core.engines.controller import references_mount_for
        from harness_core.sandbox.types import RuntimeMode, SandboxSpec
        from harness_core.workspace.sync.storage_classes import REFERENCES_DIR

        workspace = self.strategy.mount(ROOT, source="tenant-a/session-sync/workspace")
        extra = (references_mount_for(workspace, REFERENCES_DIR, 64),) if self.references else ()
        return SandboxSpec(
            image_digest="fake@sha256:" + "0" * 64,
            mode=RuntimeMode.DOCKER_HARDENED,
            mounts=(workspace, *extra),
        )


async def harness_for_broker(strategy: SyncStrategy) -> FakeSyncHarness:
    """The harness for whatever strategy the broker's capabilities select."""
    harness = FakeSyncHarness(strategy)
    assert SyncStrategies().for_broker(await harness.broker.health()) is strategy
    return harness


__all__ = ["ROOT", "SESSION", "TAR", "VOLUME", "FakeSyncHarness", "harness_for_broker"]
