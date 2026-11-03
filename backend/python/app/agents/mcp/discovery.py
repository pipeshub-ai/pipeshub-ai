"""Live tool discovery against a connected MCP server instance.

Namespaces every discovered tool as `mcp_{namespace}_{tool}` (see `naming.py`) so the agent
loop can distinguish MCP-sourced tools from native connector/toolset tools.
"""
import asyncio
import logging
from typing import TYPE_CHECKING, Any

from app.agents.mcp import step_up, tool_cache
from app.agents.mcp.client import (
    MCPClientManager,
    MCPConnectionError,
    ToolListing,
    discovery_timeout,
)
from app.agents.mcp.errors import MCPInsufficientScopeError, is_http_unauthorized
from app.agents.mcp.models import (
    MCPAuthMode,
    MCPServerConfig,
    MCPToolInfo,
    MCPTransport,
)
from app.agents.mcp.naming import build_namespaced_tool_name, namespace_key
from app.agents.mcp.service import (
    credentials_to_discovery_dict,
    instance_config_from_dict,
)
from app.agents.mcp.stdio_policy import is_allowed_env_name
from app.agents.mcp.token_refresh import refresh_credential_record

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService

logger = logging.getLogger(__name__)

DEFAULT_ENV_VAR_NAME = "API_TOKEN"


def _allowed_stdio_env_names(config: MCPServerConfig) -> set[str]:
    # Instances saved before env-name validation may declare names like NODE_OPTIONS.
    declared = set(config.required_env or []) | set(config.optional_env or [])
    return {name for name in declared if is_allowed_env_name(name)}


def build_auth_env_and_headers(
    config: MCPServerConfig,
    credentials: dict[str, Any],
) -> tuple[dict[str, str], dict[str, str]]:
    """Resolve env vars (STDIO) / headers (SSE, streamable_http) from a credential record.

    Headers-mode parity: shared `headerName`/`headerValue` credentials are applied here
    (the reference PR only ever wired `apiToken`, silently ignoring HEADERS-mode instances
    during discovery).

    STDIO multi-env: credentials may carry an allowlisted `env` map (e.g. Slack needs both
    `SLACK_BOT_TOKEN` and `SLACK_TEAM_ID`). `apiToken` still maps to `required_env[0]` for
    single-token servers and as a fallback when the primary key is absent from `env`.
    """
    env: dict[str, str] = {}
    headers: dict[str, str] = {}

    if config.auth_mode == MCPAuthMode.NONE:
        return env, headers

    if config.auth_mode == MCPAuthMode.API_TOKEN:
        if config.transport == MCPTransport.STDIO:
            allowed = _allowed_stdio_env_names(config)
            cred_env = credentials.get("env") or {}
            if isinstance(cred_env, dict):
                for key, value in cred_env.items():
                    if key in allowed and value:
                        env[str(key)] = str(value)
            api_token = credentials.get("apiToken")
            if api_token:
                env_name = (config.required_env or [DEFAULT_ENV_VAR_NAME])[0]
                if is_allowed_env_name(env_name):
                    env.setdefault(env_name, api_token)
        else:
            api_token = credentials.get("apiToken")
            if api_token:
                headers["Authorization"] = f"Bearer {api_token}"

    elif config.auth_mode == MCPAuthMode.HEADERS:
        header_name = credentials.get("headerName") or config.header_name
        header_value = credentials.get("headerValue")
        if header_name and header_value:
            headers[header_name] = header_value

    elif config.auth_mode == MCPAuthMode.OAUTH:
        access_token = credentials.get("accessToken")
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"

    return env, headers


async def discover_tools(
    config: MCPServerConfig,
    credentials: dict[str, Any],
    timeout_seconds: float | None = None,
    *,
    namespace: str | None = None,
) -> list[MCPToolInfo]:
    """Connect to `config`, list its tools, and return them namespaced for the agent loop.

    `namespace` defaults to the catalog type or instance name; a request loading two
    instances with the same one passes a disambiguated key (see `MCPToolProvider`).
    `timeout_seconds` defaults to the instance's discovery budget (`discovery_timeout`).

    Raises MCPConnectionError (including on timeout) — callers decide whether discovery
    failures are fatal (e.g. `includeTools=false` lets `/my-mcp-servers` skip this entirely).
    """
    listing = await discover_tool_listing(config, credentials, timeout_seconds)
    return tool_infos_from_listing(listing.tools, config, namespace)


async def discover_tool_listing(
    config: MCPServerConfig, credentials: dict[str, Any], timeout_seconds: float | None = None,
) -> ToolListing:
    """`discover_tools` before namespacing, with the server's instructions and cache hints."""
    env, headers = build_auth_env_and_headers(config, credentials)
    manager = MCPClientManager(config, env=env, headers=headers)
    timeout_seconds = timeout_seconds or discovery_timeout(config)

    try:
        return await asyncio.wait_for(manager.fetch_tool_listing(), timeout=timeout_seconds)
    except asyncio.TimeoutError as e:
        raise MCPConnectionError(
            f"Timed out discovering tools for MCP instance {config.id} after {timeout_seconds:g}s"
        ) from e


def tool_infos_from_listing(
    raw_tools: list[Any], config: MCPServerConfig, namespace: str | None = None,
) -> list[MCPToolInfo]:
    """A server's `tools/list` result as namespaced `MCPToolInfo`s."""
    server_namespace = namespace or namespace_key(config.type_id, config.name)
    tools: list[MCPToolInfo] = []
    for tool in raw_tools:
        name = getattr(tool, "name", None)
        if name is None and isinstance(tool, dict):
            name = tool.get("name")
        if not name:
            continue

        description = getattr(tool, "description", None)
        if description is None and isinstance(tool, dict):
            description = tool.get("description")

        # mcp 1.x types name it `inputSchema`, 2.x `input_schema`.
        input_schema = getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None)
        if input_schema is None and isinstance(tool, dict):
            input_schema = tool.get("inputSchema")

        tools.append(
            MCPToolInfo(
                name=name,
                namespaced_name=build_namespaced_tool_name(server_namespace, name),
                description=description,
                input_schema=input_schema or {},
                annotations=_annotations_of(tool),
            )
        )
    return tools


def _annotations_of(tool: Any) -> dict[str, Any] | None:  # noqa: ANN401
    """A tool's annotations with protocol (camelCase) keys, from an SDK object or a dict."""
    annotations = tool.get("annotations") if isinstance(tool, dict) else getattr(tool, "annotations", None)
    if hasattr(annotations, "model_dump"):
        annotations = annotations.model_dump(by_alias=True, exclude_none=True)
    return annotations if isinstance(annotations, dict) and annotations else None


async def discover_tools_for_owner(
    instance: dict[str, Any],
    auth: dict[str, Any],
    owner_id: str,
    config_service: "ConfigurationService",
    *,
    timeout_seconds: float | None = None,
    namespace: str | None = None,
) -> tuple[list[MCPToolInfo], dict[str, Any]]:
    """`discover_tools` with `owner_id`'s stored credential. An OAuth server that answers
    HTTP 401 gets one token refresh and one retry, so an access token that expired before
    the background refresh ran doesn't hide every tool. Returns the tools and the auth
    record they were fetched with — the refreshed one when a refresh happened.

    What it finds is kept in the tool cache, for chats and later listings.
    """
    config = instance_config_from_dict(instance)
    listing, auth = await discover_listing_for_owner(instance, auth, owner_id, config_service, timeout_seconds=timeout_seconds)
    tools = tool_infos_from_listing(listing.tools, config, namespace)
    await tool_cache.write(
        config_service, instance=instance, config=config, owner_id=owner_id, auth=auth,
        catalog=tool_cache.catalog_from_tools(tools, listing.instructions),
        server_ttl_seconds=listing.ttl_seconds, public=listing.public,
    )
    return tools, auth


async def discover_listing_for_owner(
    instance: dict[str, Any],
    auth: dict[str, Any],
    owner_id: str,
    config_service: "ConfigurationService",
    *,
    timeout_seconds: float | None = None,
) -> tuple[ToolListing, dict[str, Any]]:
    """`discover_tools_for_owner` before namespacing, with the server's instructions and hints."""
    try:
        return await _discover_listing_for_owner(instance, auth, owner_id, config_service, timeout_seconds=timeout_seconds)
    except MCPConnectionError as exc:
        scopes = await step_up.remember_needed_scopes(config_service, instance, owner_id, exc)
        if not scopes:
            raise
        raise MCPInsufficientScopeError(
            f"This server needs more permission than your sign-in grants (scopes: {', '.join(scopes)}). "
            "Reconnect it to grant it.",
            scopes=scopes,
        ) from exc


async def _discover_listing_for_owner(
    instance: dict[str, Any],
    auth: dict[str, Any],
    owner_id: str,
    config_service: "ConfigurationService",
    *,
    timeout_seconds: float | None = None,
) -> tuple[ToolListing, dict[str, Any]]:
    config = instance_config_from_dict(instance)
    auth_mode = instance.get("authMode", "")
    credentials = credentials_to_discovery_dict(auth_mode, auth)
    try:
        return await discover_tool_listing(config, credentials, timeout_seconds), auth
    except MCPConnectionError as exc:
        if auth_mode != MCPAuthMode.OAUTH.value or not is_http_unauthorized(exc):
            raise
    logger.info("MCP instance %s rejected discovery with HTTP 401; refreshing the token once", config.id)
    tokens = await refresh_credential_record(
        config.id, owner_id, config_service, stale_access_token=credentials.get("accessToken"),
    )
    refreshed = {**auth, "isAuthenticated": True, "oauthTokens": tokens.model_dump(by_alias=True, mode="json")}
    listing = await discover_tool_listing(config, credentials_to_discovery_dict(auth_mode, refreshed), timeout_seconds)
    return listing, refreshed


async def cached_tools_for_owner(
    instance: dict[str, Any],
    auth: dict[str, Any],
    owner_id: str,
    config_service: "ConfigurationService",
    *,
    namespace: str | None = None,
) -> tuple[list[MCPToolInfo], float] | None:
    """The tools the tool cache has for this sign-in, and when they were discovered (epoch
    seconds), or None on a miss."""
    config = instance_config_from_dict(instance)
    hit = await tool_cache.read(config_service, instance=instance, config=config, owner_id=owner_id, auth=auth)
    if hit is None:
        return None
    return tool_infos_from_listing(tool_cache.tool_listing(hit.catalog), config, namespace), hit.pointer.discovered_at
