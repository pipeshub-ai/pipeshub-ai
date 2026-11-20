"""Smartlead MCP server template — official remote server (Streamable HTTP), OAuth + DCR."""
from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="smartlead",
        display_name="Smartlead",
        description="Manage cold email campaigns, leads, and email accounts in Smartlead.",
        icon="/icons/connectors/smartlead.svg",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.OAUTH,
        supported_auth_modes=[MCPAuthMode.OAUTH],
        default_url="https://mcp.smartlead.ai/mcp",
        # Confirmed live via https://mcp.smartlead.ai/.well-known/oauth-protected-resource/mcp
        # -> AS https://mcp.smartlead.ai, whose oauth-authorization-server metadata advertises a
        # `registration_endpoint` (RFC 7591 DCR) and PKCE S256. These are only the UI-hint
        # defaults; the authorize flow always re-discovers live (see dcr.py). The older
        # `/sse` endpoint takes an API key inside the URL and is not usable in the catalog.
        authorization_url="https://app.smartlead.ai/oauth/authorize",
        token_url="https://server.smartlead.ai/api/mcp-oauth/token",
        default_scopes=[],
        supports_dcr=True,
        documentation_url="https://helpcenter.smartlead.ai/en/articles/468-how-to-connect-smartlead-with-claude",
        auth_hint=AuthHint(
            label="Smartlead OAuth",
            help_text="Sign in with your Smartlead account.",
        ),
        tags=["email", "outreach", "sales", "marketing"],
    )
)
class SmartleadMCPServer:
    """Marker class — see `mcp_template` for the registered catalog entry."""
