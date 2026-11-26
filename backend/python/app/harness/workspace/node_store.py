"""``WorkspaceStore`` and ``KeyProvider`` over Node's workspace routes (P3.2, D33).

Both are bound to one attempt: Node checks the attempt token, that the
attempt still owns (or may read) its run, and that every workspace belongs to
the run's session. The tenant is the run's org; a scope naming another tenant
is refused here before any call. Diffs are computed locally from manifests.
Manifests from Node are re-hashed, so a corrupted file list is refused.
"""

from __future__ import annotations

import base64
import binascii
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import quote

from pydantic import JsonValue

from app.harness.journal_node import error_code
from harness_core.journal.types import JournalUnavailable, OwnershipLost
from harness_core.workspace import (
    FILE_CHANGE_KINDS,
    FILE_MODE,
    MERGE_STATUSES,
    ChangeSet,
    DataKey,
    Diff,
    InvalidChangeSet,
    MergeResult,
    ObjectRef,
    PathConflict,
    WorkspaceAccessDenied,
    WorkspaceError,
    WorkspaceFile,
    WorkspaceForkConflict,
    WorkspaceKeyDestroyed,
    WorkspaceManifest,
    WorkspaceNotAFork,
    WorkspaceRequestRefused,
    WorkspaceRevisionConflict,
    WorkspaceRevisionNotFound,
    WorkspaceStoreUnavailable,
    WorkspaceWriterBusy,
    WriterLease,
    diff_manifests,
    workspace_manifest_content_hash,
)
from harness_core.workspace.forks import StoreChildWorkspaces

if TYPE_CHECKING:
    import httpx

    from app.harness.contracts.generated.pipeshub.errors import ErrorCode
    from app.harness.journal_node import AttemptRoute, NodeJournal
    from harness_core.journal.types import AttemptToken
    from harness_core.logs import HarnessLog
    from harness_core.workspace import (
        CommitReason,
        ConflictPolicy,
        FileChangeKind,
        ObjectScope,
    )

LOGGER = "app.harness.workspace"

# Node answers these from the catalogue (contracts/harness/ext/pipeshub/errors.json); WRITER_BUSY, PATH_NOT_FOUND and PATH_EXISTS
# only on a person's routes (restore, SLASH3 files), never to a worker.
_ERRORS: dict[str, type[WorkspaceError]] = {
    "WORKSPACE_CONFLICT": WorkspaceRevisionConflict,
    "WORKSPACE_REVISION_NOT_FOUND": WorkspaceRevisionNotFound,
    "WORKSPACE_FORK_CONFLICT": WorkspaceForkConflict,
    "WORKSPACE_NOT_A_FORK": WorkspaceNotAFork,
    "WORKSPACE_FORBIDDEN": WorkspaceAccessDenied,
    "WORKSPACE_INVALID_CHANGE": InvalidChangeSet,
    "WORKSPACE_TOO_LARGE": InvalidChangeSet,
    "WORKSPACE_KEY_DESTROYED": WorkspaceKeyDestroyed,
}
_LOST: frozenset[ErrorCode] = frozenset({"STALE_ATTEMPT"})
_POLICIES: dict[str, JsonValue] = {"refuse": "refuse", "keep_target": "keepTarget"}


def _segment(workspace_id: str) -> str:
    return quote(workspace_id, safe="")


def _file_wire(entry: WorkspaceFile) -> dict[str, JsonValue]:
    wire: dict[str, JsonValue] = {
        "path": entry.path,
        "objectId": entry.content.object_id,
        "contentHash": entry.content.content_hash,
        "sizeBytes": entry.content.size_bytes,
        "mimeType": entry.mime_type,
    }
    if entry.mode != FILE_MODE:
        wire["mode"] = entry.mode
    return wire


def _file(wire: dict[str, Any]) -> WorkspaceFile:
    return WorkspaceFile(
        path=str(wire["path"]),
        content=ObjectRef(
            object_id=str(wire["objectId"]),
            content_hash=str(wire["contentHash"]),
            size_bytes=int(wire["sizeBytes"]),
        ),
        mime_type=str(wire["mimeType"]),
        mode=int(wire.get("mode", FILE_MODE)),
    )


def _manifest(wire: dict[str, Any]) -> WorkspaceManifest:
    try:
        files = {entry.path: entry for entry in map(_file, wire["files"])}
        parent = wire.get("parentRevision")
        manifest = WorkspaceManifest(
            workspace_id=str(wire["workspaceId"]),
            revision=int(wire["revision"]),
            parent_revision=None if parent is None else int(parent),
            content_hash=str(wire["contentHash"]),
            files=files,
            created_at_ms=int(wire["createdAt"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise WorkspaceError("invalid workspace manifest from Node") from exc
    if workspace_manifest_content_hash(manifest.files) != manifest.content_hash:
        raise WorkspaceError("workspace manifest content hash does not match its files")
    return manifest


def _kinds(wire: object) -> dict[str, FileChangeKind]:
    """A merge's change kinds; an older Node sends none, and a kind this worker does not know is left out."""
    if not isinstance(wire, dict):
        return {}
    return {
        str(path): kind
        for path, kind in cast("dict[object, object]", wire).items()
        if kind in FILE_CHANGE_KINDS
    }


def _refusal(response: httpx.Response, log: HarnessLog, operation: str) -> Exception:
    code = error_code(response)
    status = response.status_code
    log = log.bind(status=status, reason=code or None, operation=operation)
    if status == 429 or status >= 500:
        log.warning("workspace.unavailable")
        return WorkspaceStoreUnavailable(f"workspace store answered {status}")
    if status == 401 or code in _LOST:
        log.warning("workspace.ownership_lost")
        return OwnershipLost(code or str(status))
    error = _ERRORS.get(code)
    if error is None:
        log.error("workspace.rejected")
        return WorkspaceRequestRefused(code or str(status))
    log.info("workspace.refused")
    return error(code)


class NodeWorkspaceStore:
    """One attempt's view of its session's workspaces."""

    def __init__(self, route: AttemptRoute) -> None:
        self._route = route
        self._log = route.log(LOGGER)

    async def head(self, scope: ObjectScope, workspace_id: str) -> WorkspaceManifest:
        self._authorize(scope)
        return await self._get(f"/workspaces/{_segment(workspace_id)}", "workspace.head")

    async def revision(
        self, scope: ObjectScope, workspace_id: str, revision: int
    ) -> WorkspaceManifest:
        self._authorize(scope)
        return await self._get(
            f"/workspaces/{_segment(workspace_id)}/revisions/{revision}", "workspace.revision"
        )

    async def commit(
        self,
        scope: ObjectScope,
        workspace_id: str,
        *,
        base_revision: int,
        changes: ChangeSet,
        reason: CommitReason,
    ) -> WorkspaceManifest:
        self._authorize(scope)
        upserts: list[JsonValue] = [_file_wire(entry) for entry in changes.upserts.values()]
        deletes = list[JsonValue](sorted(changes.deletes))
        body: dict[str, JsonValue] = {
            "baseRevision": base_revision,
            "reason": reason,
            "upserts": upserts,
            "deletes": deletes,
        }
        manifest = _manifest(
            await self._post(f"/workspaces/{_segment(workspace_id)}/commit", body, "workspace.commit")
        )
        self._log.info(
            "workspace.committed",
            workspaceId=workspace_id,
            revision=manifest.revision,
            reason=reason,
            files=len(manifest.files),
        )
        return manifest

    async def fork(
        self, scope: ObjectScope, workspace_id: str, *, at_revision: int, fork_id: str
    ) -> WorkspaceManifest:
        self._authorize(scope)
        return _manifest(
            await self._post(
                f"/workspaces/{_segment(workspace_id)}/fork",
                {"atRevision": at_revision, "forkId": fork_id},
                "workspace.fork",
            )
        )

    async def merge(
        self,
        scope: ObjectScope,
        *,
        fork_id: str,
        into: str,
        key: str | None = None,
        on_conflict: ConflictPolicy = "refuse",
    ) -> MergeResult:
        self._authorize(scope)
        body: dict[str, JsonValue] = {"forkId": fork_id, "onConflict": _POLICIES[on_conflict]}
        if key is not None:
            body["mergeKey"] = key
        wire = await self._post(f"/workspaces/{_segment(into)}/merge", body, "workspace.merge")
        try:
            status = str(wire["status"])
            if status not in MERGE_STATUSES:
                raise ValueError(status)
            conflicts = tuple(
                PathConflict(
                    path=str(item["path"]),
                    ours=None if item.get("ours") is None else _file(item["ours"]),
                    theirs=None if item.get("theirs") is None else _file(item["theirs"]),
                )
                for item in wire["conflicts"]
            )
            repositories = tuple(str(repo) for repo in wire["repositoryConflicts"])
            merged = tuple(str(path) for path in wire["merged"])
            fork_revision = int(wire["forkRevision"])
            kinds = _kinds(wire.get("kinds"))
        except (KeyError, TypeError, ValueError) as exc:
            raise WorkspaceError("invalid merge result from Node") from exc
        result = MergeResult(
            status,
            fork_id,
            into,
            _manifest(wire["manifest"]),
            conflicts,
            repositories,
            merged,
            fork_revision,
            kinds,
        )
        self._log.info(
            "workspace.merge",
            workspaceId=into,
            forkId=fork_id,
            status=status,
            onConflict=on_conflict,
            keyed=key is not None,
            revision=result.manifest.revision,
            merged=len(merged),
            conflicts=len(conflicts),
            repositoryConflicts=len(repositories),
        )
        return result

    async def diff(
        self, scope: ObjectScope, a: WorkspaceManifest, b: WorkspaceManifest
    ) -> Diff:
        self._authorize(scope)
        return diff_manifests(a, b)

    async def rollback(
        self, scope: ObjectScope, workspace_id: str, *, to_revision: int
    ) -> WorkspaceManifest:
        self._authorize(scope)
        return _manifest(
            await self._post(
                f"/workspaces/{_segment(workspace_id)}/rollback",
                {"toRevision": to_revision},
                "workspace.rollback",
            )
        )

    def _authorize(self, scope: ObjectScope) -> None:
        if scope.tenant_id != self._route.org_id:
            self._log.warning("workspace.refused", reason="other_tenant")
            raise WorkspaceAccessDenied("this attempt serves another tenant")

    async def _get(self, suffix: str, operation: str) -> WorkspaceManifest:
        try:
            response = await self._route.get(suffix, operation=operation)
        except JournalUnavailable as exc:
            raise WorkspaceStoreUnavailable(str(exc)) from exc
        if response.is_error:
            raise _refusal(response, self._log, operation)
        return _manifest(cast("dict[str, Any]", response.json()))

    async def _post(
        self, suffix: str, body: dict[str, JsonValue], operation: str
    ) -> dict[str, Any]:
        try:
            response = await self._route.post(suffix, body=body, operation=operation)
        except JournalUnavailable as exc:
            raise WorkspaceStoreUnavailable(str(exc)) from exc
        if response.is_error:
            raise _refusal(response, self._log, operation)
        return cast("dict[str, Any]", response.json())


class NodeWorkspaceWriters:
    """``WorkspaceWriters`` over Node's writer routes; the holder is always this attempt's run."""

    def __init__(self, route: AttemptRoute) -> None:
        self._route = route
        self._log = route.log(LOGGER)

    async def acquire(
        self, scope: ObjectScope, workspace_id: str, *, holder: str, ttl_ms: int
    ) -> WriterLease:
        self._authorize(scope, holder)
        wire = await self._post(
            f"/workspaces/{_segment(workspace_id)}/writer", {"ttlMs": ttl_ms}, "workspace.writer"
        )
        try:
            if wire["acquired"] is not True:
                holding = str(wire["holder"])
                self._log.info("workspace.writer_busy", workspaceId=workspace_id, holder=holding)
                raise WorkspaceWriterBusy(holding, workspace_id)
            return WriterLease(
                workspace_id=workspace_id,
                holder=str(wire["holder"]),
                expires_at_ms=int(wire["expiresAt"]),
                renewed=bool(wire["renewed"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise WorkspaceError("invalid writer lease from Node") from exc

    async def release(self, scope: ObjectScope, workspace_id: str, *, holder: str) -> None:
        if holder != self._route.run_id:
            # Only this run's lease can be released through its attempt.
            return
        self._authorize(scope, holder)
        _ = await self._post(
            f"/workspaces/{_segment(workspace_id)}/writer/release", {}, "workspace.writer_release"
        )

    def _authorize(self, scope: ObjectScope, holder: str) -> None:
        if scope.tenant_id != self._route.org_id:
            self._log.warning("workspace.refused", reason="other_tenant")
            raise WorkspaceAccessDenied("this attempt serves another tenant")
        if holder != self._route.run_id:
            self._log.warning("workspace.refused", reason="other_holder")
            raise WorkspaceAccessDenied("a lease is held by the attempt's own run")

    async def _post(
        self, suffix: str, body: dict[str, JsonValue], operation: str
    ) -> dict[str, Any]:
        try:
            response = await self._route.post(suffix, body=body, operation=operation)
        except JournalUnavailable as exc:
            raise WorkspaceStoreUnavailable(str(exc)) from exc
        if response.is_error:
            raise _refusal(response, self._log, operation)
        return cast("dict[str, Any]", response.json())


class LeasedKeys:
    """A data key Node issues to this attempt (``suffix``), held for the attempt's lease only. A subclass names its
    route and may map the key domain it is asked for to the one Node issues (``_domain_for``).

    ``forget()`` (the lease ended) drops it; the next lease asks Node again, which refuses once the key was erased.
    """

    _suffix: str
    _operation: str

    def __init__(self, route: AttemptRoute) -> None:
        self._route = route
        self._log = route.log(LOGGER)
        self._keys: dict[str, DataKey] = {}

    def _domain_for(self, key_domain: str) -> str:
        return key_domain

    async def data_key(self, tenant_id: str, key_domain: str) -> DataKey:
        if tenant_id != self._route.org_id:
            self._log.warning("workspace.key_refused", reason="other_tenant")
            raise WorkspaceAccessDenied("this attempt serves another tenant")
        domain = self._domain_for(key_domain)
        cached = self._keys.get(domain)
        if cached is not None:
            return cached
        try:
            response = await self._route.post(self._suffix, body={}, operation=self._operation)
        except JournalUnavailable as exc:
            raise WorkspaceStoreUnavailable(str(exc)) from exc
        if response.is_error:
            raise _refusal(response, self._log, self._operation)
        wire = cast("dict[str, Any]", response.json())
        if wire.get("keyDomain") != domain:
            # Every fork of a session shares the session's key; nothing else is served.
            self._log.warning("workspace.key_refused", reason="other_domain")
            raise WorkspaceAccessDenied("the key domain is not this attempt's")
        try:
            key = DataKey(str(wire["keyId"]), base64.b64decode(str(wire["key"]), validate=True))
        except (KeyError, ValueError, binascii.Error) as exc:
            raise WorkspaceError("invalid workspace key response") from exc
        self._keys[domain] = key
        self._log.info("workspace.key_leased", keyId=key.key_id, operation=self._operation)
        return key

    def forget(self) -> None:
        self._keys.clear()


class NodeKeyProvider(LeasedKeys):
    """The session's workspace key from Node, held for this attempt's lease only."""

    _suffix = "/workspace-key"
    _operation = "workspace.key"


def node_child_workspaces(journal: NodeJournal) -> StoreChildWorkspaces:
    """A parent's join merges its children's forks through its own attempt's routes (P6.4)."""

    def stores(token: AttemptToken) -> tuple[NodeWorkspaceStore, NodeWorkspaceWriters]:
        route = journal.attempt_route(token)
        return NodeWorkspaceStore(route), NodeWorkspaceWriters(route)

    return StoreChildWorkspaces(stores)


__all__ = [
    "LOGGER",
    "LeasedKeys",
    "NodeKeyProvider",
    "NodeWorkspaceStore",
    "NodeWorkspaceWriters",
    "node_child_workspaces",
]
