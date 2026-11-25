"""QA13-14 (WSP-12, TEST-STRATEGY §2.6): ``WorkspaceRevisionMachine`` over the reference workspace store.

Writes go through the object store (as the VFS writes them) and commit by CAS on the head; workspaces
fork, merge back, roll back, and two writers race from one base. After every step: every object a
revision reaches is stored and reads back as its reference says; HEAD moved only by a CAS that named it,
and of two commits from one base exactly one won; a fork stored no object; each manifest's hash is the
hash of its files; and no revision, once written, ever changes. There is no object GC (the deployment
reports ``blob.cas.gc: false``), so "GC never deletes reachable data" is "nothing is ever deleted".
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, override

import pytest
from hypothesis import HealthCheck, settings
from hypothesis import strategies as st
from hypothesis.stateful import (
    Bundle,
    RuleBasedStateMachine,
    consumes,
    invariant,
    rule,
    run_state_machine_as_test,  # pyright: ignore[reportUnknownVariableType]  # its signature is partly untyped
)

from harness_core.testing.clock import ManualClock
from harness_core.workspace import ChangeSet, ObjectScope, WorkspaceFile
from harness_core.workspace.memory import (
    InMemoryBinaryObjectStore,
    InMemoryWorkspaceStore,
)
from harness_core.workspace.types import (
    WorkspaceForkConflict,
    WorkspaceRevisionConflict,
    workspace_manifest_content_hash,
)

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from harness_core.workspace import WorkspaceManifest

SCOPE = ObjectScope("tenant", "ws:session")
ROOT = "ws:session"
PATHS = st.sampled_from(["a.txt", "b.txt", "src/x.py", "docs/r.md"])
TEXTS = st.sampled_from([b"1", b"2", b"three", b""])


class WorkspaceRevisionMachine(RuleBasedStateMachine):
    forks = Bundle("forks")

    def __init__(self) -> None:
        super().__init__()
        self.loop = asyncio.new_event_loop()
        self.objects = InMemoryBinaryObjectStore()
        self.store = InMemoryWorkspaceStore(clock=ManualClock(datetime(2026, 10, 9, tzinfo=UTC)))
        self.workspaces: set[str] = {ROOT}
        self.history: dict[tuple[str, int], WorkspaceManifest] = {}
        self.written: set[bytes] = set()
        self.forked = 0
        self.origins: dict[str, tuple[str, int]] = {}

    def _run[T](self, work: Coroutine[object, object, T]) -> T:
        return self.loop.run_until_complete(work)

    def _head(self, workspace: str) -> WorkspaceManifest:
        return self._run(self.store.head(SCOPE, workspace))

    def _keep(self, manifest: WorkspaceManifest) -> WorkspaceManifest:
        self.history.setdefault((manifest.workspace_id, manifest.revision), manifest)
        return manifest

    def _file(self, path: str, content: bytes) -> WorkspaceFile:
        self.written.add(content)
        return WorkspaceFile(path, self._run(self.objects.put(SCOPE, content)))

    def _changes(self, workspace: str, path: str, content: bytes | None) -> ChangeSet:
        if content is None:
            present = path in self._head(workspace).files
            return ChangeSet(deletes=frozenset({path}) if present else frozenset())
        return ChangeSet.of(self._file(path, content))

    # ---- rules ----

    @rule(data=st.data(), path=PATHS, content=st.one_of(st.none(), TEXTS))
    def commit(self, data: st.DataObject, path: str, content: bytes | None) -> None:
        """A write or a delete committed on the head it read."""
        workspace = data.draw(st.sampled_from(sorted(self.workspaces)))
        head = self._head(workspace)
        changes = self._changes(workspace, path, content)
        after = self._keep(
            self._run(self.store.commit(SCOPE, workspace, base_revision=head.revision, changes=changes, reason="autosave"))
        )
        assert after.revision == (head.revision if changes.is_empty else head.revision + 1)

    @rule(data=st.data(), first=TEXTS, second=TEXTS)
    def concurrent_commit(self, data: st.DataObject, first: bytes, second: bytes) -> None:
        """Two writers read one head and commit: one wins, the other's CAS fails and writes nothing."""
        workspace = data.draw(st.sampled_from(sorted(self.workspaces)))
        base = self._head(workspace).revision
        won = self._keep(
            self._run(
                self.store.commit(
                    SCOPE, workspace, base_revision=base, changes=ChangeSet.of(self._file("a.txt", first)), reason="autosave"
                )
            )
        )
        loser = ChangeSet.of(self._file("b.txt", second + b"!"))
        with pytest.raises(WorkspaceRevisionConflict):
            self._run(self.store.commit(SCOPE, workspace, base_revision=base, changes=loser, reason="autosave"))
        assert self._head(workspace) == won

    @rule(data=st.data())
    def stale_commit(self, data: st.DataObject) -> None:
        workspace = data.draw(st.sampled_from(sorted(self.workspaces)))
        head = self._head(workspace)
        if head.revision == 0:
            return
        stale = data.draw(st.integers(0, head.revision - 1))
        with pytest.raises(WorkspaceRevisionConflict):
            self._run(
                self.store.commit(
                    SCOPE, workspace, base_revision=stale, changes=ChangeSet.of(self._file("a.txt", b"late")), reason="autosave"
                )
            )
        assert self._head(workspace) == head

    @rule(target=forks, data=st.data())
    def fork(self, data: st.DataObject) -> str:
        origin = data.draw(st.sampled_from(sorted(self.workspaces)))
        at = data.draw(st.integers(0, self._head(origin).revision))
        self.forked += 1
        fork_id = f"{ROOT}:fork{self.forked}"
        before = self.objects.object_count
        manifest = self._keep(self._run(self.store.fork(SCOPE, origin, at_revision=at, fork_id=fork_id)))
        assert self.objects.object_count == before, "a fork copied objects"
        assert dict(manifest.files) == dict(self._run(self.store.revision(SCOPE, origin, at)).files)
        again = self._run(self.store.fork(SCOPE, origin, at_revision=at, fork_id=fork_id))
        assert again == manifest
        self.workspaces.add(fork_id)
        self.origins[fork_id] = (origin, at)
        return fork_id

    @rule(fork_id=forks)
    def refork_elsewhere_is_refused(self, fork_id: str) -> None:
        """A fork id names one origin revision for good: asked for another, the store refuses."""
        origin, at = self.origins[fork_id]
        other = 0 if at > 0 else 1
        if other > self._head(origin).revision:
            return
        before = self._head(fork_id)
        with pytest.raises(WorkspaceForkConflict):
            self._run(self.store.fork(SCOPE, origin, at_revision=other, fork_id=fork_id))
        assert self._head(fork_id) == before

    @rule(fork_id=consumes(forks), keep_target=st.booleans())
    def merge(self, fork_id: str, keep_target: bool) -> None:
        origin, _ = self.origins[fork_id]
        before = self._head(origin)
        result = self._run(
            self.store.merge(SCOPE, fork_id=fork_id, into=origin, on_conflict="keep_target" if keep_target else "refuse")
        )
        after = self._keep(self._head(origin))
        if result.status in ("conflict", "up_to_date"):
            assert after == before
        else:
            assert after == before or after.revision == before.revision + 1

    @rule(data=st.data())
    def rollback(self, data: st.DataObject) -> None:
        workspace = data.draw(st.sampled_from(sorted(self.workspaces)))
        head = self._head(workspace)
        to = data.draw(st.integers(0, head.revision))
        after = self._keep(self._run(self.store.rollback(SCOPE, workspace, to_revision=to)))
        assert after.revision == head.revision + 1
        assert dict(after.files) == dict(self._run(self.store.revision(SCOPE, workspace, to)).files)

    # ---- invariants ----

    @invariant()
    def every_reachable_object_is_stored_and_reads_back(self) -> None:
        for (workspace, revision), _ in list(self.history.items()):
            manifest = self._run(self.store.revision(SCOPE, workspace, revision))
            for entry in manifest.files.values():
                _ = self._run(self.objects.get(SCOPE, entry.content))

    @invariant()
    def no_revision_ever_changes(self) -> None:
        for (workspace, revision), kept in self.history.items():
            assert self._run(self.store.revision(SCOPE, workspace, revision)) == kept

    @invariant()
    def every_manifest_hash_is_the_hash_of_its_files(self) -> None:
        for kept in self.history.values():
            assert kept.content_hash == workspace_manifest_content_hash(kept.files)

    @invariant()
    def heads_move_one_revision_at_a_time(self) -> None:
        for workspace in self.workspaces:
            head = self._head(workspace)
            assert head.parent_revision == (head.revision - 1 if head.revision > 1 else None)

    @invariant()
    def objects_are_only_what_was_written(self) -> None:
        assert self.objects.object_count == len(self.written)

    @override
    def teardown(self) -> None:
        self.loop.close()


@pytest.mark.timeout(600)
def test_wsp12_the_workspace_revision_machine_keeps_its_invariants() -> None:
    run_state_machine_as_test(
        WorkspaceRevisionMachine,
        settings=settings(
            stateful_step_count=30, suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much]
        ),
    )
