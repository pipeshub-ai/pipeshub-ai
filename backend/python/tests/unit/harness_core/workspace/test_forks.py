"""P6.4: the writer lease and the child merge on the in-memory adapters (WL, CW),
the merge table (MT-1..MT-10) and forks sharing their origin's objects (WF-9)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from harness_core.journal.types import AttemptToken
from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.workspace import file
from harness_core.testing.contracts.workspace_forks import (
    ChildWorkspacesContract,
    WorkspaceWritersContract,
)
from harness_core.workspace import (
    EXECUTABLE_MODE,
    SYMLINK_MODE,
    ChangeSet,
    ObjectRef,
    ObjectScope,
    WorkspaceFile,
    plan_merge,
)
from harness_core.workspace.forks import (
    ChildMergeReport,
    StoreChildWorkspaces,
    child_fork_id,
    session_workspace_id,
)
from harness_core.workspace.memory import (
    InMemoryBinaryObjectStore,
    InMemoryWorkspaceStore,
    InMemoryWorkspaceWriters,
)

_START = datetime(2026, 10, 3, tzinfo=UTC)


class _WritersHarness:
    def __init__(self) -> None:
        self.clock = ManualClock(_START)
        self.writers = InMemoryWorkspaceWriters(clock=self.clock)
        self.scope = ObjectScope("tenant-a", "ws:s1")
        self.ttl_ms = 30_000

    def holders(self) -> tuple[tuple[InMemoryWorkspaceWriters, str], tuple[InMemoryWorkspaceWriters, str]]:
        return (self.writers, "run-a"), (self.writers, "run-b")

    def workspace_id(self, name: str) -> str:
        return f"ws:s1:{name}"

    def other_tenant(self) -> tuple[InMemoryWorkspaceWriters, ObjectScope]:
        return self.writers, ObjectScope("tenant-b", "ws:s1")

    async def elapse(self, ms: int) -> None:
        _ = self.clock.advance(ms / 1000)


class TestInMemoryWorkspaceWriters(WorkspaceWritersContract):
    @pytest.fixture
    def harness(self) -> _WritersHarness:  # type: ignore[override]
        return _WritersHarness()

    async def test_a_lease_needs_a_holder_and_a_ttl(self, harness: _WritersHarness) -> None:
        with pytest.raises(ValueError, match="holder"):
            _ = await harness.writers.acquire(harness.scope, "ws:s1", holder="", ttl_ms=1)
        with pytest.raises(ValueError, match="TTL"):
            _ = await harness.writers.acquire(harness.scope, "ws:s1", holder="run-a", ttl_ms=0)


class _ChildHarness:
    def __init__(self) -> None:
        clock = ManualClock(_START)
        self.store = InMemoryWorkspaceStore(clock=clock)
        self.writers = InMemoryWorkspaceWriters(clock=clock)
        self.merger = StoreChildWorkspaces(lambda _token: (self.store, self.writers))
        self.tenant_id = "tenant-a"
        self.session_id = "s1"
        self.parent_run_id = "parent-run"
        self.token = AttemptToken(self.parent_run_id, "attempt-1", 1)

    def child_run_id(self, name: str) -> str:
        return f"child-{name}"

    async def hold_elsewhere(self, workspace_id: str) -> bool:
        scope = ObjectScope(self.tenant_id, session_workspace_id(self.session_id))
        _ = await self.writers.acquire(scope, workspace_id, holder="another-run", ttl_ms=60_000)
        return True


class TestStoreChildWorkspaces(ChildWorkspacesContract):
    @pytest.fixture
    def harness(self) -> _ChildHarness:  # type: ignore[override]
        return _ChildHarness()

    async def test_a_parent_holding_its_own_lease_keeps_it(self, harness: _ChildHarness) -> None:
        child = self._child(harness, "own")
        await self._fork(harness, child)
        _ = await self._commit(harness, child.fork_id, file("x.txt", "x"))
        scope = ObjectScope(harness.tenant_id, session_workspace_id(harness.session_id))
        _ = await harness.writers.acquire(scope, child.workspace_id, holder=harness.parent_run_id, ttl_ms=60_000)
        report = await harness.merger.merge(harness.token, child)
        assert report is not None and report.status == "merged"
        with pytest.raises(Exception, match="being written by parent-run"):
            _ = await harness.writers.acquire(scope, child.workspace_id, holder="other", ttl_ms=1)

    async def test_the_merge_releases_a_lease_it_took(self, harness: _ChildHarness) -> None:
        child = self._child(harness, "released")
        await self._fork(harness, child)
        _ = await self._commit(harness, child.fork_id, file("y.txt", "y"))
        _ = await harness.merger.merge(harness.token, child)
        scope = ObjectScope(harness.tenant_id, session_workspace_id(harness.session_id))
        assert (await harness.writers.acquire(scope, child.workspace_id, holder="other", ttl_ms=1)).holder == "other"


def test_fork_ids_are_flat_under_the_session() -> None:
    assert child_fork_id("s1", "c-1") == "ws:s1:fork:c-1"
    assert session_workspace_id("s1") == "ws:s1"


def test_the_report_is_bounded_and_names_the_forks_revision() -> None:
    report = ChildMergeReport(
        child_run_id="c",
        fork_id="ws:s:fork:c",
        into="ws:s",
        status="partial",
        revision=4,
        fork_revision=3,
        merged=tuple(f"m{i}" for i in range(60)),
        conflicts=(("a.txt", "both_changed"),),
    )
    shown = report.as_json()
    assert len(shown["merged"]) == 50 and shown["truncated"] is True  # type: ignore[arg-type]
    assert shown["fork"] == {"workspaceId": "ws:s:fork:c", "revision": 3}
    assert "a.txt (both_changed)" in report.summary()
    clean = ChildMergeReport("c", "ws:s:fork:c", "ws:s", "merged", 2, 1, ("a",))
    assert clean.as_json() == {"status": "merged", "merged": ["a"]}


# ---- MT: the merge table, on ``plan_merge`` (pure) ----


def _binary(path: str, data: bytes) -> WorkspaceFile:
    digest = hashlib.sha256(data).hexdigest()
    return WorkspaceFile(path, ObjectRef(f"obj-{digest}", f"sha256:{digest}", len(data)), "application/octet-stream")


def _link(path: str, target: str) -> WorkspaceFile:
    entry = file(path, target, "inode/symlink")
    return WorkspaceFile(entry.path, entry.content, entry.mime_type, SYMLINK_MODE)


def _exe(path: str, text: str) -> WorkspaceFile:
    entry = file(path, text)
    return WorkspaceFile(entry.path, entry.content, entry.mime_type, EXECUTABLE_MODE)


def _files(*entries: WorkspaceFile) -> dict[str, WorkspaceFile]:
    return {e.path: e for e in entries}


_A = file("a.txt", "a")
_PNG_1 = _binary("img.png", b"\x89PNG\x00\xff\x01")
_PNG_2 = _binary("img.png", b"\x89PNG\x00\xff\x02")
_PNG_3 = _binary("img.png", b"\x89PNG\x00\xff\x03")

# (id, base, ours, theirs, upserts, deletes, conflicts as (path, kind), repositories)
MERGE_TABLE: list[tuple[str, dict[str, WorkspaceFile], dict[str, WorkspaceFile], dict[str, WorkspaceFile], set[str], set[str], list[tuple[str, str]], tuple[str, ...]]] = [
    ("MT-1 text on the fork", _files(_A), _files(_A), _files(file("a.txt", "b")), {"a.txt"}, set(), [], ()),
    ("MT-2 binary on the fork", _files(_PNG_1), _files(_PNG_1), _files(_PNG_2), {"img.png"}, set(), [], ()),
    ("MT-2 binary on both", _files(_PNG_1), _files(_PNG_2), _files(_PNG_3), set(), set(), [("img.png", "both_changed")], ()),
    ("MT-3 rename on the fork", _files(_A), _files(_A), _files(file("b.txt", "a")), {"b.txt"}, {"a.txt"}, [], ()),
    (
        "MT-4 rename on the fork, the target changed the old path",
        _files(_A),
        _files(file("a.txt", "parent")),
        _files(file("b.txt", "a")),
        {"b.txt"},
        set(),
        [("a.txt", "fork_deleted")],
        (),
    ),
    ("MT-5 add/add, same", {}, _files(_A), _files(_A), set(), set(), [], ()),
    ("MT-5 add/add, different", {}, _files(_A), _files(file("a.txt", "z")), set(), set(), [("a.txt", "both_changed")], ()),
    ("MT-6 a placeholder for an empty directory", {}, {}, _files(file("empty/.keep", "")), {"empty/.keep"}, set(), [], ()),
    ("MT-7 a link the fork adds", {}, {}, _files(_link("latest", "out/v2.md")), {"latest"}, set(), [], ()),
    (
        "MT-7 a link changed on both",
        _files(_link("latest", "v1")),
        _files(_link("latest", "v2")),
        _files(_link("latest", "v3")),
        set(),
        set(),
        [("latest", "both_changed")],
        (),
    ),
    ("MT-7 link vs file", {}, _files(_link("x", "y")), _files(file("x", "y")), set(), set(), [("x", "both_changed")], ()),
    ("MT-8 chmod +x on the fork", _files(file("run.sh", "s")), _files(file("run.sh", "s")), _files(_exe("run.sh", "s")), {"run.sh"}, set(), [], ()),
    (
        "MT-8 different modes on both",
        _files(file("run.sh", "s")),
        _files(_exe("run.sh", "s")),
        _files(WorkspaceFile("run.sh", file("run.sh", "s").content, "text/plain", 0o100600)),
        set(),
        set(),
        [("run.sh", "both_changed")],
        (),
    ),
    (
        "MT-9 a repository both touched",
        _files(file(".git/HEAD", "main")),
        _files(file(".git/HEAD", "main"), file(".git/refs/l", "1")),
        _files(file(".git/HEAD", "main"), file(".git/refs/r", "2"), file("n.md", "n")),
        {"n.md"},
        set(),
        [],
        (".git",),
    ),
    ("MT-10 the target deleted, the fork changed", _files(_A), {}, _files(file("a.txt", "b")), set(), set(), [("a.txt", "target_deleted")], ()),
]


@pytest.mark.parametrize(
    ("base", "ours", "theirs", "upserts", "deletes", "conflicts", "repositories"),
    [row[1:] for row in MERGE_TABLE],
    ids=[row[0] for row in MERGE_TABLE],
)
def test_merge_table(
    base: dict[str, WorkspaceFile],
    ours: dict[str, WorkspaceFile],
    theirs: dict[str, WorkspaceFile],
    upserts: set[str],
    deletes: set[str],
    conflicts: list[tuple[str, str]],
    repositories: tuple[str, ...],
) -> None:
    plan = plan_merge(base, ours, theirs)
    assert set(plan.changes.upserts) == upserts
    assert set(plan.changes.deletes) == deletes
    assert [(c.path, c.kind) for c in plan.conflicts] == conflicts
    assert plan.repository_conflicts == repositories
    for path in upserts:
        merged = plan.changes.upserts[path]
        assert merged.same_content(theirs[path]) and merged.mode == theirs[path].mode


async def test_mt7_a_merged_link_is_an_entry_its_target_is_never_read() -> None:
    store = InMemoryWorkspaceStore(clock=ManualClock(_START))
    scope = ObjectScope("t", "ws:s")
    _ = await store.commit(scope, "ws:s", base_revision=0, changes=ChangeSet.of(_A), reason="seed")
    _ = await store.fork(scope, "ws:s", at_revision=1, fork_id="ws:s:fork:c")
    escaping = _link("etc", "/etc/passwd")
    _ = await store.commit(scope, "ws:s:fork:c", base_revision=1, changes=ChangeSet.of(escaping), reason="autosave")
    result = await store.merge(scope, fork_id="ws:s:fork:c", into="ws:s", on_conflict="keep_target")
    entry = result.manifest.files["etc"]
    assert (entry.mode, entry.content) == (SYMLINK_MODE, escaping.content)
    assert set(result.manifest.files) == {"a.txt", "etc"}


async def test_wf9_a_fork_shares_its_origins_objects() -> None:
    objects = InMemoryBinaryObjectStore()
    store = InMemoryWorkspaceStore(clock=ManualClock(_START))
    scope = ObjectScope("t", "ws:s")
    refs = [await objects.put(scope, data) for data in (b"one", b"two")]
    files = [WorkspaceFile(f"f{i}", ref) for i, ref in enumerate(refs)]
    origin = await store.commit(scope, "ws:s", base_revision=0, changes=ChangeSet.of(*files), reason="seed")
    fork = await store.fork(scope, "ws:s", at_revision=1, fork_id="ws:s:fork:c")
    assert {p: e.content for p, e in fork.files.items()} == {p: e.content for p, e in origin.files.items()}
    assert objects.object_count == 2
