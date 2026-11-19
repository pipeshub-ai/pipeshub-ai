"""monday.com MCP server template — official hosted server, OAuth + DCR."""
from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="monday",
        display_name="monday.com",
        description="Find and manage boards, items, and updates in monday.com.",
        icon="/icons/connectors/monday.svg",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.OAUTH,
        supported_auth_modes=[MCPAuthMode.OAUTH],
        default_url="https://mcp.monday.com/mcp",
        # PipesHub discovery against the official endpoint on 2026-11-09 returned
        # issuer https://auth.monday.com/mcp and DCR at
        # https://auth.monday.com/oauth_ms/oauth/register, with S256 support.
        authorization_url="https://auth.monday.com/oauth2/authorize",
        token_url="https://auth.monday.com/oauth_ms/oauth/token",
        default_scopes=[],
        supports_dcr=True,
        documentation_url="https://developer.monday.com/api-reference/docs/mondaycom-mcp",
        tags=["project-management", "productivity"],
    )
)
class MondayMCPServer:
    """Marker class — see `mcp_template` for the registered catalog entry."""
