"""Granola MCP server template — official remote server (Streamable HTTP), OAuth + DCR."""
from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="granola",
        display_name="Granola",
        description=(
            "Search and read your meeting notes and summaries in Granola. "
            "Transcripts need a paid plan."
        ),
        icon="/icons/connectors/granola.svg",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.OAUTH,
        supported_auth_modes=[MCPAuthMode.OAUTH],
        default_url="https://mcp.granola.ai/mcp",
        # Confirmed live 2026-11-18: an unauthenticated POST returns 401 naming
        # https://mcp.granola.ai/.well-known/oauth-protected-resource -> AS
        # https://mcp-auth.granola.ai, whose metadata has a `registration_endpoint`
        # (https://mcp-auth.granola.ai/oauth2/register, RFC 7591), S256 PKCE only, token auth
        # none/client_secret_post/client_secret_basic/private_key_jwt, and CIMD support.
        # PipesHub's DCR registration with a callback redirect URI was accepted.
        authorization_url="https://mcp-auth.granola.ai/oauth2/authorize",
        token_url="https://mcp-auth.granola.ai/oauth2/token",
        # The resource metadata lists `mcp`; Granola decides what each plan can reach, so ask
        # for whatever the server names instead of pinning scopes.
        default_scopes=[],
        supports_dcr=True,
        documentation_url="https://docs.granola.ai/help-center/sharing/integrations/mcp",
        auth_hint=AuthHint(
            label="Granola OAuth",
            help_text=(
                "Sign in with your Granola account. What the tools can reach depends on your "
                "Granola plan."
            ),
        ),
        tags=["meetings", "notes", "transcripts"],
    )
)
class GranolaMCPServer:
    """Marker class — see `mcp_template` for the registered catalog entry."""
