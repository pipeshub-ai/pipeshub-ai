"""Re-exports the workspace contracts promoted to ``harness_core.workspace`` (P3.2)."""

from harness_core.workspace import (
    MAX_WORKSPACE_PATH_BYTES,
    MAX_WORKSPACE_PATH_DEPTH,
    BinaryObjectStore,
    ObjectRef,
    ObjectScope,
    WorkspaceFile,
    WorkspaceManifest,
    WorkspaceManifestStore,
    WorkspaceRevisionConflict,
    normalize_workspace_file_path,
    workspace_manifest_content_hash,
)

__all__ = [
    "MAX_WORKSPACE_PATH_BYTES",
    "MAX_WORKSPACE_PATH_DEPTH",
    "BinaryObjectStore",
    "ObjectRef",
    "ObjectScope",
    "WorkspaceFile",
    "WorkspaceManifest",
    "WorkspaceManifestStore",
    "WorkspaceRevisionConflict",
    "normalize_workspace_file_path",
    "workspace_manifest_content_hash",
]
