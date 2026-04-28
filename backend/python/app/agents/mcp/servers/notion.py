"""
Notion MCP Server Template

Official Notion MCP server integration via the notionhq package.
"""

from app.agents.mcp.mcp_server_decorator import MCPServer
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPTransport


@MCPServer(
    type_id="notion",
    display_name="Notion",
    description=(
        "Official Notion MCP server. Search, read, create, and update pages, "
        "databases, blocks, comments, and users in your Notion workspace. "
        "Requires a Notion Internal Integration Token."
    ),
    transport=MCPTransport.STDIO,
    command="npx",
    default_args=["-y", "@notionhq/notion-mcp-server"],
    auth_mode=MCPAuthMode.API_TOKEN,
    supported_auth_types=["API_TOKEN"],
    required_env=["OPENAPI_MCP_HEADERS"],
    optional_env=[],
    icon_path="/assets/icons/connectors/notion.svg",
    documentation_url="https://github.com/makenotion/notion-mcp-server",
    tags=["productivity", "notes", "databases", "wiki", "collaboration"],
    auth=AuthHint(
        methods=["api_key"],
        default_method="api_key",
        env_mapping={"apiToken": "OPENAPI_MCP_HEADERS"},
    ),
    use_admin_auth=True,
)
class NotionMCP:
    """Notion MCP server template."""
    pass
