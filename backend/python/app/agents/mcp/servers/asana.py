"""Asana MCP server template — official remote server (Streamable HTTP), OAuth, no DCR."""
from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="asana",
        display_name="Asana",
        description="Tasks, projects, and goals in an Asana workspace.",
        icon="/icons/connectors/asana.svg",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.OAUTH,
        supported_auth_modes=[MCPAuthMode.OAUTH],
        default_url="https://mcp.asana.com/v2/mcp",
        # Confirmed live via https://mcp.asana.com/.well-known/oauth-protected-resource/v2/mcp
        # -> AS https://app.asana.com, whose oauth-authorization-server metadata has no
        # `registration_endpoint` — Asana requires an admin to create an MCP app in its
        # developer console (Create new app -> MCP app) rather than dynamic client
        # registration. These are only the UI-hint defaults; the authorize flow always
        # re-discovers live (see dcr.py).
        authorization_url="https://app.asana.com/-/oauth_authorize",
        token_url="https://app.asana.com/-/oauth_token",
        default_scopes=[],
        supports_dcr=False,
        documentation_url="https://developers.asana.com/docs/connecting-mcp-clients-to-asanas-v2-server",
        auth_hint=AuthHint(
            label="Asana OAuth",
            help_text=(
                "Create an MCP app in Asana's developer console (Create new app -> MCP app) "
                "with the redirect URL shown here, then provide the client ID/secret below — "
                "Asana does not support dynamic client registration."
            ),
        ),
        tags=["tasks", "project-management"],
    )
)
class AsanaMCPServer:
    """Marker class — see `mcp_template` for the registered catalog entry."""
