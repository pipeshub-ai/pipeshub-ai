"""SN-* and SC-* (P5.9b): the snapshot store on its in-memory and sealed adapters, capture and restore on
the fake broker."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from harness_core.engines.errors import SnapshotRefused
from harness_core.engines.snapshots import (
    SNAPSHOT_FAILED,
    ObjectStoreSandboxSnapshots,
    capture,
)
from harness_core.sandbox.types import LeaseRef, MountSpec, RuntimeMode, SandboxSpec
from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.sandbox_snapshots import (
    POLICY,
    SandboxSnapshotStoreContract,
    SnapshotCaptureContract,
    tar_of,
)
from harness_core.testing.sandbox.fake import (
    FakeProcessContext,
    FakeSandboxBroker,
    FakeSandboxHandle,
)
from harness_core.workspace.memory import InMemoryBinaryObjectStore, InMemoryKeyProvider
from harness_core.workspace.sealing import SealedObjectStore
from harness_core.workspace.types import ObjectScope

if TYPE_CHECKING:
    from harness_core.sandbox.ports import SandboxHandle

SCOPE = ObjectScope("org-1", "ws:session-1")
OTHER_SCOPE = ObjectScope("org-1", "ws:session-2")


@dataclass
class _Plain:
    inner: InMemoryBinaryObjectStore = field(default_factory=InMemoryBinaryObjectStore)
    scope: ObjectScope = SCOPE
    other_scope: ObjectScope = OTHER_SCOPE

    @property
    def store(self) -> ObjectStoreSandboxSnapshots:
        return ObjectStoreSandboxSnapshots(self.inner)

    def tamper(self) -> None:
        self.inner.tamper()

    def lose(self) -> None:
        self.inner._objects.clear()  # pyright: ignore[reportPrivateUsage]

    async def shred(self) -> bool:
        return False

    def stored(self) -> list[bytes] | None:
        return self.inner.stored_bytes()


@dataclass
class _Sealed(_Plain):
    keys: InMemoryKeyProvider = field(default_factory=InMemoryKeyProvider)

    @property
    def store(self) -> ObjectStoreSandboxSnapshots:
        return ObjectStoreSandboxSnapshots(SealedObjectStore(self.inner, self.keys))

    async def shred(self) -> bool:
        assert self.scope.partition is not None
        await self.keys.destroy(self.scope.tenant_id, self.scope.partition)
        return True


class TestSnapshotsOnAPlainStore(SandboxSnapshotStoreContract):
    @pytest.fixture
    def snapshot_store_env(self) -> _Plain:
        return _Plain()


class TestSnapshotsSealedWithTheSessionKey(SandboxSnapshotStoreContract):
    @pytest.fixture
    def snapshot_store_env(self) -> _Sealed:
        return _Sealed()


class _FakeCapture:
    def __init__(self) -> None:
        self.broker = FakeSandboxBroker(clock=ManualClock())
        self._keys = itertools.count()

    async def sandbox(self) -> SandboxHandle:
        spec = SandboxSpec("img@sha256:" + "0" * 64, RuntimeMode.DOCKER_HARDENED, mounts=(MountSpec("/workspace"),))
        return await self.broker.acquire(spec, lease=LeaseRef("w", 1), idempotency_key=f"k-{next(self._keys)}")

    async def write(self, sandbox: SandboxHandle, path: str, data: bytes, *, mode: int = 0o644) -> None:
        assert isinstance(sandbox, FakeSandboxHandle)
        sandbox.put_file(path, data, mode)

    async def mkdir(self, sandbox: SandboxHandle, path: str) -> None:
        assert isinstance(sandbox, FakeSandboxHandle)
        sandbox.make_dir(path)

    async def read(self, sandbox: SandboxHandle, path: str) -> bytes | None:
        assert isinstance(sandbox, FakeSandboxHandle)
        return sandbox.files.get(path)

    async def mode(self, sandbox: SandboxHandle, path: str) -> int:
        assert isinstance(sandbox, FakeSandboxHandle)
        return sandbox.state.files[path].mode

    async def kill(self, sandbox: SandboxHandle) -> None:
        await self.broker.release(sandbox)


class TestCaptureOnTheFakeBroker(SnapshotCaptureContract):
    @pytest.fixture
    def capture_env(self) -> _FakeCapture:
        return _FakeCapture()


async def test_sc4_a_tar_that_fails_midway_is_never_a_snapshot() -> None:
    """``tar`` dies after writing part of the archive while the sandbox runs: refused, never stored half."""
    env = _FakeCapture()
    sandbox = await env.sandbox()
    assert isinstance(sandbox, FakeSandboxHandle)

    async def broken_tar(context: FakeProcessContext) -> int:
        if context.argv[2] == "exit 0":
            return 0
        await context.stdout.put(tar_of({"tmp/.cache/half": b"x" * 4096})[:1024])
        return 1

    sandbox.install("sh", broken_tar)
    with pytest.raises(SnapshotRefused) as refused:
        _ = await capture(sandbox, POLICY)
    assert refused.value.reason_code == SNAPSHOT_FAILED
