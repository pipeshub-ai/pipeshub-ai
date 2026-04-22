"""
MCP Tool Discovery.

Connects to MCP servers and discovers tools dynamically.
Namespaces tools as ``mcp_{server_name}_{tool_name}`` to avoid collisions
with built-in toolsets and across different MCP servers.
"""

from __future__ import annotations

import logging
from typing import Any

from app.agents.constants.mcp_server_constants import normalize_mcp_server_name
from app.agents.mcp.client import MCPClientManager
from app.agents.mcp.models import MCPServerConfig, MCPToolInfo

logger = logging.getLogger(__name__)


class MCPToolDiscovery:
    """Discovers tools from connected MCP servers."""

    def __init__(self, manager: MCPClientManager) -> None:
        self.manager = manager
        self._tool_map: dict[str, tuple[str, str]] = {}

    async def discover_tools(
        self,
        instance_id: str,
        config: MCPServerConfig,
    ) -> list[MCPToolInfo]:
        """
        Connect to a single MCP server and discover its tools.

        Returns a list of MCPToolInfo with namespaced names.
        """
        try:
            client = await self.manager.connect(config)
            sdk_tools = await client.list_tools()
        except Exception:
            logger.warning(
                "Failed to discover tools from MCP server '%s'",
                config.name,
                exc_info=True,
            )
            return []

        server_key = normalize_mcp_server_name(config.server_type or config.name)
        discovered: list[MCPToolInfo] = []

        for tool in sdk_tools:
            namespaced = f"mcp_{server_key}_{tool.name}"
            input_schema = (
                tool.inputSchema
                if hasattr(tool, "inputSchema") and tool.inputSchema
                else {"type": "object", "properties": {}}
            )

            info = MCPToolInfo(
                name=tool.name,
                namespaced_name=namespaced,
                description=tool.description or f"MCP tool: {tool.name}",
                input_schema=input_schema,
                server_name=config.name,
                instance_id=instance_id,
            )
            discovered.append(info)
            self._tool_map[namespaced] = (config.name, tool.name)

        logger.info(
            "Discovered %d tools from MCP server '%s'",
            len(discovered),
            config.name,
        )
        return discovered

    async def discover_all(
        self,
        configs: dict[str, MCPServerConfig],
    ) -> dict[str, list[MCPToolInfo]]:
        """
        Discover tools from multiple MCP servers.

        Args:
            configs: Mapping of instance_id -> MCPServerConfig

        Returns:
            Mapping of instance_id -> list of discovered MCPToolInfo
        """
        results: dict[str, list[MCPToolInfo]] = {}
        for instance_id, config in configs.items():
            tools = await self.discover_tools(instance_id, config)
            results[instance_id] = tools
        return results

    def resolve_tool(self, namespaced_name: str) -> tuple[str, str] | None:
        """
        Resolve a namespaced tool name to (server_name, original_tool_name).

        Returns None if the tool is not known.
        """
        return self._tool_map.get(namespaced_name)
