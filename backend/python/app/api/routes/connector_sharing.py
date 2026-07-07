"""Connector sharing API routes.

POST   /api/v1/connectors/{connector_id}/shares  — grant READER access (owner only)
GET    /api/v1/connectors/{connector_id}/shares  — list current shares
DELETE /api/v1/connectors/{connector_id}/shares  — revoke grants (owner) or self-leave (recipient)

v1: user sharing only. Team sharing is reserved for a future release.
"""
from typing import Any

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError, model_validator

from app.api.middlewares.auth import require_scopes
from app.config.constants.arangodb import CollectionNames
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import OAuthScopes
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

sharing_router = APIRouter()


def _get_graph_provider(request: Request) -> IGraphDBProvider:
    return request.app.state.graph_provider


class ShareConnectorBody(BaseModel):
    userIds: list[str] | None = Field(default=None)

    @model_validator(mode="after")
    def normalize_and_validate(self) -> "ShareConnectorBody":
        self.userIds = [uid for uid in (self.userIds or []) if uid]
        if not self.userIds:
            raise ValueError("At least one userId is required")
        return self


class RevokeShareBody(BaseModel):
    userIds: list[str] | None = Field(default=None)

    @model_validator(mode="after")
    def normalize_and_validate(self) -> "RevokeShareBody":
        self.userIds = [uid for uid in (self.userIds or []) if uid]
        if not self.userIds:
            raise ValueError("At least one userId is required")
        return self


async def _resolve_caller(request: Request) -> tuple[str, str]:
    """Extract and validate caller info from the request state."""
    user_id: str | None = request.state.user.get("userId")
    org_id: str | None = request.state.user.get("orgId")
    if not user_id or not org_id:
        raise HTTPException(
            status_code=HttpStatusCode.UNAUTHORIZED.value,
            detail="User not authenticated",
        )
    return user_id, org_id


async def _resolve_caller_ids(
    request: Request,
    graph_provider: IGraphDBProvider,
) -> tuple[str, str, set[str]]:
    """Return (jwt_user_id, org_id, all_ids) where all_ids contains every
    identifier that refers to the caller: the JWT userId (MongoDB _id) and,
    when resolvable, the graph _key used by the frontend.

    The frontend always sends graph _key values (from listGraphUsers /
    getSharedMembers). The JWT carries the MongoDB _id.  Accepting both
    prevents false-positive 403s on self-leave operations.
    """
    user_id, org_id = await _resolve_caller(request)
    all_ids: set[str] = {user_id}
    try:
        user_doc = await graph_provider.get_user_by_user_id(user_id)
        if user_doc:
            graph_key = user_doc.get("_key") or user_doc.get("id")
            if graph_key:
                all_ids.add(graph_key)
    except Exception:
        pass
    return user_id, org_id, all_ids


async def _assert_same_org(
    connector_id: str,
    org_id: str,
    graph_provider: IGraphDBProvider,
) -> None:
    """Raise 404 (not 403) if the connector belongs to a different org, so a
    cross-tenant connector id is never resolvable through this API.

    App documents do not store an orgId property (verified: not written by
    connector creation, and the enforced app_schema forbids additionalProperties).
    Org membership is instead resolved via the creator's user record — the same
    createdBy -> user -> orgId pattern get_app_creator_user already uses
    elsewhere (e.g. slack/individual/connector.py, github/connector.py). A
    creator that can't be resolved fails closed (treated as a different org).
    """
    creator = await graph_provider.get_app_creator_user(connector_id)
    if creator is None or str(creator.org_id) != str(org_id):
        raise HTTPException(
            status_code=HttpStatusCode.NOT_FOUND.value,
            detail=f"Connector {connector_id} not found",
        )


async def _assert_owner(
    connector_id: str,
    user_id: str,
    org_id: str,
    graph_provider: IGraphDBProvider,
) -> dict[str, Any]:
    """Fetch the connector and assert the caller is the owner."""
    connector = await graph_provider.get_document(connector_id, CollectionNames.APPS.value)
    if not connector:
        raise HTTPException(
            status_code=HttpStatusCode.NOT_FOUND.value,
            detail=f"Connector {connector_id} not found",
        )
    await _assert_same_org(connector_id, org_id, graph_provider)
    created_by = connector.get("createdBy")
    if created_by != user_id:
        raise HTTPException(
            status_code=HttpStatusCode.FORBIDDEN.value,
            detail="Only the connector owner can manage shares",
        )
    if connector.get("scope") != "personal":
        raise HTTPException(
            status_code=HttpStatusCode.BAD_REQUEST.value,
            detail="Only personal connectors can be shared",
        )
    return connector


async def _parse_body(request: Request, model_cls: type) -> Any:
    """Read and validate the request body manually.

    FastAPI's automatic body injection can silently fail when Starlette
    http-middleware wraps the ASGI transport.  Reading via request.body()
    uses Starlette's internal cache so the bytes are available regardless
    of how many times they have been read before.
    """
    try:
        raw = await request.body()
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=HttpStatusCode.BAD_REQUEST.value,
            detail=f"Invalid JSON body: {exc}",
        )
    try:
        return model_cls.model_validate(data)
    except ValidationError as exc:
        raise HTTPException(
            status_code=HttpStatusCode.BAD_REQUEST.value,
            detail=exc.errors()[0]["msg"] if exc.errors() else "Invalid request body",
        )


@sharing_router.post(
    "/api/v1/connectors/{connector_id}/shares",
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))],
)
async def share_connector(
    connector_id: str,
    request: Request,
    graph_provider: IGraphDBProvider = Depends(_get_graph_provider),
) -> dict:
    """Grant READER access on a personal connector to users (owner only)."""
    body: ShareConnectorBody = await _parse_body(request, ShareConnectorBody)
    user_id, org_id = await _resolve_caller(request)

    await _assert_owner(connector_id, user_id, org_id, graph_provider)

    # Ensure the ConnectorGroup exists (created lazily on first share).
    group_key = await graph_provider.get_or_create_connector_user_group(
        connector_id=connector_id,
        owner_user_key=user_id,
        org_id=org_id,
    )
    if not group_key:
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail="Failed to resolve ConnectorGroup for this connector",
        )

    result = await graph_provider.create_connector_share_permissions(
        connector_id=connector_id,
        requester_user_id=user_id,
        user_ids=body.userIds,
        team_ids=[],  # team sharing reserved for v2
        org_id=org_id,
    )

    if not result.get("success"):
        raise HTTPException(
            status_code=result.get("code", 500),
            detail=result.get("reason", "Failed to create share permissions"),
        )

    return result


@sharing_router.get(
    "/api/v1/connectors/{connector_id}/shares",
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))],
)
async def list_connector_shares(
    connector_id: str,
    request: Request,
    graph_provider: IGraphDBProvider = Depends(_get_graph_provider),
) -> dict:
    """List all shares for a connector (owner or an existing recipient only)."""
    user_id, org_id = await _resolve_caller(request)

    connector = await graph_provider.get_document(connector_id, CollectionNames.APPS.value)
    if not connector:
        raise HTTPException(
            status_code=HttpStatusCode.NOT_FOUND.value,
            detail=f"Connector {connector_id} not found",
        )

    await _assert_same_org(connector_id, org_id, graph_provider)

    is_owner = connector.get("createdBy") == user_id
    if not is_owner:
        has_access = await graph_provider.has_connector_share_access(connector_id, user_id)
        if not has_access:
            raise HTTPException(
                status_code=HttpStatusCode.FORBIDDEN.value,
                detail="You do not have access to this connector's shares",
            )

    shares = await graph_provider.list_connector_share_permissions(
        connector_id=connector_id,
        requester_user_id=user_id,
    )

    return {"shares": shares, "connectorId": connector_id}


@sharing_router.delete(
    "/api/v1/connectors/{connector_id}/shares",
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))],
)
async def revoke_connector_shares(
    connector_id: str,
    request: Request,
    graph_provider: IGraphDBProvider = Depends(_get_graph_provider),
) -> dict:
    """Revoke share grants from a personal connector.

    Owner can remove any user.
    Non-owner can only remove themselves (self-leave).
    """
    body: RevokeShareBody = await _parse_body(request, RevokeShareBody)
    # Resolve all identifiers for the caller: JWT userId (MongoDB _id) + graph _key.
    # The frontend sends graph _key values while the JWT carries MongoDB _id.
    user_id, org_id, caller_ids = await _resolve_caller_ids(request, graph_provider)

    connector = await graph_provider.get_document(connector_id, CollectionNames.APPS.value)
    if not connector:
        raise HTTPException(
            status_code=HttpStatusCode.NOT_FOUND.value,
            detail=f"Connector {connector_id} not found",
        )

    await _assert_same_org(connector_id, org_id, graph_provider)
    if connector.get("scope") != "personal":
        raise HTTPException(
            status_code=HttpStatusCode.BAD_REQUEST.value,
            detail="Only personal connectors can be shared",
        )

    is_owner = connector.get("createdBy") == user_id

    # Non-owners can only self-leave: every submitted userId must resolve to the caller.
    if not is_owner:
        if not body.userIds or not all(uid in caller_ids for uid in body.userIds):
            raise HTTPException(
                status_code=HttpStatusCode.FORBIDDEN.value,
                detail="Only the connector owner can revoke shares for others",
            )

    removed = await graph_provider.remove_connector_share_permissions(
        connector_id=connector_id,
        requester_user_id=user_id,
        user_ids=body.userIds,
        team_ids=[],  # team sharing reserved for v2
        is_owner=is_owner,
    )

    return {"removedCount": removed, "connectorId": connector_id}
