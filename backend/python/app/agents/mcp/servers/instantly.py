"""Instantly MCP server template — official remote server (Streamable HTTP).

Instantly advertises OAuth with Dynamic Client Registration (RFC 7591) and PKCE S256,
and also accepts an API key sent as `Authorization: Bearer <key>`.
"""
from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="instantly",
        display_name="Instantly",
        description="Cold email campaigns, leads, analytics, and email accounts in Instantly.",
        icon="/icons/connectors/instantly.svg",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.OAUTH,
        supported_auth_modes=[MCPAuthMode.OAUTH, MCPAuthMode.API_TOKEN],
        default_url="https://mcp.instantly.ai/mcp",
        # Confirmed live via https://mcp.instantly.ai/.well-known/oauth-protected-resource
        # -> AS https://api.instantly.ai, whose oauth-authorization-server metadata advertises a
        # `registration_endpoint` (RFC 7591 DCR) and PKCE S256. These are only the UI-hint
        # defaults; the authorize flow always re-discovers live (see dcr.py).
        authorization_url="https://api.instantly.ai/oauth/authorize",
        token_url="https://api.instantly.ai/oauth/token",
        default_scopes=[],
        supports_dcr=True,
        documentation_url="https://developer.instantly.ai/mcp/introduction",
        auth_hint=AuthHint(
            label="API Key",
            placeholder="Enter your Instantly API key",
            help_text=(
                "Find your API key in Instantly under Settings > Integrations. "
                "To sign in without an API key, choose OAuth."
            ),
        ),
        tags=["email", "campaigns", "sales", "crm", "outreach"],
    )
)
class InstantlyMCPServer:
    """Marker class — see `mcp_template` for the registered catalog entry."""
