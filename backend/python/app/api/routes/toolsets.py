"""
Toolsets API Routes
Handles toolset registry, OAuth, configuration management, and tool retrieval
"""

import asyncio
import base64
import json
import logging
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlencode, urlparse

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.config.configuration_service import ConfigurationService
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.base.token_service.oauth_service import OAuthProvider
from app.connectors.core.registry.auth_builder import OAuthScopeType
from app.connectors.services.base_arango_service import BaseArangoService
from app.containers.connector import ConnectorAppContainer
from app.utils.oauth_config import get_oauth_config
from app.utils.time_conversion import get_epoch_timestamp_in_ms

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/toolsets", tags=["toolsets"])


# ============================================================================
# Custom Exceptions
# ============================================================================

class ToolsetError(HTTPException):
    """Base exception for toolset operations"""
    def __init__(self, detail: str, status_code: int = 500):
        super().__init__(status_code=status_code, detail=detail)


class ToolsetNotFoundError(ToolsetError):
    """Toolset not found in registry"""
    def __init__(self, toolset_name: str):
        super().__init__(
            detail=f"Toolset '{toolset_name}' not found in registry",
            status_code=HttpStatusCode.NOT_FOUND.value
        )


class ToolsetConfigNotFoundError(ToolsetError):
    """Toolset configuration not found"""
    def __init__(self, toolset_name: str):
        super().__init__(
            detail=f"Toolset '{toolset_name}' is not configured. Please configure it first.",
            status_code=HttpStatusCode.NOT_FOUND.value
        )


class ToolsetAlreadyExistsError(ToolsetError):
    """Toolset configuration already exists"""
    def __init__(self, toolset_name: str):
        super().__init__(
            detail=f"Toolset '{toolset_name}' is already configured. Update the existing configuration instead.",
            status_code=HttpStatusCode.CONFLICT.value
        )


class ToolsetInUseError(ToolsetError):
    """Toolset is in use and cannot be deleted"""
    def __init__(self, toolset_name: str, agent_names: List[str]):
        if len(agent_names) == 1:
            detail = f"Cannot delete toolset '{toolset_name}': currently in use by agent '{agent_names[0]}'. Remove it from the agent first."
        else:
            names_display = ", ".join(f"'{n}'" for n in agent_names[:3])
            if len(agent_names) > 3:
                names_display += f" and {len(agent_names) - 3} more"
            detail = f"Cannot delete toolset '{toolset_name}': currently in use by {len(agent_names)} agents ({names_display}). Remove it from all agents first."

        super().__init__(detail=detail, status_code=HttpStatusCode.CONFLICT.value)


class InvalidAuthConfigError(ToolsetError):
    """Invalid authentication configuration"""
    def __init__(self, message: str):
        super().__init__(
            detail=f"Invalid authentication configuration: {message}",
            status_code=HttpStatusCode.BAD_REQUEST.value
        )


class OAuthConfigError(ToolsetError):
    """OAuth configuration error"""
    def __init__(self, message: str):
        super().__init__(
            detail=f"OAuth configuration error: {message}",
            status_code=HttpStatusCode.BAD_REQUEST.value
        )


# ============================================================================
# Helper Functions
# ============================================================================

def _get_user_context(request: Request) -> Dict[str, Any]:
    """Extract and validate user context from request"""
    user = getattr(request.state, "user", {})
    user_id = user.get("userId") or request.headers.get("X-User-Id")
    org_id = user.get("orgId") or request.headers.get("X-Organization-Id")

    if not user_id:
        raise HTTPException(
            status_code=HttpStatusCode.UNAUTHORIZED.value,
            detail="Authentication required. Please provide valid user credentials."
        )

    return {"user_id": user_id, "org_id": org_id}


def _get_registry(request: Request):
    """Get and validate toolset registry from app state"""
    registry = getattr(request.app.state, "toolset_registry", None)
    if not registry:
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail="Toolset registry not initialized. Please contact system administrator."
        )
    return registry


def _get_toolset_metadata(registry, toolset_type: str):
    """Get and validate toolset metadata"""
    if not toolset_type or not toolset_type.strip():
        raise HTTPException(
            status_code=HttpStatusCode.BAD_REQUEST.value,
            detail="Toolset type cannot be empty"
        )

    metadata = registry.get_toolset_metadata(toolset_type)
    if not metadata:
        raise ToolsetNotFoundError(toolset_type)

    if metadata.get("isInternal", False):
        raise ToolsetNotFoundError(toolset_type)

    return metadata


def _get_config_path(user_id: str, toolset_type: str) -> str:
    """Get etcd configuration path for user's toolset"""
    if not user_id or not toolset_type:
        raise HTTPException(
            status_code=HttpStatusCode.BAD_REQUEST.value,
            detail="User ID and toolset type are required"
        )
    return f"/services/toolsets/{user_id}/{toolset_type}"


def _get_user_toolsets_prefix(user_id: str) -> str:
    """Get etcd prefix for all user's toolsets"""
    return f"/services/toolsets/{user_id}/"


def _encode_state_with_toolset(state: str, toolset_id: str) -> str:
    """Encode OAuth state with toolset ID"""
    if not state or not toolset_id:
        raise OAuthConfigError("State and toolset ID are required for OAuth flow")

    try:
        state_data = {"state": state, "toolset_id": toolset_id}
        return base64.urlsafe_b64encode(json.dumps(state_data).encode()).decode()
    except Exception as e:
        raise OAuthConfigError(f"Failed to encode OAuth state: {str(e)}")


def _decode_state_with_toolset(encoded_state: str) -> Dict[str, str]:
    """Decode OAuth state to extract original state and toolset ID"""
    if not encoded_state:
        raise OAuthConfigError("OAuth state parameter is missing")

    try:
        decoded = base64.urlsafe_b64decode(encoded_state.encode()).decode()
        state_data = json.loads(decoded)

        if "state" not in state_data or "toolset_id" not in state_data:
            raise ValueError("Missing required fields in state data")

        return state_data
    except json.JSONDecodeError:
        raise OAuthConfigError("Invalid OAuth state format: not valid JSON")
    except Exception as e:
        raise OAuthConfigError(f"Failed to decode OAuth state: {str(e)}")


def _validate_auth_config(auth_config: Dict[str, Any]) -> str:
    """Validate auth configuration and return auth type"""
    if not auth_config:
        raise InvalidAuthConfigError("Authentication configuration is required")

    auth_type = auth_config.get("type", "").strip().upper()

    if not auth_type:
        raise InvalidAuthConfigError("Authentication type is required (OAUTH or API_TOKEN)")

    if auth_type == "OAUTH":
        client_id = auth_config.get("clientId", "").strip()
        client_secret = auth_config.get("clientSecret", "").strip()

        if not client_id:
            raise InvalidAuthConfigError("OAuth Client ID is required")
        if not client_secret:
            raise InvalidAuthConfigError("OAuth Client Secret is required")

    elif auth_type == "API_TOKEN":
        api_token = auth_config.get("apiToken", "").strip()
        if not api_token:
            raise InvalidAuthConfigError("API Token is required")
    else:
        raise InvalidAuthConfigError(
            f"Unsupported authentication type '{auth_type}'. Supported types: OAUTH, API_TOKEN"
        )

    return auth_type


def _get_oauth_config_from_registry(toolset_type: str, registry):
    """Get OAuth config from toolset registry"""
    metadata = registry.get_toolset_metadata(toolset_type)
    if not metadata:
        raise ToolsetNotFoundError(toolset_type)

    oauth_configs = metadata.get("config", {}).get("_oauth_configs", {})
    oauth_config = oauth_configs.get("OAUTH")

    if not oauth_config:
        raise OAuthConfigError(
            f"Toolset '{toolset_type}' does not support OAuth authentication. "
            f"Supported auth types: {', '.join(metadata.get('supported_auth_types', ['NONE']))}"
        )

    if not hasattr(oauth_config, 'authorize_url') or not hasattr(oauth_config, 'token_url'):
        raise OAuthConfigError(
            f"Toolset '{toolset_type}' has incomplete OAuth configuration"
        )

    return oauth_config


async def _build_oauth_config(
    auth_config: Dict[str, Any],
    toolset_type: str,
    registry,
    base_url: Optional[str] = None,
    request: Optional[Request] = None
) -> Dict[str, Any]:
    """Build OAuth configuration for authorization flow"""
    client_id = auth_config.get("clientId", "").strip()
    client_secret = auth_config.get("clientSecret", "").strip()

    if not client_id or not client_secret:
        raise InvalidAuthConfigError("OAuth Client ID and Client Secret are required")

    oauth_config = _get_oauth_config_from_registry(toolset_type, registry)

    # Build redirect URI
    redirect_path = oauth_config.redirect_uri
    if base_url:
        full_redirect_uri = f"{base_url.rstrip('/')}/{redirect_path}"
    elif request:
        full_redirect_uri = f"{request.url.scheme}://{request.url.netloc}/{redirect_path}"
    else:
        full_redirect_uri = f"http://localhost:3001/{redirect_path}"

    # Get scopes
    scopes = oauth_config.scopes.get_scopes_for_type(OAuthScopeType.AGENT)

    # Build config
    config = {
        "clientId": client_id,
        "clientSecret": client_secret,
        "redirectUri": full_redirect_uri,
        "scopes": scopes,
        "authorizeUrl": oauth_config.authorize_url,
        "tokenUrl": oauth_config.token_url,
        "name": toolset_type,
    }

    # Add optional parameters
    if hasattr(oauth_config, 'additional_params') and oauth_config.additional_params:
        config["additionalParams"] = oauth_config.additional_params

    if hasattr(oauth_config, 'token_access_type') and oauth_config.token_access_type:
        if "access_type" not in config.get("additionalParams", {}):
            config["tokenAccessType"] = oauth_config.token_access_type

    # Add scope_parameter_name if specified (defaults to "scope" if not provided)
    if hasattr(oauth_config, 'scope_parameter_name') and oauth_config.scope_parameter_name != "scope":
        config["scopeParameterName"] = oauth_config.scope_parameter_name

    # Add token_response_path if specified (optional, for providers with nested token responses)
    if hasattr(oauth_config, 'token_response_path') and oauth_config.token_response_path:
        config["tokenResponsePath"] = oauth_config.token_response_path

    return config


def _format_toolset_data(toolset_name: str, metadata: Dict[str, Any], include_tools: bool = False) -> Dict[str, Any]:
    """Format toolset metadata for API response"""
    tools = metadata.get("tools", [])
    data = {
        "name": toolset_name,
        "displayName": metadata.get("display_name", toolset_name),
        "description": metadata.get("description", ""),
        "category": metadata.get("category", "app"),
        "group": metadata.get("group", ""),
        "iconPath": metadata.get("icon_path", ""),
        "supportedAuthTypes": metadata.get("supported_auth_types", []),
        "toolCount": len(tools)
    }

    if include_tools:
        data["tools"] = [
            {
                "name": tool.get("name", ""),
                "fullName": f"{toolset_name}.{tool.get('name', '')}",
                "displayName": tool.get("name", "").replace("_", " ").title(),
                "description": tool.get("description", ""),
                "parameters": tool.get("parameters", []),
                "returns": tool.get("returns"),
                "tags": tool.get("tags", []),
            }
            for tool in tools
        ]

    return data


def _parse_request_json(request: Request, data: bytes) -> Dict[str, Any]:
    """Parse and validate JSON request body"""
    if not data:
        raise HTTPException(
            status_code=HttpStatusCode.BAD_REQUEST.value,
            detail="Request body is required"
        )

    try:
        return json.loads(data)
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=HttpStatusCode.BAD_REQUEST.value,
            detail=f"Invalid JSON in request body: {str(e)}"
        )


# ============================================================================
# Registry Endpoints
# ============================================================================

@router.get("/registry")
async def get_toolset_registry_endpoint(
    request: Request,
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(20, ge=1, le=200, description="Items per page"),
    search: Optional[str] = Query(None, description="Search term"),
    include_tools: bool = Query(True, description="Include full tool details"),
    include_tool_count: bool = Query(True, description="Include tool count"),
    group_by_category: bool = Query(True, description="Group by category"),
) -> Dict[str, Any]:
    """Get all available toolsets from registry"""
    registry = _get_registry(request)
    all_toolsets = registry.list_toolsets()

    toolsets_by_category = {}
    all_toolsets_list = []

    for toolset_name in all_toolsets:
        metadata = registry.get_toolset_metadata(toolset_name)
        if not metadata or metadata.get("isInternal", False):
            continue

        # Apply search filter
        if search:
            search_lower = search.lower()
            if not any(search_lower in str(metadata.get(field, "")).lower()
                      for field in ["display_name", "description", "group"]):
                continue

        toolset_data = _format_toolset_data(toolset_name, metadata, include_tools)

        if not include_tools and include_tool_count:
            toolset_data["tools"] = []

        all_toolsets_list.append(toolset_data)

        if group_by_category:
            category = toolset_data["category"]
            toolsets_by_category.setdefault(category, []).append(toolset_data)

    # Pagination
    total = len(all_toolsets_list)
    start = (page - 1) * limit
    paginated_toolsets = all_toolsets_list[start:start + limit]

    return {
        "status": "success",
        "toolsets": paginated_toolsets if not group_by_category else all_toolsets_list,
        "categorizedToolsets": toolsets_by_category if group_by_category else {},
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total,
            "totalPages": (total + limit - 1) // limit
        }
    }


@router.get("/registry/{toolset_type}/schema")
async def get_toolset_schema(toolset_type: str, request: Request) -> Dict[str, Any]:
    """Get schema/config for a specific toolset"""
    registry = _get_registry(request)
    metadata = _get_toolset_metadata(registry, toolset_type)

    oauth_registry = getattr(request.app.state, "oauth_config_registry", None)
    oauth_config = None
    if oauth_registry and oauth_registry.has_config(toolset_type):
        oauth_config = oauth_registry.get_metadata(toolset_type)

    return {
        "status": "success",
        "toolset": {
            "name": metadata["name"],
            "displayName": metadata["display_name"],
            "description": metadata["description"],
            "category": metadata["category"],
            "supportedAuthTypes": metadata["supported_auth_types"],
            "config": metadata.get("config", {}),
            "oauthConfig": oauth_config,
            "tools": metadata.get("tools", []),
        }
    }


@router.get("/tools")
async def get_all_tools(
    request: Request,
    app_name: Optional[str] = Query(None, description="Filter by app/toolset name"),
    tag: Optional[str] = Query(None, description="Filter by tag"),
    search: Optional[str] = Query(None, description="Search in name/description"),
) -> List[Dict[str, Any]]:
    """Get all available tools from registry (flat list)"""
    registry = _get_registry(request)
    tools_data = []

    for toolset_name in registry.list_toolsets():
        metadata = registry.get_toolset_metadata(toolset_name)
        if not metadata or metadata.get("isInternal", False):
            continue

        if app_name and toolset_name.lower() != app_name.lower():
            continue

        for tool_def in metadata.get("tools", []):
            tool_name = tool_def.get("name", "")
            tool_tags = tool_def.get("tags", [])

            if tag and tag not in tool_tags:
                continue

            if search:
                search_lower = search.lower()
                if not (search_lower in tool_name.lower() or
                       search_lower in tool_def.get("description", "").lower()):
                    continue

            tools_data.append({
                "app_name": toolset_name.lower(),
                "tool_name": tool_name,
                "full_name": f"{toolset_name.lower()}.{tool_name}",
                "description": tool_def.get("description", ""),
                "parameters": tool_def.get("parameters", []),
                "returns": tool_def.get("returns"),
                "examples": tool_def.get("examples", []),
                "tags": tool_tags,
            })

    return sorted(tools_data, key=lambda x: (x["app_name"], x["tool_name"]))


@router.get("/registry/{toolset_name}/tools")
async def get_toolset_tools(toolset_name: str, request: Request) -> Dict[str, Any]:
    """Get all tools for a specific toolset"""
    registry = _get_registry(request)
    metadata = _get_toolset_metadata(registry, toolset_name)

    tools = [
        {
            "name": tool.get("name", ""),
            "fullName": f"{toolset_name.lower()}.{tool.get('name', '')}",
            "description": tool.get("description", ""),
            "parameters": tool.get("parameters", []),
            "returns": tool.get("returns"),
            "examples": tool.get("examples", []),
            "tags": tool.get("tags", []),
        }
        for tool in metadata.get("tools", [])
    ]

    return {
        "status": "success",
        "toolset": toolset_name,
        "tools": tools,
        "totalCount": len(tools)
    }


# ============================================================================
# Toolset Instance Management
# ============================================================================

@router.get("/configured")
@inject
async def get_configured_toolsets(
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service])
) -> Dict[str, Any]:
    """Get all configured toolsets for authenticated user"""
    user_context = _get_user_context(request)
    registry = _get_registry(request)
    user_id = user_context["user_id"]

    # Get user's configured toolset keys
    kv_store = request.app.container.key_value_store()
    try:
        user_config_keys = await kv_store.list_keys_in_directory(_get_user_toolsets_prefix(user_id))
    except Exception as e:
        logger.error(f"Failed to list toolset configurations for user {user_id}: {e}")
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail="Failed to retrieve toolset configurations. Please try again later."
        )

    if not user_config_keys:
        return {"status": "success", "toolsets": []}

    # Fetch configs in parallel
    async def get_config_safe(path: str):
        try:
            return path, await config_service.get_config(path)
        except Exception as e:
            logger.warning(f"Failed to load config from {path}: {e}")
            return path, None

    config_results = await asyncio.gather(*[get_config_safe(key) for key in user_config_keys])
    all_configs = {k: v for k, v in config_results if v}

    toolsets = []
    for config_path, config_data in all_configs.items():
        toolset_type = config_path.split("/")[-1]
        metadata = registry.get_toolset_metadata(toolset_type)

        if not metadata or metadata.get("isInternal", False):
            continue

        auth_config = config_data.get("auth", {})
        toolsets.append({
            "_id": f"{user_id}_{toolset_type}",
            "name": toolset_type,
            "displayName": metadata.get("display_name", toolset_type),
            "description": metadata.get("description", ""),
            "category": metadata.get("category", ""),
            "iconPath": metadata.get("icon_path", ""),
            "authType": auth_config.get("type", "NONE"),
            "isAuthenticated": config_data.get("isAuthenticated", False),
            "isConfigured": True,
            "toolCount": len(metadata.get("tools", [])),
            "userId": user_id,
            "updatedAt": config_data.get("updatedAt"),
        })

    return {"status": "success", "toolsets": toolsets}


@router.get("/{toolset_type}/status")
@inject
async def get_toolset_status(
    toolset_type: str,
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service])
) -> Dict[str, Any]:
    """Check if toolset is configured and authenticated"""
    user_context = _get_user_context(request)
    registry = _get_registry(request)
    metadata = _get_toolset_metadata(registry, toolset_type)

    config_path = _get_config_path(user_context["user_id"], toolset_type)

    try:
        config = await config_service.get_config(config_path)
    except Exception as e:
        logger.error(f"Failed to get config for {toolset_type}: {e}")
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail=f"Failed to retrieve configuration for toolset '{toolset_type}'"
        )

    if not config:
        return {
            "status": "success",
            "isConfigured": False,
            "isAuthenticated": False,
            "toolsetName": toolset_type,
            "displayName": metadata.get("display_name", toolset_type)
        }

    return {
        "status": "success",
        "isConfigured": True,
        "isAuthenticated": config.get("isAuthenticated", False),
        "authType": config.get("auth", {}).get("type", "NONE"),
        "toolsetName": toolset_type,
        "displayName": metadata.get("display_name", toolset_type),
    }


@router.get("/{toolset_type}/config")
@inject
async def get_toolset_config(
    toolset_type: str,
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service])
) -> Dict[str, Any]:
    """Get toolset configuration and schema"""
    user_context = _get_user_context(request)
    toolset_type = toolset_type.lower()
    user_id = user_context["user_id"]

    registry = _get_registry(request)
    metadata = _get_toolset_metadata(registry, toolset_type)

    # Get config from etcd
    config_path = _get_config_path(user_id, toolset_type)
    try:
        config = await config_service.get_config(config_path)
    except Exception as e:
        logger.error(f"Failed to get config for {toolset_type}: {e}")
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail=f"Failed to retrieve configuration for toolset '{toolset_type}'"
        )

    # Remove sensitive fields
    safe_config = {}
    if config:
        safe_config = {k: v for k, v in config.items() if k not in ["credentials", "oauth"]}

    # Get OAuth config
    oauth_registry = getattr(request.app.state, "oauth_config_registry", None)
    oauth_config = None
    if oauth_registry and oauth_registry.has_config(toolset_type):
        oauth_config = oauth_registry.get_metadata(toolset_type)

    synthetic_id = f"{user_id}_{toolset_type}"
    registry_config = metadata.get("config", {})

    return {
        "status": "success",
        "toolset": {
            "toolsetId": synthetic_id,
            "_id": synthetic_id,
            "name": toolset_type,
            "displayName": metadata.get("display_name", toolset_type),
            "description": metadata.get("description", ""),
            "category": metadata.get("category", "app"),
            "group": metadata.get("group", ""),
            "iconPath": metadata.get("icon_path", "/assets/icons/toolsets/default.svg"),
            "supportedAuthTypes": metadata.get("supported_auth_types", []),
            "toolCount": len(metadata.get("tools", [])),
            "tools": metadata.get("tools", []),
            "userId": user_id,
            "createdBy": user_id,
            "config": safe_config or {"auth": {}, "isAuthenticated": False, "isConfigured": False},
            "schema": {
                "toolset": {
                    "name": toolset_type,
                    "displayName": metadata.get("display_name", toolset_type),
                    "description": metadata.get("description", ""),
                    "category": metadata.get("category", "app"),
                    "supportedAuthTypes": metadata.get("supported_auth_types", []),
                    "config": {"auth": registry_config.get("auth", {})},
                    "tools": metadata.get("tools", []),
                    "oauthConfig": oauth_config
                }
            },
            "oauthConfig": oauth_config,
            "isConfigured": bool(config),
            "isAuthenticated": config.get("isAuthenticated", False) if config else False,
            "authType": config.get("auth", {}).get("type") if config else None
        }
    }


# ============================================================================
# OAuth Flow
# ============================================================================

@router.get("/{toolset_type}/oauth/authorize")
@inject
async def get_oauth_authorization_url(
    toolset_type: str,
    request: Request,
    base_url: Optional[str] = Query(None, description="Base URL for redirect"),
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service])
) -> Dict[str, Any]:
    """Get OAuth authorization URL"""
    user_context = _get_user_context(request)
    toolset_type = toolset_type.lower()
    user_id = user_context["user_id"]

    # Get config
    config_path = _get_config_path(user_id, toolset_type)
    try:
        config = await config_service.get_config(config_path)
    except Exception as e:
        logger.error(f"Failed to get config for {toolset_type}: {e}")
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail="Failed to retrieve OAuth configuration"
        )

    if not config or not config.get("auth"):
        raise ToolsetConfigNotFoundError(toolset_type)

    auth_config = config["auth"]
    if auth_config.get("type", "").upper() != "OAUTH":
        raise OAuthConfigError(
            f"Toolset '{toolset_type}' is configured with {auth_config.get('type', 'NONE')} authentication, not OAuth"
        )

    # Build OAuth config
    registry = _get_registry(request)
    oauth_flow_config = await _build_oauth_config(auth_config, toolset_type, registry, base_url, request)

    # Generate authorization URL
    oauth_config = get_oauth_config(oauth_flow_config)
    if not oauth_config.scope and oauth_flow_config.get("scopes"):
        oauth_config.scope = " ".join(oauth_flow_config["scopes"])

    container = request.app.container
    oauth_provider = OAuthProvider(
        config=oauth_config,
        key_value_store=container.key_value_store(),
        credentials_path=config_path
    )

    try:
        auth_url = await oauth_provider.start_authorization()

        if not auth_url:
            raise OAuthConfigError("OAuth provider returned empty authorization URL")

        parsed_url = urlparse(auth_url)
        if not parsed_url.scheme or not parsed_url.netloc:
            raise OAuthConfigError("OAuth provider returned invalid authorization URL")

        query_params = parse_qs(parsed_url.query)

        # Clean up invalid parameters
        if "token_access_type" in query_params:
            if query_params["token_access_type"] in [["None"], [None], ["null"], [""]]:
                del query_params["token_access_type"]

        original_state = query_params.get("state", [None])[0]
        if not original_state:
            raise OAuthConfigError("OAuth state parameter is missing from authorization URL")

        # Encode state with toolset ID
        synthetic_id = f"{user_id}_{toolset_type}"
        encoded_state = _encode_state_with_toolset(original_state, synthetic_id)
        query_params["state"] = [encoded_state]

        final_url = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}?{urlencode(query_params, doseq=True)}"

        return {
            "success": True,
            "authorizationUrl": final_url,
            "state": encoded_state
        }
    except (OAuthConfigError, InvalidAuthConfigError):
        raise
    except Exception as e:
        logger.error(f"Unexpected error generating OAuth URL: {e}")
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail="Failed to generate OAuth authorization URL. Please try again."
        )
    finally:
        await oauth_provider.close()


@router.get("/oauth/callback")
@inject
async def handle_oauth_callback(
    request: Request,
    code: Optional[str] = Query(None, description="OAuth authorization code"),
    state: Optional[str] = Query(None, description="OAuth state parameter"),
    error: Optional[str] = Query(None, description="OAuth error"),
    base_url: Optional[str] = Query(None, description="Base URL for redirect"),
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service])
):
    """Handle OAuth callback and exchange code for tokens"""
    base_url = base_url or "http://localhost:3001"

    # Handle OAuth errors - return JSON instead of RedirectResponse
    if error and error not in ["null", "undefined", "None", ""]:
        logger.error(f"OAuth provider returned error: {error}")
        return {
            "success": False,
            "error": error,
            "redirect_url": f"{base_url}/tools?oauth_error={error}"
        }

    if not code or not state:
        logger.error("OAuth callback missing required parameters")
        return {
            "success": False,
            "error": "missing_parameters",
            "redirect_url": f"{base_url}/tools?oauth_error=missing_parameters"
        }

    try:
        user_context = _get_user_context(request)
        user_id = user_context["user_id"]

        # Decode state
        state_data = _decode_state_with_toolset(state)
        original_state = state_data["state"]
        synthetic_id = state_data["toolset_id"]

        # Parse synthetic ID
        parts = synthetic_id.rsplit("_", 1)
        if len(parts) != 2:
            raise OAuthConfigError("Invalid toolset ID format in OAuth state")

        toolset_user_id, toolset_type = parts
        toolset_type = toolset_type.lower()

        if toolset_user_id != user_id:
            raise HTTPException(
                status_code=HttpStatusCode.FORBIDDEN.value,
                detail="OAuth callback user mismatch. Authentication denied."
            )

        # Verify toolset exists
        registry = _get_registry(request)
        metadata = registry.get_toolset_metadata(toolset_type)
        if not metadata:
            raise ToolsetNotFoundError(toolset_type)

        # Get config
        config_path = _get_config_path(toolset_user_id, toolset_type)
        config = await config_service.get_config(config_path)

        if not config or not config.get("auth"):
            raise ToolsetConfigNotFoundError(toolset_type)

        # Build OAuth config
        oauth_flow_config = await _build_oauth_config(
            config["auth"], toolset_type, registry, base_url, request
        )

        # Exchange code for token
        container = request.app.container
        oauth_config = get_oauth_config(oauth_flow_config)
        oauth_provider = OAuthProvider(
            config=oauth_config,
            key_value_store=container.key_value_store(),
            credentials_path=config_path
        )

        try:
            token = await oauth_provider.handle_callback(code, original_state)
        finally:
            await oauth_provider.close()

        if not token or not token.access_token:
            raise OAuthConfigError("Failed to exchange authorization code for access token")

        if not token.refresh_token:
            logger.warning(f"No refresh_token received for {toolset_type}. Token refresh may fail.")

        # Update config with credentials
        try:
            kv_store = container.key_value_store()
            updated_config = await kv_store.get_key(config_path)

            if isinstance(updated_config, dict):
                updated_config["credentials"] = token.to_dict()
                updated_config["isAuthenticated"] = True
                updated_config["updatedAt"] = get_epoch_timestamp_in_ms()
                updated_config["updatedBy"] = toolset_user_id

                await kv_store.create_key(config_path, updated_config)
                await config_service.set_config(config_path, updated_config)
        except Exception as e:
            logger.error(f"Failed to save OAuth credentials: {e}")
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                detail="Authentication successful but failed to save credentials"
            )

        # Schedule token refresh
        try:
            from app.connectors.core.base.token_service.startup_service import (
                startup_service,
            )
            refresh_service = startup_service.get_toolset_token_refresh_service()
            if refresh_service:
                await refresh_service.schedule_token_refresh(config_path, toolset_type, token)
        except Exception as e:
            logger.error(f"Failed to schedule token refresh: {e}")

        # Return JSON response with redirect_url (matches connector pattern)
        return {
            "success": True,
            "redirect_url": f"{base_url}/tools?oauth_success=true&toolset_id={synthetic_id}"
        }

    except (ToolsetError, OAuthConfigError, InvalidAuthConfigError) as e:
        logger.error(f"OAuth callback error: {e.detail}")
        return {
            "success": False,
            "error": type(e).__name__,
            "redirect_url": f"{base_url}/tools?oauth_error={type(e).__name__}"
        }
    except HTTPException as e:
        logger.error(f"OAuth callback HTTP error: {e.detail}")
        return {
            "success": False,
            "error": "auth_failed",
            "redirect_url": f"{base_url}/tools?oauth_error=auth_failed"
        }
    except Exception as e:
        logger.error(f"Unexpected OAuth callback error: {e}")
        return {
            "success": False,
            "error": "server_error",
            "redirect_url": f"{base_url}/tools?oauth_error=server_error"
        }


# ============================================================================
# Configuration Management
# ============================================================================

@router.post("/")
@inject
async def create_toolset(
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service])
) -> Dict[str, Any]:
    """Create new toolset configuration"""
    user_context = _get_user_context(request)

    body_data = await request.body()
    body = _parse_request_json(request, body_data)

    toolset_name = body.get("name", "").strip()
    if not toolset_name:
        raise HTTPException(
            status_code=HttpStatusCode.BAD_REQUEST.value,
            detail="Toolset name is required"
        )

    auth_config = body.get("auth", {})
    auth_type = _validate_auth_config(auth_config)

    # Validate toolset exists
    from app.agents.constants.toolset_constants import normalize_app_name
    normalized_name = normalize_app_name(toolset_name)

    registry = _get_registry(request)
    _get_toolset_metadata(registry, normalized_name)

    # Check for existing config
    user_id = user_context["user_id"]
    config_path = _get_config_path(user_id, normalized_name)

    try:
        existing = await config_service.get_config(config_path)
        if existing:
            raise ToolsetAlreadyExistsError(toolset_name)
    except ToolsetAlreadyExistsError:
        raise
    except Exception as e:
        logger.error(f"Failed to check existing config: {e}")
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail="Failed to check existing configuration"
        )

    # Create config
    config = {
        "auth": auth_config,
        "isAuthenticated": auth_type == "API_TOKEN",
        "updatedAt": get_epoch_timestamp_in_ms(),
        "updatedBy": user_id
    }

    try:
        await config_service.set_config(config_path, config)
    except Exception as e:
        logger.error(f"Failed to save toolset config: {e}")
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail="Failed to save toolset configuration"
        )

    synthetic_id = f"{user_id}_{normalized_name}"
    return {
        "status": "success",
        "toolsetId": synthetic_id,
        "_id": synthetic_id,
        "message": "Toolset created successfully"
    }


@router.put("/{toolset_type}/config")
@inject
async def update_toolset_config(
    toolset_type: str,
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service])
) -> Dict[str, Any]:
    """Update toolset configuration"""
    user_context = _get_user_context(request)
    toolset_type = toolset_type.lower()

    body_data = await request.body()
    body = _parse_request_json(request, body_data)

    auth_config = body.get("auth", {})
    auth_type = _validate_auth_config(auth_config)

    # Validate toolset exists
    registry = _get_registry(request)
    _get_toolset_metadata(registry, toolset_type)

    # Get existing config
    user_id = user_context["user_id"]
    config_path = _get_config_path(user_id, toolset_type)

    try:
        existing = await config_service.get_config(config_path)
        if not existing:
            raise ToolsetConfigNotFoundError(toolset_type)
    except ToolsetConfigNotFoundError:
        raise
    except Exception as e:
        logger.error(f"Failed to get existing config: {e}")
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail="Failed to retrieve existing configuration"
        )

    # Update config
    config = {
        **existing,
        "auth": auth_config,
        "updatedAt": get_epoch_timestamp_in_ms(),
        "updatedBy": user_id
    }

    # Handle auth status
    if auth_type == "API_TOKEN":
        config["isAuthenticated"] = True
    elif auth_type == "OAUTH":
        config["isAuthenticated"] = False
        config.pop("credentials", None)

    try:
        await config_service.set_config(config_path, config)
    except Exception as e:
        logger.error(f"Failed to update toolset config: {e}")
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail="Failed to update toolset configuration"
        )

    return {"status": "success", "message": "Configuration updated successfully"}


@router.delete("/{toolset_type}/config")
@inject
async def delete_toolset_config(
    toolset_type: str,
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service]),
    arango_service: BaseArangoService = Depends(Provide[ConnectorAppContainer.arango_service])
) -> Dict[str, Any]:
    """Delete toolset configuration"""
    user_context = _get_user_context(request)
    toolset_type = toolset_type.lower()
    user_id = user_context["user_id"]

    # Safety check: verify no agents are using this toolset
    from app.agents.constants.toolset_constants import normalize_app_name
    normalized_name = normalize_app_name(toolset_type)

    try:
        agent_names = await arango_service.check_toolset_in_use(normalized_name, user_id)
        if agent_names:
            raise ToolsetInUseError(toolset_type, agent_names)
    except ToolsetInUseError:
        raise
    except Exception as e:
        logger.error(f"Failed to check toolset usage: {e}")
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail="Failed to verify if toolset can be deleted"
        )

    # Delete config
    config_path = _get_config_path(user_id, toolset_type)
    try:
        await config_service.delete_config(config_path)
    except Exception as e:
        logger.error(f"Failed to delete toolset config: {e}")
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail="Failed to delete toolset configuration"
        )

    return {"status": "success", "message": "Configuration deleted successfully"}
