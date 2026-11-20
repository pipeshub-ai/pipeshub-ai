"""Fathom MCP server template — official remote server (Streamable HTTP), OAuth + DCR."""
from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="fathom",
        display_name="Fathom",
        description="Search meetings, transcripts, summaries and action items recorded in Fathom.",
        icon="/icons/connectors/fathom.svg",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.OAUTH,
        supported_auth_modes=[MCPAuthMode.OAUTH],
        default_url="https://api.fathom.ai/mcp",
        # Confirmed live 2026-11-18: an unauthenticated POST returns 401 naming
        # https://api.fathom.ai/.well-known/oauth-protected-resource/mcp -> AS
        # https://api.fathom.ai, whose metadata has a `registration_endpoint`
        # (https://api.fathom.ai/mcp/oauth/register, RFC 7591), S256 PKCE only, public clients
        # only (token auth `none`), and no CIMD. The authorize page is on fathom.video.
        # PipesHub's DCR registration with a callback redirect URI was accepted.
        authorization_url="https://fathom.video/mcp/oauth/authorize",
        token_url="https://api.fathom.ai/mcp/oauth/token",
        # The server's only scope is `mcp`, which its resource metadata supplies.
        default_scopes=[],
        supports_dcr=True,
        documentation_url="https://developers.fathom.ai/mcp-docs",
        auth_hint=AuthHint(
            label="Fathom OAuth",
            help_text="Sign in with your Fathom account.",
        ),
        tags=["meetings", "transcripts", "notes"],
    )
)
class FathomMCPServer:
    """Marker class — see `mcp_template` for the registered catalog entry."""
