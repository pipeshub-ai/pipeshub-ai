"""
Toolsets API Routes
Handles toolset registry, OAuth, configuration management, and tool retrieval.

Architecture:
  - Admin creates "toolset instances" (visible org-wide): POST /instances
  - OAuth config for each toolset type stored at /services/oauths/toolsets/{type}
  - Users authenticate against instances: POST /instances/{id}/authenticate (or OAuth)
  - User credentials stored at /services/toolsets/{userId}/{instanceId}
  - GET /my-toolsets returns merged view (instances + user auth status)
"""

import asyncio
import base64
import json
import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from app.agents.registry.toolset_registry import ToolsetRegistry
from app.api.middlewares.auth import require_scopes
from app.config.configuration_service import ConfigurationService
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import DefaultEndpoints, OAuthScopes
from app.connectors.core.base.token_service.oauth_service import (
    OAuthConfig,
    OAuthProvider,
)
from app.connectors.core.registry.auth_builder import OAuthScopeType
from app.containers.connector import ConnectorAppContainer
from app.utils.oauth_config import get_oauth_config
from app.utils.time_conversion import get_epoch_timestamp_in_ms

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/toolsets", tags=["toolsets"])

# Constants
MAX_AGENT_NAMES_DISPLAY = 3
SPLIT_PATH_EXPECTED_PARTS = 2
ENCRYPTED_KEY_PARTS_COUNT = 2

# OAuth Infrastructure Fields (not credentials)
OAUTH_INFRASTRUCTURE_FIELDS = frozenset({
    "type", "redirectUri", "redirect_uri", "scopes", "authorizeUrl",
    "authorize_url", "tokenUrl", "token_url", "additionalParams",
    "additional_params", "tokenAccessType", "token_access_type",
    "scopeParameterName", "scope_parameter_name", "tokenResponsePath",
    "token_response_path"
})

# Default values
DEFAULT_BASE_URL = "http://localhost:3001"
DEFAULT_ENDPOINTS_PATH = "/services/endpoints"
DEFAULT_TOOLSET_INSTANCES_PATH = "/services/toolset-instances"

# ============================================================================
# Toolset OAuth Credentials Helper (for Client Builders)
# ============================================================================
#
# ARCHITECTURE NOTE:
# ------------------
# OAuth client credentials (clientId, clientSecret) are stored centrally in
# OAuth configs at /services/oauths/toolsets/{toolsetType} to enable:
#   1. Single source of truth for credentials across all users
#   2. Admin can update credentials once, affecting all users
#   3. Secure storage - users never see or store these credentials
#
# User-specific auth data stored at /services/toolsets/{instanceId}/{userId}:
#   - credentials: { access_token, refresh_token, expires_in }
#   - isAuthenticated: bool
#   - instanceId: UUID of the toolset instance
#   - oauthConfigId: Reference to the central OAuth config
#
# This helper is used by client builders (GoogleClient, JiraClient, etc.) to
# fetch the OAuth credentials when creating authenticated API clients.
# ============================================================================

async def get_oauth_credentials_for_toolset(
    toolset_config: Dict[str, Any],
    config_service: ConfigurationService,
    logger: Optional[logging.Logger] = None
) -> Dict[str, Any]:
    """
    Fetch complete OAuth configuration for a toolset (all fields dynamically).

    RETURNS: Complete OAuth config with ALL fields, which may include:
    - clientId, clientSecret (common)
    - tenantId (Microsoft/Azure)
    - domain, workspace (Slack)
    - companyUrl, baseUrl (various)
    - redirectUri, scopes, authorizeUrl, tokenUrl (infrastructure)
    - Any other provider-specific fields

    PERFORMANCE: Makes 1-2 ETCD calls (OAuth config list, optionally instance list).

    EDGE CASE HANDLING:
    1. Stale oauthConfigId: Falls back to fetching current instance's config
    2. Missing oauthConfigId: Fetches from instance automatically
    3. Deleted OAuth config: Clear error message for admin
    4. Config switch: Always uses current instance's config as source of truth

    This is a utility function for client builders (GoogleClient, MSGraphClient, etc.)
    to get OAuth credentials when building from toolset configs.

    Flow:
    1. Check if credentials already in toolset_config.auth (backward compatibility)
    2. Try using oauthConfigId from toolset_config
    3. If not found or missing, fetch from current instance (handles admin config switches)
    4. Return the ENTIRE OAuth config (all fields dynamically)

    Args:
        toolset_config: User's toolset config from /services/toolsets/{instanceId}/{userId}
        config_service: ConfigurationService for ETCD access
        logger: Optional logger for debugging

    Returns:
        Dict with complete OAuth configuration (clientId, clientSecret, tenantId, etc.)

    Raises:
        ValueError: If OAuth configuration cannot be found or is invalid
    """
    if not toolset_config:
        raise ValueError("Toolset configuration is required")

    # Check if full OAuth config already in the auth config (backward compatibility or admin override)
    auth_config = toolset_config.get("auth", {})

    # If auth config has OAuth credentials, return them as-is (all fields)
    # This supports backward compatibility and admin overrides
    if auth_config and isinstance(auth_config, dict):
        # Check for presence of OAuth credentials (clientId or client_id)
        has_client_id = auth_config.get("clientId") or auth_config.get("client_id")
        has_client_secret = auth_config.get("clientSecret") or auth_config.get("client_secret")

        if has_client_id and has_client_secret:
            if logger:
                logger.debug("Using OAuth credentials from toolset auth config (legacy or override)")
            # Return entire auth config to preserve all fields (tenantId, domain, etc.)
            return dict(auth_config)

    # Get required identifiers
    oauth_config_id = toolset_config.get("oauthConfigId")
    toolset_type = toolset_config.get("toolsetType")
    instance_id = toolset_config.get("instanceId")

    if not toolset_type:
        raise ValueError(
            f"Toolset type not found in config. "
            f"Config keys: {list(toolset_config.keys())}. "
            f"This indicates a corrupted toolset configuration."
        )

    # If oauthConfigId is missing, fetch it from the current instance
    # This handles cases where:
    # - User config was created before we added oauthConfigId tracking
    # - Admin switched the instance's OAuth config (user has stale reference)
    if not oauth_config_id and instance_id:
        if logger:
            logger.warning(
                f"No oauthConfigId in user config for instance {instance_id}. "
                f"Fetching current instance's OAuth config (admin may have updated it)."
            )
        try:
            # Fetch the current instance to get its current oauthConfigId
            # This is an extra ETCD call but only happens in edge cases
            instances_path = DEFAULT_TOOLSET_INSTANCES_PATH
            instances = await config_service.get_config(instances_path, default=[])

            if isinstance(instances, list):
                current_instance = next(
                    (inst for inst in instances if inst.get("_id") == instance_id),
                    None
                )
                if current_instance:
                    oauth_config_id = current_instance.get("oauthConfigId")
                    if logger:
                        logger.info(
                            f"Retrieved current oauthConfigId '{oauth_config_id}' from instance {instance_id}"
                        )
        except Exception as e:
            if logger:
                logger.warning(f"Could not fetch instance to get oauthConfigId: {e}")

    if not oauth_config_id:
        raise ValueError(
            f"No oauthConfigId found in toolset config or instance. "
            f"Config keys: {list(toolset_config.keys())}. "
            f"Please reauthenticate or ask an administrator to configure OAuth for this toolset."
        )

    try:
        # Fetch OAuth config from ETCD
        oauth_config_path = _get_toolset_oauth_config_path(toolset_type)
        oauth_configs = await config_service.get_config(oauth_config_path, default=[], use_cache=False)

        if not isinstance(oauth_configs, list):
            raise ValueError(f"Invalid OAuth config format for toolset type '{toolset_type}'")

        # Find the specific OAuth config by ID
        oauth_config = next(
            (cfg for cfg in oauth_configs if cfg.get("_id") == oauth_config_id),
            None
        )

        if not oauth_config:
            # OAuth config was deleted or ID is wrong
            if logger:
                logger.error(
                    f"OAuth configuration '{oauth_config_id}' not found for toolset '{toolset_type}'. "
                    f"Available configs: {[c.get('_id') for c in oauth_configs]}"
                )
            raise ValueError(
                f"OAuth configuration '{oauth_config_id}' not found for toolset '{toolset_type}'. "
                f"This can happen if:\n"
                f"  1. The admin deleted the OAuth configuration\n"
                f"  2. The admin switched the instance to use a different OAuth config\n"
                f"  3. There's a configuration mismatch\n"
                f"Please reauthenticate this toolset to use the current OAuth configuration."
            )

        # Extract the complete config (all fields dynamically)
        config_data = oauth_config.get("config", {})

        if not config_data or not isinstance(config_data, dict):
            raise ValueError(
                f"OAuth configuration '{oauth_config_id}' has invalid or empty config data. "
                f"Please ask an administrator to update the OAuth configuration."
            )

        # Validate that at minimum, clientId and clientSecret are present
        # (but return ALL fields, not just these two)
        client_id = config_data.get("clientId") or config_data.get("client_id")
        client_secret = config_data.get("clientSecret") or config_data.get("client_secret")

        if not client_id or not client_secret:
            raise ValueError(
                f"OAuth configuration '{oauth_config_id}' is missing clientId or clientSecret. "
                f"Available config keys: {list(config_data.keys())}. "
                f"Please ask an administrator to update the OAuth configuration."
            )

        if logger:
            logger.debug(
                f"✅ Fetched complete OAuth config '{oauth_config_id}' "
                f"for toolset type '{toolset_type}' with fields: {list(config_data.keys())}"
            )

        # Return the ENTIRE config with all fields (clientId, clientSecret, tenantId, domain, etc.)
        return dict(config_data)

    except ValueError:
        # Re-raise ValueError with our custom messages
        raise
    except Exception as e:
        if logger:
            logger.error(f"Failed to fetch OAuth credentials: {e}", exc_info=True)
        raise ValueError(
            f"Failed to retrieve OAuth credentials for toolset: {str(e)}"
        ) from e


# ============================================================================
# Custom Exceptions
# ============================================================================

class ToolsetError(HTTPException):
    """Base exception for toolset operations"""
    def __init__(self, detail: str, status_code: int = 500) -> None:
        super().__init__(status_code=status_code, detail=detail)


class ToolsetNotFoundError(ToolsetError):
    """Toolset not found in registry"""
    def __init__(self, toolset_name: str) -> None:
        super().__init__(
            detail=f"Toolset '{toolset_name}' not found in registry",
            status_code=HttpStatusCode.NOT_FOUND.value
        )


class ToolsetConfigNotFoundError(ToolsetError):
    """Toolset configuration not found"""
    def __init__(self, toolset_name: str) -> None:
        super().__init__(
            detail=f"Toolset '{toolset_name}' is not configured. Please configure it first.",
            status_code=HttpStatusCode.NOT_FOUND.value
        )


class ToolsetAlreadyExistsError(ToolsetError):
    """Toolset configuration already exists"""
    def __init__(self, toolset_name: str) -> None:
        super().__init__(
            detail=f"Toolset '{toolset_name}' is already configured. Update the existing configuration instead.",
            status_code=HttpStatusCode.CONFLICT.value
        )


class ToolsetInUseError(ToolsetError):
    """Toolset is in use and cannot be deleted"""
    def __init__(self, toolset_name: str, agent_names: List[str]) -> None:
        if len(agent_names) == 1:
            detail = f"Cannot delete toolset '{toolset_name}': currently in use by agent '{agent_names[0]}'. Remove it from the agent first."
        else:
            names_display = ", ".join(f"'{n}'" for n in agent_names[:MAX_AGENT_NAMES_DISPLAY])
            if len(agent_names) > MAX_AGENT_NAMES_DISPLAY:
                names_display += f" and {len(agent_names) - MAX_AGENT_NAMES_DISPLAY} more"
            detail = f"Cannot delete toolset '{toolset_name}': currently in use by {len(agent_names)} agents ({names_display}). Remove it from all agents first."
        super().__init__(detail=detail, status_code=HttpStatusCode.CONFLICT.value)


class InvalidAuthConfigError(ToolsetError):
    """Invalid authentication configuration"""
    def __init__(self, message: str) -> None:
        super().__init__(
            detail=f"Invalid authentication configuration: {message}",
            status_code=HttpStatusCode.BAD_REQUEST.value
        )


class OAuthConfigError(ToolsetError):
    """OAuth configuration error"""
    def __init__(self, message: str) -> None:
        super().__init__(
            detail=f"OAuth configuration error: {message}",
            status_code=HttpStatusCode.BAD_REQUEST.value
        )


# ============================================================================
# Validation Functions
# ============================================================================

def _validate_non_empty_string(value: object, field_name: str) -> str:
    """Validate and return non-empty string, raise error if invalid."""
    if not value or not isinstance(value, str) or not value.strip():
        raise HTTPException(
            status_code=HttpStatusCode.BAD_REQUEST.value,
            detail=f"{field_name} is required and must be a non-empty string"
        )
    return value.strip()


def _validate_list(value: object, field_name: str, allow_empty: bool = True) -> List[Any]:
    """Validate and return list, raise error if invalid."""
    if value is None:
        if allow_empty:
            return []
        raise HTTPException(
            status_code=HttpStatusCode.BAD_REQUEST.value,
            detail=f"{field_name} is required"
        )
    if not isinstance(value, list):
        raise HTTPException(
            status_code=HttpStatusCode.BAD_REQUEST.value,
            detail=f"{field_name} must be a list"
        )
    return value


def _validate_dict(value: object, field_name: str, allow_empty: bool = True) -> Dict[str, Any]:
    """Validate and return dict, raise error if invalid."""
    if value is None:
        if allow_empty:
            return {}
        raise HTTPException(
            status_code=HttpStatusCode.BAD_REQUEST.value,
            detail=f"{field_name} is required"
        )
    if not isinstance(value, dict):
        raise HTTPException(
            status_code=HttpStatusCode.BAD_REQUEST.value,
            detail=f"{field_name} must be an object"
        )
    return value


def _has_oauth_credentials(auth_config: Dict[str, Any]) -> bool:
    """
    Check if auth_config contains actual OAuth credentials (not just infrastructure fields).
    Returns True if ANY credential field has a non-empty value.
    """
    if not auth_config or not isinstance(auth_config, dict):
        return False

    for field, value in auth_config.items():
        # Skip infrastructure fields
        if field in OAUTH_INFRASTRUCTURE_FIELDS:
            continue

        # Check if value is non-empty
        if isinstance(value, str) and value.strip():
            return True
        elif value not in (None, "", [], {}):
            return True

    return False


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


async def _check_user_is_admin(
    user_id: str,
    request: Request,
    config_service: ConfigurationService,
) -> bool:
    """
    Check if the current user is an admin by calling the Node.js CM backend.

    Calls GET /api/v1/users/{userId}/adminCheck with the user's auth token.
    Returns True if 200 (admin), False if 400/403 (not admin) or on error.

    Args:
        user_id: The user's MongoDB ObjectId string
        request: The incoming FastAPI request (to forward auth headers)
        config_service: ConfigurationService for reading the Node.js endpoint URL

    Returns:
        bool: True if the user is an admin, False otherwise
    """
    try:
        # Resolve Node.js CM backend URL from etcd config
        try:
            endpoints = await config_service.get_config(
                "/services/endpoints", use_cache=False
            )
            nodejs_url = (
                endpoints.get("nodejs", {}).get("endpoint")
                if isinstance(endpoints, dict)
                else None
            ) or DefaultEndpoints.NODEJS_ENDPOINT.value
        except Exception:
            nodejs_url = DefaultEndpoints.NODEJS_ENDPOINT.value

        # Forward the auth headers from the original request
        auth_headers: Dict[str, str] = {}
        for header_name in ("authorization", "x-organization-id", "cookie"):
            val = request.headers.get(header_name)
            if val:
                auth_headers[header_name] = val

        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{nodejs_url}/api/v1/users/{user_id}/adminCheck",
                headers=auth_headers,
            )
            return resp.status_code == HttpStatusCode.OK.value

    except Exception as e:
        logger.warning(
            f"Admin check via REST API failed for user {user_id}: {e}. Defaulting to non-admin."
        )
        return False


def _get_registry(request: Request) -> ToolsetRegistry:
    """Get and validate toolset registry from app state"""
    registry = getattr(request.app.state, "toolset_registry", None)
    if not registry:
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail="Toolset registry not initialized. Please contact system administrator."
        )
    return registry


def _get_toolset_metadata(registry: ToolsetRegistry, toolset_type: str) -> Dict[str, Any]:
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


# ============================================================================
# Storage Path Helpers
# ============================================================================

def _get_instances_path(org_id: str) -> str:
    """
    Etcd path for admin-created toolset instances.
    Single org mode: uses a single global path.

    Args:
        org_id: Organization ID (currently unused in single-org mode)

    Returns:
        str: The etcd path for toolset instances
    """
    return DEFAULT_TOOLSET_INSTANCES_PATH


async def _load_toolset_instances(
    org_id: str,
    config_service: ConfigurationService
) -> List[Dict[str, Any]]:
    """
    Load toolset instances from etcd with proper validation and error handling.

    Args:
        org_id: Organization ID
        config_service: Configuration service for etcd access

    Returns:
        List of toolset instance dictionaries

    Raises:
        HTTPException: If loading fails or data is invalid
    """
    instances_path = _get_instances_path(org_id)
    try:
        instances_data = await config_service.get_config(instances_path, default=[])
        instances = _validate_list(instances_data, "toolset instances")
        logger.debug(f"Loaded {len(instances)} toolset instances from {instances_path}")
        return instances
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to load toolset instances from {instances_path}: {e}", exc_info=True)
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail="Failed to access toolset instances. Please try again or contact support."
        )


def _get_user_auth_path(instance_id: str, user_id: str) -> str:
    """
    Etcd path for a user's auth/credentials for a specific toolset instance.
    Keyed by instanceId first so we can list all users of an instance via prefix.
    Path: /services/toolsets/{instanceId}/{userId}
    """
    return f"/services/toolsets/{instance_id}/{user_id}"


def _get_instance_users_prefix(instance_id: str) -> str:
    """
    Etcd prefix to list all user auth records for a given toolset instance.
    Path prefix: /services/toolsets/{instanceId}/
    """
    return f"/services/toolsets/{instance_id}/"


def _get_toolset_oauth_config_path(toolset_type: str) -> str:
    """Etcd path for OAuth config list for a toolset type"""
    return f"/services/oauths/toolsets/{toolset_type.lower()}"


def _generate_instance_id() -> str:
    return str(uuid.uuid4())


def _generate_oauth_config_id() -> str:
    return str(uuid.uuid4())


# ============================================================================
# Auth Config Helpers
# ============================================================================

def _get_oauth_config_from_registry(toolset_type: str, registry: ToolsetRegistry) -> OAuthConfig:
    """Get OAuth config from toolset registry (returns dataclass instance)"""
    metadata = registry.get_toolset_metadata(toolset_type, serialize=False)
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


async def _prepare_toolset_auth_config(
    auth_config: Dict[str, Any],
    toolset_type: str,
    registry,
    config_service: ConfigurationService,
    base_url: Optional[str] = None,
    request: Optional[Request] = None
) -> Dict[str, Any]:
    """
    Prepare and enrich toolset auth config with OAuth infrastructure fields.
    Only applies to OAUTH auth type.
    """
    auth_type = auth_config.get("type", "").upper()
    if auth_type != "OAUTH":
        return auth_config

    oauth_config = _get_oauth_config_from_registry(toolset_type, registry)
    redirect_path = oauth_config.redirect_uri
    redirect_uri = ""

    if redirect_path:
        if base_url and base_url.strip():
            base_url_clean = base_url.strip().rstrip('/')
            redirect_path_clean = redirect_path.lstrip('/')
            redirect_uri = f"{base_url_clean}/{redirect_path_clean}"
        else:
            try:
                endpoints = await config_service.get_config("/services/endpoints", use_cache=False)
                fallback_url = endpoints.get("frontend", {}).get("publicEndpoint", "http://localhost:3001")
                redirect_path_clean = redirect_path.lstrip('/')
                redirect_uri = f"{fallback_url.rstrip('/')}/{redirect_path_clean}"
            except Exception:
                redirect_path_clean = redirect_path.lstrip('/')
                redirect_uri = f"http://localhost:3001/{redirect_path_clean}"

    from app.connectors.core.registry.auth_builder import OAuthScopeType
    scopes = oauth_config.scopes.get_scopes_for_type(OAuthScopeType.AGENT)

    enriched_auth = {
        **auth_config,
        "authorizeUrl": oauth_config.authorize_url,
        "tokenUrl": oauth_config.token_url,
        "redirectUri": redirect_uri,
        "scopes": scopes,
    }

    if hasattr(oauth_config, 'additional_params') and oauth_config.additional_params:
        enriched_auth["additionalParams"] = oauth_config.additional_params
    if hasattr(oauth_config, 'token_access_type') and oauth_config.token_access_type:
        enriched_auth["tokenAccessType"] = oauth_config.token_access_type
    if hasattr(oauth_config, 'scope_parameter_name') and oauth_config.scope_parameter_name != "scope":
        enriched_auth["scopeParameterName"] = oauth_config.scope_parameter_name
    if hasattr(oauth_config, 'token_response_path') and oauth_config.token_response_path:
        enriched_auth["tokenResponsePath"] = oauth_config.token_response_path

    return enriched_auth


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

    redirect_uri = auth_config.get("redirectUri", "")
    if not redirect_uri:
        redirect_path = oauth_config.redirect_uri
        if base_url:
            redirect_uri = f"{base_url.rstrip('/')}/{redirect_path}"
        else:
            redirect_uri = f"http://localhost:3001/{redirect_path}"

    scopes = auth_config.get("scopes", [])
    if not scopes:
        scopes = oauth_config.scopes.get_scopes_for_type(OAuthScopeType.AGENT)

    config = {
        "clientId": client_id,
        "clientSecret": client_secret,
        "redirectUri": redirect_uri,
        "scopes": scopes,
        "authorizeUrl": auth_config.get("authorizeUrl") or oauth_config.authorize_url,
        "tokenUrl": auth_config.get("tokenUrl") or oauth_config.token_url,
        "name": toolset_type,
    }

    if "additionalParams" in auth_config:
        config["additionalParams"] = auth_config["additionalParams"]
    elif hasattr(oauth_config, 'additional_params') and oauth_config.additional_params:
        config["additionalParams"] = oauth_config.additional_params

    if "tokenAccessType" in auth_config:
        config["tokenAccessType"] = auth_config["tokenAccessType"]
    elif hasattr(oauth_config, 'token_access_type') and oauth_config.token_access_type:
        if "access_type" not in config.get("additionalParams", {}):
            config["tokenAccessType"] = oauth_config.token_access_type

    if "scopeParameterName" in auth_config:
        config["scopeParameterName"] = auth_config["scopeParameterName"]
    elif hasattr(oauth_config, 'scope_parameter_name') and oauth_config.scope_parameter_name != "scope":
        config["scopeParameterName"] = oauth_config.scope_parameter_name

    if "tokenResponsePath" in auth_config:
        config["tokenResponsePath"] = auth_config["tokenResponsePath"]
    elif hasattr(oauth_config, 'token_response_path') and oauth_config.token_response_path:
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
# OAuth Config Management (for toolset types)
# ============================================================================

async def _get_oauth_configs_for_type(
    toolset_type: str,
    config_service: ConfigurationService
) -> List[Dict[str, Any]]:
    """Get the list of OAuth configs stored for a toolset type."""
    path = _get_toolset_oauth_config_path(toolset_type)
    try:
        configs = await config_service.get_config(path, default=[], use_cache=False)
        return configs if isinstance(configs, list) else []
    except Exception:
        return []


async def _create_or_update_toolset_oauth_config(
    toolset_type: str,
    auth_config: Dict[str, Any],
    instance_name: str,
    user_id: str,
    org_id: str,
    config_service: ConfigurationService,
    registry,
    base_url: str,
    oauth_config_id: Optional[str] = None,
) -> Optional[str]:
    """
    Create or update an OAuth config for a toolset type.
    Returns the OAuth config _id.
    """
    try:
        oauth_configs = await _get_oauth_configs_for_type(toolset_type, config_service)

        if oauth_config_id:
            # Try to update existing
            for idx, cfg in enumerate(oauth_configs):
                if cfg.get("_id") == oauth_config_id and cfg.get("orgId") == org_id:
                    if "config" not in cfg:
                        cfg["config"] = {}
                    # Enrich with infrastructure fields
                    enriched = await _prepare_toolset_auth_config(
                        auth_config, toolset_type, registry, config_service, base_url
                    )
                    # Update all fields dynamically (preserve clientSecret if not provided)
                    for k, v in enriched.items():
                        if k == "type":
                            continue  # Skip type field
                        if k == "clientSecret" and (not v or not str(v).strip()):
                            continue  # Keep existing clientSecret if not provided
                        cfg["config"][k] = v
                    cfg["updatedAtTimestamp"] = get_epoch_timestamp_in_ms()
                    oauth_configs[idx] = cfg
                    path = _get_toolset_oauth_config_path(toolset_type)
                    await config_service.set_config(path, oauth_configs)
                    return oauth_config_id
            # Not found - fall through to create
            logger.warning(f"OAuth config {oauth_config_id} not found for {toolset_type}, creating new one")

        # Create new OAuth config
        enriched = await _prepare_toolset_auth_config(
            auth_config, toolset_type, registry, config_service, base_url
        )
        # Store all fields dynamically (except type)
        config_data = {k: v for k, v in enriched.items() if k != "type"}
        new_cfg = {
            "_id": _generate_oauth_config_id(),
            "oauthInstanceName": instance_name,
            "toolsetType": toolset_type,
            "userId": user_id,
            "orgId": org_id,
            "config": config_data,
            "createdAtTimestamp": get_epoch_timestamp_in_ms(),
            "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
        }
        oauth_configs.append(new_cfg)
        path = _get_toolset_oauth_config_path(toolset_type)
        await config_service.set_config(path, oauth_configs)
        return new_cfg["_id"]

    except Exception as e:
        logger.error(f"Error creating/updating toolset OAuth config: {e}", exc_info=True)
        return None


async def _get_oauth_config_by_id(
    toolset_type: str,
    oauth_config_id: str,
    org_id: str,
    config_service: ConfigurationService
) -> Optional[Dict[str, Any]]:
    """Find an OAuth config by its _id within a toolset type."""
    configs = await _get_oauth_configs_for_type(toolset_type, config_service)
    for cfg in configs:
        if cfg.get("_id") == oauth_config_id and cfg.get("orgId") == org_id:
            return cfg
    return None


def _check_instance_name_conflict(
    instances: List[Dict[str, Any]],
    name: str,
    org_id: str,
    toolset_type: str,
    exclude_id: Optional[str] = None
) -> bool:
    """
    Return True if the instance name already exists for the same toolset type in the org.
    Instance names must be unique per toolset type, not globally.
    """
    for inst in instances:
        if inst.get("orgId") != org_id:
            continue
        if inst.get("toolsetType") != toolset_type:
            continue
        if inst.get("instanceName", "").lower() == name.lower():
            if exclude_id and inst.get("_id") == exclude_id:
                continue
            return True
    return False


def _check_oauth_name_conflict(
    oauth_configs: List[Dict[str, Any]],
    name: str,
    org_id: str,
    exclude_id: Optional[str] = None
) -> bool:
    """Return True if the OAuth config instance name already exists in the org."""
    for cfg in oauth_configs:
        if cfg.get("orgId") != org_id:
            continue
        if cfg.get("oauthInstanceName", "").lower() == name.lower():
            if exclude_id and cfg.get("_id") == exclude_id:
                continue
            return True
    return False


# ============================================================================
# State Encoding for OAuth Callback
# ============================================================================

def _encode_state_with_instance(state: str, instance_id: str, user_id: str) -> str:
    """Encode OAuth state with instance ID and user ID."""
    try:
        state_data = {"state": state, "instance_id": instance_id, "user_id": user_id}
        return base64.urlsafe_b64encode(json.dumps(state_data).encode()).decode()
    except Exception as e:
        raise OAuthConfigError(f"Failed to encode OAuth state: {str(e)}")


def _decode_state_with_instance(encoded_state: str) -> Dict[str, str]:
    """Decode OAuth state to extract original state, instance ID, and user ID."""
    try:
        decoded = base64.urlsafe_b64decode(encoded_state.encode()).decode()
        state_data = json.loads(decoded)
        if "state" not in state_data or "instance_id" not in state_data or "user_id" not in state_data:
            raise ValueError("Missing required fields in state data")
        return state_data
    except json.JSONDecodeError:
        raise OAuthConfigError("Invalid OAuth state format: not valid JSON")
    except Exception as e:
        raise OAuthConfigError(f"Failed to decode OAuth state: {str(e)}")


# ============================================================================
# Registry Endpoints
# ============================================================================

@router.get("/registry", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
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


@router.get("/registry/{toolset_type}/schema", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
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


@router.get("/tools", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
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


@router.get("/registry/{toolset_name}/tools", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
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
# Toolset Instance Management (Admin-Created Instances)
# ============================================================================

@router.post("/instances", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
@inject
async def create_toolset_instance(
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service])
) -> Dict[str, Any]:
    """
    Admin creates a toolset instance.
    - Validates the toolset type exists in registry
    - For OAUTH auth type, creates/links an OAuth config
    - Stores instance metadata at /services/toolset-instances/{orgId} (list)
    - Instance name and OAuth config name must be unique within the org
    """
    user_context = _get_user_context(request)
    is_admin = await _check_user_is_admin(user_context["user_id"], request, config_service)
    if not is_admin:
        raise HTTPException(
            status_code=HttpStatusCode.FORBIDDEN.value,
            detail="Only administrators can create toolset instances."
        )

    body_data = await request.body()
    body = _parse_request_json(request, body_data)

    # Validate required fields explicitly
    instance_name = _validate_non_empty_string(body.get("instanceName"), "instanceName")
    toolset_type = _validate_non_empty_string(body.get("toolsetType"), "toolsetType").lower()
    auth_type = _validate_non_empty_string(body.get("authType"), "authType").upper()

    # Optional fields with explicit defaults
    base_url = body.get("baseUrl", "").strip() if body.get("baseUrl") else ""
    auth_config = _validate_dict(body.get("authConfig"), "authConfig", allow_empty=True)
    oauth_config_id_from_body = body.get("oauthConfigId", "").strip() if body.get("oauthConfigId") else None
    oauth_instance_name = body.get("oauthInstanceName", "").strip() if body.get("oauthInstanceName") else ""

    # Validate toolset type exists
    registry = _get_registry(request)
    metadata = _get_toolset_metadata(registry, toolset_type)

    supported = [a.upper() for a in metadata.get("supported_auth_types", [])]
    if auth_type not in supported and auth_type != "NONE":
        raise HTTPException(
            status_code=HttpStatusCode.BAD_REQUEST.value,
            detail=f"Auth type '{auth_type}' is not supported by toolset '{toolset_type}'. Supported: {supported}"
        )

    user_id = user_context["user_id"]
    org_id = user_context["org_id"]

    # Load existing instances for conflict check
    instances = await _load_toolset_instances(org_id, config_service)

    # Check instance name uniqueness within org and toolset type
    if _check_instance_name_conflict(instances, instance_name, org_id, toolset_type):
        raise HTTPException(
            status_code=HttpStatusCode.CONFLICT.value,
            detail=f"A toolset instance named '{instance_name}' already exists for toolset type '{toolset_type}' in this organization."
        )

    # Handle OAuth config creation/selection
    oauth_config_id: Optional[str] = None
    if auth_type == "OAUTH":
        # Resolve base_url for OAuth redirects
        if not base_url:
            try:
                endpoints = await config_service.get_config(DEFAULT_ENDPOINTS_PATH, use_cache=False)
                if isinstance(endpoints, dict) and "frontend" in endpoints:
                    frontend_config = endpoints.get("frontend", {})
                    if isinstance(frontend_config, dict):
                        base_url = frontend_config.get("publicEndpoint") or DEFAULT_BASE_URL
                    else:
                        base_url = DEFAULT_BASE_URL
                else:
                    base_url = DEFAULT_BASE_URL
                logger.info(f"Resolved base_url for OAuth: {base_url}")
            except Exception as e:
                logger.warning(f"Failed to resolve frontend endpoint from config: {e}. Using default: {DEFAULT_BASE_URL}")
                base_url = DEFAULT_BASE_URL

        # Case 1: Use existing OAuth config
        if oauth_config_id_from_body:
            # Validate the OAuth config exists
            existing_config = await _get_oauth_config_by_id(toolset_type, oauth_config_id_from_body, org_id, config_service)
            if not existing_config:
                raise HTTPException(
                    status_code=HttpStatusCode.NOT_FOUND.value,
                    detail=f"OAuth configuration '{oauth_config_id_from_body}' not found."
                )
            oauth_config_id = oauth_config_id_from_body
            logger.info(f"Using existing OAuth config {oauth_config_id} for instance {instance_name}")

        # Case 2: Create new OAuth config (if credentials provided)
        else:
            # Determine OAuth instance name (separate from toolset instance name)
            oauth_app_name = oauth_instance_name or instance_name

            # Check OAuth config name uniqueness
            oauth_configs = await _get_oauth_configs_for_type(toolset_type, config_service)
            if _check_oauth_name_conflict(oauth_configs, oauth_app_name, org_id):
                raise HTTPException(
                    status_code=HttpStatusCode.CONFLICT.value,
                    detail=f"An OAuth configuration named '{oauth_app_name}' already exists for toolset '{toolset_type}'."
                )

            # Build auth_config from body fields if provided inline
            if not auth_config:
                auth_config = {
                    "type": "OAUTH",
                    "clientId": body.get("clientId", ""),
                    "clientSecret": body.get("clientSecret", ""),
                }
            else:
                auth_config["type"] = "OAUTH"

            # Check if actual credentials (not just infrastructure fields) are provided
            if _has_oauth_credentials(auth_config):
                credential_fields = [k for k in auth_config if k not in OAUTH_INFRASTRUCTURE_FIELDS]
                logger.info(
                    f"Creating OAuth config '{oauth_app_name}' for instance '{instance_name}' "
                    f"with credential fields: {credential_fields}"
                )

                oauth_config_id = await _create_or_update_toolset_oauth_config(
                    toolset_type=toolset_type,
                    auth_config=auth_config,
                    instance_name=oauth_app_name,
                    user_id=user_id,
                    org_id=org_id,
                    config_service=config_service,
                    registry=registry,
                    base_url=base_url,
                )

                if oauth_config_id:
                    logger.info(
                        f"Successfully created OAuth config (ID: {oauth_config_id}) "
                        f"for toolset instance '{instance_name}'"
                    )
                else:
                    logger.error(
                        f"Failed to create OAuth config for instance '{instance_name}'. "
                        f"Proceeding without OAuth config."
                    )
            else:
                logger.info(
                    f"No OAuth credentials provided for instance '{instance_name}'. "
                    f"Admin must configure OAuth credentials before users can authenticate."
                )

    # Build and save the instance
    now = get_epoch_timestamp_in_ms()
    new_instance: Dict[str, Any] = {
        "_id": _generate_instance_id(),
        "instanceName": instance_name,
        "toolsetType": toolset_type,
        "authType": auth_type,
        "orgId": org_id,
        "createdBy": user_id,
        "createdAtTimestamp": now,
        "updatedAtTimestamp": now,
    }
    if oauth_config_id:
        new_instance["oauthConfigId"] = oauth_config_id

    instances.append(new_instance)

    instances_path = _get_instances_path(org_id)
    try:
        await config_service.set_config(instances_path, instances)
    except Exception as e:
        logger.error(f"Failed to save toolset instance to {instances_path}: {e}", exc_info=True)
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail="Failed to save toolset instance. Please try again."
        )

    return {
        "status": "success",
        "instance": new_instance,
        "message": "Toolset instance created successfully."
    }


@router.get("/instances", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
@inject
async def get_toolset_instances(
    request: Request,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    search: Optional[str] = Query(None),
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service])
) -> Dict[str, Any]:
    """Get all admin-created toolset instances for the organization."""
    user_context = _get_user_context(request)
    org_id = user_context["org_id"]

    instances = await _load_toolset_instances(org_id, config_service)

    # Filter by org (safety)
    instances = [i for i in instances if i.get("orgId") == org_id]

    # Apply search
    if search:
        search_lower = search.lower()
        instances = [
            i for i in instances
            if search_lower in i.get("instanceName", "").lower()
            or search_lower in i.get("toolsetType", "").lower()
        ]

    # Add registry metadata
    registry = _get_registry(request)
    enriched = []
    for inst in instances:
        toolset_type = inst.get("toolsetType", "")
        meta = registry.get_toolset_metadata(toolset_type)
        enriched.append({
            **inst,
            "displayName": meta.get("display_name", toolset_type) if meta else toolset_type,
            "description": meta.get("description", "") if meta else "",
            "iconPath": meta.get("icon_path", "") if meta else "",
            "toolCount": len(meta.get("tools", [])) if meta else 0,
        })

    total = len(enriched)
    start = (page - 1) * limit
    page_items = enriched[start:start + limit]

    return {
        "status": "success",
        "instances": page_items,
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total,
            "totalPages": (total + limit - 1) // limit,
            "hasNext": start + limit < total,
            "hasPrev": page > 1,
        }
    }


@router.get("/instances/{instance_id}", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
@inject
async def get_toolset_instance(
    instance_id: str,
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service])
) -> Dict[str, Any]:
    """
    Get a specific toolset instance.
    Admins also receive the full OAuth config data and authenticatedUserCount.
    """
    user_context = _get_user_context(request)
    org_id = user_context["org_id"]
    is_admin = await _check_user_is_admin(user_context["user_id"], request, config_service)

    instances = await _load_toolset_instances(org_id, config_service)

    instance = next((i for i in instances if i.get("_id") == instance_id and i.get("orgId") == org_id), None)
    if not instance:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail=f"Toolset instance '{instance_id}' not found.")

    registry = _get_registry(request)
    toolset_type = instance.get("toolsetType", "")
    meta = registry.get_toolset_metadata(toolset_type)

    result: Dict[str, Any] = {
        **instance,
        "displayName": meta.get("display_name", toolset_type) if meta else toolset_type,
        "description": meta.get("description", "") if meta else "",
        "iconPath": meta.get("icon_path", "") if meta else "",
        "supportedAuthTypes": meta.get("supported_auth_types", []) if meta else [],
        "toolCount": len(meta.get("tools", [])) if meta else 0,
    }

    # For admins: include OAuth config data (mask clientSecret) and user count
    if is_admin and instance.get("authType") == "OAUTH":
        oauth_config_id = instance.get("oauthConfigId")
        if oauth_config_id:
            try:
                oauth_cfg = await _get_oauth_config_by_id(toolset_type, oauth_config_id, org_id, config_service)
                if oauth_cfg:
                    cfg_data = oauth_cfg.get("config", {})
                    # Return all fields dynamically (admins see clientSecret)
                    oauth_config_dict = {
                        "_id": oauth_cfg.get("_id"),
                        "oauthInstanceName": oauth_cfg.get("oauthInstanceName"),
                    }
                    # Add all config fields dynamically (include clientSecret for admins)
                    for key, value in cfg_data.items():
                        oauth_config_dict[key] = value
                    # Also add clientSecretSet flag for backward compatibility
                    if "clientSecret" in cfg_data:
                        oauth_config_dict["clientSecretSet"] = bool(cfg_data["clientSecret"])
                    result["oauthConfig"] = oauth_config_dict
            except Exception as e:
                logger.warning(f"Could not load OAuth config for instance {instance_id}: {e}")

        # Count authenticated users via prefix scan
        try:
            prefix = _get_instance_users_prefix(instance_id)
            user_keys = await config_service.list_keys_in_directory(prefix)
            result["authenticatedUserCount"] = len(user_keys)
        except Exception as e:
            logger.warning(f"Could not count authenticated users for instance {instance_id}: {e}")
            result["authenticatedUserCount"] = 0

    return {"status": "success", "instance": result}


async def _deauth_all_instance_users(
    instance_id: str,
    config_service: ConfigurationService
) -> int:
    """
    Deauthenticate all users who have authenticated against the given instance.
    Lists all user auth records under /services/toolsets/{instanceId}/ and
    sets isAuthenticated=False in parallel.

    Returns: number of users deauthenticated.
    """
    prefix = _get_instance_users_prefix(instance_id)
    try:
        user_keys = await config_service.list_keys_in_directory(prefix)
    except Exception as e:
        logger.error(f"Could not list user auth keys for instance {instance_id}: {e}")
        return 0

    if not user_keys:
        return 0

    now = get_epoch_timestamp_in_ms()

    async def _deauth_one(key: str) -> None:
        try:
            auth = await config_service.get_config(key, default=None, use_cache=False)
            if not auth or not isinstance(auth, dict):
                return
            auth["isAuthenticated"] = False
            auth["deauthReason"] = "admin_oauth_config_updated"
            auth["deauthAt"] = now
            await config_service.set_config(key, auth)
        except Exception as e:
            logger.warning(f"Could not deauth user at key {key}: {e}")

    await asyncio.gather(*[_deauth_one(k) for k in user_keys])
    return len(user_keys)


@router.put("/instances/{instance_id}", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
@inject
async def update_toolset_instance(
    instance_id: str,
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service])
) -> Dict[str, Any]:
    """
    Admin updates a toolset instance.
    Supports:
      - Renaming the instance (instanceName)
      - Updating OAuth credentials (authConfig with clientId/clientSecret)
      - Switching to a different existing OAuth config (oauthConfigId)

    When OAuth credentials are updated OR oauthConfigId changes, ALL authenticated
    users for this instance will be deauthenticated in parallel so they must
    re-authenticate with the new credentials.
    """
    user_context = _get_user_context(request)
    is_admin = await _check_user_is_admin(user_context["user_id"], request, config_service)
    if not is_admin:
        raise HTTPException(status_code=HttpStatusCode.FORBIDDEN.value, detail="Only administrators can update toolset instances.")

    org_id = user_context["org_id"]
    user_id = user_context["user_id"]

    body_data = await request.body()
    body = _parse_request_json(request, body_data)

    instances = await _load_toolset_instances(org_id, config_service)

    idx = next((i for i, inst in enumerate(instances) if inst.get("_id") == instance_id and inst.get("orgId") == org_id), None)
    if idx is None:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail=f"Toolset instance '{instance_id}' not found.")

    instance = instances[idx]
    toolset_type = instance.get("toolsetType", "")
    auth_type = instance.get("authType", "")

    # --- Rename ---
    new_name = body.get("instanceName", "").strip()
    toolset_type = instance.get("toolsetType", "")
    if new_name and new_name.lower() != instance.get("instanceName", "").lower():
        if _check_instance_name_conflict(instances, new_name, org_id, toolset_type, exclude_id=instance_id):
            raise HTTPException(
                status_code=HttpStatusCode.CONFLICT.value,
                detail=f"A toolset instance named '{new_name}' already exists for toolset type '{toolset_type}'."
            )
        instance["instanceName"] = new_name

    # --- OAuth config switch (link to existing config by ID) ---
    new_oauth_config_id_from_body = body.get("oauthConfigId", "").strip() if auth_type == "OAUTH" else ""
    oauth_credentials_changed = False

    if auth_type == "OAUTH":
        # Resolve base_url for OAuth redirects
        base_url = body.get("baseUrl", "").strip() if body.get("baseUrl") else ""
        if not base_url:
            try:
                endpoints = await config_service.get_config(DEFAULT_ENDPOINTS_PATH, use_cache=False)
                if isinstance(endpoints, dict) and "frontend" in endpoints:
                    frontend_config = endpoints.get("frontend", {})
                    if isinstance(frontend_config, dict):
                        base_url = frontend_config.get("publicEndpoint") or DEFAULT_BASE_URL
                    else:
                        base_url = DEFAULT_BASE_URL
                else:
                    base_url = DEFAULT_BASE_URL
            except Exception as e:
                logger.warning(f"Failed to resolve frontend endpoint: {e}. Using default: {DEFAULT_BASE_URL}")
                base_url = DEFAULT_BASE_URL

        # Case 1: Admin explicitly sets a different oauthConfigId (switch to existing config)
        if new_oauth_config_id_from_body and new_oauth_config_id_from_body != instance.get("oauthConfigId"):
            existing_cfg = await _get_oauth_config_by_id(toolset_type, new_oauth_config_id_from_body, org_id, config_service)
            if not existing_cfg:
                raise HTTPException(
                    status_code=HttpStatusCode.NOT_FOUND.value,
                    detail=f"OAuth configuration '{new_oauth_config_id_from_body}' not found for toolset '{toolset_type}'."
                )
            instance["oauthConfigId"] = new_oauth_config_id_from_body
            oauth_credentials_changed = True

        # Case 2: Admin provides new credentials (create or update the linked OAuth config)
        auth_config = body.get("authConfig", {})
        if auth_config:
            registry = _get_registry(request)
            auth_config["type"] = "OAUTH"
            current_oauth_config_id = instance.get("oauthConfigId")

            new_oauth_id = await _create_or_update_toolset_oauth_config(
                toolset_type=toolset_type,
                auth_config=auth_config,
                instance_name=instance.get("instanceName", instance_id),
                user_id=user_id,
                org_id=org_id,
                config_service=config_service,
                registry=registry,
                base_url=base_url,
                oauth_config_id=current_oauth_config_id,
            )
            if new_oauth_id:
                if new_oauth_id != instance.get("oauthConfigId"):
                    instance["oauthConfigId"] = new_oauth_id
                oauth_credentials_changed = True

    instance["updatedAtTimestamp"] = get_epoch_timestamp_in_ms()
    instances[idx] = instance

    instances_path = _get_instances_path(org_id)
    try:
        await config_service.set_config(instances_path, instances)
    except Exception as e:
        logger.error(f"Failed to update toolset instance in {instances_path}: {e}", exc_info=True)
        raise HTTPException(status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail="Failed to update toolset instance. Please try again.")

    # Deauthenticate all users in parallel if credentials changed
    deauthed_count = 0
    if oauth_credentials_changed:
        deauthed_count = await _deauth_all_instance_users(instance_id, config_service)
        if deauthed_count > 0:
            logger.info(f"Deauthenticated {deauthed_count} users for instance {instance_id} after OAuth config update.")

    msg = "Toolset instance updated successfully."
    if deauthed_count > 0:
        msg += f" {deauthed_count} user(s) have been deauthenticated and must re-authenticate."

    return {
        "status": "success",
        "instance": instance,
        "message": msg,
        "deauthenticatedUserCount": deauthed_count,
    }


@router.delete("/instances/{instance_id}", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_DELETE))])
@inject
async def delete_toolset_instance(
    instance_id: str,
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service])
) -> Dict[str, Any]:
    """
    Admin deletes a toolset instance.
    SAFE DELETE: Rejects deletion if any user has authenticated against this instance
    (i.e. any key exists under /services/toolsets/{instanceId}/).
    """
    user_context = _get_user_context(request)
    is_admin = await _check_user_is_admin(user_context["user_id"], request, config_service)
    if not is_admin:
        raise HTTPException(status_code=HttpStatusCode.FORBIDDEN.value, detail="Only administrators can delete toolset instances.")

    org_id = user_context["org_id"]

    instances = await _load_toolset_instances(org_id, config_service)

    instance = next((i for i in instances if i.get("_id") == instance_id and i.get("orgId") == org_id), None)
    if not instance:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail=f"Toolset instance '{instance_id}' not found.")

    # Safe-delete check: block if any user has authenticated against this instance
    try:
        prefix = _get_instance_users_prefix(instance_id)
        user_keys = await config_service.list_keys_in_directory(prefix)
        if user_keys:
            raise HTTPException(
                status_code=HttpStatusCode.CONFLICT.value,
                detail=(
                    f"Cannot delete toolset instance '{instance.get('instanceName', instance_id)}': "
                    f"{len(user_keys)} user(s) have authenticated against it. "
                    "Please remove all user credentials before deleting the instance."
                )
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Could not check user auth records for instance {instance_id}: {e}")
        # Proceed with deletion if we can't verify (fail-open for listing errors)

    updated = [i for i in instances if i.get("_id") != instance_id]

    instances_path = _get_instances_path(org_id)
    try:
        # set_config will automatically invalidate cache for this path
        await config_service.set_config(instances_path, updated)
        logger.info(f"Successfully deleted toolset instance {instance_id} ('{instance.get('instanceName', instance_id)}') from org {org_id}")
    except Exception as e:
        logger.error(f"Failed to delete toolset instance from {instances_path}: {e}", exc_info=True)
        raise HTTPException(status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail="Failed to delete toolset instance. Please try again.")

    return {"status": "success", "message": "Toolset instance deleted successfully.", "instanceId": instance_id}


# ============================================================================
# My Toolsets - Merged View (Instances + User Auth Status)
# ============================================================================

@router.get("/my-toolsets", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
@inject
async def get_my_toolsets(
    request: Request,
    search: Optional[str] = Query(None),
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service])
) -> Dict[str, Any]:
    """
    Returns all admin-created toolset instances merged with the current user's
    authentication status for each instance.

    Response shape per item:
      instanceId, instanceName, toolsetType, authType, oauthConfigId,
      displayName, description, iconPath, toolCount,
      isAuthenticated, isConfigured (always true for admin-created instances)
    """
    user_context = _get_user_context(request)
    org_id = user_context["org_id"]
    user_id = user_context["user_id"]

    # Load admin-created instances
    instances = await _load_toolset_instances(org_id, config_service)

    # Filter by org (safety check)
    instances = [i for i in instances if i.get("orgId") == org_id]

    # Apply search
    if search:
        search_lower = search.lower()
        instances = [
            i for i in instances
            if search_lower in i.get("instanceName", "").lower()
            or search_lower in i.get("toolsetType", "").lower()
        ]

    if not instances:
        return {"status": "success", "toolsets": []}

    registry = _get_registry(request)

    # Fetch user auth for all instances in parallel
    async def _fetch_user_auth(inst: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
        iid = inst.get("_id", "")
        try:
            path = _get_user_auth_path(iid, user_id)
            auth = await config_service.get_config(path, default=None)
            return inst, auth
        except Exception:
            return inst, None

    results = await asyncio.gather(*[_fetch_user_auth(i) for i in instances])

    toolsets = []
    for inst, user_auth in results:
        toolset_type = inst.get("toolsetType", "")
        meta = registry.get_toolset_metadata(toolset_type)
        is_authenticated = bool(user_auth and user_auth.get("isAuthenticated", False))

        toolsets.append({
            "instanceId": inst.get("_id"),
            "instanceName": inst.get("instanceName"),
            "toolsetType": toolset_type,
            "authType": inst.get("authType", "NONE"),
            "oauthConfigId": inst.get("oauthConfigId"),
            "displayName": meta.get("display_name", toolset_type) if meta else toolset_type,
            "description": meta.get("description", "") if meta else "",
            "iconPath": meta.get("icon_path", "") if meta else "",
            "category": meta.get("category", "app") if meta else "app",
            "supportedAuthTypes": meta.get("supported_auth_types", []) if meta else [],
            "toolCount": len(meta.get("tools", [])) if meta else 0,
            "tools": [
                {
                    "name": t.get("name", ""),
                    "fullName": f"{toolset_type}.{t.get('name', '')}",
                    "description": t.get("description", ""),
                }
                for t in (meta.get("tools", []) if meta else [])
            ],
            "isConfigured": True,
            "isAuthenticated": is_authenticated,
            "createdBy": inst.get("createdBy"),
            "createdAtTimestamp": inst.get("createdAtTimestamp"),
            "updatedAtTimestamp": inst.get("updatedAtTimestamp"),
        })

    return {"status": "success", "toolsets": toolsets}


# ============================================================================
# User Authentication Against Instances
# ============================================================================

@router.post("/instances/{instance_id}/authenticate", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
@inject
async def authenticate_toolset_instance(
    instance_id: str,
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service])
) -> Dict[str, Any]:
    """
    User authenticates an admin-created toolset instance by providing credentials
    (API token, username/password, bearer token, etc.).
    For OAUTH type, this endpoint is not used - use /oauth/authorize instead.
    Stores credentials at /services/toolsets/{userId}/{instanceId}.
    """
    user_context = _get_user_context(request)
    org_id = user_context["org_id"]
    user_id = user_context["user_id"]

    # Verify instance exists
    instances = await _load_toolset_instances(org_id, config_service)

    instance = next((i for i in instances if i.get("_id") == instance_id and i.get("orgId") == org_id), None)
    if not instance:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail=f"Toolset instance '{instance_id}' not found.")

    auth_type = instance.get("authType", "")
    if auth_type == "OAUTH":
        raise HTTPException(
            status_code=HttpStatusCode.BAD_REQUEST.value,
            detail="For OAuth toolsets, use the /instances/{instance_id}/oauth/authorize endpoint to authenticate."
        )

    body_data = await request.body()
    body = _parse_request_json(request, body_data)
    credentials = body.get("credentials", {})
    auth = body.get("auth", {})

    if not credentials and not auth:
        raise HTTPException(status_code=HttpStatusCode.BAD_REQUEST.value, detail="Credentials are required.")

    # Validate required fields based on auth type
    if auth_type == "API_TOKEN":
        token = (credentials.get("apiToken") or auth.get("apiToken") or "").strip()
        if not token:
            raise InvalidAuthConfigError("apiToken is required for API_TOKEN auth type")
    elif auth_type == "BEARER_TOKEN":
        token = (credentials.get("bearerToken") or auth.get("bearerToken") or "").strip()
        if not token:
            raise InvalidAuthConfigError("bearerToken is required for BEARER_TOKEN auth type")
    elif auth_type == "USERNAME_PASSWORD":
        username = (credentials.get("username") or auth.get("username") or "").strip()
        password = (credentials.get("password") or auth.get("password") or "").strip()
        if not username or not password:
            raise InvalidAuthConfigError("username and password are required for USERNAME_PASSWORD auth type")

    now = get_epoch_timestamp_in_ms()
    user_auth = {
        "isAuthenticated": True,
        "authType": auth_type,
        "instanceId": instance_id,
        "toolsetType": instance.get("toolsetType"),
        "auth": auth if auth else {},
        "credentials": credentials if credentials else {},
        "updatedAt": now,
        "updatedBy": user_id,
    }

    auth_path = _get_user_auth_path(instance_id, user_id)
    try:
        await config_service.set_config(auth_path, user_auth)
    except Exception as e:
        logger.error(f"Failed to save user auth for instance {instance_id}: {e}")
        raise HTTPException(status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail="Failed to save credentials.")

    return {"status": "success", "message": "Toolset authenticated successfully.", "isAuthenticated": True}


@router.delete("/instances/{instance_id}/credentials", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
@inject
async def remove_toolset_credentials(
    instance_id: str,
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service])
) -> Dict[str, Any]:
    """Remove the current user's credentials for a toolset instance."""
    user_context = _get_user_context(request)
    user_id = user_context["user_id"]

    auth_path = _get_user_auth_path(instance_id, user_id)
    try:
        await config_service.delete_config(auth_path)
    except Exception as e:
        logger.warning(f"Failed to delete auth for instance {instance_id}: {e}")

    return {"status": "success", "message": "Credentials removed successfully."}


@router.post("/instances/{instance_id}/reauthenticate", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
@inject
async def reauthenticate_toolset_instance(
    instance_id: str,
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service])
) -> Dict[str, Any]:
    """
    Clear the user's OAuth tokens for an instance, requiring a new OAuth flow.
    For non-OAuth types, clears all credentials.
    """
    user_context = _get_user_context(request)
    org_id = user_context["org_id"]
    user_id = user_context["user_id"]

    # Verify instance exists
    instances = await _load_toolset_instances(org_id, config_service)

    instance = next((i for i in instances if i.get("_id") == instance_id and i.get("orgId") == org_id), None)
    if not instance:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail=f"Toolset instance '{instance_id}' not found.")

    auth_path = _get_user_auth_path(instance_id, user_id)
    try:
        current_auth = await config_service.get_config(auth_path, default=None)
        if current_auth:
            updated = {
                **current_auth,
                "isAuthenticated": False,
                "updatedAt": get_epoch_timestamp_in_ms(),
                "updatedBy": user_id,
            }
            # Clear credentials and oauthConfigId to force fresh authentication
            # This ensures that if admin changed OAuth config, user gets the new one
            updated.pop("credentials", None)
            updated.pop("oauthConfigId", None)  # Will be refreshed on next auth
            await config_service.set_config(auth_path, updated)
        else:
            await config_service.delete_config(auth_path)
    except Exception as e:
        logger.error(f"Failed to reauthenticate instance {instance_id}: {e}")
        raise HTTPException(status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail="Failed to clear credentials.")

    return {"status": "success", "message": "Credentials cleared. Please re-authenticate."}


# ============================================================================
# OAuth Flow for Instances
# ============================================================================

@router.get("/instances/{instance_id}/oauth/authorize", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
@inject
async def get_instance_oauth_authorization_url(
    instance_id: str,
    request: Request,
    base_url: Optional[str] = Query(None),
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service])
) -> Dict[str, Any]:
    """
    Get the OAuth authorization URL for a toolset instance.
    Reads OAuth client credentials from the instance's linked OAuth config.
    """
    user_context = _get_user_context(request)
    org_id = user_context["org_id"]
    user_id = user_context["user_id"]

    # Load instance
    instances = await _load_toolset_instances(org_id, config_service)

    instance = next((i for i in instances if i.get("_id") == instance_id and i.get("orgId") == org_id), None)
    if not instance:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail=f"Toolset instance '{instance_id}' not found.")

    auth_type = instance.get("authType", "")
    if auth_type != "OAUTH":
        raise OAuthConfigError(f"Instance '{instance_id}' uses {auth_type} authentication, not OAuth.")

    oauth_config_id = instance.get("oauthConfigId")
    toolset_type = instance.get("toolsetType", "")

    if not oauth_config_id:
        raise OAuthConfigError(
            f"Instance '{instance_id}' has no OAuth configuration linked. "
            "Please ask an administrator to update the instance with OAuth credentials."
        )

    # Load OAuth config (with better error for deleted/missing configs)
    oauth_cfg = await _get_oauth_config_by_id(toolset_type, oauth_config_id, org_id, config_service)
    if not oauth_cfg:
        raise OAuthConfigError(
            f"OAuth configuration '{oauth_config_id}' not found for toolset '{toolset_type}'. "
            f"This can happen if the admin deleted or changed the OAuth configuration. "
            f"Please contact your administrator to reconfigure this toolset instance."
        )

    auth_config = {
        "type": "OAUTH",
        **oauth_cfg.get("config", {}),
    }

    registry = _get_registry(request)
    oauth_flow_config = await _build_oauth_config(auth_config, toolset_type, registry, base_url, request)

    # Generate authorization URL using etcd path for token storage
    user_auth_path = _get_user_auth_path(instance_id, user_id)
    oauth_config_obj = get_oauth_config(oauth_flow_config)
    if not oauth_config_obj.scope and oauth_flow_config.get("scopes"):
        oauth_config_obj.scope = " ".join(oauth_flow_config["scopes"])

    oauth_provider = OAuthProvider(
        config=oauth_config_obj,
        configuration_service=config_service,
        credentials_path=user_auth_path
    )

    try:
        auth_url = await oauth_provider.start_authorization()
        if not auth_url:
            raise OAuthConfigError("OAuth provider returned empty authorization URL")

        parsed_url = urlparse(auth_url)
        query_params = parse_qs(parsed_url.query)

        if "token_access_type" in query_params:
            if query_params["token_access_type"] in [["None"], [None], ["null"], [""]]:
                del query_params["token_access_type"]

        original_state = query_params.get("state", [None])[0]
        if not original_state:
            raise OAuthConfigError("OAuth state parameter is missing from authorization URL")

        encoded_state = _encode_state_with_instance(original_state, instance_id, user_id)
        query_params["state"] = [encoded_state]

        final_url = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}?{urlencode(query_params, doseq=True)}"

        return {"success": True, "authorizationUrl": final_url, "state": encoded_state}
    except (OAuthConfigError, InvalidAuthConfigError):
        raise
    except Exception as e:
        logger.error(f"Error generating OAuth URL for instance {instance_id}: {e}")
        raise HTTPException(status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail="Failed to generate OAuth authorization URL.")
    finally:
        await oauth_provider.close()


@router.get("/oauth/callback", response_model=None, dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
@inject
async def handle_toolset_oauth_callback(
    request: Request,
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    base_url: Optional[str] = Query(None),
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service])
) -> Union[Dict[str, Any], RedirectResponse]:
    """Handle OAuth callback for toolset instance authentication."""
    base_url = base_url or "http://localhost:3001"

    if error and error not in ["null", "undefined", "None", ""]:
        return {"success": False, "error": error, "redirect_url": f"{base_url}/tools?oauth_error={error}"}

    if not code or not state:
        return {"success": False, "error": "missing_parameters", "redirect_url": f"{base_url}/tools?oauth_error=missing_parameters"}

    try:
        user_context = _get_user_context(request)
        user_id = user_context["user_id"]
        org_id = user_context["org_id"]

        state_data = _decode_state_with_instance(state)
        original_state = state_data["state"]
        instance_id = state_data["instance_id"]
        state_user_id = state_data["user_id"]

        if state_user_id != user_id:
            raise HTTPException(status_code=HttpStatusCode.FORBIDDEN.value, detail="OAuth callback user mismatch.")

        # Load instance
        instances = await _load_toolset_instances(org_id, config_service)

        instance = next((i for i in instances if i.get("_id") == instance_id and i.get("orgId") == org_id), None)
        if not instance:
            raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail=f"Toolset instance '{instance_id}' not found.")

        toolset_type = instance.get("toolsetType", "")
        oauth_config_id = instance.get("oauthConfigId")

        if not oauth_config_id:
            raise OAuthConfigError("Instance has no OAuth configuration.")

        oauth_cfg = await _get_oauth_config_by_id(toolset_type, oauth_config_id, org_id, config_service)
        if not oauth_cfg:
            raise OAuthConfigError(f"OAuth configuration '{oauth_config_id}' not found.")

        auth_config = {"type": "OAUTH", **oauth_cfg.get("config", {})}
        registry = _get_registry(request)
        oauth_flow_config = await _build_oauth_config(auth_config, toolset_type, registry, base_url, request)

        user_auth_path = _get_user_auth_path(instance_id, user_id)
        oauth_config_obj = get_oauth_config(oauth_flow_config)
        oauth_provider = OAuthProvider(
            config=oauth_config_obj,
            configuration_service=config_service,
            credentials_path=user_auth_path
        )

        try:
            token = await oauth_provider.handle_callback(code, original_state)
        finally:
            await oauth_provider.close()

        if not token or not token.access_token:
            raise OAuthConfigError("Failed to exchange authorization code for access token")

        # Update user auth record
        try:
            updated_auth = await config_service.get_config(user_auth_path, use_cache=False) or {}
            if not isinstance(updated_auth, dict):
                updated_auth = {}
            updated_auth["isAuthenticated"] = True
            updated_auth["authType"] = "OAUTH"
            updated_auth["instanceId"] = instance_id
            updated_auth["toolsetType"] = toolset_type
            # Store current instance's oauthConfigId (from callback flow, not stale user data)
            # This ensures we always have the CURRENT config, handling admin config switches
            updated_auth["oauthConfigId"] = oauth_config_id
            updated_auth["updatedAt"] = get_epoch_timestamp_in_ms()
            updated_auth["updatedBy"] = user_id
            await config_service.set_config(user_auth_path, updated_auth)
        except Exception as e:
            logger.warning(f"Could not update auth status for instance {instance_id}: {e}")

        # Schedule token refresh
        try:
            from app.connectors.core.base.token_service.startup_service import (
                startup_service,
            )
            refresh_service = startup_service.get_toolset_token_refresh_service()
            if refresh_service:
                await refresh_service.schedule_token_refresh(user_auth_path, toolset_type, token)
        except Exception as e:
            logger.error(f"Could not schedule token refresh for instance {instance_id}: {e}")

        return {
            "success": True,
            "redirect_url": f"{base_url}/tools?oauth_success=true&instance_id={instance_id}"
        }

    except (ToolsetError, OAuthConfigError, InvalidAuthConfigError) as e:
        error_detail = getattr(e, 'detail', str(e))
        logger.error(f"OAuth callback error: {error_detail}")
        return {"success": False, "error": type(e).__name__, "redirect_url": f"{base_url}/tools?oauth_error={type(e).__name__}"}
    except HTTPException:
        return {"success": False, "error": "auth_failed", "redirect_url": f"{base_url}/tools?oauth_error=auth_failed"}
    except Exception as e:
        logger.error(f"Error handling OAuth callback: {e}", exc_info=True)
        return {"success": False, "error": "server_error", "redirect_url": f"{base_url}/tools?oauth_error=server_error"}


# ============================================================================
# OAuth Configs for Toolset Types (Admin Management)
# ============================================================================

@router.get("/oauth-configs/{toolset_type}", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
@inject
async def list_toolset_oauth_configs(
    toolset_type: str,
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service])
) -> Dict[str, Any]:
    """
    List OAuth configurations for a toolset type.
    Admins see all fields including clientSecret. Non-admins see only basic metadata.
    """
    user_context = _get_user_context(request)
    org_id = user_context["org_id"]
    is_admin = await _check_user_is_admin(user_context["user_id"], request, config_service)

    configs = await _get_oauth_configs_for_type(toolset_type, config_service)
    org_configs = []
    for cfg in configs:
        if cfg.get("orgId") != org_id:
            continue
        entry: Dict[str, Any] = {k: v for k, v in cfg.items() if k != "config"}
        if is_admin:
            cfg_data = cfg.get("config", {})
            # Add all config fields dynamically (include clientSecret for admins)
            for key, value in cfg_data.items():
                entry[key] = value
            # Also add clientSecretSet flag for backward compatibility
            if "clientSecret" in cfg_data:
                entry["clientSecretSet"] = bool(cfg_data["clientSecret"])
        org_configs.append(entry)

    return {"status": "success", "oauthConfigs": org_configs, "total": len(org_configs)}


@router.put("/oauth-configs/{toolset_type}/{oauth_config_id}", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
@inject
async def update_toolset_oauth_config(
    toolset_type: str,
    oauth_config_id: str,
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service])
) -> Dict[str, Any]:
    """
    Admin updates an OAuth configuration for a toolset type.
    After update, all instances referencing this oauth_config_id have their
    authenticated users deauthenticated in parallel.
    """
    user_context = _get_user_context(request)
    is_admin = await _check_user_is_admin(user_context["user_id"], request, config_service)
    if not is_admin:
        raise HTTPException(status_code=HttpStatusCode.FORBIDDEN.value, detail="Only administrators can update OAuth configurations.")

    org_id = user_context["org_id"]
    user_id = user_context["user_id"]

    body_data = await request.body()
    body = _parse_request_json(request, body_data)

    cfg = await _get_oauth_config_by_id(toolset_type, oauth_config_id, org_id, config_service)
    if not cfg:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail=f"OAuth configuration '{oauth_config_id}' not found.")

    base_url = body.get("baseUrl", "").strip()
    if not base_url:
        try:
            endpoints = await config_service.get_config("/services/endpoints", use_cache=False)
            base_url = endpoints.get("frontend", {}).get("publicEndpoint", "http://localhost:3001")
        except Exception:
            base_url = "http://localhost:3001"

    auth_config = body.get("authConfig", {})
    auth_config["type"] = "OAUTH"

    registry = _get_registry(request)
    new_oauth_id = await _create_or_update_toolset_oauth_config(
        toolset_type=toolset_type,
        auth_config=auth_config,
        instance_name=cfg.get("oauthInstanceName", oauth_config_id),
        user_id=user_id,
        org_id=org_id,
        config_service=config_service,
        registry=registry,
        base_url=base_url,
        oauth_config_id=oauth_config_id,
    )

    # Deauthenticate all users of all instances using this OAuth config (parallel)
    instances = await _load_toolset_instances(org_id, config_service)

    affected_instances = [i for i in instances if i.get("oauthConfigId") == oauth_config_id and i.get("orgId") == org_id]

    total_deauthed = 0
    if affected_instances:
        deauth_results = await asyncio.gather(
            *[_deauth_all_instance_users(inst["_id"], config_service) for inst in affected_instances],
            return_exceptions=True
        )
        for r in deauth_results:
            if isinstance(r, int):
                total_deauthed += r

    msg = "OAuth configuration updated."
    if total_deauthed > 0:
        msg += f" {total_deauthed} user(s) across {len(affected_instances)} instance(s) have been deauthenticated and must re-authenticate."

    return {"status": "success", "oauthConfigId": new_oauth_id, "message": msg, "deauthenticatedUserCount": total_deauthed}


@router.delete("/oauth-configs/{toolset_type}/{oauth_config_id}", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_DELETE))])
@inject
async def delete_toolset_oauth_config(
    toolset_type: str,
    oauth_config_id: str,
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service])
) -> Dict[str, Any]:
    """
    Admin deletes an OAuth configuration for a toolset type.
    SAFE DELETE: Rejected if any toolset instance references this OAuth config.
    """
    user_context = _get_user_context(request)
    is_admin = await _check_user_is_admin(user_context["user_id"], request, config_service)
    if not is_admin:
        raise HTTPException(status_code=HttpStatusCode.FORBIDDEN.value, detail="Only administrators can delete OAuth configurations.")

    org_id = user_context["org_id"]

    cfg = await _get_oauth_config_by_id(toolset_type, oauth_config_id, org_id, config_service)
    if not cfg:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail=f"OAuth configuration '{oauth_config_id}' not found.")

    # Safe-delete: reject if any instance references this oauth config
    instances = await _load_toolset_instances(org_id, config_service)

    using_instances = [i for i in instances if i.get("oauthConfigId") == oauth_config_id and i.get("orgId") == org_id]
    if using_instances:
        names = ", ".join(
            f"'{i.get('instanceName', i.get('_id'))}'"
            for i in using_instances[:MAX_AGENT_NAMES_DISPLAY]
        )
        if len(using_instances) > MAX_AGENT_NAMES_DISPLAY:
            names += f" and {len(using_instances) - MAX_AGENT_NAMES_DISPLAY} more"
        raise HTTPException(
            status_code=HttpStatusCode.CONFLICT.value,
            detail=(
                f"Cannot delete OAuth configuration: it is referenced by {len(using_instances)} "
                f"toolset instance(s) ({names}). Update or delete those instances first."
            )
        )

    # Remove from the list
    all_configs = await _get_oauth_configs_for_type(toolset_type, config_service)
    updated_configs = [c for c in all_configs if c.get("_id") != oauth_config_id]
    path = _get_toolset_oauth_config_path(toolset_type)
    try:
        await config_service.set_config(path, updated_configs)
    except Exception as e:
        logger.error(f"Failed to delete OAuth config {oauth_config_id}: {e}")
        raise HTTPException(status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail="Failed to delete OAuth configuration.")

    return {"status": "success", "message": "OAuth configuration deleted successfully."}


# ============================================================================
# Backward-Compatible Configured Toolsets Endpoint
# ============================================================================

@router.get("/configured", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
@inject
async def get_configured_toolsets(
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service])
) -> Dict[str, Any]:
    """
    Backward-compatible endpoint: returns toolsets the user has authenticated.
    Merges admin-created instances with user's auth status.
    Delegates to get_my_toolsets logic.
    """
    return await get_my_toolsets(request=request, search=None, config_service=config_service)


# ============================================================================
# Instance Status Endpoint
# ============================================================================

@router.get("/instances/{instance_id}/status", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
@inject
async def get_instance_status(
    instance_id: str,
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service])
) -> Dict[str, Any]:
    """Get authentication status for a specific toolset instance for the current user."""
    user_context = _get_user_context(request)
    org_id = user_context["org_id"]
    user_id = user_context["user_id"]

    # Verify instance exists
    instances = await _load_toolset_instances(org_id, config_service)

    instance = next((i for i in instances if i.get("_id") == instance_id and i.get("orgId") == org_id), None)
    if not instance:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail=f"Toolset instance '{instance_id}' not found.")

    auth_path = _get_user_auth_path(instance_id, user_id)
    try:
        user_auth = await config_service.get_config(auth_path, default=None)
    except Exception:
        user_auth = None

    return {
        "status": "success",
        "instanceId": instance_id,
        "instanceName": instance.get("instanceName"),
        "toolsetType": instance.get("toolsetType"),
        "authType": instance.get("authType"),
        "isConfigured": True,
        "isAuthenticated": bool(user_auth and user_auth.get("isAuthenticated", False)),
    }
