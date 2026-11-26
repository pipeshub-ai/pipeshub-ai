"""``WorkspaceSaver``: collect a sandbox's changes and commit them, rebasing when the head moved.

The engine controller (E0) calls ``save`` for autosave, before external
actions and at turn end. A commit that loses its CAS (P3.2: a retried commit
whose first response was lost, or another writer) re-reads the head and
rebases the sandbox's changes onto it, so a lost response never adds a
second revision and another writer's files are not deleted.

A save answers with a typed result (ADR-RUN-01 D2, D6): ``Saved``, ``PartialSave``
(the files past the store's cap were left out, or the store refused the whole
change set and only the ``fallback`` paths, the deliverables, were committed:
WP-82) or ``Refused``. A refusal the store gives as sent never raises out of it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from harness_core.logs import HarnessLog
from harness_core.workspace.changes import ChangeSet, diff_files
from harness_core.workspace.sync.storage_classes import merged_references
from harness_core.workspace.sync.types import ON_FIRST_READ, WORKSPACE_FULL, SyncState
from harness_core.workspace.types import SAVE_REFUSALS, WorkspaceRevisionConflict

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from harness_core.sandbox.ports import SandboxHandle
    from harness_core.workspace.changes import CommitReason
    from harness_core.workspace.ports import WorkspaceStore, WorkspaceSync
    from harness_core.workspace.sync.types import Collected
    from harness_core.workspace.types import (
        ObjectScope,
        WorkspaceFile,
        WorkspaceManifest,
    )

LOGGER = "harness_core.workspace.sync"
# Why a changed path is not in a ``PartialSave`` save's revision when the store refused the whole change set.
SAVE_REFUSED = "save_refused"


def _same(left: WorkspaceFile | None, right: WorkspaceFile | None) -> bool:
    if left is None or right is None:
        return left is right
    return left.same_content(right)


@dataclass(frozen=True, slots=True)
class Rebased:
    changes: ChangeSet
    # Paths the head had changed too and the sandbox's version replaced.
    overwritten: frozenset[str]


def rebase_changes(
    base: Mapping[str, WorkspaceFile], head: Mapping[str, WorkspaceFile], changes: ChangeSet
) -> Rebased:
    """``changes`` (made against ``base``) as a change set for ``head``.

    Writes ``head`` already has and deletes of paths ``head`` no longer has
    are dropped. Where ``head`` changed a path the sandbox changed too, the
    sandbox wins: it is the session's single writer and holds the latest
    intent; the other version stays in the workspace's history.
    """
    upserts: dict[str, WorkspaceFile] = {}
    deletes: set[str] = set()
    overwritten: set[str] = set()
    for path, entry in changes.upserts.items():
        theirs = head.get(path)
        if entry.same_content(theirs):
            continue
        if not _same(theirs, base.get(path)):
            overwritten.add(path)
        upserts[path] = entry
    for path in changes.deletes:
        if path not in head:
            continue
        if not _same(head[path], base.get(path)):
            overwritten.add(path)
        deletes.add(path)
    return Rebased(ChangeSet(upserts, frozenset(deletes)), frozenset(overwritten))


def narrowed(collected: Collected, only: Callable[[str], bool]) -> Collected:
    """``collected`` with only the changes of the paths ``only`` accepts; what was skipped stays named."""
    changes = ChangeSet(
        {path: entry for path, entry in collected.changes.upserts.items() if only(path)},
        frozenset(path for path in collected.changes.deletes if only(path)),
    )
    return replace(collected, changes=changes)


@dataclass(frozen=True, slots=True)
class Saved:
    """``state`` is the sandbox's new base; ``manifest`` is ``None`` when nothing needed committing."""

    state: SyncState
    collected: Collected
    manifest: WorkspaceManifest | None
    rebased: bool = False
    overwritten: int = 0

    @property
    def revision(self) -> int:
        return self.state.revision


@dataclass(frozen=True, slots=True)
class PartialSave:
    """Some of the changes were saved (``saved``); ``skipped`` names the changed paths that were not, and ``reason``
    is why: ``workspace_full`` (the store's file cap, enforced before the request) or ``save_refused`` (the store
    refused the whole change set, ``refusal`` names its error, and only the fallback paths were committed)."""

    saved: Saved
    skipped: Mapping[str, str]
    reason: str
    refusal: str | None = None

    @property
    def state(self) -> SyncState:
        return self.saved.state

    @property
    def revision(self) -> int:
        return self.saved.revision


@dataclass(frozen=True, slots=True)
class Refused:
    """Nothing was saved: the store refused the change set as sent (and the fallback too, when there was one). The
    sandbox's base stays ``state``, the last saved revision."""

    state: SyncState
    reason: str
    error: str
    attempted_fallback: bool = False


SaveResult = Saved | PartialSave | Refused


class WorkspaceSaver:
    def __init__(
        self,
        *,
        sync: WorkspaceSync,
        store: WorkspaceStore,
        scope: ObjectScope,
        max_attempts: int = 3,
        log: HarnessLog | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self._sync = sync
        self._store = store
        self._scope = scope
        self._attempts = max_attempts
        self._log = log or HarnessLog.named(LOGGER)

    async def save(
        self,
        sandbox: SandboxHandle,
        state: SyncState,
        *,
        reason: CommitReason,
        before_commit: Callable[[Collected], Awaitable[None]] | None = None,
        fallback: Callable[[str], bool] | None = None,
    ) -> SaveResult:
        """``before_commit`` runs once there is something to commit, before the first commit is tried:
        what must be recorded before the revision can exist (the publication intents of its outputs).

        ``fallback`` accepts the paths that are committed on their own when the store refuses the whole change set
        (WP-82: the deliverables); the rest stay changes the next save finds again."""
        collected = await self._sync.collect(sandbox, state)
        try:
            saved = await self._commit(sandbox, state, collected, reason, before_commit)
        except SAVE_REFUSALS as refused:
            error = type(refused).__name__
            if fallback is None:
                return Refused(state, str(refused)[:64], error)
            narrow = narrowed(collected, fallback)
            try:
                saved = await self._commit(sandbox, state, narrow, reason, before_commit)
            except SAVE_REFUSALS as again:
                return Refused(state, str(again)[:64], type(again).__name__, attempted_fallback=True)
            left = dict.fromkeys(sorted(collected.changes.paths - narrow.changes.paths), SAVE_REFUSED)
            return PartialSave(saved, left, SAVE_REFUSED, f"{error}: {str(refused)[:64]}")
        full = {path: why for path, why in collected.skipped.items() if why == WORKSPACE_FULL}
        return PartialSave(saved, full, WORKSPACE_FULL) if full else saved

    async def _commit(
        self,
        sandbox: SandboxHandle,
        state: SyncState,
        collected: Collected,
        reason: CommitReason,
        before_commit: Callable[[Collected], Awaitable[None]] | None,
    ) -> Saved:
        if collected.changes.is_empty:
            return Saved(state, collected, None)
        if before_commit is not None:
            await before_commit(collected)
        log = self._log.bind(sandboxId=sandbox.id, workspaceId=state.workspace_id, reason=reason)
        base = state.manifest
        changes = collected.changes
        overwritten: frozenset[str] = frozenset()
        head: WorkspaceManifest | None = None
        for _ in range(self._attempts):
            if changes.is_empty:
                head = base
                break
            try:
                head = await self._store.commit(
                    self._scope, state.workspace_id, base_revision=base.revision, changes=changes, reason=reason
                )
                break
            except WorkspaceRevisionConflict:
                base = await self._store.head(self._scope, state.workspace_id)
                rebased = rebase_changes(state.manifest.files, base.files, collected.changes)
                changes, overwritten = rebased.changes, rebased.overwritten
        if head is None:
            log.warning("workspace_sync.refused", reason="revision_conflict", attempts=self._attempts)
            raise WorkspaceRevisionConflict(f"the head of {state.workspace_id} kept moving")
        rebased_onto = base.revision != state.revision
        if overwritten:
            log.warning("workspace_sync.rebased", fromRevision=state.revision, toRevision=base.revision, overwritten=len(overwritten))
        foreign = diff_files(state.manifest.files, head.files).paths - collected.changes.paths
        if foreign:
            # The head brought changes the sandbox lacks; without them the next collect would undo them.
            deferred = [path for path, why in state.hidden.items() if why == ON_FIRST_READ]
            refreshed = await self._sync.materialize(sandbox, head, root=state.root, defer=deferred, keep_scratch=True)
            new_state = replace(
                refreshed,
                references=merged_references(refreshed.references, collected.references),
                vouched=state.vouched,
            )
        else:
            hidden = {
                path: why
                for path, why in state.hidden.items()
                if path in head.files and path not in collected.changes.paths
            }
            new_state = SyncState(
                state.sandbox_id, state.root, head, hidden, collected.references or state.references, state.vouched
            )
        log.info(
            "workspace_sync.saved",
            fromRevision=state.revision,
            revision=head.revision,
            upserts=len(changes.upserts),
            deletes=len(changes.deletes),
            rebased=rebased_onto,
            refreshed=bool(foreign),
        )
        return Saved(new_state, collected, head, rebased_onto, len(overwritten))


__all__ = [
    "SAVE_REFUSED",
    "PartialSave",
    "Rebased",
    "Refused",
    "SaveResult",
    "Saved",
    "WorkspaceSaver",
    "narrowed",
    "rebase_changes",
]
