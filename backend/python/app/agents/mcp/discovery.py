"""Live tool discovery against a connected MCP server instance.

Namespaces every discovered tool as `mcp_{server_type}_{tool}` so the agent loop (Phase 2)
can distinguish MCP-sourced tools from native connector/toolset tools.
"""
import asyncio
import logging
from typing import Any

from app.agents.mcp.client import MCPClientManager, MCPConnectionError
from app.agents.mcp.models import MCPAuthMode, MCPServerConfig, MCPToolInfo, MCPTransport

logger = logging.getLogger(__name__)

DEFAULT_DISCOVERY_TIMEOUT_SECONDS = 30.0
DEFAULT_ENV_VAR_NAME = "API_TOKEN"


def build_namespaced_tool_name(server_type: str, tool_name: str) -> str:
    """`mcp_{server_type}_{tool}` — keeps MCP tools distinguishable from native tools."""
    normalized_type = server_type.lower().strip().replace(" ", "_").replace("-", "_")
    return f"mcp_{normalized_type}_{tool_name}"


def _allowed_stdio_env_names(config: MCPServerConfig) -> set[str]:
    return set(config.required_env or []) | set(config.optional_env or [])


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
    timeout_seconds: float = DEFAULT_DISCOVERY_TIMEOUT_SECONDS,
) -> list[MCPToolInfo]:
    """Connect to `config`, list its tools, and return them namespaced for the agent loop.

    Raises MCPConnectionError (including on timeout) — callers decide whether discovery
    failures are fatal (e.g. `includeTools=false` lets `/my-mcp-servers` skip this entirely).
    """
    env, headers = build_auth_env_and_headers(config, credentials)
    manager = MCPClientManager(config, env=env, headers=headers)

    try:
        raw_tools = await asyncio.wait_for(manager.list_tools(), timeout=timeout_seconds)
    except asyncio.TimeoutError as e:
        raise MCPConnectionError(
            f"Timed out discovering tools for MCP instance {config.id} after {timeout_seconds}s"
        ) from e

    server_type = config.type_id or config.name
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

        input_schema = getattr(tool, "inputSchema", None)
        if input_schema is None and isinstance(tool, dict):
            input_schema = tool.get("inputSchema")

        tools.append(
            MCPToolInfo(
                name=name,
                namespaced_name=build_namespaced_tool_name(server_type, name),
                description=description,
                input_schema=input_schema or {},
            )
        )
    return tools
