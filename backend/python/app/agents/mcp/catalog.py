"""
MCP Server Catalog (DEPRECATED)

This module previously contained the static BUILTIN_CATALOG dict.

MIGRATION NOTICE:
The catalog has been migrated to a dynamic auto-discovery pattern.
MCP server templates are now defined as individual modules in
app/agents/mcp/servers/ using the @MCPServer decorator.

To add a new MCP server template:
1. Create a new file in app/agents/mcp/servers/ (e.g., my_server.py)
2. Decorate a class with @MCPServer(...metadata...)
3. Add the module path to registry.py -> auto_discover_mcp_servers()

The MCPServerRegistry now handles all catalog operations via dynamic discovery.
"""

from __future__ import annotations

# This file is kept for backward compatibility during the migration period.
# It can be deleted once all code references are removed.
