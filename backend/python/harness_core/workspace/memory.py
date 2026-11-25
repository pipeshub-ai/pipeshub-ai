"""In-memory reference adapters for the workspace ports.

Each method finishes its check-and-write without awaiting, so on one event
loop it is atomic, which is the behaviour the durable adapters must match.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from harness_core.clock import SystemClock
from harness_core.ids import RandomIds
from harness_core.workspace.changes import (
    ChangeSet,
    CommitReason,
    ConflictPolicy,
    Diff,
    MergeResult,
    MergeStatus,
    diff_manifests,
    plan_merge,
)
from harness_core.workspace.ports import DATA_KEY_BYTES, DataKey
from harness_core.workspace.types import (
    ObjectRef,
    ObjectScope,
    ObjectUnreadable,
    WorkspaceForkConflict,
    WorkspaceKeyDestroyed,
    WorkspaceManifest,
    WorkspaceNotAFork,
    WorkspaceRevisionConflict,
    WorkspaceRevisionNotFound,
    WorkspaceWriterBusy,
    WriterLease,
    build_manifest,
    empty_manifest,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from harness_core.clock import Clock
    from harness_core.ids import IdSource
    from harness_core.workspace.types import WorkspaceFile


class InMemoryBinaryObjectStore:
    """Content-addressed reference adapter used by contract tests and local demos."""

    def __init__(self) -> None:
        self._objects: dict[tuple[str, str], bytes] = {}

    @property
    def object_count(self) -> int:
        return len(self._objects)

    def stored_bytes(self) -> list[bytes]:
        return list(self._objects.values())

    async def put(self, scope: ObjectScope, content: bytes) -> ObjectRef:
        digest = hashlib.sha256(content).hexdigest()
        object_id = f"sha256:{digest}"
        _ = self._objects.setdefault((scope.tenant_id, object_id), bytes(content))
        return ObjectRef(
            object_id=object_id,
            content_hash=f"sha256:{digest}",
            size_bytes=len(content),
        )

    async def content_hash(self, scope: ObjectScope, content: bytes) -> str:
        del scope  # the hash is the plain sha256 in every scope
        return f"sha256:{hashlib.sha256(content).hexdigest()}"

    async def get(self, scope: ObjectScope, ref: ObjectRef) -> bytes:
        content = self._objects.get((scope.tenant_id, ref.object_id))
        if content is None:
            raise FileNotFoundError(ref.object_id)
        digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
        if digest != ref.content_hash or len(content) != ref.size_bytes:
            raise ObjectUnreadable("Object content does not match its immutable reference")
        return bytes(content)

    def tamper(self) -> None:
        """Test hook: flip the last byte of every stored object, keeping its key."""
        for key, content in self._objects.items():
            if content:
                self._objects[key] = content[:-1] + bytes([content[-1] ^ 0xFF])


@dataclass(frozen=True, slots=True)
class MergeRecord:
    """A keyed merge as the store keeps it: revisions only, so a replay recomputes
    the same plan from immutable history and stores no path (Node keeps the same)."""

    key: str
    into: str
    base: int
    theirs: int
    ours: int
    revision: int | None
    status: MergeStatus


@dataclass(slots=True)
class _Fork:
    origin: str
    at_revision: int
    merge_base: int
    last_merge: MergeRecord | None = None


_Key = tuple[str, str]


class InMemoryWorkspaceStore:
    """``WorkspaceStore`` reference adapter: every revision kept, keyed by tenant."""

    def __init__(self, *, clock: Clock | None = None) -> None:
        self._clock = clock or SystemClock()
        self._revisions: dict[_Key, list[WorkspaceManifest]] = {}
        self._forks: dict[_Key, _Fork] = {}

    async def head(self, scope: ObjectScope, workspace_id: str) -> WorkspaceManifest:
        return self._head(scope, workspace_id)

    async def revision(
        self, scope: ObjectScope, workspace_id: str, revision: int
    ) -> WorkspaceManifest:
        return self._revision(scope, workspace_id, revision)

    async def commit(
        self,
        scope: ObjectScope,
        workspace_id: str,
        *,
        base_revision: int,
        changes: ChangeSet,
        reason: CommitReason,
    ) -> WorkspaceManifest:
        del reason  # the durable adapters record it; nothing here reads it
        head = self._head(scope, workspace_id)
        if head.revision != base_revision:
            raise WorkspaceRevisionConflict(
                f"Workspace head is {head.revision}, not {base_revision}"
            )
        if changes.is_empty:
            return head
        return self._append(scope, head, changes.apply(head.files))

    async def fork(
        self, scope: ObjectScope, workspace_id: str, *, at_revision: int, fork_id: str
    ) -> WorkspaceManifest:
        if fork_id == workspace_id:
            raise WorkspaceForkConflict("A workspace cannot fork into itself")
        key = (scope.tenant_id, fork_id)
        existing = self._forks.get(key)
        if existing is not None:
            if (existing.origin, existing.at_revision) != (workspace_id, at_revision):
                raise WorkspaceForkConflict(f"{fork_id} is a fork of another origin")
            return self._revision(scope, fork_id, 1)
        if self._revisions.get(key):
            raise WorkspaceForkConflict(f"{fork_id} already exists and is not a fork")
        source = self._revision(scope, workspace_id, at_revision)
        self._forks[key] = _Fork(workspace_id, at_revision, merge_base=1)
        return self._append(scope, empty_manifest(fork_id), dict(source.files))

    async def merge(
        self,
        scope: ObjectScope,
        *,
        fork_id: str,
        into: str,
        key: str | None = None,
        on_conflict: ConflictPolicy = "refuse",
    ) -> MergeResult:
        fork = self._forks.get((scope.tenant_id, fork_id))
        if fork is None or fork.origin != into:
            raise WorkspaceNotAFork(f"{fork_id} is not a fork of {into}")
        last = fork.last_merge
        if key is not None and last is not None and (last.key, last.into) == (key, into):
            return self._replayed(scope, fork_id, last)
        base = self._revision(scope, fork_id, fork.merge_base)
        theirs = self._head(scope, fork_id)
        ours = self._head(scope, into)
        if theirs.revision == base.revision or diff_manifests(base, theirs).is_empty:
            fork.merge_base = theirs.revision
            self._record(fork, key, into, base, theirs, ours, None, "up_to_date")
            return MergeResult("up_to_date", fork_id, into, ours, fork_revision=theirs.revision)
        plan = plan_merge(base.files, ours.files, theirs.files)
        if not plan.clean and on_conflict == "refuse":
            return MergeResult(
                "conflict",
                fork_id,
                into,
                ours,
                plan.conflicts,
                plan.repository_conflicts,
                fork_revision=theirs.revision,
            )
        merged = ours
        if not plan.changes.is_empty:
            merged = self._append(scope, ours, plan.changes.apply(ours.files))
        fork.merge_base = theirs.revision
        status: MergeStatus = "merged" if plan.clean else "partial"
        produced = merged.revision if merged is not ours else None
        self._record(fork, key, into, base, theirs, ours, produced, status)
        return MergeResult(
            status,
            fork_id,
            into,
            merged,
            plan.conflicts,
            plan.repository_conflicts,
            tuple(sorted(plan.changes.paths)),
            theirs.revision,
            plan.changes.kinds(ours.files),
        )

    @staticmethod
    def _record(
        fork: _Fork,
        key: str | None,
        into: str,
        base: WorkspaceManifest,
        theirs: WorkspaceManifest,
        ours: WorkspaceManifest,
        revision: int | None,
        status: MergeStatus,
    ) -> None:
        if key is not None:
            fork.last_merge = MergeRecord(
                key, into, base.revision, theirs.revision, ours.revision, revision, status
            )

    def _replayed(self, scope: ObjectScope, fork_id: str, record: MergeRecord) -> MergeResult:
        manifest = self._revision(scope, record.into, record.revision or record.ours)
        if record.status == "up_to_date":
            return MergeResult(
                "up_to_date", fork_id, record.into, manifest, fork_revision=record.theirs
            )
        ours = self._revision(scope, record.into, record.ours).files
        plan = plan_merge(
            self._revision(scope, fork_id, record.base).files,
            ours,
            self._revision(scope, fork_id, record.theirs).files,
        )
        return MergeResult(
            record.status,
            fork_id,
            record.into,
            manifest,
            plan.conflicts,
            plan.repository_conflicts,
            tuple(sorted(plan.changes.paths)),
            record.theirs,
            plan.changes.kinds(ours),
        )

    async def diff(
        self, scope: ObjectScope, a: WorkspaceManifest, b: WorkspaceManifest
    ) -> Diff:
        del scope  # manifests carry their files; nothing is read
        return diff_manifests(a, b)

    async def rollback(
        self, scope: ObjectScope, workspace_id: str, *, to_revision: int
    ) -> WorkspaceManifest:
        target = self._revision(scope, workspace_id, to_revision)
        return self._append(scope, self._head(scope, workspace_id), dict(target.files))

    def _head(self, scope: ObjectScope, workspace_id: str) -> WorkspaceManifest:
        revisions = self._revisions.get((scope.tenant_id, workspace_id))
        return revisions[-1] if revisions else empty_manifest(workspace_id)

    def _revision(
        self, scope: ObjectScope, workspace_id: str, revision: int
    ) -> WorkspaceManifest:
        revisions = self._revisions.get((scope.tenant_id, workspace_id), [])
        if revision == 0:
            return empty_manifest(workspace_id)
        if 0 < revision <= len(revisions):
            return revisions[revision - 1]
        raise WorkspaceRevisionNotFound(f"{workspace_id} has no revision {revision}")

    def _append(
        self,
        scope: ObjectScope,
        head: WorkspaceManifest,
        files: Mapping[str, WorkspaceFile],
    ) -> WorkspaceManifest:
        manifest = build_manifest(
            head.workspace_id,
            revision=head.revision + 1,
            parent_revision=head.revision or None,
            files=files,
            created_at_ms=int(self._clock.now().timestamp() * 1000),
        )
        self._revisions.setdefault((scope.tenant_id, head.workspace_id), []).append(manifest)
        return manifest


class InMemoryWorkspaceWriters:
    """``WorkspaceWriters`` reference adapter: one lease per (tenant, workspace), on an injected clock."""

    def __init__(self, *, clock: Clock | None = None) -> None:
        self._clock = clock or SystemClock()
        self._leases: dict[_Key, WriterLease] = {}

    async def acquire(
        self, scope: ObjectScope, workspace_id: str, *, holder: str, ttl_ms: int
    ) -> WriterLease:
        if not holder or ttl_ms <= 0:
            raise ValueError("A writer lease needs a holder and a positive TTL")
        now = self._now_ms()
        key = (scope.tenant_id, workspace_id)
        current = self._leases.get(key)
        live = current is not None and current.expires_at_ms > now
        if live and current is not None and current.holder != holder:
            raise WorkspaceWriterBusy(current.holder, workspace_id)
        lease = WriterLease(workspace_id, holder, now + ttl_ms, renewed=live)
        self._leases[key] = lease
        return lease

    async def release(self, scope: ObjectScope, workspace_id: str, *, holder: str) -> None:
        key = (scope.tenant_id, workspace_id)
        current = self._leases.get(key)
        if current is not None and current.holder == holder:
            del self._leases[key]

    def _now_ms(self) -> int:
        return int(self._clock.now().timestamp() * 1000)


class InMemoryKeyProvider:
    """``KeyProvider`` and ``KeyShredder`` with injected ids and key material."""

    def __init__(
        self,
        *,
        ids: IdSource | None = None,
        random_bytes: Callable[[int], bytes] = os.urandom,
    ) -> None:
        self._ids = ids or RandomIds()
        self._random_bytes = random_bytes
        self._keys: dict[_Key, DataKey | None] = {}

    async def data_key(self, tenant_id: str, key_domain: str) -> DataKey:
        key = (tenant_id, key_domain)
        if key not in self._keys:
            self._keys[key] = DataKey(
                self._ids.new_id("wsk"), self._random_bytes(DATA_KEY_BYTES)
            )
        current = self._keys[key]
        if current is None:
            raise WorkspaceKeyDestroyed(f"The key of {key_domain} was destroyed")
        return current

    async def destroy(self, tenant_id: str, key_domain: str) -> None:
        self._keys[(tenant_id, key_domain)] = None
