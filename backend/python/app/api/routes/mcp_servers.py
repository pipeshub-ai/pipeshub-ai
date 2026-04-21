"""
MCP Servers API Routes

Handles MCP server catalog, instance management, authentication, and tool discovery.

Architecture:
  - Built-in catalog provides templates for well-known MCP servers (Jira, Slack, etc.)
  - Admin creates "MCP server instances" (visible org-wide): POST /instances
  - Users authenticate against instances:
      * API_TOKEN: POST /instances/{id}/authenticate  (sends apiToken in body)
      * OAuth:     GET  /instances/{id}/oauth/authorize  -> returns redirect URL
                   POST /instances/{id}/oauth/callback    -> exchanges code for tokens
  - User credentials stored at /services/mcp-servers/{instanceId}/{userId}
  - GET /my-mcp-servers returns merged view (instances + user auth status)
  - GET /instances/{id}/tools discovers tools by connecting to the MCP server
  - GET /agents/{agentKey} returns merged view for service-account agents
"""

from typing import Any

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.agents.mcp.registry import MCPServerRegistry, get_mcp_server_registry
from app.agents.mcp.service import get_mcp_service
from app.api.middlewares.auth import require_scopes
from app.config.configuration_service import ConfigurationService
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import OAuthScopes
from app.containers.connector import ConnectorAppContainer

router = APIRouter(prefix="/api/v1/mcp-servers", tags=["mcp-servers"])


def _get_registry(request: Request) -> MCPServerRegistry:
    registry = getattr(request.app.state, "mcp_server_registry", None)
    if registry is None:
        registry = get_mcp_server_registry()
    return registry


# ============================================================================
# Catalog Endpoints
# ============================================================================

@router.get("/catalog", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
async def get_catalog(
    request: Request,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=200),
    search: str | None = Query(None),
) -> dict[str, Any]:
    """List available MCP server templates from the built-in catalog."""
    registry = _get_registry(request)
    service = get_mcp_service(registry)
    result = service.list_catalog_templates(search=search, page=page, limit=limit)
    return {"status": "success", **result}


@router.get("/catalog/{type_id}", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
async def get_catalog_item(type_id: str, request: Request) -> dict[str, Any]:
    """Get a specific catalog template with its configuration schema."""
    registry = _get_registry(request)
    service = get_mcp_service(registry)
    schema = service.get_catalog_template_schema(type_id)
    if not schema:
        raise HTTPException(
            status_code=HttpStatusCode.NOT_FOUND.value,
            detail=f"MCP server type '{type_id}' not found in catalog."
        )
    return {"status": "success", "template": schema}


# ============================================================================
# Instance CRUD (Admin)
# ============================================================================

@router.get("/instances", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
@inject
async def get_instances(
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service]),
) -> dict[str, Any]:
    """List all admin-created MCP server instances."""
    registry = _get_registry(request)
    service = get_mcp_service(registry)
    return await service.list_instances(request, config_service)


@router.post("/instances", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
@inject
async def create_instance(
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service]),
) -> dict[str, Any]:
    """Create a new MCP server instance (from catalog or custom)."""
    registry = _get_registry(request)
    service = get_mcp_service(registry)
    return await service.create_instance(request, config_service)


@router.get("/instances/{instance_id}", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
@inject
async def get_instance(
    instance_id: str,
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service]),
) -> dict[str, Any]:
    """Get a specific MCP server instance."""
    registry = _get_registry(request)
    service = get_mcp_service(registry)
    return await service.get_instance(instance_id, request, config_service)


@router.put("/instances/{instance_id}", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
@inject
async def update_instance(
    instance_id: str,
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service]),
) -> dict[str, Any]:
    """Update an MCP server instance configuration."""
    registry = _get_registry(request)
    service = get_mcp_service(registry)
    return await service.update_instance(instance_id, request, config_service)


@router.delete("/instances/{instance_id}", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_DELETE))])
@inject
async def delete_instance(
    instance_id: str,
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service]),
) -> dict[str, Any]:
    """Delete an MCP server instance and all associated user credentials."""
    registry = _get_registry(request)
    service = get_mcp_service(registry)
    return await service.delete_instance(instance_id, request, config_service)


# ============================================================================
# User Authentication — API Token
# ============================================================================

@router.post("/instances/{instance_id}/authenticate", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
@inject
async def authenticate_instance(
    instance_id: str,
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service]),
) -> dict[str, Any]:
    """
    User authenticates against an MCP server instance by providing credentials
    (API token, headers, etc.). For OAuth servers, use the /oauth/authorize flow.
    """
    registry = _get_registry(request)
    service = get_mcp_service(registry)
    return await service.authenticate_instance(instance_id, request, config_service)


@router.put("/instances/{instance_id}/credentials", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
@inject
async def update_credentials(
    instance_id: str,
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service]),
) -> dict[str, Any]:
    """Update credentials for an MCP server instance."""
    registry = _get_registry(request)
    service = get_mcp_service(registry)
    return await service.update_credentials(instance_id, request, config_service)


@router.delete("/instances/{instance_id}/credentials", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
@inject
async def remove_credentials(
    instance_id: str,
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service]),
) -> dict[str, Any]:
    """Remove credentials for an MCP server instance."""
    registry = _get_registry(request)
    service = get_mcp_service(registry)
    return await service.remove_credentials(instance_id, request, config_service)


@router.post("/instances/{instance_id}/reauthenticate", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
@inject
async def reauthenticate_instance(
    instance_id: str,
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service]),
) -> dict[str, Any]:
    """Clear credentials for an MCP server instance, requiring re-authentication."""
    registry = _get_registry(request)
    service = get_mcp_service(registry)
    return await service.reauthenticate_instance(instance_id, request, config_service)


# ============================================================================
# OAuth Flow
# ============================================================================

@router.get("/instances/{instance_id}/oauth/authorize", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
@inject
async def oauth_authorize(
    instance_id: str,
    request: Request,
    base_url: str | None = Query(None, alias="baseUrl"),
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service]),
) -> dict[str, Any]:
    """Generate an OAuth authorization URL for the user to authorize this MCP server."""
    registry = _get_registry(request)
    service = get_mcp_service(registry)
    return await service.oauth_authorize(instance_id, request, config_service, base_url=base_url)


@router.get("/oauth/callback", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
@inject
async def oauth_callback_global(
    request: Request,
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
    base_url: str | None = Query(None, alias="baseUrl"),
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service]),
) -> dict[str, Any]:
    """Global OAuth callback — mirrors the connector OAuth pattern."""
    registry = _get_registry(request)
    service = get_mcp_service(registry)
    return await service.oauth_callback_global(
        request, config_service,
        code=code, state=state, error=error, base_url=base_url,
    )


@router.post("/instances/{instance_id}/oauth/refresh", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
@inject
async def oauth_refresh(
    instance_id: str,
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service]),
) -> dict[str, Any]:
    """Refresh an expired OAuth access token using the stored refresh token."""
    registry = _get_registry(request)
    service = get_mcp_service(registry)
    return await service.oauth_refresh(instance_id, request, config_service)


# ============================================================================
# OAuth Client Configuration (Admin — per instance)
# ============================================================================

@router.get("/instances/{instance_id}/oauth-config", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
@inject
async def get_instance_oauth_config(
    instance_id: str,
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service]),
) -> dict[str, Any]:
    """Get OAuth client configuration for an instance (admin only, client_secret masked)."""
    registry = _get_registry(request)
    service = get_mcp_service(registry)
    return await service.get_instance_oauth_config(instance_id, request, config_service)


@router.put("/instances/{instance_id}/oauth-config", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
@inject
async def set_instance_oauth_config(
    instance_id: str,
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service]),
) -> dict[str, Any]:
    """Set or update OAuth client credentials for an instance (admin only)."""
    registry = _get_registry(request)
    service = get_mcp_service(registry)
    return await service.set_instance_oauth_config(instance_id, request, config_service)


# ============================================================================
# User Views (merged instances + auth status)
# ============================================================================

@router.get("/my-mcp-servers", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
@inject
async def get_my_mcp_servers(
    request: Request,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=200),
    search: str | None = Query(None),
    include_registry: bool = Query(False, alias="includeRegistry"),
    auth_status: str | None = Query(None, alias="authStatus"),
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service]),
) -> dict[str, Any]:
    """Get all MCP servers for the current user: configured instances merged with auth status."""
    registry = _get_registry(request)
    service = get_mcp_service(registry)
    return await service.get_my_mcp_servers(
        request, config_service,
        page=page, limit=limit, search=search,
        include_registry=include_registry, auth_status=auth_status,
    )


# ============================================================================
# Tool Discovery
# ============================================================================

@router.get("/instances/{instance_id}/tools", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
@inject
async def discover_instance_tools(
    instance_id: str,
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service]),
) -> dict[str, Any]:
    """Discover tools from a connected MCP server (live probe)."""
    registry = _get_registry(request)
    service = get_mcp_service(registry)
    return await service.discover_instance_tools(instance_id, request, config_service)


# ============================================================================
# Agent-Scoped Endpoints (service accounts)
# ============================================================================

@router.get("/agents/{agent_key}", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
@inject
async def get_agent_mcp_servers(
    agent_key: str,
    request: Request,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=200),
    search: str | None = Query(None),
    include_registry: bool = Query(False, alias="includeRegistry"),
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service]),
) -> dict[str, Any]:
    """Get MCP servers merged with agent-level auth status for service-account agents."""
    registry = _get_registry(request)
    service = get_mcp_service(registry)
    return await service.get_agent_mcp_servers(
        agent_key, request, config_service,
        page=page, limit=limit, search=search, include_registry=include_registry,
    )


@router.post("/agents/{agent_key}/instances/{instance_id}/authenticate", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
@inject
async def authenticate_agent_instance(
    agent_key: str,
    instance_id: str,
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service]),
) -> dict[str, Any]:
    """Authenticate an MCP server instance for a service-account agent."""
    registry = _get_registry(request)
    service = get_mcp_service(registry)
    return await service.authenticate_agent_instance(agent_key, instance_id, request, config_service)


@router.put("/agents/{agent_key}/instances/{instance_id}/credentials", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
@inject
async def update_agent_credentials(
    agent_key: str,
    instance_id: str,
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service]),
) -> dict[str, Any]:
    """Update agent credentials for an MCP server instance."""
    registry = _get_registry(request)
    service = get_mcp_service(registry)
    return await service.update_agent_credentials(agent_key, instance_id, request, config_service)


@router.delete("/agents/{agent_key}/instances/{instance_id}/credentials", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
@inject
async def remove_agent_credentials(
    agent_key: str,
    instance_id: str,
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service]),
) -> dict[str, Any]:
    """Remove agent credentials for an MCP server instance."""
    registry = _get_registry(request)
    service = get_mcp_service(registry)
    return await service.remove_agent_credentials(agent_key, instance_id, config_service)
