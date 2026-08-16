"""Linear MCP server template — official remote server (Streamable HTTP), OAuth + DCR."""
from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="linear",
        display_name="Linear",
        description="Find, create, and update issues, projects, and comments in Linear.",
        icon="/icons/connectors/linear.svg",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.OAUTH,
        supported_auth_modes=[MCPAuthMode.OAUTH],
        default_url="https://mcp.linear.app/mcp",
        # Confirmed live via https://mcp.linear.app/.well-known/oauth-protected-resource/mcp
        # -> AS https://mcp.linear.app, whose oauth-authorization-server metadata advertises a
        # `registration_endpoint` (RFC 7591 DCR). These are only the UI-hint defaults; the
        # authorize flow always re-discovers live (see dcr.py). The older `/sse` endpoint is
        # deprecated in favor of Streamable HTTP.
        authorization_url="https://mcp.linear.app/authorize",
        token_url="https://mcp.linear.app/token",
        default_scopes=["read", "write"],
        supports_dcr=True,
        documentation_url="https://linear.app/docs/mcp",
        auth_hint=AuthHint(
            label="Linear OAuth",
            help_text="Sign in with your Linear account.",
        ),
        tags=["issues", "project-management"],
    )
)
class LinearMCPServer:
    """Marker class — see `mcp_template` for the registered catalog entry."""
