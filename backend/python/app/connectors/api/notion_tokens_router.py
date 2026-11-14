"""Notion personal access tokens a user links to a Notion team connector (connectors service).

Notion's API does not say who may open a page, so a Notion team connector grants a page only to
people whose own token lists it. Each user links, replaces or removes their own token; an admin sees
who linked one and its state, never a token. Anything that is not a Notion team connector the
caller may read gets the 404 of a missing one.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.middlewares.auth import is_request_admin, require_scopes
from app.config.constants.arangodb import Connectors
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import OAuthScopes
from app.connectors.api.connector_resolvers import resolve_config_service
from app.connectors.core.registry.connector_builder import ConnectorScope
from app.connectors.sources.notion import personal_tokens
from app.core.org_context import current_org_id
from app.utils.user_lookup import get_user_or_503

notion_tokens_router = APIRouter(tags=["Connectors"])

_PATH = "/api/v1/connectors/{connector_id}/notion/personal-token"


class _LinkBody(BaseModel):
    token: str = Field(min_length=1, max_length=512)


async def _caller_and_config(request: Request, connector_id: str) -> tuple[str, Any]:
    """The caller's user id and the config service, for a Notion team connector of their org.

    Not the registry's read gate, which opens a team connector to admins and its creator only:
    any active member of the org may link their own token, since it can only show them pages
    their own Notion account opens.
    """
    not_found = HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="Not found")
    user_id, org_id = (request.state.user or {}).get("userId"), current_org_id()
    if not user_id or not org_id:
        raise not_found
    registry = request.app.state.connector_registry
    document = await registry._get_connector_instance_from_db(connector_id)
    # The registry names the type "Notion"; the enum spells it in capitals.
    kind = str((document or {}).get("type") or "").upper()
    if (
        kind != Connectors.NOTION.value
        or document.get("scope") != ConnectorScope.TEAM.value
        or not await registry.belongs_to_org(document, org_id)
    ):
        raise not_found
    # The token names the user and their org together; a deactivated account keeps neither.
    user = await get_user_or_503(request.app.state.graph_provider, user_id)
    if not user or user.get("isActive") is False:
        raise not_found
    return user_id, resolve_config_service(request.app.container, org_id)


@notion_tokens_router.get(_PATH, dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
async def get_personal_token(connector_id: str, request: Request) -> dict[str, Any]:
    user_id, config = await _caller_and_config(request, connector_id)
    entry = await config.get_config(personal_tokens.token_path(connector_id, user_id), use_cache=False)
    return personal_tokens.public_view(entry if isinstance(entry, dict) else None)


@notion_tokens_router.put(_PATH, dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
async def link_personal_token(connector_id: str, body: _LinkBody, request: Request) -> dict[str, Any]:
    """Takes effect at the connector's next sync, which checks the token is the caller's own."""
    token = body.token.strip()
    if not token or any(c.isspace() for c in token):
        raise HTTPException(status_code=HttpStatusCode.BAD_REQUEST.value, detail="Paste the personal access token as it is.")
    user_id, config = await _caller_and_config(request, connector_id)
    try:
        await personal_tokens.link_token(config, connector_id, user_id, token)
    except RuntimeError as e:
        raise HTTPException(status_code=HttpStatusCode.SERVICE_UNAVAILABLE.value, detail="The token could not be saved. Try again.") from e
    return personal_tokens.public_view(await config.get_config(personal_tokens.token_path(connector_id, user_id), use_cache=False))


@notion_tokens_router.delete(_PATH, dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
async def unlink_personal_token(connector_id: str, request: Request) -> dict[str, Any]:
    user_id, config = await _caller_and_config(request, connector_id)
    if not await personal_tokens.unlink_token(config, connector_id, user_id):
        raise HTTPException(status_code=HttpStatusCode.SERVICE_UNAVAILABLE.value, detail="The token could not be removed. Try again.")
    return personal_tokens.public_view(None)


@notion_tokens_router.get(
    "/api/v1/connectors/{connector_id}/notion/personal-tokens",
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))],
)
async def list_personal_tokens(connector_id: str, request: Request) -> dict[str, Any]:
    """Admin only: who linked a token and its state."""
    if not is_request_admin(request):
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="Not found")
    _, config = await _caller_and_config(request, connector_id)
    linked = await personal_tokens.entries(config, connector_id)
    return {"users": [{"userId": user_id, **personal_tokens.public_view(entry)} for user_id, entry in sorted(linked.items())]}
