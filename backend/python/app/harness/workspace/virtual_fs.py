from __future__ import annotations

import posixpath
import unicodedata
from dataclasses import dataclass, replace

from app.harness.contracts.workspace import (
    MAX_WORKSPACE_PATH_BYTES,
    MAX_WORKSPACE_PATH_DEPTH,
    BinaryObjectStore,
    ObjectScope,
    WorkspaceFile,
    WorkspaceManifest,
    WorkspaceManifestStore,
    normalize_workspace_file_path,
)
from harness_core.mime import mime_for


def normalize_vfs_path(path: str) -> str:
    if not isinstance(path, str) or not path:
        raise ValueError("VFS path must be a non-empty string")
    if "\x00" in path or "\\" in path:
        raise ValueError("VFS path contains a forbidden character")
    normalized = unicodedata.normalize("NFC", path)
    if len(normalized.encode("utf-8")) > MAX_WORKSPACE_PATH_BYTES:
        raise ValueError("VFS path is too long")
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    parts = normalized.split("/")[1:]
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("VFS path must be canonical and cannot traverse")
    if len(parts) > MAX_WORKSPACE_PATH_DEPTH:
        raise ValueError("VFS path is too deep")
    return "/" + "/".join(parts)


@dataclass(frozen=True, slots=True)
class WorkspaceMount:
    prefix: str
    manifest: WorkspaceManifest
    writable: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "prefix", normalize_vfs_path(self.prefix))


class VirtualFileSystem:
    """Binary-safe composite view with one CAS-committed writable mount."""

    def __init__(
        self,
        *,
        mounts: list[WorkspaceMount],
        object_store: BinaryObjectStore,
        object_scope: ObjectScope,
        manifest_store: WorkspaceManifestStore,
    ) -> None:
        prefixes = [mount.prefix for mount in mounts]
        if len(prefixes) != len(set(prefixes)):
            raise ValueError("VFS mount prefixes must be unique")
        writable = [mount for mount in mounts if mount.writable]
        if len(writable) != 1:
            raise ValueError("VFS requires exactly one writable workspace mount")
        self._mounts = sorted(mounts, key=lambda mount: len(mount.prefix), reverse=True)
        self._writable_prefix = writable[0].prefix
        self._object_store = object_store
        self._object_scope = object_scope
        self._manifest_store = manifest_store
        self._overlay: dict[str, bytes | None] = {}

    @property
    def writable_manifest(self) -> WorkspaceManifest:
        return next(
            mount.manifest for mount in self._mounts
            if mount.prefix == self._writable_prefix
        )

    @property
    def has_changes(self) -> bool:
        return bool(self._overlay)

    async def read_bytes(self, path: str) -> bytes:
        mount, relative = self._resolve_file(path)
        if mount.writable and relative in self._overlay:
            content = self._overlay[relative]
            if content is None:
                raise FileNotFoundError(path)
            return bytes(content)
        entry = mount.manifest.files.get(relative)
        if entry is None:
            raise FileNotFoundError(path)
        return await self._object_store.get(self._object_scope, entry.content)

    async def read_text(self, path: str, encoding: str = "utf-8") -> str:
        return (await self.read_bytes(path)).decode(encoding)

    async def write_bytes(self, path: str, content: bytes) -> None:
        mount, relative = self._resolve_file(path)
        if not mount.writable:
            raise PermissionError(f"VFS mount is read-only: {mount.prefix}")
        self._overlay[relative] = bytes(content)

    async def write_text(self, path: str, content: str, encoding: str = "utf-8") -> None:
        await self.write_bytes(path, content.encode(encoding))

    async def delete(self, path: str) -> None:
        mount, relative = self._resolve_file(path)
        if not mount.writable:
            raise PermissionError(f"VFS mount is read-only: {mount.prefix}")
        if relative not in mount.manifest.files and relative not in self._overlay:
            raise FileNotFoundError(path)
        self._overlay[relative] = None

    async def list_dir(self, path: str = "/") -> list[str]:
        directory = "/" if path == "/" else normalize_vfs_path(path)
        prefix = directory.rstrip("/") + "/"
        children: set[str] = set()
        for candidate in self._all_file_paths():
            if not candidate.startswith(prefix):
                continue
            remainder = candidate[len(prefix):]
            if not remainder:
                continue
            head, separator, _tail = remainder.partition("/")
            children.add(head + ("/" if separator else ""))
        return sorted(children)

    def walk_files(self, path: str = "/") -> list[str]:
        """List file paths recursively without exposing backing object IDs."""
        root = "/" if path == "/" else normalize_vfs_path(path)
        paths = self._all_file_paths()
        if root in paths:
            return [root]
        prefix = root.rstrip("/") + "/"
        return sorted(candidate for candidate in paths if candidate.startswith(prefix))

    async def commit(self) -> WorkspaceManifest:
        mount = next(item for item in self._mounts if item.prefix == self._writable_prefix)
        files = dict(mount.manifest.files)
        for path, content in sorted(self._overlay.items()):
            if content is None:
                files.pop(path, None)
                continue
            ref = await self._object_store.put(self._object_scope, content)
            mime_type = mime_for(path, content)
            files[path] = WorkspaceFile(path=path, content=ref, mime_type=mime_type)
        committed = await self._manifest_store.commit(
            mount.manifest.workspace_id,
            mount.manifest.revision,
            files,
        )
        self._mounts = [
            replace(item, manifest=committed) if item.prefix == self._writable_prefix else item
            for item in self._mounts
        ]
        self._overlay.clear()
        return committed

    def _resolve_file(self, path: str) -> tuple[WorkspaceMount, str]:
        normalized = normalize_vfs_path(path)
        for mount in self._mounts:
            if normalized.startswith(mount.prefix + "/"):
                relative = posixpath.relpath(normalized, mount.prefix)
                return mount, normalize_workspace_file_path(relative)
        raise FileNotFoundError(path)

    def _all_file_paths(self) -> set[str]:
        paths: set[str] = set()
        for mount in self._mounts:
            for relative in mount.manifest.files:
                paths.add(f"{mount.prefix}/{relative}")
            if mount.writable:
                for relative, content in self._overlay.items():
                    absolute = f"{mount.prefix}/{relative}"
                    if content is None:
                        paths.discard(absolute)
                    else:
                        paths.add(absolute)
        return paths
