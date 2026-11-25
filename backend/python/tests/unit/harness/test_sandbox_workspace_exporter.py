from __future__ import annotations

import pytest

from app.harness.adapters.memory_workspace import (
    InMemoryBinaryObjectStore,
    InMemoryWorkspaceManifestStore,
)
from app.harness.adapters.sandbox_workspace import SandboxWorkspaceExporter
from app.harness.contracts.workspace import ObjectScope, WorkspaceFile
from app.harness.workspace.virtual_fs import VirtualFileSystem, WorkspaceMount

_SCOPE = ObjectScope("org-1")


class FakeSandbox:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.downloaded: list[str] = []

    async def list_files(self) -> list[str]:
        return list(self.files)

    async def download_file(self, path: str) -> bytes:
        self.downloaded.append(path)
        return self.files[path]


async def _vfs(initial: dict[str, bytes]) -> tuple[
    VirtualFileSystem,
    InMemoryBinaryObjectStore,
]:
    objects = InMemoryBinaryObjectStore()
    manifests = InMemoryWorkspaceManifestStore(clock_ms=lambda: 1000)
    entries = {
        path: WorkspaceFile(path=path, content=await objects.put(_SCOPE, content))
        for path, content in initial.items()
    }
    manifest = await manifests.commit("session-1", 0, entries)
    return VirtualFileSystem(
        mounts=[WorkspaceMount("/workspace", manifest, writable=True)],
        object_store=objects,
        object_scope=_SCOPE,
        manifest_store=manifests,
    ), objects


class TestSandboxWorkspaceExporter:
    async def test_commits_created_modified_and_deleted_workspace_files(self) -> None:
        vfs, _ = await _vfs({"keep.txt": b"before", "delete.txt": b"remove"})
        sandbox = FakeSandbox({
            "workspace/keep.txt": b"after",
            "workspace/nested/chart.bin": b"\x00\xff",
            "output/final.pdf": b"visible artifact",
            "skills/report/SKILL.md": b"protected",
        })

        manifest = await SandboxWorkspaceExporter().export(vfs, sandbox)

        assert manifest.revision == 2
        assert await vfs.read_bytes("/workspace/keep.txt") == b"after"
        assert await vfs.read_bytes("/workspace/nested/chart.bin") == b"\x00\xff"
        with pytest.raises(FileNotFoundError):
            await vfs.read_bytes("/workspace/delete.txt")
        assert sandbox.downloaded == [
            "workspace/keep.txt",
            "workspace/nested/chart.bin",
        ]

    async def test_unchanged_snapshot_does_not_create_a_revision(self) -> None:
        vfs, _ = await _vfs({"same.txt": b"same"})
        manifest = await SandboxWorkspaceExporter().export(
            vfs,
            FakeSandbox({"workspace/same.txt": b"same"}),
        )
        assert manifest.revision == 1

    async def test_quota_failure_does_not_mutate_vfs(self) -> None:
        vfs, _ = await _vfs({"safe.txt": b"safe"})
        exporter = SandboxWorkspaceExporter(max_total_bytes=3)

        with pytest.raises(ValueError, match="total byte quota"):
            await exporter.export(
                vfs,
                FakeSandbox({"workspace/replacement.txt": b"1234"}),
            )

        assert not vfs.has_changes
        assert await vfs.read_bytes("/workspace/safe.txt") == b"safe"

    async def test_non_workspace_files_are_never_downloaded_or_persisted(self) -> None:
        vfs, _ = await _vfs({})
        sandbox = FakeSandbox({
            "output/final.pdf": b"visible",
            "artifacts/private.bin": b"staged input",
            "skills/report/assets/logo.png": b"skill input",
        })

        manifest = await SandboxWorkspaceExporter().export(vfs, sandbox)
        assert manifest.revision == 1
        assert manifest.files == {}
        assert sandbox.downloaded == []
