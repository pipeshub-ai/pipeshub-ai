from __future__ import annotations

import pytest

from app.harness.adapters.memory_workspace import (
    InMemoryBinaryObjectStore,
    InMemoryWorkspaceManifestStore,
)
from app.harness.contracts.workspace import (
    ObjectRef,
    ObjectScope,
    WorkspaceFile,
    WorkspaceManifest,
    WorkspaceRevisionConflict,
)
from app.harness.workspace.virtual_fs import (
    VirtualFileSystem,
    WorkspaceMount,
    normalize_vfs_path,
)

_SCOPE = ObjectScope("org-1")


async def _manifest(
    store: InMemoryWorkspaceManifestStore,
    blobs: InMemoryBinaryObjectStore,
    workspace_id: str,
    files: dict[str, bytes],
) -> WorkspaceManifest:
    entries = {}
    for path, content in files.items():
        entries[path] = WorkspaceFile(path=path, content=await blobs.put(_SCOPE, content))
    return await store.commit(workspace_id, 0, entries)


class TestVirtualFileSystem:
    async def test_reads_nested_text_and_binary_skill_bundle(self) -> None:
        blobs = InMemoryBinaryObjectStore()
        manifests = InMemoryWorkspaceManifestStore()
        workspace = await _manifest(manifests, blobs, "session-1", {})
        skill = await _manifest(manifests, blobs, "skill-report-v3", {
            "SKILL.md": b"# Report skill",
            "scripts/render.py": b"print('ok')",
            "templates/logo.png": b"\x89PNG\x00binary",
        })
        vfs = VirtualFileSystem(
            mounts=[
                WorkspaceMount("/workspace", workspace, writable=True),
                WorkspaceMount("/skills/report/v3", skill),
            ],
            object_store=blobs,
            object_scope=_SCOPE,
            manifest_store=manifests,
        )

        assert await vfs.read_text("/skills/report/v3/SKILL.md") == "# Report skill"
        assert await vfs.read_bytes("/skills/report/v3/templates/logo.png") == b"\x89PNG\x00binary"
        assert await vfs.list_dir("/skills/report/v3") == ["SKILL.md", "scripts/", "templates/"]

    async def test_read_only_mount_cannot_be_changed(self) -> None:
        blobs = InMemoryBinaryObjectStore()
        manifests = InMemoryWorkspaceManifestStore()
        workspace = await _manifest(manifests, blobs, "session-1", {})
        skill = await _manifest(manifests, blobs, "skill-1", {"SKILL.md": b"safe"})
        vfs = VirtualFileSystem(
            mounts=[
                WorkspaceMount("/workspace", workspace, writable=True),
                WorkspaceMount("/skills/locked/v1", skill),
            ],
            object_store=blobs,
            object_scope=_SCOPE,
            manifest_store=manifests,
        )
        with pytest.raises(PermissionError):
            await vfs.write_text("/skills/locked/v1/SKILL.md", "tampered")

    async def test_commit_is_immutable_and_restores_on_another_worker(self) -> None:
        blobs = InMemoryBinaryObjectStore()
        manifests = InMemoryWorkspaceManifestStore(clock_ms=lambda: 123)
        base = await _manifest(manifests, blobs, "session-1", {})
        first = VirtualFileSystem(
            mounts=[WorkspaceMount("/workspace", base, writable=True)],
            object_store=blobs,
            object_scope=_SCOPE,
            manifest_store=manifests,
        )
        await first.write_bytes("/workspace/reports/final.pdf", b"%PDF-version-1")
        committed = await first.commit()
        assert committed.revision == 2
        assert committed.parent_revision == 1

        restored = VirtualFileSystem(
            mounts=[WorkspaceMount("/workspace", committed, writable=True)],
            object_store=blobs,
            object_scope=_SCOPE,
            manifest_store=manifests,
        )
        assert await restored.read_bytes("/workspace/reports/final.pdf") == b"%PDF-version-1"
        original = await manifests.get("session-1", 1)
        assert original is not None
        assert "reports/final.pdf" not in original.files

    async def test_concurrent_revision_commit_is_rejected(self) -> None:
        blobs = InMemoryBinaryObjectStore()
        manifests = InMemoryWorkspaceManifestStore()
        base = await _manifest(manifests, blobs, "session-1", {})
        left = VirtualFileSystem(
            mounts=[WorkspaceMount("/workspace", base, writable=True)],
            object_store=blobs,
            object_scope=_SCOPE,
            manifest_store=manifests,
        )
        right = VirtualFileSystem(
            mounts=[WorkspaceMount("/workspace", base, writable=True)],
            object_store=blobs,
            object_scope=_SCOPE,
            manifest_store=manifests,
        )
        await left.write_text("/workspace/a.txt", "left")
        await right.write_text("/workspace/b.txt", "right")
        await left.commit()
        with pytest.raises(WorkspaceRevisionConflict):
            await right.commit()

    @pytest.mark.parametrize(
        "path",
        ["/workspace/../secret", "/workspace//secret", "/workspace/./secret", "C:\\secret"],
    )
    def test_rejects_noncanonical_or_cross_platform_traversal(self, path: str) -> None:
        with pytest.raises(ValueError):
            normalize_vfs_path(path)

    async def test_manifest_rejects_mismatched_or_traversing_file_keys(self) -> None:
        blobs = InMemoryBinaryObjectStore()
        manifests = InMemoryWorkspaceManifestStore()
        ref = await blobs.put(_SCOPE, b"secret")
        with pytest.raises(ValueError):
            await manifests.commit(
                "session-1",
                0,
                {"../secret": WorkspaceFile(path="safe", content=ref)},
            )
        with pytest.raises(ValueError):
            WorkspaceFile(path="../secret", content=ref)

    async def test_manifest_and_object_references_are_immutable(self) -> None:
        blobs = InMemoryBinaryObjectStore()
        manifests = InMemoryWorkspaceManifestStore()
        manifest = await _manifest(manifests, blobs, "session-1", {"safe.txt": b"safe"})

        with pytest.raises(TypeError):
            manifest.files["other.txt"] = WorkspaceFile(  # type: ignore[index]
                path="other.txt",
                content=ObjectRef("object", "sha256:value", 1),
            )
        restored = await manifests.get("session-1")
        assert restored is not None
        assert set(restored.files) == {"safe.txt"}

    async def test_object_reference_cannot_cross_tenant_boundary(self) -> None:
        blobs = InMemoryBinaryObjectStore()
        ref = await blobs.put(_SCOPE, b"tenant secret")

        with pytest.raises(FileNotFoundError):
            await blobs.get(ObjectScope("org-2"), ref)
