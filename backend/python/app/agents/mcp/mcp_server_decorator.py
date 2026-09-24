"""Decorator-based auto-discovery for MCP server catalog templates.

Mirrors the toolset registry's decorator pattern (`app/agents/registry/toolset_registry.py`)
but templates are pure metadata (no tool implementations) — the actual tool list is
discovered live from the running MCP server (see `discovery.py`).
"""
import logging
from typing import TypeVar

from app.agents.constants.mcp_server_constants import normalize_mcp_type
from app.agents.mcp.models import MCPServerTemplate

logger = logging.getLogger(__name__)

T = TypeVar("T")

_MCP_SERVER_TEMPLATES: dict[str, MCPServerTemplate] = {}


def mcp_server(template: MCPServerTemplate):
    """Class decorator that registers `template` in the in-memory catalog under `template.type_id`.

    Usage:
        @mcp_server(MCPServerTemplate(type_id="brave_search", ...))
        class BraveSearchMCPServer:
            '''Marker class; the decorated class body is unused, only the template matters.'''
    """

    def _wrap(cls: T) -> T:
        type_id = normalize_mcp_type(template.type_id)
        if type_id in _MCP_SERVER_TEMPLATES:
            raise ValueError(f"Duplicate MCP server template type_id: {type_id}")
        _MCP_SERVER_TEMPLATES[type_id] = template
        cls.mcp_template = template
        logger.debug(f"Registered MCP server template: {type_id}")
        return cls

    return _wrap


def get_registered_templates() -> dict[str, MCPServerTemplate]:
    """Snapshot of every template registered so far via `@mcp_server`."""
    return dict(_MCP_SERVER_TEMPLATES)


def clear_registered_templates() -> None:
    """Reset the in-memory registry. Test-only — production code never needs this."""
    _MCP_SERVER_TEMPLATES.clear()
