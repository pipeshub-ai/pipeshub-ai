"""The session workspace for a durable loop run's ``run_code`` (SLASH3 SB3a: workspace path parity).

Engines materialise the session workspace's head at ``/workspace`` and save it back at turn end (P5.5); the
durable loop's ``run_code`` saw none of it, so a file it wrote was lost and a file an engine wrote was not there.
Here each call is given the head under the sandbox's ``workspace/`` folder (``SandboxProjection("/workspace",
"workspace")``) and what the call left there is committed as one revision (``turn_end``), with the run's attempt:
the same manifest paths the engines read and write, so ``out.txt`` is ``./workspace/out.txt`` to ``run_code`` and
``/workspace/out.txt`` to an engine.

Engine plug-in paths and symbolic links are never staged (an engine never gets them either) and never written
back, so code cannot configure an engine or point outside the tree.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from harness_core.mime import mime_for
from harness_core.workspace.changes import ChangeSet
from harness_core.workspace.sync.rules import is_engine_config
from harness_core.workspace.sync.saver import rebase_changes
from harness_core.workspace.types import WorkspaceFile, WorkspaceRevisionConflict

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.workspace.ports import BinaryObjectStore, WorkspaceStore
    from harness_core.workspace.types import ObjectScope, WorkspaceManifest

WORKSPACE_DIR: Final = "workspace"
# The legacy materializer's quota (``SandboxWorkspaceMaterializer``): a bigger head is not staged, and not saved.
MAX_STAGED_FILES: Final = 1_000
MAX_STAGED_BYTES: Final = 100 * 1024 * 1024
_COMMIT_ATTEMPTS: Final = 3


def _staged(path: str, entry: WorkspaceFile) -> bool:
    return not entry.is_symlink and not is_engine_config(path)


@dataclass(frozen=True)
class Checkout:
    """What one call was given: the head it started from, and the paths it staged."""

    base: WorkspaceManifest
    staged: frozenset[str]
    files: Mapping[str, bytes]

    def sandbox_files(self) -> dict[str, bytes]:
        """The staged files under the sandbox's ``workspace/`` folder."""
        return {f"{WORKSPACE_DIR}/{path}": data for path, data in self.files.items()}


class RunCodeWorkspace:
    """One root run's session workspace, read and written with its attempt (Node's store, the session's key)."""

    def __init__(
        self, *, store: WorkspaceStore, objects: BinaryObjectStore, scope: ObjectScope, workspace_id: str
    ) -> None:
        self._store = store
        self._objects = objects
        self._scope = scope
        self._workspace_id = workspace_id

    @property
    def workspace_id(self) -> str:
        return self._workspace_id

    async def listing(self) -> list[tuple[str, int]]:
        """The head's files a call would be given, with their sizes, by path."""
        head = await self._store.head(self._scope, self._workspace_id)
        return sorted((p, e.content.size_bytes) for p, e in head.files.items() if _staged(p, e))

    async def read(self, path: str) -> bytes | None:
        """One file of the head, as a call would be given it; ``None`` when there is none at ``path``."""
        head = await self._store.head(self._scope, self._workspace_id)
        entry = head.files.get(path)
        if entry is None or not _staged(path, entry):
            return None
        return await self._objects.get(self._scope, entry.content)

    async def checkout(self) -> Checkout | None:
        """The head's files to stage; ``None`` when the head is past the quota (the call runs without them)."""
        head = await self._store.head(self._scope, self._workspace_id)
        entries = {path: entry for path, entry in head.files.items() if _staged(path, entry)}
        if len(entries) > MAX_STAGED_FILES or sum(e.content.size_bytes for e in entries.values()) > MAX_STAGED_BYTES:
            return None
        files = {path: await self._objects.get(self._scope, entry.content) for path, entry in entries.items()}
        return Checkout(base=head, staged=frozenset(entries), files=files)

    async def save(self, checkout: Checkout, after: Mapping[str, bytes]) -> WorkspaceManifest | None:
        """Commits what the call changed under ``workspace/``: written, new and removed files; ``None``: nothing."""
        upserts: dict[str, WorkspaceFile] = {}
        for path, data in after.items():
            if is_engine_config(path):
                continue
            before = checkout.files.get(path)
            if before is not None and before == data:
                continue
            ref = await self._objects.put(self._scope, data)
            upserts[path] = WorkspaceFile(
                path=path, content=ref, mime_type=mime_for(path, data)
            )
        deletes = frozenset(path for path in checkout.staged if path not in after)
        changes = ChangeSet(upserts, deletes)
        if changes.is_empty:
            return None
        base = checkout.base
        for _ in range(_COMMIT_ATTEMPTS):
            try:
                return await self._store.commit(
                    self._scope, self._workspace_id, base_revision=base.revision, changes=changes, reason="turn_end"
                )
            except WorkspaceRevisionConflict:
                # Another writer moved the head: this call's changes go on top of it, as an engine's save does.
                base = await self._store.head(self._scope, self._workspace_id)
                changes = rebase_changes(checkout.base.files, base.files, ChangeSet(upserts, deletes)).changes
                if changes.is_empty:
                    return None
        raise WorkspaceRevisionConflict("the workspace kept moving while this call's files were saved")
