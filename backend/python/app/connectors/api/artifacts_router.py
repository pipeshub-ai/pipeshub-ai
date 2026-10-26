"""User-facing artifact gallery HTTP routes (connectors service)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from app.api.middlewares.auth import require_scopes
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import OAuthScopes
from app.modules.transformers.blob_storage import BlobStorage
from app.services.artifact_registry.access import AccessDeniedError, ArtifactNotFoundError
from app.services.artifact_registry.gallery import ArtifactGalleryService, ArtifactListQuery
from app.services.artifact_registry.models import Actor
from app.services.artifact_registry.registry import ArtifactRegistryService, ArtifactStorageError
from app.services.artifact_registry.sharing import (
    MAX_SHARE_TARGETS,
    ArtifactSharingService,
    InvalidShareTargetError,
)
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

logger = logging.getLogger(__name__)

artifacts_router = APIRouter(tags=["Artifacts"])


async def get_graph_provider(request: Request) -> IGraphDBProvider:
    return request.app.state.graph_provider


async def get_sharing_service(
    request: Request,
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
) -> ArtifactSharingService:
    container = getattr(request.app, "container", None)
    notifications = container.connector_notification_service() if container else None
    return ArtifactSharingService(graph_provider, notifications)


class ArtifactShareRequest(BaseModel):
    userIds: list[str] = Field(min_length=1, max_length=MAX_SHARE_TARGETS)


async def get_blob_store(request: Request) -> BlobStorage:
    return BlobStorage(logger, request.app.container.config_service(), request.app.state.graph_provider)


_STORAGE_UNAVAILABLE = "Couldn't delete the artifact's files. Try again."


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
    ownership: str = Query("all", description="all, mine or shared"),
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
        ownership=ownership,
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
        "counts": result.counts,
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


def _sharing_error(error: Exception) -> HTTPException:
    if isinstance(error, ArtifactNotFoundError):
        return HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="Artifact not found")
    if isinstance(error, AccessDeniedError):
        return HTTPException(
            status_code=HttpStatusCode.FORBIDDEN.value,
            detail="Only the artifact's owner can manage its sharing",
        )
    return HTTPException(status_code=HttpStatusCode.BAD_REQUEST.value, detail=str(error))


@artifacts_router.get(
    "/api/v1/artifacts/{artifact_id}/permissions",
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ, OAuthScopes.KB_READ))],
)
async def list_artifact_members(
    request: Request,
    artifact_id: str,
    sharing: ArtifactSharingService = Depends(get_sharing_service),
) -> dict:
    actor = _actor_from_request(request)
    try:
        members = await sharing.list_members(actor, artifact_id)
    except (ArtifactNotFoundError, AccessDeniedError) as e:
        raise _sharing_error(e) from None
    return {"members": members}


@artifacts_router.post(
    "/api/v1/artifacts/{artifact_id}/permissions",
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE, OAuthScopes.KB_WRITE))],
)
async def share_artifact(
    request: Request,
    artifact_id: str,
    body: ArtifactShareRequest,
    sharing: ArtifactSharingService = Depends(get_sharing_service),
) -> dict:
    actor = _actor_from_request(request)
    try:
        shared = await sharing.share(actor, artifact_id, body.userIds)
    except (ArtifactNotFoundError, AccessDeniedError, InvalidShareTargetError) as e:
        raise _sharing_error(e) from None
    return {"shared": shared}


@artifacts_router.delete(
    "/api/v1/artifacts/{artifact_id}/permissions",
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE, OAuthScopes.KB_WRITE))],
)
async def unshare_artifact(
    request: Request,
    artifact_id: str,
    body: ArtifactShareRequest,
    sharing: ArtifactSharingService = Depends(get_sharing_service),
) -> dict:
    actor = _actor_from_request(request)
    try:
        unshared = await sharing.unshare(actor, artifact_id, body.userIds)
    except (ArtifactNotFoundError, AccessDeniedError, InvalidShareTargetError) as e:
        raise _sharing_error(e) from None
    return {"unshared": unshared}


@artifacts_router.delete(
    "/api/v1/artifacts/conversations/{conversation_id}",
    # agent:write too: agent-chat delete takes it, and calls this first.
    dependencies=[Depends(require_scopes(OAuthScopes.CONVERSATION_WRITE, OAuthScopes.AGENT_WRITE))],
)
async def delete_conversation_artifacts(
    request: Request,
    conversation_id: str,
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
    blob_store: BlobStorage = Depends(get_blob_store),
) -> dict:
    """Called by the Node API before it deletes a conversation, with the
    caller's own token: only artifacts the caller owns are deleted. The
    conversation's storage folders are purged by the Node API, which knows who
    owns the conversation; this service does not."""
    actor = _actor_from_request(request)
    registry = ArtifactRegistryService(graph_provider, blob_store)
    try:
        result = await registry.delete_for_conversation(actor=actor, conversation_id=conversation_id)
    except ArtifactNotFoundError:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="User not found") from None
    except ArtifactStorageError as e:
        raise HTTPException(status_code=HttpStatusCode.SERVICE_UNAVAILABLE.value, detail=_STORAGE_UNAVAILABLE) from e
    return {"deleted": result.deleted, "kept": result.kept}


@artifacts_router.delete(
    "/api/v1/artifacts/{artifact_id}",
    status_code=HttpStatusCode.NO_CONTENT.value,
    dependencies=[Depends(require_scopes(OAuthScopes.CONVERSATION_WRITE))],
)
async def delete_artifact(
    request: Request,
    artifact_id: str,
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
    blob_store: BlobStorage = Depends(get_blob_store),
) -> Response:
    actor = _actor_from_request(request)
    try:
        # Only what the gallery shows can be deleted here; hidden artifacts go with their chat.
        await ArtifactGalleryService(graph_provider).get(actor, artifact_id)
        await ArtifactRegistryService(graph_provider, blob_store).delete(actor=actor, artifact_id=artifact_id)
    except ArtifactNotFoundError:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="Artifact not found") from None
    except AccessDeniedError:
        raise HTTPException(
            status_code=HttpStatusCode.FORBIDDEN.value, detail="Only the artifact's owner can delete it",
        ) from None
    except ArtifactStorageError as e:
        raise HTTPException(status_code=HttpStatusCode.SERVICE_UNAVAILABLE.value, detail=_STORAGE_UNAVAILABLE) from e
    return Response(status_code=HttpStatusCode.NO_CONTENT.value)


MAX_COPY_ARTIFACTS = 50


class ArtifactCopyRef(BaseModel):
    artifactId: str = Field(min_length=1)
    upToVersion: int = Field(ge=1)


class ArtifactCopyRequest(BaseModel):
    conversationId: str = Field(min_length=1)
    artifacts: list[ArtifactCopyRef] = Field(min_length=1, max_length=MAX_COPY_ARTIFACTS)


@artifacts_router.post(
    "/api/v1/artifacts/copy",
    # The scopes of the two fork routes that call this: a client allowed to fork
    # must be allowed to copy what the fork carries over.
    dependencies=[Depends(require_scopes(OAuthScopes.CONVERSATION_CHAT, OAuthScopes.AGENT_EXECUTE))],
)
async def copy_artifacts_to_conversation(
    request: Request,
    body: ArtifactCopyRequest,
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
) -> dict:
    """Give a forked chat its own copy of every artifact it carried over.

    Only artifacts the caller can already read are copied, and each copy is
    owned by the caller and filed under `conversationId`, so deleting the
    fork deletes its copies and deleting the original does not. A ref that
    cannot be copied is reported in `failed` instead of failing the batch, so
    the caller decides whether that matters."""
    actor = _actor_from_request(request)
    container = getattr(request.app, "container", None)
    if container is None:
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail="Artifact storage is not available",
        )
    registry = ArtifactRegistryService(
        graph_provider,
        BlobStorage(logger, container.config_service(), graph_provider=graph_provider),
    )

    copies: list[dict] = []
    failed: list[dict] = []
    for ref in body.artifacts:
        try:
            copied = await registry.copy_to_conversation(
                actor=actor,
                artifact_id=ref.artifactId,
                up_to_version=ref.upToVersion,
                conversation_id=body.conversationId,
            )
        except ArtifactNotFoundError:
            failed.append({"artifactId": ref.artifactId, "reason": "not_found"})
            continue
        except AccessDeniedError:
            failed.append({"artifactId": ref.artifactId, "reason": "forbidden"})
            continue
        except Exception:
            logger.exception("Could not copy artifact %s into conversation %s", ref.artifactId, body.conversationId)
            failed.append({"artifactId": ref.artifactId, "reason": "error"})
            continue
        copies.append(
            {
                "sourceArtifactId": copied.source_artifact_id,
                "sourceDocumentId": copied.source_document_id,
                "artifactId": copied.artifact_id,
                "documentId": copied.document_id,
                "versions": [{"from": src, "to": dst} for src, dst in sorted(copied.versions.items())],
            }
        )
    return {"copies": copies, "failed": failed}
