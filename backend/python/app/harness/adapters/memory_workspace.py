from __future__ import annotations

import time
from typing import TYPE_CHECKING

from harness_core.workspace import (
    WorkspaceFile,
    WorkspaceManifest,
    WorkspaceRevisionConflict,
    workspace_manifest_content_hash,
)
from harness_core.workspace.memory import InMemoryBinaryObjectStore

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


class InMemoryWorkspaceManifestStore:
    """CAS manifest adapter; production adapters must preserve this contract."""

    def __init__(self, clock_ms: Callable[[], int] | None = None) -> None:
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._manifests: dict[str, list[WorkspaceManifest]] = {}

    def seed(self, manifest: WorkspaceManifest) -> None:
        """Restore a hash-verified manifest captured by a durable checkpoint."""
        revisions = self._manifests.setdefault(manifest.workspace_id, [])
        if revisions and revisions[-1].revision != manifest.revision:
            raise WorkspaceRevisionConflict("Workspace seed conflicts with current revision")
        if not revisions:
            revisions.append(self._copy(manifest))

    async def get(
        self,
        workspace_id: str,
        revision: int | None = None,
    ) -> WorkspaceManifest | None:
        revisions = self._manifests.get(workspace_id, [])
        if not revisions:
            return None
        selected = revisions[-1] if revision is None else next(
            (item for item in revisions if item.revision == revision),
            None,
        )
        return self._copy(selected) if selected else None

    async def commit(
        self,
        workspace_id: str,
        expected_revision: int,
        files: Mapping[str, WorkspaceFile],
    ) -> WorkspaceManifest:
        revisions = self._manifests.setdefault(workspace_id, [])
        current_revision = revisions[-1].revision if revisions else 0
        if current_revision != expected_revision:
            raise WorkspaceRevisionConflict(
                f"Workspace revision changed from {expected_revision} to {current_revision}",
            )
        content_hash = workspace_manifest_content_hash(files)
        manifest = WorkspaceManifest(
            workspace_id=workspace_id,
            revision=current_revision + 1,
            parent_revision=current_revision or None,
            content_hash=content_hash,
            files=dict(files),
            created_at_ms=self._clock_ms(),
        )
        revisions.append(manifest)
        return self._copy(manifest)

    @staticmethod
    def _copy(manifest: WorkspaceManifest) -> WorkspaceManifest:
        return WorkspaceManifest(
            workspace_id=manifest.workspace_id,
            revision=manifest.revision,
            parent_revision=manifest.parent_revision,
            content_hash=manifest.content_hash,
            files=dict(manifest.files),
            created_at_ms=manifest.created_at_ms,
        )


__all__ = ["InMemoryBinaryObjectStore", "InMemoryWorkspaceManifestStore"]
