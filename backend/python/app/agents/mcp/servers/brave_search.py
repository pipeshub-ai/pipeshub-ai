"""
Brave Search MCP Server Template

Brave Search API integration providing comprehensive search capabilities.
"""

from app.agents.mcp.mcp_server_decorator import MCPServer
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPTransport


@MCPServer(
    type_id="brave_search",
    display_name="Brave Search",
    description=(
        "Brave Search API integration providing comprehensive search capabilities "
        "including web search, local business search, image search, video search, "
        "news search, and AI-powered summarization. Requires a Brave Search API key."
    ),
    transport=MCPTransport.STDIO,
    command="npx",
    default_args=["-y", "@brave/brave-search-mcp-server"],
    auth_mode=MCPAuthMode.API_TOKEN,
    supported_auth_types=["API_TOKEN"],
    required_env=["BRAVE_API_KEY"],
    optional_env=[
        "BRAVE_MCP_TRANSPORT",
        "BRAVE_MCP_PORT",
        "BRAVE_MCP_HOST",
        "BRAVE_MCP_LOG_LEVEL",
        "BRAVE_MCP_ENABLED_TOOLS",
        "BRAVE_MCP_DISABLED_TOOLS",
    ],
    icon_path="/assets/icons/mcp-servers/brave.svg",
    documentation_url="https://github.com/brave/brave-search-mcp-server",
    tags=["search", "web-search", "news", "images", "videos", "local-search"],
    auth=AuthHint(
        methods=["api_key"],
        default_method="api_key",
        env_mapping={"apiKey": "BRAVE_API_KEY"},
    ),
)
class BraveSearch:
    """Brave Search MCP server template."""
    pass
