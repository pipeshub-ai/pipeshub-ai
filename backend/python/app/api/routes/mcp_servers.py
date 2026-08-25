"""MCP Servers API Routes — registry, org-scoped instances, auth, OAuth+DCR, and
live tool discovery for Model Context Protocol servers.

Architecture:
  - Catalog: in-memory templates (`app.agents.mcp.registry`), no secrets.
  - Admin creates "instances" (org-wide, no secrets): /services/mcp/instances/{instanceId}
  - Users authenticate against instances: POST /instances/{id}/authenticate (or OAuth)
  - Per-user/admin credentials: /services/mcp/credentials/{instanceId}/{userId}
  - GET /my-mcp-servers returns a merged view (instances + current user's auth status + tools)

Secrets are never written to plain dicts returned to the caller (OAuth client secrets and
admin-shared credentials are masked); the underlying `ConfigurationService` store encrypts
values at rest exactly like the toolsets/connectors credential paths.
"""
import asyncio
import logging
import uuid
from typing import Any, Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.agents.constants.mcp_server_constants import (
    OAUTH_STATE_TTL_SECONDS,
    get_mcp_credentials_path,
    get_mcp_dcr_client_path,
    get_mcp_instance_credentials_prefix,
    get_mcp_instance_path,
    get_mcp_oauth_client_config_path,
    get_mcp_oauth_state_path,
    get_mcp_oauth_states_prefix,
    get_mcp_shared_dcr_client_path,
)
from app.agents.mcp import dcr as dcr_module
from app.agents.mcp import oauth_client as oauth_client_module
from app.agents.mcp import service as mcp_service
from app.agents.mcp import token_refresh as mcp_token_refresh
from app.agents.mcp.client import MCPConnectionError
from app.agents.mcp.discovery import discover_tools
from app.agents.mcp.models import DiscoveredOAuthMetadata, MCPAuthMode, MCPServerInstanceConfig, MCPTransport
from app.agents.mcp.registry import MCPRegistry
from app.api.middlewares.auth import require_scopes
from app.config.configuration_service import ConfigurationService
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import DefaultEndpoints, OAuthScopes
from app.edition_config import (
    build_schedule_refresh_kwargs,
    forbid_inherited_mcp_mutation,
    get_mcp_instance_resolved,
    load_mcp_instances,
    mask_mcp_instance_for_response,
    resolve_instance_owner_config_service,
    resolve_mcp_instances_with_inheritance,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms

logger = logging.getLogger(__name__)
DEFAULT_TOOLS_DISCOVERY_TIMEOUT_SECONDS = 8.0
DEFAULT_ENDPOINTS_PATH = "/services/endpoints"
ADMIN_CHECK_TIMEOUT_SECONDS = 5.0


# ============================================================================
# Request bodies
# ============================================================================


class AuthenticateRequest(BaseModel):
    """Non-OAuth credential payload — fields depend on the instance's authMode."""

    model_config = ConfigDict(populate_by_name=True)

    api_token: Optional[str] = Field(default=None, alias="apiToken")
    header_name: Optional[str] = Field(default=None, alias="headerName")
    header_value: Optional[str] = Field(default=None, alias="headerValue")
    # STDIO multi-env (allowlisted against instance requiredEnv/optionalEnv), e.g. Slack.
    env: Optional[dict[str, str]] = None


class OAuthClientConfigRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    client_id: str = Field(alias="clientId")
    client_secret: str = Field(alias="clientSecret")


class OAuthDiscoveryRequest(BaseModel):
    """Body for `POST /oauth/discover` — probe a server URL for live OAuth metadata before
    an instance exists (needed by the config panel in create mode)."""

    url: str


# ============================================================================
# Helpers — user context, admin gate, registry, storage
# ============================================================================


def _get_user_context(request: Request) -> dict[str, Any]:
    """Extract and validate user context from request (same convention as toolsets)."""
    user = getattr(request.state, "user", {}) or {}
    user_id = user.get("userId") or request.headers.get("X-User-Id")
    org_id = user.get("orgId") or request.headers.get("X-Organization-Id")

    if not user_id:
        raise HTTPException(
            status_code=HttpStatusCode.UNAUTHORIZED.value,
            detail="Authentication required. Please provide valid user credentials.",
        )

    return {"user_id": user_id, "org_id": org_id}


async def _check_user_is_admin(
    user_id: str,
    request: Request,
    config_service: ConfigurationService,
) -> bool:
    """Check admin status by calling the Node.js CM backend, mirroring toolsets' `_check_user_is_admin`.

    Python never trusts a client- or proxy-supplied admin flag; it independently verifies via
    GET /api/v1/users/{userId}/adminCheck using the caller's own auth headers.
    """
    try:
        try:
            endpoints = await config_service.get_config(DEFAULT_ENDPOINTS_PATH, use_cache=False)
            nodejs_url = (
                endpoints.get("nodejs", {}).get("endpoint") if isinstance(endpoints, dict) else None
            ) or DefaultEndpoints.NODEJS_ENDPOINT.value
        except Exception:
            nodejs_url = DefaultEndpoints.NODEJS_ENDPOINT.value

        auth_headers: dict[str, str] = {}
        for header_name in ("authorization", "x-organization-id", "cookie"):
            val = request.headers.get(header_name)
            if val:
                auth_headers[header_name] = val

        async with httpx.AsyncClient(timeout=ADMIN_CHECK_TIMEOUT_SECONDS) as client:
            resp = await client.get(
                f"{nodejs_url}/api/v1/users/{user_id}/adminCheck",
                headers=auth_headers,
            )
            return resp.status_code == HttpStatusCode.OK.value
    except Exception as e:
        logger.warning(f"Admin check via REST API failed for user {user_id}: {e}. Defaulting to non-admin.")
        return False


def _get_config_service(request: Request) -> ConfigurationService:
    """Configuration service is set on `app.state` at connectors-service startup —
    no DI wiring needed for this module (same pattern as `_get_registry`/`_get_graph_provider`
    for the toolset registry / graph provider).
    """
    config_service = getattr(request.app.state, "config_service", None)
    if not config_service:
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail="Configuration service not initialized. Please contact system administrator.",
        )
    return config_service


async def _require_mcp_enabled(request: Request) -> None:
    """FastAPI dependency — rejects the request with 403 when the ``ENABLE_MCP``
    platform feature flag is disabled for the calling org.  Applied to every MCP
    endpoint so that visibility, auth, and runtime are gated consistently."""
    from app.agents.mcp.service import is_mcp_enabled

    if not await is_mcp_enabled(_get_config_service(request)):
        raise HTTPException(
            status_code=HttpStatusCode.FORBIDDEN.value,
            detail="MCP servers are disabled for this organization.",
        )


router = APIRouter(
    prefix="/api/v1/mcp-servers",
    tags=["mcp-servers"],
    dependencies=[Depends(_require_mcp_enabled)],
)


def _get_mcp_registry(request: Request) -> MCPRegistry:
    registry = getattr(request.app.state, "mcp_registry", None)
    if not registry:
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail="MCP registry not initialized. Please contact system administrator.",
        )
    return registry


async def _get_configured_frontend_base_url(config_service: ConfigurationService) -> str:
    try:
        endpoints = await config_service.get_config(DEFAULT_ENDPOINTS_PATH, use_cache=False)
        if isinstance(endpoints, dict):
            frontend_url = endpoints.get("frontend", {}).get("publicEndpoint")
            if frontend_url:
                return frontend_url.rstrip("/")
    except Exception as e:
        logger.debug(f"Could not resolve configured frontend endpoint: {e}")
    return DefaultEndpoints.FRONTEND_ENDPOINT.value.rstrip("/")


def _resolve_validated_base_url(requested_base_url: Optional[str], configured_base_url: str) -> str:
    """Only trust a client-supplied `baseUrl` if it matches the configured public frontend
    endpoint's scheme+host — otherwise this is an open-redirect vector for the OAuth flow.
    """
    if not requested_base_url:
        return configured_base_url
    try:
        requested = urlparse(requested_base_url)
        configured = urlparse(configured_base_url)
    except Exception:
        return configured_base_url
    if requested.scheme == configured.scheme and requested.netloc == configured.netloc:
        return requested_base_url.rstrip("/")
    logger.warning(
        f"Rejected client-supplied OAuth baseUrl '{requested_base_url}'; "
        f"falling back to configured '{configured_base_url}'"
    )
    return configured_base_url


# ---------------------------------------------------------------------------
# Instance storage
# ---------------------------------------------------------------------------



_load_org_instances = load_mcp_instances
_get_org_instance = get_mcp_instance_resolved


def _validate_instance_config(payload: MCPServerInstanceConfig, registry: MCPRegistry) -> None:
    """Cross-field validation the Pydantic model alone can't express."""
    if payload.type_id:
        template = registry.get_template(payload.type_id)
        if not template:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail=f"Unknown catalog type_id: {payload.type_id}",
            )
        return

    # Custom server — validate transport-specific required fields.
    if payload.transport == MCPTransport.STDIO:
        if not payload.command:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="A custom STDIO MCP server requires a command.",
            )
    else:
        if not payload.url:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="A custom SSE/streamable_http MCP server requires a url.",
            )


def _build_instance_record(
    payload: MCPServerInstanceConfig,
    instance_id: str,
    org_id: str,
    user_id: str,
    registry: MCPRegistry,
    existing: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Merge a validated request body into the stored instance record (camelCase, no secrets)."""
    template = registry.get_template(payload.type_id) if payload.type_id else None
    now_ms = get_epoch_timestamp_in_ms()

    record: dict[str, Any] = {
        "_id": instance_id,
        "orgId": org_id,
        "createdBy": existing.get("createdBy") if existing else user_id,
        "name": payload.name,
        "typeId": payload.type_id,
        "transport": payload.transport.value,
        "authMode": payload.auth_mode.value,
        "useAdminAuth": payload.use_admin_auth,
        "description": payload.description,
        "command": payload.command or (template.command if template else None),
        "args": payload.args or (template.args if template else []),
        "requiredEnv": template.required_env if template else list(payload.required_env or []),
        "optionalEnv": template.optional_env if template else [],
        "url": payload.url or (template.default_url if template else None),
        "headerName": payload.header_name,
        "authorizationUrl": payload.authorization_url or (template.authorization_url if template else None),
        "tokenUrl": payload.token_url or (template.token_url if template else None),
        "scopes": payload.scopes or (template.default_scopes if template else []),
        "isCustom": payload.type_id is None,
        "createdAt": existing.get("createdAt") if existing else now_ms,
        "updatedAt": now_ms,
    }
    return record


# ---------------------------------------------------------------------------
# Effective auth resolution (shared by discovery + the agent-loop runtime) —
# implementations live in `app.agents.mcp.service` (see that module's docstring);
# aliased here under the original private names for route-internal call sites and
# existing tests.
# ---------------------------------------------------------------------------

_resolve_effective_user_auth = mcp_service.resolve_effective_user_auth
_instance_config_model_from_dict = mcp_service.instance_config_from_dict
_credentials_to_discovery_dict = mcp_service.credentials_to_discovery_dict




# ============================================================================
# Catalog
# ============================================================================


@router.get("/catalog", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
async def list_catalog(
    request: Request,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    search: Optional[str] = Query(default=None),
) -> dict[str, Any]:
    _get_user_context(request)
    registry = _get_mcp_registry(request)

    from app.agents.mcp.catalog import paginate, search_templates

    templates = search_templates(registry.list_templates(), search)
    page_items, total = paginate(templates, page, limit)

    return {
        "templates": [t.model_dump(by_alias=True) for t in page_items],
        "total": total,
        "page": page,
        "limit": limit,
    }


@router.get("/catalog/{type_id}", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
async def get_catalog_template(request: Request, type_id: str) -> dict[str, Any]:
    _get_user_context(request)
    registry = _get_mcp_registry(request)
    template = registry.get_template(type_id)
    if not template:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail=f"Unknown MCP server type: {type_id}")
    return template.model_dump(by_alias=True)


# ============================================================================
# Instances (admin-managed)
# ============================================================================


@router.get("/instances", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
async def list_instances(request: Request) -> dict[str, Any]:
    config_service = _get_config_service(request)
    user_context = _get_user_context(request)
    is_admin = await _check_user_is_admin(user_context["user_id"], request, config_service)
    if not is_admin:
        raise HTTPException(status_code=HttpStatusCode.FORBIDDEN.value, detail="Only administrators can list MCP server instances.")

    instances = await resolve_mcp_instances_with_inheritance(config_service)
    for instance in instances:
        owner_svc = await resolve_instance_owner_config_service(instance["_id"], config_service) or config_service
        instance["hasOAuthClientConfig"] = bool(
            await owner_svc.get_config(get_mcp_oauth_client_config_path(instance["_id"]), default=None)
        )
    instances = [mask_mcp_instance_for_response(i) for i in instances]
    return {"instances": instances}


@router.post(
    "/instances",
    status_code=HttpStatusCode.CREATED.value,
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))],
)
async def create_instance(
    request: Request,
    payload: MCPServerInstanceConfig,
) -> dict[str, Any]:
    config_service = _get_config_service(request)
    user_context = _get_user_context(request)
    is_admin = await _check_user_is_admin(user_context["user_id"], request, config_service)
    if not is_admin:
        raise HTTPException(status_code=HttpStatusCode.FORBIDDEN.value, detail="Only administrators can create MCP server instances.")

    registry = _get_mcp_registry(request)
    _validate_instance_config(payload, registry)

    # STDIO instance creation is admin-only (already enforced above) and env vars are built
    # from an explicit allowlist — never pass through arbitrary client-supplied env keys.
    if payload.transport == MCPTransport.STDIO and payload.env:
        template = registry.get_template(payload.type_id) if payload.type_id else None
        if template:
            allowed_keys = set(template.required_env + template.optional_env)
        else:
            # Custom STDIO: allowlist is the required_env names the admin declared (or env keys if unset).
            allowed_keys = set(payload.required_env or payload.env.keys())
        rejected = set(payload.env.keys()) - allowed_keys
        if rejected:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail=f"Env vars not allowed for this MCP server type: {sorted(rejected)}",
            )

    instance_id = str(uuid.uuid4())
    record = _build_instance_record(payload, instance_id, user_context["org_id"], user_context["user_id"], registry)

    success = await config_service.set_config(get_mcp_instance_path(instance_id), record)
    if not success:
        raise HTTPException(status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail="Failed to create MCP server instance.")

    return record


@router.get("/instances/{instance_id}", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
async def get_instance(request: Request, instance_id: str) -> dict[str, Any]:
    config_service = _get_config_service(request)
    user_context = _get_user_context(request)
    is_admin = await _check_user_is_admin(user_context["user_id"], request, config_service)
    if not is_admin:
        raise HTTPException(status_code=HttpStatusCode.FORBIDDEN.value, detail="Only administrators can view MCP server instance details.")

    instance = await _get_org_instance(instance_id, config_service)
    if not instance:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="MCP server instance not found.")

    owner_svc = await resolve_instance_owner_config_service(instance_id, config_service) or config_service
    instance["hasOAuthClientConfig"] = bool(
        await owner_svc.get_config(get_mcp_oauth_client_config_path(instance_id), default=None)
    )
    return instance


@router.put("/instances/{instance_id}", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
async def update_instance(
    request: Request,
    instance_id: str,
    payload: MCPServerInstanceConfig,
) -> dict[str, Any]:
    config_service = _get_config_service(request)
    user_context = _get_user_context(request)
    is_admin = await _check_user_is_admin(user_context["user_id"], request, config_service)
    if not is_admin:
        raise HTTPException(status_code=HttpStatusCode.FORBIDDEN.value, detail="Only administrators can update MCP server instances.")

    existing = await _get_org_instance(instance_id, config_service)
    if not existing:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="MCP server instance not found.")
    forbid_inherited_mcp_mutation(existing)

    registry = _get_mcp_registry(request)
    _validate_instance_config(payload, registry)
    record = _build_instance_record(payload, instance_id, user_context["org_id"], user_context["user_id"], registry, existing=existing)

    success = await config_service.set_config(get_mcp_instance_path(instance_id), record)
    if not success:
        raise HTTPException(status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail="Failed to update MCP server instance.")
    return record


@router.delete("/instances/{instance_id}", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_DELETE))])
async def delete_instance(request: Request, instance_id: str) -> dict[str, Any]:
    config_service = _get_config_service(request)
    user_context = _get_user_context(request)
    is_admin = await _check_user_is_admin(user_context["user_id"], request, config_service)
    if not is_admin:
        raise HTTPException(status_code=HttpStatusCode.FORBIDDEN.value, detail="Only administrators can delete MCP server instances.")

    instance = await _get_org_instance(instance_id, config_service)
    if not instance:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="MCP server instance not found.")
    forbid_inherited_mcp_mutation(instance)

    # Full cascade cleanup — the reference PR left credentials/oauth-clients/oauth-states orphaned.
    await config_service.delete_config(get_mcp_instance_path(instance_id))

    creds_prefix = get_mcp_instance_credentials_prefix(instance_id)
    try:
        cred_keys = await config_service.list_keys_in_directory(creds_prefix)
    except Exception as e:
        logger.warning(f"Failed to list credential keys under {creds_prefix} during instance delete: {e}")
        cred_keys = []
    for key in cred_keys:
        await config_service.delete_config(key)

    await config_service.delete_config(get_mcp_oauth_client_config_path(instance_id))
    await config_service.delete_config(get_mcp_shared_dcr_client_path(instance_id))

    try:
        state_keys = await config_service.list_keys_in_directory(get_mcp_oauth_states_prefix())
    except Exception:
        state_keys = []
    for key in state_keys:
        state_data = await config_service.get_config(key, default=None)
        if isinstance(state_data, dict) and state_data.get("instanceId") == instance_id:
            await config_service.delete_config(key)

    from app.connectors.core.base.token_service.startup_service import startup_service

    refresh_service = startup_service.get_mcp_token_refresh_service()
    if refresh_service:
        refresh_service.cancel_refresh_tasks_for_instance(instance_id)

    return {"success": True, "_id": instance_id}


# ============================================================================
# Auth — API token / headers
# ============================================================================


def _assert_non_oauth_auth_mode(instance: dict[str, Any]) -> None:
    if instance.get("authMode") == MCPAuthMode.OAUTH.value:
        raise HTTPException(
            status_code=HttpStatusCode.BAD_REQUEST.value,
            detail="This instance uses OAuth — use /oauth/authorize instead.",
        )
    if instance.get("authMode") == MCPAuthMode.NONE.value:
        raise HTTPException(status_code=HttpStatusCode.BAD_REQUEST.value, detail="This instance does not require authentication.")


async def _assert_admin_owns_shared_credential(
    instance: dict[str, Any], user_context: dict[str, Any], request: Request, config_service: ConfigurationService
) -> None:
    """Shared `useAdminAuth` guard — only administrators may manage the one shared credential
    every other user/agent on this instance relies on.
    """
    if instance.get("useAdminAuth"):
        is_admin = await _check_user_is_admin(user_context["user_id"], request, config_service)
        if not is_admin:
            raise HTTPException(
                status_code=HttpStatusCode.FORBIDDEN.value,
                detail="This instance uses a shared admin credential; only administrators can manage it.",
            )


async def _assert_can_write_credentials(
    instance: dict[str, Any], user_context: dict[str, Any], request: Request, config_service: ConfigurationService
) -> None:
    """Shared guard for authenticate/credentials writes — `PUT /credentials` must enforce the
    same useAdminAuth + oauth-mode checks as `POST /authenticate` (the reference PR let
    `PUT /credentials` bypass both). Only applies to writing raw apiToken/header credentials;
    clearing credentials (disconnect) is valid for any auth mode, see
    `_assert_admin_owns_shared_credential`.
    """
    _assert_non_oauth_auth_mode(instance)
    await _assert_admin_owns_shared_credential(instance, user_context, request, config_service)


def _filter_stdio_env(
    instance: dict[str, Any],
    env_payload: Optional[dict[str, str]],
) -> dict[str, str]:
    """Keep only allowlisted, non-empty env entries from the authenticate payload."""
    if not env_payload:
        return {}
    allowed = set(instance.get("requiredEnv") or []) | set(instance.get("optionalEnv") or [])
    filtered: dict[str, str] = {}
    for key, value in env_payload.items():
        if key not in allowed or value is None:
            continue
        trimmed = str(value).strip()
        if trimmed:
            filtered[key] = trimmed
    return filtered


def _build_credential_record(instance: dict[str, Any], payload: AuthenticateRequest, user_id: str, org_id: str) -> dict[str, Any]:
    auth_mode = instance.get("authMode")
    credentials: dict[str, Any] = {}
    if auth_mode == MCPAuthMode.API_TOKEN.value:
        filtered_env = _filter_stdio_env(instance, payload.env)
        if payload.api_token:
            credentials["apiToken"] = payload.api_token
        if filtered_env:
            credentials["env"] = filtered_env

        required_env = list(instance.get("requiredEnv") or [])
        if instance.get("transport") == MCPTransport.STDIO.value and required_env:
            effective = dict(filtered_env)
            if payload.api_token:
                effective.setdefault(required_env[0], payload.api_token)
            missing = [key for key in required_env if not effective.get(key)]
            if missing:
                raise HTTPException(
                    status_code=HttpStatusCode.BAD_REQUEST.value,
                    detail=f"Missing required credentials: {', '.join(missing)}",
                )
        elif not payload.api_token:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="apiToken is required for this instance.",
            )
    elif auth_mode == MCPAuthMode.HEADERS.value:
        if not payload.header_value:
            raise HTTPException(status_code=HttpStatusCode.BAD_REQUEST.value, detail="headerValue is required for this instance.")
        credentials = {
            "headerName": payload.header_name or instance.get("headerName") or "Authorization",
            "headerValue": payload.header_value,
        }

    now_ms = get_epoch_timestamp_in_ms()
    return {
        "instanceId": instance["_id"],
        "userId": user_id,
        "orgId": org_id,
        "authMode": auth_mode,
        "isAuthenticated": True,
        "credentials": credentials,
        "updatedAt": now_ms,
    }


@router.post(
    "/instances/{instance_id}/authenticate",
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))],
)
async def authenticate_instance(
    request: Request,
    instance_id: str,
    payload: AuthenticateRequest,
) -> dict[str, Any]:
    config_service = _get_config_service(request)
    user_context = _get_user_context(request)
    org_id, user_id = user_context["org_id"], user_context["user_id"]

    instance = await _get_org_instance(instance_id, config_service)
    if not instance:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="MCP server instance not found.")

    await _assert_can_write_credentials(instance, user_context, request, config_service)
    record = _build_credential_record(instance, payload, user_id, org_id)

    success = await config_service.set_config(get_mcp_credentials_path(instance_id, user_id), record)
    if not success:
        raise HTTPException(status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail="Failed to save credentials.")
    return {"success": True, "isAuthenticated": True}


@router.put(
    "/instances/{instance_id}/credentials",
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))],
)
async def update_credentials(
    request: Request,
    instance_id: str,
    payload: AuthenticateRequest,
) -> dict[str, Any]:
    # Reuses the exact same guard + record-building as authenticate — the reference PR
    # duplicated (and under-guarded) this path.
    return await authenticate_instance(request, instance_id, payload)


@router.delete(
    "/instances/{instance_id}/credentials",
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))],
)
async def remove_credentials(request: Request, instance_id: str) -> dict[str, Any]:
    """Disconnect the caller's stored credentials/tokens, regardless of auth mode — an
    OAuth-authenticated instance must be disconnectable the same way an API-token one is.
    """
    config_service = _get_config_service(request)
    user_context = _get_user_context(request)
    user_id = user_context["user_id"]

    instance = await _get_org_instance(instance_id, config_service)
    if not instance:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="MCP server instance not found.")
    await _assert_admin_owns_shared_credential(instance, user_context, request, config_service)

    cred_path = get_mcp_credentials_path(instance_id, user_id)
    await config_service.delete_config(cred_path)
    if instance.get("authMode") == MCPAuthMode.OAUTH.value:
        # Mirrors `reauthenticate_instance` — only the legacy per-owner DCR client is this
        # caller's alone to clear; the shared per-instance DCR client survives for other
        # authenticated users/agents on this instance.
        await config_service.delete_config(get_mcp_dcr_client_path(instance_id, user_id))

    from app.connectors.core.base.token_service.startup_service import startup_service

    refresh_service = startup_service.get_mcp_token_refresh_service()
    if refresh_service:
        refresh_service.cancel_refresh_task(cred_path)

    return {"success": True}


@router.post(
    "/instances/{instance_id}/auto-authenticate",
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))],
)
async def auto_authenticate_instance(request: Request, instance_id: str) -> dict[str, Any]:
    """Adopt the admin's shared credential for a `useAdminAuth` instance (verifies it exists first)."""
    config_service = _get_config_service(request)
    _get_user_context(request)

    instance = await _get_org_instance(instance_id, config_service)
    if not instance:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="MCP server instance not found.")
    if not instance.get("useAdminAuth"):
        raise HTTPException(status_code=HttpStatusCode.BAD_REQUEST.value, detail="This instance does not use a shared admin credential.")

    admin_record = await config_service.get_config(
        get_mcp_credentials_path(instance_id, instance.get("createdBy")), default=None, use_cache=False
    )
    if not isinstance(admin_record, dict) or not admin_record.get("isAuthenticated"):
        raise HTTPException(
            status_code=HttpStatusCode.CONFLICT.value,
            detail="The administrator has not configured shared credentials for this instance yet.",
        )
    return {"success": True, "isAuthenticated": True}


@router.post(
    "/instances/{instance_id}/reauthenticate",
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))],
)
async def reauthenticate_instance(request: Request, instance_id: str) -> dict[str, Any]:
    """Clear the caller's stored credentials/tokens for this instance, forcing re-auth."""
    config_service = _get_config_service(request)
    user_context = _get_user_context(request)
    user_id = user_context["user_id"]

    instance = await _get_org_instance(instance_id, config_service)
    if not instance:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="MCP server instance not found.")

    cred_path = get_mcp_credentials_path(instance_id, user_id)
    await config_service.delete_config(cred_path)
    # Only the legacy per-owner DCR client is this caller's alone to clear — the shared
    # per-instance client (if this instance uses one) is relied on by every other
    # authenticated user/agent and must survive a single caller's reauthenticate.
    await config_service.delete_config(get_mcp_dcr_client_path(instance_id, user_id))

    from app.connectors.core.base.token_service.startup_service import startup_service

    refresh_service = startup_service.get_mcp_token_refresh_service()
    if refresh_service:
        refresh_service.cancel_refresh_task(cred_path)

    return {"success": True}


# ============================================================================
# OAuth + DCR
# ============================================================================


def _resolve_oauth_endpoints(
    instance: dict[str, Any], discovered: Optional[DiscoveredOAuthMetadata]
) -> tuple[Optional[str], Optional[str]]:
    """Pick the authorization/token endpoints to use.

    Catalog instances copy `authorizationUrl`/`tokenUrl` verbatim from the template at
    creation time and an admin never edits them directly — for those, a live discovery
    result is authoritative (this is what fixes Notion/Atlassian, whose templates used to
    point at their *direct*-OAuth endpoints, not the ones fronting their MCP servers).

    Custom instances expose these as admin-editable fields — for those, the admin's
    explicit value wins, with discovery only filling in what's left blank.
    """
    stored_authorization_url = instance.get("authorizationUrl")
    stored_token_url = instance.get("tokenUrl")
    discovered_authorization_url = discovered.authorization_endpoint if discovered else None
    discovered_token_url = discovered.token_endpoint if discovered else None

    if instance.get("isCustom"):
        return (
            stored_authorization_url or discovered_authorization_url,
            stored_token_url or discovered_token_url,
        )
    return (
        discovered_authorization_url or stored_authorization_url,
        discovered_token_url or stored_token_url,
    )


async def _resolve_oauth_client(
    config_service: ConfigurationService,
    instance_id: str,
    owner_id: str,
    redirect_uri: str,
    discovered: Optional[DiscoveredOAuthMetadata],
    authorization_url: Optional[str],
    token_url: Optional[str],
) -> tuple[str, Optional[str], bool]:
    """Resolve `(clientId, clientSecret, isDcr)`, in the same priority order as
    `token_refresh.resolve_client_credentials` — legacy per-owner DCR client, then the
    shared per-instance DCR client, then the admin-configured static client, then a fresh
    DCR registration into the shared path. Matching refresh's priority order means a token
    always refreshes against the same client it was issued under, regardless of what an
    admin configures afterwards.
    """
    legacy_dcr = await config_service.get_config(get_mcp_dcr_client_path(instance_id, owner_id), default=None)
    if isinstance(legacy_dcr, dict) and legacy_dcr.get("clientId"):
        return legacy_dcr["clientId"], legacy_dcr.get("clientSecret"), True

    shared_dcr = await config_service.get_config(get_mcp_shared_dcr_client_path(instance_id), default=None)
    if isinstance(shared_dcr, dict) and shared_dcr.get("clientId"):
        return shared_dcr["clientId"], shared_dcr.get("clientSecret"), True

    oauth_client_config = await config_service.get_config(get_mcp_oauth_client_config_path(instance_id), default=None)
    if not isinstance(oauth_client_config, dict) or not oauth_client_config.get("clientId"):
        owner_svc = await resolve_instance_owner_config_service(instance_id, config_service)
        if owner_svc is not None and owner_svc is not config_service:
            oauth_client_config = await owner_svc.get_config(get_mcp_oauth_client_config_path(instance_id), default=None)
    if isinstance(oauth_client_config, dict) and oauth_client_config.get("clientId"):
        return oauth_client_config["clientId"], oauth_client_config.get("clientSecret"), False

    if discovered and discovered.registration_endpoint:
        try:
            dcr_client = await dcr_module.register_dynamic_client(
                registration_endpoint=discovered.registration_endpoint,
                redirect_uri=redirect_uri,
                authorization_url=authorization_url or discovered.authorization_endpoint or "",
                token_url=token_url or discovered.token_endpoint or "",
            )
        except dcr_module.DCRError as e:
            # `DCRError`'s message embeds the registration endpoint's raw response body
            # (see `dcr.register_dynamic_client`) — log it server-side only; a third-party
            # AS's response text is not something to echo back to our own API caller.
            logger.error(f"MCP dynamic client registration failed for instance {instance_id}: {e}")
            raise HTTPException(
                status_code=HttpStatusCode.BAD_GATEWAY.value,
                detail="Dynamic client registration with the OAuth provider failed. Ask an administrator to configure a static OAuth app instead.",
            ) from e
        # One shared registration per instance, not one per owner — every subsequent
        # owner (any user/agent) reuses this same client instead of registering their own.
        await config_service.set_config(
            get_mcp_shared_dcr_client_path(instance_id), dcr_client.model_dump(by_alias=True)
        )
        return dcr_client.client_id, dcr_client.client_secret, True

    raise HTTPException(
        status_code=HttpStatusCode.CONFLICT.value,
        detail=(
            "No OAuth client is configured for this instance and it does not support dynamic "
            f"client registration. Ask an administrator to configure an OAuth app with redirect URI: {redirect_uri}"
        ),
    )


async def _build_oauth_authorization_url(
    config_service: ConfigurationService,
    instance: dict[str, Any],
    instance_id: str,
    owner_id: str,
    org_id: str,
    base_url: Optional[str],
    *,
    initiated_by: str,
    owner_type: str,
) -> dict[str, Any]:
    """Core DCR-or-static-client OAuth authorize flow, shared by the per-user and
    agent-key (`/agents/{agent_key}/...`) routes. `owner_id` is a plain user for the
    former, an agentKey for the latter — the resulting state record's `userId` field
    is exactly what `/oauth/callback` uses to persist tokens at
    `/services/mcp/credentials/{instanceId}/{owner_id}`.

    `initiated_by` is the *authenticated caller* (always a real user, even for the
    agent-key path) who started this authorize request — `/oauth/callback` rejects a
    callback whose caller doesn't match, so one signed-in session can't complete an
    authorization someone else's browser started.
    """
    if instance.get("authMode") != MCPAuthMode.OAUTH.value:
        raise HTTPException(status_code=HttpStatusCode.BAD_REQUEST.value, detail="This instance does not use OAuth.")

    configured_base = await _get_configured_frontend_base_url(config_service)
    validated_base = _resolve_validated_base_url(base_url, configured_base)
    redirect_uri = f"{validated_base}/mcp-servers/oauth/callback/"

    # Discover live metadata *before* resolving endpoints/client, not only as a DCR
    # fallback — a configured static client used to skip discovery entirely and fall back
    # to the (for catalog instances, often wrong) stored authorizationUrl/tokenUrl.
    discovered: Optional[DiscoveredOAuthMetadata] = None
    if instance.get("url"):
        discovered = await dcr_module.discover_oauth_metadata(instance["url"])

    authorization_url, token_url = _resolve_oauth_endpoints(instance, discovered)
    # The secret is intentionally discarded here — see the state_record comment below on why
    # it is re-resolved at callback/exchange time instead of being carried through this flow.
    client_id, _client_secret, is_dcr = await _resolve_oauth_client(
        config_service, instance_id, owner_id, redirect_uri, discovered, authorization_url, token_url
    )

    if not authorization_url or not token_url:
        raise HTTPException(status_code=HttpStatusCode.CONFLICT.value, detail="This instance is missing OAuth authorization/token URLs.")

    # Admin-configured scopes win; otherwise use what the AS actually advertises; otherwise
    # omit `scope` entirely rather than send a template default the discovered AS may reject
    # (Atlassian's MCP AS metadata advertises no scopes_supported at all).
    scopes = instance.get("scopes") or (discovered.scopes_supported if discovered else None)

    state = dcr_module.generate_state()
    code_verifier = dcr_module.generate_code_verifier()
    code_challenge = dcr_module.generate_code_challenge(code_verifier)

    state_record = {
        "instanceId": instance_id,
        "userId": owner_id,
        "orgId": org_id,
        "initiatedBy": initiated_by,
        "ownerType": owner_type,
        "codeVerifier": code_verifier,
        "isDcr": is_dcr,
        "clientId": client_id,
        # clientSecret is deliberately NOT persisted here — /oauth/callback re-resolves it
        # from the DCR/static client store at exchange time, so a rotated admin secret (or
        # an expired DCR registration) fails loudly instead of a stale copy silently working.
        "tokenUrl": token_url,
        "redirectUri": redirect_uri,
        "expiresAt": get_epoch_timestamp_in_ms() + OAUTH_STATE_TTL_SECONDS * 1000,
    }
    await config_service.set_config(get_mcp_oauth_state_path(state), state_record)

    # Opportunistic sweep of abandoned states — bounded by whatever the periodic
    # refresh-service pass hasn't already caught.
    asyncio.create_task(_sweep_expired_oauth_states(config_service))

    authorization_redirect_url = dcr_module.build_authorization_url(
        authorization_url=authorization_url,
        client_id=client_id,
        redirect_uri=redirect_uri,
        state=state,
        scopes=scopes,
        code_challenge=code_challenge,
    )
    return {"authorizationUrl": authorization_redirect_url}


@router.get(
    "/instances/{instance_id}/oauth/authorize",
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))],
)
async def get_oauth_authorization_url(
    request: Request,
    instance_id: str,
    base_url: Optional[str] = Query(default=None, alias="baseUrl"),
) -> dict[str, Any]:
    config_service = _get_config_service(request)
    user_context = _get_user_context(request)
    org_id, user_id = user_context["org_id"], user_context["user_id"]

    instance = await _get_org_instance(instance_id, config_service)
    if not instance:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="MCP server instance not found.")

    return await _build_oauth_authorization_url(
        config_service, instance, instance_id, user_id, org_id, base_url,
        initiated_by=user_id, owner_type="user",
    )


@router.post("/oauth/discover", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
async def discover_oauth_metadata_endpoint(request: Request, payload: OAuthDiscoveryRequest) -> dict[str, Any]:
    """Admin-only probe: does this MCP server support OAuth dynamic client registration,
    and if so, what are its real endpoints? Needed by the config panel in *create* mode,
    before an instance (and therefore a stored authorizationUrl/tokenUrl) exists, and to
    correct a catalog template's endpoints in edit mode.

    This fetches an admin-supplied URL from inside the cluster — an SSRF surface — so it is
    admin-gated and gets the same host-resolution guard as every other outbound discovery
    request (see `dcr_module.assert_discovery_target_allowed`).
    """
    config_service = _get_config_service(request)
    user_context = _get_user_context(request)
    is_admin = await _check_user_is_admin(user_context["user_id"], request, config_service)
    if not is_admin:
        raise HTTPException(status_code=HttpStatusCode.FORBIDDEN.value, detail="Only administrators can probe OAuth metadata.")

    try:
        await dcr_module.assert_discovery_target_allowed(payload.url)
    except dcr_module.DiscoveryBlockedError as e:
        raise HTTPException(status_code=HttpStatusCode.BAD_REQUEST.value, detail=str(e)) from e

    metadata = await dcr_module.discover_oauth_metadata(payload.url)
    if metadata is None:
        # Distinct from "confirmed no DCR" — nothing could be discovered at all (network
        # error, timeout, or the server simply publishes no RFC 8414/9728 metadata). The
        # caller should treat DCR support as unknown, not as a confirmed "false".
        return {
            "metadataFound": False,
            "supportsDcr": False,
            "authorizationEndpoint": None,
            "tokenEndpoint": None,
            "registrationEndpoint": None,
            "scopesSupported": [],
        }
    return {
        "metadataFound": True,
        "supportsDcr": metadata.supports_dcr,
        "authorizationEndpoint": metadata.authorization_endpoint,
        "tokenEndpoint": metadata.token_endpoint,
        "registrationEndpoint": metadata.registration_endpoint,
        "scopesSupported": metadata.scopes_supported,
    }


async def _sweep_expired_oauth_states(config_service: ConfigurationService) -> None:
    try:
        now_ms = get_epoch_timestamp_in_ms()
        keys = await config_service.list_keys_in_directory(get_mcp_oauth_states_prefix())
        for key in keys:
            state_data = await config_service.get_config(key, default=None)
            if isinstance(state_data, dict) and state_data.get("expiresAt", 0) < now_ms:
                await config_service.delete_config(key)
    except Exception as e:
        logger.debug(f"OAuth state sweep failed (non-fatal): {e}")


async def _resolve_oauth_client_secret(
    config_service: ConfigurationService,
    instance_id: str,
    owner_id: str,
    client_id: str,
    is_dcr: bool,
) -> Optional[str]:
    """Re-resolve the client secret at token-exchange time rather than trusting a copy
    persisted on the authorize-time state record (see `_build_oauth_authorization_url`).

    Raises:
        ValueError: the client this authorization started against can no longer be found
            (an admin rotated/removed the OAuth app, or a DCR registration expired) — the
            caller turns this into a `{"success": False, ...}` response like every other
            callback failure mode, rather than a raw 5xx.
    """
    if is_dcr:
        for path in (get_mcp_dcr_client_path(instance_id, owner_id), get_mcp_shared_dcr_client_path(instance_id)):
            client = await config_service.get_config(path, default=None)
            if isinstance(client, dict) and client.get("clientId") == client_id:
                return client.get("clientSecret")
        raise ValueError(
            "The dynamically-registered OAuth client for this authorization could no longer be found."
        )

    static_client = await config_service.get_config(get_mcp_oauth_client_config_path(instance_id), default=None)
    if isinstance(static_client, dict) and static_client.get("clientId") == client_id:
        return static_client.get("clientSecret")

    owner_svc = await resolve_instance_owner_config_service(instance_id, config_service)
    if owner_svc is not None and owner_svc is not config_service:
        parent_client = await owner_svc.get_config(get_mcp_oauth_client_config_path(instance_id), default=None)
        if isinstance(parent_client, dict) and parent_client.get("clientId") == client_id:
            return parent_client.get("clientSecret")

    raise ValueError("The OAuth app configuration for this instance changed during authorization.")


@router.get("/oauth/callback", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
async def handle_oauth_callback(
    request: Request,
    code: Optional[str] = Query(default=None),
    state: Optional[str] = Query(default=None),
    error: Optional[str] = Query(default=None),
) -> dict[str, Any]:
    config_service = _get_config_service(request)
    # Same extraction as `_get_user_context`, but without raising 401 — this route always
    # responds with a 200 + `{"success": false, ...}` body on any failure (including "no
    # caller identity"), matching every other error branch below.
    caller_user = getattr(request.state, "user", {}) or {}
    caller_id = caller_user.get("userId") or request.headers.get("X-User-Id")

    if error:
        return {"success": False, "error": error, "errorMessage": f"OAuth provider returned an error: {error}"}
    if not code or not state:
        return {"success": False, "error": "missing_params", "errorMessage": "Missing code or state parameter."}

    state_path = get_mcp_oauth_state_path(state)
    state_record = await config_service.get_config(state_path, default=None, use_cache=False)
    if not isinstance(state_record, dict):
        return {"success": False, "error": "invalid_state", "errorMessage": "Invalid or expired authorization state."}

    # Single-use: delete immediately so a replayed callback can't reuse it.
    await config_service.delete_config(state_path)

    if state_record.get("expiresAt", 0) < get_epoch_timestamp_in_ms():
        return {"success": False, "error": "expired_state", "errorMessage": "The authorization request expired. Please try again."}

    # Bind the callback to whoever's session started it — without this, anyone who could
    # guess or intercept a valid `state` value could complete someone else's authorization
    # and have the resulting tokens attributed to the original initiator's credential
    # record. A state record predating this field is treated as unbound and rejected; the
    # 10-minute TTL means nothing long-lived survives a deploy of this change.
    initiated_by = state_record.get("initiatedBy")
    if not initiated_by or initiated_by != caller_id:
        logger.warning(
            f"Rejected MCP OAuth callback for instance {state_record.get('instanceId')}: "
            f"caller {caller_id!r} does not match initiator {initiated_by!r}"
        )
        return {
            "success": False,
            "error": "caller_mismatch",
            "errorMessage": "This authorization was not started from the current session. Please try connecting again.",
        }

    instance_id = state_record["instanceId"]
    user_id = state_record["userId"]
    client_id = state_record["clientId"]
    is_dcr = bool(state_record.get("isDcr"))

    try:
        client_secret = await _resolve_oauth_client_secret(config_service, instance_id, user_id, client_id, is_dcr)
    except ValueError as e:
        logger.error(f"MCP OAuth client resolution failed for instance {instance_id}: {e}")
        return {
            "success": False,
            "error": "client_config_changed",
            "errorMessage": (
                "The OAuth app configuration for this instance changed during authorization. "
                "Please try connecting again."
            ),
        }

    try:
        tokens = await oauth_client_module.exchange_code_for_token(
            token_url=state_record["tokenUrl"],
            client_id=client_id,
            client_secret=client_secret,
            code=code,
            redirect_uri=state_record["redirectUri"],
            code_verifier=state_record.get("codeVerifier"),
        )
    except oauth_client_module.MCPOAuthError as e:
        logger.error(f"MCP OAuth code exchange failed for instance {instance_id}: {e}")
        return {
            "success": False,
            "error": "token_exchange_failed",
            "errorMessage": "Failed to exchange the authorization code for tokens. Please try connecting again.",
        }

    now_ms = get_epoch_timestamp_in_ms()
    record = {
        "instanceId": instance_id,
        "userId": user_id,
        "orgId": state_record.get("orgId"),
        "authMode": MCPAuthMode.OAUTH.value,
        "isAuthenticated": True,
        "oauthTokens": tokens.model_dump(by_alias=True, mode="json"),
        "updatedAt": now_ms,
    }
    await config_service.set_config(get_mcp_credentials_path(instance_id, user_id), record)

    from app.connectors.core.base.token_service.startup_service import startup_service

    refresh_service = startup_service.get_mcp_token_refresh_service()
    if refresh_service:
        await refresh_service.schedule_token_refresh(
            get_mcp_credentials_path(instance_id, user_id),
            tokens,
            **build_schedule_refresh_kwargs(state_record.get("orgId")),
        )

    return {"success": True, "instanceId": instance_id}


@router.post(
    "/instances/{instance_id}/oauth/refresh",
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))],
)
async def refresh_oauth_token(request: Request, instance_id: str) -> dict[str, Any]:
    config_service = _get_config_service(request)
    user_context = _get_user_context(request)
    user_id = user_context["user_id"]

    try:
        owner_svc = await resolve_instance_owner_config_service(instance_id, config_service)
        fallbacks = [owner_svc] if owner_svc is not None and owner_svc is not config_service else None
        await mcp_token_refresh.refresh_credential_record(instance_id, user_id, config_service, fallbacks)
    except mcp_token_refresh.MCPTokenRefreshError as e:
        # Covers "no credential record", "no refresh token", "no tokenUrl", and "no
        # resolvable OAuth client" — all mean the caller must re-authenticate or an admin
        # must configure OAuth, not that the token endpoint itself rejected anything.
        raise HTTPException(status_code=HttpStatusCode.BAD_REQUEST.value, detail=str(e)) from e
    except oauth_client_module.MCPRefreshTokenInvalidError as e:
        cred_path = get_mcp_credentials_path(instance_id, user_id)
        record = await config_service.get_config(cred_path, default=None, use_cache=False)
        if isinstance(record, dict):
            record["isAuthenticated"] = False
            await config_service.set_config(cred_path, record)
        # Both `MCPRefreshTokenInvalidError` and `MCPOAuthError` embed the token endpoint's
        # raw response body (see `oauth_client._post_token_request`) — log it server-side
        # only, same treatment as `/oauth/callback`'s token-exchange-failure branch above.
        logger.warning(f"MCP OAuth refresh token rejected for instance {instance_id}: {e}")
        raise HTTPException(
            status_code=HttpStatusCode.UNAUTHORIZED.value,
            detail="The refresh token was rejected by the OAuth provider. Please reconnect this MCP server.",
        ) from e
    except oauth_client_module.MCPOAuthError as e:
        logger.error(f"MCP OAuth token refresh failed for instance {instance_id}: {e}")
        raise HTTPException(
            status_code=HttpStatusCode.BAD_GATEWAY.value,
            detail="Failed to refresh the OAuth token. Please try again or reconnect this MCP server.",
        ) from e

    return {"success": True}


@router.get(
    "/instances/{instance_id}/oauth-config",
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))],
)
async def get_oauth_config(request: Request, instance_id: str) -> dict[str, Any]:
    config_service = _get_config_service(request)
    user_context = _get_user_context(request)
    is_admin = await _check_user_is_admin(user_context["user_id"], request, config_service)
    if not is_admin:
        raise HTTPException(status_code=HttpStatusCode.FORBIDDEN.value, detail="Only administrators can view OAuth client configuration.")

    config = await config_service.get_config(get_mcp_oauth_client_config_path(instance_id), default=None)
    if not isinstance(config, dict):
        owner_svc = await resolve_instance_owner_config_service(instance_id, config_service)
        if owner_svc is not None and owner_svc is not config_service:
            config = await owner_svc.get_config(get_mcp_oauth_client_config_path(instance_id), default=None)
    if not isinstance(config, dict):
        return {"configured": False}

    return {
        "configured": True,
        "clientId": _mask_secret(config.get("clientId")),
        "clientSecret": _mask_secret(config.get("clientSecret")),
    }


def _mask_secret(value: Optional[str]) -> Optional[str]:
    if not value:
        return value
    if len(value) <= 8:
        return "•" * len(value)
    return f"{value[:4]}{'•' * 8}{value[-4:]}"


@router.put(
    "/instances/{instance_id}/oauth-config",
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))],
)
async def update_oauth_config(
    request: Request,
    instance_id: str,
    payload: OAuthClientConfigRequest,
) -> dict[str, Any]:
    config_service = _get_config_service(request)
    user_context = _get_user_context(request)
    is_admin = await _check_user_is_admin(user_context["user_id"], request, config_service)
    if not is_admin:
        raise HTTPException(status_code=HttpStatusCode.FORBIDDEN.value, detail="Only administrators can configure OAuth clients.")

    instance = await _get_org_instance(instance_id, config_service)
    if not instance:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="MCP server instance not found.")

    record = {
        "instanceId": instance_id,
        "clientId": payload.client_id,
        "clientSecret": payload.client_secret,
        "updatedAt": get_epoch_timestamp_in_ms(),
    }
    success = await config_service.set_config(get_mcp_oauth_client_config_path(instance_id), record)
    if not success:
        raise HTTPException(status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail="Failed to save OAuth client configuration.")
    return {"success": True}


# ============================================================================
# Discovery / consumption
# ============================================================================


async def _build_mcp_instance_entry(
    instance: dict[str, Any],
    owner_id: str,
    config_service: ConfigurationService,
    include_tools: bool,
) -> dict[str, Any]:
    """Merge one instance's metadata with `owner_id`'s auth status (+ live tools).

    `owner_id` is opaque — a plain user for `/my-mcp-servers`, or an agentKey for the
    service-account path `/agents/{agent_key}` (`_resolve_effective_user_auth` already
    treats both the same way). Shared so the two routes can't drift.
    """
    entry = dict(instance)
    owner_svc = await resolve_instance_owner_config_service(instance["_id"], config_service) or config_service
    entry["hasOAuthClientConfig"] = bool(
        await owner_svc.get_config(get_mcp_oauth_client_config_path(instance["_id"]), default=None)
    )
    effective_auth = await _resolve_effective_user_auth(instance, owner_id, config_service)
    entry["isAuthenticated"] = bool(effective_auth is not None and (
        effective_auth == {} or effective_auth.get("isAuthenticated")
    ))
    entry["tools"] = []
    entry["toolsError"] = None

    if include_tools and entry["isAuthenticated"]:
        try:
            credentials_dict = _credentials_to_discovery_dict(instance.get("authMode"), effective_auth or {})
            tools = await asyncio.wait_for(
                discover_tools(_instance_config_model_from_dict(instance), credentials_dict),
                timeout=DEFAULT_TOOLS_DISCOVERY_TIMEOUT_SECONDS,
            )
            entry["tools"] = [t.model_dump(by_alias=True) for t in tools]
        except (MCPConnectionError, asyncio.TimeoutError) as e:
            entry["toolsError"] = str(e)
        except Exception as e:
            logger.warning(f"Unexpected error discovering tools for MCP instance {instance['_id']}: {e}")
            entry["toolsError"] = "Failed to discover tools."
    return entry


@router.get("/my-mcp-servers", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
async def get_my_mcp_servers(
    request: Request,
    include_tools: bool = Query(default=True, alias="includeTools"),
) -> dict[str, Any]:
    config_service = _get_config_service(request)
    user_context = _get_user_context(request)
    user_id = user_context["user_id"]

    instances = await resolve_mcp_instances_with_inheritance(config_service)
    entries = await asyncio.gather(
        *[_build_mcp_instance_entry(i, user_id, config_service, include_tools) for i in instances]
    )
    return {"instances": list(entries)}


@router.get(
    "/instances/{instance_id}/tools",
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))],
)
async def get_instance_tools(request: Request, instance_id: str) -> dict[str, Any]:
    config_service = _get_config_service(request)
    user_context = _get_user_context(request)
    user_id = user_context["user_id"]

    instance = await _get_org_instance(instance_id, config_service)
    if not instance:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="MCP server instance not found.")

    effective_auth = await _resolve_effective_user_auth(instance, user_id, config_service)
    if effective_auth is None:
        raise HTTPException(status_code=HttpStatusCode.CONFLICT.value, detail="Not authenticated for this MCP server instance.")

    credentials_dict = _credentials_to_discovery_dict(instance.get("authMode"), effective_auth)
    try:
        tools = await discover_tools(_instance_config_model_from_dict(instance), credentials_dict)
    except MCPConnectionError as e:
        raise HTTPException(status_code=HttpStatusCode.BAD_GATEWAY.value, detail=str(e)) from e

    return {"tools": [t.model_dump(by_alias=True) for t in tools]}


# ============================================================================
# Agent-key (service-account) credentials
#
# Mirrors the per-user routes above, but the credential owner is `agent_key`
# instead of the caller's `user_id` — for service-account agents, which act with
# their own MCP credentials rather than the invoking user's. Reuses the agent
# permission helpers already used by `app.api.routes.toolsets`'s equivalent
# routes so both features share exactly one `can_edit` + `isServiceAccount` gate.
# ============================================================================


async def _require_mcp_agent_edit_access(agent_key: str, request: Request) -> dict[str, Any]:
    from app.api.routes.toolsets import _require_agent_edit_access

    return await _require_agent_edit_access(
        agent_key, request, feature_name="MCP servers", settings_path="Workspace → MCP Servers",
    )


@router.get("/agents/{agent_key}", dependencies=[Depends(require_scopes(OAuthScopes.AGENT_READ))])
async def get_agent_mcp_servers(
    request: Request,
    agent_key: str,
    include_tools: bool = Query(default=True, alias="includeTools"),
) -> dict[str, Any]:
    """Org MCP server instances merged with `agent_key`'s auth status (+ live tools).

    Accessible to any user with view access to the agent (mirrors
    `toolsets.get_agent_toolsets`); the write endpoints below additionally
    require `can_edit` + `isServiceAccount` via `_require_mcp_agent_edit_access`.
    """
    from app.api.routes.toolsets import _resolve_agent_with_permission

    await _resolve_agent_with_permission(agent_key, request)
    config_service = _get_config_service(request)
    _get_user_context(request)

    instances = await resolve_mcp_instances_with_inheritance(config_service)
    entries = await asyncio.gather(
        *[_build_mcp_instance_entry(i, agent_key, config_service, include_tools) for i in instances]
    )
    return {"instances": list(entries)}


@router.post(
    "/agents/{agent_key}/instances/{instance_id}/authenticate",
    dependencies=[Depends(require_scopes(OAuthScopes.AGENT_WRITE))],
)
async def authenticate_agent_instance(
    request: Request,
    agent_key: str,
    instance_id: str,
    payload: AuthenticateRequest,
) -> dict[str, Any]:
    """Save agent (service-account) credentials for a non-OAuth MCP server instance."""
    await _require_mcp_agent_edit_access(agent_key, request)
    config_service = _get_config_service(request)
    user_context = _get_user_context(request)
    org_id = user_context["org_id"]

    instance = await _get_org_instance(instance_id, config_service)
    if not instance:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="MCP server instance not found.")

    _assert_non_oauth_auth_mode(instance)
    record = _build_credential_record(instance, payload, agent_key, org_id)
    record["agentKey"] = agent_key

    success = await config_service.set_config(get_mcp_credentials_path(instance_id, agent_key), record)
    if not success:
        raise HTTPException(status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail="Failed to save agent credentials.")
    return {"success": True, "isAuthenticated": True}


@router.put(
    "/agents/{agent_key}/instances/{instance_id}/credentials",
    dependencies=[Depends(require_scopes(OAuthScopes.AGENT_WRITE))],
)
async def update_agent_instance_credentials(
    request: Request,
    agent_key: str,
    instance_id: str,
    payload: AuthenticateRequest,
) -> dict[str, Any]:
    # Reuses the exact same guard + record-building as authenticate (same convention
    # as the per-user `update_credentials` -> `authenticate_instance` delegation above).
    return await authenticate_agent_instance(request, agent_key, instance_id, payload)


@router.delete(
    "/agents/{agent_key}/instances/{instance_id}/credentials",
    dependencies=[Depends(require_scopes(OAuthScopes.AGENT_WRITE))],
)
async def remove_agent_instance_credentials(request: Request, agent_key: str, instance_id: str) -> dict[str, Any]:
    await _require_mcp_agent_edit_access(agent_key, request)
    config_service = _get_config_service(request)
    _get_user_context(request)

    instance = await _get_org_instance(instance_id, config_service)
    if not instance:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="MCP server instance not found.")

    cred_path = get_mcp_credentials_path(instance_id, agent_key)
    await config_service.delete_config(cred_path)

    from app.connectors.core.base.token_service.startup_service import startup_service

    refresh_service = startup_service.get_mcp_token_refresh_service()
    if refresh_service:
        refresh_service.cancel_refresh_task(cred_path)

    return {"success": True}


@router.post(
    "/agents/{agent_key}/instances/{instance_id}/reauthenticate",
    dependencies=[Depends(require_scopes(OAuthScopes.AGENT_WRITE))],
)
async def reauthenticate_agent_instance(request: Request, agent_key: str, instance_id: str) -> dict[str, Any]:
    """Clear the agent's stored credentials/tokens for this instance, forcing re-auth."""
    await _require_mcp_agent_edit_access(agent_key, request)
    config_service = _get_config_service(request)
    _get_user_context(request)

    instance = await _get_org_instance(instance_id, config_service)
    if not instance:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="MCP server instance not found.")

    cred_path = get_mcp_credentials_path(instance_id, agent_key)
    await config_service.delete_config(cred_path)
    # See per-user reauthenticate_instance above — only the legacy per-owner DCR client is
    # this agent's alone to clear; the shared per-instance client must survive.
    await config_service.delete_config(get_mcp_dcr_client_path(instance_id, agent_key))

    from app.connectors.core.base.token_service.startup_service import startup_service

    refresh_service = startup_service.get_mcp_token_refresh_service()
    if refresh_service:
        refresh_service.cancel_refresh_task(cred_path)

    return {"success": True}


@router.get(
    "/agents/{agent_key}/instances/{instance_id}/oauth/authorize",
    dependencies=[Depends(require_scopes(OAuthScopes.AGENT_WRITE))],
)
async def get_agent_oauth_authorization_url(
    request: Request,
    agent_key: str,
    instance_id: str,
    base_url: Optional[str] = Query(default=None, alias="baseUrl"),
) -> dict[str, Any]:
    """OAuth authorize for a service-account agent — the resulting state record's
    `userId` field carries `agent_key`, so `/oauth/callback` persists tokens at
    `/services/mcp/credentials/{instanceId}/{agent_key}`. `initiatedBy` still carries the
    real signed-in user (never `agent_key`) — they're the one whose browser session
    completes the callback.
    """
    await _require_mcp_agent_edit_access(agent_key, request)
    config_service = _get_config_service(request)
    user_context = _get_user_context(request)
    org_id, user_id = user_context["org_id"], user_context["user_id"]

    instance = await _get_org_instance(instance_id, config_service)
    if not instance:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="MCP server instance not found.")

    return await _build_oauth_authorization_url(
        config_service, instance, instance_id, agent_key, org_id, base_url,
        initiated_by=user_id, owner_type="agent",
    )
