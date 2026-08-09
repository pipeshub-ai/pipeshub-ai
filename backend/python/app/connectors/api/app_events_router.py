"""App events ingress FastAPI router (connectors service, port 8088).

Receives raw provider webhooks and dispatches to Topic.APP_EVENTS, which the
`AppEventConsumer` in the query service turns into workflow runs.

The URL carries an opaque, per-registration `endpoint_id` -- the same shape as
`task_webhook_router.py`'s `{webhook_id}` -- and the tenant plus signing
secrets are looked up from that id server-side. Provider webhooks carry no
PipesHub identity, so an `X-Org-Id` header (the previous scheme) would let any
caller publish events into any org's workflows.

Like the task webhook router, this path is excluded from JWT auth in
`connectors_main.py`; per-provider HMAC verification is the auth.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request

from app.config.constants.http_status_code import HttpStatusCode
from app.services.events.models import AppCredentials, RawWebhookRequest

if TYPE_CHECKING:
    from app.services.events.ingress import AppEventIngress

__all__ = ["router", "APP_EVENTS_PATH_PREFIX", "endpoint_config_path"]

logger = logging.getLogger(__name__)

APP_EVENTS_PATH_PREFIX = "/app-events/"

router = APIRouter(prefix="/app-events", tags=["app-events"])


def endpoint_config_path(endpoint_id: str) -> str:
    """Config key holding `{org_id, source_app, signing_secret, ...}` for one
    registered ingress URL. Same `ConfigurationService` +
    `EncryptedKeyValueStore` precedent as `ConfigServiceWebhookSecretStore`
    (`app/services/tasks/adapters/config/webhook_secret_store.py`)."""
    return f"/services/connectors/app-events/endpoints/{endpoint_id}"


def _get_ingress(request: Request) -> "AppEventIngress":
    ingress = getattr(request.app.state, "app_event_ingress", None)
    if ingress is None:
        raise HTTPException(
            status_code=HttpStatusCode.SERVICE_UNAVAILABLE.value,
            detail="App event ingress is temporarily unavailable",
        )
    return ingress


async def _resolve_endpoint(
    request: Request, endpoint_id: str, source_app: str,
) -> AppCredentials:
    """Look up the registration for `endpoint_id`. Unknown ids, and ids
    registered for a different `source_app`, are indistinguishable 404s so the
    endpoint namespace cannot be probed."""
    try:
        config_service = request.app.container.config_service()  # type: ignore[attr-defined]
        record = await config_service.get_config(endpoint_config_path(endpoint_id), default=None)
    except Exception:
        logger.exception("app-events: failed to read endpoint config")
        raise HTTPException(
            status_code=HttpStatusCode.SERVICE_UNAVAILABLE.value,
            detail="App event ingress is temporarily unavailable",
        ) from None

    if not isinstance(record, dict) or record.get("source_app") != source_app or not record.get("org_id"):
        logger.warning("app-events: unknown endpoint id for source_app=%s", source_app)
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="Unknown webhook endpoint")

    return AppCredentials(
        org_id=str(record["org_id"]),
        signing_secret=record.get("signing_secret") or "",
        webhook_secret=record.get("webhook_secret") or "",
        app_id=record.get("app_id") or "",
    )


@router.post("/{source_app}/{endpoint_id}")
async def receive_app_event(source_app: str, endpoint_id: str, request: Request) -> dict:
    """Receive a provider webhook and fan out to subscribed workflows."""
    credentials = await _resolve_endpoint(request, endpoint_id, source_app)

    raw = RawWebhookRequest(
        headers=dict(request.headers),
        body=await request.body(),
        source_ip=request.client.host if request.client else "",
        path=request.url.path,
        query_params=dict(request.query_params),
    )

    ingress = _get_ingress(request)
    result = await ingress.handle(source_app=source_app, req=raw, credentials=credentials)

    if result.get("action") == "url_verification":
        # Slack challenge — must be echoed back synchronously.
        return {"challenge": result["challenge"]}

    if result.get("action") == "unsupported_app":
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="Unknown webhook endpoint")

    if result.get("action") == "rate_limited":
        # 429, not 401: providers back off and redeliver on 429, whereas
        # repeated 401s make Slack and GitHub disable the endpoint outright.
        raise HTTPException(
            status_code=HttpStatusCode.TOO_MANY_REQUESTS.value,
            detail="Too many events for this endpoint",
        )

    if not result.get("ok"):
        # Never echo the verifier's reason: it distinguishes "bad signature"
        # from "no secret configured" for an unauthenticated caller.
        raise HTTPException(
            status_code=HttpStatusCode.UNAUTHORIZED.value, detail="Webhook verification failed",
        )

    return {"ok": True, "action": result.get("action")}
