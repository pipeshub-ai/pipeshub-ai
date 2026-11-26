"""Knowledge publications (P2.13, D21): an owner discloses a knowledge base or record group to an audience.

Node's ``/api/v1/agent-harness/publications`` forwards the caller's own Authorization header here. The org, the
caller and their admin role come from the credential (and Node's live role), never the body: a body naming
``orgId`` or ``createdBy`` is refused. Only an owner of the container or an org admin may publish, list or revoke.
"""

from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.middlewares.auth import require_scopes
from app.api.middlewares.caller_role import fetch_caller_role
from app.config.constants.service import OAuthScopes
from app.harness.knowledge.directory import PublicationStoreError
from app.harness.knowledge.graph import doc, text
from app.harness.knowledge.service import (
    Caller,
    ContainerKind,
    KnowledgePublications,
    container_id,
)
from harness_core.knowledge.errors import PublicationRefused
from harness_core.knowledge.publications import (
    MAX_AUDIENCE_ENTRIES,
    Audience,
    Publication,
    PublicationDraft,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_Id = Annotated[str, Field(min_length=1, max_length=256)]


class AudienceBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    user_ids: list[_Id] = Field(default_factory=list, alias="userIds", max_length=MAX_AUDIENCE_ENTRIES)
    team_ids: list[_Id] = Field(default_factory=list, alias="teamIds", max_length=MAX_AUDIENCE_ENTRIES)
    org: bool = False


class CreatePublicationBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    container_kind: ContainerKind = Field(alias="containerKind")
    container_id: _Id = Field(alias="containerId")
    audience: AudienceBody
    mode: Literal["answer_only", "full"]
    expires_at: int | None = Field(default=None, alias="expiresAt", gt=0)


class PublicationView(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    container_kind: ContainerKind = Field(alias="containerKind")
    container_id: str = Field(alias="containerId")
    audience: AudienceBody
    mode: Literal["answer_only", "full"]
    created_by: str = Field(alias="createdBy")
    created_at: int = Field(alias="createdAt")
    expires_at: int | None = Field(alias="expiresAt")
    revoked_at: int | None = Field(alias="revokedAt")
    revoked_by: str | None = Field(alias="revokedBy")


class PublicationList(BaseModel):
    publications: list[PublicationView]


_KINDS: dict[str, ContainerKind] = {"apps": "kb", "recordGroups": "recordGroup"}


def view(publication: Publication) -> PublicationView:
    collection, _, key = publication.container_id.partition("/")
    return PublicationView(
        id=publication.id,
        containerKind=_KINDS[collection],
        containerId=key,
        audience=AudienceBody(
            userIds=sorted(publication.audience.user_ids),
            teamIds=sorted(publication.audience.group_ids),
            org=publication.audience.org,
        ),
        mode=publication.mode,
        createdBy=publication.created_by,
        createdAt=publication.created_at_ms,
        expiresAt=publication.expires_at_ms,
        revokedAt=publication.revoked_at_ms,
        revokedBy=publication.revoked_by,
    )


async def _context(request: Request) -> tuple[KnowledgePublications, Caller]:
    user = doc(getattr(request.state, "user", None)) or {}
    user_id, org_id = text(user, "userId"), text(user, "orgId")
    if user_id is None or org_id is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    container = request.app.container
    config_service = container.config_service()
    graph_provider = await container.graph_provider()
    role = await fetch_caller_role(request, config_service)
    return KnowledgePublications(graph_provider, config_service), Caller(user_id, org_id, is_admin=role.is_admin)


def _http(error: Exception) -> HTTPException:
    if isinstance(error, PublicationRefused):
        logger.warning("publications.refused reason=%s", error.reason_code)
        return HTTPException(status_code=error.http_status, detail={"code": error.reason_code, "message": str(error)})
    logger.warning("publications.store_failed errorType=%s", type(error).__name__)
    return HTTPException(status_code=503, detail={"code": "publications_unavailable", "message": "Try again shortly"})


@router.post(
    "/knowledge/publications",
    status_code=201,
    dependencies=[Depends(require_scopes(OAuthScopes.KB_WRITE))],
)
async def create_publication(request: Request, body: CreatePublicationBody) -> PublicationView:
    publications, caller = await _context(request)
    draft = PublicationDraft(
        org_id=caller.org_id,
        container_id=container_id(body.container_kind, body.container_id),
        audience=Audience(
            user_ids=frozenset(body.audience.user_ids),
            group_ids=frozenset(body.audience.team_ids),
            org=body.audience.org,
        ),
        mode=body.mode,
        created_by=caller.user_id,
        expires_at_ms=body.expires_at,
    )
    try:
        return view(await publications.create(caller, draft))
    except (PublicationRefused, PublicationStoreError) as error:
        raise _http(error) from error


@router.get(
    "/knowledge/publications",
    dependencies=[Depends(require_scopes(OAuthScopes.KB_READ))],
)
async def list_publications(
    request: Request,
    container_kind: Annotated[ContainerKind, Query(alias="containerKind")],
    container_key: Annotated[str, Query(alias="containerId", min_length=1, max_length=256)],
) -> PublicationList:
    publications, caller = await _context(request)
    try:
        found = await publications.list_for_container(caller, container_id(container_kind, container_key))
    except PublicationRefused as error:
        raise _http(error) from error
    return PublicationList(publications=[view(p) for p in found])


@router.delete(
    "/knowledge/publications/{publication_id}",
    dependencies=[Depends(require_scopes(OAuthScopes.KB_WRITE))],
)
async def revoke_publication(request: Request, publication_id: str) -> PublicationView:
    publications, caller = await _context(request)
    try:
        return view(await publications.revoke(caller, publication_id))
    except (PublicationRefused, PublicationStoreError) as error:
        raise _http(error) from error


__all__ = ["router"]
