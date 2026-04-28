"""
MCP Server Registry (in-memory catalog singleton).

Holds the built-in catalog of MCP server templates and provides
listing, search, and pagination for the API layer. Instance
management is handled by etcd, not this registry.
"""

from __future__ import annotations

import importlib
import inspect
import logging
from typing import Any

from app.agents.mcp.models import MCPServerTemplate

logger = logging.getLogger(__name__)

_registry_instance: MCPServerRegistry | None = None


class MCPServerRegistry:
    """
    Singleton registry for MCP server templates.
    Uses dynamic auto-discovery to find and register server templates.
    Initialized once at application startup.
    """

    def __init__(self) -> None:
        self._catalog: dict[str, MCPServerTemplate] = {}
        self._initialized = False
        logger.info("MCPServerRegistry created (empty until auto_discover_mcp_servers is called)")

    def register_server(self, server_class: type) -> bool:
        """
        Register an MCP server template class in the in-memory registry.

        Extracts metadata from the server class (added by @MCPServer decorator)
        and creates an MCPServerTemplate entry.

        Args:
            server_class: The server class decorated with @MCPServer

        Returns:
            True if registration succeeded, False otherwise
        """
        try:
            # Get metadata from the server class (added by @MCPServer decorator)
            metadata = getattr(server_class, '_mcp_server_metadata', {})
            if not metadata:
                logger.warning(f"Class {server_class.__name__} missing _mcp_server_metadata")
                return False

            type_id = metadata.get('type_id')
            if not type_id:
                logger.warning(f"Server class {server_class.__name__} missing type_id in metadata")
                return False

            # Create MCPServerTemplate from metadata
            template = MCPServerTemplate(**metadata)
            
            # Store in catalog
            self._catalog[type_id] = template
            
            logger.info(f"Registered MCP server template: {template.display_name} ({type_id})")
            return True

        except Exception as e:
            logger.error(f"Failed to register MCP server {server_class.__name__}: {e}", exc_info=True)
            return False

    def discover_servers(self, module_paths: list[str]) -> None:
        """
        Discover and register MCP server templates from module paths.

        Args:
            module_paths: List of Python module paths to scan for @MCPServer decorated classes
        """
        for module_path in module_paths:
            try:
                module = importlib.import_module(module_path)

                for _name, obj in inspect.getmembers(module):
                    # Check for _is_mcp_server marker (added by @MCPServer decorator)
                    if inspect.isclass(obj) and getattr(obj, '_is_mcp_server', False):
                        self.register_server(obj)

            except Exception as e:
                logger.error(f"Failed to discover MCP servers in {module_path}: {e}", exc_info=True)

    def auto_discover_mcp_servers(self) -> None:
        """
        Auto-discover MCP server templates from standard module paths.

        This is called at application startup to populate the registry
        with all available MCP server templates.
        """
        if self._initialized:
            logger.warning("MCP server auto-discovery already ran, skipping")
            return

        standard_paths = [
            'app.agents.mcp.servers.brave_search',
            'app.agents.mcp.servers.exa',
            'app.agents.mcp.servers.github',
            'app.agents.mcp.servers.jira',
            'app.agents.mcp.servers.notion',
            'app.agents.mcp.servers.slack',
        ]

        logger.info("Starting MCP server auto-discovery...")
        self.discover_servers(standard_paths)
        self._initialized = True
        logger.info(f"MCP server auto-discovery complete. Loaded {len(self._catalog)} templates")

    def list_all(self) -> list[MCPServerTemplate]:
        """Return all registered templates."""
        return list(self._catalog.values())

    def get_template(self, type_id: str) -> MCPServerTemplate | None:
        """Get a catalog template by type_id."""
        return self._catalog.get(type_id)

    def list_templates(
        self,
        search: str | None = None,
        page: int = 1,
        limit: int = 20,
    ) -> dict[str, Any]:
        """List catalog templates with optional search and pagination."""
        # Get all templates
        all_items = list(self._catalog.values())

        # Apply search filter if provided
        if search:
            q = search.lower()
            all_items = [
                tpl for tpl in all_items
                if (
                    q in tpl.type_id.lower()
                    or q in tpl.display_name.lower()
                    or q in tpl.description.lower()
                    or any(q in tag for tag in tpl.tags)
                )
            ]

        total = len(all_items)
        start = (page - 1) * limit
        end = start + limit
        items = all_items[start:end]

        return {
            "items": [t.model_dump(by_alias=True) for t in items],
            "total": total,
            "page": page,
            "limit": limit,
            "totalPages": (total + limit - 1) // limit if limit > 0 else 0,
        }

    def get_template_schema(self, type_id: str) -> dict[str, Any] | None:
        """Get full schema for a template including auth and config metadata."""
        tpl = self.get_template(type_id)
        if not tpl:
            return None

        auth_data = tpl.auth.model_dump(by_alias=True) if tpl.auth else {}

        return {
            **tpl.model_dump(by_alias=True),
            "configSchema": {
                "transport": tpl.transport.value,
                "requiredEnv": tpl.required_env,
                "optionalEnv": tpl.optional_env,
                "authMode": tpl.auth_mode.value,
                "supportedAuthTypes": tpl.supported_auth_types,
            },
            "authConfig": {
                "methods": auth_data.get("methods", []),
                "defaultMethod": auth_data.get("defaultMethod", ""),
                "oauth2AuthorizationUrl": auth_data.get("oauth2AuthorizationUrl", ""),
                "oauth2TokenUrl": auth_data.get("oauth2TokenUrl", ""),
                "oauth2Scopes": auth_data.get("oauth2Scopes", []),
                "envMapping": auth_data.get("envMapping", {}),
            },
        }


def get_mcp_server_registry() -> MCPServerRegistry:
    """Get or create the global MCPServerRegistry singleton."""
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = MCPServerRegistry()
    return _registry_instance
