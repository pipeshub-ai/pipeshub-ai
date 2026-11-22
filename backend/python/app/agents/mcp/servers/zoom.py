"""Zoom MCP server template — official remote server (Streamable HTTP), OAuth, no DCR.

One catalog URL for everyone: Zoom's combined Workplace server. Chat, Whiteboard,
and Admin are different hosts and stay out of this card.
Zoom rejects ``localhost`` redirects; the admin registers the public frontend
callback shown on the form (HTTPS). A General App is required — no DCR or CIMD.
"""
from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="zoom",
        display_name="Zoom",
        description="Meetings and related Workplace tools via Zoom's official MCP server.",
        icon="/icons/connectors/zoom.svg",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.OAUTH,
        supported_auth_modes=[MCPAuthMode.OAUTH],
        default_url="https://mcp.zoom.us/mcp/zoom/streamable",
        # Confirmed live 2026-10-31: unauthenticated GET returns 401 with
        # WWW-Authenticate resource_metadata on zoom.us, PKCE S256, and
        # token_endpoint_auth_methods_supported=["client_secret_basic"] only.
        # No registration_endpoint. Sign-in in PipesHub with a Marketplace
        # General App (user-managed) listed tools.
        # Scopes match this host's tools/list (9 tools). Zoom does not expose
        # create/update/delete meeting on this server; those REST scopes are omitted.
        authorization_url="https://zoom.us/oauth/authorize",
        token_url="https://zoom.us/oauth/token",
        default_scopes=[
            "meeting:read:search",
            "meeting:read:assets",
            "agentic_search:read:search",
            "cloud_recording:read:list_user_recordings",
            "cloud_recording:read:content",
            "docs:read:export",
            "docs:write:import",
            "hub:read:content",
            "hub:write:content",
        ],
        supports_dcr=False,
        documentation_url="https://developers.zoom.us/docs/mcp/servers/connect-to-zoom-mcp-servers/",
        auth_hint=AuthHint(
            label="Zoom OAuth",
            help_text=(
                "Create a User-managed General App in the Zoom App Marketplace, add "
                "this deployment's redirect URI (HTTPS; Zoom rejects localhost), add "
                "the scopes listed above, and paste that app's client ID and secret — "
                "Zoom does not support dynamic client registration. Use the Production "
                "credentials when this PipesHub URL is the live one. Recordings, Docs "
                "and Hub also need those products on the Zoom account."
            ),
        ),
        tags=["meetings", "collaboration"],
    )
)
class ZoomMCPServer:
    """Marker class — see `mcp_template` for the registered catalog entry."""
