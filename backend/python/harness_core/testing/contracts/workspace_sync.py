"""Contract suite every ``WorkspaceSync`` must pass, on every mount strategy and broker (P5.5 WY-1..WY-12).

Subclass :class:`WorkspaceSyncContract` (or :class:`PersistentWorkspaceSyncContract`
for a strategy whose folder outlives the sandbox) and provide a ``harness``
fixture (:class:`WorkspaceSyncHarness`). ``EngineHands`` change the folder the
way an engine would, from inside the sandbox; the suite reads the folder back
through ``get_tar`` independently of the sync. Skips belong in fixtures.
"""

from __future__ import annotations

import io
import stat
import tarfile
import uuid
from typing import TYPE_CHECKING, Protocol

import pytest

from harness_core.sandbox.streams import collect as read_stream
from harness_core.sandbox.types import LeaseRef
from harness_core.workspace.changes import ChangeSet
from harness_core.workspace.sync.types import ENGINE_CONFIG
from harness_core.workspace.types import (
    EXECUTABLE_MODE,
    FILE_MODE,
    SYMLINK_MODE,
    WorkspaceFile,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.sandbox.ports import SandboxBroker, SandboxHandle
    from harness_core.sandbox.types import SandboxSpec
    from harness_core.testing.sandbox.fake import FakeSandboxHandle
    from harness_core.workspace.ports import (
        BinaryObjectStore,
        WorkspaceStore,
        WorkspaceSync,
    )
    from harness_core.workspace.sync.types import SyncState
    from harness_core.workspace.types import ObjectRef, ObjectScope, WorkspaceManifest

LEASE = LeaseRef("sync-contract", 1)
WORKSPACE = "ws:session-sync"
PLUGIN = b"export default async () => { throw new Error('planted plug-in ran') }"

Tree = dict[str, tuple[int, bytes]]


class CountingObjectStore:
    """A ``BinaryObjectStore`` that counts reads and writes, so a case can assert what was not done."""

    def __init__(self, inner: BinaryObjectStore) -> None:
        self.inner = inner
        self.puts = 0
        self.gets = 0

    async def put(self, scope: ObjectScope, content: bytes) -> ObjectRef:
        self.puts += 1
        return await self.inner.put(scope, content)

    async def get(self, scope: ObjectScope, ref: ObjectRef) -> bytes:
        self.gets += 1
        return await self.inner.get(scope, ref)

    async def content_hash(self, scope: ObjectScope, content: bytes) -> str:
        return await self.inner.content_hash(scope, content)

    def reset(self) -> None:
        self.puts = self.gets = 0


class EngineHands(Protocol):
    """Changes a sandbox folder from inside, as an engine's tools would; paths are absolute."""

    async def write(self, sandbox: SandboxHandle, path: str, data: bytes, mode: int = 0o644) -> None: ...

    async def symlink(self, sandbox: SandboxHandle, path: str, target: str) -> None: ...

    async def remove(self, sandbox: SandboxHandle, path: str) -> None: ...

    async def mkdir(self, sandbox: SandboxHandle, path: str) -> None: ...


class FakeEngineHands:
    """``EngineHands`` for :class:`~harness_core.testing.sandbox.fake.FakeSandboxBroker` sandboxes."""

    async def write(self, sandbox: SandboxHandle, path: str, data: bytes, mode: int = 0o644) -> None:
        _fake(sandbox).put_file(path, data, mode)

    async def symlink(self, sandbox: SandboxHandle, path: str, target: str) -> None:
        _fake(sandbox).put_symlink(path, target)

    async def remove(self, sandbox: SandboxHandle, path: str) -> None:
        state = _fake(sandbox).state
        prefix = path + "/"
        for name in [n for n in state.files if n == path or n.startswith(prefix)]:
            del state.files[name]
        state.dirs.difference_update({d for d in state.dirs if d == path or d.startswith(prefix)})

    async def mkdir(self, sandbox: SandboxHandle, path: str) -> None:
        _fake(sandbox).make_dir(path)


def _fake(sandbox: SandboxHandle) -> FakeSandboxHandle:
    from harness_core.testing.sandbox.fake import FakeSandboxHandle

    assert isinstance(sandbox, FakeSandboxHandle)
    return sandbox


class WorkspaceSyncHarness(Protocol):
    @property
    def sync(self) -> WorkspaceSync: ...

    @property
    def objects(self) -> CountingObjectStore:
        """The store the sync was built with."""
        ...

    @property
    def object_scope(self) -> ObjectScope: ...

    @property
    def store(self) -> WorkspaceStore: ...

    @property
    def scope(self) -> ObjectScope: ...

    @property
    def broker(self) -> SandboxBroker: ...

    @property
    def hands(self) -> EngineHands: ...

    @property
    def root(self) -> str: ...

    def spec(self) -> SandboxSpec:
        """A spec with the strategy's workspace mount at ``root``; every call names the same session."""
        ...


async def acquire(h: WorkspaceSyncHarness) -> SandboxHandle:
    return await h.broker.acquire(h.spec(), lease=LEASE, idempotency_key=f"sync-{uuid.uuid4().hex}")


async def commit(
    h: WorkspaceSyncHarness,
    files: Mapping[str, bytes],
    *,
    modes: Mapping[str, int] | None = None,
    links: Mapping[str, str] | None = None,
    deletes: frozenset[str] = frozenset(),
) -> WorkspaceManifest:
    """A new head with ``files`` (and ``links``) written over the current one."""
    upserts: dict[str, WorkspaceFile] = {}
    for path, data in files.items():
        ref = await h.objects.inner.put(h.object_scope, data)
        upserts[path] = WorkspaceFile(path, ref, "application/octet-stream", (modes or {}).get(path, FILE_MODE))
    for path, target in (links or {}).items():
        ref = await h.objects.inner.put(h.object_scope, target.encode())
        upserts[path] = WorkspaceFile(path, ref, "inode/symlink", SYMLINK_MODE)
    head = await h.store.head(h.scope, WORKSPACE)
    return await h.store.commit(
        h.scope, WORKSPACE, base_revision=head.revision, changes=ChangeSet(upserts, deletes), reason="seed"
    )


async def tree(sandbox: SandboxHandle, root: str) -> Tree:
    """Files and links under ``root`` as the broker hands them out: path → (mode, bytes or link target)."""
    data = await read_stream(await sandbox.get_tar(root))
    base = root.rstrip("/").rsplit("/", 1)[-1] + "/"
    found: Tree = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
        for member in archive:
            if not member.name.startswith(base):
                continue
            path = member.name[len(base) :]
            if member.issym():
                found[path] = (SYMLINK_MODE, member.linkname.encode())
            elif member.isfile():
                extracted = archive.extractfile(member)
                found[path] = (stat.S_IFREG | (member.mode & 0o777), extracted.read() if extracted else b"")
    return found


async def folders(sandbox: SandboxHandle, root: str) -> set[str]:
    data = await read_stream(await sandbox.get_tar(root))
    base = root.rstrip("/").rsplit("/", 1)[-1] + "/"
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
        return {m.name[len(base) :].rstrip("/") for m in archive if m.isdir() and m.name.startswith(base)}


async def expected(h: WorkspaceSyncHarness, manifest: WorkspaceManifest, *, hidden: Mapping[str, str]) -> Tree:
    out: Tree = {}
    for path, entry in manifest.files.items():
        if path not in hidden:
            out[path] = (entry.mode, await h.objects.inner.get(h.object_scope, entry.content))
    return out


SAMPLE = {
    "README.md": b"# workspace\n",
    "src/app.py": b"print('hi')\n",
    "src/deep/nested/data.bin": bytes(range(256)) * 4,
    "empty.txt": b"",
    "bin/run.sh": b"#!/bin/sh\necho run\n",
    "out/report.csv": b"a,b\n1,2\n",
}
SAMPLE_MODES = {"bin/run.sh": EXECUTABLE_MODE}
SAMPLE_LINKS = {"latest": "out/report.csv", "src/up": "../README.md"}


class WorkspaceSyncContract:
    @pytest.fixture
    def harness(self) -> WorkspaceSyncHarness:
        raise NotImplementedError("provide a harness fixture")

    async def _seeded(self, h: WorkspaceSyncHarness) -> tuple[SandboxHandle, SyncState]:
        manifest = await commit(h, SAMPLE, modes=SAMPLE_MODES, links=SAMPLE_LINKS)
        sandbox = await acquire(h)
        return sandbox, await h.sync.materialize(sandbox, manifest, root=h.root)

    async def test_wy1_a_revision_appears_with_its_bytes_modes_and_links(self, harness: WorkspaceSyncHarness) -> None:
        sandbox, state = await self._seeded(harness)
        try:
            assert state.manifest.revision == 1
            assert state.hidden == {}
            assert await tree(sandbox, harness.root) == await expected(harness, state.manifest, hidden={})
        finally:
            await harness.broker.release(sandbox)

    async def test_wy2_nothing_changed_collects_nothing_and_uploads_nothing(
        self, harness: WorkspaceSyncHarness
    ) -> None:
        sandbox, state = await self._seeded(harness)
        try:
            harness.objects.reset()
            collected = await harness.sync.collect(sandbox, state)
            assert collected.changes.is_empty
            assert collected.skipped == {}
            assert (harness.objects.puts, collected.uploaded) == (0, 0)
        finally:
            await harness.broker.release(sandbox)

    async def test_wy3_engine_writes_become_exactly_those_changes(self, harness: WorkspaceSyncHarness) -> None:
        sandbox, state = await self._seeded(harness)
        root, hands = harness.root, harness.hands
        try:
            await hands.write(sandbox, f"{root}/notes/new.md", b"new file")
            await hands.write(sandbox, f"{root}/README.md", b"# changed\n")
            await hands.remove(sandbox, f"{root}/empty.txt")
            await hands.write(sandbox, f"{root}/src/app.py", SAMPLE["src/app.py"], 0o755)
            await hands.symlink(sandbox, f"{root}/notes/link", "new.md")
            harness.objects.reset()
            collected = await harness.sync.collect(sandbox, state)
            changes = collected.changes
            assert set(changes.upserts) == {"notes/new.md", "README.md", "src/app.py", "notes/link"}
            assert changes.deletes == frozenset({"empty.txt"})
            assert changes.upserts["src/app.py"].mode == EXECUTABLE_MODE
            assert changes.upserts["src/app.py"].content == state.manifest.files["src/app.py"].content
            assert changes.upserts["notes/link"].mode == SYMLINK_MODE
            assert collected.uploaded == harness.objects.puts == 3
        finally:
            await harness.broker.release(sandbox)

    async def test_wy4_a_fresh_sandbox_gets_the_committed_tree(self, harness: WorkspaceSyncHarness) -> None:
        sandbox, state = await self._seeded(harness)
        root, hands = harness.root, harness.hands
        try:
            await hands.write(sandbox, f"{root}/src/new.py", b"x = 1\n", 0o700)
            await hands.remove(sandbox, f"{root}/out/report.csv")
            await hands.remove(sandbox, f"{root}/latest")
            collected = await harness.sync.collect(sandbox, state)
            head = await harness.store.commit(
                harness.scope, WORKSPACE, base_revision=state.revision, changes=collected.changes, reason="autosave"
            )
            first = await tree(sandbox, root)
        finally:
            await harness.broker.release(sandbox)
        fresh = await acquire(harness)
        try:
            _ = await harness.sync.materialize(fresh, head, root=root)
            assert await tree(fresh, root) == first
            assert first["src/new.py"] == (0o100700, b"x = 1\n")
        finally:
            await harness.broker.release(fresh)

    async def test_wy5_known_content_is_never_uploaded_again(self, harness: WorkspaceSyncHarness) -> None:
        sandbox, state = await self._seeded(harness)
        root, hands = harness.root, harness.hands
        try:
            await hands.write(sandbox, f"{root}/copy.py", SAMPLE["src/app.py"])
            await hands.write(sandbox, f"{root}/README.md", b"temporary")
            harness.objects.reset()
            collected = await harness.sync.collect(sandbox, state)
            assert collected.changes.upserts["copy.py"].content == state.manifest.files["src/app.py"].content
            assert harness.objects.puts == 1
            await hands.write(sandbox, f"{root}/README.md", SAMPLE["README.md"])
            harness.objects.reset()
            again = await harness.sync.collect(sandbox, state)
            assert set(again.changes.upserts) == {"copy.py"}
            assert harness.objects.puts == 0
        finally:
            await harness.broker.release(sandbox)

    async def test_wy6_a_mount_holding_another_revision_gets_only_the_difference(
        self, harness: WorkspaceSyncHarness
    ) -> None:
        first = await commit(harness, {"a.txt": b"a", "b.txt": b"b", "keep/c.txt": b"c", "gone.txt": b"g"})
        second = await commit(
            harness, {"a.txt": b"a2", "new/d.txt": b"d"}, modes={}, deletes=frozenset({"gone.txt"})
        )
        sandbox = await acquire(harness)
        try:
            _ = await harness.sync.materialize(sandbox, first, root=harness.root)
            await harness.hands.write(sandbox, f"{harness.root}/stray.txt", b"not in any revision")
            harness.objects.reset()
            state = await harness.sync.materialize(sandbox, second, root=harness.root)
            assert harness.objects.gets == 2
            assert await tree(sandbox, harness.root) == await expected(harness, second, hidden=state.hidden)
        finally:
            await harness.broker.release(sandbox)

    async def test_wy7_engine_configuration_stays_in_the_store(self, harness: WorkspaceSyncHarness) -> None:
        config = {
            ".opencode/plugin/x.ts": PLUGIN,
            ".claude/settings.json": b'{"hooks": {}}',
            ".claude/commands/c.md": b"run",
            ".codex/config.toml": b"model = 'x'",
            "opencode.json": b"{}",
            "pkg/.mcp.json": b"{}",
        }
        manifest = await commit(harness, {**config, "src/main.py": b"main"})
        sandbox = await acquire(harness)
        root = harness.root
        try:
            state = await harness.sync.materialize(sandbox, manifest, root=root)
            assert dict(state.hidden) == dict.fromkeys(config, ENGINE_CONFIG)
            assert set(await tree(sandbox, root)) == {"src/main.py"}
            await harness.hands.write(sandbox, f"{root}/.opencode/plugin/y.ts", PLUGIN)
            await harness.hands.write(sandbox, f"{root}/.claude/settings.local.json", b"{}")
            collected = await harness.sync.collect(sandbox, state)
            assert collected.changes.is_empty
            assert collected.skipped == {
                ".opencode/plugin/y.ts": ENGINE_CONFIG,
                ".claude/settings.local.json": ENGINE_CONFIG,
            }
        finally:
            await harness.broker.release(sandbox)

    async def test_wy8_a_planted_plugin_never_reaches_the_engine(self, harness: WorkspaceSyncHarness) -> None:
        manifest = await commit(harness, {".opencode/plugin/x.ts": PLUGIN, "a.txt": b"a"})
        sandbox = await acquire(harness)
        root = harness.root
        try:
            await harness.hands.write(sandbox, f"{root}/.opencode/plugin/planted.ts", PLUGIN)
            await harness.hands.write(sandbox, f"{root}/sub/.codex/config.toml", b"x")
            _ = await harness.sync.materialize(sandbox, manifest, root=root)
            found = await tree(sandbox, root)
            assert set(found) == {"a.txt"}
            assert not any(".opencode" in f or ".codex" in f for f in await folders(sandbox, root))
        finally:
            await harness.broker.release(sandbox)

    async def test_wy9_empty_folders_are_not_versioned(self, harness: WorkspaceSyncHarness) -> None:
        sandbox, state = await self._seeded(harness)
        try:
            await harness.hands.mkdir(sandbox, f"{harness.root}/empty/inner")
            assert (await harness.sync.collect(sandbox, state)).changes.is_empty
        finally:
            await harness.broker.release(sandbox)

    async def test_wy10_pending_counts_changes_without_uploading(self, harness: WorkspaceSyncHarness) -> None:
        sandbox, state = await self._seeded(harness)
        root = harness.root
        try:
            await harness.hands.write(sandbox, f"{root}/one.txt", b"1")
            await harness.hands.write(sandbox, f"{root}/README.md", b"2")
            await harness.hands.remove(sandbox, f"{root}/empty.txt")
            harness.objects.reset()
            assert await harness.sync.pending(sandbox, state) == 3
            assert harness.objects.puts == 0
        finally:
            await harness.broker.release(sandbox)

    async def test_wy11_materializing_again_writes_nothing(self, harness: WorkspaceSyncHarness) -> None:
        sandbox, state = await self._seeded(harness)
        try:
            harness.objects.reset()
            again = await harness.sync.materialize(sandbox, state.manifest, root=harness.root)
            assert again == state
            assert harness.objects.gets == len(SAMPLE_LINKS)
        finally:
            await harness.broker.release(sandbox)

    async def test_wy12_files_and_folders_trade_places(self, harness: WorkspaceSyncHarness) -> None:
        manifest = await commit(harness, {"a": b"file a", "d/x": b"x"})
        sandbox = await acquire(harness)
        root, hands = harness.root, harness.hands
        try:
            state = await harness.sync.materialize(sandbox, manifest, root=root)
            await hands.remove(sandbox, f"{root}/a")
            await hands.write(sandbox, f"{root}/a/inner", b"now a folder")
            await hands.remove(sandbox, f"{root}/d")
            await hands.write(sandbox, f"{root}/d", b"now a file")
            changes = (await harness.sync.collect(sandbox, state)).changes
            assert set(changes.upserts) == {"a/inner", "d"}
            assert changes.deletes == frozenset({"a", "d/x"})
            head = await harness.store.commit(
                harness.scope, WORKSPACE, base_revision=state.revision, changes=changes, reason="autosave"
            )
            _ = await harness.sync.materialize(sandbox, manifest, root=root)
            assert await tree(sandbox, root) == await expected(harness, manifest, hidden={})
            _ = await harness.sync.materialize(sandbox, head, root=root)
            assert await tree(sandbox, root) == await expected(harness, head, hidden={})
        finally:
            await harness.broker.release(sandbox)


class PersistentWorkspaceSyncContract(WorkspaceSyncContract):
    """For a strategy whose folder outlives the sandbox: the next sandbox of the session finds it."""

    async def test_wy6_the_next_sandbox_gets_only_what_changed(self, harness: WorkspaceSyncHarness) -> None:
        first = await commit(harness, {f"f{i}.txt": f"{i}".encode() for i in range(20)})
        second = await commit(harness, {"f3.txt": b"changed", "added.txt": b"added"})
        sandbox = await acquire(harness)
        try:
            _ = await harness.sync.materialize(sandbox, first, root=harness.root)
        finally:
            await harness.broker.release(sandbox)
        later = await acquire(harness)
        try:
            harness.objects.reset()
            state = await harness.sync.materialize(later, second, root=harness.root)
            assert harness.objects.gets == 2
            assert await tree(later, harness.root) == await expected(harness, second, hidden=state.hidden)
        finally:
            await harness.broker.release(later)

    async def test_wy8_a_plugin_planted_by_an_earlier_sandbox_is_removed(self, harness: WorkspaceSyncHarness) -> None:
        manifest = await commit(harness, {"a.txt": b"a"})
        sandbox = await acquire(harness)
        try:
            _ = await harness.sync.materialize(sandbox, manifest, root=harness.root)
            await harness.hands.write(sandbox, f"{harness.root}/.opencode/plugin/planted.ts", PLUGIN)
        finally:
            await harness.broker.release(sandbox)
        later = await acquire(harness)
        try:
            assert ".opencode/plugin/planted.ts" in await tree(later, harness.root)
            _ = await harness.sync.materialize(later, manifest, root=harness.root)
            assert set(await tree(later, harness.root)) == {"a.txt"}
        finally:
            await harness.broker.release(later)


__all__ = [
    "SAMPLE",
    "SAMPLE_LINKS",
    "SAMPLE_MODES",
    "WORKSPACE",
    "CountingObjectStore",
    "EngineHands",
    "FakeEngineHands",
    "PersistentWorkspaceSyncContract",
    "WorkspaceSyncContract",
    "WorkspaceSyncHarness",
    "acquire",
    "commit",
    "expected",
    "folders",
    "tree",
]
