"""API routes for the AI model provider registry."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# Importing triggers provider registration via __init__.py side-effect
import app.config.ai_models.providers  # noqa: F401
from app.api.middlewares.admin_gate import require_admin_caller
from app.api.middlewares.auth import deny_service_tokens
from app.config.ai_models.registry import ai_model_registry
from app.config.ai_models.types import CAPABILITY_TO_MODEL_TYPE, ModelCapability
from app.services.ai_models.discovery.registry import discovery_registry
from app.services.ai_models.discovery.service import ModelDiscoveryService
from app.services.ai_models.discovery.types import DiscoveryRequest
from app.services.model_identity.workload import WorkloadClass, workload_class

# One service so the discovery cache survives across requests.
_discovery_service = ModelDiscoveryService()

router = APIRouter(dependencies=[Depends(deny_service_tokens)])


@router.get("/ai-models/registry")
async def get_registry(
    search: str | None = Query(None, description="Search by name or description"),
    capability: str | None = Query(None, description="Filter by capability"),
) -> JSONResponse:
    """List all registered AI model providers with metadata."""
    if search:
        providers = ai_model_registry.search(search)
        if capability:
            providers = [p for p in providers if capability in p.get("capabilities", [])]
    elif capability:
        providers = ai_model_registry.filter_by_capability(capability)
    else:
        providers = ai_model_registry.list_providers()
    enriched = []
    for provider in providers:
        copy = dict(provider)
        copy["discovery"] = discovery_registry.describe(str(provider.get("providerId") or ""))
        enriched.append(copy)
    return JSONResponse(
        content={
            "success": True,
            "providers": enriched,
            "total": len(enriched),
        }
    )


@router.post("/ai-models/discover", dependencies=[Depends(require_admin_caller)])
async def discover_models(body: DiscoveryRequest) -> JSONResponse:
    """List models for a provider. Credentials stay in the body and are not logged."""
    result = await _discovery_service.discover(body)
    payload = result.model_dump(by_alias=True, mode="json")
    payload["success"] = result.error_code is None
    return JSONResponse(content=payload)


class IdentityPreviewRequest(BaseModel):
    """Which person, what kind of call, and which connection's settings to render."""

    userId: str | None = Field(default=None, description="Defaults to the administrator asking")
    workload: str = Field(default="chat", description="A workload name, for example chat or indexing")
    configuration: dict[str, Any] = Field(
        default_factory=dict, description="A connection's configuration, as the model dialog holds it"
    )


@router.post("/ai-models/identity/preview", dependencies=[Depends(require_admin_caller)])
async def preview_identity_forwarding(request: Request, body: IdentityPreviewRequest) -> JSONResponse:
    """Show the exact headers that would be sent for a person, with credentials redacted."""
    from app.services.model_identity.boundary import principal_from_request_user
    from app.services.model_identity.preview import build_preview
    from app.services.model_identity.principal import service_principal, user_principal
    from app.services.model_identity.runtime import get_identity_runtime
    from app.services.model_identity.transport.instrument import settings_from_config
    from app.services.model_identity.workload import Workload

    try:
        workload = Workload(body.workload)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Unknown workload '{body.workload}'.") from None

    caller = getattr(request.state, "user", None) or {}
    org_id = str(caller.get("orgId") or "")
    container = request.app.container
    if workload_class(workload) is not WorkloadClass.INTERACTIVE:
        principal = service_principal(
            "indexing" if workload_class(workload) is WorkloadClass.BACKGROUND else workload.value, org_id
        )
    elif body.userId and body.userId != caller.get("userId"):
        graph_provider = await container.graph_provider()
        target = await graph_provider.get_user_by_user_id(body.userId)
        if not target or str(target.get("orgId") or "") != org_id:
            raise HTTPException(status_code=404, detail="That user was not found in this organisation.")
        principal = user_principal({"userId": body.userId, **target}, org_id)
    else:
        principal = principal_from_request_user(caller)

    runtime = get_identity_runtime(container.config_service(), container)
    governance = await runtime.governance(org_id)
    groups = None
    if principal.is_user and governance.groups_enabled:
        groups = await runtime.resolve_groups(principal)
    preview = build_preview(
        principal=principal,
        workload=workload,
        settings=settings_from_config({"configuration": body.configuration}),
        governance=governance,
        pseudonym_key=await runtime.pseudonym_key() if governance.pseudonymize else None,
        groups=groups,
    )
    return JSONResponse(content={"success": True, **preview})


@router.get("/ai-models/registry/capabilities")
async def get_capabilities() -> JSONResponse:
    """List all available model capabilities."""
    capabilities = [
        {
            "id": cap.value,
            "name": cap.name.replace("_", " ").title(),
            "modelType": CAPABILITY_TO_MODEL_TYPE.get(cap.value, cap.value),
        }
        for cap in ModelCapability
    ]
    return JSONResponse(content={"success": True, "capabilities": capabilities})


@router.get("/ai-models/registry/{provider_id}/schema")
async def get_provider_schema(
    provider_id: str,
    capability: str | None = Query(None, description="Filter fields by capability"),
) -> JSONResponse:
    """Get the field schema for a specific provider."""
    schema = ai_model_registry.get_provider_schema(provider_id, capability)
    if schema is None:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")

    provider = ai_model_registry.get_provider(provider_id)
    return JSONResponse(
        content={
            "success": True,
            "provider": {
                "providerId": provider_id,
                "name": provider.get("name", "") if provider else "",
            },
            "schema": schema,
        }
    )
