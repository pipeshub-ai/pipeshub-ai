"""User-facing artifact gallery HTTP routes (connectors service)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.middlewares.auth import require_scopes
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import OAuthScopes
from app.services.artifact_registry.access import ArtifactNotFoundError
from app.services.artifact_registry.gallery import ArtifactGalleryService, ArtifactListQuery
from app.services.artifact_registry.models import Actor
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

logger = logging.getLogger(__name__)

artifacts_router = APIRouter(tags=["Artifacts"])


async def get_graph_provider(request: Request) -> IGraphDBProvider:
    return request.app.state.graph_provider


def _parse_comma_separated_str(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def _actor_from_request(request: Request) -> Actor:
    user = getattr(request.state, "user", None) or {}
    user_id = user.get("userId")
    org_id = user.get("orgId")
    if not user_id or not org_id:
        raise HTTPException(
            status_code=HttpStatusCode.UNAUTHORIZED.value,
            detail="Authentication required",
        )
    return Actor(org_id=org_id, user_id=user_id)


@artifacts_router.get(
    "/api/v1/artifacts",
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ, OAuthScopes.KB_READ))],
)
async def list_artifacts_gallery(
    request: Request,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    search: str | None = None,
    artifact_types: str | None = Query(None, description="Comma-separated artifact types"),
    conversation_id: str | None = None,
    date_from: int | None = None,
    date_to: int | None = None,
    sort_by: str = "createdAtTimestamp",
    sort_order: str = "desc",
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
) -> dict:
    actor = _actor_from_request(request)
    gallery = ArtifactGalleryService(graph_provider)
    query = ArtifactListQuery(
        search=search,
        page=page,
        limit=limit,
        artifact_types=_parse_comma_separated_str(artifact_types),
        conversation_id=conversation_id,
        date_from=date_from,
        date_to=date_to,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    try:
        result = await gallery.list(actor, query)
    except ArtifactNotFoundError as e:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail=str(e)) from e
    return {
        "items": [item.model_dump(by_alias=True) for item in result.items],
        "pagination": {
            "page": result.page,
            "limit": result.limit,
            "totalCount": result.total_count,
            "totalPages": result.total_pages,
        },
    }


@artifacts_router.get(
    "/api/v1/artifacts/{artifact_id}",
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ, OAuthScopes.KB_READ))],
)
async def get_artifact_gallery(
    request: Request,
    artifact_id: str,
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
) -> dict:
    actor = _actor_from_request(request)
    gallery = ArtifactGalleryService(graph_provider)
    try:
        detail = await gallery.get(actor, artifact_id)
    except ArtifactNotFoundError:
        raise HTTPException(
            status_code=HttpStatusCode.NOT_FOUND.value,
            detail="Artifact not found",
        ) from None
    return detail.model_dump(by_alias=True)


@artifacts_router.get(
    "/api/v1/artifacts/{artifact_id}/versions",
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ, OAuthScopes.KB_READ))],
)
async def list_artifact_versions(
    request: Request,
    artifact_id: str,
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
) -> dict:
    actor = _actor_from_request(request)
    gallery = ArtifactGalleryService(graph_provider)
    try:
        versions = await gallery.list_versions(actor, artifact_id)
    except ArtifactNotFoundError:
        raise HTTPException(
            status_code=HttpStatusCode.NOT_FOUND.value,
            detail="Artifact not found",
        ) from None
    return {"versions": [v.model_dump(by_alias=True) for v in versions]}
