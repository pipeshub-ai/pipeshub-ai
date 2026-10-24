"""The nodes routes serialize the page themselves (`_serialized`) instead of
handing the model to FastAPI. The body a client receives must not change."""

import json
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.connectors.sources.localKB.api.knowledge_hub_models import (
    AppliedFilters,
    AvailableFilters,
    BreadcrumbItem,
    CountItem,
    CountsInfo,
    CurrentNode,
    DateRangeFilter,
    FilterOption,
    FiltersInfo,
    ItemPermission,
    KnowledgeHubNodesResponse,
    NodeItem,
    NodeType,
    OriginType,
    PaginationInfo,
    ParentRef,
    PermissionsInfo,
)
from app.connectors.sources.localKB.api.knowledge_hub_router import _serialized


def _page() -> KnowledgeHubNodesResponse:
    return KnowledgeHubNodesResponse(
        success=True,
        id="app-1",
        currentNode=CurrentNode(id="app-1", name="Drive — équipe", nodeType="app", subType="DRIVE"),
        parentNode=None,
        items=[
            NodeItem(
                id="r-1", name='Résumé "final" \\ 日本語  .pdf', nodeType=NodeType.RECORD, parentId="g-1",
                parent=ParentRef(id="g-1", nodeType="recordGroup", name=None),
                origin=OriginType.CONNECTOR, connector="DRIVE", connectorId="app-1", recordType="FILE",
                indexingStatus="COMPLETED", createdAt=1759500000000, updatedAt=1759500000001,
                sizeInBytes=2**40, mimeType="application/pdf", extension="pdf", webUrl="https://x/y?a=1&b=<2>",
                hasChildren=False, previewRenderable=True,
                permission=ItemPermission(role="READER", canEdit=False, canDelete=False),
            ),
            NodeItem(
                id="g-2", name="", nodeType=NodeType.RECORD_GROUP, origin=OriginType.COLLECTION,
                createdAt=0, updatedAt=0, hasChildren=True, isInternal=True, isPlaceholder=True,
            ),
        ],
        pagination=PaginationInfo(limit=50, totalItems=2, hasNext=False, hasPrev=False, nextCursor="abc=="),
        filters=FiltersInfo(
            applied=AppliedFilters(q="é", nodeTypes=["record"], createdAt=DateRangeFilter(gte=1)),
            available=AvailableFilters(nodeTypes=[FilterOption(id="record", label="Record")]),
        ),
        breadcrumbs=[BreadcrumbItem(id="app-1", name="Drive", nodeType="app")],
        counts=CountsInfo(items=[CountItem(label="files", count=1)], total=2),
        permissions=PermissionsInfo(
            role="READER", canUpload=False, canCreateFolders=False, canEdit=False, canDelete=False,
            canManagePermissions=False,
        ),
        typed_records={
            "r-1": {"nested": [1, "two", None, True, {"k": "v"}], "at": datetime(2026, 10, 4, tzinfo=timezone.utc)},
        },
    )


def _client() -> TestClient:
    app = FastAPI()

    @app.get("/model", response_model=KnowledgeHubNodesResponse)
    async def model() -> KnowledgeHubNodesResponse:
        return _page()

    @app.get("/serialized", response_model=KnowledgeHubNodesResponse)
    async def serialized() -> KnowledgeHubNodesResponse:
        return _serialized(_page())

    @app.get("/passthrough")
    async def passthrough() -> dict:
        return _serialized({"success": False})

    return TestClient(app)


def test_the_body_is_the_one_fastapi_would_have_written() -> None:
    client = _client()
    by_fastapi, by_us = client.get("/model"), client.get("/serialized")
    assert by_us.status_code == by_fastapi.status_code == 200
    assert by_us.headers["content-type"] == by_fastapi.headers["content-type"] == "application/json"
    assert by_us.content == by_fastapi.content
    assert json.loads(by_us.content)["items"][0]["name"] == 'Résumé "final" \\ 日本語  .pdf'


def test_anything_else_is_left_to_fastapi() -> None:
    assert _client().get("/passthrough").json() == {"success": False}
