"""
MCP Server Decorator

Decorator for registering MCP server templates with metadata.
Mirrors the @Toolset decorator pattern from toolset_registry.py.
"""

from collections.abc import Callable
from typing import Any

from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPTransport


def MCPServer(
    type_id: str,
    display_name: str,
    description: str,
    transport: MCPTransport = MCPTransport.STDIO,
    command: str = "",
    default_args: list[str] | None = None,
    url: str = "",
    auth_mode: MCPAuthMode = MCPAuthMode.NONE,
    supported_auth_types: list[str] | None = None,
    required_env: list[str] | None = None,
    optional_env: list[str] | None = None,
    redirect_uri: str = "",
    icon_path: str = "",
    documentation_url: str = "",
    tags: list[str] | None = None,
    auth: AuthHint | None = None,
) -> Callable[[type], type]:
    """
    Decorator to register an MCP server template with metadata.

    Args:
        type_id: Unique identifier for this MCP server type (e.g., "brave_search")
        display_name: Human-readable name (e.g., "Brave Search")
        description: Description of the MCP server and its capabilities
        transport: Transport type (stdio, sse, streamable_http)
        command: Command to launch the server (for stdio)
        default_args: Default arguments for the command
        url: Server URL (for HTTP-based transports)
        auth_mode: Authentication mode (none, api_token, oauth, headers)
        supported_auth_types: List of supported auth types
        required_env: Required environment variables
        optional_env: Optional environment variables
        redirect_uri: Relative redirect URI path for OAuth callbacks
        icon_path: Path to the server icon
        documentation_url: URL to server documentation
        tags: Tags for search and categorization
        auth: Auth hint with OAuth URLs, scopes, and env mapping

    Returns:
        Decorator function that marks a class as an MCP server template

    Example:
        @MCPServer(
            type_id="brave_search",
            display_name="Brave Search",
            description="Brave Search API integration",
            transport=MCPTransport.STDIO,
            command="npx",
            default_args=["-y", "@brave/brave-search-mcp-server"],
            auth_mode=MCPAuthMode.API_TOKEN,
            supported_auth_types=["API_TOKEN"],
            required_env=["BRAVE_API_KEY"],
            auth=AuthHint(methods=["api_key"], ...),
        )
        class BraveSearch:
            pass
    """
    def decorator(cls: type) -> type:
        # Normalize lists (treat None as empty list)
        default_args_list = default_args or []
        supported_auth_types_list = supported_auth_types or []
        required_env_list = required_env or []
        optional_env_list = optional_env or []
        tags_list = tags or []

        # Safely extract enum values
        transport_value = transport.value if hasattr(transport, 'value') else str(transport)
        auth_mode_value = auth_mode.value if hasattr(auth_mode, 'value') else str(auth_mode)

        # Store metadata in the class (will be read by MCPServerRegistry.register_server)
        cls._mcp_server_metadata = {
            "type_id": type_id,
            "display_name": display_name,
            "description": description,
            "transport": transport_value,
            "command": command,
            "default_args": default_args_list,
            "url": url,
            "auth_mode": auth_mode_value,
            "supported_auth_types": supported_auth_types_list,
            "required_env": required_env_list,
            "optional_env": optional_env_list,
            "redirect_uri": redirect_uri,
            "icon_path": icon_path,
            "documentation_url": documentation_url,
            "tags": tags_list,
            "auth": auth,
        }

        # Mark class as an MCP server
        cls._is_mcp_server = True

        return cls
    return decorator
