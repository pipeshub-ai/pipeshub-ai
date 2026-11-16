"""Egnyte MCP server template — official remote server (Streamable HTTP), OAuth + DCR.

One URL for every customer: Egnyte's sign-in page asks for the user's Egnyte domain.
"""
from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="egnyte",
        display_name="Egnyte",
        description="Search, read, and ask questions about files and folders in Egnyte.",
        icon="/icons/connectors/egnyte.svg",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.OAUTH,
        supported_auth_modes=[MCPAuthMode.OAUTH],
        default_url="https://mcp-server.egnyte.com/mcp",
        # Confirmed live 2026-11-05 with `discover_oauth_metadata`: an unauthenticated POST
        # returns 401, and the protected-resource metadata names the AS
        # https://mcp-oauth.egnyte.com/egnyte-connect, whose metadata has a
        # `registration_endpoint` (https://mcp-oauth.egnyte.com/clients, RFC 7591), PKCE S256,
        # client_secret_post/basic and no scopes. A DCR registration with a localhost
        # redirect URI was accepted, and the authorize endpoint took that redirect.
        authorization_url="https://mcp-oauth.egnyte.com/external/oauth2/authorize/egnyte-connect",
        token_url="https://mcp-oauth.egnyte.com/egnyte-connect/oauth2/token",
        default_scopes=[],
        supports_dcr=True,
        documentation_url="https://developers.egnyte.com/integration/cfs/api-docs/remote-mcp-server",
        auth_hint=AuthHint(
            label="Egnyte OAuth",
            help_text=(
                "Sign in with your Egnyte account. Which tools you get depends on your Egnyte "
                "plan, and an Egnyte admin may need to allow MCP access first."
            ),
        ),
        tags=["storage", "docs"],
    )
)
class EgnyteMCPServer:
    """Marker class — see `mcp_template` for the registered catalog entry."""
