"""Tests for `app.agents.actions.artifacts.artifacts.ArtifactManager` — the
model-facing save/update/list/download-link tools over
`ArtifactRegistryService`."""

from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.actions.artifacts.artifacts import ArtifactManager
from app.models.entities import ArtifactType, LifecycleStatus
from app.services.artifact_registry import ArtifactMetadata, ArtifactVersion, VersionConflictError
from app.services.artifact_registry.access import AccessDeniedError, ArtifactNotFoundError


def _make_metadata(**overrides) -> ArtifactMetadata:
    defaults = {
        "artifact_id": "art-1",
        "org_id": "org-1",
        "conversation_id": "conv-1",
        "name": "report.md",
        "logical_name": "report.md",
        "artifact_type": ArtifactType.OTHER,
        "mime_type": "text/markdown",
        "lifecycle_status": LifecycleStatus.PUBLISHED,
        "version": 1,
        "size_bytes": 5,
        "document_id": "doc-1",
    }
    defaults.update(overrides)
    return ArtifactMetadata(**defaults)


def _make_manager(*, registry: MagicMock | None = None, **state_overrides) -> tuple[ArtifactManager, MagicMock]:
    state = {
        "org_id": "org-1", "user_id": "user-1", "conversation_id": "conv-1",
        "graph_provider": MagicMock(), "blob_store": MagicMock(),
    }
    state.update(state_overrides)
    manager = ArtifactManager(state)
    mock_registry = registry if registry is not None else MagicMock()
    manager._registry = lambda: mock_registry  # bypass real ArtifactRegistryService construction
    return manager, mock_registry


class TestSaveArtifact:
    async def test_creates_new_artifact_and_returns_id(self) -> None:
        metadata = _make_metadata()
        registry = MagicMock()
        registry.register_output = AsyncMock(return_value=(metadata, None))
        registry.get_download_url = AsyncMock(return_value="https://blob.example/report.md")
        manager, registry = _make_manager(registry=registry)

        success, payload = await manager.save_artifact(name="report.md", content="hello world")

        assert success is True
        body = json.loads(payload)
        assert body["artifact_id"] == "art-1"
        assert body["version"] == 1
        assert body["deduplicated"] is False
        registry.register_output.assert_awaited_once()
        _, kwargs = registry.register_output.call_args
        assert kwargs["content"] == b"hello world"
        assert kwargs["conversation_id"] == "conv-1"

    async def test_decodes_base64_content(self) -> None:
        metadata = _make_metadata()
        registry = MagicMock()
        registry.register_output = AsyncMock(return_value=(metadata, None))
        manager, registry = _make_manager(registry=registry)

        raw = b"\x89PNG-bytes"
        success, _ = await manager.save_artifact(
            name="chart.png", content=base64.b64encode(raw).decode(), is_base64=True,
        )

        assert success is True
        _, kwargs = registry.register_output.call_args
        assert kwargs["content"] == raw

    async def test_invalid_base64_returns_error_without_calling_registry(self) -> None:
        registry = MagicMock()
        registry.register_output = AsyncMock()
        manager, registry = _make_manager(registry=registry)

        success, payload = await manager.save_artifact(name="x.bin", content="not-base64!!", is_base64=True)

        assert success is False
        assert "Invalid base64" in json.loads(payload)["error"]
        registry.register_output.assert_not_awaited()

    async def test_reports_deduplication_when_content_unchanged(self) -> None:
        metadata = _make_metadata(version=3)
        version = ArtifactVersion(
            version=3, size_bytes=5, content_hash="h", mime_type="text/markdown",
            created_at=1, deduplicated=True,
        )
        registry = MagicMock()
        registry.register_output = AsyncMock(return_value=(metadata, version))
        manager, _ = _make_manager(registry=registry)

        success, payload = await manager.save_artifact(name="report.md", content="same content")
        body = json.loads(payload)
        assert success is True
        assert body["deduplicated"] is True

    async def test_size_cap_violation_surfaces_as_error(self) -> None:
        registry = MagicMock()
        registry.register_output = AsyncMock(side_effect=ValueError("Artifact content exceeds cap"))
        manager, _ = _make_manager(registry=registry)

        success, payload = await manager.save_artifact(name="big.bin", content="x")
        assert success is False
        assert "exceeds cap" in json.loads(payload)["error"]

    async def test_missing_registry_returns_error(self) -> None:
        manager = ArtifactManager({"org_id": "org-1", "user_id": "user-1", "conversation_id": "conv-1"})
        success, payload = await manager.save_artifact(name="x.txt", content="hi")
        assert success is False
        assert "unavailable" in json.loads(payload)["error"]

    async def test_missing_conversation_id_returns_error(self) -> None:
        manager, _ = _make_manager(conversation_id=None)
        success, payload = await manager.save_artifact(name="x.txt", content="hi")
        assert success is False
        assert "unavailable" in json.loads(payload)["error"]


class TestUpdateArtifact:
    async def test_updates_existing_artifact(self) -> None:
        metadata = _make_metadata(version=2)
        version = ArtifactVersion(
            version=2, size_bytes=5, content_hash="h", mime_type="text/markdown", created_at=1,
        )
        registry = MagicMock()
        registry.add_version = AsyncMock(return_value=(version, metadata))
        registry.get_download_url = AsyncMock(return_value="https://blob.example/report.md")
        manager, registry = _make_manager(registry=registry)

        success, payload = await manager.update_artifact(artifact_id="art-1", content="new content")

        assert success is True
        body = json.loads(payload)
        assert body["version"] == 2
        registry.add_version.assert_awaited_once()
        _, kwargs = registry.add_version.call_args
        assert kwargs["artifact_id"] == "art-1"
        assert kwargs["content"] == b"new content"

    async def test_not_found_returns_clear_error(self) -> None:
        registry = MagicMock()
        registry.add_version = AsyncMock(side_effect=ArtifactNotFoundError("nope"))
        manager, _ = _make_manager(registry=registry)

        success, payload = await manager.update_artifact(artifact_id="ghost", content="x")
        assert success is False
        assert "No artifact found" in json.loads(payload)["error"]

    async def test_access_denied_returns_permission_error(self) -> None:
        registry = MagicMock()
        registry.add_version = AsyncMock(side_effect=AccessDeniedError("nope"))
        manager, _ = _make_manager(registry=registry)

        success, payload = await manager.update_artifact(artifact_id="art-1", content="x")
        assert success is False
        assert "permission" in json.loads(payload)["error"]

    async def test_version_conflict_is_reported_not_silently_overwritten(self) -> None:
        registry = MagicMock()
        registry.add_version = AsyncMock(
            side_effect=VersionConflictError("Artifact art-1 is at version 3, but caller expected 2")
        )
        manager, _ = _make_manager(registry=registry)

        success, payload = await manager.update_artifact(
            artifact_id="art-1", content="x", expected_version=2,
        )
        assert success is False
        assert "expected 2" in json.loads(payload)["error"]


class TestGetArtifactDownloadUrl:
    async def test_returns_url_on_success(self) -> None:
        registry = MagicMock()
        registry.get_download_url = AsyncMock(return_value="https://blob.example/x")
        manager, _ = _make_manager(registry=registry)

        success, payload = await manager.get_artifact_download_url(artifact_id="art-1")
        assert success is True
        assert json.loads(payload)["download_url"] == "https://blob.example/x"

    async def test_not_found(self) -> None:
        registry = MagicMock()
        registry.get_download_url = AsyncMock(side_effect=ArtifactNotFoundError("nope"))
        manager, _ = _make_manager(registry=registry)

        success, payload = await manager.get_artifact_download_url(artifact_id="ghost")
        assert success is False
        assert "No artifact found" in json.loads(payload)["error"]

    async def test_access_denied(self) -> None:
        registry = MagicMock()
        registry.get_download_url = AsyncMock(side_effect=AccessDeniedError("nope"))
        manager, _ = _make_manager(registry=registry)

        success, payload = await manager.get_artifact_download_url(artifact_id="art-1")
        assert success is False
        assert "permission" in json.loads(payload)["error"]


class TestListArtifacts:
    async def test_lists_artifacts_with_lineage(self) -> None:
        metadata = _make_metadata(
            derived_from_code_artifact_id="code-1", derived_from_code_version=2,
        )
        registry = MagicMock()
        registry.list_for_conversation = AsyncMock(return_value=[metadata])
        manager, _ = _make_manager(registry=registry)

        success, payload = await manager.list_artifacts()
        body = json.loads(payload)
        assert success is True
        assert body["count"] == 1
        entry = body["artifacts"][0]
        assert entry["artifact_id"] == "art-1"
        assert entry["derived_from_code_artifact_id"] == "code-1"
        assert entry["derived_from_code_version"] == 2

    async def test_empty_conversation_returns_empty_list(self) -> None:
        registry = MagicMock()
        registry.list_for_conversation = AsyncMock(return_value=[])
        manager, _ = _make_manager(registry=registry)

        success, payload = await manager.list_artifacts()
        body = json.loads(payload)
        assert success is True
        assert body["count"] == 0
        assert body["artifacts"] == []
