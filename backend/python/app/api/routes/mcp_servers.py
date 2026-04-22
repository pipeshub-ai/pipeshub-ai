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

import asyncio
import json
import logging
import secrets
import time
import uuid
from typing import Any
from urllib.parse import urlencode

import httpx
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.agents.constants.mcp_server_constants import (
    get_mcp_server_config_path,
    get_mcp_server_instance_users_prefix,
    get_mcp_server_instances_path,
    get_mcp_server_oauth_client_path,
    get_mcp_server_oauth_tokens_path,
)
from app.agents.mcp.models import MCPAuthMode, MCPServerConfig, MCPTransport
from app.agents.mcp.registry import MCPServerRegistry, get_mcp_server_registry
from app.api.middlewares.auth import require_scopes
from app.config.configuration_service import ConfigurationService
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import OAuthScopes
from app.containers.connector import ConnectorAppContainer
from app.utils.time_conversion import get_epoch_timestamp_in_ms

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/mcp-servers", tags=["mcp-servers"])


# ============================================================================
# Helpers
# ============================================================================

def _get_user_context(request: Request) -> dict[str, str]:
    """Extract user context from request headers or auth middleware state."""
    user = getattr(request.state, "user", None) or {}
    user_id = request.headers.get("x-user-id", "") or user.get("userId", "")
    org_id = request.headers.get("x-org-id", "") or user.get("orgId", "")
    if not user_id:
        raise HTTPException(status_code=HttpStatusCode.UNAUTHORIZED.value, detail="User ID is required.")
    if not org_id:
        raise HTTPException(status_code=HttpStatusCode.BAD_REQUEST.value, detail="Organization ID is required.")
    return {"user_id": user_id, "org_id": org_id}


def _parse_request_json(request: Request, body_data: bytes) -> dict[str, Any]:
    if not body_data:
        return {}
    try:
        return json.loads(body_data)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=HttpStatusCode.BAD_REQUEST.value, detail="Invalid JSON in request body.") from e


def _get_registry(request: Request) -> MCPServerRegistry:
    registry = getattr(request.app.state, "mcp_server_registry", None)
    if registry is None:
        registry = get_mcp_server_registry()
    return registry


async def _check_user_is_admin(user_id: str, request: Request, config_service: ConfigurationService) -> bool:
    """Check if user is an admin. Simplified for single-org mode."""
    try:
        is_admin = request.headers.get("x-is-admin", "false").lower() == "true"
        return is_admin
    except Exception:
        return False


async def _load_instances(config_service: ConfigurationService, org_id: str) -> list[dict[str, Any]]:
    """Load all MCP server instances from etcd."""
    instances_path = get_mcp_server_instances_path()
    try:
        instances_data = await config_service.get_config(instances_path, default=[])
        if not isinstance(instances_data, list):
            return []
        return [i for i in instances_data if i.get("orgId") == org_id]
    except Exception as e:
        logger.error("Failed to load MCP server instances: %s", e, exc_info=True)
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail="Failed to load MCP server instances."
        ) from e


async def _save_instances(config_service: ConfigurationService, instances: list[dict[str, Any]]) -> None:
    """Save all MCP server instances to etcd."""
    instances_path = get_mcp_server_instances_path()
    try:
        await config_service.set_config(instances_path, instances)
    except Exception as e:
        logger.error("Failed to save MCP server instances: %s", e, exc_info=True)
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail="Failed to save MCP server instances."
        ) from e


async def _fetch_user_auth(
    instance_id: str,
    user_id: str,
    config_service: ConfigurationService,
) -> dict[str, Any] | None:
    """Fetch a user's auth record for an MCP server instance."""
    auth_path = get_mcp_server_config_path(instance_id, user_id)
    try:
        auth_data = await config_service.get_config(auth_path, default=None)
        if isinstance(auth_data, dict):
            return auth_data
        return None
    except Exception:
        return None


async def _load_oauth_client_config(
    instance_id: str,
    config_service: ConfigurationService,
) -> dict[str, Any] | None:
    """Load OAuth client credentials (clientId, clientSecret) for an MCP server instance."""
    path = get_mcp_server_oauth_client_path(instance_id)
    try:
        data = await config_service.get_config(path, default=None)
        if isinstance(data, dict) and data.get("clientId"):
            return data
        return None
    except Exception:
        return None


_TOOL_DISCOVERY_TIMEOUT_SECONDS = 10



async def _discover_tools_for_instance(
    instance: dict[str, Any],
    user_auth: dict[str, Any],
) -> list[dict[str, Any]]:
    """Discover tools from a single authenticated MCP server instance.

    Builds an MCPServerConfig from the instance metadata + user auth,
    connects, lists tools, and returns serialised MCPToolInfo dicts.
    Returns an empty list on any failure (timeout, connection error, etc.).
    """
    from app.agents.mcp.client import MCPClientManager
    from app.agents.mcp.discovery import MCPToolDiscovery

    instance_id = instance.get("_id", "")
    display_name = instance.get("displayName", instance.get("instanceName", instance_id))

    transport_str = instance.get("transport", "stdio")
    try:
        transport = MCPTransport(transport_str)
    except ValueError:
        transport = MCPTransport.STDIO

    auth_mode_str = instance.get("authMode", "none")
    try:
        auth_mode = MCPAuthMode(auth_mode_str)
    except ValueError:
        auth_mode = MCPAuthMode.NONE

    env: dict[str, str] = {}
    headers: dict[str, str] = {}
    auth = user_auth.get("auth", {})
    credentials = user_auth.get("credentials", {})

    logger.debug(
        "Tool discovery for '%s' (id=%s): transport=%s, authMode=%s, "
        "auth_keys=%s, credentials_keys=%s",
        display_name, instance_id, transport_str, auth_mode_str,
        list(auth.keys()), list(credentials.keys()),
    )

    if transport == MCPTransport.STDIO:
        token = auth.get("apiToken", "")
        if token:
            required_env = instance.get("requiredEnv", [])
            if required_env:
                env[required_env[0]] = token
    else:
        if auth_mode == MCPAuthMode.OAUTH:
            token = credentials.get("access_token", "") or auth.get("access_token", "")
            if token:
                headers["Authorization"] = f"Bearer {token}"
        else:
            token = auth.get("apiToken", "") or credentials.get("access_token", "")
            if token:
                header_mapping = instance.get("authHeaderMapping", {})
                mapped_header = header_mapping.get("apiToken", "")
                if mapped_header:
                    headers[mapped_header] = token
                else:
                    headers["Authorization"] = f"Bearer {token}"

    logger.debug(
        "Tool discovery for '%s': url=%s, header_keys=%s, token_length=%d",
        display_name, instance.get("url", ""), list(headers.keys()),
        len(token) if token else 0,
    )

    config = MCPServerConfig(
        name=instance_id,
        server_type=instance.get("serverType", ""),
        transport=transport,
        command=instance.get("command", ""),
        args=instance.get("args", []),
        env=env,
        url=instance.get("url", ""),
        headers=headers,
        auth_mode=auth_mode,
    )

    manager = MCPClientManager()
    discovery = MCPToolDiscovery(manager)
    try:
        tools = await asyncio.wait_for(
            discovery.discover_tools(instance_id, config),
            timeout=_TOOL_DISCOVERY_TIMEOUT_SECONDS,
        )
        logger.info(
            "Discovered %d tools for '%s' (id=%s)",
            len(tools), display_name, instance_id,
        )
        return [
            {
                "name": t.name,
                "namespacedName": t.namespaced_name,
                "description": t.description,
                "inputSchema": t.input_schema,
            }
            for t in tools
        ]
    except Exception:
        logger.warning("Tool discovery failed for instance '%s' (id=%s)", display_name, instance_id, exc_info=True)
        return []
    finally:
        await manager.disconnect_all()


async def _save_oauth_client_config(
    instance_id: str,
    client_id: str,
    client_secret: str,
    config_service: ConfigurationService,
    updated_by: str = "",
) -> None:
    """Persist OAuth client credentials for an MCP server instance."""
    path = get_mcp_server_oauth_client_path(instance_id)
    data = {
        "clientId": client_id,
        "clientSecret": client_secret,
        "instanceId": instance_id,
        "updatedAt": get_epoch_timestamp_in_ms(),
        "updatedBy": updated_by,
    }
    await config_service.set_config(path, data)


async def _save_oauth_tokens(
    instance_id: str,
    user_id: str,
    tokens: dict[str, Any],
    config_service: ConfigurationService,
) -> None:
    """Persist OAuth tokens to etcd at the dedicated tokens path."""
    path = get_mcp_server_oauth_tokens_path(instance_id, user_id)
    await config_service.set_config(path, tokens)


async def _load_oauth_tokens(
    instance_id: str,
    user_id: str,
    config_service: ConfigurationService,
) -> dict[str, Any] | None:
    """Load OAuth tokens from etcd."""
    path = get_mcp_server_oauth_tokens_path(instance_id, user_id)
    try:
        data = await config_service.get_config(path, default=None)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


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
    result = registry.list_templates(search=search, page=page, limit=limit)
    return {"status": "success", **result}


@router.get("/catalog/{type_id}", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
async def get_catalog_item(type_id: str, request: Request) -> dict[str, Any]:
    """Get a specific catalog template with its configuration schema."""
    registry = _get_registry(request)
    schema = registry.get_template_schema(type_id)
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
    user_context = _get_user_context(request)
    instances = await _load_instances(config_service, user_context["org_id"])

    for inst in instances:
        if inst.get("authMode") == "oauth":
            oauth_cfg = await _load_oauth_client_config(inst.get("_id", ""), config_service)
            inst["hasOAuthClientConfig"] = bool(oauth_cfg)

    return {"status": "success", "instances": instances}


@router.post("/instances", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
@inject
async def create_instance(
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service]),
) -> dict[str, Any]:
    """Create a new MCP server instance (from catalog or custom)."""
    user_context = _get_user_context(request)
    is_admin = await _check_user_is_admin(user_context["user_id"], request, config_service)
    if not is_admin:
        raise HTTPException(status_code=HttpStatusCode.FORBIDDEN.value, detail="Only administrators can create MCP server instances.")

    body_data = await request.body()
    body = _parse_request_json(request, body_data)

    instance_name = body.get("instanceName", "").strip()
    if not instance_name:
        raise HTTPException(status_code=HttpStatusCode.BAD_REQUEST.value, detail="instanceName is required.")

    server_type = body.get("serverType", "custom")
    transport = body.get("transport", "stdio")
    auth_mode = body.get("authMode", "none")

    registry = _get_registry(request)
    template = registry.get_template(server_type)

    now = get_epoch_timestamp_in_ms()
    instance_id = str(uuid.uuid4())

    auth_header_mapping: dict[str, str] = {}
    if template and template.auth.env_mapping:
        auth_header_mapping = dict(template.auth.env_mapping)

    instance: dict[str, Any] = {
        "_id": instance_id,
        "instanceName": instance_name,
        "serverType": server_type,
        "displayName": body.get("displayName", instance_name),
        "description": body.get("description", template.description if template else ""),
        "transport": transport,
        "command": body.get("command", template.command if template else ""),
        "args": body.get("args", list(template.default_args) if template else []),
        "url": body.get("url", template.url if template else ""),
        "authMode": auth_mode,
        "supportedAuthTypes": body.get("supportedAuthTypes", template.supported_auth_types if template else []),
        "requiredEnv": body.get("requiredEnv", list(template.required_env) if template else []),
        "iconPath": body.get("iconPath", template.icon_path if template else ""),
        "authHeaderMapping": body.get("authHeaderMapping", auth_header_mapping),
        "enabled": True,
        "orgId": user_context["org_id"],
        "createdBy": user_context["user_id"],
        "createdAtTimestamp": now,
        "updatedAtTimestamp": now,
    }

    all_instances_data = await config_service.get_config(get_mcp_server_instances_path(), default=[])
    if not isinstance(all_instances_data, list):
        all_instances_data = []
    all_instances_data.append(instance)
    await _save_instances(config_service, all_instances_data)

    # Store OAuth client credentials separately (never in instance metadata)
    client_id = body.get("clientId", "").strip()
    client_secret = body.get("clientSecret", "").strip()
    if auth_mode == "oauth" and client_id:
        await _save_oauth_client_config(
            instance_id, client_id, client_secret,
            config_service, updated_by=user_context["user_id"],
        )
        instance["hasOAuthClientConfig"] = True

    return {"status": "success", "instance": instance, "message": "MCP server instance created successfully."}


@router.get("/instances/{instance_id}", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
@inject
async def get_instance(
    instance_id: str,
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service]),
) -> dict[str, Any]:
    """Get a specific MCP server instance."""
    user_context = _get_user_context(request)
    instances = await _load_instances(config_service, user_context["org_id"])
    instance = next((i for i in instances if i.get("_id") == instance_id), None)
    if not instance:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail=f"MCP server instance '{instance_id}' not found.")
    return {"status": "success", "instance": instance}


@router.put("/instances/{instance_id}", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
@inject
async def update_instance(
    instance_id: str,
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service]),
) -> dict[str, Any]:
    """Update an MCP server instance configuration."""
    user_context = _get_user_context(request)
    is_admin = await _check_user_is_admin(user_context["user_id"], request, config_service)
    if not is_admin:
        raise HTTPException(status_code=HttpStatusCode.FORBIDDEN.value, detail="Only administrators can update MCP server instances.")

    body_data = await request.body()
    body = _parse_request_json(request, body_data)
    org_id = user_context["org_id"]

    all_instances_data = await config_service.get_config(get_mcp_server_instances_path(), default=[])
    if not isinstance(all_instances_data, list):
        all_instances_data = []

    idx = next((i for i, inst in enumerate(all_instances_data) if inst.get("_id") == instance_id and inst.get("orgId") == org_id), None)
    if idx is None:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail=f"MCP server instance '{instance_id}' not found.")

    instance = all_instances_data[idx]

    updatable_fields = [
        "instanceName", "displayName", "description", "transport",
        "command", "args", "url", "authMode", "supportedAuthTypes",
        "requiredEnv", "iconPath", "authHeaderMapping", "enabled",
    ]
    for field in updatable_fields:
        if field in body:
            instance[field] = body[field]

    instance["updatedAtTimestamp"] = get_epoch_timestamp_in_ms()
    all_instances_data[idx] = instance
    await _save_instances(config_service, all_instances_data)

    return {"status": "success", "instance": instance, "message": "MCP server instance updated successfully."}


@router.delete("/instances/{instance_id}", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_DELETE))])
@inject
async def delete_instance(
    instance_id: str,
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service]),
) -> dict[str, Any]:
    """Delete an MCP server instance and all associated user credentials."""
    user_context = _get_user_context(request)
    is_admin = await _check_user_is_admin(user_context["user_id"], request, config_service)
    if not is_admin:
        raise HTTPException(status_code=HttpStatusCode.FORBIDDEN.value, detail="Only administrators can delete MCP server instances.")

    org_id = user_context["org_id"]

    all_instances_data = await config_service.get_config(get_mcp_server_instances_path(), default=[])
    if not isinstance(all_instances_data, list):
        all_instances_data = []

    instance = next((i for i in all_instances_data if i.get("_id") == instance_id and i.get("orgId") == org_id), None)
    if not instance:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail=f"MCP server instance '{instance_id}' not found.")

    deleted_count = 0
    try:
        prefix = get_mcp_server_instance_users_prefix(instance_id)
        user_keys = await config_service.list_keys_in_directory(prefix)
        if user_keys:
            for key in user_keys:
                try:
                    await config_service.delete_config(key)
                    deleted_count += 1
                except Exception as e:
                    logger.warning("Failed to delete user credential %s: %s", key, e)
    except Exception as e:
        logger.warning("Failed to list user credentials for instance %s: %s", instance_id, e)

    all_instances_data = [i for i in all_instances_data if i.get("_id") != instance_id]
    await _save_instances(config_service, all_instances_data)

    # Cancel all refresh tasks for this instance (all users)
    try:
        from app.connectors.core.base.token_service.startup_service import startup_service
        mcp_refresh_service = startup_service.get_mcp_token_refresh_service()
        if mcp_refresh_service:
            mcp_refresh_service.cancel_refresh_tasks_for_instance(instance_id)
    except Exception:
        pass

    return {
        "status": "success",
        "message": f"MCP server instance deleted. {deleted_count} user credential(s) removed.",
    }


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
    user_context = _get_user_context(request)
    org_id = user_context["org_id"]
    user_id = user_context["user_id"]

    instances = await _load_instances(config_service, org_id)
    instance = next((i for i in instances if i.get("_id") == instance_id), None)
    if not instance:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail=f"MCP server instance '{instance_id}' not found.")

    auth_mode = instance.get("authMode", "none")
    if auth_mode == "oauth":
        raise HTTPException(
            status_code=HttpStatusCode.BAD_REQUEST.value,
            detail="For OAuth MCP servers, use /instances/{instance_id}/oauth/authorize."
        )

    body_data = await request.body()
    body = _parse_request_json(request, body_data)
    auth = body.get("auth", {})

    if not auth and auth_mode != "none":
        raise HTTPException(status_code=HttpStatusCode.BAD_REQUEST.value, detail="Credentials are required.")

    now = get_epoch_timestamp_in_ms()
    user_auth: dict[str, Any] = {
        "isAuthenticated": True,
        "authMode": auth_mode,
        "instanceId": instance_id,
        "serverType": instance.get("serverType"),
        "transport": instance.get("transport", "stdio"),
        "command": instance.get("command", ""),
        "args": instance.get("args", []),
        "url": instance.get("url", ""),
        "requiredEnv": instance.get("requiredEnv", []),
        "auth": auth,
        "credentials": {},
        "updatedAt": now,
        "updatedBy": user_id,
    }

    auth_path = get_mcp_server_config_path(instance_id, user_id)
    try:
        await config_service.set_config(auth_path, user_auth)
    except Exception as e:
        logger.error("Failed to save MCP server auth for instance %s: %s", instance_id, e)
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail="Failed to save credentials."
        ) from e

    return {"status": "success", "message": "MCP server authenticated successfully.", "isAuthenticated": True}


@router.put("/instances/{instance_id}/credentials", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
@inject
async def update_credentials(
    instance_id: str,
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service]),
) -> dict[str, Any]:
    """Update credentials for an MCP server instance."""
    user_context = _get_user_context(request)
    user_id = user_context["user_id"]

    body_data = await request.body()
    body = _parse_request_json(request, body_data)
    auth = body.get("auth", {})
    if not auth:
        raise HTTPException(status_code=HttpStatusCode.BAD_REQUEST.value, detail="Credentials are required.")

    auth_path = get_mcp_server_config_path(instance_id, user_id)
    try:
        existing = await config_service.get_config(auth_path, default=None)
        if not existing or not isinstance(existing, dict):
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail="No existing credentials found. Please authenticate first."
            )
        existing["auth"] = auth
        existing["updatedAt"] = get_epoch_timestamp_in_ms()
        existing["updatedBy"] = user_id
        await config_service.set_config(auth_path, existing)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to update credentials for MCP server %s: %s", instance_id, e)
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail="Failed to update credentials."
        ) from e

    return {"status": "success", "message": "Credentials updated successfully."}


@router.delete("/instances/{instance_id}/credentials", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
@inject
async def remove_credentials(
    instance_id: str,
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service]),
) -> dict[str, Any]:
    """Remove credentials for an MCP server instance."""
    user_context = _get_user_context(request)
    user_id = user_context["user_id"]
    auth_path = get_mcp_server_config_path(instance_id, user_id)
    try:
        await config_service.delete_config(auth_path)
    except Exception as e:
        logger.warning("Failed to delete MCP server auth for instance %s: %s", instance_id, e)

    tokens_path = get_mcp_server_oauth_tokens_path(instance_id, user_id)
    try:
        await config_service.delete_config(tokens_path)
    except Exception:
        pass

    # Cancel any pending token refresh task for this user's credentials
    try:
        from app.connectors.core.base.token_service.startup_service import startup_service
        mcp_refresh_service = startup_service.get_mcp_token_refresh_service()
        if mcp_refresh_service:
            mcp_refresh_service.cancel_refresh_task(auth_path)
    except Exception:
        pass

    return {"status": "success", "message": "Credentials removed successfully."}


@router.post("/instances/{instance_id}/auto-authenticate", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
@inject
async def auto_authenticate_instance(
    instance_id: str,
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service]),
) -> dict[str, Any]:
    """
    Automatically authenticate a non-admin user against an API-token MCP server
    by copying the admin's stored API token to the current user's credential path.

    This is only valid for instances where authMode is 'api_token'. The admin who
    created the instance must have previously authenticated themselves (storing their
    token at their own user credential path). That token is then shared to the
    requesting user's credential path.
    """
    user_context = _get_user_context(request)
    org_id = user_context["org_id"]
    user_id = user_context["user_id"]

    instances = await _load_instances(config_service, org_id)
    instance = next((i for i in instances if i.get("_id") == instance_id), None)
    if not instance:
        raise HTTPException(
            status_code=HttpStatusCode.NOT_FOUND.value,
            detail=f"MCP server instance '{instance_id}' not found.",
        )

    auth_mode = instance.get("authMode", "none")
    if auth_mode == "oauth":
        raise HTTPException(
            status_code=HttpStatusCode.BAD_REQUEST.value,
            detail="OAuth MCP servers require manual sign-in. Use /instances/{instance_id}/oauth/authorize.",
        )
    if auth_mode == "none":
        raise HTTPException(
            status_code=HttpStatusCode.BAD_REQUEST.value,
            detail="This MCP server requires no authentication.",
        )

    # Retrieve the admin's stored credentials (from the user who created the instance)
    created_by = instance.get("createdBy", "")
    if not created_by:
        raise HTTPException(
            status_code=HttpStatusCode.BAD_REQUEST.value,
            detail="Cannot determine the instance creator. Please contact an administrator.",
        )

    admin_auth = await _fetch_user_auth(instance_id, created_by, config_service)
    if not admin_auth or not admin_auth.get("isAuthenticated"):
        raise HTTPException(
            status_code=HttpStatusCode.CONFLICT.value,
            detail=(
                "The administrator has not yet authenticated this MCP server. "
                "Please ask an admin to authenticate the instance before you can auto-authenticate."
            ),
        )

    # Copy admin credentials to the current user's path
    now = get_epoch_timestamp_in_ms()
    user_auth: dict[str, Any] = {
        "isAuthenticated": True,
        "authMode": auth_mode,
        "instanceId": instance_id,
        "serverType": instance.get("serverType"),
        "transport": instance.get("transport", "stdio"),
        "command": instance.get("command", ""),
        "args": instance.get("args", []),
        "url": instance.get("url", ""),
        "requiredEnv": instance.get("requiredEnv", []),
        "auth": admin_auth.get("auth", {}),
        "credentials": admin_auth.get("credentials", {}),
        "updatedAt": now,
        "updatedBy": user_id,
    }

    auth_path = get_mcp_server_config_path(instance_id, user_id)
    try:
        await config_service.set_config(auth_path, user_auth)
    except Exception as e:
        logger.error("Failed to auto-authenticate user %s for MCP instance %s: %s", user_id, instance_id, e)
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail="Failed to save credentials.",
        ) from e

    return {
        "status": "success",
        "message": "MCP server authenticated successfully using shared credentials.",
        "isAuthenticated": True,
    }


@router.post("/instances/{instance_id}/reauthenticate", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
@inject
async def reauthenticate_instance(
    instance_id: str,
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service]),
) -> dict[str, Any]:
    """Clear credentials for an MCP server instance, requiring re-authentication."""
    user_context = _get_user_context(request)
    user_id = user_context["user_id"]
    auth_path = get_mcp_server_config_path(instance_id, user_id)
    try:
        await config_service.delete_config(auth_path)
    except Exception as e:
        logger.error("Failed to reauthenticate MCP server instance %s: %s", instance_id, e)
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail="Failed to clear credentials."
        ) from e

    tokens_path = get_mcp_server_oauth_tokens_path(instance_id, user_id)
    try:
        await config_service.delete_config(tokens_path)
    except Exception:
        pass

    # Cancel any pending token refresh task for this user's credentials
    try:
        from app.connectors.core.base.token_service.startup_service import startup_service
        mcp_refresh_service = startup_service.get_mcp_token_refresh_service()
        if mcp_refresh_service:
            mcp_refresh_service.cancel_refresh_task(auth_path)
    except Exception:
        pass

    return {"status": "success", "message": "Credentials cleared. Please re-authenticate."}


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
    """
    Generate an OAuth authorization URL for the user to authorize this MCP server.

    The frontend provides its baseUrl (window.location.origin). This endpoint
    builds the redirect_uri from baseUrl + template.redirect_uri (same pattern
    as connector OAuth), constructs the authorization URL, and returns it.
    """
    user_context = _get_user_context(request)
    org_id = user_context["org_id"]
    user_id = user_context["user_id"]

    instances = await _load_instances(config_service, org_id)
    instance = next((i for i in instances if i.get("_id") == instance_id), None)
    if not instance:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="MCP server instance not found.")

    auth_mode = instance.get("authMode", "none")
    if auth_mode != "oauth":
        raise HTTPException(
            status_code=HttpStatusCode.BAD_REQUEST.value,
            detail="This MCP server does not use OAuth authentication."
        )

    server_type = instance.get("serverType", "")
    registry = _get_registry(request)
    template = registry.get_template(server_type)
    if not template or not template.auth.oauth2_authorization_url:
        raise HTTPException(
            status_code=HttpStatusCode.BAD_REQUEST.value,
            detail=f"No OAuth configuration found for server type '{server_type}'."
        )

    oauth_config = await _load_oauth_client_config(instance_id, config_service)
    if not oauth_config:
        raise HTTPException(
            status_code=HttpStatusCode.BAD_REQUEST.value,
            detail=(
                "OAuth client credentials (Client ID / Client Secret) have not been "
                "configured for this MCP server instance. An administrator must provide "
                "them when creating or updating the instance."
            ),
        )

    # Build redirect URI from base_url + template path (like connector OAuth)
    if not base_url:
        try:
            endpoints = await config_service.get_config("/services/endpoints", use_cache=False)
            base_url = endpoints.get("frontend", {}).get("publicEndpoint", "http://localhost:3001")
        except Exception:
            base_url = "http://localhost:3001"

    redirect_uri_path = template.redirect_uri or f"mcp-servers/oauth/callback/{server_type}"
    redirect_uri = f"{base_url.rstrip('/')}/{redirect_uri_path}"

    state = secrets.token_urlsafe(32)
    state_data = {
        "state": state,
        "instanceId": instance_id,
        "serverType": server_type,
        "userId": user_id,
        "redirectUri": redirect_uri,
        "createdAt": int(time.time()),
    }
    state_path = f"/services/mcp-servers/{instance_id}/{user_id}/oauth-state"
    await config_service.set_config(state_path, state_data)

    params = {
        "response_type": "code",
        "client_id": oauth_config["clientId"],
        "redirect_uri": redirect_uri,
        "scope": " ".join(template.auth.oauth2_scopes),
        "state": state,
    }

    if "audience" in oauth_config:
        params["audience"] = oauth_config["audience"]

    if template.auth.oauth2_scopes and "offline_access" in template.auth.oauth2_scopes:
        params["prompt"] = "consent"

    authorization_url = f"{template.auth.oauth2_authorization_url}?{urlencode(params)}"

    return {
        "status": "success",
        "authorizationUrl": authorization_url,
        "state": state,
    }


@router.post("/instances/{instance_id}/oauth/callback", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
@inject
async def oauth_callback(
    instance_id: str,
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service]),
) -> dict[str, Any]:
    """
    Exchange an OAuth authorization code for access and refresh tokens.

    The frontend receives the code and state from the OAuth provider's redirect
    and POSTs them here. This endpoint validates the state, exchanges the code
    for tokens, and persists them.
    """
    user_context = _get_user_context(request)
    org_id = user_context["org_id"]
    user_id = user_context["user_id"]

    body_data = await request.body()
    body = _parse_request_json(request, body_data)
    code = body.get("code", "")
    state = body.get("state", "")
    redirect_uri = body.get("redirectUri", "")

    if not code:
        raise HTTPException(status_code=HttpStatusCode.BAD_REQUEST.value, detail="Authorization code is required.")
    if not state:
        raise HTTPException(status_code=HttpStatusCode.BAD_REQUEST.value, detail="State parameter is required.")

    state_path = f"/services/mcp-servers/{instance_id}/{user_id}/oauth-state"
    try:
        saved_state = await config_service.get_config(state_path, default=None)
    except Exception:
        saved_state = None

    if not saved_state or saved_state.get("state") != state:
        raise HTTPException(
            status_code=HttpStatusCode.BAD_REQUEST.value,
            detail="Invalid or expired OAuth state. Please restart the authorization flow."
        )

    if int(time.time()) - saved_state.get("createdAt", 0) > 600:
        raise HTTPException(
            status_code=HttpStatusCode.BAD_REQUEST.value,
            detail="OAuth state has expired (10 min). Please restart the authorization flow."
        )

    try:
        await config_service.delete_config(state_path)
    except Exception:
        pass

    instances = await _load_instances(config_service, org_id)
    instance = next((i for i in instances if i.get("_id") == instance_id), None)
    if not instance:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="MCP server instance not found.")

    server_type = instance.get("serverType", "")
    registry = _get_registry(request)
    template = registry.get_template(server_type)
    if not template:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="Template not found.")

    oauth_config = await _load_oauth_client_config(instance_id, config_service)
    if not oauth_config:
        raise HTTPException(
            status_code=HttpStatusCode.BAD_REQUEST.value,
            detail="OAuth client credentials not configured for this instance."
        )

    token_url = template.auth.oauth2_token_url
    if not token_url:
        raise HTTPException(status_code=HttpStatusCode.BAD_REQUEST.value, detail="No token URL configured for this server type.")

    if not redirect_uri:
        redirect_uri = saved_state.get("redirectUri", "")

    token_payload = {
        "grant_type": "authorization_code",
        "client_id": oauth_config["clientId"],
        "client_secret": oauth_config.get("clientSecret", ""),
        "code": code,
        "redirect_uri": redirect_uri,
    }

    async with httpx.AsyncClient(timeout=30.0) as http_client:
        try:
            resp = await http_client.post(
                token_url,
                data=token_payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except httpx.RequestError as e:
            logger.error("OAuth token exchange network error for %s: %s", server_type, e)
            raise HTTPException(
                status_code=HttpStatusCode.BAD_GATEWAY.value,
                detail=f"Failed to reach OAuth token endpoint: {e}"
            ) from e

    if resp.status_code != 200:
        error_detail = resp.text[:500]
        logger.error("OAuth token exchange failed for %s: %d %s", server_type, resp.status_code, error_detail)
        raise HTTPException(
            status_code=HttpStatusCode.BAD_GATEWAY.value,
            detail=f"OAuth token exchange failed (HTTP {resp.status_code}): {error_detail}"
        )

    token_data = resp.json()
    access_token = token_data.get("access_token", "")
    refresh_token = token_data.get("refresh_token", "")
    expires_in = token_data.get("expires_in", 3600)
    scope = token_data.get("scope", "")

    if not access_token:
        raise HTTPException(
            status_code=HttpStatusCode.BAD_GATEWAY.value,
            detail="OAuth provider returned empty access token."
        )

    now = get_epoch_timestamp_in_ms()
    expires_at = int(time.time()) + int(expires_in)

    tokens = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": token_data.get("token_type", "Bearer"),
        "expires_at": expires_at,
        "scope": scope,
    }
    await _save_oauth_tokens(instance_id, user_id, tokens, config_service)

    user_auth: dict[str, Any] = {
        "isAuthenticated": True,
        "authMode": "oauth",
        "instanceId": instance_id,
        "serverType": server_type,
        "transport": instance.get("transport", "streamable_http"),
        "command": instance.get("command", ""),
        "args": instance.get("args", []),
        "url": instance.get("url", ""),
        "requiredEnv": instance.get("requiredEnv", []),
        "auth": {},
        "credentials": {
            "access_token": access_token,
            "token_type": tokens["token_type"],
            "expires_at": expires_at,
            "scope": scope,
        },
        "updatedAt": now,
        "updatedBy": user_id,
    }

    auth_path = get_mcp_server_config_path(instance_id, user_id)
    await config_service.set_config(auth_path, user_auth)

    # Schedule proactive token refresh before expiry (mirrors toolset OAuth callback behaviour)
    if tokens.get("refresh_token"):
        try:
            from app.connectors.core.base.token_service.startup_service import startup_service
            mcp_refresh_service = startup_service.get_mcp_token_refresh_service()
            if mcp_refresh_service:
                await mcp_refresh_service.schedule_token_refresh(
                    auth_path, instance_id, user_id, expires_at
                )
        except Exception as _sched_err:
            logger.warning(
                "Failed to schedule MCP token refresh after callback for instance %s: %s",
                instance_id, _sched_err,
            )

    return {
        "status": "success",
        "message": "OAuth authentication completed successfully.",
        "isAuthenticated": True,
        "expiresAt": expires_at,
        "scope": scope,
    }


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
    """
    Global OAuth callback — mirrors the connector OAuth pattern.

    The OAuth provider redirects the browser to a frontend callback page.
    That page calls this endpoint with code, state, and baseUrl as query params.
    The instanceId is extracted from the persisted state data.
    """
    user_context = _get_user_context(request)
    org_id = user_context["org_id"]
    user_id = user_context["user_id"]

    if error:
        raise HTTPException(
            status_code=HttpStatusCode.BAD_REQUEST.value,
            detail=f"OAuth provider returned an error: {error}"
        )

    if not code:
        raise HTTPException(status_code=HttpStatusCode.BAD_REQUEST.value, detail="Authorization code is required.")
    if not state:
        raise HTTPException(status_code=HttpStatusCode.BAD_REQUEST.value, detail="State parameter is required.")

    # Look up state across all MCP server instances for this user
    instances = await _load_instances(config_service, org_id)
    saved_state: dict[str, Any] | None = None
    instance_id: str = ""

    for inst in instances:
        iid = inst.get("_id", "")
        state_path = f"/services/mcp-servers/{iid}/{user_id}/oauth-state"
        try:
            candidate = await config_service.get_config(state_path, default=None)
            if isinstance(candidate, dict) and candidate.get("state") == state:
                saved_state = candidate
                instance_id = iid
                break
        except Exception:
            continue

    if not saved_state or not instance_id:
        raise HTTPException(
            status_code=HttpStatusCode.BAD_REQUEST.value,
            detail="Invalid or expired OAuth state. Please restart the authorization flow."
        )

    if int(time.time()) - saved_state.get("createdAt", 0) > 600:
        raise HTTPException(
            status_code=HttpStatusCode.BAD_REQUEST.value,
            detail="OAuth state has expired (10 min). Please restart the authorization flow."
        )

    state_path = f"/services/mcp-servers/{instance_id}/{user_id}/oauth-state"
    try:
        await config_service.delete_config(state_path)
    except Exception:
        pass

    instance = next((i for i in instances if i.get("_id") == instance_id), None)
    if not instance:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="MCP server instance not found.")

    server_type = instance.get("serverType", "")
    registry = _get_registry(request)
    template = registry.get_template(server_type)
    if not template:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="Template not found.")

    oauth_config = await _load_oauth_client_config(instance_id, config_service)
    if not oauth_config:
        raise HTTPException(
            status_code=HttpStatusCode.BAD_REQUEST.value,
            detail="OAuth client credentials not configured for this instance."
        )

    token_url = template.auth.oauth2_token_url
    if not token_url:
        raise HTTPException(status_code=HttpStatusCode.BAD_REQUEST.value, detail="No token URL configured for this server type.")

    redirect_uri = saved_state.get("redirectUri", "")

    token_payload = {
        "grant_type": "authorization_code",
        "client_id": oauth_config["clientId"],
        "client_secret": oauth_config.get("clientSecret", ""),
        "code": code,
        "redirect_uri": redirect_uri,
    }

    async with httpx.AsyncClient(timeout=30.0) as http_client:
        try:
            resp = await http_client.post(
                token_url,
                data=token_payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except httpx.RequestError as e:
            logger.error("OAuth token exchange network error for %s: %s", server_type, e)
            raise HTTPException(
                status_code=HttpStatusCode.BAD_GATEWAY.value,
                detail=f"Failed to reach OAuth token endpoint: {e}"
            ) from e

    if resp.status_code != 200:
        error_detail = resp.text[:500]
        logger.error("OAuth token exchange failed for %s: %d %s", server_type, resp.status_code, error_detail)
        raise HTTPException(
            status_code=HttpStatusCode.BAD_GATEWAY.value,
            detail=f"OAuth token exchange failed (HTTP {resp.status_code}): {error_detail}"
        )

    token_data = resp.json()
    access_token = token_data.get("access_token", "")
    refresh_token = token_data.get("refresh_token", "")
    expires_in = token_data.get("expires_in", 3600)
    scope = token_data.get("scope", "")

    if not access_token:
        raise HTTPException(
            status_code=HttpStatusCode.BAD_GATEWAY.value,
            detail="OAuth provider returned empty access token."
        )

    now = get_epoch_timestamp_in_ms()
    expires_at = int(time.time()) + int(expires_in)

    tokens = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": token_data.get("token_type", "Bearer"),
        "expires_at": expires_at,
        "scope": scope,
    }
    await _save_oauth_tokens(instance_id, user_id, tokens, config_service)

    user_auth: dict[str, Any] = {
        "isAuthenticated": True,
        "authMode": "oauth",
        "instanceId": instance_id,
        "serverType": server_type,
        "transport": instance.get("transport", "streamable_http"),
        "command": instance.get("command", ""),
        "args": instance.get("args", []),
        "url": instance.get("url", ""),
        "requiredEnv": instance.get("requiredEnv", []),
        "auth": {},
        "credentials": {
            "access_token": access_token,
            "token_type": tokens["token_type"],
            "expires_at": expires_at,
            "scope": scope,
        },
        "updatedAt": now,
        "updatedBy": user_id,
    }

    auth_path = get_mcp_server_config_path(instance_id, user_id)
    await config_service.set_config(auth_path, user_auth)

    # Schedule proactive token refresh before expiry (mirrors toolset OAuth callback behaviour)
    if tokens.get("refresh_token"):
        try:
            from app.connectors.core.base.token_service.startup_service import startup_service
            mcp_refresh_service = startup_service.get_mcp_token_refresh_service()
            if mcp_refresh_service:
                await mcp_refresh_service.schedule_token_refresh(
                    auth_path, instance_id, user_id, expires_at
                )
        except Exception as _sched_err:
            logger.warning(
                "Failed to schedule MCP token refresh after global callback for instance %s: %s",
                instance_id, _sched_err,
            )

    # Return redirect URL like connectors do
    redirect_url = ""
    if base_url:
        redirect_url = f"{base_url.rstrip('/')}/dashboard/mcp-servers?tab=my-servers"

    return {
        "success": True,
        "redirectUrl": redirect_url,
        "message": "OAuth authentication completed successfully.",
        "isAuthenticated": True,
        "expiresAt": expires_at,
        "scope": scope,
    }


@router.post("/instances/{instance_id}/oauth/refresh", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
@inject
async def oauth_refresh(
    instance_id: str,
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service]),
) -> dict[str, Any]:
    """
    Refresh an expired OAuth access token using the stored refresh token.
    """
    user_context = _get_user_context(request)
    org_id = user_context["org_id"]
    user_id = user_context["user_id"]

    existing_tokens = await _load_oauth_tokens(instance_id, user_id, config_service)
    if not existing_tokens or not existing_tokens.get("refresh_token"):
        raise HTTPException(
            status_code=HttpStatusCode.BAD_REQUEST.value,
            detail="No refresh token available. Please re-authorize."
        )

    instances = await _load_instances(config_service, org_id)
    instance = next((i for i in instances if i.get("_id") == instance_id), None)
    if not instance:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="MCP server instance not found.")

    server_type = instance.get("serverType", "")
    registry = _get_registry(request)
    template = registry.get_template(server_type)
    if not template or not template.auth.oauth2_token_url:
        raise HTTPException(status_code=HttpStatusCode.BAD_REQUEST.value, detail="No OAuth configuration for this server type.")

    oauth_config = await _load_oauth_client_config(instance_id, config_service)
    if not oauth_config:
        raise HTTPException(status_code=HttpStatusCode.BAD_REQUEST.value, detail="OAuth client credentials not configured for this instance.")

    refresh_payload = {
        "grant_type": "refresh_token",
        "client_id": oauth_config["clientId"],
        "client_secret": oauth_config.get("clientSecret", ""),
        "refresh_token": existing_tokens["refresh_token"],
    }

    async with httpx.AsyncClient(timeout=30.0) as http_client:
        try:
            resp = await http_client.post(
                template.auth.oauth2_token_url,
                data=refresh_payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except httpx.RequestError as e:
            logger.error("OAuth token refresh network error for %s: %s", server_type, e)
            raise HTTPException(
                status_code=HttpStatusCode.BAD_GATEWAY.value,
                detail=f"Failed to reach OAuth token endpoint: {e}"
            ) from e

    if resp.status_code != 200:
        error_detail = resp.text[:500]
        logger.error("OAuth token refresh failed for %s: %d %s", server_type, resp.status_code, error_detail)
        raise HTTPException(
            status_code=HttpStatusCode.BAD_GATEWAY.value,
            detail=f"Token refresh failed (HTTP {resp.status_code}). You may need to re-authorize."
        )

    token_data = resp.json()
    access_token = token_data.get("access_token", "")
    new_refresh_token = token_data.get("refresh_token", existing_tokens["refresh_token"])
    expires_in = token_data.get("expires_in", 3600)
    scope = token_data.get("scope", existing_tokens.get("scope", ""))

    if not access_token:
        raise HTTPException(status_code=HttpStatusCode.BAD_GATEWAY.value, detail="Provider returned empty access token on refresh.")

    expires_at = int(time.time()) + int(expires_in)
    now = get_epoch_timestamp_in_ms()

    tokens = {
        "access_token": access_token,
        "refresh_token": new_refresh_token,
        "token_type": token_data.get("token_type", "Bearer"),
        "expires_at": expires_at,
        "scope": scope,
    }
    await _save_oauth_tokens(instance_id, user_id, tokens, config_service)

    auth_path = get_mcp_server_config_path(instance_id, user_id)
    try:
        user_auth = await config_service.get_config(auth_path, default=None)
        if isinstance(user_auth, dict):
            user_auth["credentials"]["access_token"] = access_token
            user_auth["credentials"]["expires_at"] = expires_at
            user_auth["credentials"]["scope"] = scope
            user_auth["updatedAt"] = now
            await config_service.set_config(auth_path, user_auth)
    except Exception as e:
        logger.warning("Failed to update auth record after token refresh: %s", e)

    # Re-schedule the next proactive refresh with the new expiry
    try:
        from app.connectors.core.base.token_service.startup_service import startup_service
        mcp_refresh_service = startup_service.get_mcp_token_refresh_service()
        if mcp_refresh_service:
            await mcp_refresh_service.schedule_token_refresh(
                auth_path, instance_id, user_id, expires_at
            )
    except Exception as _sched_err:
        logger.warning(
            "Failed to re-schedule MCP token refresh after manual refresh for instance %s: %s",
            instance_id, _sched_err,
        )

    return {
        "status": "success",
        "message": "Token refreshed successfully.",
        "isAuthenticated": True,
        "expiresAt": expires_at,
    }


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
    user_context = _get_user_context(request)
    is_admin = await _check_user_is_admin(user_context["user_id"], request, config_service)
    if not is_admin:
        raise HTTPException(status_code=HttpStatusCode.FORBIDDEN.value, detail="Admin access required.")

    config = await _load_oauth_client_config(instance_id, config_service)
    if not config:
        return {"status": "success", "configured": False, "config": None}

    masked = {**config}
    if masked.get("clientSecret"):
        secret = masked["clientSecret"]
        masked["clientSecret"] = "***" + secret[-4:] if len(secret) > 4 else "****"

    return {"status": "success", "configured": True, "config": masked}


@router.put("/instances/{instance_id}/oauth-config", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
@inject
async def set_instance_oauth_config(
    instance_id: str,
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service]),
) -> dict[str, Any]:
    """Set or update OAuth client credentials for an instance (admin only)."""
    user_context = _get_user_context(request)
    is_admin = await _check_user_is_admin(user_context["user_id"], request, config_service)
    if not is_admin:
        raise HTTPException(status_code=HttpStatusCode.FORBIDDEN.value, detail="Admin access required.")

    body_data = await request.body()
    body = _parse_request_json(request, body_data)

    client_id = body.get("clientId", "").strip()
    client_secret = body.get("clientSecret", "").strip()
    if not client_id:
        raise HTTPException(status_code=HttpStatusCode.BAD_REQUEST.value, detail="clientId is required.")

    await _save_oauth_client_config(
        instance_id, client_id, client_secret,
        config_service, updated_by=user_context["user_id"],
    )

    return {"status": "success", "message": "OAuth client configuration saved."}


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
    """
    Get all MCP servers for the current user: configured instances merged
    with auth status, optionally including unconfigured catalog entries.
    """
    user_context = _get_user_context(request)
    org_id = user_context["org_id"]
    user_id = user_context["user_id"]

    instances = await _load_instances(config_service, org_id)
    merged: list[dict[str, Any]] = []

    # Collect authenticated instances for parallel tool discovery
    discovery_tasks: list[tuple[int, asyncio.Task]] = []

    for inst in instances:
        if not inst.get("enabled", True):
            continue

        instance_id = inst.get("_id", "")
        user_auth = await _fetch_user_auth(instance_id, user_id, config_service)
        is_authenticated = bool(user_auth and user_auth.get("isAuthenticated"))

        item: dict[str, Any] = {
            "instanceId": instance_id,
            "instanceName": inst.get("instanceName", ""),
            "displayName": inst.get("displayName", inst.get("instanceName", "")),
            "serverType": inst.get("serverType", "custom"),
            "description": inst.get("description", ""),
            "transport": inst.get("transport", "stdio"),
            "authMode": inst.get("authMode", "none"),
            "supportedAuthTypes": inst.get("supportedAuthTypes", []),
            "iconPath": inst.get("iconPath", ""),
            "isConfigured": True,
            "isAuthenticated": is_authenticated,
            "isFromRegistry": False,
            "tools": [],
            "toolCount": 0,
        }

        if is_authenticated and user_auth:
            creds = user_auth.get("credentials", {})
            if creds.get("expires_at"):
                item["oauthExpiresAt"] = creds["expires_at"]

            idx = len(merged)
            task = asyncio.create_task(
                _discover_tools_for_instance(inst, user_auth)
            )
            discovery_tasks.append((idx, task))

        if inst.get("authMode") == "oauth":
            oauth_cfg = await _load_oauth_client_config(instance_id, config_service)
            item["hasOAuthClientConfig"] = bool(oauth_cfg)

        merged.append(item)

    # Await all tool discovery tasks in parallel
    if discovery_tasks:
        task_results = await asyncio.gather(
            *(task for _, task in discovery_tasks),
            return_exceptions=True,
        )
        for (idx, _task), result in zip(discovery_tasks, task_results):
            if isinstance(result, list):
                merged[idx]["tools"] = result
                merged[idx]["toolCount"] = len(result)

    if include_registry:
        registry = _get_registry(request)
        configured_types = {inst.get("serverType") for inst in instances}
        catalog_result = registry.list_templates(search=search, page=1, limit=200)
        for tpl_data in catalog_result.get("items", []):
            type_id = tpl_data.get("typeId", "")
            if type_id in configured_types:
                continue
            merged.append({
                "instanceId": "",
                "instanceName": tpl_data.get("displayName", ""),
                "displayName": tpl_data.get("displayName", ""),
                "serverType": type_id,
                "description": tpl_data.get("description", ""),
                "transport": tpl_data.get("transport", "stdio"),
                "authMode": tpl_data.get("authMode", "none"),
                "supportedAuthTypes": tpl_data.get("supportedAuthTypes", []),
                "iconPath": tpl_data.get("iconPath", ""),
                "isConfigured": False,
                "isAuthenticated": False,
                "isFromRegistry": True,
                "tools": [],
                "toolCount": 0,
            })

    if search:
        search_lower = search.lower()
        merged = [
            m for m in merged
            if search_lower in m.get("displayName", "").lower()
            or search_lower in m.get("description", "").lower()
            or search_lower in m.get("serverType", "").lower()
        ]

    if auth_status == "authenticated":
        merged = [m for m in merged if m.get("isAuthenticated")]
    elif auth_status == "unauthenticated":
        merged = [m for m in merged if not m.get("isAuthenticated")]

    total = len(merged)
    total_pages = (total + limit - 1) // limit if limit > 0 else 0
    start = (page - 1) * limit
    paginated = merged[start:start + limit]

    return {
        "status": "success",
        "mcpServers": paginated,
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total,
            "totalPages": total_pages,
            "hasNext": page < total_pages,
            "hasPrev": page > 1,
        },
    }


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
    """
    Discover tools from a connected MCP server (live probe).
    Requires the user to be authenticated against the instance.
    """
    user_context = _get_user_context(request)
    org_id = user_context["org_id"]
    user_id = user_context["user_id"]

    instances = await _load_instances(config_service, org_id)
    instance = next((i for i in instances if i.get("_id") == instance_id), None)
    if not instance:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail=f"MCP server instance '{instance_id}' not found.")

    user_auth = await _fetch_user_auth(instance_id, user_id, config_service)
    if not user_auth or not user_auth.get("isAuthenticated"):
        raise HTTPException(
            status_code=HttpStatusCode.FORBIDDEN.value,
            detail="You must authenticate against this MCP server before discovering tools."
        )

    from app.agents.mcp.client import MCPClientManager
    from app.agents.mcp.discovery import MCPToolDiscovery

    transport_str = instance.get("transport", "stdio")
    try:
        transport = MCPTransport(transport_str)
    except ValueError:
        transport = MCPTransport.STDIO

    auth_mode_str = instance.get("authMode", "none")
    try:
        auth_mode = MCPAuthMode(auth_mode_str)
    except ValueError:
        auth_mode = MCPAuthMode.NONE

    env: dict[str, str] = {}
    headers: dict[str, str] = {}
    auth = user_auth.get("auth", {})
    credentials = user_auth.get("credentials", {})

    if transport == MCPTransport.STDIO:
        token = auth.get("apiToken", "")
        if token:
            required_env = instance.get("requiredEnv", [])
            if required_env:
                env[required_env[0]] = token
    else:
        if auth_mode == MCPAuthMode.OAUTH:
            token = credentials.get("access_token", "") or auth.get("access_token", "")
            if token:
                headers["Authorization"] = f"Bearer {token}"
        else:
            token = auth.get("apiToken", "") or credentials.get("access_token", "")
            if token:
                header_mapping = instance.get("authHeaderMapping", {})
                mapped_header = header_mapping.get("apiToken", "")
                if mapped_header:
                    headers[mapped_header] = token
                else:
                    headers["Authorization"] = f"Bearer {token}"

    config = MCPServerConfig(
        name=instance_id,
        server_type=instance.get("serverType", ""),
        transport=transport,
        command=instance.get("command", ""),
        args=instance.get("args", []),
        env=env,
        url=instance.get("url", ""),
        headers=headers,
        auth_mode=auth_mode,
    )

    manager = MCPClientManager()
    discovery = MCPToolDiscovery(manager)

    try:
        tools = await discovery.discover_tools(instance_id, config)
    finally:
        await manager.disconnect_all()

    return {
        "status": "success",
        "tools": [
            {
                "name": t.name,
                "namespacedName": t.namespaced_name,
                "description": t.description,
                "inputSchema": t.input_schema,
            }
            for t in tools
        ],
        "toolCount": len(tools),
    }


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
    user_context = _get_user_context(request)
    org_id = user_context["org_id"]

    instances = await _load_instances(config_service, org_id)
    merged: list[dict[str, Any]] = []

    discovery_tasks: list[tuple[int, asyncio.Task]] = []

    for inst in instances:
        if not inst.get("enabled", True):
            continue

        instance_id = inst.get("_id", "")
        agent_auth = await _fetch_user_auth(instance_id, agent_key, config_service)
        is_authenticated = bool(agent_auth and agent_auth.get("isAuthenticated"))

        item: dict[str, Any] = {
            "instanceId": instance_id,
            "instanceName": inst.get("instanceName", ""),
            "displayName": inst.get("displayName", inst.get("instanceName", "")),
            "serverType": inst.get("serverType", "custom"),
            "description": inst.get("description", ""),
            "transport": inst.get("transport", "stdio"),
            "authMode": inst.get("authMode", "none"),
            "supportedAuthTypes": inst.get("supportedAuthTypes", []),
            "iconPath": inst.get("iconPath", ""),
            "isConfigured": True,
            "isAuthenticated": is_authenticated,
            "agentIsAuthenticated": is_authenticated,
            "isFromRegistry": False,
            "tools": [],
            "toolCount": 0,
        }

        if is_authenticated and agent_auth:
            idx = len(merged)
            task = asyncio.create_task(
                _discover_tools_for_instance(inst, agent_auth)
            )
            discovery_tasks.append((idx, task))

        merged.append(item)

    # Await all tool discovery tasks in parallel
    if discovery_tasks:
        task_results = await asyncio.gather(
            *(task for _, task in discovery_tasks),
            return_exceptions=True,
        )
        for (idx, _task), result in zip(discovery_tasks, task_results):
            if isinstance(result, list):
                merged[idx]["tools"] = result
                merged[idx]["toolCount"] = len(result)

    if include_registry:
        registry = _get_registry(request)
        configured_types = {inst.get("serverType") for inst in instances}
        catalog_result = registry.list_templates(search=search, page=1, limit=200)
        for tpl_data in catalog_result.get("items", []):
            type_id = tpl_data.get("typeId", "")
            if type_id in configured_types:
                continue
            merged.append({
                "instanceId": "",
                "instanceName": tpl_data.get("displayName", ""),
                "displayName": tpl_data.get("displayName", ""),
                "serverType": type_id,
                "description": tpl_data.get("description", ""),
                "transport": tpl_data.get("transport", "stdio"),
                "authMode": tpl_data.get("authMode", "none"),
                "supportedAuthTypes": tpl_data.get("supportedAuthTypes", []),
                "iconPath": tpl_data.get("iconPath", ""),
                "isConfigured": False,
                "isAuthenticated": False,
                "agentIsAuthenticated": False,
                "isFromRegistry": True,
                "tools": [],
                "toolCount": 0,
            })

    if search:
        search_lower = search.lower()
        merged = [
            m for m in merged
            if search_lower in m.get("displayName", "").lower()
            or search_lower in m.get("description", "").lower()
            or search_lower in m.get("serverType", "").lower()
        ]

    total = len(merged)
    total_pages = (total + limit - 1) // limit if limit > 0 else 0
    start = (page - 1) * limit
    paginated = merged[start:start + limit]

    return {
        "status": "success",
        "mcpServers": paginated,
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total,
            "totalPages": total_pages,
            "hasNext": page < total_pages,
            "hasPrev": page > 1,
        },
    }


@router.post("/agents/{agent_key}/instances/{instance_id}/authenticate", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
@inject
async def authenticate_agent_instance(
    agent_key: str,
    instance_id: str,
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service]),
) -> dict[str, Any]:
    """Authenticate an MCP server instance for a service-account agent."""
    user_context = _get_user_context(request)
    org_id = user_context["org_id"]
    user_id = user_context["user_id"]

    instances = await _load_instances(config_service, org_id)
    instance = next((i for i in instances if i.get("_id") == instance_id), None)
    if not instance:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail=f"MCP server instance '{instance_id}' not found.")

    body_data = await request.body()
    body = _parse_request_json(request, body_data)
    auth = body.get("auth", {})

    now = get_epoch_timestamp_in_ms()
    agent_auth: dict[str, Any] = {
        "isAuthenticated": True,
        "authMode": instance.get("authMode", "none"),
        "instanceId": instance_id,
        "serverType": instance.get("serverType"),
        "transport": instance.get("transport", "stdio"),
        "command": instance.get("command", ""),
        "args": instance.get("args", []),
        "url": instance.get("url", ""),
        "requiredEnv": instance.get("requiredEnv", []),
        "auth": auth,
        "credentials": {},
        "updatedAt": now,
        "updatedBy": user_id,
    }

    auth_path = get_mcp_server_config_path(instance_id, agent_key)
    try:
        await config_service.set_config(auth_path, agent_auth)
    except Exception as e:
        logger.error("Failed to save agent MCP server auth for %s/%s: %s", agent_key, instance_id, e)
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail="Failed to save agent credentials."
        ) from e

    return {"status": "success", "message": "Agent MCP server authenticated successfully.", "isAuthenticated": True}


@router.put("/agents/{agent_key}/instances/{instance_id}/credentials", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
@inject
async def update_agent_credentials(
    agent_key: str,
    instance_id: str,
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service]),
) -> dict[str, Any]:
    """Update agent credentials for an MCP server instance."""
    user_context = _get_user_context(request)
    body_data = await request.body()
    body = _parse_request_json(request, body_data)
    auth = body.get("auth", {})
    if not auth:
        raise HTTPException(status_code=HttpStatusCode.BAD_REQUEST.value, detail="Credentials are required.")

    auth_path = get_mcp_server_config_path(instance_id, agent_key)
    try:
        existing = await config_service.get_config(auth_path, default=None)
        if not existing or not isinstance(existing, dict):
            raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="No existing agent credentials found.")
        existing["auth"] = auth
        existing["updatedAt"] = get_epoch_timestamp_in_ms()
        existing["updatedBy"] = user_context["user_id"]
        await config_service.set_config(auth_path, existing)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to update agent credentials for %s/%s: %s", agent_key, instance_id, e)
        raise HTTPException(status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail="Failed to update credentials.") from e

    return {"status": "success", "message": "Agent credentials updated successfully."}


@router.delete("/agents/{agent_key}/instances/{instance_id}/credentials", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
@inject
async def remove_agent_credentials(
    agent_key: str,
    instance_id: str,
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service]),
) -> dict[str, Any]:
    """Remove agent credentials for an MCP server instance."""
    auth_path = get_mcp_server_config_path(instance_id, agent_key)
    try:
        await config_service.delete_config(auth_path)
    except Exception as e:
        logger.warning("Failed to delete agent MCP server auth for %s/%s: %s", agent_key, instance_id, e)
    return {"status": "success", "message": "Agent credentials removed successfully."}
