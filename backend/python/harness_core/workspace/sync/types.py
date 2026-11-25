"""Value types and typed refusals of ``WorkspaceSync`` (P5.5)."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, ClassVar, Final

from harness_core.workspace.types import MAX_WORKSPACE_FILES, WorkspaceError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.workspace.changes import ChangeSet
    from harness_core.workspace.sync.storage_classes import Reference
    from harness_core.workspace.types import WorkspaceManifest

# Why a path is kept out of a sandbox (materialize) or out of a change set (collect).
ENGINE_CONFIG: Final = "engine_config"
SHADOWED: Final = "shadowed"
SYMLINK_ESCAPES: Final = "symlink_escapes"
# C5.01 (J-26): a large input the sandbox gets only when the engine first asks to read it (``stage``).
ON_FIRST_READ: Final = "on_first_read"
UNSTORABLE_PATH: Final = "unstorable_path"
HARD_LINK: Final = "hard_link"
SPECIAL_FILE: Final = "special_file"
FILE_TOO_LARGE: Final = "file_too_large"
# The archive's files together expand past the sync's cap or ratio (hard links to one big file, SEC-FS-03).
EXPANSION_LIMIT: Final = "expansion_limit"
# ADR-RUN-01 D2 (``storage_classes.py``): a path inside a reference (a repository ``ph git clone`` checked out, WP-82)
# and an engine's scratch file stay in the sandbox and out of the store; the record folder is the harness's own.
REFERENCE_COPY: Final = "reference"
SCRATCH_FILE: Final = "scratch"
STORAGE_RECORD: Final = "storage_record"
# WP-82: a new file past the store's file cap (``MAX_WORKSPACE_FILES``); the files nearest the root are kept.
WORKSPACE_FULL: Final = "workspace_full"
SKIP_REASONS: Final = (
    ENGINE_CONFIG,
    SHADOWED,
    SYMLINK_ESCAPES,
    UNSTORABLE_PATH,
    HARD_LINK,
    SPECIAL_FILE,
    FILE_TOO_LARGE,
    EXPANSION_LIMIT,
    REFERENCE_COPY,
    SCRATCH_FILE,
    STORAGE_RECORD,
    WORKSPACE_FULL,
)

_NOTHING: Mapping[str, str] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class SyncLimits:
    """Bounds on what a sandbox archive may make the sync read (tar bombs)."""

    max_entries: int = 50_000
    max_file_bytes: int = 100 * 1024**2
    max_archive_bytes: int = 2 * 1024**3
    # What all collected files may add up to, past which the rest are skipped (``EXPANSION_LIMIT``); the ratio
    # to the archive's size and its floor are the archive limits' (``HARNESS_ARCHIVE_*``).
    max_expanded_bytes: int = 2 * 1024**3
    # The store's file cap (WP-82): a collect never proposes a revision with more files; past it new files are
    # skipped (``WORKSPACE_FULL``), so a save stays a commit the store takes.
    max_workspace_files: int = MAX_WORKSPACE_FILES

    def __post_init__(self) -> None:
        if (
            min(
                self.max_entries,
                self.max_file_bytes,
                self.max_archive_bytes,
                self.max_expanded_bytes,
                self.max_workspace_files,
            )
            <= 0
        ):
            raise ValueError("sync limits must be positive")


def write_back_limits(max_file_bytes: int) -> SyncLimits:
    """The sync's bounds when the largest file is the deployment's largest input (``HARNESS_INPUT_MAX_MB``, J-26): an
    engine can write back changed any file it could be given, and the archive and expansion bounds never refuse a
    file that size."""
    default = SyncLimits()
    return SyncLimits(
        max_entries=default.max_entries,
        max_file_bytes=max_file_bytes,
        max_archive_bytes=max(default.max_archive_bytes, max_file_bytes),
        max_expanded_bytes=max(default.max_expanded_bytes, max_file_bytes),
    )


class WorkspaceSyncRefused(WorkspaceError):
    """The sync refused a sandbox's archive or could not change the sandbox; ``reason_code`` says why."""

    reason_code: ClassVar[str] = "workspace_sync_refused"


class UnsafeArchiveMember(WorkspaceSyncRefused):
    """A member outside the root, absolute, traversing or with a NUL: a correct broker never sends one."""

    reason_code = "unsafe_member"


class ArchiveMalformed(WorkspaceSyncRefused):
    reason_code = "archive_malformed"


class TooManyEntries(WorkspaceSyncRefused):
    reason_code = "too_many_entries"


class ArchiveTooLarge(WorkspaceSyncRefused):
    reason_code = "workspace_too_large"


class SandboxRemoveFailed(WorkspaceSyncRefused):
    reason_code = "sync_remove_failed"


@dataclass(frozen=True, slots=True)
class SyncState:
    """What a sandbox's folder holds: ``manifest`` minus ``hidden`` (path → reason it was kept out).

    The base of the next ``collect``: a path in ``hidden`` is never deleted from
    the workspace because the sandbox lacks it. ``references``: the session's
    references as the revision records them, and as this sandbox found them since.
    """

    sandbox_id: str
    root: str
    manifest: WorkspaceManifest
    hidden: Mapping[str, str] = field(default_factory=lambda: _NOTHING)
    references: tuple[Reference, ...] = ()
    # The identities (``reference_identity``) of the repositories the worker fetched for this run: a mark the sandbox
    # holds is recorded as a reference only for one of them.
    vouched: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        object.__setattr__(self, "hidden", MappingProxyType(dict(self.hidden)))

    @property
    def revision(self) -> int:
        return self.manifest.revision

    @property
    def workspace_id(self) -> str:
        return self.manifest.workspace_id


@dataclass(frozen=True, slots=True)
class Collected:
    """``changes`` against the state's revision; ``skipped`` names sandbox paths left out and why; ``references``:
    the session's references once this change set is committed (its record is one of the changes when they moved)."""

    changes: ChangeSet
    skipped: Mapping[str, str] = field(default_factory=lambda: _NOTHING)
    uploaded: int = 0
    references: tuple[Reference, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "skipped", MappingProxyType(dict(self.skipped)))


__all__ = [
    "ENGINE_CONFIG",
    "EXPANSION_LIMIT",
    "FILE_TOO_LARGE",
    "HARD_LINK",
    "ON_FIRST_READ",
    "REFERENCE_COPY",
    "SCRATCH_FILE",
    "SHADOWED",
    "SKIP_REASONS",
    "SPECIAL_FILE",
    "STORAGE_RECORD",
    "SYMLINK_ESCAPES",
    "UNSTORABLE_PATH",
    "ArchiveMalformed",
    "ArchiveTooLarge",
    "Collected",
    "SandboxRemoveFailed",
    "SyncLimits",
    "SyncState",
    "TooManyEntries",
    "UnsafeArchiveMember",
    "WORKSPACE_FULL",
    "WorkspaceSyncRefused",
    "write_back_limits",
]
