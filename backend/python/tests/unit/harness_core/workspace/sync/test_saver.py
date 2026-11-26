"""SV-1..SV-6: saving a sandbox's changes, and what happens when the head moved underneath."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from harness_core.testing.contracts.workspace import file
from harness_core.testing.contracts.workspace_sync import (
    WORKSPACE,
    acquire,
    commit,
    expected,
    tree,
)
from harness_core.workspace import ChangeSet, WorkspaceRevisionConflict
from harness_core.workspace.sync import TAR, WorkspaceSaver, rebase_changes
from tests.unit.harness_core.workspace.sync.support import FakeSyncHarness

if TYPE_CHECKING:
    from harness_core.workspace import CommitReason, ObjectScope, WorkspaceManifest


def test_sv1_rebase_table() -> None:
    base = {"same": file("same", "0"), "theirs": file("theirs", "0"), "both": file("both", "0"), "gone": file("gone", "0")}
    head = {"same": file("same", "1"), "theirs": file("theirs", "2"), "both": file("both", "3")}
    ours = ChangeSet(
        {"same": file("same", "1"), "both": file("both", "4"), "new": file("new", "1")},
        frozenset({"gone", "theirs"}),
    )
    rebased = rebase_changes(base, head, ours)
    assert set(rebased.changes.upserts) == {"both", "new"}
    assert rebased.changes.deletes == frozenset({"theirs"})
    assert rebased.overwritten == frozenset({"both", "theirs"})


class _Store:
    """The harness's store, with commits that can lose their response or race another writer."""

    def __init__(self, harness: FakeSyncHarness) -> None:
        self.inner = harness.store
        self.lose_next_response = False
        self.conflicts_left = 0
        self.commits = 0

    async def head(self, scope: ObjectScope, workspace_id: str) -> WorkspaceManifest:
        return await self.inner.head(scope, workspace_id)

    async def commit(
        self, scope: ObjectScope, workspace_id: str, *, base_revision: int, changes: ChangeSet, reason: CommitReason
    ) -> WorkspaceManifest:
        self.commits += 1
        if self.conflicts_left:
            self.conflicts_left -= 1
            raise WorkspaceRevisionConflict("raced")
        manifest = await self.inner.commit(
            scope, workspace_id, base_revision=base_revision, changes=changes, reason=reason
        )
        if self.lose_next_response:
            self.lose_next_response = False
            raise WorkspaceRevisionConflict("the response was lost; the commit was not")
        return manifest


@pytest.fixture
def harness() -> FakeSyncHarness:
    return FakeSyncHarness(TAR)


async def _saver(harness: FakeSyncHarness, store: _Store, attempts: int = 3) -> WorkspaceSaver:
    return WorkspaceSaver(sync=harness.sync, store=store, scope=harness.scope, max_attempts=attempts)  # type: ignore[arg-type]


async def test_sv2_a_lost_commit_response_adds_no_second_revision(harness: FakeSyncHarness) -> None:
    manifest = await commit(harness, {"a.txt": b"a"})
    sandbox = await acquire(harness)
    state = await harness.sync.materialize(sandbox, manifest, root=harness.root)
    await harness.hands.write(sandbox, f"{harness.root}/b.txt", b"b")
    store = _Store(harness)
    store.lose_next_response = True
    saved = await (await _saver(harness, store)).save(sandbox, state, reason="autosave")
    head = await harness.store.head(harness.scope, WORKSPACE)
    assert head.revision == 2
    assert store.commits == 1
    assert saved.state.manifest == head
    assert (await harness.sync.collect(sandbox, saved.state)).changes.is_empty


async def test_sv3_another_writers_files_reach_the_sandbox_and_are_kept(harness: FakeSyncHarness) -> None:
    manifest = await commit(harness, {"a.txt": b"a"})
    sandbox = await acquire(harness)
    state = await harness.sync.materialize(sandbox, manifest, root=harness.root)
    await harness.hands.write(sandbox, f"{harness.root}/mine.txt", b"mine")
    await commit(harness, {"theirs.txt": b"theirs"})
    saved = await (await _saver(harness, _Store(harness))).save(sandbox, state, reason="autosave")
    head = await harness.store.head(harness.scope, WORKSPACE)
    assert set(head.files) == {"a.txt", "mine.txt", "theirs.txt"}
    assert saved.rebased
    assert saved.state.manifest == head
    assert await tree(sandbox, harness.root) == await expected(harness, head, hidden={})
    assert (await harness.sync.collect(sandbox, saved.state)).changes.is_empty


async def test_sv4_on_the_same_path_the_sandbox_wins_and_it_is_logged(
    harness: FakeSyncHarness, caplog: pytest.LogCaptureFixture
) -> None:
    manifest = await commit(harness, {"shared.txt": b"0"})
    sandbox = await acquire(harness)
    state = await harness.sync.materialize(sandbox, manifest, root=harness.root)
    await harness.hands.write(sandbox, f"{harness.root}/shared.txt", b"sandbox")
    theirs = await commit(harness, {"shared.txt": b"other writer"})
    with caplog.at_level(logging.INFO, logger="harness_core.workspace.sync"):
        saved = await (await _saver(harness, _Store(harness))).save(sandbox, state, reason="turn_end")
    head = await harness.store.head(harness.scope, WORKSPACE)
    assert await harness.objects.get(harness.object_scope, head.files["shared.txt"].content) == b"sandbox"
    assert saved.overwritten == 1
    history = await harness.store.revision(harness.scope, WORKSPACE, theirs.revision)
    assert await harness.objects.get(harness.object_scope, history.files["shared.txt"].content) == b"other writer"
    line = next(r for r in caplog.records if r.getMessage().startswith("workspace_sync.rebased"))
    assert line.levelno == logging.WARNING
    assert line.harness["overwritten"] == 1  # type: ignore[attr-defined]


async def test_sv5_a_head_that_keeps_moving_is_a_conflict(harness: FakeSyncHarness) -> None:
    manifest = await commit(harness, {"a.txt": b"a"})
    sandbox = await acquire(harness)
    state = await harness.sync.materialize(sandbox, manifest, root=harness.root)
    await harness.hands.write(sandbox, f"{harness.root}/b.txt", b"b")
    store = _Store(harness)
    store.conflicts_left = 3
    with pytest.raises(WorkspaceRevisionConflict):
        await (await _saver(harness, store, attempts=3)).save(sandbox, state, reason="autosave")
    assert store.commits == 3


async def test_sv6_nothing_changed_commits_nothing(harness: FakeSyncHarness) -> None:
    manifest = await commit(harness, {"a.txt": b"a"})
    sandbox = await acquire(harness)
    state = await harness.sync.materialize(sandbox, manifest, root=harness.root)
    store = _Store(harness)
    saved = await (await _saver(harness, store)).save(sandbox, state, reason="autosave")
    assert (store.commits, saved.manifest, saved.state) == (0, None, state)


async def test_save_keeps_hidden_paths_hidden(harness: FakeSyncHarness) -> None:
    manifest = await commit(harness, {".opencode/plugin/x.ts": b"x", "a.txt": b"a"})
    sandbox = await acquire(harness)
    state = await harness.sync.materialize(sandbox, manifest, root=harness.root)
    await harness.hands.write(sandbox, f"{harness.root}/b.txt", b"b")
    saved = await (await _saver(harness, _Store(harness))).save(sandbox, state, reason="autosave")
    assert saved.manifest is not None
    assert ".opencode/plugin/x.ts" in saved.manifest.files
    assert set(saved.state.hidden) == {".opencode/plugin/x.ts"}
