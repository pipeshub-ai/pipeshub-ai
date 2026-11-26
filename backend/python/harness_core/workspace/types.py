"""Workspace value types: scopes, object references, files and manifests.

Promoted from ``app/harness/contracts/workspace.py`` (ARCHITECTURE §4.0). The
core knows no tenant model: a scope is an opaque tenant id plus an optional
partition, and every store keys its data by both.
"""

from __future__ import annotations

import hashlib
import json
import stat
import unicodedata
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


MAX_WORKSPACE_PATH_BYTES = 1024
MAX_WORKSPACE_PATH_DEPTH = 32
# The most files one revision holds: Node's store keeps a manifest in one document and refuses a commit past it
# (``MAX_WORKSPACE_FILES`` in ``domain/workspace.ts``; ``tests/cohesion/test_wp82_workspace_file_cap.py`` pins both).
MAX_WORKSPACE_FILES = 20_000
# Where a task's input files are placed in its workspace (a durable engine run's input artifacts).
INPUTS_DIR = "in"
MAX_SCOPE_ID_LENGTH = 128
MAX_PARTITION_LENGTH = 256
REPOSITORY_DIR = ".git"
# Git-like file modes: a regular file with its permission bits, or a symbolic
# link whose content is its target. Only non-default modes enter the manifest
# hash and the wire, so manifests written before modes keep their hash.
FILE_MODE = 0o100644
EXECUTABLE_MODE = 0o100755
SYMLINK_MODE = 0o120777
_PERMISSION_BITS = 0o777

_SCOPE_ID_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-"
)


def normalize_workspace_file_path(path: str) -> str:
    """Return the canonical relative path accepted inside a manifest."""
    if not isinstance(path, str) or not path:  # pyright: ignore[reportUnnecessaryIsInstance]
        raise ValueError("Workspace file path must be a non-empty string")
    if "\x00" in path or "\\" in path:
        raise ValueError("Workspace file path contains a forbidden character")
    normalized = unicodedata.normalize("NFC", path)
    if normalized.startswith("/"):
        raise ValueError("Workspace file path must be relative")
    if len(normalized.encode("utf-8")) > MAX_WORKSPACE_PATH_BYTES:
        raise ValueError("Workspace file path is too long")
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Workspace file path must be canonical and cannot traverse")
    if len(parts) > MAX_WORKSPACE_PATH_DEPTH:
        raise ValueError("Workspace file path is too deep")
    return "/".join(parts)


def valid_file_mode(mode: int) -> bool:
    """A regular file with permission bits only (no set-uid, set-gid or sticky), or a symbolic link."""
    return mode == SYMLINK_MODE or stat.S_IFREG <= mode <= stat.S_IFREG | _PERMISSION_BITS


def repository_of(path: str) -> str | None:
    """The ``.git`` directory a canonical path belongs to, or ``None``.

    ``src/.git/HEAD`` belongs to ``src/.git``; a ``.git`` file (a worktree's
    pointer) is its own repository entry.
    """
    parts = path.split("/")
    try:
        index = parts.index(REPOSITORY_DIR)
    except ValueError:
        return None
    return "/".join(parts[: index + 1])


def _valid_scope_id(value: str, max_length: int) -> bool:
    return 0 < len(value) <= max_length and all(c in _SCOPE_ID_CHARACTERS for c in value)


@dataclass(frozen=True, slots=True)
class ObjectScope:
    """Authority boundary supplied separately from an untrusted object reference.

    ``tenant_id`` is opaque here (PipesHub passes the org id). ``partition``
    narrows the scope to one encryption and dedupe domain, a session
    workspace in PipesHub; ``None`` means tenant-wide.
    """

    tenant_id: str
    partition: str | None = None

    def __post_init__(self) -> None:
        if not _valid_scope_id(self.tenant_id, MAX_SCOPE_ID_LENGTH):
            raise ValueError("Object scope has an invalid tenant ID")
        if self.partition is not None and not _valid_scope_id(
            self.partition, MAX_PARTITION_LENGTH
        ):
            raise ValueError("Object scope has an invalid partition")


@dataclass(frozen=True, slots=True)
class ObjectRef:
    object_id: str
    content_hash: str
    size_bytes: int

    def __post_init__(self) -> None:
        if not self.object_id or not self.content_hash or self.size_bytes < 0:
            raise ValueError("Object reference is incomplete")


@dataclass(frozen=True, slots=True)
class WorkspaceFile:
    path: str
    content: ObjectRef
    mime_type: str = "application/octet-stream"
    mode: int = FILE_MODE

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", normalize_workspace_file_path(self.path))
        if not valid_file_mode(self.mode):
            raise ValueError("Workspace file mode must be a regular file's permissions or a symbolic link")

    @property
    def is_symlink(self) -> bool:
        return self.mode == SYMLINK_MODE

    def same_content(self, other: WorkspaceFile | None) -> bool:
        """Equal for the manifest hash: object ids may differ between stores."""
        return (
            other is not None
            and self.content.content_hash == other.content.content_hash
            and self.content.size_bytes == other.content.size_bytes
            and self.mime_type == other.mime_type
            and self.mode == other.mode
        )


def freeze_files(files: Mapping[str, WorkspaceFile]) -> Mapping[str, WorkspaceFile]:
    """A read-only copy whose keys are the canonical paths of their entries."""
    immutable: dict[str, WorkspaceFile] = {}
    for path, entry in files.items():
        canonical = normalize_workspace_file_path(path)
        if canonical != path or entry.path != canonical:
            raise ValueError("Workspace manifest key must match its canonical file path")
        immutable[canonical] = entry
    return MappingProxyType(immutable)


@dataclass(frozen=True, slots=True)
class WorkspaceManifest:
    workspace_id: str
    revision: int
    parent_revision: int | None
    content_hash: str
    files: Mapping[str, WorkspaceFile]
    created_at_ms: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "files", freeze_files(self.files))


def workspace_manifest_content_hash(files: Mapping[str, WorkspaceFile]) -> str:
    """Hash a manifest independently of its storage adapter or revision.

    Node computes the same bytes (``domain/workspace.ts``); keep both in step.
    """
    canonical = [
        {
            "path": path,
            "contentHash": entry.content.content_hash,
            "sizeBytes": entry.content.size_bytes,
            "mimeType": entry.mime_type,
            **({} if entry.mode == FILE_MODE else {"mode": entry.mode}),
        }
        for path, entry in sorted(files.items())
    ]
    payload = json.dumps(canonical, separators=(",", ":"), ensure_ascii=False).encode()
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def build_manifest(
    workspace_id: str,
    *,
    revision: int,
    parent_revision: int | None,
    files: Mapping[str, WorkspaceFile],
    created_at_ms: int,
) -> WorkspaceManifest:
    return WorkspaceManifest(
        workspace_id=workspace_id,
        revision=revision,
        parent_revision=parent_revision,
        content_hash=workspace_manifest_content_hash(files),
        files=files,
        created_at_ms=created_at_ms,
    )


def empty_manifest(workspace_id: str) -> WorkspaceManifest:
    """Revision 0: what every workspace is before its first commit."""
    return build_manifest(
        workspace_id, revision=0, parent_revision=None, files={}, created_at_ms=0
    )


class WorkspaceError(Exception):
    """Base of the typed workspace refusals."""


class WorkspaceRevisionConflict(WorkspaceError, RuntimeError):
    """The head moved since the caller's base revision (CAS lost)."""


class WorkspaceRevisionNotFound(WorkspaceError, LookupError):
    pass


class WorkspaceForkConflict(WorkspaceError):
    """The fork id is taken by a fork of another origin."""


class WorkspaceNotAFork(WorkspaceError):
    """Merge of something that is not a fork of the target."""


class InvalidChangeSet(WorkspaceError, ValueError):
    pass


class WorkspaceRequestRefused(WorkspaceError):
    """The store refused the request as sent (a 4xx naming no typed refusal): sent again, it is refused again."""


# A commit the store refuses as sent: sent again, it is refused again (WP-82). The saver answers these with a typed
# result (``Partial`` or ``Refused``, ADR-RUN-01 D2/D6); ``outcome/faults.py`` reads the same tuple.
SAVE_REFUSALS: tuple[type[WorkspaceError], ...] = (InvalidChangeSet, WorkspaceRequestRefused)


class WorkspaceAccessDenied(WorkspaceError, PermissionError):
    pass


class WorkspaceKeyDestroyed(WorkspaceError):
    """The partition's data key was destroyed (crypto-shred); its objects are gone."""


class ObjectUnreadable(WorkspaceError, ValueError):
    """Stored bytes fail authentication or do not match their reference."""


class WorkspaceStoreUnavailable(WorkspaceError):
    """Transient: the backing service could not be reached; safe to retry."""


class ObjectStoreUnavailable(WorkspaceStoreUnavailable):
    """The blob store under the object port (local, S3, Azure) could not be reached or did not keep a write (CH-09).

    Objects are content-addressed, so a put or get repeated after the outage is the same object."""


class WorkspaceWriterBusy(WorkspaceError):
    """Another holder has the workspace's write lease; nothing was acquired or written."""

    reason_code = "workspace_writer_busy"

    def __init__(self, holder: str, workspace_id: str = "") -> None:
        super().__init__(f"{workspace_id or 'the workspace'} is being written by {holder}")
        self.holder = holder


@dataclass(frozen=True, slots=True)
class WriterLease:
    """The write lease of one workspace; ``renewed``: the holder already held it."""

    workspace_id: str
    holder: str
    expires_at_ms: int
    renewed: bool = False
