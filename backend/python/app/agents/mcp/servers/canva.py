"""Canva MCP server template — official remote server (Streamable HTTP), OAuth + DCR.

This is Canva's hosted design server (``mcp.canva.com``). It is not Canva's
local Dev MCP server used while building Canva apps.
"""

from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="canva",
        display_name="Canva",
        description="Create, edit, search, and export designs in Canva.",
        icon="/icons/connectors/canva.svg",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.OAUTH,
        supported_auth_modes=[MCPAuthMode.OAUTH],
        default_url="https://mcp.canva.com/mcp",
        # Confirmed live 2026-11-23 via discover_oauth_metadata('https://mcp.canva.com/mcp'):
        # authorization_endpoint https://mcp.canva.com/authorize, token_endpoint
        # https://mcp.canva.com/token, registration_endpoint https://mcp.canva.com/register
        # (RFC 7591 DCR), PKCE methods include S256, and CIMD is advertised.
        # The 401 names no scopes; protected-resource metadata lists the design,
        # folder, asset, comment, brand, profile, and help scopes. These endpoints
        # are only the fallback; the authorize flow re-discovers live (see dcr.py).
        authorization_url="https://mcp.canva.com/authorize",
        token_url="https://mcp.canva.com/token",
        default_scopes=[],
        supports_dcr=True,
        documentation_url="https://www.canva.dev/docs/apps/mcp/",
        auth_hint=AuthHint(
            label="Canva OAuth",
            help_text="Sign in with your Canva account.",
        ),
        tags=["design", "graphics", "marketing"],
    )
)
class CanvaMCPServer:
    """Marker class — see `mcp_template` for the registered catalog entry."""
