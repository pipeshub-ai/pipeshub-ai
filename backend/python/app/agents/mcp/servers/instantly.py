"""Instantly MCP server template — official hosted server (Streamable HTTP), OAuth + DCR or API key."""
from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="instantly",
        display_name="Instantly",
        description="Manage cold email campaigns, leads, sending accounts and analytics in Instantly.",
        icon="/icons/connectors/instantly.svg",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.OAUTH,
        supported_auth_modes=[MCPAuthMode.OAUTH, MCPAuthMode.API_TOKEN],
        default_url="https://mcp.instantly.ai/mcp",
        # Confirmed live: an unauthenticated POST to the endpoint returns 401 with
        # `resource_metadata=https://mcp.instantly.ai/.well-known/oauth-protected-resource`
        # -> AS https://api.instantly.ai, whose oauth-authorization-server metadata advertises a
        # `registration_endpoint` (RFC 7591 DCR) and PKCE S256. Neither the 401 nor the
        # protected-resource metadata names scopes, and the AS lists every scope (including
        # `all:all`), so `default_scopes` stays empty rather than asking for all of them.
        # The API-key mode sends `Authorization: Bearer <key>` (the `/mcp/<key>` URL form is not used).
        authorization_url="https://api.instantly.ai/oauth/authorize",
        token_url="https://api.instantly.ai/oauth/token",
        default_scopes=[],
        supports_dcr=True,
        documentation_url="https://developer.instantly.ai/mcp/introduction",
        auth_hint=AuthHint(
            label="Instantly API key",
            help_text="Create an API key in Instantly under Settings → Integrations, or sign in with OAuth.",
        ),
        tags=["email", "sales", "outreach", "marketing"],
    )
)
class InstantlyMCPServer:
    """Marker class — see `mcp_template` for the registered catalog entry."""
