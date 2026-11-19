"""Zapier MCP server template - official remote server (Streamable HTTP), OAuth + DCR."""

from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="zapier",
        display_name="Zapier",
        description="Connect to Zapier's MCP server to discover and use actions across your connected apps and workflows.",
        icon="/icons/connectors/zapier.svg",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.OAUTH,
        supported_auth_modes=[MCPAuthMode.OAUTH],
        default_url="https://mcp.zapier.com/api/v1/connect",
        authorization_url="https://mcp.zapier.com/oauth/authorize",
        token_url="https://mcp.zapier.com/api/v1/oauth/token",
        default_scopes=[],
        supports_dcr=True,
        documentation_url="https://docs.zapier.com/mcp/get-started/connect",
        auth_hint=AuthHint(
            label="Zapier OAuth",
            help_text="Sign in with your Zapier account.",
        ),
        tags=["automation", "productivity", "integrations"],
    )
)
class ZapierMCPServer:
    """Marker class - see `mcp_template` for the registered catalog entry."""
