"""Notion MCP server template — official remote server (Streamable HTTP), OAuth-only."""
from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="notion",
        display_name="Notion",
        description="Pages, databases, and blocks in a Notion workspace.",
        icon="/icons/connectors/notion.svg",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.OAUTH,
        supported_auth_modes=[MCPAuthMode.OAUTH],
        default_url="https://mcp.notion.com/mcp",
        # Notion's MCP server fronts its own AS at mcp.notion.com (not api.notion.com's
        # direct-OAuth endpoints) and supports RFC 7591 DCR — confirmed by probing
        # https://mcp.notion.com/.well-known/oauth-authorization-server. These are only the
        # UI-hint defaults; the authorize flow always re-discovers live (see dcr.py).
        authorization_url="https://mcp.notion.com/authorize",
        token_url="https://mcp.notion.com/token",
        default_scopes=[],
        supports_dcr=True,
        documentation_url="https://developers.notion.com/docs/mcp",
        auth_hint=AuthHint(
            label="Notion OAuth",
            help_text="Sign in with your Notion account.",
        ),
        tags=["docs", "productivity"],
    )
)
class NotionMCPServer:
    """Marker class — see `mcp_template` for the registered catalog entry."""
