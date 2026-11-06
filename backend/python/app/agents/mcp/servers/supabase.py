"""Supabase MCP server template — official hosted server (Streamable HTTP), OAuth + DCR.

Supabase says the server is meant for development and testing, and that it shouldn't be connected
to production databases (https://supabase.com/docs/guides/getting-started/mcp#security-risks).
"""

from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="supabase",
        display_name="Supabase",
        description=(
            "Query databases, run migrations and manage Supabase projects. "
            "For development, not production databases."
        ),
        icon="/icons/connectors/supabase.svg",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.OAUTH,
        supported_auth_modes=[MCPAuthMode.OAUTH],
        default_url="https://mcp.supabase.com/mcp",
        # Confirmed live via https://mcp.supabase.com/.well-known/oauth-protected-resource/mcp
        # -> AS https://api.supabase.com, whose oauth-authorization-server metadata advertises a
        # `registration_endpoint` (RFC 7591 DCR), S256 PKCE and client_secret_basic/post. The 401
        # names no scopes, so `default_scopes=[]` asks for the protected-resource scopes. These
        # endpoints are only fallback hints; the authorize flow always re-discovers live (dcr.py).
        authorization_url="https://api.supabase.com/v1/oauth/authorize",
        token_url="https://api.supabase.com/v1/oauth/token",
        default_scopes=[],
        supports_dcr=True,
        documentation_url="https://supabase.com/docs/guides/getting-started/mcp",
        auth_hint=AuthHint(
            label="Supabase OAuth",
            help_text="Sign in with Supabase and choose the organization to connect.",
        ),
        tags=["database", "postgres", "developer-tools"],
    )
)
class SupabaseMCPServer:
    """Marker class — see `mcp_template` for the registered catalog entry."""
