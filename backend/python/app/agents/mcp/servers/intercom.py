"""Intercom MCP server template — official remote server (Streamable HTTP), OAuth + DCR.

US-hosted workspaces only. Intercom also runs https://mcp.eu.intercom.com/mcp for EU-hosted
workspaces; it is a separate host with its own authorization server, so it is not covered here.
The legacy SSE endpoint (/sse) is deprecated and not used.
"""
from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="intercom",
        display_name="Intercom",
        description=(
            "Search and manage conversations, contacts, companies and tickets in Intercom. "
            "US-hosted workspaces only."
        ),
        icon="/icons/connectors/intercom.svg",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.OAUTH,
        supported_auth_modes=[MCPAuthMode.OAUTH],
        default_url="https://mcp.intercom.com/mcp",
        # Confirmed live 2026-11-18: an unauthenticated POST returns 401 with
        # `resource_metadata=https://mcp.intercom.com/.well-known/oauth-protected-resource/mcp` and
        # `scope="mcp operator"`. The authorization server is https://mcp.intercom.com itself; its
        # metadata has a `registration_endpoint` (https://mcp.intercom.com/register, RFC 7591),
        # S256 PKCE only, token auth client_secret_basic/client_secret_post/none, and no CIMD.
        # PipesHub's DCR registration with a callback redirect URI was accepted.
        authorization_url="https://mcp.intercom.com/authorize",
        token_url="https://mcp.intercom.com/token",
        # The 401 names the scopes (`mcp operator`), so leave this empty and let it decide.
        default_scopes=[],
        supports_dcr=True,
        documentation_url="https://developers.intercom.com/docs/guides/mcp",
        auth_hint=AuthHint(
            label="Intercom OAuth",
            help_text="Sign in with your Intercom account. Only US-hosted workspaces are supported.",
        ),
        tags=["support", "customer-service", "crm"],
    )
)
class IntercomMCPServer:
    """Marker class — see `mcp_template` for the registered catalog entry."""
