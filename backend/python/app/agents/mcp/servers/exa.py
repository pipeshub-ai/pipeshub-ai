"""
Exa Search MCP Server Template

Exa web search and content retrieval via the remote Exa MCP server.
"""

from app.agents.mcp.mcp_server_decorator import MCPServer
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPTransport


@MCPServer(
    type_id="exa",
    display_name="Exa Search",
    description=(
        "Exa web search and content retrieval via the remote Exa MCP server. "
        "Search the web by meaning, fetch and read full page content as clean "
        "markdown, and perform advanced filtered searches. Requires an Exa API key."
    ),
    transport=MCPTransport.STREAMABLE_HTTP,
    url="https://mcp.exa.ai/mcp",
    auth_mode=MCPAuthMode.API_TOKEN,
    supported_auth_types=["API_TOKEN"],
    required_env=[],
    optional_env=[],
    icon_path="/assets/icons/connectors/exa.svg",
    documentation_url="https://exa.ai/docs/reference/exa-mcp",
    tags=["search", "web-search", "research"],
    auth=AuthHint(
        methods=["api_key"],
        default_method="api_key",
        env_mapping={"apiToken": "x-api-key"},
    ),
    use_admin_auth=True,
)
class ExaSearch:
    """Exa Search MCP server template."""
    pass
