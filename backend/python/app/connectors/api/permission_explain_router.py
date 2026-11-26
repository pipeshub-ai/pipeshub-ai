"""Why one person can or cannot read one record: an organisation admin's question (connectors service).

The Node API is the gate (admins only, rate limited, logged); this route refuses a non-admin as well, with the 404 of a route that
is not there, so a caller that reaches this service directly learns nothing.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.middlewares.auth import is_request_admin, require_scopes
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import OAuthScopes
from app.core.org_context import current_org_id
from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider  # noqa: TC001 - FastAPI reads the annotation at runtime
from app.services.permissions.errors import ViewerScopesUnavailable
from app.services.permissions.explain import (
    ExplainNotFound,
    ExplainService,
)
from app.utils.user_lookup import get_user_or_503

permission_explain_router = APIRouter(tags=["Permissions"])


async def get_graph_provider(request: Request) -> IGraphDBProvider:
    return request.app.state.graph_provider


def _service(request: Request, provider: IGraphDBProvider) -> ExplainService:
    """One per process, so the mismatches it counts accumulate."""
    state = request.app.state
    if getattr(state, "explain_service", None) is None:
        state.explain_service = ExplainService(provider)
    return state.explain_service


@permission_explain_router.get(
    "/api/v1/connectors/{connector_id}/permissions/explain",
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))],
)
async def explain_permission(
    connector_id: str,
    request: Request,
    user_id: str = Query(..., min_length=1, max_length=128, description="The person the question is about."),
    record_id: str = Query(..., min_length=1, max_length=256, description="The record, by id."),
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
) -> dict:
    not_found = HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="Not found")
    if not is_request_admin(request):
        raise not_found
    asker_id, org_id = request.state.user.get("userId"), current_org_id()
    if not asker_id:
        raise not_found
    asker, viewer = await get_user_or_503(graph_provider, asker_id), await get_user_or_503(graph_provider, user_id)
    asker_key = (asker or {}).get("id") or (asker or {}).get("_key")
    viewer_key = (viewer or {}).get("id") or (viewer or {}).get("_key")
    # A person of another organisation is refused by the service, with the same not-found.
    if not asker_key or not viewer_key:
        raise not_found
    try:
        result = await _service(request, graph_provider).explain(
            org_id, viewer_key, record_id, asker_key=asker_key, connector_id=connector_id)
    except ExplainNotFound:
        raise not_found from None
    except (ViewerScopesUnavailable, PermissionVerificationUnavailableError) as exc:
        raise HTTPException(
            status_code=HttpStatusCode.SERVICE_UNAVAILABLE.value, detail="The access check is unavailable. Try again.") from exc
    return result.as_dict()
