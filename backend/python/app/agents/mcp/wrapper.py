"""
MCP Tool Wrapper for LangChain.

Wraps MCP tools discovered from external servers as LangChain BaseTool
instances so they can be used by the agent framework alongside built-in
toolset tools.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from langchain_core.tools import BaseTool

from app.agents.mcp.client import MCPClientManager
from app.agents.mcp.models import MCPAuthMode, MCPServerConfig, MCPToolInfo, MCPTransport

logger = logging.getLogger(__name__)

MCP_MAX_RETRIES = 3
MCP_RETRY_BASE_DELAY = 1.0  # seconds

_TRANSIENT_PATTERNS = [
    "try again",
    "temporarily unavailable",
    "having trouble",
    "timeout",
    "timed out",
    "connection reset",
    "connection refused",
    "service unavailable",
    "internal server error",
    "502",
    "503",
    "504",
]

_NON_TRANSIENT_PATTERNS = [
    "unauthorized",
    "forbidden",
    "not found",
    "invalid",
    "authentication",
    "permission",
    "401",
    "403",
    "404",
]


def _is_transient_error(error_text: str) -> bool:
    """Return True if the error looks like a transient/retriable failure."""
    lower = error_text.lower()
    if any(p in lower for p in _NON_TRANSIENT_PATTERNS):
        return False
    return any(p in lower for p in _TRANSIENT_PATTERNS)


class MCPToolWrapper(BaseTool):
    """
    LangChain tool that proxies calls to an external MCP server.

    At invocation time it resolves connection config from ChatState,
    connects via fastmcp Client, calls the tool, and returns the result.
    """

    name: str = ""
    description: str = ""

    mcp_server_name: str = ""
    mcp_tool_name: str = ""
    mcp_instance_id: str = ""
    mcp_input_schema: dict[str, Any] = {}
    mcp_fixed_params: dict[str, str] = {}
    _state: dict[str, Any] = {}
    _manager: MCPClientManager | None = None

    class Config:
        arbitrary_types_allowed = True

    def __init__(
        self,
        tool_info: MCPToolInfo,
        state: dict[str, Any],
        manager: MCPClientManager | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            name=tool_info.namespaced_name,
            description=tool_info.description,
            **kwargs,
        )
        self.mcp_server_name = tool_info.server_name
        self.mcp_tool_name = tool_info.name
        self.mcp_instance_id = tool_info.instance_id
        self.mcp_fixed_params = dict(tool_info.fixed_params) if tool_info.fixed_params else {}

        # Build a cleaned schema that hides fixed params from the LLM
        self.mcp_input_schema = self._strip_fixed_params(tool_info.input_schema, self.mcp_fixed_params)

        self._state = state
        self._manager = manager

    @staticmethod
    def _strip_fixed_params(schema: dict[str, Any], fixed: dict[str, str]) -> dict[str, Any]:
        """Remove fixed-param fields from the JSON Schema so the LLM never sees them."""
        if not fixed or not schema:
            return schema

        cleaned = dict(schema)
        props = cleaned.get("properties")
        if isinstance(props, dict):
            cleaned["properties"] = {k: v for k, v in props.items() if k not in fixed}
        required = cleaned.get("required")
        if isinstance(required, list):
            cleaned["required"] = [r for r in required if r not in fixed]
        return cleaned

    @property
    def args(self) -> dict[str, Any]:
        """Return JSON Schema properties for LangChain arg parsing."""
        return self.mcp_input_schema.get("properties", {})

    def _run(self, *args: Any, **kwargs: Any) -> str:
        raise NotImplementedError("MCPToolWrapper only supports async execution via _arun")

    def set_manager(self, manager: MCPClientManager | None) -> None:
        """Inject a shared MCPClientManager for connection reuse."""
        self._manager = manager

    async def _arun(self, **kwargs: Any) -> str:
        """Execute the MCP tool call asynchronously with retry for transient errors."""
        try:
            if self.mcp_fixed_params:
                injected = []
                for k, v in self.mcp_fixed_params.items():
                    if k not in kwargs:
                        kwargs[k] = v
                        injected.append(k)
                if injected:
                    logger.debug(
                        "MCP '%s': injected fixedParams %s",
                        self.name, injected,
                    )

            config = self._resolve_config()
            if config is None:
                return json.dumps({
                    "error": f"No configuration found for MCP server instance '{self.mcp_instance_id}'"
                })

            manager = self._manager or MCPClientManager()
            should_cleanup = self._manager is None

            try:
                client = await manager.connect(config)
                logger.debug(
                    "MCP call '%s' -> tool='%s', arg_keys=%s",
                    self.name, self.mcp_tool_name, list(kwargs.keys()),
                )

                last_error: Exception | None = None
                for attempt in range(1, MCP_MAX_RETRIES + 1):
                    try:
                        result = await client.call_tool(self.mcp_tool_name, kwargs)
                        return self._extract_result(result)
                    except Exception as e:
                        last_error = e
                        if attempt < MCP_MAX_RETRIES and _is_transient_error(str(e)):
                            delay = MCP_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                            logger.warning(
                                "MCP tool '%s' transient error (attempt %d/%d), "
                                "retrying in %.1fs: %s",
                                self.name, attempt, MCP_MAX_RETRIES, delay, str(e),
                            )
                            await asyncio.sleep(delay)
                            continue
                        raise

                raise last_error  # type: ignore[misc]

            finally:
                if should_cleanup:
                    await manager.disconnect_all()

        except Exception as e:
            logger.error(
                "MCP tool '%s' execution failed: %s",
                self.name,
                str(e),
                exc_info=True,
            )
            return json.dumps({"error": f"MCP tool execution failed: {str(e)}"})

    def _resolve_config(self) -> MCPServerConfig | None:
        """Resolve MCP server config from ChatState."""
        mcp_server_configs = self._state.get("mcp_server_configs", {})
        config_data = mcp_server_configs.get(self.mcp_instance_id)
        if not config_data:
            return None

        transport_str = config_data.get("transport", "stdio")
        try:
            transport = MCPTransport(transport_str)
        except ValueError:
            transport = MCPTransport.STDIO

        auth_mode_str = config_data.get("authMode", "none")
        try:
            auth_mode = MCPAuthMode(auth_mode_str)
        except ValueError:
            auth_mode = MCPAuthMode.NONE

        env: dict[str, str] = {}
        headers: dict[str, str] = {}
        auth = config_data.get("auth", {})
        credentials = config_data.get("credentials", {})

        if transport == MCPTransport.STDIO:
            token = (
                auth.get("apiToken")
                or credentials.get("access_token")
                or ""
            )
            if token:
                required_env = config_data.get("requiredEnv", [])
                if required_env:
                    env[required_env[0]] = token
        else:
            if auth_mode == MCPAuthMode.OAUTH:
                token = (
                    credentials.get("access_token")
                    or auth.get("access_token")
                    or auth.get("apiToken")
                    or ""
                )
                if token:
                    headers["Authorization"] = f"Bearer {token}"
            elif auth_mode == MCPAuthMode.HEADERS:
                header_name = auth.get("headerName", "Authorization")
                header_val = auth.get("headerValue") or auth.get("apiToken") or ""
                if header_val:
                    headers[header_name] = header_val
            else:
                token = (
                    auth.get("apiToken")
                    or credentials.get("access_token")
                    or ""
                )
                if token:
                    headers["Authorization"] = f"Bearer {token}"

        return MCPServerConfig(
            name=self.mcp_instance_id,
            transport=transport,
            command=config_data.get("command", ""),
            args=config_data.get("args", []),
            env=env,
            url=config_data.get("url", ""),
            headers=headers,
            auth_mode=auth_mode,
        )

    @staticmethod
    def _extract_result(result: Any) -> str:
        """Extract a string result from MCP CallToolResult."""
        if result is None:
            return ""
        if isinstance(result, str):
            return result
        if hasattr(result, "content"):
            parts = []
            for item in result.content:
                if hasattr(item, "text"):
                    parts.append(item.text)
                else:
                    parts.append(str(item))
            return "\n".join(parts)
        return json.dumps(result) if not isinstance(result, str) else result
