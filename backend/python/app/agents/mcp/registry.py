"""In-memory MCP server catalog registry.

Loaded once at connectors-service startup (`app.state.mcp_registry`), mirroring
`app.agents.registry.toolset_registry`.
"""
import importlib
import logging
import pkgutil
from typing import Optional

from app.agents.constants.mcp_server_constants import normalize_mcp_type
from app.agents.mcp.mcp_server_decorator import get_registered_templates
from app.agents.mcp.models import MCPServerTemplate

logger = logging.getLogger(__name__)


class MCPRegistry:
    """Catalog of well-known MCP server templates, discovered from `app.agents.mcp.servers`."""

    def __init__(self) -> None:
        self._templates: dict[str, MCPServerTemplate] = {}
        self._discovered = False

    def auto_discover_templates(self) -> None:
        """Import every module under `app.agents.mcp.servers` so `@mcp_server` decorators run."""
        if self._discovered:
            return

        from app.agents.mcp import servers as servers_pkg

        for module_info in pkgutil.iter_modules(servers_pkg.__path__):
            module_name = f"{servers_pkg.__name__}.{module_info.name}"
            try:
                importlib.import_module(module_name)
            except Exception as e:
                logger.error(f"Failed to import MCP server template module {module_name}: {e}", exc_info=True)

        self._templates = get_registered_templates()
        self._discovered = True
        logger.info(f"MCP registry discovered {len(self._templates)} server templates")

    def list_templates(self) -> list[MCPServerTemplate]:
        return list(self._templates.values())

    def get_template(self, type_id: str) -> Optional[MCPServerTemplate]:
        if not type_id:
            return None
        return self._templates.get(normalize_mcp_type(type_id))


_mcp_registry_singleton: Optional[MCPRegistry] = None


def get_mcp_registry() -> MCPRegistry:
    """Process-wide singleton, matching `get_toolset_registry()`."""
    global _mcp_registry_singleton
    if _mcp_registry_singleton is None:
        _mcp_registry_singleton = MCPRegistry()
    return _mcp_registry_singleton
