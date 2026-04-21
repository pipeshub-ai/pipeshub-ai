"""
MCP Client Manager.

Manages lifecycle of multiple MCP server connections using fastmcp.Client.
Each connection is identified by a server name (typically the instance_id).
"""

from __future__ import annotations

import logging
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import SSETransport, StdioTransport, StreamableHttpTransport

from app.agents.mcp.models import MCPAuthMode, MCPServerConfig, MCPTransport

logger = logging.getLogger(__name__)


class MCPClientManager:
    """Manages lifecycle of multiple MCP server connections."""

    def __init__(self) -> None:
        self._clients: dict[str, Client] = {}
        self._contexts: dict[str, Any] = {}

    async def connect(self, config: MCPServerConfig) -> Client:
        """Start and initialize an MCP server connection."""
        if config.name in self._clients:
            return self._clients[config.name]

        client = self._build_client(config)

        await client.__aenter__()

        self._clients[config.name] = client
        logger.info("Connected to MCP server '%s' (%s)", config.name, config.transport.value)
        return client

    async def disconnect(self, server_name: str) -> None:
        """Disconnect a specific MCP server."""
        client = self._clients.pop(server_name, None)
        if client is not None:
            try:
                await client.__aexit__(None, None, None)
            except Exception:
                logger.warning("Error disconnecting MCP server '%s'", server_name, exc_info=True)
            logger.info("Disconnected MCP server '%s'", server_name)

    async def disconnect_all(self) -> None:
        """Disconnect all MCP servers."""
        names = list(self._clients.keys())
        for name in names:
            await self.disconnect(name)

    def get_client(self, server_name: str) -> Client | None:
        """Get a connected client by name."""
        return self._clients.get(server_name)

    def list_servers(self) -> list[str]:
        """List names of all connected servers."""
        return list(self._clients.keys())

    async def list_tools(self, server_name: str) -> list[Any]:
        """List available tools from a connected MCP server."""
        client = self._clients.get(server_name)
        if not client:
            raise RuntimeError(f"MCP server '{server_name}' is not connected")
        return await client.list_tools()

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> Any:
        """Call a tool on a specific MCP server."""
        client = self._clients.get(server_name)
        if not client:
            raise RuntimeError(f"MCP server '{server_name}' is not connected")
        return await client.call_tool(tool_name, arguments or {})

    @staticmethod
    def _build_client(config: MCPServerConfig) -> Client:
        """Build a fastmcp Client from a resolved MCPServerConfig.

        fastmcp v3 requires transport-specific classes for passing headers
        and env vars -- they cannot be passed directly to ``Client()``.
        """
        if config.transport == MCPTransport.STDIO:
            logger.debug(
                "Building STDIO client for '%s': command=%s, args=%s, env_keys=%s",
                config.name, config.command, config.args, list((config.env or {}).keys()),
            )
            transport = StdioTransport(
                command=config.command,
                args=config.args or [],
                env=config.env or None,
            )
            return Client(transport)

        if not config.url:
            raise ValueError(
                f"MCP server '{config.name}' has transport '{config.transport.value}' "
                "but no URL configured"
            )

        headers = dict(config.headers) if config.headers else {}

        if config.auth_mode == MCPAuthMode.OAUTH:
            if "Authorization" not in headers:
                logger.warning(
                    "OAuth MCP server '%s' has no Authorization header — "
                    "the server may reject unauthenticated requests",
                    config.name,
                )

        logger.debug(
            "Building %s client for '%s': url=%s, has_auth=%s, header_keys=%s",
            config.transport.value, config.name, config.url,
            "Authorization" in headers, list(headers.keys()),
        )

        if config.transport == MCPTransport.SSE:
            transport = SSETransport(url=config.url, headers=headers)
        else:
            transport = StreamableHttpTransport(url=config.url, headers=headers)

        return Client(transport)
