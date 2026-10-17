"""API routes for the AI model provider registry."""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

# Importing triggers provider registration via __init__.py side-effect
import app.config.ai_models.providers  # noqa: F401
from app.api.middlewares.admin_gate import require_admin_caller
from app.api.middlewares.auth import deny_service_tokens
from app.config.ai_models.registry import ai_model_registry
from app.config.ai_models.types import CAPABILITY_TO_MODEL_TYPE, ModelCapability
from app.services.ai_models.discovery.registry import discovery_registry
from app.services.ai_models.discovery.service import ModelDiscoveryService
from app.services.ai_models.discovery.types import DiscoveryRequest

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
