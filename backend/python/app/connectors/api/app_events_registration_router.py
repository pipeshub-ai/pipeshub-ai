"""Registration surface for app-event ingress URLs (connectors service).

`app_events_router` resolves an inbound webhook by looking up
`/services/connectors/app-events/endpoints/{endpoint_id}`, and nothing wrote
that key -- so every provider webhook 404'd and no event trigger could ever
fire. These routes are the missing writer: they mint an endpoint id, store the
tenant and signing secrets against it, and hand back the path to paste into
Slack/GitHub/Jira.

Unlike the ingest path, these are ordinary authenticated routes: the tenant
comes from the verified JWT, never from the request body, so one org cannot
register an endpoint that publishes into another.
"""
from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.middlewares.auth import require_scopes
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import OAuthScopes
from app.connectors.api.app_events_router import endpoint_config_path

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService

__all__ = ["router"]

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/app-events/endpoints",
    tags=["app-events-registration"],
    dependencies=[Depends(require_scopes(OAuthScopes.WORKFLOW_WRITE))],
)

# Only apps with a verifier can have their signatures checked; registering
# anything else would create an endpoint that accepts unverified posts.
_SUPPORTED_SOURCE_APPS = frozenset({"slack", "github", "jira", "confluence"})


def _org_index_path(org_id: str) -> str:
    return f"/services/connectors/app-events/orgs/{org_id}"


class RegisterEndpointRequest(BaseModel):
    source_app: str
    signing_secret: str = ""
    """Provider-side HMAC secret (Slack signing secret, GitHub webhook secret)."""
    webhook_secret: str = ""
    """Jira's query-parameter shared secret, for providers without body HMAC."""
    app_id: str = ""
    label: str = Field(default="", max_length=200)


class EndpointResponse(BaseModel):
    endpoint_id: str
    source_app: str
    label: str
    path: str
    """Relative ingest path. The absolute URL depends on how the connectors
    service is exposed publicly, which this service cannot know reliably."""


def _config_service(request: Request) -> "ConfigurationService":
    try:
        return request.app.container.config_service()  # type: ignore[attr-defined]
    except Exception:
        raise HTTPException(
            status_code=HttpStatusCode.SERVICE_UNAVAILABLE.value,
            detail="Configuration service is unavailable",
        ) from None


def _org_id(request: Request) -> str:
    user = getattr(request.state, "user", None) or {}
    org_id = user.get("orgId")
    if not org_id:
        raise HTTPException(
            status_code=HttpStatusCode.UNAUTHORIZED.value,
            detail="Authentication required. Please provide valid credentials.",
        )
    return str(org_id)


def _ingest_path(source_app: str, endpoint_id: str) -> str:
    return f"/app-events/{source_app}/{endpoint_id}"


async def _read_index(config_service: "ConfigurationService", org_id: str) -> list[str]:
    record = await config_service.get_config(_org_index_path(org_id), default=None)
    if isinstance(record, dict):
        ids = record.get("endpoint_ids")
        if isinstance(ids, list):
            return [str(i) for i in ids]
    return []


@router.post("", response_model=EndpointResponse)
async def register_endpoint(body: RegisterEndpointRequest, request: Request) -> EndpointResponse:
    """Mint an ingest URL for one provider app in the caller's org."""
    source_app = body.source_app.strip().lower()
    if source_app not in _SUPPORTED_SOURCE_APPS:
        raise HTTPException(
            status_code=HttpStatusCode.BAD_REQUEST.value,
            detail=(
                f"Unsupported source_app '{body.source_app}'. "
                f"Supported: {', '.join(sorted(_SUPPORTED_SOURCE_APPS))}"
            ),
        )

    org_id = _org_id(request)
    config_service = _config_service(request)
    endpoint_id = uuid.uuid4().hex

    record: dict[str, Any] = {
        "org_id": org_id,
        "source_app": source_app,
        "signing_secret": body.signing_secret,
        "webhook_secret": body.webhook_secret,
        "app_id": body.app_id,
        "label": body.label,
    }
    await config_service.set_config(endpoint_config_path(endpoint_id), record)

    existing = await _read_index(config_service, org_id)
    await config_service.set_config(
        _org_index_path(org_id), {"endpoint_ids": [*existing, endpoint_id]},
    )

    logger.info(
        "app-events: registered endpoint %s for org=%s source_app=%s",
        endpoint_id, org_id, source_app,
    )
    return EndpointResponse(
        endpoint_id=endpoint_id,
        source_app=source_app,
        label=body.label,
        path=_ingest_path(source_app, endpoint_id),
    )


@router.get("", response_model=list[EndpointResponse])
async def list_endpoints(request: Request) -> list[EndpointResponse]:
    """Registered endpoints for the caller's org. Secrets are never returned."""
    org_id = _org_id(request)
    config_service = _config_service(request)

    endpoints: list[EndpointResponse] = []
    for endpoint_id in await _read_index(config_service, org_id):
        record = await config_service.get_config(endpoint_config_path(endpoint_id), default=None)
        # The index can outlive a record deleted by another path; a stale id is
        # not an error worth failing the whole listing for.
        if not isinstance(record, dict) or record.get("org_id") != org_id:
            continue
        source_app = str(record.get("source_app") or "")
        endpoints.append(EndpointResponse(
            endpoint_id=endpoint_id,
            source_app=source_app,
            label=str(record.get("label") or ""),
            path=_ingest_path(source_app, endpoint_id),
        ))
    return endpoints


@router.delete("/{endpoint_id}")
async def delete_endpoint(endpoint_id: str, request: Request) -> dict:
    """Revoke an ingest URL. Idempotent."""
    org_id = _org_id(request)
    config_service = _config_service(request)

    record = await config_service.get_config(endpoint_config_path(endpoint_id), default=None)
    # An id belonging to another org must be indistinguishable from one that
    # does not exist, or the namespace becomes probeable.
    if not isinstance(record, dict) or record.get("org_id") != org_id:
        raise HTTPException(
            status_code=HttpStatusCode.NOT_FOUND.value, detail="Unknown webhook endpoint",
        )

    await config_service.delete_config(endpoint_config_path(endpoint_id))
    remaining = [i for i in await _read_index(config_service, org_id) if i != endpoint_id]
    await config_service.set_config(_org_index_path(org_id), {"endpoint_ids": remaining})

    logger.info("app-events: deleted endpoint %s for org=%s", endpoint_id, org_id)
    return {"ok": True}
