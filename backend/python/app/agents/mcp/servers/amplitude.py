"""Amplitude MCP server template — official remote server (Streamable HTTP), OAuth + DCR.

US-hosted accounts only. Amplitude also runs https://mcp.eu.amplitude.com/mcp for EU-region
accounts; it is a separate host with its own authorization server, so it is not covered here.
Amplitude's docs MCP (amplitude.com/docs/api/mcp) is a different server and is not used.
"""
from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="amplitude",
        display_name="Amplitude",
        description=(
            "Query product analytics, charts, and cohorts in Amplitude. "
            "US-hosted accounts only."
        ),
        icon="/icons/connectors/amplitude.svg",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.OAUTH,
        supported_auth_modes=[MCPAuthMode.OAUTH],
        default_url="https://mcp.amplitude.com/mcp",
        # Confirmed live 2026-11-21: an unauthenticated POST returns 401 with
        # resource_metadata pointing at /.well-known/oauth-protected-resource. The
        # authorization server is https://mcp.amplitude.com itself; its metadata has a
        # registration_endpoint (https://mcp.amplitude.com/register, RFC 7591), S256 PKCE,
        # token auth client_secret_post/none, scopes_supported all:read/all:write/
        # offline_access, and client_id_metadata_document_supported. These are only the
        # UI-hint defaults; the authorize flow always re-discovers live (see dcr.py).
        authorization_url="https://mcp.amplitude.com/authorize",
        token_url="https://mcp.amplitude.com/token",
        # The protected-resource metadata names all:read/all:write; leave empty so
        # discovery decides (challenge_scopes was empty on probe).
        default_scopes=[],
        supports_dcr=True,
        documentation_url="https://amplitude.com/docs/amplitude-ai/amplitude-mcp",
        auth_hint=AuthHint(
            label="Amplitude OAuth",
            help_text=(
                "Sign in with your Amplitude account. "
                "Only US-hosted accounts are supported."
            ),
        ),
        tags=["analytics", "product-analytics", "charts"],
    )
)
class AmplitudeMCPServer:
    """Marker class — see `mcp_template` for the registered catalog entry."""
