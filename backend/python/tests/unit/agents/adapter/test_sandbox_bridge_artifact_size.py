"""Output files over the artifact size limit are never read into memory.

The POST hook used to read every `$OUTPUT_DIR` file whole and only then let
the artifact registry refuse the ones over its limit, so a run that left a
sparse multi-gigabyte file there filled the worker's memory. The size is now
checked first; the file is reported to the model and skipped unread.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from app.agent_loop_lib.sandbox.coding.local import LocalCodingSandbox
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.sandbox_bridge import _register_run_code_artifacts
from app.models.entities import ArtifactType, LifecycleStatus
from app.services.artifact_registry import ArtifactMetadata

MiB = 1024 * 1024


def _context() -> AgentContext:
    return AgentContext(
        org_id="org-1", user_id="user-1", user_email="u@example.com", logger=MagicMock(),
        retrieval_service=MagicMock(config_service=MagicMock()), conversation_id="conv-1",
    )


def _registry() -> MagicMock:
    registry = MagicMock()
    registry._max_bytes = 25 * MiB
    registry.register_output = AsyncMock(return_value=(ArtifactMetadata(
        artifact_id="rec-1", org_id="org-1", conversation_id="conv-1", name="ok.txt",
        logical_name="ok.txt", artifact_type=ArtifactType.OTHER, mime_type="text/plain",
        lifecycle_status=LifecycleStatus.PUBLISHED, version=1, size_bytes=2, document_id="doc-1",
    ), 1))
    registry.get_download_url = AsyncMock(return_value="https://blob.example/ok.txt")
    registry.record_derivation = AsyncMock()
    return registry


async def test_an_oversized_output_file_is_reported_and_never_read(tmp_path, monkeypatch) -> None:
    registry = _registry()
    monkeypatch.setattr(AgentContext, "artifact_registry", property(lambda self: registry))
    backend = LocalCodingSandbox(working_dir=str(tmp_path / "sbx"))
    await backend.provision()
    out = os.path.join(backend.working_dir, "output")
    with open(os.path.join(out, "big.bin"), "wb") as f:
        f.truncate(60 * MiB)  # sparse: no disk used
    with open(os.path.join(out, "ok.txt"), "wb") as f:
        f.write(b"ok")
    downloads: list[str] = []
    real_download = backend.download_file

    async def _spy(path: str) -> bytes:
        downloads.append(path)
        return await real_download(path)

    backend.download_file = _spy  # type: ignore[method-assign]
    real_head = backend.read_file_head

    async def _head_spy(path: str, max_bytes: int) -> tuple[bytes, bool]:
        downloads.append(path)
        return await real_head(path, max_bytes)

    backend.read_file_head = _head_spy  # type: ignore[method-assign]
    manager = MagicMock()
    manager.get.return_value = backend
    data: dict[str, Any] = {}

    await _register_run_code_artifacts(
        _context(), manager, backend.sandbox_id, ["output/big.bin", "output/ok.txt"], data,
        source_tool="run_code", code_artifact_id=None, code_artifact_version=None,
    )

    assert downloads == ["output/ok.txt"]
    assert registry.register_output.await_count == 1
    errors = data.get("artifact_errors") or []
    assert [e["file"] for e in errors] == ["big.bin"]
    assert "exceeds the 26214400-byte cap" in errors[0]["error"]
    assert data.get("artifact_errors_note")
    assert len(data["artifacts"]) == 1
