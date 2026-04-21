"""
MCP Server Registry (in-memory catalog singleton).

Holds the built-in catalog of MCP server templates and provides
listing, search, and pagination for the API layer. Instance
management is handled by etcd, not this registry.
"""

from __future__ import annotations

import logging
from typing import Any

from app.agents.mcp.catalog import BUILTIN_CATALOG, get_template, list_templates, search_templates
from app.agents.mcp.models import MCPServerTemplate

logger = logging.getLogger(__name__)

_registry_instance: MCPServerRegistry | None = None


class MCPServerRegistry:
    """
    Singleton registry that wraps the built-in MCP server catalog.
    Initialized once at application startup.
    """

    def __init__(self) -> None:
        self._catalog = BUILTIN_CATALOG
        logger.info(
            "MCPServerRegistry initialized with %d built-in templates",
            len(self._catalog),
        )

    def get_template(self, type_id: str) -> MCPServerTemplate | None:
        return get_template(type_id)

    def resolve_runtime_fields(self, instance: dict[str, Any]) -> dict[str, Any]:
        """Return runtime connection fields for an MCP server instance.

        For custom instances, reads from the stored instance dict.
        For catalog-backed instances, reads from the template. If the
        template is missing, logs a warning and returns empty values
        (connection attempts will then fail meaningfully downstream).

        Used by both the MCP service (tool discovery) and the agent
        runtime (building mcp_server_configs for wrapper.py) so that
        connection fields are always fresh — never read from a stale
        user_auth snapshot.
        """
        server_type = instance.get("serverType", "custom")
        if server_type == "custom":
            return {
                "command": instance.get("command", ""),
                "args": list(instance.get("args", [])),
                "url": instance.get("url", ""),
                "transport": instance.get("transport", "stdio"),
                "requiredEnv": list(instance.get("requiredEnv", [])),
                "authHeaderMapping": dict(instance.get("authHeaderMapping", {})),
            }
        template = self.get_template(server_type)
        if template is None:
            logger.warning(
                "Template '%s' not found for instance '%s'",
                server_type,
                instance.get("_id", "?"),
            )
            return {
                "command": "",
                "args": [],
                "url": "",
                "transport": "stdio",
                "requiredEnv": [],
                "authHeaderMapping": {},
            }
        return {
            "command": template.command,
            "args": list(template.default_args),
            "url": template.url,
            "transport": template.transport.value,
            "requiredEnv": list(template.required_env),
            "authHeaderMapping": (
                dict(template.auth.env_mapping) if template.auth.env_mapping else {}
            ),
        }

    def list_templates(
        self,
        search: str | None = None,
        page: int = 1,
        limit: int = 20,
    ) -> dict[str, Any]:
        """List catalog templates with optional search and pagination."""
        if search:
            all_items = search_templates(search)
        else:
            all_items = list_templates()

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
