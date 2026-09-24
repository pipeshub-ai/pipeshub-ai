"""ClickUp MCP server template — official remote server (Streamable HTTP), OAuth + DCR."""
from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="clickup",
        display_name="ClickUp",
        description="Tasks, documents, and chat in a ClickUp workspace (public beta).",
        icon="/icons/connectors/clickup.svg",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.OAUTH,
        supported_auth_modes=[MCPAuthMode.OAUTH],
        default_url="https://mcp.clickup.com/mcp",
        # Confirmed live via https://mcp.clickup.com/.well-known/oauth-protected-resource/mcp
        # -> AS https://mcp.clickup.com, whose oauth-authorization-server metadata advertises a
        # `registration_endpoint` (RFC 7591 DCR). These are only the UI-hint defaults; the
        # authorize flow always re-discovers live (see dcr.py).
        authorization_url="https://mcp.clickup.com/oauth/authorize",
        token_url="https://mcp.clickup.com/oauth/token",
        default_scopes=["read", "write"],
        supports_dcr=True,
        documentation_url="https://developer.clickup.com/docs/connect-an-ai-assistant-to-clickups-mcp-server",
        auth_hint=AuthHint(
            label="ClickUp OAuth",
            help_text="Sign in with your ClickUp account.",
        ),
        tags=["project-management", "docs"],
    )
)
class ClickUpMCPServer:
    """Marker class — see `mcp_template` for the registered catalog entry."""
