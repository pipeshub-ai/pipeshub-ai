"""Contract suites for P6.4 (``impl/P6.4-workspace-forks.md``).

- :class:`WorkspaceWritersContract` (WL-1..WL-6) runs on every ``WorkspaceWriters``;
- :class:`ChildWorkspacesContract` (CW-1..CW-5) runs on every ``ChildWorkspaces``
  over the stores a host binds it to.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import pytest

from harness_core.testing.contracts.workspace import file
from harness_core.workspace.changes import ChangeSet
from harness_core.workspace.forks import (
    UNFINISHED_WORK_FOLDER,
    ChildMergeReport,
    JoinedChild,
    session_scope,
    session_workspace_id,
)
from harness_core.workspace.types import WorkspaceWriterBusy

if TYPE_CHECKING:
    from harness_core.journal.types import AttemptToken
    from harness_core.workspace.ports import (
        ChildWorkspaces,
        WorkspaceStore,
        WorkspaceWriters,
    )
    from harness_core.workspace.types import (
        ObjectScope,
        WorkspaceFile,
        WorkspaceManifest,
    )


class WorkspaceWritersHarness(Protocol):
    @property
    def scope(self) -> ObjectScope: ...

    @property
    def ttl_ms(self) -> int:
        """Short enough for ``elapse`` to outlast it."""
        ...

    def holders(self) -> tuple[tuple[WorkspaceWriters, str], tuple[WorkspaceWriters, str]]:
        """Two distinct holders, each with the adapter it acquires through."""
        ...

    def workspace_id(self, name: str) -> str: ...

    def other_tenant(self) -> tuple[WorkspaceWriters, ObjectScope] | None:
        """An adapter and scope of another tenant, or ``None`` when one binding serves one tenant."""
        ...

    async def elapse(self, ms: int) -> None: ...


class WorkspaceWritersContract:
    @pytest.fixture
    def harness(self) -> WorkspaceWritersHarness:
        raise NotImplementedError("provide a harness fixture")

    async def test_wl1_a_free_workspace_is_leased(self, harness: WorkspaceWritersHarness) -> None:
        (writers, holder), _ = harness.holders()
        workspace = harness.workspace_id("wl1")
        lease = await writers.acquire(harness.scope, workspace, holder=holder, ttl_ms=harness.ttl_ms)
        assert (lease.workspace_id, lease.holder, lease.renewed) == (workspace, holder, False)
        assert lease.expires_at_ms > 0

    async def test_wl2_the_holder_renews(self, harness: WorkspaceWritersHarness) -> None:
        (writers, holder), _ = harness.holders()
        workspace = harness.workspace_id("wl2")
        first = await writers.acquire(harness.scope, workspace, holder=holder, ttl_ms=harness.ttl_ms)
        again = await writers.acquire(harness.scope, workspace, holder=holder, ttl_ms=harness.ttl_ms)
        assert again.renewed and again.holder == holder
        assert again.expires_at_ms >= first.expires_at_ms

    async def test_wl3_another_holder_is_refused_naming_the_holder(
        self, harness: WorkspaceWritersHarness
    ) -> None:
        (writers, holder), (others, other) = harness.holders()
        workspace = harness.workspace_id("wl3")
        _ = await writers.acquire(harness.scope, workspace, holder=holder, ttl_ms=harness.ttl_ms)
        with pytest.raises(WorkspaceWriterBusy) as busy:
            _ = await others.acquire(harness.scope, workspace, holder=other, ttl_ms=harness.ttl_ms)
        assert busy.value.holder == holder
        assert busy.value.reason_code == "workspace_writer_busy"

    async def test_wl4_an_expired_lease_is_taken_over(self, harness: WorkspaceWritersHarness) -> None:
        (writers, holder), (others, other) = harness.holders()
        workspace = harness.workspace_id("wl4")
        _ = await writers.acquire(harness.scope, workspace, holder=holder, ttl_ms=harness.ttl_ms)
        await harness.elapse(harness.ttl_ms + 50)
        taken = await others.acquire(harness.scope, workspace, holder=other, ttl_ms=harness.ttl_ms)
        assert (taken.holder, taken.renewed) == (other, False)
        with pytest.raises(WorkspaceWriterBusy):
            _ = await writers.acquire(harness.scope, workspace, holder=holder, ttl_ms=harness.ttl_ms)

    async def test_wl5_only_the_holder_releases(self, harness: WorkspaceWritersHarness) -> None:
        (writers, holder), (others, other) = harness.holders()
        workspace = harness.workspace_id("wl5")
        await others.release(harness.scope, harness.workspace_id("wl5-free"), holder=other)
        _ = await writers.acquire(harness.scope, workspace, holder=holder, ttl_ms=harness.ttl_ms)
        await others.release(harness.scope, workspace, holder=other)
        with pytest.raises(WorkspaceWriterBusy):
            _ = await others.acquire(harness.scope, workspace, holder=other, ttl_ms=harness.ttl_ms)
        await writers.release(harness.scope, workspace, holder=holder)
        taken = await others.acquire(harness.scope, workspace, holder=other, ttl_ms=harness.ttl_ms)
        assert (taken.holder, taken.renewed) == (other, False)

    async def test_wl6_leases_are_per_workspace_and_per_tenant(
        self, harness: WorkspaceWritersHarness
    ) -> None:
        (writers, holder), (others, other) = harness.holders()
        origin = harness.workspace_id("wl6")
        fork = harness.workspace_id("wl6:fork:x")
        _ = await writers.acquire(harness.scope, origin, holder=holder, ttl_ms=harness.ttl_ms)
        assert (await others.acquire(harness.scope, fork, holder=other, ttl_ms=harness.ttl_ms)).holder == other
        tenant = harness.other_tenant()
        if tenant is not None:
            foreign, scope = tenant
            lease = await foreign.acquire(scope, origin, holder=other, ttl_ms=harness.ttl_ms)
            assert lease.holder == other


class ChildWorkspacesHarness(Protocol):
    @property
    def merger(self) -> ChildWorkspaces: ...

    @property
    def store(self) -> WorkspaceStore: ...

    @property
    def tenant_id(self) -> str: ...

    @property
    def session_id(self) -> str: ...

    @property
    def parent_run_id(self) -> str: ...

    @property
    def token(self) -> AttemptToken: ...

    def child_run_id(self, name: str) -> str:
        """A child run id the host accepts in a fork id."""
        ...

    async def hold_elsewhere(self, workspace_id: str) -> bool:
        """Another live run takes ``workspace_id``'s write lease; ``False`` where no
        other live run can hold it (Node: one root run per session, and the lease
        of a run that ended is taken over)."""
        ...


class ChildWorkspacesContract:
    @pytest.fixture
    def harness(self) -> ChildWorkspacesHarness:
        raise NotImplementedError("provide a harness fixture")

    def _child(self, h: ChildWorkspacesHarness, name: str, outcome: str = "answered") -> JoinedChild:
        return JoinedChild(
            tenant_id=h.tenant_id,
            session_id=h.session_id,
            parent_run_id=h.parent_run_id,
            workspace_id=session_workspace_id(h.session_id),
            child_run_id=h.child_run_id(name),
            outcome=outcome,
        )

    async def _commit(
        self, h: ChildWorkspacesHarness, workspace: str, *files: WorkspaceFile
    ) -> WorkspaceManifest:
        scope = session_scope(h.tenant_id, h.session_id)
        head = await h.store.head(scope, workspace)
        return await h.store.commit(
            scope, workspace, base_revision=head.revision, changes=ChangeSet.of(*files), reason="autosave"
        )

    async def _fork(self, h: ChildWorkspacesHarness, child: JoinedChild) -> None:
        scope = session_scope(h.tenant_id, h.session_id)
        head = await h.store.head(scope, child.workspace_id)
        _ = await h.store.fork(scope, child.workspace_id, at_revision=head.revision, fork_id=child.fork_id)

    async def test_cw1_a_childs_files_merge_into_its_parent(self, harness: ChildWorkspacesHarness) -> None:
        child = self._child(harness, "cw1")
        _ = await self._commit(harness, child.workspace_id, file("seed.txt", "s"))
        await self._fork(harness, child)
        changed = await self._commit(harness, child.fork_id, file("out/report.md", "r"))
        report = await harness.merger.merge(harness.token, child)
        assert report is not None
        assert (report.status, report.merged, report.fork_revision) == ("merged", ("out/report.md",), changed.revision)
        head = await harness.store.head(session_scope(harness.tenant_id, harness.session_id), child.workspace_id)
        assert report.revision == head.revision and "out/report.md" in head.files

    async def test_cw2_asking_again_reports_the_same_and_writes_nothing(
        self, harness: ChildWorkspacesHarness
    ) -> None:
        child = self._child(harness, "cw2")
        await self._fork(harness, child)
        _ = await self._commit(harness, child.fork_id, file("a.txt", "a"))
        first = await harness.merger.merge(harness.token, child)
        _ = await self._commit(harness, child.workspace_id, file("parent.txt", "p"))
        scope = session_scope(harness.tenant_id, harness.session_id)
        moved = await harness.store.head(scope, child.workspace_id)
        assert await harness.merger.merge(harness.token, child) == first
        assert await harness.store.head(scope, child.workspace_id) == moved

    async def test_cw3_a_failed_child_is_not_merged_and_no_fork_is_none(
        self, harness: ChildWorkspacesHarness
    ) -> None:
        failed = self._child(harness, "cw3", outcome="failed")
        await self._fork(harness, failed)
        _ = await self._commit(harness, failed.fork_id, file("half.txt", "h"))
        scope = session_scope(harness.tenant_id, harness.session_id)
        before = await harness.store.head(scope, failed.workspace_id)
        report = await harness.merger.merge(harness.token, failed)
        assert report is not None
        assert (report.status, report.reason) == ("not_merged", "child_failed")
        assert await harness.store.head(scope, failed.workspace_id) == before
        assert await harness.merger.merge(harness.token, self._child(harness, "cw3-none")) is None

    async def test_cw4_a_conflict_keeps_the_parents_side_and_is_listed(
        self, harness: ChildWorkspacesHarness
    ) -> None:
        child = self._child(harness, "cw4")
        _ = await self._commit(harness, child.workspace_id, file("cw4.txt", "base"))
        await self._fork(harness, child)
        _ = await self._commit(harness, child.workspace_id, file("cw4.txt", "parent"))
        _ = await self._commit(harness, child.fork_id, file("cw4.txt", "child"), file("cw4-new.txt", "n"))
        report = await harness.merger.merge(harness.token, child)
        assert report is not None and report.status == "partial"
        assert report.conflicts == (("cw4.txt", "both_changed"),)
        assert report.merged == ("cw4-new.txt",)
        shown = report.as_json()
        assert shown["kept"] == "parent"
        assert shown["fork"] == {"workspaceId": child.fork_id, "revision": report.fork_revision}
        head = await harness.store.head(session_scope(harness.tenant_id, harness.session_id), child.workspace_id)
        assert head.files["cw4.txt"].same_content(file("cw4.txt", "parent"))

    async def test_cw5_another_writer_holds_the_parent_workspace(
        self, harness: ChildWorkspacesHarness
    ) -> None:
        child = self._child(harness, "cw5")
        await self._fork(harness, child)
        _ = await self._commit(harness, child.fork_id, file("cw5.txt", "c"))
        if not await harness.hold_elsewhere(child.workspace_id):
            pytest.skip("no other live run can hold this host's session workspace")
        report = await harness.merger.merge(harness.token, child)
        assert report is not None and (report.status, report.reason) == ("not_merged", "writer_busy")
        head = await harness.store.head(session_scope(harness.tenant_id, harness.session_id), child.workspace_id)
        assert "cw5.txt" not in head.files

    # WP-56 (WP56-UNFIN-1..5): a failed child's saved work reaches the person, apart from the conversation's files.
    # CW3 holds: the merge offers none of it; the parent then keeps the child's own changes in a separate
    # "Unfinished work" folder, never over a file of its own.

    async def _failed_with_work(self, h: ChildWorkspacesHarness, name: str) -> tuple[JoinedChild, ChildMergeReport]:
        failed = self._child(h, name, outcome="failed")
        _ = await self._commit(h, failed.workspace_id, file(f"{name}/seed.txt", "parent"))
        await self._fork(h, failed)
        _ = await self._commit(
            h, failed.fork_id, file("out/report.md", "half a report"), file(f"{name}/seed.txt", "child's edit")
        )
        report = await h.merger.merge(h.token, failed)
        assert report is not None and (report.status, report.reason) == ("not_merged", "child_failed")
        return failed, report

    async def test_wp56_unfin_1_a_failed_childs_work_is_kept_in_its_own_folder(
        self, harness: ChildWorkspacesHarness
    ) -> None:
        failed, report = await self._failed_with_work(harness, "u1")
        scope = session_scope(harness.tenant_id, harness.session_id)
        before = await harness.store.head(scope, failed.workspace_id)
        fork_before = await harness.store.head(scope, failed.fork_id)
        kept = await harness.merger.keep_unfinished(harness.token, failed, report)
        folder = kept.unfinished
        assert folder is not None and folder.startswith(f"{UNFINISHED_WORK_FOLDER}/")
        assert failed.child_run_id not in folder and ":" not in folder
        assert (kept.status, kept.reason, kept.merged) == ("not_merged", "child_failed", ())
        assert kept.kept == ("out/report.md", "u1/seed.txt")
        head = await harness.store.head(scope, failed.workspace_id)
        assert kept.revision == head.revision > before.revision
        assert head.files[f"{folder}/out/report.md"].same_content(file("x", "half a report"))
        assert head.files[f"{folder}/u1/seed.txt"].same_content(file("x", "child's edit"))
        # The conversation's own files are as they were: nothing of the child's is merged over them (CW3).
        assert {p: f for p, f in head.files.items() if not p.startswith(f"{folder}/")} == dict(before.files)
        assert await harness.store.head(scope, failed.fork_id) == fork_before
        shown = kept.as_json()
        assert shown["unfinished"] == {"folder": folder, "files": ["out/report.md", "u1/seed.txt"], "count": 2}
        assert folder in kept.summary()

    async def test_wp56_unfin_2_keeping_again_writes_nothing_and_says_the_same(
        self, harness: ChildWorkspacesHarness
    ) -> None:
        failed, report = await self._failed_with_work(harness, "u2")
        first = await harness.merger.keep_unfinished(harness.token, failed, report)
        scope = session_scope(harness.tenant_id, harness.session_id)
        after = await harness.store.head(scope, failed.workspace_id)
        again = await harness.merger.merge(harness.token, failed)
        assert again is not None
        second = await harness.merger.keep_unfinished(harness.token, failed, again)
        assert (second.unfinished, second.kept) == (first.unfinished, first.kept)
        assert await harness.store.head(scope, failed.workspace_id) == after

    async def test_wp56_unfin_3_only_the_childs_own_changes_travel(
        self, harness: ChildWorkspacesHarness
    ) -> None:
        failed = self._child(harness, "u3", outcome="failed")
        _ = await self._commit(harness, failed.workspace_id, file("u3/inherited.txt", "parent"))
        await self._fork(harness, failed)
        _ = await self._commit(
            harness, failed.fork_id, file("u3/new.txt", "n"), file("repo/.git/HEAD", "ref: refs/heads/main")
        )
        report = await harness.merger.merge(harness.token, failed)
        assert report is not None
        kept = await harness.merger.keep_unfinished(harness.token, failed, report)
        assert kept.kept == ("u3/new.txt",)

    async def test_wp56_unfin_4_nothing_saved_or_nothing_failed_keeps_nothing(
        self, harness: ChildWorkspacesHarness
    ) -> None:
        scope = session_scope(harness.tenant_id, harness.session_id)
        idle = self._child(harness, "u4-idle", outcome="failed")
        _ = await self._commit(harness, idle.workspace_id, file("u4.txt", "parent"))
        await self._fork(harness, idle)
        report = await harness.merger.merge(harness.token, idle)
        assert report is not None
        before = await harness.store.head(scope, idle.workspace_id)
        assert await harness.merger.keep_unfinished(harness.token, idle, report) == report
        answered = self._child(harness, "u4-ok")
        await self._fork(harness, answered)
        _ = await self._commit(harness, answered.fork_id, file("u4-ok.txt", "done"))
        merged = await harness.merger.merge(harness.token, answered)
        assert merged is not None and merged.status == "merged"
        moved = await harness.store.head(scope, idle.workspace_id)
        assert moved.revision == merged.revision and before.revision < moved.revision
        assert await harness.merger.keep_unfinished(harness.token, answered, merged) == merged
        assert await harness.store.head(scope, idle.workspace_id) == moved

    async def test_wp56_unfin_5_another_writer_holds_the_parent_workspace(
        self, harness: ChildWorkspacesHarness
    ) -> None:
        failed, report = await self._failed_with_work(harness, "u5")
        scope = session_scope(harness.tenant_id, harness.session_id)
        before = await harness.store.head(scope, failed.workspace_id)
        if not await harness.hold_elsewhere(failed.workspace_id):
            pytest.skip("no other live run can hold this host's session workspace")
        assert await harness.merger.keep_unfinished(harness.token, failed, report) == report
        assert await harness.store.head(scope, failed.workspace_id) == before


__all__ = [
    "ChildWorkspacesContract",
    "ChildWorkspacesHarness",
    "WorkspaceWritersContract",
    "WorkspaceWritersHarness",
]
