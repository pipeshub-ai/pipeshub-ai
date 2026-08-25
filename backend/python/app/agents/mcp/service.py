"""Shared MCP data-access helpers — etcd instance/credential resolution.

Extracted from `api/routes/mcp_servers.py` (Phase 1 duplicated these as module-private
`_`-prefixed helpers). Pure data-access, no FastAPI dependency, so this module can be
imported by the agent-loop runtime (query service) and the connectors-service routes
without pulling in HTTP framework concerns.

Single shared implementation of "which credential record actually backs an instance for
a given caller" — used by routes, the agent-loop's `MCPAccessResolver`/`MCPToolProvider`,
and (via `get_authenticated_mcp_servers`) the assistant/placeholder agent's auto-attach path.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.agents.constants.mcp_server_constants import (
    get_mcp_credentials_path,
    get_mcp_instance_path,
    get_mcp_instances_prefix,
)
from app.agents.mcp.models import MCPAuthMode, MCPServerConfig
from app.config.configuration_service import ConfigurationService
from app.services.featureflag.config.config import CONFIG
from app.services.featureflag.platform_settings import read_platform_feature_flag

logger = logging.getLogger(__name__)

__all__ = [
    "load_org_instances",
    "get_instance",
    "resolve_effective_user_auth",
    "instance_config_from_dict",
    "credentials_to_discovery_dict",
    "is_effective_auth_authenticated",
    "get_authenticated_mcp_servers",
    "match_enabled_tools_for_mcp_server",
    "is_mcp_enabled",
]


# ---------------------------------------------------------------------------
# Instance storage
# ---------------------------------------------------------------------------


async def load_org_instances(config_service: ConfigurationService) -> list[dict[str, Any]]:
    """Every MCP instance metadata record for the current org scope (no secrets)."""
    prefix = get_mcp_instances_prefix()
    try:
        keys = await config_service.list_keys_in_directory(prefix)
    except Exception as e:
        logger.error(f"Failed to list MCP instance keys under {prefix}: {e}", exc_info=True)
        return []

    instances: list[dict[str, Any]] = []
    for key in keys:
        try:
            data = await config_service.get_config(key, default=None, use_cache=False)
            if isinstance(data, dict):
                instances.append(data)
        except Exception as e:
            logger.warning(f"Failed to load MCP instance at {key}: {e}")
    return instances


async def get_instance(
    instance_id: str, config_service: ConfigurationService
) -> Optional[dict[str, Any]]:
    data = await config_service.get_config(
        get_mcp_instance_path(instance_id), default=None, use_cache=False
    )
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------------------
# Effective auth resolution (shared by discovery + routes + agent-loop runtime)
# ---------------------------------------------------------------------------


async def resolve_effective_user_auth(
    instance: dict[str, Any],
    user_id: str,
    config_service: ConfigurationService,
) -> Optional[dict[str, Any]]:
    """Resolve which credential record actually backs `instance` for `user_id`.

    - authMode == none: no record needed; returns {} (trivially authenticated).
    - useAdminAuth (api_token/headers only — OAuth is always per-caller): resolves the
      instance creator's record instead of the caller's own.
    - otherwise: the caller's own record (a plain user, or an agentKey for a
      service-account agent — the credential path treats both as opaque owner ids).
    """
    auth_mode = instance.get("authMode")
    if auth_mode == MCPAuthMode.NONE.value:
        return {}

    effective_user_id = user_id
    if instance.get("useAdminAuth") and auth_mode in (MCPAuthMode.API_TOKEN.value, MCPAuthMode.HEADERS.value):
        effective_user_id = instance.get("createdBy") or user_id

    record = await config_service.get_config(
        get_mcp_credentials_path(instance.get("_id"), effective_user_id), default=None, use_cache=False
    )
    return record if isinstance(record, dict) else None


def is_effective_auth_authenticated(effective_auth: Optional[dict[str, Any]]) -> bool:
    """`effective_auth` from `resolve_effective_user_auth` -> whether the instance is
    usable: `{}` (authMode == none) counts as authenticated; a stored record must have
    `isAuthenticated` set."""
    return effective_auth is not None and (effective_auth == {} or bool(effective_auth.get("isAuthenticated")))


def instance_config_from_dict(instance: dict[str, Any]) -> MCPServerConfig:
    """Build the typed `MCPServerConfig` the discovery/client layer expects, from the stored dict.

    Uses `.get()` rather than direct indexing for required fields so a malformed/stale etcd
    record surfaces as a normal Pydantic `ValidationError` (missing field) instead of an
    unhandled `KeyError` from this function.
    """
    return MCPServerConfig(
        _id=instance.get("_id"),
        org_id=instance.get("orgId"),
        created_by=instance.get("createdBy"),
        name=instance.get("name"),
        type_id=instance.get("typeId"),
        transport=instance.get("transport"),
        auth_mode=instance.get("authMode"),
        use_admin_auth=bool(instance.get("useAdminAuth")),
        description=instance.get("description"),
        command=instance.get("command"),
        args=instance.get("args") or [],
        required_env=instance.get("requiredEnv") or [],
        optional_env=instance.get("optionalEnv") or [],
        url=instance.get("url"),
        header_name=instance.get("headerName"),
        authorization_url=instance.get("authorizationUrl"),
        token_url=instance.get("tokenUrl"),
        scopes=instance.get("scopes") or [],
        is_custom=bool(instance.get("isCustom")),
        created_at=instance.get("createdAt") or 0,
        updated_at=instance.get("updatedAt") or 0,
    )


def credentials_to_discovery_dict(auth_mode: str, credential_record: dict[str, Any]) -> dict[str, Any]:
    """Flatten a stored credential record into the shape `discovery.build_auth_env_and_headers` expects."""
    if auth_mode == MCPAuthMode.OAUTH.value:
        tokens = credential_record.get("oauthTokens") or {}
        return {"accessToken": tokens.get("accessToken") or tokens.get("access_token")}
    return credential_record.get("credentials") or {}


# ---------------------------------------------------------------------------
# Assistant / placeholder agent support
# ---------------------------------------------------------------------------


def _tool_namespace_key(instance: dict[str, Any]) -> str:
    """The same discriminator `discovery.build_namespaced_tool_name` derives a tool's
    `mcp_{server_type}_{tool}` name from (`typeId` if this is a catalog instance, else the
    instance's own `name`) — used to dedupe instances that would otherwise produce
    colliding tool names for the assistant's auto-attach path (see
    `get_authenticated_mcp_servers`)."""
    type_id = instance.get("typeId")
    discriminator = type_id or instance.get("name") or instance.get("_id") or ""
    return discriminator.lower().strip().replace(" ", "_").replace("-", "_")


def match_enabled_tools_for_mcp_server(
    mcp_server: dict[str, Any],
    enabled_tools: set[str],
) -> list[dict[str, Any]]:
    """Match chat-time selected tool fullNames to one MCP server (assistant path).

    Assistant/placeholder attachments from `get_authenticated_mcp_servers` have no
    stored `tools` list — only `typeId`/`name`. Selected tools use the same
    `mcp_{type}_{tool}` names as live discovery, so match by that prefix.
    """
    type_key = _tool_namespace_key(mcp_server)
    if not type_key:
        return []
    prefix = f"mcp_{type_key}_"
    matched: list[dict[str, Any]] = []
    for full_name in enabled_tools:
        if not isinstance(full_name, str) or not full_name.startswith(prefix):
            continue
        tool_name = full_name[len(prefix):]
        if tool_name:
            matched.append({"name": tool_name, "fullName": full_name})
    return matched


async def get_authenticated_mcp_servers(
    owner_id: str,
    config_service: ConfigurationService,
    instances: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """All MCP instances `owner_id` (a user, or an agentKey for a service-account agent) has
    completed authentication for — the MCP analog of
    `api/routes/toolsets.py::get_authenticated_toolsets`.

    Used by the assistant/placeholder agent (`get_assistant_agent` in `api/routes/agent.py`)
    to auto-attach every authenticated MCP server with no graph edges involved. Because there
    is no attach-time "reject duplicate server type" guard on this path (unlike explicit
    per-agent attachment), duplicate types are deduped here instead — first authenticated
    instance for a given type wins — so `mcp_{server_type}_{tool}` names stay unique.

    ``instances``, when provided, is used as-is instead of loading from the config service.
    """
    if instances is None:
        try:
            instances = await load_org_instances(config_service)
        except Exception as e:
            logger.error(f"Failed to load MCP instances: {e}", exc_info=True)
            return []

    if not instances:
        return []

    seen_type_keys: set[str] = set()
    authenticated: list[dict[str, Any]] = []
    for instance in instances:
        try:
            effective_auth = await resolve_effective_user_auth(instance, owner_id, config_service)
        except Exception as e:
            logger.warning(f"Failed to resolve MCP auth for instance {instance.get('_id')}: {e}")
            continue

        if not is_effective_auth_authenticated(effective_auth):
            continue

        type_key = _tool_namespace_key(instance)
        if type_key in seen_type_keys:
            logger.info(
                f"Skipping duplicate-type MCP instance {instance.get('_id')} "
                f"(type key '{type_key}' already included) for assistant agent auto-attach"
            )
            continue
        seen_type_keys.add(type_key)

        authenticated.append({
            "instanceId": instance.get("_id"),
            "name": instance.get("name"),
            "typeId": instance.get("typeId"),
            "displayName": instance.get("name"),
            "transport": instance.get("transport"),
            "authMode": instance.get("authMode"),
        })

    return authenticated


# ---------------------------------------------------------------------------
# Feature flag gate
# ---------------------------------------------------------------------------


async def is_mcp_enabled(config_service: Optional[ConfigurationService] = None) -> bool:
    """Deployment-level gate for MCP: agents may only load/use MCP servers when
    this is true. Source of truth is the ``ENABLE_MCP`` platform feature flag.
    Defaults to DISABLED — admins must opt in from Labs.

    Resolution order (first hit wins):
    1. ``config_service`` — live read of the platform settings the Labs UI writes,
       via the shared ``read_platform_feature_flag`` helper
    2. ``FeatureFlagService`` — only reachable in services that wire an
       ``EtcdProvider`` (the connectors service); the query service does not, which
       is why the ``config_service`` read above is the primary path
    3. Default: ``False``

    Reads with ``use_cache=False`` (like `load_org_instances`) so flipping the flag
    in Labs takes effect on the next chat instead of after a service restart.
    """
    if config_service is not None:
        return await read_platform_feature_flag(
            CONFIG.ENABLE_MCP, config_service, default=False,
        )

    try:
        from app.services.featureflag.featureflag import FeatureFlagService

        return bool(
            FeatureFlagService.get_service().is_feature_enabled(
                CONFIG.ENABLE_MCP, default=False
            )
        )
    except Exception as e:
        logger.warning(f"FeatureFlagService unavailable for ENABLE_MCP, treating MCP as disabled: {e}")
        return False
