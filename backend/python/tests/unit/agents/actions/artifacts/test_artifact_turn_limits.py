"""The artifact tools on a limited turn reach only this conversation's artifacts.

A saved agent's turn is always limited, and a service-account agent reads as its
creator: by id, `get_artifact_content`, `get_artifact_download_url`,
`update_artifact`, `promote_artifact`, the image edit and `run_code`'s
`input_artifacts` used to reach any artifact the creator owns, from any of their
conversations. On a turn limited by nothing (the user's own assistant with no
picks) every artifact the user may read stays usable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.artifacts.artifacts import ArtifactManager
from app.agents.actions.knowledge_graph.ops.scope import ARTIFACT_OF_ANOTHER_CONVERSATION
from app.models.entities import ArtifactType, LifecycleStatus
from app.services.artifact_registry import ArtifactMetadata, ArtifactVersion

LIMITED = {"apps": [], "kb": ["NO_KB_SELECTED"], "allowedApps": ["kb-1"],
           "allowedRecordGroups": [], "allowedRecords": []}
UNLIMITED = {"apps": [], "kb": []}


def _metadata(conversation_id: str) -> ArtifactMetadata:
    return ArtifactMetadata(
        artifact_id="art-1", org_id="org-1", conversation_id=conversation_id, name="notes.md",
        logical_name="notes.md", artifact_type=ArtifactType.OTHER, mime_type="text/markdown",
        lifecycle_status=LifecycleStatus.PUBLISHED, version=1, size_bytes=5, document_id="doc-1",
    )


def _manager(produced_in: str, filters: dict) -> tuple[ArtifactManager, MagicMock]:
    metadata = _metadata(produced_in)
    registry = MagicMock()
    registry.resolve = AsyncMock(return_value=metadata)
    registry.get_content = AsyncMock(return_value=b"hello")
    registry.get_download_url = AsyncMock(return_value="https://blob.example/notes.md")
    registry.add_version = AsyncMock(return_value=(
        ArtifactVersion(version=2, size_bytes=5, content_hash="h", mime_type="text/markdown", created_at=1),
        metadata,
    ))
    registry.promote_to_visible = AsyncMock(return_value=metadata)
    manager = ArtifactManager({
        "org_id": "org-1", "user_id": "creator", "conversation_id": "conv-here",
        "graph_provider": MagicMock(), "blob_store": MagicMock(), "filters": filters,
    })
    manager._registry = lambda: registry
    manager._schedule_marker = MagicMock()
    manager._emit_promotion_sse = AsyncMock()
    return manager, registry


CALLS = {
    "get_artifact_content": lambda m: m.get_artifact_content(artifact_id="art-1"),
    "get_artifact_download_url": lambda m: m.get_artifact_download_url(artifact_id="art-1"),
    "update_artifact": lambda m: m.update_artifact(artifact_id="art-1", content="new"),
    "promote_artifact": lambda m: m.promote_artifact(artifact_id="art-1"),
}
REACHES = {
    "get_artifact_content": "get_content",
    "get_artifact_download_url": "get_download_url",
    "update_artifact": "add_version",
    "promote_artifact": "promote_to_visible",
}


@pytest.mark.parametrize("tool", sorted(CALLS))
class TestTheArtifactTools:
    @pytest.mark.asyncio
    async def test_an_artifact_of_another_conversation_is_refused_on_a_limited_turn(self, tool) -> None:
        manager, registry = _manager("conv-old", LIMITED)

        success, payload = await CALLS[tool](manager)

        assert success is False
        assert json.loads(payload)["error"] == ARTIFACT_OF_ANOTHER_CONVERSATION
        getattr(registry, REACHES[tool]).assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_artifact_of_this_conversation_is_used_on_a_limited_turn(self, tool) -> None:
        manager, registry = _manager("conv-here", LIMITED)

        success, _ = await CALLS[tool](manager)

        assert success is True
        getattr(registry, REACHES[tool]).assert_awaited_once()

    @pytest.mark.asyncio
    async def test_any_readable_artifact_is_used_on_a_turn_limited_by_nothing(self, tool) -> None:
        manager, _ = _manager("conv-old", UNLIMITED)

        success, _ = await CALLS[tool](manager)

        assert success is True


class TestTheImageEdit:
    @staticmethod
    async def _fetch(produced_in: str, filters: dict):
        from app.agents.actions.image_generator import image_generator as mod

        registry = MagicMock()
        registry.resolve = AsyncMock(return_value=_metadata(produced_in))
        registry.get_content = AsyncMock(return_value=b"png")
        tool = mod.ImageGenerator({"conversation_id": "conv-here", "filters": filters})
        with patch("app.services.artifact_registry.ArtifactRegistryService", return_value=registry):
            result = await tool._fetch_edit_source_image(
                record_id="art-1", graph_provider=MagicMock(), blob_store=MagicMock(),
                org_id="org-1", user_id="creator",
            )
        return result, registry

    @pytest.mark.asyncio
    async def test_an_image_of_another_conversation_is_refused_on_a_limited_turn(self) -> None:
        (image, _, error), registry = await self._fetch("conv-old", LIMITED)
        assert image is None
        assert json.loads(error[1])["error"] == ARTIFACT_OF_ANOTHER_CONVERSATION
        registry.get_content.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_image_of_this_conversation_is_edited(self) -> None:
        (image, _, error), _ = await self._fetch("conv-here", LIMITED)
        assert image == b"png" and error is None


@dataclass
class _Context:
    tool_state: dict[str, Any]
    org_id: str = "org-1"
    user_id: str = "creator"
    conversation_id: str = "conv-here"


class TestRunCodeInputArtifacts:
    @pytest.mark.asyncio
    async def test_an_artifact_of_another_conversation_is_not_staged_and_says_why(self) -> None:
        from app.agents.agent_loop.sandbox_bridge import _resolve_input_artifacts

        registry = MagicMock()
        registry.resolve = AsyncMock(return_value=_metadata("conv-old"))
        registry.get_content = AsyncMock(return_value=b"data")
        context = _Context(tool_state={"conversation_id": "conv-here", "filters": LIMITED})

        files, resolved, missing = await _resolve_input_artifacts(context, registry, ["art-1"])

        assert files == {} and resolved == []
        assert missing == [f"art-1 ({ARTIFACT_OF_ANOTHER_CONVERSATION})"]
        registry.get_content.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_artifact_of_this_conversation_is_staged(self) -> None:
        from app.agents.agent_loop.sandbox_bridge import _resolve_input_artifacts

        registry = MagicMock()
        registry.resolve = AsyncMock(return_value=_metadata("conv-here"))
        registry.get_content = AsyncMock(return_value=b"data")
        context = _Context(tool_state={"conversation_id": "conv-here", "filters": LIMITED})

        files, _, missing = await _resolve_input_artifacts(context, registry, ["art-1"])

        assert files == {"input/artifacts/notes.md": b"data"} and missing == []
