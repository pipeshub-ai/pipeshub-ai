"""Contract suites every workspace adapter must pass (P3.2, ``impl/P3.2-workspace-store.md``).

Subclass a contract in a test module and provide its ``harness`` fixture:

- :class:`WorkspaceStoreContract` (WS-1..WS-19) runs on the in-memory store
  and on any durable adapter (PipesHub's Node routes, a Loom store, ...);
- :class:`BinaryObjectStoreContract` (OS-1..OS-4) on every object store,
  sealed or not;
- :class:`KeyProviderContract` (SK-5) on every key provider.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Protocol

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from harness_core.workspace.changes import ChangeSet, diff_files, diff_manifests
from harness_core.workspace.types import (
    InvalidChangeSet,
    ObjectRef,
    WorkspaceFile,
    WorkspaceForkConflict,
    WorkspaceKeyDestroyed,
    WorkspaceNotAFork,
    WorkspaceRevisionConflict,
    WorkspaceRevisionNotFound,
    normalize_workspace_file_path,
    workspace_manifest_content_hash,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.workspace.changes import MergeResult
    from harness_core.workspace.ports import (
        BinaryObjectStore,
        KeyProvider,
        WorkspaceStore,
    )
    from harness_core.workspace.types import ObjectScope, WorkspaceManifest


def file(path: str, text: str, mime_type: str = "text/plain") -> WorkspaceFile:
    """A file entry whose ref is derived from ``text``; stores do not fetch objects."""
    data = text.encode()
    digest = hashlib.sha256(data).hexdigest()
    return WorkspaceFile(path, ObjectRef(f"obj-{digest}", f"sha256:{digest}", len(data)), mime_type)


def _texts(files: Mapping[str, WorkspaceFile]) -> dict[str, str]:
    return {path: entry.content.content_hash for path, entry in files.items()}


class WorkspaceStoreHarness(Protocol):
    @property
    def store(self) -> WorkspaceStore: ...

    @property
    def scope(self) -> ObjectScope: ...

    def other_tenant(self) -> tuple[WorkspaceStore, ObjectScope]:
        """A store and scope of another tenant that may use the same workspace ids."""
        ...

    def workspace_id(self, name: str) -> str:
        """A workspace id the store accepts for this scope (a host may require a prefix)."""
        ...

    @property
    def property_examples(self) -> int: ...


class WorkspaceStoreContract:
    """Behaviour shared by every ``WorkspaceStore`` (ARCHITECTURE §4.3)."""

    @pytest.fixture
    def harness(self) -> WorkspaceStoreHarness:
        raise NotImplementedError("provide a harness fixture")

    async def _seed(
        self, h: WorkspaceStoreHarness, workspace: str, *files: WorkspaceFile
    ) -> WorkspaceManifest:
        head = await h.store.head(h.scope, workspace)
        return await h.store.commit(
            h.scope,
            workspace,
            base_revision=head.revision,
            changes=ChangeSet.of(*files),
            reason="seed",
        )

    async def _write(
        self, h: WorkspaceStoreHarness, workspace: str, changes: ChangeSet
    ) -> WorkspaceManifest:
        head = await h.store.head(h.scope, workspace)
        return await h.store.commit(
            h.scope, workspace, base_revision=head.revision, changes=changes, reason="autosave"
        )

    async def test_ws1_unknown_workspace_is_revision_zero(self, harness: WorkspaceStoreHarness) -> None:
        workspace = harness.workspace_id("ws1")
        head = await harness.store.head(harness.scope, workspace)
        assert (head.workspace_id, head.revision, head.parent_revision) == (workspace, 0, None)
        assert dict(head.files) == {}
        assert head.content_hash == workspace_manifest_content_hash({})

    async def test_ws2_commit_moves_the_head(self, harness: WorkspaceStoreHarness) -> None:
        workspace = harness.workspace_id("ws2")
        first = await self._seed(harness, workspace, file("a.txt", "a"), file("b.txt", "b"))
        assert (first.revision, first.parent_revision) == (1, None)
        second = await harness.store.commit(
            harness.scope,
            workspace,
            base_revision=1,
            changes=ChangeSet.of(file("c/d.txt", "d"), deletes=frozenset({"a.txt"})),
            reason="turn_end",
        )
        assert (second.revision, second.parent_revision) == (2, 1)
        assert _texts(second.files) == _texts({"b.txt": file("b.txt", "b"), "c/d.txt": file("c/d.txt", "d")})
        assert second.content_hash == workspace_manifest_content_hash(second.files)
        assert await harness.store.head(harness.scope, workspace) == second

    async def test_ws3_stale_base_revision_is_a_conflict(self, harness: WorkspaceStoreHarness) -> None:
        workspace = harness.workspace_id("ws3")
        await self._seed(harness, workspace, file("a.txt", "a"))
        head = await self._write(harness, workspace, ChangeSet.of(file("a.txt", "b")))
        with pytest.raises(WorkspaceRevisionConflict):
            await harness.store.commit(
                harness.scope,
                workspace,
                base_revision=1,
                changes=ChangeSet.of(file("a.txt", "c")),
                reason="autosave",
            )
        assert await harness.store.head(harness.scope, workspace) == head

    async def test_ws4_invalid_change_sets_are_refused(self, harness: WorkspaceStoreHarness) -> None:
        workspace = harness.workspace_id("ws4")
        await self._seed(harness, workspace, file("a.txt", "a"))
        with pytest.raises(InvalidChangeSet):
            await self._write(harness, workspace, ChangeSet(deletes=frozenset({"missing.txt"})))
        assert (await harness.store.head(harness.scope, workspace)).revision == 1
        with pytest.raises(InvalidChangeSet):
            ChangeSet.of(file("a.txt", "x"), deletes=frozenset({"a.txt"}))

    async def test_ws5_empty_change_set_adds_no_revision(self, harness: WorkspaceStoreHarness) -> None:
        workspace = harness.workspace_id("ws5")
        head = await self._seed(harness, workspace, file("a.txt", "a"))
        again = await self._write(harness, workspace, ChangeSet())
        assert again == head

    async def test_ws6_revisions_are_kept(self, harness: WorkspaceStoreHarness) -> None:
        workspace = harness.workspace_id("ws6")
        first = await self._seed(harness, workspace, file("a.txt", "a"))
        await self._write(harness, workspace, ChangeSet.of(file("a.txt", "b")))
        assert await harness.store.revision(harness.scope, workspace, 1) == first
        assert (await harness.store.revision(harness.scope, workspace, 0)).revision == 0
        with pytest.raises(WorkspaceRevisionNotFound):
            await harness.store.revision(harness.scope, workspace, 3)

    async def test_ws7_tenants_are_isolated(self, harness: WorkspaceStoreHarness) -> None:
        workspace = harness.workspace_id("ws7")
        await self._seed(harness, workspace, file("secret.txt", "s"))
        other_store, other_scope = harness.other_tenant()
        assert (await other_store.head(other_scope, workspace)).revision == 0
        with pytest.raises(WorkspaceRevisionNotFound):
            await other_store.fork(
                other_scope, workspace, at_revision=1, fork_id=harness.workspace_id("ws7-fork")
            )

    async def test_ws8_fork_copies_a_revision_and_is_idempotent(
        self, harness: WorkspaceStoreHarness
    ) -> None:
        workspace, fork_id = harness.workspace_id("ws8"), harness.workspace_id("ws8-fork")
        await self._seed(harness, workspace, file("a.txt", "a"))
        await self._write(harness, workspace, ChangeSet.of(file("a.txt", "b")))
        fork = await harness.store.fork(harness.scope, workspace, at_revision=1, fork_id=fork_id)
        assert (fork.workspace_id, fork.revision) == (fork_id, 1)
        assert _texts(fork.files) == _texts({"a.txt": file("a.txt", "a")})
        again = await harness.store.fork(harness.scope, workspace, at_revision=1, fork_id=fork_id)
        assert again == fork
        with pytest.raises(WorkspaceForkConflict):
            await harness.store.fork(harness.scope, workspace, at_revision=2, fork_id=fork_id)
        with pytest.raises(WorkspaceRevisionNotFound):
            await harness.store.fork(
                harness.scope, workspace, at_revision=9, fork_id=harness.workspace_id("ws8-far")
            )

    async def _two_forks(
        self, h: WorkspaceStoreHarness, name: str, *files: WorkspaceFile
    ) -> tuple[str, str, str]:
        parent = h.workspace_id(name)
        left, right = h.workspace_id(f"{name}-l"), h.workspace_id(f"{name}-r")
        seeded = await self._seed(h, parent, *files)
        await h.store.fork(h.scope, parent, at_revision=seeded.revision, fork_id=left)
        await h.store.fork(h.scope, parent, at_revision=seeded.revision, fork_id=right)
        return parent, left, right

    async def test_ws9_disjoint_merges_both_land(self, harness: WorkspaceStoreHarness) -> None:
        parent, left, right = await self._two_forks(harness, "ws9", file("shared.txt", "s"))
        await self._write(harness, left, ChangeSet.of(file("left.txt", "l")))
        await self._write(harness, right, ChangeSet.of(file("right.txt", "r"), deletes=frozenset({"shared.txt"})))
        first = await harness.store.merge(harness.scope, fork_id=left, into=parent)
        second = await harness.store.merge(harness.scope, fork_id=right, into=parent)
        assert (first.status, first.manifest.revision) == ("merged", 2)
        assert (second.status, second.manifest.revision) == ("merged", 3)
        head = await harness.store.head(harness.scope, parent)
        assert head == second.manifest
        assert _texts(head.files) == _texts({"left.txt": file("left.txt", "l"), "right.txt": file("right.txt", "r")})

    async def test_ws10_overlapping_merge_reports_the_path(self, harness: WorkspaceStoreHarness) -> None:
        parent, left, right = await self._two_forks(harness, "ws10", file("a.txt", "base"))
        await self._write(harness, left, ChangeSet.of(file("a.txt", "left")))
        await self._write(harness, right, ChangeSet.of(file("a.txt", "right"), file("b.txt", "b")))
        assert (await harness.store.merge(harness.scope, fork_id=left, into=parent)).status == "merged"
        before = await harness.store.head(harness.scope, parent)
        result = await harness.store.merge(harness.scope, fork_id=right, into=parent)
        assert result.status == "conflict"
        assert [c.path for c in result.conflicts] == ["a.txt"]
        conflict = result.conflicts[0]
        assert conflict.ours is not None and conflict.ours.same_content(file("a.txt", "left"))
        assert conflict.theirs is not None and conflict.theirs.same_content(file("a.txt", "right"))
        assert result.repository_conflicts == ()
        assert await harness.store.head(harness.scope, parent) == before == result.manifest

    async def test_ws11_identical_changes_do_not_conflict(self, harness: WorkspaceStoreHarness) -> None:
        parent, left, right = await self._two_forks(harness, "ws11", file("a.txt", "base"))
        for fork in (left, right):
            await self._write(harness, fork, ChangeSet.of(file("a.txt", "same")))
        for fork in (left, right):
            assert (await harness.store.merge(harness.scope, fork_id=fork, into=parent)).status == "merged"
        head = await harness.store.head(harness.scope, parent)
        assert head.files["a.txt"].same_content(file("a.txt", "same"))

    async def test_ws12_a_repository_both_forks_touch_is_not_merged(
        self, harness: WorkspaceStoreHarness
    ) -> None:
        parent, left, right = await self._two_forks(
            harness, "ws12", file(".git/HEAD", "ref: main"), file("src/app.py", "v1")
        )
        await self._write(harness, left, ChangeSet.of(file(".git/refs/heads/left", "1"), file("src/app.py", "v2")))
        await self._write(harness, right, ChangeSet.of(file(".git/refs/heads/right", "2"), file("notes.md", "n")))
        assert (await harness.store.merge(harness.scope, fork_id=left, into=parent)).status == "merged"
        before = await harness.store.head(harness.scope, parent)
        result = await harness.store.merge(harness.scope, fork_id=right, into=parent)
        assert result.status == "conflict"
        assert result.repository_conflicts == (".git",)
        assert result.conflicts == ()
        after = await harness.store.head(harness.scope, parent)
        assert after == before
        assert ".git/refs/heads/right" not in after.files and "notes.md" not in after.files

    async def test_ws13_one_fork_in_a_repository_merges(self, harness: WorkspaceStoreHarness) -> None:
        parent, left, right = await self._two_forks(harness, "ws13", file("repo/.git/HEAD", "ref: main"))
        await self._write(harness, left, ChangeSet.of(file("repo/.git/refs/heads/x", "1")))
        await self._write(harness, right, ChangeSet.of(file("other.txt", "o")))
        for fork in (left, right):
            assert (await harness.store.merge(harness.scope, fork_id=fork, into=parent)).status == "merged"
        assert {"repo/.git/refs/heads/x", "other.txt"} <= set((await harness.store.head(harness.scope, parent)).files)

    async def test_ws14_merge_base_advances(self, harness: WorkspaceStoreHarness) -> None:
        parent, fork, _ = await self._two_forks(harness, "ws14", file("a.txt", "a"))
        idle = await harness.store.merge(harness.scope, fork_id=fork, into=parent)
        assert (idle.status, idle.manifest.revision) == ("up_to_date", 1)
        await self._write(harness, fork, ChangeSet.of(file("b.txt", "b")))
        assert (await harness.store.merge(harness.scope, fork_id=fork, into=parent)).status == "merged"
        assert (await harness.store.merge(harness.scope, fork_id=fork, into=parent)).status == "up_to_date"
        # The parent changes b.txt after the first merge; the fork's next change must not revert it.
        await self._write(harness, parent, ChangeSet.of(file("b.txt", "parent")))
        await self._write(harness, fork, ChangeSet.of(file("c.txt", "c")))
        result = await harness.store.merge(harness.scope, fork_id=fork, into=parent)
        assert result.status == "merged"
        assert result.manifest.files["b.txt"].same_content(file("b.txt", "parent"))
        assert "c.txt" in result.manifest.files

    async def test_ws15_merge_needs_a_fork_of_the_target(self, harness: WorkspaceStoreHarness) -> None:
        parent, fork, _ = await self._two_forks(harness, "ws15", file("a.txt", "a"))
        stranger = harness.workspace_id("ws15-x")
        await self._seed(harness, stranger, file("x.txt", "x"))
        with pytest.raises(WorkspaceNotAFork):
            await harness.store.merge(harness.scope, fork_id=fork, into=stranger)
        with pytest.raises(WorkspaceNotAFork):
            await harness.store.merge(harness.scope, fork_id=stranger, into=parent)

    async def _merge_keeping(
        self, h: WorkspaceStoreHarness, fork: str, into: str, key: str | None = None
    ) -> MergeResult:
        return await h.store.merge(
            h.scope, fork_id=fork, into=into, key=key, on_conflict="keep_target"
        )

    async def test_wf1_keep_target_without_conflict_is_a_plain_merge(
        self, harness: WorkspaceStoreHarness
    ) -> None:
        parent, left, _ = await self._two_forks(harness, "wf1", file("a.txt", "a"))
        changed = await self._write(
            harness, left, ChangeSet.of(file("b.txt", "b"), deletes=frozenset({"a.txt"}))
        )
        result = await self._merge_keeping(harness, left, parent)
        assert (result.status, result.manifest.revision) == ("merged", 2)
        assert result.merged == ("a.txt", "b.txt")
        assert result.fork_revision == changed.revision
        assert result.conflicts == () and result.repository_conflicts == ()
        assert _texts(result.manifest.files) == _texts({"b.txt": file("b.txt", "b")})

    async def test_wf2_keep_target_merges_the_rest_and_reports_the_conflict(
        self, harness: WorkspaceStoreHarness
    ) -> None:
        parent, left, right = await self._two_forks(harness, "wf2", file("a.txt", "base"))
        await self._write(harness, left, ChangeSet.of(file("a.txt", "left")))
        theirs = await self._write(
            harness, right, ChangeSet.of(file("a.txt", "right"), file("b.txt", "b"))
        )
        assert (await self._merge_keeping(harness, left, parent)).status == "merged"
        result = await self._merge_keeping(harness, right, parent)
        assert result.status == "partial"
        assert [(c.path, c.kind) for c in result.conflicts] == [("a.txt", "both_changed")]
        conflict = result.conflicts[0]
        assert conflict.ours is not None and conflict.ours.same_content(file("a.txt", "left"))
        assert conflict.theirs is not None and conflict.theirs.same_content(file("a.txt", "right"))
        assert result.merged == ("b.txt",)
        assert result.fork_revision == theirs.revision
        head = await harness.store.head(harness.scope, parent)
        assert head == result.manifest and head.revision == 3
        assert head.files["a.txt"].same_content(file("a.txt", "left"))
        assert head.files["b.txt"].same_content(file("b.txt", "b"))
        again = await harness.store.merge(harness.scope, fork_id=right, into=parent)
        assert (again.status, again.manifest.revision) == ("up_to_date", 3)

    async def test_wf3_delete_and_modify_keep_the_targets_side(
        self, harness: WorkspaceStoreHarness
    ) -> None:
        parent, fork, _ = await self._two_forks(
            harness, "wf3", file("gone.txt", "g"), file("kept.txt", "k")
        )
        await self._write(
            harness, parent, ChangeSet.of(file("kept.txt", "parent"), deletes=frozenset({"gone.txt"}))
        )
        await self._write(
            harness, fork, ChangeSet.of(file("gone.txt", "fork"), deletes=frozenset({"kept.txt"}))
        )
        result = await self._merge_keeping(harness, fork, parent)
        assert result.status == "partial"
        assert [(c.path, c.kind) for c in result.conflicts] == [
            ("gone.txt", "target_deleted"),
            ("kept.txt", "fork_deleted"),
        ]
        head = await harness.store.head(harness.scope, parent)
        assert "gone.txt" not in head.files
        assert head.files["kept.txt"].same_content(file("kept.txt", "parent"))
        assert result.merged == () and head.revision == 2

    async def test_wf4_keep_target_leaves_a_shared_repository_whole(
        self, harness: WorkspaceStoreHarness
    ) -> None:
        parent, left, right = await self._two_forks(harness, "wf4", file(".git/HEAD", "ref: main"))
        await self._write(harness, left, ChangeSet.of(file(".git/refs/heads/l", "1")))
        await self._write(
            harness, right, ChangeSet.of(file(".git/refs/heads/r", "2"), file("notes.md", "n"))
        )
        assert (await self._merge_keeping(harness, left, parent)).status == "merged"
        result = await self._merge_keeping(harness, right, parent)
        assert result.status == "partial"
        assert result.repository_conflicts == (".git",) and result.conflicts == ()
        assert result.merged == ("notes.md",)
        head = await harness.store.head(harness.scope, parent)
        assert ".git/refs/heads/r" not in head.files and "notes.md" in head.files

    async def test_wf5_only_conflicts_add_no_revision(self, harness: WorkspaceStoreHarness) -> None:
        parent, fork, _ = await self._two_forks(harness, "wf5", file("a.txt", "base"))
        mine = await self._write(harness, parent, ChangeSet.of(file("a.txt", "parent")))
        await self._write(harness, fork, ChangeSet.of(file("a.txt", "fork")))
        result = await self._merge_keeping(harness, fork, parent)
        assert (result.status, result.merged) == ("partial", ())
        assert result.manifest == mine == await harness.store.head(harness.scope, parent)
        again = await self._merge_keeping(harness, fork, parent)
        assert again.status == "up_to_date"

    async def test_wf6_the_same_key_reports_the_same_merge_and_writes_nothing(
        self, harness: WorkspaceStoreHarness
    ) -> None:
        parent, left, right = await self._two_forks(harness, "wf6", file("a.txt", "base"))
        await self._write(harness, left, ChangeSet.of(file("a.txt", "left")))
        await self._write(harness, right, ChangeSet.of(file("a.txt", "right"), file("c.txt", "c")))
        assert (await self._merge_keeping(harness, left, parent, key="child-l")).status == "merged"
        first = await self._merge_keeping(harness, right, parent, key="child-r")
        await self._write(harness, parent, ChangeSet.of(file("later.txt", "moved on")))
        moved = await harness.store.head(harness.scope, parent)
        replay = await self._merge_keeping(harness, right, parent, key="child-r")
        assert replay == first
        assert replay.status == "partial" and replay.merged == ("c.txt",)
        assert await harness.store.head(harness.scope, parent) == moved
        older = await self._merge_keeping(harness, left, parent, key="child-l")
        assert (older.status, older.manifest.revision, older.merged) == ("merged", 2, ("a.txt",))
        assert await harness.store.head(harness.scope, parent) == moved

    async def test_wf7_another_key_merges_what_changed_since(
        self, harness: WorkspaceStoreHarness
    ) -> None:
        parent, fork, _ = await self._two_forks(harness, "wf7", file("a.txt", "a"))
        await self._write(harness, fork, ChangeSet.of(file("b.txt", "b")))
        assert (await self._merge_keeping(harness, fork, parent, key="k1")).merged == ("b.txt",)
        await self._write(harness, fork, ChangeSet.of(file("c.txt", "c")))
        second = await self._merge_keeping(harness, fork, parent, key="k2")
        assert (second.status, second.merged) == ("merged", ("c.txt",))
        assert {"a.txt", "b.txt", "c.txt"} == set(second.manifest.files)

    async def test_wf8_a_merge_says_how_it_changed_each_path(self, harness: WorkspaceStoreHarness) -> None:
        """R27REST-FU-1: the kinds are the target's (a file the fork made is added to the parent), and a keyed
        replay says the same."""
        parent, fork, _ = await self._two_forks(
            harness, "wf8", file("kept.txt", "k"), file("gone.txt", "g")
        )
        await self._write(
            harness,
            fork,
            ChangeSet.of(file("kept.txt", "fork"), file("new.csv", "n"), deletes=frozenset({"gone.txt"})),
        )
        result = await self._merge_keeping(harness, fork, parent, key="child-wf8")
        assert dict(result.kinds) == {"gone.txt": "deleted", "kept.txt": "modified", "new.csv": "added"}
        assert set(result.kinds) == set(result.merged)
        replay = await self._merge_keeping(harness, fork, parent, key="child-wf8")
        assert dict(replay.kinds) == dict(result.kinds)
        again = await self._merge_keeping(harness, fork, parent)
        assert (again.status, dict(again.kinds)) == ("up_to_date", {})

    async def test_ws16_rollback_adds_a_revision_equal_to_the_old_one(
        self, harness: WorkspaceStoreHarness
    ) -> None:
        workspace = harness.workspace_id("ws16")
        first = await self._seed(harness, workspace, file("a.txt", "a"))
        await self._write(harness, workspace, ChangeSet.of(file("a.txt", "b"), file("c.txt", "c")))
        rolled = await harness.store.rollback(harness.scope, workspace, to_revision=1)
        assert (rolled.revision, rolled.parent_revision) == (3, 2)
        assert rolled.content_hash == first.content_hash
        assert _texts(rolled.files) == _texts(first.files)
        assert await harness.store.head(harness.scope, workspace) == rolled
        with pytest.raises(WorkspaceRevisionNotFound):
            await harness.store.rollback(harness.scope, workspace, to_revision=7)

    async def test_ws17_diff(self, harness: WorkspaceStoreHarness) -> None:
        workspace = harness.workspace_id("ws17")
        a = await self._seed(
            harness, workspace, file("keep.txt", "k"), file("edit.txt", "1"), file("gone.txt", "g"), file("mime.txt", "m")
        )
        b = await self._write(
            harness,
            workspace,
            ChangeSet.of(
                file("edit.txt", "2"),
                file("new.txt", "n"),
                file("mime.txt", "m", "text/markdown"),
                file("keep.txt", "k"),
                deletes=frozenset({"gone.txt"}),
            ),
        )
        diff = await harness.store.diff(harness.scope, a, b)
        assert set(diff.added) == {"new.txt"}
        assert set(diff.modified) == {"edit.txt", "mime.txt"}
        assert diff.removed == frozenset({"gone.txt"})
        assert (await harness.store.diff(harness.scope, a, a)).is_empty
        assert _texts(diff.change_set().apply(a.files)) == _texts(b.files)

    async def test_ws18_commit_and_diff_round_trip(self, harness: WorkspaceStoreHarness) -> None:
        # Hypothesis cannot drive a coroutine inside the running loop, so it
        # draws the examples and the store runs them; a failure names its example.
        segments = st.sampled_from(["a", "b", "src", ".git", "README.md", "\u00e9"])
        paths = st.lists(segments, min_size=1, max_size=3).map("/".join)
        manifests = st.dictionaries(paths, st.sampled_from(["x", "y", "z", ""]), max_size=6)
        examples: list[tuple[dict[str, str], dict[str, str], frozenset[str]]] = []

        @settings(
            max_examples=harness.property_examples,
            deadline=None,
            database=None,
            suppress_health_check=[HealthCheck.function_scoped_fixture],
        )
        @given(before=manifests, writes=manifests, data=st.data())
        def draw(before: dict[str, str], writes: dict[str, str], data: st.DataObject) -> None:
            deletable = sorted(set(before) - set(writes))
            deletes = data.draw(st.frozensets(st.sampled_from(deletable))) if deletable else frozenset[str]()
            examples.append((before, writes, deletes))

        draw()
        for index, (before, writes, deletes) in enumerate(examples):
            workspace = harness.workspace_id(f"ws18-{index}")
            start = await self._seed(harness, workspace, *(file(p, t) for p, t in before.items()))
            changes = ChangeSet.of(*(file(p, t) for p, t in writes.items()), deletes=deletes)
            after = await harness.store.commit(
                harness.scope, workspace, base_revision=start.revision, changes=changes, reason="autosave"
            )
            diff = await harness.store.diff(harness.scope, start, after)
            expected = changes.without_noops(start.files)
            example = (before, writes, sorted(deletes))
            assert _texts(diff.change_set().upserts) == _texts(expected.upserts), example
            assert diff.change_set().deletes == expected.deletes, example
            assert _texts(diff.change_set().apply(start.files)) == _texts(after.files), example
            assert diff_manifests(start, after) == diff_files(start.files, after.files), example

    @pytest.mark.parametrize(
        "path",
        ["../x", "a/../b", "/a", "a//b", "./a", "a/", "a\\b", "a\x00b", "/".join(["d"] * 33), "x" * 1025],
    )
    async def test_ws19_traversal_and_non_canonical_paths_are_refused(
        self, harness: WorkspaceStoreHarness, path: str
    ) -> None:
        del harness
        with pytest.raises(ValueError):
            normalize_workspace_file_path(path)
        with pytest.raises(InvalidChangeSet):
            ChangeSet(deletes=frozenset({path}))

    async def test_ws19_paths_are_nfc(self, harness: WorkspaceStoreHarness) -> None:
        workspace = harness.workspace_id("ws19")
        decomposed = "café.txt"
        head = await self._seed(harness, workspace, file(decomposed, "c"))
        assert list(head.files) == ["café.txt"]


class ObjectStoreHarness(Protocol):
    @property
    def objects(self) -> BinaryObjectStore: ...

    @property
    def scope(self) -> ObjectScope: ...

    @property
    def other_tenant(self) -> ObjectScope: ...


class BinaryObjectStoreContract:
    @pytest.fixture
    def harness(self) -> ObjectStoreHarness:
        raise NotImplementedError("provide a harness fixture")

    async def test_os1_round_trip(self, harness: ObjectStoreHarness) -> None:
        ref = await harness.objects.put(harness.scope, b"hello workspace")
        assert ref.size_bytes == len(b"hello workspace")
        assert await harness.objects.get(harness.scope, ref) == b"hello workspace"

    async def test_os2_equal_content_has_one_hash(self, harness: ObjectStoreHarness) -> None:
        first = await harness.objects.put(harness.scope, b"same")
        second = await harness.objects.put(harness.scope, b"same")
        other = await harness.objects.put(harness.scope, b"different")
        assert first.content_hash == second.content_hash != other.content_hash

    async def test_os3_another_tenant_cannot_read(self, harness: ObjectStoreHarness) -> None:
        ref = await harness.objects.put(harness.scope, b"tenant secret")
        with pytest.raises((FileNotFoundError, ValueError, LookupError)):
            await harness.objects.get(harness.other_tenant, ref)

    async def test_os4_empty_content(self, harness: ObjectStoreHarness) -> None:
        ref = await harness.objects.put(harness.scope, b"")
        assert ref.size_bytes == 0
        assert await harness.objects.get(harness.scope, ref) == b""

    async def test_os5_content_hash_is_the_hash_a_put_carries(self, harness: ObjectStoreHarness) -> None:
        expected = await harness.objects.content_hash(harness.scope, b"not stored yet")
        assert expected != await harness.objects.content_hash(harness.scope, b"other bytes")
        ref = await harness.objects.put(harness.scope, b"not stored yet")
        assert ref.content_hash == expected
        assert await harness.objects.content_hash(harness.scope, b"") == (await harness.objects.put(harness.scope, b"")).content_hash


class KeyProviderHarness(Protocol):
    @property
    def keys(self) -> KeyProvider: ...

    @property
    def tenant_id(self) -> str: ...

    @property
    def key_domain(self) -> str: ...

    def others(self) -> tuple[tuple[KeyProvider, str, str], ...]:
        """(provider, tenant, domain) triples that differ from the main one.

        A provider may serve one domain only (a lease-bound one does), so each
        comes with the provider that serves it.
        """
        ...

    async def shred(self, tenant_id: str, key_domain: str) -> None:
        """Destroy the domain's key and end any lease that holds it."""
        ...


class KeyProviderContract:
    @pytest.fixture
    def harness(self) -> KeyProviderHarness:
        raise NotImplementedError("provide a harness fixture")

    async def test_sk5_one_key_per_domain(self, harness: KeyProviderHarness) -> None:
        key = await harness.keys.data_key(harness.tenant_id, harness.key_domain)
        assert await harness.keys.data_key(harness.tenant_id, harness.key_domain) == key
        assert key.material.hex() not in repr(key)
        for provider, tenant_id, domain in harness.others():
            other = await provider.data_key(tenant_id, domain)
            assert other.material != key.material
            assert other.key_id != key.key_id

    async def test_sk5_a_destroyed_key_is_never_issued_again(self, harness: KeyProviderHarness) -> None:
        _ = await harness.keys.data_key(harness.tenant_id, harness.key_domain)
        await harness.shred(harness.tenant_id, harness.key_domain)
        with pytest.raises(WorkspaceKeyDestroyed):
            _ = await harness.keys.data_key(harness.tenant_id, harness.key_domain)
        for provider, tenant_id, domain in harness.others():
            assert (await provider.data_key(tenant_id, domain)).material


__all__ = [
    "BinaryObjectStoreContract",
    "KeyProviderContract",
    "KeyProviderHarness",
    "ObjectStoreHarness",
    "WorkspaceStoreContract",
    "WorkspaceStoreHarness",
    "file",
]
