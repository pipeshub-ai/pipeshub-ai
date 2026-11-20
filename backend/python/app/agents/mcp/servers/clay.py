"""Clay MCP server template — official remote server hosted by Clay (Streamable HTTP), OAuth + DCR."""
from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="clay",
        display_name="Clay",
        description="Enrich leads and companies, and work with tables and workflows in Clay.",
        icon="/icons/connectors/clay.svg",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.OAUTH,
        supported_auth_modes=[MCPAuthMode.OAUTH],
        default_url="https://api.clay.com/v3/mcp",
        # Confirmed live: an unauthenticated POST to https://api.clay.com/v3/mcp returns 401 with
        # `resource_metadata="https://api.clay.com/.well-known/oauth-protected-resource/v3/mcp"`
        # -> AS https://api.clay.com (scopes_supported ["mcp"]), whose
        # /.well-known/oauth-authorization-server advertises a `registration_endpoint`
        # (https://api.clay.com/oauth/register, RFC 7591 DCR) and PKCE S256 only.
        authorization_url="https://app.clay.com/oauth/authorize",
        token_url="https://api.clay.com/oauth/token",
        default_scopes=[],
        supports_dcr=True,
        documentation_url="https://university.clay.com/docs/connect-to-clay-mcp",
        auth_hint=AuthHint(
            label="Clay OAuth",
            help_text="Sign in with your Clay account. Enrichment tools spend Clay credits.",
        ),
        tags=["enrichment", "sales", "leads", "crm"],
    )
)
class ClayMCPServer:
    """Marker class — see `mcp_template` for the registered catalog entry."""
