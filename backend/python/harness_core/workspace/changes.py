"""Change sets, diffs and three-way merge planning: pure functions over files."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

from harness_core.workspace.types import (
    InvalidChangeSet,
    WorkspaceFile,
    freeze_files,
    normalize_workspace_file_path,
    repository_of,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.workspace.types import WorkspaceManifest

CommitReason = Literal["autosave", "turn_end", "before_effect", "seed", "merge", "rollback"]
COMMIT_REASONS: tuple[CommitReason, ...] = (
    "autosave",
    "turn_end",
    "before_effect",
    "seed",
    "merge",
    "rollback",
)
# `partial` (policy `keep_target` only): the fork's non-conflicting changes merged,
# the target kept its own version of every conflicting path and repository.
MergeStatus = Literal["merged", "up_to_date", "conflict", "partial"]
MERGE_STATUSES: tuple[MergeStatus, ...] = ("merged", "up_to_date", "conflict", "partial")
# `refuse`: all or nothing (a conflict merges nothing); `keep_target`: merge
# what does not conflict and keep the target's side of what does.
ConflictPolicy = Literal["refuse", "keep_target"]
ConflictKind = Literal["both_changed", "target_deleted", "fork_deleted"]
# How a commit changed one path (``workspace.committed`` ``kinds``).
FileChangeKind = Literal["added", "modified", "deleted"]
FILE_CHANGE_KINDS: tuple[FileChangeKind, ...] = ("added", "modified", "deleted")

_NO_FILES: Mapping[str, WorkspaceFile] = MappingProxyType({})


def _same(left: WorkspaceFile | None, right: WorkspaceFile | None) -> bool:
    if left is None or right is None:
        return left is right
    return left.same_content(right)


@dataclass(frozen=True, slots=True)
class ChangeSet:
    """Files to write and paths to delete, applied atomically by one commit."""

    upserts: Mapping[str, WorkspaceFile] = field(default_factory=lambda: _NO_FILES)
    deletes: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        try:
            upserts = freeze_files(self.upserts)
            deletes = frozenset(normalize_workspace_file_path(p) for p in self.deletes)
        except ValueError as exc:
            raise InvalidChangeSet(str(exc)) from exc
        if deletes != frozenset(self.deletes):
            raise InvalidChangeSet("Deleted paths must be canonical")
        if deletes & upserts.keys():
            raise InvalidChangeSet("A path cannot be both written and deleted")
        object.__setattr__(self, "upserts", upserts)
        object.__setattr__(self, "deletes", deletes)

    @classmethod
    def of(cls, *files: WorkspaceFile, deletes: frozenset[str] = frozenset()) -> ChangeSet:
        return cls({entry.path: entry for entry in files}, deletes)

    @property
    def is_empty(self) -> bool:
        return not self.upserts and not self.deletes

    @property
    def paths(self) -> frozenset[str]:
        return frozenset(self.upserts) | self.deletes

    def apply(self, files: Mapping[str, WorkspaceFile]) -> dict[str, WorkspaceFile]:
        missing = sorted(path for path in self.deletes if path not in files)
        if missing:
            raise InvalidChangeSet(f"Cannot delete {len(missing)} path(s) not in the workspace")
        result = {path: entry for path, entry in files.items() if path not in self.deletes}
        result.update(self.upserts)
        return result

    def kinds(self, before: Mapping[str, WorkspaceFile]) -> dict[str, FileChangeKind]:
        """How applying the change set to ``before`` changes each of its paths."""
        kinds: dict[str, FileChangeKind] = dict.fromkeys(self.deletes, "deleted")
        kinds.update({path: "modified" if path in before else "added" for path in self.upserts})
        return kinds

    def without_noops(self, files: Mapping[str, WorkspaceFile]) -> ChangeSet:
        """The change set minus writes that leave a file as it is."""
        return ChangeSet(
            {p: e for p, e in self.upserts.items() if not e.same_content(files.get(p))},
            self.deletes,
        )


@dataclass(frozen=True, slots=True)
class Diff:
    """From ``a`` to ``b``: entries are ``b``'s; ``modified`` means content or MIME type."""

    added: Mapping[str, WorkspaceFile]
    modified: Mapping[str, WorkspaceFile]
    removed: frozenset[str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "added", freeze_files(self.added))
        object.__setattr__(self, "modified", freeze_files(self.modified))

    @property
    def is_empty(self) -> bool:
        return not self.added and not self.modified and not self.removed

    @property
    def paths(self) -> frozenset[str]:
        return frozenset(self.added) | frozenset(self.modified) | self.removed

    def change_set(self) -> ChangeSet:
        return ChangeSet({**self.added, **self.modified}, self.removed)

    def kinds(self) -> dict[str, FileChangeKind]:
        kinds: dict[str, FileChangeKind] = dict.fromkeys(self.removed, "deleted")
        kinds.update(dict.fromkeys(self.added, "added"))
        kinds.update(dict.fromkeys(self.modified, "modified"))
        return kinds


def diff_files(a: Mapping[str, WorkspaceFile], b: Mapping[str, WorkspaceFile]) -> Diff:
    added = {path: entry for path, entry in b.items() if path not in a}
    modified = {
        path: entry
        for path, entry in b.items()
        if path in a and not entry.same_content(a[path])
    }
    return Diff(added, modified, frozenset(path for path in a if path not in b))


def diff_manifests(a: WorkspaceManifest, b: WorkspaceManifest) -> Diff:
    return diff_files(a.files, b.files)


@dataclass(frozen=True, slots=True)
class PathConflict:
    """Both sides changed ``path`` differently since the merge base; ``None`` is a delete."""

    path: str
    ours: WorkspaceFile | None
    theirs: WorkspaceFile | None

    @property
    def kind(self) -> ConflictKind:
        if self.ours is None:
            return "target_deleted"
        if self.theirs is None:
            return "fork_deleted"
        return "both_changed"


@dataclass(frozen=True, slots=True)
class MergePlan:
    changes: ChangeSet
    conflicts: tuple[PathConflict, ...]
    repository_conflicts: tuple[str, ...]

    @property
    def clean(self) -> bool:
        return not self.conflicts and not self.repository_conflicts


def plan_merge(
    base: Mapping[str, WorkspaceFile],
    ours: Mapping[str, WorkspaceFile],
    theirs: Mapping[str, WorkspaceFile],
) -> MergePlan:
    """Three-way merge of ``theirs`` into ``ours`` (ARCHITECTURE §4.3).

    A path changed on one side only takes that side; the same change on both
    is no conflict. A repository both sides touched is never merged path by
    path: its paths are left out and the repository is reported instead.
    """
    theirs_paths = diff_files(base, theirs).paths
    ours_paths = diff_files(base, ours).paths
    repositories = sorted(
        {r for p in theirs_paths if (r := repository_of(p)) is not None}
        & {r for p in ours_paths if (r := repository_of(p)) is not None}
    )
    blocked = frozenset(repositories)
    upserts: dict[str, WorkspaceFile] = {}
    deletes: set[str] = set()
    conflicts: list[PathConflict] = []
    for path in sorted(theirs_paths):
        if repository_of(path) in blocked:
            continue
        mine, yours = ours.get(path), theirs.get(path)
        if _same(mine, yours):
            continue
        if not _same(mine, base.get(path)):
            conflicts.append(PathConflict(path, mine, yours))
        elif yours is None:
            deletes.add(path)
        else:
            upserts[path] = yours
    return MergePlan(ChangeSet(upserts, frozenset(deletes)), tuple(conflicts), tuple(repositories))


@dataclass(frozen=True, slots=True)
class MergeResult:
    """``manifest``: the target after the merge (the revision it produced, else
    the head it read). ``merged``: the paths it wrote or deleted in the target,
    ``kinds`` how it changed each (empty from a Node that does not say).
    ``fork_revision``: the fork revision it took (where the fork's side of every
    conflict stays readable)."""

    status: MergeStatus
    fork_id: str
    into: str
    manifest: WorkspaceManifest
    conflicts: tuple[PathConflict, ...] = ()
    repository_conflicts: tuple[str, ...] = ()
    merged: tuple[str, ...] = ()
    fork_revision: int | None = None
    kinds: Mapping[str, FileChangeKind] = field(default_factory=lambda: MappingProxyType({}))
