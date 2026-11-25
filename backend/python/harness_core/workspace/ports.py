"""Workspace ports: objects, manifests, the versioned store and data keys."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping

    from harness_core.journal.types import AttemptToken
    from harness_core.sandbox.ports import SandboxHandle
    from harness_core.workspace.changes import (
        ChangeSet,
        CommitReason,
        ConflictPolicy,
        Diff,
        MergeResult,
    )
    from harness_core.workspace.forks import ChildMergeReport, JoinedChild
    from harness_core.workspace.sync.types import Collected, SyncState
    from harness_core.workspace.types import (
        ObjectRef,
        ObjectScope,
        WorkspaceFile,
        WorkspaceManifest,
        WriterLease,
    )

DATA_KEY_BYTES = 32


class BinaryObjectStore(Protocol):
    """Immutable content-addressed objects; a ref carries no authority, the scope does."""

    async def put(self, scope: ObjectScope, content: bytes) -> ObjectRef: ...

    async def get(self, scope: ObjectScope, ref: ObjectRef) -> bytes: ...

    async def content_hash(self, scope: ObjectScope, content: bytes) -> str:
        """The ``content_hash`` a ``put`` of ``content`` would carry, computed without storing anything.

        Lets a caller recognise bytes it already stored (a sandbox file that did
        not change) without reading or writing an object.
        """
        ...


class WorkspaceManifestStore(Protocol):
    """The single-writer manifest port the VFS and skill bundles use."""

    async def get(
        self,
        workspace_id: str,
        revision: int | None = None,
    ) -> WorkspaceManifest | None: ...

    async def commit(
        self,
        workspace_id: str,
        expected_revision: int,
        files: Mapping[str, WorkspaceFile],
    ) -> WorkspaceManifest: ...


class WorkspaceStore(Protocol):
    """Versioned workspaces with CAS commits, forks and three-way merge (ARCHITECTURE §4.3).

    Revision 0 is the empty workspace every id starts as. Merge, rollback and
    fork never rewrite history: each adds a revision or a workspace.
    """

    async def head(self, scope: ObjectScope, workspace_id: str) -> WorkspaceManifest: ...

    async def revision(
        self, scope: ObjectScope, workspace_id: str, revision: int
    ) -> WorkspaceManifest:
        """Raise ``WorkspaceRevisionNotFound`` for a revision that does not exist."""
        ...

    async def commit(
        self,
        scope: ObjectScope,
        workspace_id: str,
        *,
        base_revision: int,
        changes: ChangeSet,
        reason: CommitReason,
    ) -> WorkspaceManifest:
        """CAS on the head: ``WorkspaceRevisionConflict`` unless it is ``base_revision``.

        An empty change set adds no revision and returns the head.
        """
        ...

    async def fork(
        self, scope: ObjectScope, workspace_id: str, *, at_revision: int, fork_id: str
    ) -> WorkspaceManifest:
        """Revision 1 of ``fork_id`` holds ``workspace_id`` at ``at_revision``; idempotent."""
        ...

    async def merge(
        self,
        scope: ObjectScope,
        *,
        fork_id: str,
        into: str,
        key: str | None = None,
        on_conflict: ConflictPolicy = "refuse",
    ) -> MergeResult:
        """Merge the fork's changes since its merge base into ``into``.

        ``refuse``: all or nothing (``conflict`` commits nothing). ``keep_target``:
        the non-conflicting changes land in one revision and the target keeps its
        side of every conflict (``partial``); the merge base advances either way.
        ``key``: the same key again returns the same result and writes nothing,
        even after the target moved (one merge per caller's unit of work).
        """
        ...

    async def diff(
        self, scope: ObjectScope, a: WorkspaceManifest, b: WorkspaceManifest
    ) -> Diff: ...

    async def rollback(
        self, scope: ObjectScope, workspace_id: str, *, to_revision: int
    ) -> WorkspaceManifest:
        """A new head revision whose files equal ``to_revision``'s."""
        ...


class WorkspaceWriters(Protocol):
    """At most one writer per workspace: a lease with an expiry, re-entrant for its holder.

    A writer that keeps a sandbox's working copy (it rebases on a CAS conflict)
    takes it before it starts; a second writer is refused, never queued.
    """

    async def acquire(
        self, scope: ObjectScope, workspace_id: str, *, holder: str, ttl_ms: int
    ) -> WriterLease:
        """The lease until now + ``ttl_ms``; ``WorkspaceWriterBusy`` while another holder's is live."""
        ...

    async def release(self, scope: ObjectScope, workspace_id: str, *, holder: str) -> None:
        """Frees the lease if ``holder`` has it; otherwise does nothing."""
        ...


class ChildWorkspaces(Protocol):
    """Offers a joined child run's workspace fork to its parent (P6.4).

    Idempotent per child: asking again reports what the first call did and
    writes nothing. ``None``: the child had no fork.
    """

    async def merge(self, token: AttemptToken, child: JoinedChild) -> ChildMergeReport | None: ...

    async def keep_unfinished(
        self, token: AttemptToken, child: JoinedChild, report: ChildMergeReport
    ) -> ChildMergeReport:
        """A failed child's own changes, kept in a folder of their own in the parent's workspace (WP-56); idempotent.
        ``report`` as it was when nothing is kept."""
        ...


class WorkspaceSync(Protocol):
    """A workspace revision into a sandbox folder and the folder's changes back (ARCHITECTURE §4.3).

    Hash-diff only: nothing unchanged is uploaded or committed. Engine
    configuration folders never reach the sandbox and are never collected.
    """

    async def materialize(
        self,
        sandbox: SandboxHandle,
        manifest: WorkspaceManifest,
        *,
        root: str,
        defer: Collection[str] = (),
        keep_scratch: bool = False,
    ) -> SyncState:
        """Make ``root`` hold exactly ``manifest``'s visible files (extra files removed, empty folders kept). A path in
        ``defer`` the folder does not already hold as is waits for ``stage`` (hidden ``ON_FIRST_READ``). References
        the folder holds stay (ADR-RUN-01 D2); its scratch stays only with ``keep_scratch`` (a refresh mid-run)."""
        ...

    async def stage(self, sandbox: SandboxHandle, state: SyncState, path: str) -> SyncState:
        """Writes a deferred file into the folder; the state that no longer hides it (C5.01, J-26)."""
        ...

    async def collect(self, sandbox: SandboxHandle, state: SyncState) -> Collected:
        """What changed under ``state.root`` since ``state``: a change set for ``commit(base_revision=state.revision)``."""
        ...

    async def pending(self, sandbox: SandboxHandle, state: SyncState) -> int:
        """How many paths ``collect`` would change now, uploading nothing (a full scan)."""
        ...


@dataclass(frozen=True, slots=True)
class DataKey:
    """A partition's symmetric key; the material never appears in a repr."""

    key_id: str
    material: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if not self.key_id or len(self.key_id.encode()) > 255:
            raise ValueError("Data key id must be 1 to 255 bytes")
        if len(self.material) != DATA_KEY_BYTES:
            raise ValueError("Data key material has the wrong length")


class KeyProvider(Protocol):
    """Data keys per (tenant, key domain), held by the caller for its lease only."""

    async def data_key(self, tenant_id: str, key_domain: str) -> DataKey:
        """Raise ``WorkspaceKeyDestroyed`` once the domain's key was destroyed."""
        ...


class KeyShredder(Protocol):
    """Erasure by crypto-shred: the key goes, and no new key is issued for the domain."""

    async def destroy(self, tenant_id: str, key_domain: str) -> None: ...
