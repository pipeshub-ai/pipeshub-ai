from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.agent_loop_lib.tools.builtin.sandbox.input_staging import stage_workspace_files
from app.harness.contracts.workspace import normalize_workspace_file_path
from app.harness.workspace.virtual_fs import VirtualFileSystem, normalize_vfs_path
from harness_core.workspace.safe_names import check_portable_path

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from typing import Protocol

    from app.harness.contracts.workspace import WorkspaceManifest

    class SandboxFileAccess(Protocol):
        async def list_files(self) -> list[str]: ...

        async def download_file(self, path: str) -> bytes: ...


@dataclass(frozen=True, slots=True)
class SandboxProjection:
    source_root: str
    destination_root: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_root", normalize_vfs_path(self.source_root))
        destination = self.destination_root.strip("/")
        if destination:
            destination = normalize_workspace_file_path(destination)
        object.__setattr__(self, "destination_root", destination)


DEFAULT_SANDBOX_PROJECTIONS = (
    SandboxProjection("/workspace", "workspace"),
    SandboxProjection("/skills", "skills"),
    SandboxProjection("/artifacts", "artifacts"),
    SandboxProjection("/session", "session"),
    SandboxProjection("/system", "system"),
)


class SandboxWorkspaceMaterializer:
    def __init__(
        self,
        *,
        max_files: int = 1_000,
        max_total_bytes: int = 100 * 1024 * 1024,
    ) -> None:
        if max_files <= 0 or max_total_bytes <= 0:
            raise ValueError("Sandbox workspace quotas must be positive")
        self._max_files = max_files
        self._max_total_bytes = max_total_bytes

    async def materialize(
        self,
        vfs: VirtualFileSystem,
        projections: tuple[SandboxProjection, ...] = DEFAULT_SANDBOX_PROJECTIONS,
    ) -> dict[str, bytes]:
        files: dict[str, bytes] = {}
        total_bytes = 0
        for projection in projections:
            for source_path in vfs.walk_files(projection.source_root):
                relative = source_path.removeprefix(projection.source_root + "/")
                destination = "/".join(filter(None, [projection.destination_root, relative]))
                destination = normalize_workspace_file_path(destination)
                if destination in files:
                    raise ValueError(f"Sandbox projection path collides: {destination}")
                content = await vfs.read_bytes(source_path)
                total_bytes += len(content)
                if len(files) + 1 > self._max_files or total_bytes > self._max_total_bytes:
                    raise ValueError("Sandbox workspace exceeds materialization quota")
                files[destination] = content
        return files

    @asynccontextmanager
    async def stage(
        self,
        vfs: VirtualFileSystem,
        projections: tuple[SandboxProjection, ...] = DEFAULT_SANDBOX_PROJECTIONS,
    ) -> AsyncIterator[None]:
        files = await self.materialize(vfs, projections)
        with stage_workspace_files(files):
            yield


class SandboxWorkspaceExporter:
    """Commits only the sandbox's writable workspace tree back to VFS CAS."""

    def __init__(
        self,
        *,
        max_files: int = 1_000,
        max_total_bytes: int = 100 * 1024 * 1024,
        max_file_bytes: int = 25 * 1024 * 1024,
    ) -> None:
        if min(max_files, max_total_bytes, max_file_bytes) <= 0:
            raise ValueError("Sandbox export quotas must be positive")
        self._max_files = max_files
        self._max_total_bytes = max_total_bytes
        self._max_file_bytes = max_file_bytes

    async def export(
        self,
        vfs: VirtualFileSystem,
        sandbox: "SandboxFileAccess",
    ) -> "WorkspaceManifest":
        sandbox_files = await self.snapshot(sandbox)
        current_paths = {
            path.removeprefix("/workspace/")
            for path in vfs.walk_files("/workspace")
        }

        # Snapshot and quota validation complete before VFS mutation, so a
        # partial or oversized sandbox response cannot leave a half-applied overlay.
        for relative, content in sandbox_files.items():
            absolute = f"/workspace/{relative}"
            try:
                unchanged = await vfs.read_bytes(absolute) == content
            except FileNotFoundError:
                unchanged = False
            if not unchanged:
                await vfs.write_bytes(absolute, content)
        for relative in current_paths - sandbox_files.keys():
            await vfs.delete(f"/workspace/{relative}")

        if not vfs.has_changes:
            return vfs.writable_manifest
        return await vfs.commit()

    async def snapshot(
        self,
        sandbox: "SandboxFileAccess",
    ) -> dict[str, bytes]:
        candidates: dict[str, str] = {}
        aliases: set[str] = set()
        for raw_path in await sandbox.list_files():
            if not raw_path.startswith("workspace/"):
                continue
            # SEC-FS-02: a name with a drive or an encoded separator is refused (``UnsafeName`` is a ``ValueError``).
            relative = check_portable_path(raw_path.removeprefix("workspace/"))
            alias = relative.casefold()
            if alias in aliases:
                raise ValueError(f"Sandbox workspace contains a duplicate path: {relative!r}")
            aliases.add(alias)
            candidates[relative] = raw_path
        if len(candidates) > self._max_files:
            raise ValueError("Sandbox workspace exceeds file-count quota")

        files: dict[str, bytes] = {}
        total_bytes = 0
        for relative, raw_path in sorted(candidates.items()):
            content = await sandbox.download_file(raw_path)
            if len(content) > self._max_file_bytes:
                raise ValueError(f"Sandbox workspace file exceeds quota: {relative!r}")
            total_bytes += len(content)
            if total_bytes > self._max_total_bytes:
                raise ValueError("Sandbox workspace exceeds total byte quota")
            files[relative] = bytes(content)
        return files
