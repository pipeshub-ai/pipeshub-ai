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


_PROGRAM = (
    "import PptxGenJS from 'pptxgenjs';\n"
    "const BRAND = { font: 'Georgia', accent: '#5B2C6F' };\n"
    "// ... styling the model must be able to see to preserve it ...\n"
)


class TestGetArtifactContent:
    """The tool that makes "update this / keep the same style" possible.

    Before it existed, a code artifact could be staged into the sandbox as a
    FILE but never read by the model, so a follow-up turn had nothing to edit
    and rewrote the program from scratch — losing the original's styling.
    """

    def _manager_with(self, content: bytes, **meta):
        fields = {
            "artifact_id": "code-1", "name": "code_abc123.ts",
            "artifact_type": ArtifactType.CODE,
            "mime_type": "application/typescript",
        }
        fields.update(meta)
        metadata = _make_metadata(**fields)
        registry = MagicMock()
        registry.resolve = AsyncMock(return_value=metadata)
        registry.get_content = AsyncMock(return_value=content)
        return _make_manager(registry=registry)

    async def test_returns_the_full_source(self) -> None:
        manager, _ = self._manager_with(_PROGRAM.encode())
        success, payload = await manager.get_artifact_content(artifact_id="code-1")

        assert success is True
        body = json.loads(payload)
        assert body["content"] == _PROGRAM
        assert body["truncated"] is False
        assert body["total_chars"] == len(_PROGRAM)
        assert body["name"] == "code_abc123.ts"

    async def test_accepts_a_derived_from_code_artifact_id(self) -> None:
        """The reminder hands the model an ID, not a name — `resolve` takes
        either, and the tool must pass the ref through untouched."""
        manager, registry = self._manager_with(_PROGRAM.encode())
        await manager.get_artifact_content(artifact_id="4bc1ae97-derived-id")

        assert registry.resolve.await_args.kwargs["ref"] == "4bc1ae97-derived-id"
        assert registry.resolve.await_args.kwargs["conversation_id"] == "conv-1"

    async def test_truncates_long_content_and_says_so(self) -> None:
        manager, _ = self._manager_with(("x" * 5000).encode())
        success, payload = await manager.get_artifact_content(
            artifact_id="code-1", max_chars=1000,
        )

        body = json.loads(payload)
        assert success is True
        assert len(body["content"]) == 1000
        assert body["truncated"] is True
        assert body["total_chars"] == 5000
        # A prefix silently presented as the whole file is the failure mode
        # this tool exists to avoid, so the response must be explicit.
        assert "1000" in body["note"] and "5000" in body["note"]

    async def test_offset_pages_through_a_long_file(self) -> None:
        manager, _ = self._manager_with(b"ABCDEFGHIJ")
        _, payload = await manager.get_artifact_content(
            artifact_id="code-1", max_chars=4, offset=4,
        )
        body = json.loads(payload)
        assert body["content"] == "EFGH"
        assert body["offset"] == 4
        assert body["truncated"] is True

    async def test_reading_to_the_end_is_not_marked_truncated(self) -> None:
        manager, _ = self._manager_with(b"ABCDEFGHIJ")
        _, payload = await manager.get_artifact_content(
            artifact_id="code-1", max_chars=6, offset=4,
        )
        assert json.loads(payload)["truncated"] is False

    async def test_binary_is_refused_with_a_usable_alternative(self) -> None:
        """A PDF in the context window is wasted tokens and can wedge the
        turn; the model needs to be told where to go instead."""
        manager, _ = self._manager_with(
            b"%PDF-1.4\x00\xff\xfe binary", mime_type="application/pdf", name="deck.pdf",
        )
        success, payload = await manager.get_artifact_content(artifact_id="code-1")

        assert success is False
        body = json.loads(payload)
        assert "binary" in body["error"]
        assert "get_artifact_download_url" in body["error"]
        assert "content" not in body

    async def test_missing_artifact_reports_the_ref(self) -> None:
        registry = MagicMock()
        registry.resolve = AsyncMock(side_effect=ArtifactNotFoundError("nope"))
        manager, _ = _make_manager(registry=registry)

        success, payload = await manager.get_artifact_content(artifact_id="ghost")
        assert success is False
        assert "ghost" in json.loads(payload)["error"]

    async def test_access_denied_is_not_swallowed(self) -> None:
        registry = MagicMock()
        registry.resolve = AsyncMock(side_effect=AccessDeniedError("denied"))
        manager, _ = _make_manager(registry=registry)

        success, payload = await manager.get_artifact_content(artifact_id="someone-elses")
        assert success is False
        assert "permission" in json.loads(payload)["error"].lower()

    async def test_max_chars_cannot_exceed_the_ceiling(self) -> None:
        """A caller asking for 10 million characters must not be able to
        blow the context window."""
        from app.agents.actions.artifacts.artifacts import _MAX_ARTIFACT_CONTENT_CHARS

        manager, _ = self._manager_with(("x" * 100_000).encode())
        _, payload = await manager.get_artifact_content(
            artifact_id="code-1", max_chars=10_000_000,
        )
        assert len(json.loads(payload)["content"]) == _MAX_ARTIFACT_CONTENT_CHARS

    async def test_unavailable_registry_degrades_cleanly(self) -> None:
        manager, _ = _make_manager()
        manager._registry = lambda: None
        success, payload = await manager.get_artifact_content(artifact_id="code-1")
        assert success is False
        assert "unavailable" in json.loads(payload)["error"]


class TestGetArtifactContentBoundsTheFetch:
    """`registry.get_content()` pulls the WHOLE blob into memory before
    anything is decoded or sliced, so `max_chars` bounds the response but not
    the read. A multi-hundred-MB artifact would be fetched and decoded in
    full just to return 40 000 characters of it.

    `ArtifactMetadata.size_bytes` is known before the fetch, so the check
    belongs there — no blob retrieved and no decode attempted.
    """

    def _manager_for(self, size_bytes: int):
        metadata = _make_metadata(
            artifact_id="big-1", name="huge.csv", mime_type="text/csv",
            size_bytes=size_bytes,
        )
        registry = MagicMock()
        registry.resolve = AsyncMock(return_value=metadata)
        registry.get_content = AsyncMock(return_value=b"x" * 10)
        return _make_manager(registry=registry)

    async def test_oversized_artifact_is_refused_without_fetching(self) -> None:
        from app.agents.actions.artifacts.artifacts import _MAX_ARTIFACT_FETCH_BYTES

        manager, registry = self._manager_for(_MAX_ARTIFACT_FETCH_BYTES + 1)
        success, payload = await manager.get_artifact_content(artifact_id="big-1")

        assert success is False
        registry.get_content.assert_not_awaited(), "the blob must never be read"
        body = json.loads(payload)
        assert "too large" in body["error"].lower()
        assert "get_artifact_download_url" in body["error"]

    async def test_normal_sized_artifact_is_unaffected(self) -> None:
        manager, registry = self._manager_for(10)
        success, _ = await manager.get_artifact_content(artifact_id="big-1")
        assert success is True
        registry.get_content.assert_awaited_once()

    async def test_unknown_size_still_reads(self) -> None:
        """Artifacts written before `size_bytes` was recorded report 0. A
        missing size is not evidence of a large file, and refusing on it
        would make old artifacts permanently unreadable."""
        manager, registry = self._manager_for(0)
        success, _ = await manager.get_artifact_content(artifact_id="big-1")
        assert success is True
        registry.get_content.assert_awaited_once()

    async def test_paging_a_large_but_allowed_file_still_works(self) -> None:
        """The fetch ceiling has to sit well above `max_chars`, or `offset`
        paging through a legitimately long program would be impossible."""
        from app.agents.actions.artifacts.artifacts import (
            _MAX_ARTIFACT_CONTENT_CHARS,
            _MAX_ARTIFACT_FETCH_BYTES,
        )

        assert _MAX_ARTIFACT_FETCH_BYTES > _MAX_ARTIFACT_CONTENT_CHARS * 4
