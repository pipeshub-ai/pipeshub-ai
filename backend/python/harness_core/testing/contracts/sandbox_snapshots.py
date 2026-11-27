"""Contract suites for sandbox snapshots (P5.9b).

* ``SandboxSnapshotStoreContract`` (SN-*): provide ``snapshot_store_env`` (``SnapshotStoreEnv``).
* ``SnapshotCaptureContract`` (SC-*): provide ``capture_env`` (``CaptureEnv``): fresh sandboxes of one
  broker, a way to write a file inside one (as its processes would) and to kill one.
"""

from __future__ import annotations

import io
import logging
import tarfile
from typing import TYPE_CHECKING, Protocol

import pytest

from harness_core.engines.errors import SnapshotRefused, SnapshotUnavailable
from harness_core.engines.snapshots import (
    SNAPSHOT_CORRUPT,
    SNAPSHOT_FOREIGN,
    SNAPSHOT_MISSING,
    SNAPSHOT_SANDBOX_GONE,
    SNAPSHOT_TOO_LARGE,
    SNAPSHOT_UNSAFE,
    ObjectStoreSandboxSnapshots,
    SandboxSnapshots,
    SnapshotPolicy,
    capture,
    read_archive,
    restore,
)
from harness_core.logs import HarnessLog
from harness_core.workspace.memory import InMemoryBinaryObjectStore
from harness_core.workspace.types import ObjectScope

if TYPE_CHECKING:
    from collections.abc import Sequence

    from harness_core.engines.ports import SandboxSnapshotStore
    from harness_core.sandbox.ports import SandboxHandle


def tar_of(files: dict[str, bytes], *, folders: Sequence[str] = ()) -> bytes:
    """A tar of member name → content (and empty folders), as a sandbox's ``tar -c`` at ``/`` writes it."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for folder in folders:
            info = tarfile.TarInfo(folder)
            info.type, info.mode = tarfile.DIRTYPE, 0o755
            archive.addfile(info)
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(content), 0o644, 1_700_000_000
            archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


ARCHIVE = tar_of({"tmp/.cache/tool/index.json": b'{"warm": true}\n', "tmp/.cache/tool/blob.bin": bytes(range(256))})
OTHER = tar_of({"tmp/.cache/tool/index.json": b'{"warm": false}\n'})


class SnapshotStoreEnv(Protocol):
    @property
    def store(self) -> SandboxSnapshotStore: ...

    @property
    def scope(self) -> ObjectScope:
        """A session's partition."""
        ...

    @property
    def other_scope(self) -> ObjectScope:
        """Another session of the same tenant."""
        ...

    def tamper(self) -> None:
        """Change the stored bytes of every object, keeping their references."""
        ...

    def lose(self) -> None:
        """Remove every stored object."""
        ...

    async def shred(self) -> bool:
        """Destroy ``scope``'s data key; ``False`` when the store does not seal."""
        ...

    def stored(self) -> list[bytes] | None:
        """The bytes the backing store holds, when it can be read directly; ``None`` otherwise."""
        ...


class SandboxSnapshotStoreContract:
    @pytest.fixture
    def snapshot_store_env(self) -> SnapshotStoreEnv:
        raise NotImplementedError("provide a snapshot_store_env fixture")

    async def test_sn1_save_then_load_round_trips(self, snapshot_store_env: SnapshotStoreEnv) -> None:
        env = snapshot_store_env
        ref = await env.store.save(env.scope, ARCHIVE)
        assert await env.store.load(env.scope, ref) == ARCHIVE

    async def test_sn2_equal_archives_are_one_object(self, snapshot_store_env: SnapshotStoreEnv) -> None:
        env = snapshot_store_env
        first = await env.store.save(env.scope, ARCHIVE)
        assert await env.store.save(env.scope, ARCHIVE) == first
        assert await env.store.content_hash(env.scope, ARCHIVE) == first.content_hash
        other = await env.store.save(env.scope, OTHER)
        assert other.content_hash != first.content_hash
        assert other.object_id != first.object_id

    async def test_sn3_missing_bytes_are_snapshot_missing(self, snapshot_store_env: SnapshotStoreEnv) -> None:
        env = snapshot_store_env
        ref = await env.store.save(env.scope, ARCHIVE)
        env.lose()
        with pytest.raises(SnapshotUnavailable) as refused:
            await env.store.load(env.scope, ref)
        assert refused.value.reason_code == SNAPSHOT_MISSING

    async def test_sn3_changed_bytes_are_snapshot_corrupt(self, snapshot_store_env: SnapshotStoreEnv) -> None:
        env = snapshot_store_env
        ref = await env.store.save(env.scope, ARCHIVE)
        env.tamper()
        with pytest.raises(SnapshotUnavailable) as refused:
            await env.store.load(env.scope, ref)
        assert refused.value.reason_code == SNAPSHOT_CORRUPT

    async def test_sn4_another_sessions_snapshot_is_never_read(self, snapshot_store_env: SnapshotStoreEnv) -> None:
        env = snapshot_store_env
        theirs = await env.store.save(env.other_scope, ARCHIVE)
        with pytest.raises(SnapshotUnavailable) as refused:
            await env.store.load(env.scope, theirs)
        assert refused.value.reason_code in (SNAPSHOT_FOREIGN, SNAPSHOT_CORRUPT)

    async def test_sn5_a_shredded_session_key_makes_snapshots_unreadable(
        self, snapshot_store_env: SnapshotStoreEnv
    ) -> None:
        env = snapshot_store_env
        ref = await env.store.save(env.scope, ARCHIVE)
        if not await env.shred():
            pytest.skip("this store does not seal")
        with pytest.raises(SnapshotUnavailable) as refused:
            await env.store.load(env.scope, ref)
        assert refused.value.reason_code == SNAPSHOT_MISSING

    async def test_sn6_the_backing_store_holds_no_plaintext(self, snapshot_store_env: SnapshotStoreEnv) -> None:
        env = snapshot_store_env
        marker = b"pxh-plaintext-marker-" + bytes(range(65, 91))
        _ = await env.store.save(env.scope, tar_of({"tmp/.cache/m.txt": marker * 4}))
        stored = env.stored()
        if stored is None or not await env.shred():
            pytest.skip("this store does not seal, or its bytes cannot be read directly")
        assert stored
        assert all(marker not in blob and b"tmp/.cache" not in blob for blob in stored)


POLICY = SnapshotPolicy(("/tmp/.cache", "/tmp/pxh-snap-b"), max_bytes=256 * 1024)


class CaptureEnv(Protocol):
    async def sandbox(self) -> SandboxHandle:
        """A fresh, running sandbox with a writable ``/workspace`` mount and the broker's ``/tmp``."""
        ...

    async def write(self, sandbox: SandboxHandle, path: str, data: bytes, *, mode: int = 0o644) -> None:
        """Write a file inside the sandbox as its own processes would (parents created)."""
        ...

    async def mkdir(self, sandbox: SandboxHandle, path: str) -> None: ...

    async def read(self, sandbox: SandboxHandle, path: str) -> bytes | None:
        """The file's bytes, ``None`` when it does not exist."""
        ...

    async def mode(self, sandbox: SandboxHandle, path: str) -> int:
        """The file's permission bits."""
        ...

    async def kill(self, sandbox: SandboxHandle) -> None:
        """Stop the sandbox the way a crash does (its tmpfs goes with it)."""
        ...


def _snapshots(store: SandboxSnapshotStore, scope: ObjectScope) -> SandboxSnapshots:
    return SandboxSnapshots(
        store=store, policy=POLICY, scope=scope, secrets=tuple, log=HarnessLog(logging.getLogger("snapshot.contract"))
    )


class SnapshotCaptureContract:
    @pytest.fixture
    def capture_env(self) -> CaptureEnv:
        raise NotImplementedError("provide a capture_env fixture")

    async def test_sc1_capture_holds_the_policys_paths_only(self, capture_env: CaptureEnv) -> None:
        env = capture_env
        sandbox = await env.sandbox()
        assert await capture(sandbox, POLICY) is None
        await env.write(sandbox, "/tmp/.cache/tool/index.json", b'{"warm": true}\n')
        await env.write(sandbox, "/tmp/.cache/tool/run.sh", b"#!/bin/sh\n", mode=0o755)
        await env.mkdir(sandbox, "/tmp/.cache/empty")
        await env.write(sandbox, "/tmp/elsewhere.txt", b"not in the policy\n")
        await env.write(sandbox, "/workspace/keep.txt", b"workspace\n")
        data = await capture(sandbox, POLICY)
        assert data is not None
        archive = read_archive(data, POLICY)
        files = {e.name: e for e in archive.entries if e.data is not None}
        folders = {e.name for e in archive.entries if e.data is None}
        assert set(files) == {"tmp/.cache/tool/index.json", "tmp/.cache/tool/run.sh"}
        assert files["tmp/.cache/tool/index.json"].data == b'{"warm": true}\n'
        assert files["tmp/.cache/tool/run.sh"].mode & 0o777 == 0o755
        assert "tmp/.cache/empty" in folders

    async def test_sc2_restore_into_a_fresh_sandbox(self, capture_env: CaptureEnv) -> None:
        env = capture_env
        first = await env.sandbox()
        await env.write(first, "/tmp/.cache/tool/index.json", b'{"warm": true}\n')
        await env.write(first, "/tmp/pxh-snap-b/deep/er/run.sh", b"#!/bin/sh\n", mode=0o755)
        data = await capture(first, POLICY)
        assert data is not None
        second = await env.sandbox()
        await restore(second, read_archive(data, POLICY))
        assert await env.read(second, "/tmp/.cache/tool/index.json") == b'{"warm": true}\n'
        assert await env.read(second, "/tmp/pxh-snap-b/deep/er/run.sh") == b"#!/bin/sh\n"
        assert await env.mode(second, "/tmp/pxh-snap-b/deep/er/run.sh") & 0o777 == 0o755

    async def test_sc3_a_capture_over_the_cap_is_refused(self, capture_env: CaptureEnv) -> None:
        env = capture_env
        sandbox = await env.sandbox()
        await env.write(sandbox, "/tmp/.cache/big.bin", b"\x00" * (POLICY.max_bytes * 2))
        with pytest.raises(SnapshotRefused) as refused:
            data = await capture(sandbox, POLICY)
            # A tar a little over the cap is read whole; its files' total is what refuses it.
            assert data is not None
            _ = read_archive(data, POLICY)
        assert refused.value.reason_code == SNAPSHOT_TOO_LARGE
        # The sandbox still answers: the capture stopped, nothing else did.
        await env.write(sandbox, "/tmp/.cache/after.txt", b"ok")
        assert await env.read(sandbox, "/tmp/.cache/after.txt") == b"ok"

    async def test_sc4_a_sandbox_that_is_gone_gives_no_snapshot(self, capture_env: CaptureEnv) -> None:
        env = capture_env
        sandbox = await env.sandbox()
        await env.write(sandbox, "/tmp/.cache/tool/index.json", b'{"warm": true}\n')
        await env.kill(sandbox)
        with pytest.raises(SnapshotRefused) as refused:
            _ = await capture(sandbox, POLICY)
        assert refused.value.reason_code == SNAPSHOT_SANDBOX_GONE

    async def test_sc5_a_hostile_snapshot_writes_nothing(self, capture_env: CaptureEnv) -> None:
        env = capture_env
        sandbox = await env.sandbox()
        store = ObjectStoreSandboxSnapshots(InMemoryBinaryObjectStore())
        scope = ObjectScope("org-1", "ws:session-1")
        hostile = tar_of({"tmp/.cache/ok.txt": b"ok", "workspace/evil.txt": b"evil"})
        ref = await store.save(scope, hostile)
        assert not await _snapshots(store, scope).restore_into(sandbox, ref)
        assert await env.read(sandbox, "/workspace/evil.txt") is None
        assert await env.read(sandbox, "/tmp/.cache/ok.txt") is None
        with pytest.raises(SnapshotRefused) as refused:
            _ = read_archive(hostile, POLICY)
        assert refused.value.reason_code == SNAPSHOT_UNSAFE


__all__ = [
    "ARCHIVE",
    "OTHER",
    "POLICY",
    "CaptureEnv",
    "SandboxSnapshotStoreContract",
    "SnapshotCaptureContract",
    "SnapshotStoreEnv",
    "tar_of",
]
