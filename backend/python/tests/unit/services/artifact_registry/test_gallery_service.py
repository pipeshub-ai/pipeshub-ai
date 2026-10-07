"""Unit tests for ArtifactGalleryService."""

from unittest.mock import AsyncMock

import pytest

from app.services.artifact_registry.access import ArtifactNotFoundError
from app.services.artifact_registry.gallery import (
    ArtifactGalleryService,
    ArtifactListQuery,
)
from app.services.artifact_registry.models import Actor


def _service(graph=None) -> ArtifactGalleryService:
    return ArtifactGalleryService(graph or AsyncMock())


def _actor() -> Actor:
    return Actor(org_id="org-1", user_id="auth-user-1")


def _row(**overrides):
    art_doc = {
        "name": "chart.png",
        "artifactType": "CHART",
        "conversationId": "conv-1",
        "visibility": "VISIBLE",
        "mimeType": "image/png",
        "sizeInBytes": 12,
        "logicalName": "chart.png",
        "contentHash": "abc",
        "versions": [
            {
                "registryVersion": 1,
                "storageVersion": 0,
                "contentHash": "aaa",
                "sizeBytes": 10,
                "createdAt": 50,
            },
            {
                "registryVersion": 2,
                "storageVersion": 1,
                "contentHash": "abc",
                "sizeBytes": 12,
                "createdAt": 200,
            },
        ],
        "description": "A chart",
        "sourceTool": "coding_sandbox",
        "isTemporary": False,
    }
    row = {
        "id": "art-1",
        "recordName": "chart.png",
        "recordType": "ARTIFACT",
        "mimeType": "image/png",
        "sizeInBytes": 12,
        "version": 2,
        "createdAtTimestamp": 100,
        "updatedAtTimestamp": 200,
        "artifactDoc": art_doc,
        "permission": {"role": "OWNER", "type": "USER"},
    }
    row.update(overrides)
    return row


@pytest.fixture
def graph():
    g = AsyncMock()
    g.get_user_by_user_id = AsyncMock(return_value={"_key": "user-key-1", "id": "user-key-1"})
    return g


class TestGalleryList:
    @pytest.mark.asyncio
    async def test_maps_rows_and_pagination(self, graph):
        graph.list_accessible_artifacts = AsyncMock(return_value=([_row()], 51))
        page = await _service(graph).list(_actor(), ArtifactListQuery(page=2, limit=50))
        assert page.page == 2
        assert page.limit == 50
        assert page.total_count == 51
        assert page.total_pages == 2
        assert page.items[0].artifact_id == "art-1"
        assert page.items[0].artifact_type == "CHART"
        graph.list_accessible_artifacts.assert_awaited_once()
        kwargs = graph.list_accessible_artifacts.await_args.kwargs
        assert kwargs["user_id"] == "user-key-1"
        assert kwargs["skip"] == 50

    @pytest.mark.asyncio
    async def test_empty(self, graph):
        graph.list_accessible_artifacts = AsyncMock(return_value=([], 0))
        page = await _service(graph).list(_actor(), ArtifactListQuery())
        assert page.items == []
        assert page.total_pages == 0

    @pytest.mark.asyncio
    async def test_user_not_found(self, graph):
        graph.get_user_by_user_id = AsyncMock(return_value=None)
        with pytest.raises(ArtifactNotFoundError):
            await _service(graph).list(_actor(), ArtifactListQuery())

    @pytest.mark.asyncio
    async def test_strips_tool_result_from_type_filter(self, graph):
        graph.list_accessible_artifacts = AsyncMock(return_value=([], 0))
        await _service(graph).list(
            _actor(),
            ArtifactListQuery(artifact_types=["IMAGE", "TOOL_RESULT"]),
        )
        kwargs = graph.list_accessible_artifacts.await_args.kwargs
        assert kwargs["artifact_types"] == ["IMAGE"]


class TestGalleryGet:
    @pytest.mark.asyncio
    async def test_happy_path(self, graph):
        graph.get_artifact_detail = AsyncMock(return_value=_row())
        detail = await _service(graph).get(_actor(), "art-1")
        assert detail.artifact_id == "art-1"
        assert len(detail.versions) == 2
        assert detail.source_tool == "coding_sandbox"
        graph.get_artifact_detail.assert_awaited_once_with(
            "user-key-1", "org-1", "art-1"
        )

    @pytest.mark.asyncio
    async def test_missing_raises(self, graph):
        graph.get_artifact_detail = AsyncMock(return_value=None)
        with pytest.raises(ArtifactNotFoundError):
            await _service(graph).get(_actor(), "missing")

    @pytest.mark.asyncio
    async def test_staging_raises(self, graph):
        row = _row()
        row["artifactDoc"]["visibility"] = "STAGING"
        graph.get_artifact_detail = AsyncMock(return_value=row)
        with pytest.raises(ArtifactNotFoundError):
            await _service(graph).get(_actor(), "art-1")

    @pytest.mark.asyncio
    async def test_tool_result_raises(self, graph):
        row = _row()
        row["artifactDoc"]["artifactType"] = "TOOL_RESULT"
        graph.get_artifact_detail = AsyncMock(return_value=row)
        with pytest.raises(ArtifactNotFoundError):
            await _service(graph).get(_actor(), "art-1")

    @pytest.mark.asyncio
    async def test_list_versions_delegates(self, graph):
        graph.get_artifact_detail = AsyncMock(return_value=_row())
        versions = await _service(graph).list_versions(_actor(), "art-1")
        assert [v.version for v in versions] == [1, 2]
        graph.get_artifact_detail.assert_awaited_once_with(
            "user-key-1", "org-1", "art-1"
        )
