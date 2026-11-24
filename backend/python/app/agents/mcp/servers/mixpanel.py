"""Mixpanel MCP server template — official remote server (Streamable HTTP), OAuth + DCR.

US data center only. EU and India projects are served by https://mcp-eu.mixpanel.com/mcp and
https://mcp-in.mixpanel.com/mcp, which register clients at their own eu./in.mixpanel.com hosts.
"""
from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="mixpanel",
        display_name="Mixpanel",
        description="Query events, funnels, retention and flows, and look up events and properties in Mixpanel.",
        icon="/icons/connectors/mixpanel.svg",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.OAUTH,
        supported_auth_modes=[MCPAuthMode.OAUTH],
        default_url="https://mcp.mixpanel.com/mcp",
        # Confirmed live 2026-11-16: an unauthenticated POST returns 401 naming
        # https://mcp.mixpanel.com/.well-known/oauth-protected-resource/mcp -> AS
        # https://auth.mixpanel.com, whose oauth-authorization-server metadata has a
        # `registration_endpoint` (https://mixpanel.com/oauth/mcp/register/, RFC 7591), S256 PKCE
        # only, token auth none/client_secret_basic/client_secret_post, and no CIMD.
        authorization_url="https://mixpanel.com/oauth/authorize",
        token_url="https://mixpanel.com/oauth/token/",
        # Mixpanel's scopes are per product area, not read/write. These are the ten its docs call
        # mandatory, and the ones its 401 asks for; pinning them keeps PipesHub off the 14 optional
        # ones (dashboards, experiments, feature flags, alerts, custom events, ...) if that 401
        # ever widens.
        default_scopes=[
            "projects", "analysis", "events", "insights", "segmentation",
            "retention", "data:read", "funnels", "flows", "data_definitions",
        ],
        supports_dcr=True,
        documentation_url="https://docs.mixpanel.com/docs/mcp",
        auth_hint=AuthHint(
            label="Mixpanel OAuth",
            help_text=(
                "Sign in with your Mixpanel account. An org admin must first turn on MCP in "
                "Settings → Org → Overview."
            ),
        ),
        tags=["analytics", "product"],
    )
)
class MixpanelMCPServer:
    """Marker class — see `mcp_template` for the registered catalog entry."""
