from __future__ import annotations

import pytest

from app.agent_loop_lib.tools.builtin.sandbox.input_staging import (
    peek_staged_workspace_files,
)
from app.harness.adapters.memory_workspace import (
    InMemoryBinaryObjectStore,
    InMemoryWorkspaceManifestStore,
)
from app.harness.adapters.sandbox_workspace import SandboxWorkspaceMaterializer
from app.harness.contracts.workspace import ObjectScope, WorkspaceFile
from app.harness.workspace.virtual_fs import VirtualFileSystem, WorkspaceMount

_SCOPE = ObjectScope("org-1")


async def _mount(
    blobs: InMemoryBinaryObjectStore,
    manifests: InMemoryWorkspaceManifestStore,
    workspace_id: str,
    files: dict[str, bytes],
    *,
    writable: bool,
    prefix: str,
) -> WorkspaceMount:
    entries = {
        path: WorkspaceFile(path=path, content=await blobs.put(_SCOPE, content))
        for path, content in files.items()
    }
    manifest = await manifests.commit(workspace_id, 0, entries)
    return WorkspaceMount(prefix, manifest, writable=writable)


async def test_materializes_nested_binary_skills_and_private_artifacts() -> None:
    blobs = InMemoryBinaryObjectStore()
    manifests = InMemoryWorkspaceManifestStore()
    mounts = [
        await _mount(blobs, manifests, "session-1", {"notes/plan.md": b"plan"}, writable=True, prefix="/workspace"),
        await _mount(blobs, manifests, "skill-1-v2", {"SKILL.md": b"skill", "assets/logo.png": b"\x89PNG"}, writable=False, prefix="/skills/report/v2"),
        await _mount(blobs, manifests, "artifact-3-v1", {"source.csv": b"a,b\n1,2"}, writable=False, prefix="/artifacts/artifact-3/v1"),
    ]
    vfs = VirtualFileSystem(
        mounts=mounts,
        object_store=blobs,
        object_scope=_SCOPE,
        manifest_store=manifests,
    )

    files = await SandboxWorkspaceMaterializer().materialize(vfs)

    assert files["workspace/notes/plan.md"] == b"plan"
    assert files["skills/report/v2/assets/logo.png"] == b"\x89PNG"
    assert files["artifacts/artifact-3/v1/source.csv"] == b"a,b\n1,2"


async def test_stage_scope_does_not_leak_between_agent_runs() -> None:
    blobs = InMemoryBinaryObjectStore()
    manifests = InMemoryWorkspaceManifestStore()
    mount = await _mount(
        blobs, manifests, "session-1", {"state.json": b"{}"},
        writable=True, prefix="/workspace",
    )
    vfs = VirtualFileSystem(
        mounts=[mount],
        object_store=blobs,
        object_scope=_SCOPE,
        manifest_store=manifests,
    )
    materializer = SandboxWorkspaceMaterializer()

    assert peek_staged_workspace_files() is None
    async with materializer.stage(vfs):
        assert peek_staged_workspace_files() == {"workspace/state.json": b"{}"}
    assert peek_staged_workspace_files() is None


async def test_materialization_fails_before_partial_staging_when_quota_is_exceeded() -> None:
    blobs = InMemoryBinaryObjectStore()
    manifests = InMemoryWorkspaceManifestStore()
    mount = await _mount(
        blobs, manifests, "session-1", {"large.bin": b"12345"},
        writable=True, prefix="/workspace",
    )
    vfs = VirtualFileSystem(
        mounts=[mount],
        object_store=blobs,
        object_scope=_SCOPE,
        manifest_store=manifests,
    )

    with pytest.raises(ValueError, match="quota"):
        await SandboxWorkspaceMaterializer(max_total_bytes=4).materialize(vfs)
    assert peek_staged_workspace_files() is None
