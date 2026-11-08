"""Vercel MCP server template - official remote server (Streamable HTTP), OAuth + DCR."""

from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="vercel",
        display_name="Vercel",
        description="Search Vercel documentation, manage projects and deployments, and analyze deployment logs.",
        icon="/icons/connectors/vercel.svg",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.OAUTH,
        supported_auth_modes=[MCPAuthMode.OAUTH],
        default_url="https://mcp.vercel.com",
        authorization_url="https://vercel.com/oauth/authorize",
        token_url="https://api.vercel.com/login/oauth/token",
        default_scopes=[],
        supports_dcr=True,
        documentation_url="https://vercel.com/docs/agent-resources/vercel-mcp",
        auth_hint=AuthHint(
            label="Vercel OAuth",
            help_text="Sign in with your Vercel account.",
        ),
        tags=["deployment", "developer-tools"],
    )
)
class VercelMCPServer:
    """Marker class - see `mcp_template` for the registered catalog entry."""
