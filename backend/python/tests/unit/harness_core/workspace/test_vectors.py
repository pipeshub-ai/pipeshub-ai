"""NW-5, NW-6 (Python side): the shared vectors Node is tested against hold here too."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_core.workspace import (
    FILE_MODE,
    ObjectRef,
    WorkspaceFile,
    normalize_workspace_file_path,
    workspace_manifest_content_hash,
)

VECTORS = json.loads(
    (Path(__file__).resolve().parents[6] / "contracts/harness/vectors/workspace.json").read_text(
        encoding="utf-8"
    )
)


@pytest.mark.parametrize(("raw", "canonical"), VECTORS["paths"]["accepted"])
def test_accepted_paths(raw: str, canonical: str) -> None:
    assert normalize_workspace_file_path(raw) == canonical


@pytest.mark.parametrize("raw", VECTORS["paths"]["refused"])
def test_refused_paths(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_workspace_file_path(raw)


@pytest.mark.parametrize("manifest", VECTORS["manifests"])
def test_manifest_hashes(manifest: dict[str, object]) -> None:
    entries = [
        WorkspaceFile(
            f["path"],
            ObjectRef(f["objectId"], f["contentHash"], f["sizeBytes"]),
            f["mimeType"],
            f.get("mode", FILE_MODE),
        )
        for f in manifest["files"]  # type: ignore[union-attr]
    ]
    files = {entry.path: entry for entry in entries}
    assert workspace_manifest_content_hash(files) == manifest["contentHash"]
