"""Workspace forks of child runs (P6.4): their names, and the parent's merge at the join.

A child granted a workspace works on a fork of its parent's workspace; when it
has ended, its parent merges the fork back with ``keep_target``: the child's
non-conflicting changes land, the parent keeps its own side of every conflict,
and the report lists each one with the fork revision that holds the child's
side. Nothing is resolved without being reported.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

from harness_core.workspace.changes import ChangeSet
from harness_core.workspace.types import (
    MAX_WORKSPACE_PATH_BYTES,
    MAX_WORKSPACE_PATH_DEPTH,
    ObjectScope,
    WorkspaceFile,
    WorkspaceNotAFork,
    WorkspaceRevisionConflict,
    WorkspaceWriterBusy,
    repository_of,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from pydantic import JsonValue

    from harness_core.journal.types import AttemptToken
    from harness_core.workspace.changes import ConflictKind, FileChangeKind, MergeResult
    from harness_core.workspace.ports import WorkspaceStore, WorkspaceWriters

WRITER_LEASE_MS = 120_000
MERGE_ATTEMPTS = 3
# Outcomes whose work is offered to the parent; a failed or cancelled child's fork stays as it is.
MERGED_OUTCOMES = frozenset({"answered", "needs_input"})
REPORT_PATHS = 50
# WP-56: where a failed child's own changes are kept in its parent's workspace, one folder per child, so the person
# gets what it saved without any of it standing in for the conversation's files (CW3).
UNFINISHED_WORK_FOLDER = "Unfinished work"
# A folder name another failed child already took (the same second) moves on to " (2)", " (3)" ...
_UNFINISHED_SUFFIXES = 20

ChildMergeStatus = Literal["merged", "up_to_date", "partial", "not_merged"]

_FORK = ":fork:"
_PRINCIPAL = ":p:"


def session_workspace_id(session_id: str) -> str:
    """The one workspace a session's root runs write."""
    return f"ws:{session_id}"


def principal_workspace_id(session_id: str, actor_id: str, *, collaborator: bool) -> str:
    """The workspace one principal's root runs of a session write (OWD-16): the session's own for its owner, and
    one of their own beside it for a collaborator, so files fetched under two people's access never mix."""
    root = session_workspace_id(session_id)
    return f"{root}{_PRINCIPAL}{actor_id}" if collaborator else root


def fork_root(workspace_id: str) -> str:
    """The principal workspace a workspace or any of its forks belongs to."""
    return workspace_id.split(_FORK, 1)[0]


def workspace_of_principal(workspace_id: str, session_id: str, actor_id: str, *, collaborator: bool) -> bool:
    """Whether the workspace is this principal's own or one under it, such as a fork (Node's workspace routes check
    the same). The owner's tree is the session's, less every collaborator's."""
    root = principal_workspace_id(session_id, actor_id, collaborator=collaborator)
    if workspace_id != root and not workspace_id.startswith(f"{root}:"):
        return False
    return collaborator or not workspace_id.startswith(f"{root}{_PRINCIPAL}")


def child_fork_of(origin: str, child_run_id: str) -> str:
    """A child run's fork of ``origin``: flat under its principal's workspace whatever the depth (child run ids are
    unique)."""
    return f"{fork_root(origin)}{_FORK}{child_run_id}"


def child_fork_id(session_id: str, child_run_id: str) -> str:
    """The fork of a child of the session owner's runs."""
    return child_fork_of(session_workspace_id(session_id), child_run_id)


def session_scope(tenant_id: str, session_id: str) -> ObjectScope:
    """The session's partition: a fork's objects are its origin's, under the session's key."""
    return ObjectScope(tenant_id, session_workspace_id(session_id))


@dataclass(frozen=True)
class JoinedChild:
    """A child run its parent has joined; ``workspace_id`` is the parent's own workspace."""

    tenant_id: str
    session_id: str
    parent_run_id: str
    workspace_id: str
    child_run_id: str
    outcome: str | None

    @property
    def fork_id(self) -> str:
        return child_fork_of(self.workspace_id, self.child_run_id)


@dataclass(frozen=True)
class ChildMergeReport:
    """What the parent's model reads of a child's files."""

    child_run_id: str
    fork_id: str
    into: str
    status: ChildMergeStatus
    revision: int | None = None
    fork_revision: int | None = None
    merged: tuple[str, ...] = ()
    conflicts: tuple[tuple[str, ConflictKind], ...] = ()
    repositories: tuple[str, ...] = ()
    reason: str | None = None
    # A failed child's saved work, kept in the parent's workspace under ``unfinished`` (WP-56): its paths there,
    # relative to that folder.
    unfinished: str | None = None
    kept: tuple[str, ...] = ()
    # How the merge changed each ``merged`` path; journaled, not shown to the model.
    kinds: Mapping[str, FileChangeKind] = field(default_factory=lambda: MappingProxyType({}))

    @classmethod
    def of(cls, child_run_id: str, result: MergeResult) -> ChildMergeReport:
        if result.status == "conflict":
            raise ValueError("a keep_target merge never refuses")
        return cls(
            child_run_id=child_run_id,
            fork_id=result.fork_id,
            into=result.into,
            status=result.status,
            revision=result.manifest.revision,
            fork_revision=result.fork_revision,
            merged=result.merged,
            conflicts=tuple((c.path, c.kind) for c in result.conflicts),
            repositories=result.repository_conflicts,
            kinds=result.kinds,
        )

    def as_json(self) -> dict[str, JsonValue]:
        """Bounded: at most ``REPORT_PATHS`` paths per list, ``truncated`` when cut."""
        out: dict[str, JsonValue] = {"status": self.status}
        if self.reason is not None:
            out["reason"] = self.reason
        if self.merged:
            out["merged"] = list(self.merged[:REPORT_PATHS])
        if self.conflicts:
            out["conflicts"] = [
                {"path": path, "kind": kind} for path, kind in self.conflicts[:REPORT_PATHS]
            ]
            out["kept"] = "parent"
        if self.repositories:
            out["repositories"] = list(self.repositories[:REPORT_PATHS])
        if max(len(self.merged), len(self.conflicts), len(self.repositories)) > REPORT_PATHS:
            out["truncated"] = True
        if self.fork_revision is not None and (self.conflicts or self.repositories or self.status == "not_merged"):
            out["fork"] = {"workspaceId": self.fork_id, "revision": self.fork_revision}
        if self.unfinished is not None:
            # ``count`` is every kept file; ``files`` lists at most ``REPORT_PATHS`` of them (R13-SALV).
            out["unfinished"] = {
                "folder": self.unfinished,
                "files": list(self.kept[:REPORT_PATHS]),
                "count": len(self.kept),
            }
            if len(self.kept) > REPORT_PATHS:
                out["truncated"] = True
        return out

    def summary(self) -> str:
        """One line for a text outcome (a programmatic dispatch reads text, not JSON)."""
        parts = [self.status]
        if self.reason is not None:
            parts.append(f"reason {self.reason}")
        if self.merged:
            parts.append(f"merged {', '.join(self.merged[:REPORT_PATHS])}")
        if self.conflicts:
            shown = ", ".join(f"{p} ({k})" for p, k in self.conflicts[:REPORT_PATHS])
            parts.append(f"conflicts kept the parent's version: {shown}")
        if self.repositories:
            parts.append(f"repositories not merged: {', '.join(self.repositories[:REPORT_PATHS])}")
        if self.fork_revision is not None and (self.conflicts or self.repositories or self.status == "not_merged"):
            parts.append(f"the sub-agent's version is in {self.fork_id} revision {self.fork_revision}")
        if self.unfinished is not None:
            parts.append(f"its unfinished work was saved to the folder {self.unfinished}")
        return "; ".join(parts)


class StoreChildWorkspaces:
    """``ChildWorkspaces`` over any ``WorkspaceStore`` and ``WorkspaceWriters``.

    ``stores`` gives the pair an attempt may use (Node binds them to the
    attempt's token; tests share one in-memory pair). The merge is keyed by the
    child run id, so asking twice merges once and reports the same.
    """

    def __init__(
        self,
        stores: Callable[[AttemptToken], tuple[WorkspaceStore, WorkspaceWriters]],
        *,
        lease_ms: int = WRITER_LEASE_MS,
        attempts: int = MERGE_ATTEMPTS,
    ) -> None:
        self._stores = stores
        self._lease_ms = lease_ms
        self._attempts = attempts

    async def merge(self, token: AttemptToken, child: JoinedChild) -> ChildMergeReport | None:
        store, writers = self._stores(token)
        scope = session_scope(child.tenant_id, child.session_id)
        if child.outcome not in MERGED_OUTCOMES:
            return await self._not_merged(store, scope, child, f"child_{child.outcome or 'failed'}")
        try:
            lease = await writers.acquire(
                scope, child.workspace_id, holder=child.parent_run_id, ttl_ms=self._lease_ms
            )
        except WorkspaceWriterBusy:
            return await self._not_merged(store, scope, child, "writer_busy")
        try:
            result = await self._merge(store, scope, child)
        except WorkspaceNotAFork:
            return None
        finally:
            if not lease.renewed:
                await writers.release(scope, child.workspace_id, holder=child.parent_run_id)
        return ChildMergeReport.of(child.child_run_id, result)

    async def keep_unfinished(
        self, token: AttemptToken, child: JoinedChild, report: ChildMergeReport
    ) -> ChildMergeReport:
        """A failed child's own changes (files it added or changed; never a repository's internals), kept in a
        folder of their own in the parent's workspace (WP-56). The merge still offers none of it (CW3): no file of the
        conversation's own is written. Asking again writes nothing and reports the same folder. ``report`` comes
        back as it was when there is nothing to keep, or another writer holds the parent's workspace."""
        if report.status != "not_merged" or report.reason != "child_failed" or report.unfinished is not None:
            return report
        store, writers = self._stores(token)
        scope = session_scope(child.tenant_id, child.session_id)
        head = await store.head(scope, child.fork_id)
        if head.revision <= 1:
            return report
        start = await store.revision(scope, child.fork_id, 1)
        diff = await store.diff(scope, start, head)
        own = {
            path: entry
            for path, entry in {**diff.added, **diff.modified}.items()
            if repository_of(path) is None
        }
        if not own:
            return report
        try:
            lease = await writers.acquire(
                scope, child.workspace_id, holder=child.parent_run_id, ttl_ms=self._lease_ms
            )
        except WorkspaceWriterBusy:
            return report
        try:
            return await self._keep(store, scope, child, report, own, head.created_at_ms)
        finally:
            if not lease.renewed:
                await writers.release(scope, child.workspace_id, holder=child.parent_run_id)

    async def _keep(
        self,
        store: WorkspaceStore,
        scope: ObjectScope,
        child: JoinedChild,
        report: ChildMergeReport,
        own: dict[str, WorkspaceFile],
        saved_at_ms: int,
    ) -> ChildMergeReport:
        stamp = datetime.fromtimestamp(saved_at_ms / 1000, tz=UTC).strftime("%Y-%m-%d %H.%M.%S UTC")
        for attempt in range(1, self._attempts + 1):
            target = await store.head(scope, child.workspace_id)
            for n in range(1, _UNFINISHED_SUFFIXES + 1):
                folder = f"{UNFINISHED_WORK_FOLDER}/{stamp}" + ("" if n == 1 else f" ({n})")
                files = _under(folder, own)
                there = {p: f for p, f in target.files.items() if p.startswith(f"{folder}/")}
                if there and (there.keys() != files.keys() or not all(
                    there[p].same_content(f) for p, f in files.items()
                )):
                    continue
                kept = tuple(sorted(p[len(folder) + 1 :] for p in files))
                if there:
                    return replace(report, unfinished=folder, kept=kept, revision=target.revision)
                try:
                    committed = await store.commit(
                        scope,
                        child.workspace_id,
                        base_revision=target.revision,
                        changes=ChangeSet.of(*files.values()),
                        # A worker may not name ``merge`` (Node makes merges); this is the parent's own write.
                        reason="turn_end",
                    )
                except WorkspaceRevisionConflict:
                    if attempt == self._attempts:
                        raise
                    break
                return replace(report, unfinished=folder, kept=kept, revision=committed.revision)
            else:
                return report
        return report

    async def _merge(
        self, store: WorkspaceStore, scope: ObjectScope, child: JoinedChild
    ) -> MergeResult:
        for attempt in range(1, self._attempts + 1):
            try:
                return await store.merge(
                    scope,
                    fork_id=child.fork_id,
                    into=child.workspace_id,
                    key=child.child_run_id,
                    on_conflict="keep_target",
                )
            except WorkspaceRevisionConflict:
                # Another writer moved the target between the merge's read and its commit.
                if attempt == self._attempts:
                    raise
        raise AssertionError("unreachable")

    @staticmethod
    async def _not_merged(
        store: WorkspaceStore, scope: ObjectScope, child: JoinedChild, reason: str
    ) -> ChildMergeReport | None:
        head = await store.head(scope, child.fork_id)
        if head.revision == 0:
            return None
        return ChildMergeReport(
            child_run_id=child.child_run_id,
            fork_id=child.fork_id,
            into=child.workspace_id,
            status="not_merged",
            fork_revision=head.revision,
            reason=reason,
        )


def _under(folder: str, files: dict[str, WorkspaceFile]) -> dict[str, WorkspaceFile]:
    """``files`` moved under ``folder``; a path the move would make too long or too deep stays behind."""
    out: dict[str, WorkspaceFile] = {}
    for path, entry in files.items():
        moved = f"{folder}/{path}"
        if len(moved.encode("utf-8")) > MAX_WORKSPACE_PATH_BYTES or moved.count("/") + 1 > MAX_WORKSPACE_PATH_DEPTH:
            continue
        out[moved] = replace(entry, path=moved)
    return out


__all__ = [
    "MERGED_OUTCOMES",
    "UNFINISHED_WORK_FOLDER",
    "REPORT_PATHS",
    "WRITER_LEASE_MS",
    "ChildMergeReport",
    "ChildMergeStatus",
    "JoinedChild",
    "StoreChildWorkspaces",
    "child_fork_id",
    "child_fork_of",
    "fork_root",
    "principal_workspace_id",
    "session_scope",
    "session_workspace_id",
    "workspace_of_principal",
]
